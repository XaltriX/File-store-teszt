from __future__ import annotations
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, Message

from app.handlers.admin.channel_management import parse_reference
from app.handlers.admin.common import drop, edit_panel, page_of, show, temp
from app.keyboards.admin import btn, cancel, confirm, files_menu, kb
from app.keyboards.pagination import pager_row
from app.states.broadcast import BroadcastFlow
from app.states.channel_setup import ChannelSetup
from app.states.file_upload import FileUpload
from app.states.welcome_editor import SettingsInput
from app.services.file_service import extract_file_info
from app.utils.fancy import bold
from app.utils.formatting import chunk_text, esc, human_size, share_url

log = logging.getLogger(__name__)
router = Router(name="admin-files")
PAGE = 8


def _panel(cb_or_msg) -> dict:
    m = cb_or_msg.message if isinstance(cb_or_msg, CallbackQuery) else cb_or_msg
    return {"pc": m.chat.id, "pm": m.message_id}


def _fname(f: dict) -> str:
    return f.get("name") or f"{f['type']}-{f['_id'][:6]}"


# ------------------------------------------------------------------ menu
@router.callback_query(F.data == "a:f")
async def menu(cb: CallbackQuery, state: FSMContext, t) -> None:
    await state.clear()
    await show(cb, t("a_files_menu"), files_menu())
    await cb.answer()


# ------------------------------------------------------------------ listing / search
async def _list_screen(cb, ctx, t, page: int, query: str | None, prefix: str, title_key: str, item_prefix: str):
    total = await ctx.repos.files.count(query)
    files = await ctx.repos.files.list(page, PAGE, query)
    rows = [[btn(f"📄 {_fname(f)[:40]}", f"{item_prefix}:{f['_id']}")] for f in files]
    if total > PAGE:
        rows.append(pager_row(prefix, page, total, PAGE))
    rows.append([btn("↩️ Files", "a:f")])
    await show(cb, t(title_key, total=total), kb(*rows))


@router.callback_query(F.data.startswith("a:f:list:"))
async def stored(cb: CallbackQuery, ctx, t) -> None:
    await _list_screen(cb, ctx, t, page_of(cb.data), None, "a:f:list", "a_stored_files", "a:f:view")
    await cb.answer()


@router.callback_query(F.data == "a:f:search")
async def search_start(cb: CallbackQuery, state: FSMContext, t) -> None:
    await state.set_state(FileUpload.search)
    await state.update_data(**_panel(cb))
    await show(cb, t("a_search_prompt"), cancel("a:f"))
    await cb.answer()


@router.message(FileUpload.search, F.text)
async def search_query(m: Message, state: FSMContext, ctx, t) -> None:
    d = await state.get_data()
    q = m.text.strip()[:60]
    await state.set_state(None)
    await state.update_data(q=q)
    await drop(m)
    total = await ctx.repos.files.count(q)
    files = await ctx.repos.files.list(0, PAGE, q)
    rows = [[btn(f"📄 {_fname(f)[:40]}", f"a:f:view:{f['_id']}")] for f in files]
    if total > PAGE:
        rows.append(pager_row("a:f:sl", 0, total, PAGE))
    rows.append([btn("🔍 New search", "a:f:search"), btn("↩️ Files", "a:f")])
    await edit_panel(ctx.bot, d["pc"], d["pm"], t("a_search_results", q=esc(q), total=total), kb(*rows))


@router.callback_query(F.data.startswith("a:f:sl:"))
async def search_page(cb: CallbackQuery, state: FSMContext, ctx, t) -> None:
    q = (await state.get_data()).get("q")
    await _list_screen(cb, ctx, t, page_of(cb.data), q, "a:f:sl", "a_stored_files", "a:f:view")
    await cb.answer()


# ------------------------------------------------------------------ detail
PROT_LABEL = {None: "Default", True: "ON", False: "OFF"}


def fmt_len(sec: int | None) -> str:
    if not sec:
        return "unknown"
    h, rem = divmod(int(sec), 3600)
    m, s_ = divmod(rem, 60)
    return f"{h}:{m:02d}:{s_:02d}" if h else f"{m}:{s_:02d}"


def parse_length(text: str) -> int | None:
    """'150' | '2:30' | '1:02:30' -> seconds (1..86400) or None."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(p.strip().isdigit() for p in parts):
        return None
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return total if 1 <= total <= 86400 else None


async def render_detail(cb, ctx, t, fid: str) -> None:
    f = await ctx.repos.files.get(fid)
    if not f:
        await cb.answer(t("err_unavailable"), show_alert=True)
        return
    links = await ctx.repos.links.for_file(fid)
    cfg = (await ctx.repos.settings.get())["file_autodelete_seconds"]
    delay = ctx.delivery.cleanup_delay(cfg, f)
    autodel = f"{fmt_len(delay)} min" if delay else "off"
    lines = [f"{'🟢' if l['status'] == 'active' else '🚫'} <code>{ctx.links.deep_link(l['token'])}</code>" for l in links]
    text = t("a_file_detail", name=esc(_fname(f)), ftype=f["type"], size=human_size(f.get("size")),
             mime=esc(f.get("mime") or "-"), length=fmt_len(f.get("duration")), autodel=autodel, status=f["status"], prot=PROT_LABEL[f.get("protect")],
             created=f["created_at"].strftime("%Y-%m-%d %H:%M"), links="\n".join(lines) or "-")
    rows = [[btn("🔗 New link", f"a:f:nl:{fid}"), btn(f"🔒 Protect: {PROT_LABEL[f.get('protect')]}", f"a:f:pf:{fid}")],
            [btn("⏱ Set length", f"a:f:dur:{fid}")]]
    active = [l for l in links if l["status"] == "active"]
    if active:
        rows.append([B(text=bold("↗ Share"), url=share_url(ctx.links.deep_link(active[0]["token"])))])
    for i, l in enumerate(active, 1):
        rows.append([btn(f"🚫 Revoke link #{i}", f"a:f:lr:{l['token']}")])
    rows.append([btn("🗄 Archive", f"a:f:arch:{fid}"), btn("🗑 Delete", f"a:f:del:{fid}")])
    rows.append([btn("↩️ Back", "a:f:list:0")])
    await show(cb, text, kb(*rows))


@router.callback_query(F.data.startswith("a:f:view:"))
async def view(cb: CallbackQuery, ctx, t) -> None:
    await render_detail(cb, ctx, t, cb.data.split(":")[-1])
    await cb.answer()


@router.callback_query(F.data.startswith("a:f:nl:"))
async def new_link(cb: CallbackQuery, ctx, t) -> None:
    fid = cb.data.split(":")[-1]
    if await ctx.repos.files.get(fid):
        await ctx.links.create(fid)
    await render_detail(cb, ctx, t, fid)
    await cb.answer("🔗")


@router.callback_query(F.data.startswith("a:f:pf:"))
async def cycle_protect(cb: CallbackQuery, ctx, t) -> None:
    fid = cb.data.split(":")[-1]
    f = await ctx.repos.files.get(fid)
    if f:
        nxt = {None: True, True: False, False: None}[f.get("protect")]
        await ctx.repos.files.set_protect(fid, nxt)
    await render_detail(cb, ctx, t, fid)
    await cb.answer()


@router.callback_query(F.data.startswith("a:f:lr:"))
async def revoke_ask(cb: CallbackQuery, ctx, t) -> None:
    token = cb.data.split(":")[-1]
    link = await ctx.repos.links.get(token)
    back = f"a:f:view:{link['file_id']}" if link else "a:f"
    await show(cb, t("a_revoke_confirm"), confirm(f"a:f:lrc:{token}", back, "🚫 Revoke"))
    await cb.answer()


@router.callback_query(F.data.startswith("a:f:lrc:"))
async def revoke_do(cb: CallbackQuery, ctx, t) -> None:
    token = cb.data.split(":")[-1]
    link = await ctx.repos.links.get(token)
    if link:
        await ctx.repos.links.revoke(token)  # only this token; other links to the file untouched
        await render_detail(cb, ctx, t, link["file_id"])
    await cb.answer("🚫")


@router.callback_query(F.data.startswith("a:f:arch:"))
async def archive(cb: CallbackQuery, ctx, t) -> None:
    fid = cb.data.split(":")[-1]
    await ctx.repos.files.set_status(fid, "archived")
    await show(cb, t("a_archived"), kb([btn("📋 Stored Files", "a:f:list:0")], [btn("↩️ Files", "a:f")]))
    await cb.answer()


@router.callback_query(F.data.startswith("a:f:del:"))
async def delete_ask(cb: CallbackQuery, t) -> None:
    fid = cb.data.split(":")[-1]
    await show(cb, t("a_delete_confirm"), confirm(f"a:f:delc:{fid}", f"a:f:view:{fid}", "🗑 Delete"))
    await cb.answer()


@router.callback_query(F.data.startswith("a:f:delc:"))
async def delete_do(cb: CallbackQuery, ctx, t) -> None:
    fid = cb.data.split(":")[-1]
    await ctx.repos.links.delete_for_file(fid)
    await ctx.repos.files.delete(fid)
    await show(cb, t("a_deleted"), kb([btn("📋 Stored Files", "a:f:list:0")], [btn("↩️ Files", "a:f")]))
    await cb.answer()


# ------------------------------------------------------------------ content protection
@router.callback_query(F.data.in_({"a:f:prot", "a:f:protg"}))
async def protection(cb: CallbackQuery, ctx, t) -> None:
    s = await ctx.repos.settings.get()
    if cb.data == "a:f:protg":
        await ctx.repos.settings.update(default_protect=not s["default_protect"])
        s = await ctx.repos.settings.get()
    state_txt = "ON" if s["default_protect"] else "OFF"
    await show(cb, t("a_protection", state=state_txt),
               kb([btn(f"🔐 Default: {state_txt} (tap to toggle)", "a:f:protg")], [btn("↩️ Files", "a:f")]))
    await cb.answer()


# ------------------------------------------------------------------ direct save: admin just sends a file
# States that legitimately expect a media/forwarded message themselves; in every other state a file the admin
# sends is saved (a leftover menu state must never swallow it silently).
EXPECTING_MEDIA = {BroadcastFlow.message.state, SettingsInput.value.state, ChannelSetup.reference.state,
                   ChannelSetup.invite.state, FileUpload.dbch.state}


async def is_file_to_save(m: Message, state: FSMContext) -> bool:
    return extract_file_info(m) is not None and (await state.get_state()) not in EXPECTING_MEDIA


def db_failure_key(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, TelegramForbiddenError) or any(x in text for x in ("not enough rights", "no rights", "administrator rights")):
        return "a_db_fail_rights"
    if "chat not found" in text or "chat_id is empty" in text:
        return "a_db_fail_chat"
    return "a_db_fail"


@router.message(is_file_to_save)
async def direct_save(m: Message, ctx, t) -> None:
    info = extract_file_info(m)
    if info is None:
        return  # not a file: nothing to do
    store, db_key = None, "a_db_none"
    db = (await ctx.repos.settings.get()).get("db_channel")
    existing = await ctx.repos.files.get_by_unique(info["file_unique_id"])
    if existing and existing.get("store"):
        db_key = "a_db_already"
    elif db:
        try:  # keep a durable copy in the DB channel and deliver from it later
            copied = await ctx.bot.copy_message(db["id"], m.chat.id, m.message_id)
            store = {"chat_id": db["id"], "message_id": copied.message_id}
            db_key = "a_db_ok"
        except TelegramAPIError as e:
            log.warning("copy to DB channel failed: %s", e)
            db_key = db_failure_key(e)
    res = await ctx.files.ingest(m, store=store)
    if res is None:
        return
    file, link, created = res
    url = ctx.links.deep_link(link["token"])
    text = t("a_file_saved" if created else "a_file_duplicate", name=esc(_fname(file)),
             size=human_size(file.get("size")), ftype=file["type"], url=url, db=t(db_key))
    await m.answer(text, reply_markup=kb([B(text=bold("↗ Share"), url=share_url(url))],
                                         [btn("📂 File", f"a:f:view:{file['_id']}"), btn("↩️ Files", "a:f")]))


# ------------------------------------------------------------------ DB channel
async def _db_screen(cb: CallbackQuery, ctx, t) -> None:
    s = await ctx.repos.settings.get()
    db = s.get("db_channel")
    rows = [[btn("✏️ Set / change", "a:f:dbset"),
             btn(f"💬 Reply: {'ON' if s['db_reply'] else 'OFF'}", "a:f:dbr")]]
    if db:
        rows.append([btn("🗑 Remove", "a:f:dbx")])
    rows.append([btn("↩️ Files", "a:f")])
    await show(cb, t("a_dbch_menu", current=esc(db["title"]) if db else "not set",
                     reply="ON" if s["db_reply"] else "OFF"), kb(*rows))


@router.callback_query(F.data == "a:f:db")
async def db_menu(cb: CallbackQuery, state: FSMContext, ctx, t) -> None:
    await state.clear()
    await _db_screen(cb, ctx, t)
    await cb.answer()


@router.callback_query(F.data == "a:f:dbr")
async def db_toggle_reply(cb: CallbackQuery, ctx, t) -> None:
    s = await ctx.repos.settings.get()
    await ctx.repos.settings.update(db_reply=not s["db_reply"])
    await _db_screen(cb, ctx, t)
    await cb.answer()


@router.callback_query(F.data == "a:f:dbx")
async def db_remove(cb: CallbackQuery, ctx, t) -> None:
    await ctx.repos.settings.update(db_channel=None)
    await _db_screen(cb, ctx, t)
    await cb.answer()


@router.callback_query(F.data == "a:f:dbset")
async def db_set_prompt(cb: CallbackQuery, state: FSMContext, t) -> None:
    await state.set_state(FileUpload.dbch)
    await state.update_data(**_panel(cb))
    await show(cb, t("a_dbch_set"), cancel("a:f:db"))
    await cb.answer()


@router.message(FileUpload.dbch)
async def db_set_value(m: Message, state: FSMContext, ctx, t) -> None:
    d = await state.get_data()
    ref = await parse_reference(m)
    await drop(m)
    if ref is None:
        await temp(ctx, m.chat.id, t("a_channel_bad_ref"))
        return
    try:
        chat = await ctx.bot.get_chat(ref)
        me = await ctx.bot.get_chat_member(chat.id, ctx.bot_id)
    except TelegramAPIError:
        await temp(ctx, m.chat.id, t("a_channel_inaccessible"))
        return
    if chat.type != "channel":
        await temp(ctx, m.chat.id, t("a_dbch_not_channel"))
        return
    status = getattr(getattr(me, "status", None), "value", getattr(me, "status", None))
    if status not in ("administrator", "creator"):
        await temp(ctx, m.chat.id, t("a_channel_not_admin"))
        return
    title = chat.title or str(chat.id)
    await ctx.repos.settings.update(db_channel={"id": chat.id, "title": title, "admin": m.from_user.id})
    await state.clear()
    warn = "\n\n" + t("a_dbch_no_post") if getattr(me, "can_post_messages", None) is False else ""
    await edit_panel(ctx.bot, d["pc"], d["pm"], t("a_dbch_set_ok", title=esc(title)) + warn,
                     kb([btn("🗄 DB Channel", "a:f:db")], [btn("↩️ Files", "a:f")]))


# ------------------------------------------------------------------ manual length (documents have none)
@router.callback_query(F.data.startswith("a:f:dur:"))
async def length_prompt(cb: CallbackQuery, state: FSMContext, t) -> None:
    fid = cb.data.split(":")[-1]
    await state.set_state(FileUpload.duration)
    await state.update_data(**_panel(cb), fid=fid)
    await show(cb, t("a_dur_prompt"), cancel(f"a:f:view:{fid}"))
    await cb.answer()


@router.message(FileUpload.duration, F.text)
async def length_value(m: Message, state: FSMContext, ctx, t) -> None:
    d = await state.get_data()
    sec = parse_length(m.text)
    await drop(m)
    if sec is None:
        await temp(ctx, m.chat.id, t("a_dur_bad"))
        return
    await ctx.repos.files.set_duration(d["fid"], sec)
    await state.clear()
    f = await ctx.repos.files.get(d["fid"])
    delay = ctx.delivery.cleanup_delay((await ctx.repos.settings.get())["file_autodelete_seconds"], f or {"duration": sec})
    await edit_panel(ctx.bot, d["pc"], d["pm"], t("a_dur_saved", length=fmt_len(sec), delete=fmt_len(delay) + " min" if delay else "off"),
                     kb([btn("📂 File", f"a:f:view:{d['fid']}")], [btn("↩️ Files", "a:f")]))
