from __future__ import annotations
from datetime import datetime, timezone

# Counter semantics:
#  requests            valid permanent-link opens by a user (one per open, not per retry)
#  deliveries_ok       file messages successfully sent to a user
#  deliveries_failed   delivery attempts that raised a Telegram/permission error
#  public_confirmed    public-channel membership confirmed via getChatMember (idempotent per flow step)
#  private_requests    chat_join_request events received (deduplicated); NOT membership
COUNTERS = ("requests", "deliveries_ok", "deliveries_failed", "public_confirmed", "private_requests",
            "broadcast_ok", "broadcast_failed")


class StatisticsRepo:
    def __init__(self, col, writer):
        self.col, self.w = col, writer

    async def inc(self, counter: str, n: int = 1) -> None:
        assert counter in COUNTERS
        await self.w.inc(self.col, "global", counter, n, upsert=True)

    async def inc_new_user(self) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await self.w.inc(self.col, f"day:{day}", "new_users", 1, upsert=True)

    async def totals(self) -> dict:
        doc = await self.col.find_one({"_id": "global"}) or {}
        return {c: int(doc.get(c, 0)) for c in COUNTERS}

    async def new_users_by_day(self, days: int = 7) -> list[tuple[str, int]]:
        cur = self.col.find({"_id": {"$regex": "^day:"}}).sort("_id", -1).limit(days)
        return [(d["_id"][4:], int(d.get("new_users", 0))) async for d in cur]
