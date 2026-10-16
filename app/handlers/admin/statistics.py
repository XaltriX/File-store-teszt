from __future__ import annotations
from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.handlers.admin.common import show
from app.keyboards.admin import BACK_HOME, btn, kb

router = Router(name="admin-stats")


@router.callback_query(F.data == "a:s")
async def stats(cb: CallbackQuery, ctx, t) -> None:
    d = await ctx.stats.detailed()
    text = t("a_stats", **d)
    await show(cb, text, kb([btn("🔄 Refresh", "a:s")], [BACK_HOME]))
    await cb.answer()
