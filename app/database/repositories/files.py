from __future__ import annotations
import re
import uuid
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from app.utils.tokens import generate_token


def now() -> datetime:
    return datetime.now(timezone.utc)


class FilesRepo:
    def __init__(self, col):
        self.col = col
        self.version = 0  # bumped on every mutation; LinkService caches are keyed on it

    async def create(self, *, file_id: str, file_unique_id: str, ftype: str, name, size, mime, caption,
                     source: dict | None = None, order: int = 0, store: dict | None = None,
                     duration: int | None = None) -> tuple[dict, bool]:
        """Insert unless file_unique_id exists. Returns (doc, created). Archived duplicates are re-activated."""
        existing = await self.col.find_one({"file_unique_id": file_unique_id})
        if existing:
            upd = {}
            if existing.get("status") != "active":
                upd["status"] = "active"
            if duration and not existing.get("duration"):
                upd["duration"] = duration
            if store and not existing.get("store"):
                upd["store"] = store  # first DB-channel copy wins; never overwritten
            if upd:
                await self.col.update_one({"_id": existing["_id"]}, {"$set": upd})
                existing.update(upd)
                self.version += 1
            return existing, False
        doc = {"_id": uuid.uuid4().hex[:12], "file_id": file_id, "file_unique_id": file_unique_id,
               "type": ftype, "name": name, "name_lc": (name or "").lower(), "size": size, "mime": mime,
               "caption": caption, "source": source, "created_at": now(), "status": "active",
               "protect": None, "meta": {}, "order": order, "store": store, "duration": duration}
        try:
            await self.col.insert_one(doc)
            return doc, True
        except DuplicateKeyError:
            return await self.col.find_one({"file_unique_id": file_unique_id}), False

    async def get(self, fid: str):
        return await self.col.find_one({"_id": fid})

    async def get_by_unique(self, unique_id: str):
        return await self.col.find_one({"file_unique_id": unique_id})

    async def list(self, page: int, size: int = 8, query: str | None = None, status: str = "active"):
        flt = self._flt(query, status)
        cur = self.col.find(flt).sort("created_at", -1).skip(page * size).limit(size)
        return [d async for d in cur]

    async def count(self, query: str | None = None, status: str = "active") -> int:
        return await self.col.count_documents(self._flt(query, status))

    @staticmethod
    def _flt(query, status):
        flt: dict = {"status": status}
        if query:
            flt["name_lc"] = {"$regex": re.escape(query.lower())}
        return flt

    async def set_protect(self, fid: str, value) -> None:
        await self.col.update_one({"_id": fid}, {"$set": {"protect": value}})
        self.version += 1

    async def set_duration(self, fid: str, seconds: int | None) -> None:
        await self.col.update_one({"_id": fid}, {"$set": {"duration": seconds}})
        self.version += 1

    async def set_status(self, fid: str, status: str) -> None:
        await self.col.update_one({"_id": fid}, {"$set": {"status": status}})
        self.version += 1

    async def delete(self, fid: str) -> None:
        await self.col.delete_one({"_id": fid})
        self.version += 1


class LinksRepo:
    def __init__(self, col):
        self.col = col
        self.version = 0

    async def create(self, file_id: str, expires_at=None) -> dict:
        for _ in range(5):
            doc = {"token": generate_token(), "file_id": file_id, "created_at": now(),
                   "status": "active", "expires_at": expires_at, "meta": {}}
            try:
                await self.col.insert_one(doc)
                return doc
            except DuplicateKeyError:
                continue
        raise RuntimeError("could not allocate unique token")

    async def get(self, token: str):
        return await self.col.find_one({"token": token})

    async def first_active_for_file(self, file_id: str):
        return await self.col.find_one({"file_id": file_id, "status": "active"}, sort=[("created_at", 1)])

    async def for_file(self, file_id: str, limit: int = 6):
        return [d async for d in self.col.find({"file_id": file_id}).sort("created_at", -1).limit(limit)]

    async def revoke(self, token: str) -> bool:
        r = await self.col.update_one({"token": token, "status": "active"}, {"$set": {"status": "revoked"}})
        self.version += 1
        return r.modified_count == 1

    async def delete_for_file(self, file_id: str) -> None:
        await self.col.delete_many({"file_id": file_id})
        self.version += 1

    async def count_active(self) -> int:
        return await self.col.count_documents({"status": "active"})
