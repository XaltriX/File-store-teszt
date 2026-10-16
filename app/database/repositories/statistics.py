from __future__ import annotations
from app.utils.clock import today

# Counter semantics:
#  requests            valid permanent-link opens by a user (one per open, not per retry)
#  deliveries_ok       file messages successfully sent to a user
#  deliveries_failed   delivery attempts that raised a Telegram/permission error
#  public_confirmed    public-channel membership confirmed via getChatMember (idempotent per flow step)
#  private_requests    chat_join_request events received (deduplicated); NOT membership
TODAY_COUNTERS = ("requests", "deliveries_ok", "private_requests")
COUNTERS = ("requests", "deliveries_ok", "deliveries_failed", "public_confirmed", "private_requests",
            "broadcast_ok", "broadcast_failed")


class StatisticsRepo:
    def __init__(self, col, writer):
        self.col, self.w = col, writer

    async def inc(self, counter: str, n: int = 1) -> None:
        assert counter in COUNTERS
        await self.w.inc(self.col, "global", counter, n, upsert=True)
        if counter in TODAY_COUNTERS:
            await self.w.inc(self.col, f"day:{today()}", counter, n, upsert=True)

    async def inc_new_user(self) -> None:
        await self.w.inc(self.col, f"day:{today()}", "new_users", 1, upsert=True)

    async def today_totals(self) -> dict:
        doc = await self.col.find_one({"_id": f"day:{today()}"}) or {}
        return {k: int(doc.get(k, 0)) for k in ("new_users", *TODAY_COUNTERS)}

    # ---- per-channel, per-day counters: requests / joins / leaves / other (requests via other links)
    async def inc_channel_day(self, chat_id: int, field: str, n: int = 1) -> None:
        await self.w.inc(self.col, f"chd:{chat_id}:{today()}", field, n, upsert=True)

    async def channel_days(self, chat_ids: list[int]) -> dict[int, dict]:
        day = today()
        ids = [f"chd:{c}:{day}" for c in chat_ids]
        out: dict[int, dict] = {}
        async for d in self.col.find({"_id": {"$in": ids}}):
            out[int(d["_id"].split(":")[1])] = d
        return out

    async def reset_channel_days(self) -> None:
        await self.col.delete_many({"_id": {"$regex": "^chd:"}})

    async def totals(self) -> dict:
        doc = await self.col.find_one({"_id": "global"}) or {}
        return {c: int(doc.get(c, 0)) for c in COUNTERS}

    async def new_users_by_day(self, days: int = 7) -> list[tuple[str, int]]:
        cur = self.col.find({"_id": {"$regex": "^day:"}}).sort("_id", -1).limit(days)
        return [(d["_id"][4:], int(d.get("new_users", 0))) async for d in cur]
