from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup

from app.keyboards.user import welcome_keyboard
from app.utils.formatting import esc

SEND = {"photo": ("send_photo", "photo"), "video": ("send_video", "video"), "animation": ("send_animation", "animation")}


def welcome_media(s: dict) -> dict | None:
    m = s.get("welcome_media")
    if m and m.get("type") in SEND:
        return m
    if s.get("welcome_image"):  # legacy setting
        return {"type": "photo", "file_id": s["welcome_image"]}
    return None


class WelcomeService:
    def __init__(self, bot, repos, i18n):
        self.bot, self.repos, self.i18n = bot, repos, i18n

    def render_text(self, lang: str, settings: dict, first_name: str | None) -> str:
        custom = settings.get("welcome_text")
        name = esc(first_name or "")
        if custom:
            lines = esc(custom).replace("{first_name}", name).split("\n")
            lines[0] = f"<b>{lines[0]}</b>" if lines[0] else lines[0]
            return "\n".join(lines)
        return self.i18n.t(lang, "welcome", name=name)

    def keyboard(self, lang: str, settings: dict) -> InlineKeyboardMarkup:
        return welcome_keyboard(lambda k: self.i18n.t(lang, k), settings)

    async def send(self, chat_id: int, lang: str, first_name: str | None, settings: dict | None = None):
        s = settings or await self.repos.settings.get()
        text, kb, media = self.render_text(lang, s, first_name), self.keyboard(lang, s), welcome_media(s)
        if media:
            method, key = SEND[media["type"]]
            return await getattr(self.bot, method)(chat_id, **{key: media["file_id"]}, caption=text, reply_markup=kb)
        return await self.bot.send_message(chat_id, text, reply_markup=kb)
