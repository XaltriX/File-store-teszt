from __future__ import annotations
import uuid
from datetime import datetime, timedelta, timezone

from pymongo.errors import DuplicateKeyError


def now() -> datetime:
    return datetime.now(timezone.utc)


COMPLETED_RETENTION = timedelta(days=30)


class RequestsRepo:
    """Verification flows, join-request events, and short-lived idempotency guards."""

    def __init__(self, flows, join_requests, guards):
        self.flows, self.joins, self.guards = flows, join_requests, guards

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

    async def find_completed(self, user_id: int, file_id: str):
        return await self.flows.find_one({"user_id": user_id, "file_id": file_id, "status": "completed"})

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
    async def record_join_request(self, chat_id: int, user_id: int, request_ts: int) -> bool:
        try:
            await self.joins.insert_one({"chat_id": chat_id, "user_id": user_id, "request_ts": request_ts,
                                         "created_at": now()})
            return True
        except DuplicateKeyError:
            return False

    async def has_join_request(self, chat_id: int, user_id: int) -> bool:
        return await self.joins.find_one({"chat_id": chat_id, "user_id": user_id}) is not None

    # ---- guards
    async def claim(self, key: str, ttl_seconds: float) -> bool:
        """True if caller acquired the guard. Atomic; expired guards are re-acquirable."""
        t = now()
        try:
            r = await self.guards.update_one(
                {"_id": key, "expires_at": {"$lte": t}},
                {"$set": {"expires_at": t + timedelta(seconds=ttl_seconds)}}, upsert=True)
        except DuplicateKeyError:
            return False
        return r.modified_count == 1 or r.upserted_id is not None
