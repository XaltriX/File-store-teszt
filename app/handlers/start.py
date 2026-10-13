from __future__ import annotations
from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.handlers.verification import present_step
from app.keyboards.user import language_keyboard

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, ctx, user: dict, lang: str,
                    t, settings: dict) -> None:
    await state.clear()
    payload = (command.args or "").strip()
    if payload:
        if user.get("language") is None:  # deep-link users are not blocked behind a language prompt
            lang = ctx.i18n.normalize(message.from_user.language_code, settings["enabled_languages"])
            await ctx.repos.users.set_language(message.from_user.id, lang)
        step = await ctx.verification.start(message.from_user.id, payload)
        await present_step(ctx, message.from_user.id, lang, step)
        return
    if user.get("language") is None:
        await message.answer(t("lang_prompt"), reply_markup=language_keyboard(settings["enabled_languages"]))
        return
    await ctx.welcome.send(message.chat.id, lang, message.from_user.first_name, settings)
