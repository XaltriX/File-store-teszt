from __future__ import annotations
import time
from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


COUNTERS = ("presented", "joins_confirmed", "requests_received", "requests_repeat", "requests_other",
            "joins_total", "leaves_total", "failed_checks", "errors")


class ChannelsRepo:
    """Hot reads (get/eligible) are cached for TTL seconds and invalidated on any admin write.
    Counter increments go through the write-behind buffer AND bump the cached copy, so balancing
    sees them immediately without a DB round-trip."""
    TTL = 20.0

    def __init__(self, col, writer, clock=time.monotonic):
        self.col, self.w, self.clock = col, writer, clock
        self._by_id: dict[int, tuple[float, dict]] = {}
        self._elig: tuple[float, list[dict]] | None = None

    def _invalidate(self) -> None:
        self._by_id.clear()
        self._elig = None

    def _bump(self, chat_id: int, counter: str, n: int) -> None:
        hit = self._by_id.get(chat_id)
        if hit:
            hit[1][counter] = hit[1].get(counter, 0) + n
        if self._elig:
            for c in self._elig[1]:
                if c["_id"] == chat_id:
                    c[counter] = c.get(counter, 0) + n

    async def upsert(self, chat_id: int, *, title: str, mode: str, username: str | None, url: str) -> dict:
        """mode: 'public' (membership check) | 'request' (join-request event)."""
        fields = {"title": title, "mode": mode, "username": username, "url": url, "active": True, "removed": False}
        await self.col.update_one(
            {"_id": chat_id},
            {"$set": fields, "$setOnInsert": {"created_at": now(), "last_activity": None,
                                              **{c: 0 for c in COUNTERS}}},
            upsert=True)
        self._invalidate()
        return await self.get(chat_id)

    async def get(self, chat_id: int):
        hit = self._by_id.get(chat_id)
        if hit and self.clock() - hit[0] < self.TTL:
            return dict(hit[1])
        doc = await self.col.find_one({"_id": chat_id})
        if doc:
            self._by_id[chat_id] = (self.clock(), doc)
            return dict(doc)
        return None

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
        if self._elig and self.clock() - self._elig[0] < self.TTL:
            return [dict(c) for c in self._elig[1]]
        docs = await self.list(include_inactive=False)
        self._elig = (self.clock(), docs)
        return [dict(c) for c in docs]

    async def inc(self, chat_id: int, counter: str, n: int = 1) -> None:
        assert counter in COUNTERS
        self._bump(chat_id, counter, n)
        await self.w.inc(self.col, chat_id, counter, n, set_fields={"last_activity": now()})

    async def inc_many(self, chat_ids: list[int], counter: str = "presented") -> None:
        for cid in chat_ids:
            await self.inc(cid, counter)

    async def set(self, chat_id: int, **fields) -> None:
        await self.col.update_one({"_id": chat_id}, {"$set": fields})
        self._invalidate()

    async def soft_remove(self, chat_id: int) -> None:
        await self.col.update_one({"_id": chat_id}, {"$set": {"removed": True, "active": False}})
        self._invalidate()

    async def reset_stats(self) -> None:
        """Zero the statistics counters (keeps `presented`, which drives channel balancing)."""
        zero = {c: 0 for c in COUNTERS if c != "presented"}
        await self.col.update_many({}, {"$set": zero})
        self._invalidate()

    async def reset_presented(self) -> None:
        await self.col.update_many({}, {"$set": {"presented": 0}})
        self._invalidate()
