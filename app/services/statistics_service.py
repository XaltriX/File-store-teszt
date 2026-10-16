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
        today = await r.stats.today_totals()
        return {**await r.stats.totals(), "users": await r.users.count(), "files": await r.files.count(),
                "t_users": today["new_users"], "t_opens": today["requests"], "t_delivered": today["deliveries_ok"],
                "t_requests": today["private_requests"]}
