from __future__ import annotations
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, ChatJoinRequest, ChatMemberUpdated

from app.keyboards.user import retry_keyboard, verify_keyboard
from app.services.verification_service import Step, is_member
from app.utils.messaging import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="verification")


async def _delete_quiet(bot, chat_id, message_id) -> None:
    if chat_id and message_id:
        try:
            await bot.delete_message(chat_id, message_id)
        except TelegramAPIError:
            pass


async def send_temp(ctx, chat_id: int, text: str, kb=None) -> None:
    try:
        m = await ctx.bot.send_message(chat_id, text, reply_markup=kb)
        await ctx.lifecycle.schedule(chat_id, m.message_id)
    except TelegramAPIError as e:
        log.warning("temp message failed: %s", type(e).__name__)


async def present_step(ctx, user_id: int, lang: str, step: Step, edit_message=None) -> None:
    t = lambda k, **kw: ctx.i18n.t(lang, k, **kw)  # noqa: E731
    if step.kind == "prompt":
        flow, ch = step.flow, step.channel
        text = t("locked_request" if ch["mode"] == "request" else "locked")
        kb = verify_keyboard(t, ch, flow["_id"])
        if edit_message is not None:
            await edit_or_send(edit_message, text, kb)
            await ctx.repos.requests.set_message(flow["_id"], edit_message.chat.id, edit_message.message_id)
            return
        await _delete_quiet(ctx.bot, flow.get("chat_id"), flow.get("message_id"))  # one card at a time
        try:
            m = await ctx.bot.send_message(user_id, text, reply_markup=kb)
        except TelegramAPIError as e:
            log.warning("prompt send failed: %s", type(e).__name__)
            return
        await ctx.repos.requests.set_message(flow["_id"], user_id, m.message_id)
        # the card lives as long as the flow (not the short auto-delete timer): it holds the Verify button
        await ctx.lifecycle.schedule(user_id, m.message_id, ctx.cfg.flow_ttl_seconds)
    elif step.kind == "deliver":
        if edit_message is not None:
            await _delete_quiet(ctx.bot, edit_message.chat.id, edit_message.message_id)
        elif step.flow:
            await _delete_quiet(ctx.bot, step.flow.get("chat_id"), step.flow.get("message_id"))
        err = await ctx.delivery.deliver(user_id, lang, step.file, step.link)
        if err:
            kb = None
            if step.flow:
                await ctx.verification.reopen(step.flow["_id"])  # allow retry via Verify
                kb = retry_keyboard(t, step.flow["_id"])
            await send_temp(ctx, user_id, t(err), kb)
    elif step.kind == "error":
        await send_temp(ctx, user_id, t(step.key))


@router.callback_query(F.data.startswith("v:"))
async def on_verify(cb: CallbackQuery, ctx, lang: str) -> None:
    flow_id = cb.data[2:]
    step = await ctx.verification.verify(flow_id, cb.from_user.id)
    t = lambda k, **kw: ctx.i18n.t(lang, k, **kw)  # noqa: E731
    if step.kind == "retry":
        await cb.answer(t(step.key), show_alert=True)
        return
    if step.kind == "noop":
        await cb.answer()
        return
    if step.kind == "error":
        await cb.answer(t(step.key), show_alert=True)
        return
    await cb.answer(t("verified") if step.kind == "prompt" else None)
    await present_step(ctx, cb.from_user.id, lang, step, edit_message=cb.message)


async def _present_all(ctx, user_id: int, steps: list[Step]) -> None:
    user = await ctx.repos.users.get(user_id)
    s = await ctx.repos.settings.get()
    lang = ctx.i18n.normalize((user or {}).get("language"), s["enabled_languages"])
    for step in steps:
        await present_step(ctx, user_id, lang, step)


@router.chat_join_request()
async def on_join_request(req: ChatJoinRequest, ctx) -> None:
    """Records the request only. NEVER approves or declines it. Auto-advances the user's flow."""
    ts = int(req.date.timestamp()) if req.date else 0
    link = req.invite_link.invite_link if getattr(req, "invite_link", None) else None
    steps = await ctx.verification.on_join_request(req.chat.id, req.from_user.id, ts, link)
    await _present_all(ctx, req.from_user.id, steps)


@router.chat_member()
async def on_chat_member(upd: ChatMemberUpdated, ctx) -> None:
    """User just became a member of a pool channel (needs the bot to be admin there)."""
    if not is_member(upd.new_chat_member) or is_member(upd.old_chat_member):
        return
    steps = await ctx.verification.on_member_joined(upd.chat.id, upd.new_chat_member.user.id)
    await _present_all(ctx, upd.new_chat_member.user.id, steps)
