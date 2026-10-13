from __future__ import annotations
import uuid
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError


def now() -> datetime:
    return datetime.now(timezone.utc)


class BroadcastsRepo:
    """Job document holds only counters + source message ref. Per-recipient state lives in
    broadcast_deliveries (one small doc each) so no unbounded array is stored."""

    def __init__(self, col, deliveries):
        self.col, self.deliveries = col, deliveries

    async def create(self, admin_id: int, src_chat: int, src_msg: int, target: int, preview: str) -> dict:
        doc = {"_id": uuid.uuid4().hex[:10], "admin_id": admin_id, "src_chat": src_chat, "src_msg": src_msg,
               "audience": "all_active", "status": "pending", "target": target, "success": 0, "blocked": 0,
               "failed": 0, "cursor": 0, "created_at": now(), "finished_at": None, "preview": preview[:100],
               "status_chat": None, "status_msg": None}
        await self.col.insert_one(doc)
        return doc

    async def get(self, bid: str):
        return await self.col.find_one({"_id": bid})

    async def set_status(self, bid: str, status: str, finished: bool = False) -> None:
        upd = {"status": status}
        if finished:
            upd["finished_at"] = now()
        await self.col.update_one({"_id": bid}, {"$set": upd})

    async def start(self, bid: str) -> bool:
        r = await self.col.update_one({"_id": bid, "status": {"$in": ["pending", "interrupted"]}},
                                      {"$set": {"status": "running"}})
        return r.modified_count == 1

    async def set_status_message(self, bid: str, chat: int, msg: int) -> None:
        await self.col.update_one({"_id": bid}, {"$set": {"status_chat": chat, "status_msg": msg}})

    async def record(self, bid: str, uid: int, outcome: str, error: str | None = None) -> bool:
        """outcome: success|blocked|failed. Idempotent per (bid, uid)."""
        try:
            await self.deliveries.insert_one({"bid": bid, "uid": uid, "outcome": outcome, "error": error, "at": now()})
        except DuplicateKeyError:
            return False
        field = {"success": "success", "blocked": "blocked", "failed": "failed"}[outcome]
        await self.col.update_one({"_id": bid}, {"$inc": {field: 1}})
        return True

    async def already_done(self, bid: str, uid: int) -> bool:
        return await self.deliveries.find_one({"bid": bid, "uid": uid}) is not None

    async def advance_cursor(self, bid: str, uid: int) -> None:
        await self.col.update_one({"_id": bid}, {"$max": {"cursor": uid}})

    async def unfinished(self):
        return [d async for d in self.col.find({"status": {"$in": ["running", "interrupted"]}})]
