from __future__ import annotations
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


class UsersRepo:
    CACHE_TTL, CACHE_MAX = 60.0, 5000

    def __init__(self, col, writer, clock=time.monotonic):
        self.col, self.w, self.clock = col, writer, clock
        self._cache: OrderedDict[int, tuple[float, dict]] = OrderedDict()

    def _remember(self, doc: dict) -> None:
        self._cache[doc["_id"]] = (self.clock(), dict(doc))
        self._cache.move_to_end(doc["_id"])
        while len(self._cache) > self.CACHE_MAX:
            self._cache.popitem(last=False)

    async def touch(self, uid: int, first_name, last_name, username) -> tuple[dict, bool]:
        """Create or refresh a user. Returns (doc, is_new). Cached 60 s; DB writes throttled to 1/5min."""
        hit = self._cache.get(uid)
        if hit and self.clock() - hit[0] < self.CACHE_TTL:
            return dict(hit[1]), False
        doc, is_new = await self._touch_db(uid, first_name, last_name, username)
        self._remember(doc)
        return dict(doc), is_new

    async def _touch_db(self, uid: int, first_name, last_name, username) -> tuple[dict, bool]:
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
        hit = self._cache.get(uid)
        if hit and self.clock() - hit[0] < self.CACHE_TTL:
            return dict(hit[1])
        doc = await self.col.find_one({"_id": uid})
        if doc:
            self._remember(doc)
        return doc

    async def set_fsub(self, uid: int, channel_ids: list[int]) -> None:
        """Remember which channels the user last satisfied (returning users skip flows entirely)."""
        if uid in self._cache:
            self._cache[uid][1]["fsub_ids"] = list(channel_ids)
        await self.w.set(self.col, uid, {"fsub_ids": list(channel_ids)}, upsert=True)

    async def set_language(self, uid: int, lang: str) -> None:
        await self.col.update_one({"_id": uid}, {"$set": {"language": lang, "lang_chosen": True}})
        if uid in self._cache:
            self._cache[uid][1].update(language=lang, lang_chosen=True)

    async def mark_blocked(self, uid: int, blocked: bool = True) -> None:
        await self.col.update_one({"_id": uid}, {"$set": {"blocked": blocked}})
        self._cache.pop(uid, None)

    async def count(self) -> int:
        return await self.col.estimated_document_count()

    async def count_audience(self) -> int:
        return await self.col.count_documents({"blocked": {"$ne": True}})

    async def count_since(self, since: datetime) -> int:
        return await self.col.count_documents({"first_seen": {"$gte": since}})

    async def iter_audience_ids(self, after_id: int = 0, limit: int = 100) -> list[int]:
        cur = self.col.find({"_id": {"$gt": after_id}, "blocked": {"$ne": True}}, {"_id": 1}).sort("_id", 1).limit(limit)
        return [d["_id"] async for d in cur]
