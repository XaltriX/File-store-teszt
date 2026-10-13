"""In-memory stand-ins for MongoDB collections and the Telegram bot (no network, no real DB)."""
from __future__ import annotations
import copy
import re
from datetime import datetime

from pymongo.errors import DuplicateKeyError


class Result:
    def __init__(self, matched=0, modified=0, upserted_id=None, deleted=0):
        self.matched_count, self.modified_count, self.upserted_id, self.deleted_count = matched, modified, upserted_id, deleted


def _get(doc, path):
    return doc.get(path)


def _match_value(val, cond) -> bool:
    if isinstance(cond, dict) and cond and all(k.startswith("$") for k in cond):
        for op, arg in cond.items():
            if op == "$ne":
                if (arg in val) if isinstance(val, list) else (val == arg):
                    return False
            elif op == "$gt":
                if val is None or not val > arg: return False
            elif op == "$gte":
                if val is None or not val >= arg: return False
            elif op == "$lt":
                if val is None or not val < arg: return False
            elif op == "$lte":
                if val is None or not val <= arg: return False
            elif op == "$in":
                if val not in arg: return False
            elif op == "$regex":
                if val is None or not re.search(arg, val): return False
            else:
                raise NotImplementedError(op)
        return True
    if isinstance(val, list) and not isinstance(cond, list):
        return cond in val
    return val == cond


def matches(doc: dict, flt: dict) -> bool:
    for k, cond in flt.items():
        if k == "$expr":  # only the pattern used by claim_completion: size(done) >= required
            if not len(doc["done"]) >= doc["required"]:
                return False
            continue
        if not _match_value(_get(doc, k), cond):
            return False
    return True


class Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction=1):
        if isinstance(key, list):
            key, direction = key[0]
        self.docs.sort(key=lambda d: (d.get(key) is None, d.get(key)), reverse=direction < 0)
        return self

    def skip(self, n):
        self.docs = self.docs[n:]
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    def __aiter__(self):
        self._it = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return copy.deepcopy(next(self._it))
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, unique: list[tuple[str, ...]] | None = None):
        self.docs: list[dict] = []
        self.unique = unique or []

    def _find(self, flt):
        return [d for d in self.docs if matches(d, flt or {})]

    def _check_unique(self, doc, ignore=None):
        for d in self.docs:
            if d is ignore:
                continue
            if d.get("_id") is not None and d.get("_id") == doc.get("_id"):
                raise DuplicateKeyError("dup _id")
            for cols in self.unique:
                if all(c in doc and c in d and d[c] == doc[c] for c in cols):
                    raise DuplicateKeyError(f"dup {cols}")

    async def find_one(self, flt=None, projection=None, sort=None):
        found = self._find(flt)
        if sort:
            key, direction = sort[0]
            found.sort(key=lambda d: d.get(key), reverse=direction < 0)
        return copy.deepcopy(found[0]) if found else None

    def find(self, flt=None, projection=None):
        return Cursor(copy.deepcopy(self._find(flt)))

    async def insert_one(self, doc):
        doc.setdefault("_id", __import__("uuid").uuid4().hex)  # pymongo assigns _id the same way
        self._check_unique(doc)
        self.docs.append(copy.deepcopy(doc))
        return Result(upserted_id=doc.get("_id"))

    def _apply(self, doc, upd):
        for op, fields in upd.items():
            for k, v in fields.items():
                if op == "$set": doc[k] = v
                elif op == "$inc": doc[k] = doc.get(k, 0) + v
                elif op == "$push": doc.setdefault(k, []).append(v)
                elif op == "$max": doc[k] = max(doc.get(k, v), v)
                elif op == "$setOnInsert": pass
                else: raise NotImplementedError(op)

    async def update_one(self, flt, upd, upsert=False):
        found = self._find(flt)
        if found:
            before = copy.deepcopy(found[0])
            self._apply(found[0], upd)
            return Result(1, 1 if found[0] != before or "$inc" in upd else 0)
        if not upsert:
            return Result(0, 0)
        new = {k: v for k, v in flt.items() if not isinstance(v, dict) and not k.startswith("$")}
        self._check_unique(new)
        self._apply(new, {k: v for k, v in upd.items() if k != "$setOnInsert"})
        for k, v in upd.get("$setOnInsert", {}).items():
            new.setdefault(k, v)
        self.docs.append(new)
        return Result(0, 0, upserted_id=new.get("_id", "x"))

    async def update_many(self, flt, upd):
        n = 0
        for d in self._find(flt):
            self._apply(d, upd)
            n += 1
        return Result(n, n)

    async def find_one_and_update(self, flt, upd):
        found = self._find(flt)
        if not found:
            return None
        before = copy.deepcopy(found[0])
        self._apply(found[0], upd)
        return before

    async def delete_one(self, flt):
        f = self._find(flt)
        if f:
            self.docs.remove(f[0])
        return Result(deleted=1 if f else 0)

    async def delete_many(self, flt):
        f = self._find(flt)
        for d in f:
            self.docs.remove(d)
        return Result(deleted=len(f))

    async def count_documents(self, flt):
        return len(self._find(flt))

    async def estimated_document_count(self):
        return len(self.docs)


class FakeDB:
    UNIQUE = {"files": [("file_unique_id",)], "links": [("token",)],
              "join_requests": [("chat_id", "user_id", "request_ts")],
              "broadcast_deliveries": [("bid", "uid")]}

    def __init__(self):
        self.cols: dict[str, FakeCollection] = {}

    def __getitem__(self, name):
        if name not in self.cols:
            self.cols[name] = FakeCollection(self.UNIQUE.get(name))
        return self.cols[name]


# ---------------------------------------------------------------- Telegram
class FakeBot:
    """Records calls. Set .raise_on[method] = exception (or list for sequential failures)."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.raise_on: dict[str, object] = {}
        self.members: dict[tuple[int, int], object] = {}

    def _maybe_raise(self, method):
        r = self.raise_on.get(method)
        if isinstance(r, list):
            if r:
                exc = r.pop(0)
                if exc is not None:
                    raise exc
        elif r is not None:
            raise r

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            self._maybe_raise(name)
            self.calls.append((name, {"args": args, **kwargs}))
            if name == "get_chat_member":
                return self.members.get((args[0], args[1]), type("M", (), {"status": "left"})())
            return type("Msg", (), {"message_id": len(self.calls)})()
        return call

    def sent(self, method):
        return [c for c in self.calls if c[0] == method]
