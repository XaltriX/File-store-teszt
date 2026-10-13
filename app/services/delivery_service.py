from __future__ import annotations
import asyncio
import logging

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.utils.telegram_errors import is_blocked, is_retryable, retry_after
from app.utils.formatting import share_url

log = logging.getLogger(__name__)

SEND_METHOD = {"document": "send_document", "video": "send_video", "audio": "send_audio",
               "voice": "send_voice", "animation": "send_animation", "video_note": "send_video_note",
               "sticker": "send_sticker", "photo": "send_photo"}
CAPTIONABLE = {"document", "video", "audio", "voice", "animation", "photo"}
MAX_ATTEMPTS = 3


class DeliveryService:
    def __init__(self, bot, repos, i18n, links):
        self.bot, self.repos, self.i18n, self.links = bot, repos, i18n, links

    async def effective_protect(self, file: dict) -> bool:
        if file.get("protect") is not None:
            return bool(file["protect"])
        return bool((await self.repos.settings.get()).get("default_protect"))

    def share_keyboard(self, lang: str, token: str) -> InlineKeyboardMarkup:
        btn = InlineKeyboardButton(text=self.i18n.t(lang, "btn_share"), url=share_url(self.links.deep_link(token)))
        return InlineKeyboardMarkup(inline_keyboard=[[btn]])

    async def deliver(self, user_id: int, lang: str, file: dict, link: dict) -> str | None:
        """Send the stored file. Returns None on success or a localized-error key."""
        method = SEND_METHOD.get(file.get("type"))
        if method is None:
            await self.repos.stats.inc("deliveries_failed")
            return "err_unsupported"
        kwargs = {"chat_id": user_id, file["type"]: file["file_id"],
                  "protect_content": await self.effective_protect(file),
                  "reply_markup": self.share_keyboard(lang, link["token"])}
        if file["type"] in CAPTIONABLE:
            kwargs["caption"] = self.i18n.t(lang, "file_ready")
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                await getattr(self.bot, method)(**kwargs)
                await self.repos.stats.inc("deliveries_ok")
                return None
            except Exception as exc:  # noqa: BLE001 - classified below
                if is_blocked(exc) and isinstance(exc, TelegramForbiddenError):
                    await self.repos.users.mark_blocked(user_id)
                    await self.repos.stats.inc("deliveries_failed")
                    return "err_delivery"
                if is_retryable(exc) and attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(min(retry_after(exc), 5) if hasattr(exc, "retry_after") else 0.5 * attempt)
                    continue
                key = "err_unavailable" if isinstance(exc, TelegramBadRequest) else "err_delivery"
                log.warning("delivery failed file=%s user=%s: %s", file.get("_id"), user_id, type(exc).__name__)
                await self.repos.stats.inc("deliveries_failed")
                return key
        return "err_delivery"
