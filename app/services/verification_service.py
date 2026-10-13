from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from aiogram.exceptions import TelegramAPIError

from app.services.channel_selector import select_sticky

log = logging.getLogger(__name__)
MEMBER_STATUSES = {"creator", "administrator", "member"}


@dataclass
class Step:
    kind: str  # prompt | deliver | error | retry | noop
    flow: dict | None = None
    channel: dict | None = None
    file: dict | None = None
    link: dict | None = None
    key: str | None = None


def is_member(cm) -> bool:
    st = getattr(cm, "status", None)
    st = getattr(st, "value", st)
    return st in MEMBER_STATUSES or (st == "restricted" and bool(getattr(cm, "is_member", False)))


class MembershipChecker:
    """getChatMember behind a global concurrency cap (protects against Telegram flood limits when
    hundreds of users arrive at once) plus a very short positive cache against double taps."""

    def __init__(self, bot, concurrency: int = 12, positive_ttl: float = 5.0, clock=time.monotonic):
        self.bot, self.ttl, self.clock = bot, positive_ttl, clock
        self.sem = asyncio.Semaphore(concurrency)
        self._ok: dict[tuple[int, int], float] = {}

    async def is_member(self, chat_id: int, user_id: int) -> bool:
        key, now = (chat_id, user_id), self.clock()
        if self.ttl > 0 and now - self._ok.get(key, -1e9) < self.ttl:
            return True
        async with self.sem:
            member = is_member(await self.bot.get_chat_member(chat_id, user_id))
        if member and self.ttl > 0:
            if len(self._ok) > 10000:
                self._ok = {k: t for k, t in self._ok.items() if now - t < self.ttl}
            self._ok[key] = now
        return member


class VerificationService:
    OK = ("ok", "ok_request")

    def __init__(self, bot, repos, links, ttl_seconds: int = 1800, rng=None, member_cache_ttl: float = 5.0):
        self.bot, self.repos, self.links = bot, repos, links
        self.ttl, self.rng = ttl_seconds, rng
        self.checker = MembershipChecker(bot, positive_ttl=member_cache_ttl)

    # ------------------------------------------------------------------ entry points
    async def start(self, user_id: int, token: str) -> Step:
        res = await self.links.resolve(token)
        if res.status != "ok":
            return Step("error", key={"invalid": "err_invalid", "revoked": "err_revoked"}.get(res.status, "err_unavailable"))
        if not await self.repos.requests.claim(f"start:{user_id}:{token}", 2):
            return Step("noop")  # duplicate /start within 2s
        fid, r = res.file["_id"], self.repos
        cfg, eligible, user, _ = await asyncio.gather(
            r.settings.get(), r.channels.eligible(), r.users.get(user_id), r.stats.inc("requests"))
        n = int(cfg["required_steps"])
        prev = list((user or {}).get("fsub_ids") or [])
        known: dict[int, str] = {}
        # FAST PATH (returning users): no flow, no writes. Re-check live membership of the channels they
        # verified last time; still a member of all of them -> deliver at once, otherwise ask again.
        cand = sorted((c for c in eligible if c["_id"] in prev), key=lambda c: prev.index(c["_id"]))[:n]
        if cand and len(cand) >= min(n, len(eligible)):
            outs = await asyncio.gather(*(self._check(c, user_id) for c in cand))
            known = {c["_id"]: o for c, o in zip(cand, outs)}
            if all(o in self.OK for o in outs):
                return Step("deliver", file=res.file, link=res.link)
        flow = await r.requests.find_active(user_id, fid)
        if flow is None:
            chosen = select_sticky(eligible, prev, n, cfg["balancing"], self.rng)
            if not chosen:
                if not await r.requests.claim(f"dl:{user_id}:{token}", 5):
                    return Step("noop")
                return Step("deliver", file=res.file, link=res.link)
            ids = [c["_id"] for c in chosen]
            flow = await r.requests.create_flow(user_id, fid, token, ids, self.ttl)
            new_ids = [i for i in ids if i not in prev]  # sticky channels are not re-counted as "presented"
            if new_ids:
                await r.channels.inc_many(new_ids, "presented")
        return await self._verify_loop(flow["_id"], user_id, silent=True, flow=flow, known=known)

    async def verify(self, flow_id: str, user_id: int) -> Step:
        flow = await self.repos.requests.get_flow(flow_id)
        if flow is None or flow["user_id"] != user_id:  # ownership check
            return Step("error", key="err_flow_expired")
        if flow["status"] == "completed":
            return Step("noop", flow=flow)
        exp = flow["expires_at"]
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            return Step("error", key="err_flow_expired")
        return await self._verify_loop(flow_id, user_id, silent=False, flow=flow)

    @staticmethod
    def via_our_link(ch: dict, req_link: str | None) -> bool:
        """Telegram shortens links created by other admins ("https://t.me/+Ab…"), so compare by prefix.
        No link at all (public username join) counts as ours."""
        if not req_link:
            return True
        norm = req_link.rstrip("…").rstrip(".")
        url = ch.get("url") or ""
        return url.startswith(norm) or norm.startswith(url)

    async def on_join_request(self, chat_id: int, user_id: int, request_ts: int,
                              invite_link: str | None = None) -> list[Step]:
        """Any pool channel: a join-request event only exists when the channel needs approval.
        Statistics: `requests_received` = UNIQUE users who requested through OUR link (first time only);
        repeats and requests through other/old links are counted separately so totals stay explainable."""
        ch = await self.repos.channels.get(chat_id)
        if not ch or ch.get("removed"):
            return []
        outcome = await self.repos.requests.record_join_request(chat_id, user_id, request_ts, invite_link)
        if outcome == "dup":
            return []  # same event redelivered
        if not self.via_our_link(ch, invite_link):
            await self.repos.channels.inc(chat_id, "requests_other")
        elif outcome == "repeat":
            await self.repos.channels.inc(chat_id, "requests_repeat")
        else:
            await self.repos.channels.inc(chat_id, "requests_received")
            await self.repos.stats.inc("private_requests")
        steps = []
        for flow in await self.repos.requests.active_for_user_channel(user_id, chat_id):
            if await self.repos.requests.mark_done(flow["_id"], chat_id):
                steps.append(await self._verify_loop(flow["_id"], user_id, silent=True))
        return steps

    async def on_member_joined(self, chat_id: int, user_id: int) -> list[Step]:
        """chat_member update: user became a member of a pool channel -> advance their flows."""
        ch = await self.repos.channels.get(chat_id)
        if not ch or ch.get("removed"):
            return []
        await self.repos.requests.consume_join_requests(chat_id, user_id)  # request became membership
        steps, marked = [], False
        for flow in await self.repos.requests.active_for_user_channel(user_id, chat_id):
            if await self.repos.requests.mark_done(flow["_id"], chat_id):
                marked = True
                steps.append(await self._verify_loop(flow["_id"], user_id, silent=True))
        if marked and ch["mode"] == "public":
            await self.repos.channels.inc(chat_id, "joins_confirmed")
            await self.repos.stats.inc("public_confirmed")
        return steps

    async def reopen(self, flow_id: str) -> None:
        await self.repos.requests.reopen(flow_id, self.ttl)

    # ------------------------------------------------------------------ internals
    async def _verify_loop(self, flow_id: str, user_id: int, silent: bool, flow: dict | None = None,
                           known: dict | None = None) -> Step:
        """Check every pending step the user may already satisfy. All membership lookups run
        concurrently (<= 3 API calls, one round-trip of latency), results are applied in order.
        silent=True (link open / events): never returns an error and never counts failed checks."""
        r, known = self.repos, known or {}
        flow = flow or await r.requests.get_flow(flow_id)
        if flow is None:
            return Step("error", key="err_flow_expired")
        if flow["status"] != "active":
            return Step("noop", flow=flow)
        pending_ids = [c for c in flow["channel_ids"] if c not in flow["done"]]
        docs = await asyncio.gather(*(r.channels.get(c) for c in pending_ids))
        pending = []
        for cid, ch in zip(pending_ids, docs):
            if not ch or ch.get("removed") or not ch.get("active"):
                await r.requests.mark_done(flow_id, cid)  # channel left the pool: don't strand the user
            else:
                pending.append(ch)
        outcomes = await asyncio.gather(*(self._known_or_check(ch, user_id, known) for ch in pending))
        progressed = False
        for ch, outcome in zip(pending, outcomes):
            if outcome in ("ok", "ok_request"):
                # counters only for joins the user made while being asked (button press), never for
                # people who were already members when they opened the link
                if await r.requests.mark_done(flow_id, ch["_id"]) and not silent \
                        and outcome == "ok" and ch["mode"] == "public":
                    await r.channels.inc(ch["_id"], "joins_confirmed")
                    await r.stats.inc("public_confirmed")
                progressed = True
                continue
            if silent or progressed:  # show the card for the first unsatisfied channel
                return Step("prompt", flow=flow, channel=ch)
            if outcome == "verify_not_member":
                await r.channels.inc(ch["_id"], "failed_checks")
            return Step("retry", flow=flow, channel=ch, key=outcome)
        return await self._advance(flow_id)  # everything satisfied -> complete + deliver exactly once

    async def _known_or_check(self, ch: dict, user_id: int, known: dict) -> str:
        return known[ch["_id"]] if ch["_id"] in known else await self._check(ch, user_id)

    async def _check(self, ch: dict, user_id: int) -> str:
        """'ok' (member) | 'ok_request' (unconsumed recent join request) | verify_* error key."""
        try:
            member = await self.checker.is_member(ch["_id"], user_id)
        except TelegramAPIError as exc:
            # Never fall back to a stored request here: if we can't see membership we can't tell
            # whether the user has since left. (Live join-request events still complete flows directly.)
            log.warning("membership lookup failed chat=%s: %s", ch["_id"], type(exc).__name__)
            await self.repos.channels.inc(ch["_id"], "errors")
            return "verify_api_error"
        if member:
            await self.repos.requests.consume_join_requests(ch["_id"], user_id)
            return "ok"
        if await self.repos.requests.has_join_request(ch["_id"], user_id):
            return "ok_request"
        return "verify_request_missing" if ch["mode"] == "request" else "verify_not_member"

    async def _advance(self, flow_id: str) -> Step:
        flow = await self.repos.requests.get_flow(flow_id)
        if flow is None:
            return Step("error", key="err_flow_expired")
        for cid in flow["channel_ids"]:
            if cid in flow["done"]:
                continue
            ch = await self.repos.channels.get(cid)
            if not ch or ch.get("removed") or not ch.get("active"):
                await self.repos.requests.mark_done(flow_id, cid)  # channel left the pool: don't strand user
                continue
            return Step("prompt", flow=flow, channel=ch)
        won = await self.repos.requests.claim_completion(flow_id)
        if won is None:
            return Step("noop", flow=flow)
        await self.repos.users.set_fsub(won["user_id"], won["channel_ids"])  # returning users take the fast path
        file, link = await asyncio.gather(self.repos.files.get(won["file_id"]), self.repos.links.get(won["token"]))
        if not file or not link:
            return Step("error", key="err_unavailable")
        return Step("deliver", flow=won, file=file, link=link)
