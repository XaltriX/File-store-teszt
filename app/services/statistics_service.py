from __future__ import annotations
from datetime import datetime, timedelta, timezone


class StatisticsService:
    def __init__(self, repos):
        self.repos = repos

    async def dashboard(self) -> dict:
        r = self.repos
        totals = await r.stats.totals()
        return {"users": await r.users.count(), "files": await r.files.count(),
                "channels": await r.channels.count(active_only=True), "requests": totals["requests"]}

    async def detailed(self) -> dict:
        r = self.repos
        since = datetime.now(timezone.utc) - timedelta(days=7)
        return {**await r.stats.totals(), "users": await r.users.count(),
                "audience": await r.users.count_audience(), "new_7d": await r.users.count_since(since),
                "files": await r.files.count(), "links": await r.links.count_active(),
                "by_day": await r.stats.new_users_by_day(7)}
