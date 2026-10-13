from __future__ import annotations
import asyncio
import logging
import random
import time

from app.utils.telegram_errors import is_blocked, is_retryable, retry_after

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 4


class BroadcastService:
    """One job at a time, batches of <=rate recipients per second, resumable via cursor +
    per-recipient delivery records. No unbounded task creation: one runner task + bounded gather."""

    def __init__(self, bot, repos, rate: int = 20, sleeper=asyncio.sleep):
        self.bot, self.repos, self.rate, self.sleep = bot, repos, rate, sleeper
        self.notify = None  # async (job: dict, final: bool) -> None, set by UI layer
        self._task: asyncio.Task | None = None
        self._cancel: set[str] = set()
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def create(self, admin_id: int, src_chat: int, src_msg: int, preview: str) -> dict:
        target = await self.repos.users.count_audience()
        return await self.repos.broadcasts.create(admin_id, src_chat, src_msg, target, preview)

    def launch(self, bid: str) -> bool:
        if self.running:
            return False
        self._task = asyncio.create_task(self._run(bid), name=f"broadcast-{bid}")
        return True

    async def cancel(self, bid: str) -> None:
        self._cancel.add(bid)

    async def resume_unfinished(self) -> None:
        for job in await self.repos.broadcasts.unfinished():
            if self.launch(job["_id"]):
                log.info("resuming broadcast %s", job["_id"])
            break  # single runner; remaining jobs resume on next restart/stop

    async def shutdown(self) -> None:
        self._stopping = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # -------------------------------------------------------------- runner
    async def _run(self, bid: str) -> None:
        repo = self.repos.broadcasts
        job = await repo.get(bid)
        if not job or not (await repo.start(bid) or job["status"] == "running"):
            return
        last_notify = 0.0
        try:
            cursor = job.get("cursor", 0)
            while True:
                if bid in self._cancel:
                    await repo.set_status(bid, "cancelled", finished=True)
                    break
                t0 = time.monotonic()
                ids = await self.repos.users.iter_audience_ids(cursor, self.rate)
                if not ids:
                    await repo.set_status(bid, "completed", finished=True)
                    break
                todo = [u for u in ids if not await repo.already_done(bid, u)]
                await asyncio.gather(*(self._send_one(job, u) for u in todo))
                cursor = ids[-1]
                await repo.advance_cursor(bid, cursor)
                if time.monotonic() - last_notify >= 3:
                    last_notify = time.monotonic()
                    await self._notify(bid, False)
                await self.sleep(max(0.0, 1.0 - (time.monotonic() - t0)))
        except asyncio.CancelledError:
            await repo.set_status(bid, "interrupted")  # resumed on next startup
            raise
        except Exception:  # noqa: BLE001
            log.exception("broadcast %s failed", bid)
            await repo.set_status(bid, "failed", finished=True)
        finally:
            self._cancel.discard(bid)
        if not self._stopping:
            await self._notify(bid, True)

    async def _notify(self, bid: str, final: bool) -> None:
        if self.notify:
            try:
                await self.notify(await self.repos.broadcasts.get(bid), final)
            except Exception:  # noqa: BLE001
                log.exception("broadcast notify failed")

    async def _send_one(self, job: dict, uid: int) -> None:
        repo, bid = self.repos.broadcasts, job["_id"]
        err = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                await self.bot.copy_message(uid, job["src_chat"], job["src_msg"])
                await repo.record(bid, uid, "success")
                await self.repos.stats.inc("broadcast_ok")
                return
            except Exception as exc:  # noqa: BLE001
                err = type(exc).__name__
                if is_blocked(exc):
                    await self.repos.users.mark_blocked(uid)
                    await repo.record(bid, uid, "blocked", err)
                    await self.repos.stats.inc("broadcast_failed")
                    return
                if is_retryable(exc) and attempt < MAX_ATTEMPTS:
                    delay = retry_after(exc) if hasattr(exc, "retry_after") else (2 ** attempt) + random.random()
                    await self.sleep(min(delay, 30))
                    continue
                break
        await repo.record(bid, uid, "failed", err)
        await self.repos.stats.inc("broadcast_failed")
