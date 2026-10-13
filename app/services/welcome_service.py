from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup

from app.keyboards.user import welcome_keyboard
from app.utils.formatting import esc


class WelcomeService:
    def __init__(self, bot, repos, i18n):
        self.bot, self.repos, self.i18n = bot, repos, i18n

    def render_text(self, lang: str, settings: dict, first_name: str | None, override: str | None = None) -> str:
        custom = override if override is not None else settings.get("welcome_text")
        name = esc(first_name or "")
        if custom:
            lines = esc(custom).replace("{first_name}", name).split("\n")
            lines[0] = f"<b>{lines[0]}</b>" if lines[0] else lines[0]
            return "\n".join(lines)
        return self.i18n.t(lang, "welcome", name=name)

    def keyboard(self, lang: str, settings: dict, buttons=None) -> InlineKeyboardMarkup:
        s = dict(settings)
        if buttons is not None:
            s["welcome_buttons"] = buttons
        return welcome_keyboard(lambda k: self.i18n.t(lang, k), s)

    async def send(self, chat_id: int, lang: str, first_name: str | None, settings: dict | None = None,
                   override_text=None, override_image="__keep__", override_buttons=None):
        s = settings or await self.repos.settings.get()
        text = self.render_text(lang, s, first_name, override_text)
        kb = self.keyboard(lang, s, override_buttons)
        image = s.get("welcome_image") if override_image == "__keep__" else override_image
        if image:
            return await self.bot.send_photo(chat_id, image, caption=text, reply_markup=kb)
        return await self.bot.send_message(chat_id, text, reply_markup=kb)
