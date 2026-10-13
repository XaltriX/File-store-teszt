from __future__ import annotations
from dataclasses import dataclass
import time
from collections import OrderedDict
from datetime import datetime, timezone

from app.utils.tokens import is_valid_token


@dataclass
class Resolution:
    status: str  # ok | invalid | revoked | unavailable
    link: dict | None = None
    file: dict | None = None


class LinkService:
    TTL, MAX = 30.0, 2000

    def __init__(self, repos, bot_username: str, clock=time.monotonic):
        self.repos, self.bot_username, self.clock = repos, bot_username, clock
        self._cache: OrderedDict[str, tuple[float, tuple, Resolution]] = OrderedDict()

    def deep_link(self, token: str) -> str:
        return f"https://t.me/{self.bot_username}?start={token}"

    async def create(self, file_id: str) -> dict:
        return await self.repos.links.create(file_id)

    async def get_or_create(self, file_id: str) -> dict:
        return await self.repos.links.first_active_for_file(file_id) or await self.create(file_id)

    async def resolve(self, token: str | None) -> Resolution:
        """Cached 30 s; any file/link mutation bumps a repo version and drops stale entries."""
        if not is_valid_token(token):
            return Resolution("invalid")
        ver = (self.repos.files.version, self.repos.links.version)
        hit = self._cache.get(token)
        if hit and hit[1] == ver and self.clock() - hit[0] < self.TTL:
            self._cache.move_to_end(token)
            return hit[2]
        res = await self._resolve_db(token)
        if res.status == "ok" and not res.link.get("expires_at"):  # only cache plain, permanent hits
            self._cache[token] = (self.clock(), ver, res)
            while len(self._cache) > self.MAX:
                self._cache.popitem(last=False)
        return res

    async def _resolve_db(self, token: str) -> Resolution:
        link = await self.repos.links.get(token)
        if not link:
            return Resolution("invalid")
        exp = link.get("expires_at")
        if exp is not None:
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < datetime.now(timezone.utc):
                return Resolution("revoked", link)
        if link.get("status") != "active":
            return Resolution("revoked", link)
        file = await self.repos.files.get(link["file_id"])
        if not file or file.get("status") != "active":
            return Resolution("unavailable", link, file)
        return Resolution("ok", link, file)
