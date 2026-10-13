"""Write-behind buffer for non-critical writes (counters, last-activity, auto-delete schedule).
Keeps them OFF the request path: they are aggregated in memory and flushed in a few round-trips every
`interval` seconds and on shutdown. Disabled (write-through) by default so tests/tools see exact state."""
from __future__ import annotations
import asyncio
import logging

log = logging.getLogger(__name__)
MAX_PENDING_INSERTS = 20000


class WriteBehind:
    def __init__(self, interval: float = 1.5, enabled: bool = False):
        self.interval, self.enabled = interval, enabled
        self._upd: dict[tuple[int, object], dict] = {}
        self._ins: dict[int, tuple[object, list]] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.flushes = 0

    # ------------------------------------------------------------------ producers
    async def inc(self, col, key, field: str, n: int = 1, set_fields: dict | None = None, upsert: bool = False) -> None:
        if not self.enabled:
            upd = {"$inc": {field: n}}
            if set_fields:
                upd["$set"] = set_fields
            await col.update_one({"_id": key}, upd, upsert=upsert)
            return
        e = self._upd.setdefault((id(col), key), {"col": col, "inc": {}, "set": {}, "upsert": False})
        e["inc"][field] = e["inc"].get(field, 0) + n
        e["upsert"] = e["upsert"] or upsert
        if set_fields:
            e["set"].update(set_fields)

    async def set(self, col, key, fields: dict, upsert: bool = False) -> None:
        if not self.enabled:
            await col.update_one({"_id": key}, {"$set": fields}, upsert=upsert)
            return
        e = self._upd.setdefault((id(col), key), {"col": col, "inc": {}, "set": {}, "upsert": False})
        e["set"].update(fields)
        e["upsert"] = e["upsert"] or upsert

    async def insert(self, col, doc: dict) -> None:
        if not self.enabled or sum(len(v[1]) for v in self._ins.values()) >= MAX_PENDING_INSERTS:
            await col.insert_one(doc)  # buffer full (or disabled): fall back to a direct write, never drop
            return
        self._ins.setdefault(id(col), (col, []))[1].append(doc)

    # ------------------------------------------------------------------ flushing
    async def flush(self) -> None:
        upd, self._upd = self._upd, {}
        ins, self._ins = self._ins, {}
        ops = []
        for (_, key), e in upd.items():
            body = {}
            if e["inc"]:
                body["$inc"] = e["inc"]
            if e["set"]:
                body["$set"] = e["set"]
            ops.append(self._safe(e["col"].update_one({"_id": key}, body, upsert=e["upsert"]), "update", e, key))
        for col, docs in ins.values():
            ops.append(self._safe(col.insert_many(docs, ordered=False), "insert", (col, docs), None))
        if ops:
            await asyncio.gather(*ops)
            self.flushes += 1

    async def _safe(self, coro, kind: str, payload, key) -> None:
        try:
            await coro
        except Exception:  # noqa: BLE001 - never lose data to a DB blip: merge it back and retry next tick
            log.warning("write-behind %s failed, will retry", kind)
            if kind == "update":
                e = self._upd.setdefault((id(payload["col"]), key), {"col": payload["col"], "inc": {}, "set": {}, "upsert": payload["upsert"]})
                for f, n in payload["inc"].items():
                    e["inc"][f] = e["inc"].get(f, 0) + n
                for f, v in payload["set"].items():
                    e["set"].setdefault(f, v)
            else:
                col, docs = payload
                self._ins.setdefault(id(col), (col, []))[1].extend(docs)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass
            await self.flush()

    def start(self) -> None:
        self.enabled = True
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="write-behind")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
        await self.flush()
        self.enabled = False
