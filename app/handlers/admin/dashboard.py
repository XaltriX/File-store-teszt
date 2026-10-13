from __future__ import annotations
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.handlers.admin.common import drop, show
from app.keyboards.admin import dashboard as dash_kb

router = Router(name="admin-dashboard")
_last_dash: dict[int, tuple[int, int]] = {}


async def dashboard_text(ctx, t) -> str:
    d = await ctx.stats.dashboard()
    return t("a_dashboard", users=d["users"], files=d["files"], channels=d["channels"], requests=d["requests"])


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext, ctx, t) -> None:
    await state.clear()
    old = _last_dash.pop(message.from_user.id, None)
    if old:
        try:
            await ctx.bot.delete_message(*old)
        except Exception:  # noqa: BLE001 - old dashboard may be gone
            pass
    sent = await message.answer(await dashboard_text(ctx, t), reply_markup=dash_kb())
    _last_dash[message.from_user.id] = (sent.chat.id, sent.message_id)
    await drop(message)


@router.callback_query(F.data == "a:home")
async def home(cb: CallbackQuery, state: FSMContext, ctx, t) -> None:
    await state.clear()
    await show(cb, await dashboard_text(ctx, t), dash_kb())
    await cb.answer()


@router.callback_query(F.data == "a:close")
async def close(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await drop(cb.message)
    await cb.answer()


@router.callback_query(F.data == "a:noop")
async def noop(cb: CallbackQuery) -> None:
    await cb.answer()
