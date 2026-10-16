from __future__ import annotations
import asyncio
import logging
import math

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.utils.telegram_errors import is_blocked, is_retryable, retry_after
from app.utils.fancy import bold
from app.utils.formatting import share_url

log = logging.getLogger(__name__)

SEND_METHOD = {"document": "send_document", "video": "send_video", "audio": "send_audio",
               "voice": "send_voice", "animation": "send_animation", "video_note": "send_video_note",
               "sticker": "send_sticker", "photo": "send_photo"}
DURATION_TYPES = {"video", "audio", "voice", "animation", "video_note"}
CAPTIONABLE = {"document", "video", "audio", "voice", "animation", "photo"}
MAX_ATTEMPTS = 3
AUTO_MARGIN = 60      # seconds added to the media length
AUTO_FALLBACK = 300   # files with no length (documents, photos): 5 min
MAX_AUTO = 24 * 3600  # Telegram only lets bots delete recent messages


class DeliveryService:
    def __init__(self, bot, repos, i18n, links, lifecycle=None):
        self.bot, self.repos, self.i18n, self.links, self.lifecycle = bot, repos, i18n, links, lifecycle

    async def effective_protect(self, file: dict) -> bool:
        if file.get("protect") is not None:
            return bool(file["protect"])
        return bool((await self.repos.settings.get()).get("default_protect"))

    def share_keyboard(self, lang: str, token: str) -> InlineKeyboardMarkup:
        btn = InlineKeyboardButton(text=bold(self.i18n.t(lang, "btn_share")), url=share_url(self.links.deep_link(token)))
        return InlineKeyboardMarkup(inline_keyboard=[[btn]])

    async def _retrying(self, send):
        """Run one send with bounded retries on transient errors. Returns (message, None) or (None, exc)."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await send(), None
            except Exception as exc:  # noqa: BLE001 - classified by the caller
                if is_retryable(exc) and attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(min(retry_after(exc), 5) if hasattr(exc, "retry_after") else 0.5 * attempt)
                    continue
                return None, exc
        return None, None

    async def deliver(self, user_id: int, lang: str, file: dict, link: dict) -> str | None:
        """Send the stored file. Returns None on success or a localized-error key."""
        ftype = file.get("type")
        method = SEND_METHOD.get(ftype)
        if method is None:
            await self.repos.stats.inc("deliveries_failed")
            return "err_unsupported"
        protect = await self.effective_protect(file)
        markup = self.share_keyboard(lang, link["token"])
        cap = {"caption": self.i18n.t(lang, "file_ready")} if ftype in CAPTIONABLE else {}
        attempts = []
        store = file.get("store")
        direct = lambda: getattr(self.bot, method)(chat_id=user_id, **{ftype: file["file_id"]},  # noqa: E731
                                                   protect_content=protect, reply_markup=markup, **cap)
        # copy_message does not tell us the media length; when it is unknown send by file_id ONCE (the reply
        # carries the duration), remember it, and use the DB copy from then on.
        learn = ftype in DURATION_TYPES and not file.get("duration")
        if learn:
            attempts.append(direct)
        if store:  # the DB-channel copy is the durable source; file_id is only the fallback
            attempts.append(lambda: self.bot.copy_message(chat_id=user_id, from_chat_id=store["chat_id"],
                                                          message_id=store["message_id"], protect_content=protect,
                                                          reply_markup=markup, **cap))
        if not learn:
            attempts.append(direct)
        msg, exc = None, None
        for send in attempts:
            msg, exc = await self._retrying(send)
            if exc is None:
                break
            if isinstance(exc, TelegramForbiddenError):
                await self.repos.users.mark_blocked(user_id)
                await self.repos.stats.inc("deliveries_failed")
                return "err_delivery"
            log.warning("delivery attempt failed file=%s user=%s: %s", file.get("_id"), user_id, type(exc).__name__)
        if exc is not None:
            await self.repos.stats.inc("deliveries_failed")
            return "err_unavailable" if isinstance(exc, TelegramBadRequest) else "err_delivery"
        await self.repos.stats.inc("deliveries_ok")
        if learn:
            dur = getattr(getattr(msg, ftype, None), "duration", None)
            if isinstance(dur, (int, float)) and dur > 0:
                file["duration"] = int(dur)  # used for THIS delivery's timer too
                await self.repos.files.set_duration(file["_id"], int(dur))
        await self._schedule_cleanup(user_id, lang, getattr(msg, "message_id", None), file)
        return None

    @staticmethod
    def cleanup_delay(setting: int, file: dict) -> int:
        """-1 = auto: media length + 1 minute (2 min video -> 3 min); files without a length use the fallback."""
        if setting == 0:
            return 0
        if setting > 0:
            return setting
        dur = file.get("duration")
        return min((int(dur) + AUTO_MARGIN) if dur else AUTO_FALLBACK, MAX_AUTO)

    async def _schedule_cleanup(self, user_id: int, lang: str, message_id: int | None, file: dict) -> None:
        """Delivered file auto-delete (+ a notice that is removed together with it)."""
        if self.lifecycle is None or message_id is None:
            return
        secs = self.cleanup_delay(int((await self.repos.settings.get()).get("file_autodelete_seconds", -1)), file)
        if secs <= 0:
            return
        await self.lifecycle.schedule(user_id, message_id, secs)
        try:
            notice = await self.bot.send_message(user_id, self.i18n.t(lang, "file_deleting", minutes=max(1, math.ceil(secs / 60))))
            await self.lifecycle.schedule(user_id, notice.message_id, secs)
        except TelegramAPIError as e:
            log.warning("auto-delete notice failed: %s", type(e).__name__)
