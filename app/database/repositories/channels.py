from __future__ import annotations
from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


COUNTERS = ("presented", "joins_confirmed", "requests_received", "failed_checks", "errors")


class ChannelsRepo:
    def __init__(self, col):
        self.col = col

    async def upsert(self, chat_id: int, *, title: str, mode: str, username: str | None, url: str) -> dict:
        """mode: 'public' (membership check) | 'request' (join-request event)."""
        fields = {"title": title, "mode": mode, "username": username, "url": url, "active": True, "removed": False}
        await self.col.update_one(
            {"_id": chat_id},
            {"$set": fields, "$setOnInsert": {"created_at": now(), "last_activity": None,
                                              **{c: 0 for c in COUNTERS}}},
            upsert=True)
        return await self.get(chat_id)

    async def get(self, chat_id: int):
        return await self.col.find_one({"_id": chat_id})

    async def list(self, include_inactive: bool = True, page: int | None = None, size: int = 8):
        flt: dict = {"removed": {"$ne": True}}
        if not include_inactive:
            flt["active"] = True
        cur = self.col.find(flt).sort("created_at", 1)
        if page is not None:
            cur = cur.skip(page * size).limit(size)
        return [d async for d in cur]

    async def count(self, active_only: bool = False) -> int:
        flt: dict = {"removed": {"$ne": True}}
        if active_only:
            flt["active"] = True
        return await self.col.count_documents(flt)

    async def eligible(self):
        return await self.list(include_inactive=False)

    async def inc(self, chat_id: int, counter: str, n: int = 1) -> None:
        assert counter in COUNTERS
        await self.col.update_one({"_id": chat_id}, {"$inc": {counter: n}, "$set": {"last_activity": now()}})

    async def inc_many(self, chat_ids: list[int], counter: str = "presented") -> None:
        await self.col.update_many({"_id": {"$in": chat_ids}}, {"$inc": {counter: 1}, "$set": {"last_activity": now()}})

    async def set(self, chat_id: int, **fields) -> None:
        await self.col.update_one({"_id": chat_id}, {"$set": fields})

    async def soft_remove(self, chat_id: int) -> None:
        await self.col.update_one({"_id": chat_id}, {"$set": {"removed": True, "active": False}})

    async def reset_presented(self) -> None:
        await self.col.update_many({}, {"$set": {"presented": 0}})
