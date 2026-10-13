from __future__ import annotations
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery

from app.localization import LANGUAGES

router = Router(name="language")


@router.callback_query(F.data.startswith("l:"))
async def choose_language(cb: CallbackQuery, ctx, settings: dict) -> None:
    code = cb.data[2:]
    if code not in LANGUAGES or code not in settings["enabled_languages"]:
        await cb.answer()
        return
    await ctx.repos.users.set_language(cb.from_user.id, code)
    await cb.answer(ctx.i18n.t(code, "lang_saved"))
    try:
        await cb.message.delete()
    except TelegramAPIError:
        pass
    await ctx.welcome.send(cb.from_user.id, code, cb.from_user.first_name, settings,
                           is_admin=cb.from_user.id in ctx.cfg.admin_ids)
