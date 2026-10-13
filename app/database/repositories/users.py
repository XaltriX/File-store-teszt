from __future__ import annotations
from datetime import datetime, timedelta, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


class UsersRepo:
    def __init__(self, col):
        self.col = col

    async def touch(self, uid: int, first_name, last_name, username) -> tuple[dict, bool]:
        """Create or refresh a user. Returns (doc, is_new). Writes throttled to 1/5min."""
        t = now()
        doc = await self.col.find_one({"_id": uid})
        if doc is None:
            new = {"_id": uid, "first_name": first_name, "last_name": last_name, "username": username,
                   "language": None, "first_seen": t, "last_active": t, "blocked": False}
            try:
                await self.col.insert_one(new)
                return new, True
            except Exception:  # duplicate key race: someone inserted first
                doc = await self.col.find_one({"_id": uid})
                if doc is None:
                    raise
        la = doc.get("last_active")
        if la is not None and la.tzinfo is None:
            la = la.replace(tzinfo=timezone.utc)
        if la is None or t - la > timedelta(minutes=5) or doc.get("blocked"):
            upd = {"first_name": first_name, "last_name": last_name, "username": username,
                   "last_active": t, "blocked": False}
            await self.col.update_one({"_id": uid}, {"$set": upd})
            doc.update(upd)
        return doc, False

    async def get(self, uid: int):
        return await self.col.find_one({"_id": uid})

    async def set_language(self, uid: int, lang: str) -> None:
        await self.col.update_one({"_id": uid}, {"$set": {"language": lang}})

    async def mark_blocked(self, uid: int, blocked: bool = True) -> None:
        await self.col.update_one({"_id": uid}, {"$set": {"blocked": blocked}})

    async def count(self) -> int:
        return await self.col.estimated_document_count()

    async def count_audience(self) -> int:
        return await self.col.count_documents({"blocked": {"$ne": True}})

    async def count_since(self, since: datetime) -> int:
        return await self.col.count_documents({"first_seen": {"$gte": since}})

    async def iter_audience_ids(self, after_id: int = 0, limit: int = 100) -> list[int]:
        cur = self.col.find({"_id": {"$gt": after_id}, "blocked": {"$ne": True}}, {"_id": 1}).sort("_id", 1).limit(limit)
        return [d["_id"] async for d in cur]
