from __future__ import annotations
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery

from app.handlers.verification import present_step
from app.localization import LANGUAGES
from app.utils.tokens import is_valid_token

router = Router(name="language")


@router.callback_query(F.data.startswith("l:"))
async def choose_language(cb: CallbackQuery, ctx, settings: dict) -> None:
    parts = cb.data.split(":")  # l:<code>[:<pending file token>]
    code = parts[1] if len(parts) > 1 else ""
    token = parts[2] if len(parts) > 2 and is_valid_token(parts[2]) else None
    if code not in LANGUAGES or code not in settings["enabled_languages"]:
        await cb.answer()
        return
    await ctx.repos.users.set_language(cb.from_user.id, code)
    await cb.answer(ctx.i18n.t(code, "lang_saved"))
    try:
        await cb.message.delete()
    except TelegramAPIError:
        pass
    if token:  # they came for a file: continue straight into it
        step = await ctx.verification.start(cb.from_user.id, token)
        await present_step(ctx, cb.from_user.id, code, step)
        return
    await ctx.welcome.send(cb.from_user.id, code, cb.from_user.first_name, settings,
                           is_admin=cb.from_user.id in ctx.cfg.admin_ids)
