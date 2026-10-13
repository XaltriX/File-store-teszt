from __future__ import annotations
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.handlers.admin.common import edit_panel, show, temp
from app.keyboards.admin import BACK_HOME, btn, cancel, confirm, kb
from app.states.broadcast import BroadcastFlow

log = logging.getLogger(__name__)
router = Router(name="admin-broadcast")


def progress_text(t, job: dict) -> str:
    done = job["success"] + job["blocked"] + job["failed"]
    return t("a_bc_status", status=job["status"], target=job["target"], success=job["success"],
             blocked=job["blocked"], failed=job["failed"], done=done)


def make_notifier(ctx, t_for):
    async def notify(job: dict | None, final: bool) -> None:
        if not job or not job.get("status_chat"):
            return
        t = t_for(job["admin_id"])
        markup = kb([BACK_HOME]) if final else kb([btn("🛑 Stop", f"a:b:stop:{job['_id']}")])
        await edit_panel(ctx.bot, job["status_chat"], job["status_msg"], progress_text(t, job), markup)
    return notify


@router.callback_query(F.data == "a:b")
async def menu(cb: CallbackQuery, state: FSMContext, ctx, t) -> None:
    await state.clear()
    if ctx.broadcast.running:
        unfinished = await ctx.repos.broadcasts.unfinished()
        if unfinished:
            j = unfinished[0]
            await show(cb, progress_text(t, j), kb([btn("🛑 Stop", f"a:b:stop:{j['_id']}")], [BACK_HOME]))
            await cb.answer()
            return
    await state.set_state(BroadcastFlow.message)
    await state.update_data(pc=cb.message.chat.id, pm=cb.message.message_id)
    await show(cb, t("a_bc_prompt"), cancel())
    await cb.answer()


@router.message(BroadcastFlow.message)
async def got_message(m: Message, state: FSMContext, ctx, t) -> None:
    d = await state.get_data()
    if m.text is None and not any((m.photo, m.video, m.document, m.animation, m.audio, m.voice, m.sticker)):
        await temp(ctx, m.chat.id, t("a_bc_unsupported"))
        return
    await state.update_data(src_chat=m.chat.id, src_msg=m.message_id)
    audience = await ctx.repos.users.count_audience()
    try:
        pv = await ctx.bot.copy_message(m.chat.id, m.chat.id, m.message_id)
        await ctx.lifecycle.schedule(m.chat.id, pv.message_id, 300)
    except TelegramAPIError:
        await temp(ctx, m.chat.id, t("a_bc_preview_failed"))
    await edit_panel(ctx.bot, d["pc"], d["pm"], t("a_bc_confirm", audience=audience),
                     confirm("a:b:go", "a:home", "🚀 Send"))


@router.callback_query(F.data == "a:b:go")
async def go(cb: CallbackQuery, state: FSMContext, ctx, t) -> None:
    d = await state.get_data()
    if "src_msg" not in d:
        await cb.answer(t("err_flow_expired"), show_alert=True)
        return
    if ctx.broadcast.running:
        await cb.answer(t("a_bc_busy"), show_alert=True)
        return
    await state.clear()
    job = await ctx.broadcast.create(cb.from_user.id, d["src_chat"], d["src_msg"], "")
    await ctx.repos.broadcasts.set_status_message(job["_id"], cb.message.chat.id, cb.message.message_id)
    ctx.broadcast.launch(job["_id"])
    await show(cb, progress_text(t, {**job, "status": "running"}), kb([btn("🛑 Stop", f"a:b:stop:{job['_id']}")]))
    await cb.answer()


@router.callback_query(F.data.startswith("a:b:stop:"))
async def stop(cb: CallbackQuery, ctx, t) -> None:
    await ctx.broadcast.cancel(cb.data.split(":")[-1])
    await cb.answer(t("a_bc_stopping"))
