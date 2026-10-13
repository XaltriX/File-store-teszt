from __future__ import annotations
import time
import uuid
from datetime import datetime, timedelta, timezone


from pymongo.errors import DuplicateKeyError


def now() -> datetime:
    return datetime.now(timezone.utc)


REQUEST_VALID = timedelta(days=7)  # an unconsumed join request stops counting after this
COMPLETED_RETENTION = timedelta(hours=6)  # audit / duplicate-delivery protection only


class RequestsRepo:
    """Verification flows, join-request events, and short-lived idempotency guards."""

    def __init__(self, flows, join_requests, clock=time.monotonic):
        self.flows, self.joins, self.clock = flows, join_requests, clock
        self._guards: dict[str, float] = {}
        # (chat, user) pairs that MAY have an unconsumed recent request. Authoritative negative: a pair not in
        # here needs no DB access at all (the common case). Warmed from the DB on startup.
        self._pending: set[tuple[int, int]] = set()

    async def load_pending(self) -> int:
        cutoff = now() - REQUEST_VALID
        cur = self.joins.find({"consumed": {"$ne": True}, "created_at": {"$gte": cutoff}})
        self._pending = {(d["chat_id"], d["user_id"]) async for d in cur}
        return len(self._pending)

    # ---- flows
    async def create_flow(self, user_id: int, file_id: str, token: str, channel_ids: list[int],
                          ttl_seconds: int) -> dict:
        t = now()
        doc = {"_id": uuid.uuid4().hex[:10], "user_id": user_id, "file_id": file_id, "token": token,
               "channel_ids": channel_ids, "required": len(channel_ids), "done": [], "status": "active",
               "chat_id": None, "message_id": None, "created_at": t, "updated_at": t,
               "expires_at": t + timedelta(seconds=ttl_seconds)}
        await self.flows.insert_one(doc)
        return doc

    async def get_flow(self, flow_id: str):
        return await self.flows.find_one({"_id": flow_id})

    async def find_active(self, user_id: int, file_id: str):
        return await self.flows.find_one({"user_id": user_id, "file_id": file_id, "status": "active",
                                          "expires_at": {"$gt": now()}})

    async def active_for_user_channel(self, user_id: int, chat_id: int):
        cur = self.flows.find({"user_id": user_id, "channel_ids": chat_id, "status": "active",
                               "expires_at": {"$gt": now()}})
        return [d async for d in cur]

    async def mark_done(self, flow_id: str, chat_id: int) -> bool:
        """Atomic, idempotent. True only for the call that actually added the step."""
        r = await self.flows.update_one(
            {"_id": flow_id, "status": "active", "channel_ids": chat_id, "done": {"$ne": chat_id}},
            {"$push": {"done": chat_id}, "$set": {"updated_at": now()}})
        return r.modified_count == 1

    async def set_message(self, flow_id: str, chat_id: int, message_id: int) -> None:
        await self.flows.update_one({"_id": flow_id}, {"$set": {"chat_id": chat_id, "message_id": message_id}})

    async def claim_completion(self, flow_id: str) -> dict | None:
        """Atomically move active->completed when all steps done. Only the winner may deliver."""
        t = now()
        return await self.flows.find_one_and_update(
            {"_id": flow_id, "status": "active", "$expr": {"$gte": [{"$size": "$done"}, "$required"]}},
            {"$set": {"status": "completed", "completed_at": t, "expires_at": t + COMPLETED_RETENTION}})

    async def reopen(self, flow_id: str, ttl_seconds: int = 1800) -> None:
        await self.flows.update_one(
            {"_id": flow_id, "status": "completed"},
            {"$set": {"status": "active", "expires_at": now() + timedelta(seconds=ttl_seconds)}})

    # ---- join requests (private channels): dedupe by (chat, user, request timestamp)
    async def record_join_request(self, chat_id: int, user_id: int, request_ts: int,
                                  invite_link: str | None = None) -> str:
        """Returns 'dup' (same event redelivered), 'repeat' (this user already requested before) or 'first'."""
        first = await self.joins.find_one({"chat_id": chat_id, "user_id": user_id}) is None
        try:
            await self.joins.insert_one({"chat_id": chat_id, "user_id": user_id, "request_ts": request_ts,
                                         "invite_link": invite_link, "consumed": False, "created_at": now()})
        except DuplicateKeyError:
            return "dup"
        self._pending.add((chat_id, user_id))
        return "first" if first else "repeat"

    async def has_join_request(self, chat_id: int, user_id: int) -> bool:
        """True only for an unconsumed request (one that has not yet turned into membership)."""
        if (chat_id, user_id) not in self._pending:
            return False
        return await self.joins.find_one({"chat_id": chat_id, "user_id": user_id, "consumed": {"$ne": True},
                                          "created_at": {"$gte": now() - REQUEST_VALID}}) is not None

    async def consume_join_requests(self, chat_id: int, user_id: int) -> None:
        """The user is now a member: their old request must not count again after they leave."""
        if (chat_id, user_id) not in self._pending:
            return  # nothing to consume: no DB access on the hot path
        self._pending.discard((chat_id, user_id))
        await self.joins.update_many({"chat_id": chat_id, "user_id": user_id, "consumed": {"$ne": True}},
                                     {"$set": {"consumed": True}})

    # ---- guards (in-memory: a single bot process, so no DB write per request)
    async def claim(self, key: str, ttl_seconds: float) -> bool:
        """True if the caller acquired the guard; expired guards are re-acquirable."""
        now_m = self.clock()
        if len(self._guards) > 5000:
            self._guards = {k: v for k, v in self._guards.items() if v > now_m}
        if self._guards.get(key, 0) > now_m:
            return False
        self._guards[key] = now_m + ttl_seconds
        return True

    def reset_guards(self) -> None:
        self._guards.clear()
