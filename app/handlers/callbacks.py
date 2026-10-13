from __future__ import annotations
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery

from app.keyboards.user import back_keyboard, language_keyboard
from app.utils.formatting import esc
from app.utils.messaging import edit_or_send

router = Router(name="user-callbacks")


@router.callback_query(F.data == "u:nc")
async def not_configured(cb: CallbackQuery, t) -> None:
    await cb.answer(t("not_configured"), show_alert=True)


@router.callback_query(F.data == "u:ch")
async def channels(cb: CallbackQuery, t, settings: dict) -> None:
    items = settings["official_channels"]
    if items:
        body = "\n".join(f'• <a href="{escape(c["url"], quote=True)}">{esc(c["title"])}</a>' for c in items)
    else:
        body = t("channels_empty")
    await edit_or_send(cb.message, f'{t("channels_title")}\n\n{body}', back_keyboard(t))
    await cb.answer()


@router.callback_query(F.data == "u:about")
async def about(cb: CallbackQuery, t, settings: dict) -> None:
    text = esc(settings["about_text"]) if settings.get("about_text") else t("about")
    await edit_or_send(cb.message, text, back_keyboard(t))
    await cb.answer()


@router.callback_query(F.data == "u:lang")
async def language(cb: CallbackQuery, t, settings: dict) -> None:
    await edit_or_send(cb.message, t("lang_prompt"), language_keyboard(settings["enabled_languages"]))
    await cb.answer()


@router.callback_query(F.data == "u:home")
async def home(cb: CallbackQuery, ctx, lang: str, settings: dict) -> None:
    text = ctx.welcome.render_text(lang, settings, cb.from_user.first_name)
    await edit_or_send(cb.message, text, ctx.welcome.keyboard(lang, settings, cb.from_user.id in ctx.cfg.admin_ids))
    await cb.answer()
