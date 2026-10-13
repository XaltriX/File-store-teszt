from __future__ import annotations
import time
from collections import defaultdict, deque

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery


class ThrottleMiddleware(BaseMiddleware):
    """In-memory sliding window per user (abuse control; admins exempt)."""

    def __init__(self, admin_ids, limit: int = 10, window: float = 10.0, clock=time.monotonic):
        self.admin_ids, self.limit, self.window, self.clock = frozenset(admin_ids), limit, window, clock
        self.hits: dict[int, deque] = defaultdict(deque)

    def allow(self, uid: int) -> bool:
        now, q = self.clock(), self.hits[uid]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True

    async def __call__(self, handler, event, data):
        u = data.get("event_from_user")
        if u is None or u.id in self.admin_ids or self.allow(u.id):
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer("⏳", show_alert=False)
        return None
