from __future__ import annotations
import asyncio
import logging

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramRetryAfter

log = logging.getLogger(__name__)


class FloodControlMiddleware(BaseRequestMiddleware):
    """Outgoing-request middleware: caps simultaneous Telegram calls and transparently waits out
    flood limits (`retry_after`) instead of failing the user's request. getUpdates is exempt."""

    def __init__(self, max_concurrent: int = 30, max_wait: float = 30.0, retries: int = 2, sleeper=asyncio.sleep):
        self.sem = asyncio.Semaphore(max_concurrent)
        self.max_wait, self.retries, self.sleep = max_wait, retries, sleeper

    async def __call__(self, make_request, bot, method):
        if getattr(method, "__api_method__", "") == "getUpdates":
            return await make_request(bot, method)
        attempt = 0
        while True:
            try:
                async with self.sem:
                    return await make_request(bot, method)
            except TelegramRetryAfter as exc:
                attempt += 1
                wait = float(exc.retry_after)
                if attempt > self.retries or wait > self.max_wait:
                    raise
                log.warning("Telegram flood wait %.1fs on %s", wait, getattr(method, "__api_method__", "?"))
                await self.sleep(wait + 0.5)  # sleeping outside the semaphore: other calls keep flowing
