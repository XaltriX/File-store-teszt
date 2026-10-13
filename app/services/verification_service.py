from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from aiogram.exceptions import TelegramAPIError

from app.services.channel_selector import select_channels

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


class VerificationService:
    def __init__(self, bot, repos, links, ttl_seconds: int = 1800, rng=None):
        self.bot, self.repos, self.links = bot, repos, links
        self.ttl, self.rng = ttl_seconds, rng

    # ------------------------------------------------------------------ entry points
    async def start(self, user_id: int, token: str) -> Step:
        res = await self.links.resolve(token)
        if res.status != "ok":
            return Step("error", key={"invalid": "err_invalid", "revoked": "err_revoked"}.get(res.status, "err_unavailable"))
        if not await self.repos.requests.claim(f"start:{user_id}:{token}", 2):
            return Step("noop")  # duplicate /start within 2s
        await self.repos.stats.inc("requests")
        fid = res.file["_id"]
        if await self.repos.requests.find_completed(user_id, fid):
            if not await self.repos.requests.claim(f"dl:{user_id}:{token}", 5):
                return Step("noop")
            return Step("deliver", file=res.file, link=res.link)
        flow = await self.repos.requests.find_active(user_id, fid)
        if flow is None:
            cfg = await self.repos.settings.get()
            chosen = select_channels(await self.repos.channels.eligible(), int(cfg["required_steps"]),
                                     cfg["balancing"], self.rng)
            if not chosen:
                if not await self.repos.requests.claim(f"dl:{user_id}:{token}", 5):
                    return Step("noop")
                return Step("deliver", file=res.file, link=res.link)
            ids = [c["_id"] for c in chosen]
            flow = await self.repos.requests.create_flow(user_id, fid, token, ids, self.ttl)
            await self.repos.channels.inc_many(ids, "presented")
        # Auto-verify: users who already joined / already sent a request skip straight through.
        return await self._verify_loop(flow["_id"], user_id, silent=True)

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
        return await self._verify_loop(flow_id, user_id, silent=False)

    async def on_join_request(self, chat_id: int, user_id: int, request_ts: int) -> list[Step]:
        """Any pool channel: a join-request event only exists when the channel needs approval."""
        ch = await self.repos.channels.get(chat_id)
        if not ch or ch.get("removed"):
            return []
        if not await self.repos.requests.record_join_request(chat_id, user_id, request_ts):
            return []  # duplicate event
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
        steps = []
        for flow in await self.repos.requests.active_for_user_channel(user_id, chat_id):
            if await self.repos.requests.mark_done(flow["_id"], chat_id):
                if ch["mode"] == "public":
                    await self.repos.channels.inc(chat_id, "joins_confirmed")
                    await self.repos.stats.inc("public_confirmed")
                steps.append(await self._verify_loop(flow["_id"], user_id, silent=True))
        return steps

    async def reopen(self, flow_id: str) -> None:
        await self.repos.requests.reopen(flow_id, self.ttl)

    # ------------------------------------------------------------------ internals
    async def _verify_loop(self, flow_id: str, user_id: int, silent: bool) -> Step:
        """Check every pending step the user may already satisfy (<= required API calls).
        silent=True (link open / events): never returns an error and never counts failed checks."""
        progressed = False
        while True:
            step = await self._advance(flow_id)
            if step.kind != "prompt":
                return step
            ch = step.channel
            outcome = await self._check(ch, user_id, count_failure=not (silent or progressed))
            if outcome not in ("ok", "ok_request"):
                if silent or progressed:
                    return step
                return Step("retry", flow=step.flow, channel=ch, key=outcome)
            if await self.repos.requests.mark_done(flow_id, ch["_id"]) and outcome == "ok" and ch["mode"] == "public":
                await self.repos.channels.inc(ch["_id"], "joins_confirmed")
                await self.repos.stats.inc("public_confirmed")
            progressed = True

    async def _check(self, ch: dict, user_id: int, count_failure: bool = True) -> str:
        """'ok' (member) | 'ok_request' (stored join request) | verify_* error key."""
        try:
            member = is_member(await self.bot.get_chat_member(ch["_id"], user_id))
        except TelegramAPIError as exc:
            log.warning("membership lookup failed chat=%s: %s", ch["_id"], type(exc).__name__)
            await self.repos.channels.inc(ch["_id"], "errors")
            if await self.repos.requests.has_join_request(ch["_id"], user_id):
                return "ok_request"  # event already proven; lookup failure must not block it
            return "verify_api_error"
        if member:
            return "ok"
        if await self.repos.requests.has_join_request(ch["_id"], user_id):
            return "ok_request"
        if ch["mode"] == "request":
            return "verify_request_missing"
        if count_failure:
            await self.repos.channels.inc(ch["_id"], "failed_checks")
        return "verify_not_member"

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
        file = await self.repos.files.get(won["file_id"])
        link = await self.repos.links.get(won["token"])
        if not file or not link:
            return Step("error", key="err_unavailable")
        return Step("deliver", flow=won, file=file, link=link)
