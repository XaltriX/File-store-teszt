from __future__ import annotations
from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.handlers.verification import present_step
from app.keyboards.user import language_keyboard
from app.utils.tokens import is_valid_token

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, ctx, user: dict, lang: str,
                    t, settings: dict) -> None:
    await state.clear()
    payload = (command.args or "").strip()
    if not user.get("lang_chosen"):  # nobody has picked a language yet (or it was only auto-detected): ask first,
        # even when arriving through a file link; the link rides along in the buttons
        token = payload if is_valid_token(payload) else None
        await message.answer(t("lang_prompt"), reply_markup=language_keyboard(settings["enabled_languages"], token))
        return
    if payload:
        step = await ctx.verification.start(message.from_user.id, payload)
        await present_step(ctx, message.from_user.id, lang, step)
        return
    await ctx.welcome.send(message.chat.id, lang, message.from_user.first_name, settings,
                           is_admin=message.from_user.id in ctx.cfg.admin_ids)


@router.message(Command("id"))
async def cmd_id(message: Message, ctx) -> None:
    """Handy when the admin panel does not appear: shows the numeric ID that must be listed in ADMIN_IDS."""
    uid = message.from_user.id
    await message.answer(f"🆔 <code>{uid}</code> {'✅ admin' if uid in ctx.cfg.admin_ids else '— not in ADMIN_IDS'}")


@router.message(Command("language"))
async def cmd_language(message: Message, settings: dict, t) -> None:
    await message.answer(t("lang_prompt"), reply_markup=language_keyboard(settings["enabled_languages"]))
