from __future__ import annotations
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, Message

from app.handlers.admin.channel_management import parse_reference
from app.handlers.admin.common import drop, edit_panel, page_of, show, temp
from app.keyboards.admin import btn, cancel, confirm, files_menu, kb
from app.keyboards.pagination import pager_row
from app.services.file_service import extract_file_info
from app.states.file_upload import FileUpload
from app.utils.fancy import bold
from app.utils.formatting import chunk_text, esc, human_size, share_url

log = logging.getLogger(__name__)
router = Router(name="admin-files")
PAGE = 8
MAX_LINKS_LISTED = 200


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


# ------------------------------------------------------------------ single add
@router.callback_query(F.data == "a:f:add")
async def add_file(cb: CallbackQuery, state: FSMContext, t) -> None:
    await state.set_state(FileUpload.single)
    await state.update_data(**_panel(cb))
    await show(cb, t("a_send_file"), cancel("a:f"))
    await cb.answer()


@router.message(FileUpload.single)
async def got_single(m: Message, state: FSMContext, ctx, t) -> None:
    data = await state.get_data()
    res = await ctx.files.ingest(m)
    if res is None:
        await temp(ctx, m.chat.id, t("a_not_a_file"))
        return
    file, link, created = res
    url = ctx.links.deep_link(link["token"])
    text = t("a_file_saved" if created else "a_file_duplicate", name=esc(_fname(file)), size=human_size(file.get("size")),
             ftype=file["type"], url=url)
    markup = kb([B(text=bold("↗ Share"), url=share_url(url))],
                [btn("➕ Add another", "a:f:add"), btn("📂 File", f"a:f:view:{file['_id']}")],
                [btn("↩️ Files", "a:f")])
    await state.clear()
    await edit_panel(ctx.bot, data["pc"], data["pm"], text, markup)
    await drop(m)


# ------------------------------------------------------------------ batch
@router.callback_query(F.data == "a:f:batch")
async def batch_start(cb: CallbackQuery, state: FSMContext, t) -> None:
    await state.set_state(FileUpload.batch)
    await state.update_data(**_panel(cb), processed=0, new=0, dup=0, skipped=0, failed=0, items=[])
    await show(cb, t("a_batch_prompt"), kb([btn("✅ Done", "a:f:batchdone")], [btn("✖️ Cancel", "a:f")]))
    await cb.answer()


@router.message(FileUpload.batch)
async def batch_item(m: Message, state: FSMContext, ctx, t) -> None:
    d = await state.get_data()
    d["processed"] += 1
    if extract_file_info(m) is None:
        d["skipped"] += 1
    else:
        try:
            file, link, created = await ctx.files.ingest(m)
            d["new" if created else "dup"] += 1
            if len(d["items"]) < MAX_LINKS_LISTED:
                d["items"].append([_fname(file), link["token"], created])
        except Exception:  # noqa: BLE001
            log.exception("batch ingest failed")
            d["failed"] += 1
    await state.update_data(**d)
    await drop(m)
    if d["processed"] % 5 == 0:
        await edit_panel(ctx.bot, d["pc"], d["pm"], t("a_batch_progress", **d),
                         kb([btn("✅ Done", "a:f:batchdone")], [btn("✖️ Cancel", "a:f")]))


@router.callback_query(F.data == "a:f:batchdone")
async def batch_done(cb: CallbackQuery, state: FSMContext, ctx, t) -> None:
    d = await state.get_data()
    await state.clear()
    head = t("a_batch_done", processed=d.get("processed", 0), new=d.get("new", 0), dup=d.get("dup", 0),
             skipped=d.get("skipped", 0), failed=d.get("failed", 0))
    lines = [f"{i}. {esc(n)}{'' if c else ' (existing)'}\n<code>{ctx.links.deep_link(tok)}</code>"
             for i, (n, tok, c) in enumerate(d.get("items", []), 1)]
    chunks = chunk_text("\n".join(lines)) if lines else []
    await show(cb, head, kb([btn("📋 Stored Files", "a:f:list:0")], [btn("↩️ Files", "a:f")]))
    for ch in chunks:  # persistent link lists; split to respect 4096 limit
        await ctx.bot.send_message(cb.message.chat.id, ch, disable_web_page_preview=True)
    await cb.answer()


# ------------------------------------------------------------------ import
@router.callback_query(F.data == "a:f:imp")
async def import_menu(cb: CallbackQuery, state: FSMContext, ctx, t, settings: dict) -> None:
    await state.clear()
    chans = await ctx.repos.channels.list(page=0, size=8)
    rows = [[btn("📥 Forward messages", "a:f:impfwd")]]
    for c in chans:
        on = c["_id"] in settings["import_sources"]
        rows.append([btn(f"{'✅' if on else '⬜'} Watch: {c['title'][:30]}", f"a:f:src:{c['_id']}")])
    rows.append([btn("↩️ Files", "a:f")])
    await show(cb, t("a_import_menu"), kb(*rows))
    await cb.answer()


@router.callback_query(F.data.startswith("a:f:src:"))
async def toggle_source(cb: CallbackQuery, state: FSMContext, ctx, t, settings: dict) -> None:
    cid = int(cb.data.split(":")[-1])
    src = list(settings["import_sources"])
    src.remove(cid) if cid in src else src.append(cid)
    await ctx.repos.settings.update(import_sources=src)
    s = await ctx.repos.settings.get()
    await import_menu(cb, state, ctx, t, s)


@router.callback_query(F.data == "a:f:impfwd")
async def import_forward(cb: CallbackQuery, state: FSMContext, t) -> None:
    await state.set_state(FileUpload.imp)
    await state.update_data(**_panel(cb), new=0, dup=0, skipped=0)
    await show(cb, t("a_import_forward"), kb([btn("✅ Done", "a:f:imp")]))
    await cb.answer()


@router.message(FileUpload.imp)
async def import_item(m: Message, state: FSMContext, ctx, t) -> None:
    d = await state.get_data()
    fo = getattr(m, "forward_origin", None)
    if extract_file_info(m) is None or getattr(fo, "chat", None) is None:
        d["skipped"] += 1  # not a file, or not forwarded from a channel
    else:
        res = await ctx.files.ingest(m)
        d["new" if res[2] else "dup"] += 1
    await state.update_data(**d)
    await drop(m)
    await edit_panel(ctx.bot, d["pc"], d["pm"], t("a_import_progress", **d), kb([btn("✅ Done", "a:f:imp")]))


# ------------------------------------------------------------------ listing / search / generate
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


@router.callback_query(F.data.startswith("a:f:gen:"))
async def generate(cb: CallbackQuery, ctx, t) -> None:
    await _list_screen(cb, ctx, t, page_of(cb.data), None, "a:f:gen", "a_generate", "a:f:ql")
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


async def render_detail(cb, ctx, t, fid: str) -> None:
    f = await ctx.repos.files.get(fid)
    if not f:
        await cb.answer(t("err_unavailable"), show_alert=True)
        return
    links = await ctx.repos.links.for_file(fid)
    lines = [f"{'🟢' if l['status'] == 'active' else '🚫'} <code>{ctx.links.deep_link(l['token'])}</code>" for l in links]
    text = t("a_file_detail", name=esc(_fname(f)), ftype=f["type"], size=human_size(f.get("size")),
             mime=esc(f.get("mime") or "-"), status=f["status"], prot=PROT_LABEL[f.get("protect")],
             created=f["created_at"].strftime("%Y-%m-%d %H:%M"), links="\n".join(lines) or "-")
    rows = [[btn("🔗 New link", f"a:f:nl:{fid}"), btn(f"🔒 Protect: {PROT_LABEL[f.get('protect')]}", f"a:f:pf:{fid}")]]
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


@router.callback_query(F.data.startswith("a:f:ql:"))
async def quick_link(cb: CallbackQuery, ctx, t) -> None:
    fid = cb.data.split(":")[-1]
    f = await ctx.repos.files.get(fid)
    if f:
        await ctx.links.get_or_create(fid)
    await render_detail(cb, ctx, t, fid)
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
@router.message(StateFilter(None))
async def direct_save(m: Message, ctx, t) -> None:
    res = await ctx.files.ingest(m)
    if res is None:
        return  # not a file: nothing to do
    file, link, created = res
    url = ctx.links.deep_link(link["token"])
    text = t("a_file_saved" if created else "a_file_duplicate", name=esc(_fname(file)),
             size=human_size(file.get("size")), ftype=file["type"], url=url)
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
    await edit_panel(ctx.bot, d["pc"], d["pm"], t("a_dbch_set_ok", title=esc(title)),
                     kb([btn("🗄 DB Channel", "a:f:db")], [btn("↩️ Files", "a:f")]))
