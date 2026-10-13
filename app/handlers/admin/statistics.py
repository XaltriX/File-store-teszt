from __future__ import annotations
from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.handlers.admin.common import show
from app.keyboards.admin import BACK_HOME, btn, kb

router = Router(name="admin-stats")


@router.callback_query(F.data == "a:s")
async def stats(cb: CallbackQuery, ctx, t) -> None:
    d = await ctx.stats.detailed()
    days = "\n".join(f"{day}: {n}" for day, n in d["by_day"]) or "-"
    text = t("a_stats", **{k: v for k, v in d.items() if k != "by_day"}, days=days)
    await show(cb, text, kb([btn("🔄 Refresh", "a:s")], [BACK_HOME]))
    await cb.answer()
