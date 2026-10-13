from __future__ import annotations
import asyncio
import logging

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery

log = logging.getLogger(__name__)


class ConcurrencyMiddleware(BaseMiddleware):
    """Different users run in parallel; the same user's updates stay in order; a global cap protects
    MongoDB; and no single update may run longer than `timeout` (a hung call can't freeze the bot)."""

    def __init__(self, max_concurrent: int = 40, timeout: float = 45.0, admin_ids=()):
        self.sem = asyncio.Semaphore(max_concurrent)
        self.admin_ids = frozenset(admin_ids)
        self.timeout = timeout
        self._locks: dict[int, list] = {}  # uid -> [lock, refcount]

    def _acquire(self, uid: int) -> asyncio.Lock:
        entry = self._locks.setdefault(uid, [asyncio.Lock(), 0])
        entry[1] += 1
        return entry[0]

    def _release(self, uid: int) -> None:
        entry = self._locks[uid]
        entry[1] -= 1
        if entry[1] <= 0:
            del self._locks[uid]

    async def _run(self, handler, event, data, uid):
        try:
            return await asyncio.wait_for(handler(event, data), self.timeout)
        except asyncio.TimeoutError:
            log.error("update timed out after %.0fs (user=%s)", self.timeout, uid)
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer()
                except TelegramAPIError:
                    pass
            return None

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        uid = user.id if user else 0
        lock = self._acquire(uid)
        try:
            async with lock:
                if uid in self.admin_ids:  # admins are never queued behind the user crowd
                    return await self._run(handler, event, data, uid)
                async with self.sem:
                    return await self._run(handler, event, data, uid)
        finally:
            self._release(uid)
