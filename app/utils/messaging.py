from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter

from app.utils.telegram_errors import is_not_modified

log = logging.getLogger(__name__)


async def edit_or_send(msg, text: str, kb=None) -> None:
    """Edit in place (text or caption); fall back to delete+send. Never raises on benign failures."""
    try:
        if msg.text is not None:
            await msg.edit_text(text, reply_markup=kb)
        else:
            await msg.edit_caption(caption=text, reply_markup=kb)
        return
    except TelegramBadRequest as e:
        if is_not_modified(e):
            return
        log.debug("edit failed (%s); re-sending", e)
    except TelegramAPIError as e:
        log.warning("edit failed: %s", e)
        return
    try:
        await msg.delete()
    except TelegramAPIError:
        pass
    try:
        await msg.answer(text, reply_markup=kb)
    except TelegramAPIError as e:
        log.warning("send fallback failed: %s", e)


class MessageLifecycle:
    """Persisted auto-delete queue + single worker. Survives dyno restarts (due items are
    processed on startup). One task, bounded batch per tick, no per-message sleeps."""

    def __init__(self, bot, col, settings_repo, tick: float = 2.0, batch: int = 100, concurrency: int = 20, writer=None):
        self.bot, self.col, self.settings, self.writer = bot, col, settings_repo, writer
        self.tick, self.batch, self.concurrency = tick, batch, concurrency
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def schedule(self, chat_id: int, message_id: int, delay: int | None = None) -> None:
        if delay is None:
            delay = int((await self.settings.get()).get("autodelete_seconds", 60))
        if not delay or delay <= 0:
            return
        due = datetime.now(timezone.utc) + timedelta(seconds=delay)
        doc = {"chat_id": chat_id, "message_id": message_id, "due": due}
        if self.writer is not None:
            await self.writer.insert(self.col, doc)  # batched with other schedules
        else:
            await self.col.insert_one(doc)

    async def run_once(self) -> int:
        now = datetime.now(timezone.utc)
        items = [d async for d in self.col.find({"due": {"$lte": now}}).limit(self.batch)]
        if not items:
            return 0
        sem = asyncio.Semaphore(self.concurrency)

        async def one(d) -> object | None:
            async with sem:
                try:
                    await self.bot.delete_message(d["chat_id"], d["message_id"])
                except TelegramRetryAfter:
                    return None  # flood wait: keep the row, next tick retries
                except TelegramAPIError as e:  # already deleted / too old / no rights: never fatal
                    log.debug("auto-delete skipped: %s", e)
                return d["_id"]

        done = [i for i in await asyncio.gather(*(one(d) for d in items)) if i is not None]
        if done:
            await self.col.delete_many({"_id": {"$in": done}})
        return len(done)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001 - worker must survive DB blips
                log.exception("lifecycle tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="lifecycle")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
