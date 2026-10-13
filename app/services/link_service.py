from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone

from app.utils.tokens import is_valid_token


@dataclass
class Resolution:
    status: str  # ok | invalid | revoked | unavailable
    link: dict | None = None
    file: dict | None = None


class LinkService:
    def __init__(self, repos, bot_username: str):
        self.repos, self.bot_username = repos, bot_username

    def deep_link(self, token: str) -> str:
        return f"https://t.me/{self.bot_username}?start={token}"

    async def create(self, file_id: str) -> dict:
        return await self.repos.links.create(file_id)

    async def get_or_create(self, file_id: str) -> dict:
        return await self.repos.links.first_active_for_file(file_id) or await self.create(file_id)

    async def resolve(self, token: str | None) -> Resolution:
        if not is_valid_token(token):
            return Resolution("invalid")
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
