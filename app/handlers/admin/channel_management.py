from __future__ import annotations
import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.handlers.admin.common import drop, edit_panel, page_of, show, temp
from app.keyboards.admin import btn, cancel, channels_menu, confirm, kb
from app.keyboards.pagination import pager_row
from app.states.channel_setup import ChannelSetup
from app.utils.formatting import esc

log = logging.getLogger(__name__)
router = Router(name="admin-channels")
PAGE = 8
INVITE_RE = re.compile(r"^https://t\.me/(\+[\w-]+|joinchat/[\w-]+)$")
USERNAME_RE = re.compile(r"^(?:https?://t\.me/|@)?([A-Za-z][\w]{3,31})$")


def _panel(cb) -> dict:
    return {"pc": cb.message.chat.id, "pm": cb.message.message_id}


async def parse_reference(m: Message):
    fo = getattr(m, "forward_origin", None)
    chat = getattr(fo, "chat", None)
    if chat is not None:
        return chat.id
    txt = (m.text or "").strip()
    if re.fullmatch(r"-100\d{5,}", txt):
        return int(txt)
    mt = USERNAME_RE.match(txt)
    return f"@{mt.group(1)}" if mt else None


async def make_request_link(bot, chat_id: int) -> str:
    link = await bot.create_chat_invite_link(chat_id, name="FileStoreBot", creates_join_request=True)
    return link.invite_link


# ------------------------------------------------------------------ menu
@router.callback_query(F.data == "a:c")
async def menu(cb: CallbackQuery, state: FSMContext, ctx, t) -> None:
    await state.clear()
    await show(cb, t("a_channels_menu", n=await ctx.repos.channels.count()), channels_menu())
    await cb.answer()


# ------------------------------------------------------------------ add
@router.callback_query(F.data == "a:c:add")
async def add_start(cb: CallbackQuery, state: FSMContext, t) -> None:
    await state.set_state(ChannelSetup.reference)
    await state.update_data(**_panel(cb))
    await show(cb, t("a_channel_add"), cancel("a:c"))
    await cb.answer()


@router.message(ChannelSetup.reference)
async def add_reference(m: Message, state: FSMContext, ctx, t) -> None:
    d = await state.get_data()
    ref = await parse_reference(m)
    await drop(m)
    if ref is None:
        await temp(ctx, m.chat.id, t("a_channel_bad_ref"))
        return
    try:
        chat = await ctx.bot.get_chat(ref)
        me = await ctx.bot.get_chat_member(chat.id, ctx.bot_id)
    except TelegramAPIError as e:
        log.info("channel lookup failed: %s", type(e).__name__)
        await temp(ctx, m.chat.id, t("a_channel_inaccessible"))
        return
    if chat.type not in ("channel", "supergroup"):
        await temp(ctx, m.chat.id, t("a_channel_wrong_type"))
        return
    status = getattr(getattr(me, "status", None), "value", getattr(me, "status", None))
    if status not in ("administrator", "creator"):
        await temp(ctx, m.chat.id, t("a_channel_not_admin"))
        return
    title = chat.title or str(chat.id)
    if chat.username:
        row = await ctx.repos.channels.upsert(chat.id, title=title, mode="public", username=chat.username,
                                              url=f"https://t.me/{chat.username}")
        await state.clear()
        await edit_panel(ctx.bot, d["pc"], d["pm"], t("a_channel_added_public", title=esc(title)),
                         kb([btn("📋 Manage", f"a:c:view:{row['_id']}")], [btn("↩️ Channels", "a:c")]))
        return
    try:
        url = await make_request_link(ctx.bot, chat.id)
    except TelegramAPIError:
        await state.set_state(ChannelSetup.invite)
        await state.update_data(cid=chat.id, title=title)
        await edit_panel(ctx.bot, d["pc"], d["pm"], t("a_channel_need_invite", title=esc(title)), cancel("a:c"))
        return
    await ctx.repos.channels.upsert(chat.id, title=title, mode="request", username=None, url=url)
    await state.clear()
    await edit_panel(ctx.bot, d["pc"], d["pm"], t("a_channel_added_private", title=esc(title)),
                     kb([btn("📋 Manage", f"a:c:view:{chat.id}")], [btn("↩️ Channels", "a:c")]))


@router.message(ChannelSetup.invite, F.text)
async def add_invite(m: Message, state: FSMContext, ctx, t) -> None:
    d = await state.get_data()
    url = m.text.strip()
    await drop(m)
    if not INVITE_RE.match(url):
        await temp(ctx, m.chat.id, t("a_channel_bad_invite"))
        return
    cid = d["cid"]
    existing = await ctx.repos.channels.get(cid)
    if existing:
        await ctx.repos.channels.set(cid, url=url, mode="request", active=True, removed=False)
    else:
        await ctx.repos.channels.upsert(cid, title=d["title"], mode="request", username=None, url=url)
    await state.clear()
    await edit_panel(ctx.bot, d["pc"], d["pm"], t("a_channel_added_private", title=esc(d["title"])),
                     kb([btn("📋 Manage", f"a:c:view:{cid}")], [btn("↩️ Channels", "a:c")]))


# ------------------------------------------------------------------ manage
@router.callback_query(F.data.startswith("a:c:list:"))
async def manage(cb: CallbackQuery, ctx, t) -> None:
    page = page_of(cb.data)
    total = await ctx.repos.channels.count()
    chans = await ctx.repos.channels.list(page=page, size=PAGE)
    rows = [[btn(f"{'🟢' if c['active'] else '⚪'} {c['title'][:34]}", f"a:c:view:{c['_id']}")] for c in chans]
    if total > PAGE:
        rows.append(pager_row("a:c:list", page, total, PAGE))
    rows.append([btn("↩️ Channels", "a:c")])
    await show(cb, t("a_channel_list", total=total), kb(*rows))
    await cb.answer()


def channel_stats_text(t, c: dict) -> str:
    last = c.get("last_activity")
    return t("a_channel_stats_row", title=esc(c["title"]), mode=c["mode"], active="yes" if c["active"] else "no",
             presented=c.get("presented", 0), joins=c.get("joins_confirmed", 0), requests=c.get("requests_received", 0), repeat=c.get("requests_repeat", 0), other=c.get("requests_other", 0),
             failed=c.get("failed_checks", 0), errors=c.get("errors", 0),
             last=last.strftime("%Y-%m-%d %H:%M") if last else "-")


async def render_channel(cb, ctx, t, cid: int) -> None:
    c = await ctx.repos.channels.get(cid)
    if not c or c.get("removed"):
        await cb.answer(t("a_channel_gone"), show_alert=True)
        return
    text = channel_stats_text(t, c) + f"\n\n🔗 <code>{esc(c['url'])}</code>"
    rows = [[btn("⏸ Deactivate" if c["active"] else "▶️ Activate", f"a:c:tg:{cid}"),
             btn("🔁 Mode: " + c["mode"], f"a:c:mode:{cid}")]]
    if c["mode"] == "request":
        rows.append([btn("♻️ New invite link", f"a:c:inv:{cid}"), btn("✏️ Set invite link", f"a:c:invset:{cid}")])
    rows.append([btn("🗑 Remove from pool", f"a:c:rm:{cid}")])
    rows.append([btn("↩️ Back", "a:c:list:0")])
    await show(cb, text, kb(*rows))


@router.callback_query(F.data.startswith("a:c:view:"))
async def view(cb: CallbackQuery, ctx, t) -> None:
    await render_channel(cb, ctx, t, int(cb.data.split(":")[-1]))
    await cb.answer()


@router.callback_query(F.data.startswith("a:c:tg:"))
async def toggle(cb: CallbackQuery, ctx, t) -> None:
    cid = int(cb.data.split(":")[-1])
    c = await ctx.repos.channels.get(cid)
    if c:
        await ctx.repos.channels.set(cid, active=not c["active"])
    await render_channel(cb, ctx, t, cid)
    await cb.answer()


@router.callback_query(F.data.startswith("a:c:mode:"))
async def switch_mode(cb: CallbackQuery, ctx, t) -> None:
    cid = int(cb.data.split(":")[-1])
    c = await ctx.repos.channels.get(cid)
    if not c:
        await cb.answer()
        return
    if c["mode"] == "public":
        try:
            url = await make_request_link(ctx.bot, cid)
        except TelegramAPIError:
            await cb.answer(t("a_channel_need_invite_short"), show_alert=True)
            return
        await ctx.repos.channels.set(cid, mode="request", url=url)
    else:
        if not c.get("username"):
            await cb.answer(t("a_channel_no_username"), show_alert=True)
            return
        await ctx.repos.channels.set(cid, mode="public", url=f"https://t.me/{c['username']}")
    await render_channel(cb, ctx, t, cid)
    await cb.answer()


@router.callback_query(F.data.startswith("a:c:inv:"))
async def regen_invite(cb: CallbackQuery, ctx, t) -> None:
    cid = int(cb.data.split(":")[-1])
    try:
        url = await make_request_link(ctx.bot, cid)
    except TelegramAPIError:
        await cb.answer(t("a_channel_need_invite_short"), show_alert=True)
        return
    await ctx.repos.channels.set(cid, url=url)
    await render_channel(cb, ctx, t, cid)
    await cb.answer("♻️")


@router.callback_query(F.data.startswith("a:c:invset:"))
async def set_invite(cb: CallbackQuery, state: FSMContext, ctx, t) -> None:
    cid = int(cb.data.split(":")[-1])
    c = await ctx.repos.channels.get(cid)
    if not c:
        await cb.answer()
        return
    await state.set_state(ChannelSetup.invite)
    await state.update_data(**_panel(cb), cid=cid, title=c["title"])
    await show(cb, t("a_channel_need_invite", title=esc(c["title"])), cancel(f"a:c:view:{cid}"))
    await cb.answer()


@router.callback_query(F.data.startswith("a:c:rm:"))
async def remove_ask(cb: CallbackQuery, t) -> None:
    cid = cb.data.split(":")[-1]
    await show(cb, t("a_channel_remove_confirm"), confirm(f"a:c:rmc:{cid}", f"a:c:view:{cid}", "🗑 Remove"))
    await cb.answer()


@router.callback_query(F.data.startswith("a:c:rmc:"))
async def remove_do(cb: CallbackQuery, ctx, t) -> None:
    await ctx.repos.channels.soft_remove(int(cb.data.split(":")[-1]))  # stats kept; flows skip it
    await show(cb, t("a_channel_removed"), kb([btn("📋 Manage", "a:c:list:0")], [btn("↩️ Channels", "a:c")]))
    await cb.answer()


# ------------------------------------------------------------------ statistics / steps / balancing
@router.callback_query(F.data.startswith("a:c:stats"))
async def stats(cb: CallbackQuery, ctx, t) -> None:
    page = page_of(cb.data) if cb.data.count(":") >= 3 else 0
    total = await ctx.repos.channels.count()
    chans = await ctx.repos.channels.list(page=page, size=5)
    body = "\n\n".join(channel_stats_text(t, c) for c in chans) or t("a_channel_none")
    rows = [pager_row("a:c:stats", page, total, 5)] if total > 5 else []
    rows.append([btn("↩️ Channels", "a:c")])
    await show(cb, t("a_channel_stats_title") + "\n\n" + body, kb(*rows))
    await cb.answer()


@router.callback_query(F.data == "a:c:steps")
async def steps(cb: CallbackQuery, ctx, t, settings: dict) -> None:
    cur = settings["required_steps"]
    row = [btn(("● " if n == cur else "") + str(n), f"a:c:st:{n}") for n in (1, 2, 3)]
    await show(cb, t("a_steps", n=cur), kb(row, [btn("↩️ Channels", "a:c")]))
    await cb.answer()


@router.callback_query(F.data.startswith("a:c:st:"))
async def set_steps(cb: CallbackQuery, ctx, t) -> None:
    n = page_of(cb.data)
    if n in (1, 2, 3):
        await ctx.repos.settings.update(required_steps=n)
    await steps(cb, ctx, t, await ctx.repos.settings.get())


@router.callback_query(F.data == "a:c:bal")
async def balancing(cb: CallbackQuery, ctx, t, settings: dict) -> None:
    cur = settings["balancing"]
    await show(cb, t("a_balancing", mode=cur),
               kb([btn(("● " if cur == "least" else "") + "Least presented", "a:c:bs:least"),
                   btn(("● " if cur == "random" else "") + "Random", "a:c:bs:random")],
                  [btn("♻️ Reset presentation counters", "a:c:reset")], [btn("↩️ Channels", "a:c")]))
    await cb.answer()


@router.callback_query(F.data.startswith("a:c:bs:"))
async def set_balancing(cb: CallbackQuery, ctx, t) -> None:
    mode = cb.data.split(":")[-1]
    if mode in ("least", "random"):
        await ctx.repos.settings.update(balancing=mode)
    await balancing(cb, ctx, t, await ctx.repos.settings.get())


@router.callback_query(F.data == "a:c:reset")
async def reset_ask(cb: CallbackQuery, t) -> None:
    await show(cb, t("a_reset_confirm"), confirm("a:c:resetc", "a:c:bal", "♻️ Reset"))
    await cb.answer()


@router.callback_query(F.data == "a:c:resetc")
async def reset_do(cb: CallbackQuery, ctx, t) -> None:
    await ctx.repos.channels.reset_presented()
    await balancing(cb, ctx, t, await ctx.repos.settings.get())
