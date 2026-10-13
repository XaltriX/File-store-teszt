from __future__ import annotations
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.handlers.admin.common import drop, edit_panel, show, temp
from app.keyboards.admin import btn, cancel, confirm, kb, settings_menu
from app.localization import LANGUAGES
from app.states.welcome_editor import SettingsInput
from app.utils.formatting import esc, is_valid_url

log = logging.getLogger(__name__)
router = Router(name="admin-settings")

# fields whose change is previewed on the real welcome card before it is applied
PREVIEWED = {"welcome_text", "welcome_media", "welcome_buttons", "more_videos_url", "developer_url"}
PROMPT = {"welcome_media": "a_set_image", "welcome_text": "a_set_text", "welcome_buttons": "a_set_buttons",
          "more_videos_url": "a_set_url", "developer_url": "a_set_url", "official_channels": "a_set_channels",
          "about_text": "a_set_about"}
SCREEN = {"wi": "welcome_media", "wt": "welcome_text", "wb": "welcome_buttons", "mv": "more_videos_url",
          "dv": "developer_url", "oc": "official_channels", "ab": "about_text"}
EMPTY = {"welcome_buttons": [], "official_channels": [], "welcome_media": None, "welcome_text": None,
         "more_videos_url": None, "developer_url": None, "about_text": None}
MAX_LINES = {"welcome_buttons": 6, "official_channels": 20}


def parse_pairs(text: str, limit: int) -> list[dict] | None:
    """'Label | URL' per line. Returns None if anything is invalid."""
    out = []
    for line in text.strip().split("\n"):
        if not line.strip():
            continue
        if "|" not in line:
            return None
        label, url = (p.strip() for p in line.rsplit("|", 1))
        if not label or len(label) > 40 or not is_valid_url(url) or len(url) > 300:
            return None
        out.append({"text": label, "title": label, "url": url})
    if not out or len(out) > limit:
        return None
    return out


def current_value_text(field: str, s: dict) -> str:
    v = s.get(field)
    if field == "welcome_media":
        return f"{v['type']} set" if isinstance(v, dict) else "none"
    if isinstance(v, list):
        return "\n".join(f"• {esc(i.get('text') or i.get('title'))} → {esc(i['url'])}" for i in v) or "none"
    return esc(v) if v else "default / none"


@router.callback_query(F.data == "a:t")
async def menu(cb: CallbackQuery, state: FSMContext, t) -> None:
    await state.clear()
    await show(cb, t("a_settings_menu"), settings_menu())
    await cb.answer()


async def _render_field(cb: CallbackQuery, state: FSMContext, t, settings: dict, code: str) -> None:
    field = SCREEN[code]
    await state.clear()
    await show(cb, t("a_field_screen", field=field, current=current_value_text(field, settings)),
               kb([btn("✏️ Set", f"a:t:set:{code}"), btn("🧽 Clear/Reset", f"a:t:clr:{code}")],
                  [btn("↩️ Settings", "a:t")]))


@router.callback_query(F.data.regexp(r"^a:t:(wi|wt|wb|mv|dv|oc|ab)$"))
async def field_screen(cb: CallbackQuery, state: FSMContext, t, settings: dict) -> None:
    await _render_field(cb, state, t, settings, cb.data.split(":")[-1])
    await cb.answer()


@router.callback_query(F.data.startswith("a:t:set:"))
async def set_prompt(cb: CallbackQuery, state: FSMContext, t) -> None:
    code = cb.data.split(":")[-1]
    field = SCREEN[code]
    await state.set_state(SettingsInput.value)
    await state.update_data(field=field, pc=cb.message.chat.id, pm=cb.message.message_id)
    await show(cb, t(PROMPT[field]), cancel(f"a:t:{code}"))
    await cb.answer()


@router.callback_query(F.data.startswith("a:t:clr:"))
async def clear_ask(cb: CallbackQuery, t) -> None:
    code = cb.data.split(":")[-1]
    await show(cb, t("a_clear_confirm"), confirm(f"a:t:clrc:{code}", f"a:t:{code}", "🧽 Clear"))
    await cb.answer()


@router.callback_query(F.data.startswith("a:t:clrc:"))
async def clear_do(cb: CallbackQuery, state: FSMContext, ctx, t) -> None:
    code = cb.data.split(":")[-1]
    await ctx.repos.settings.update(**{SCREEN[code]: EMPTY[SCREEN[code]]})
    await _render_field(cb, state, t, await ctx.repos.settings.get(), code)
    await cb.answer()


@router.message(SettingsInput.value)
async def got_value(m: Message, state: FSMContext, ctx, t, lang: str, settings: dict) -> None:
    d = await state.get_data()
    field = d["field"]
    await drop(m)
    value = None
    if field == "welcome_media":
        if m.animation:
            value = {"type": "animation", "file_id": m.animation.file_id}
        elif m.video:
            value = {"type": "video", "file_id": m.video.file_id}
        elif m.photo:
            value = {"type": "photo", "file_id": m.photo[-1].file_id}
        else:
            await temp(ctx, m.chat.id, t("a_need_photo"))
            return
    elif m.text is None:
        await temp(ctx, m.chat.id, t("a_need_text"))
        return
    elif field in ("welcome_text", "about_text"):
        if len(m.text) > 900:
            await temp(ctx, m.chat.id, t("a_too_long"))
            return
        value = m.text
    elif field in ("more_videos_url", "developer_url"):
        if not is_valid_url(m.text):
            await temp(ctx, m.chat.id, t("a_bad_url"))
            return
        value = m.text.strip()
    else:
        value = parse_pairs(m.text, MAX_LINES[field])
        if value is None:
            await temp(ctx, m.chat.id, t("a_bad_pairs"))
            return
        if field == "welcome_buttons":
            value = [{"text": v["text"], "url": v["url"]} for v in value]
        else:
            value = [{"title": v["title"], "url": v["url"]} for v in value]
    if field in PREVIEWED:
        merged = {**settings, field: value}
        try:
            pv = await ctx.welcome.send(m.chat.id, lang, m.from_user.first_name, merged)
            await state.update_data(pv=pv.message_id)
            await ctx.lifecycle.schedule(m.chat.id, pv.message_id, 300)
        except TelegramAPIError:
            await temp(ctx, m.chat.id, t("a_preview_failed"))
            return
        await state.update_data(value=value)
        await edit_panel(ctx.bot, d["pc"], d["pm"], t("a_preview_apply"), confirm("a:t:apply", "a:t:cancel", "✅ Apply"))
        return
    await ctx.repos.settings.update(**{field: value})
    await state.clear()
    await edit_panel(ctx.bot, d["pc"], d["pm"], t("a_saved"), kb([btn("↩️ Settings", "a:t")]))


async def _drop_preview(ctx, cb: CallbackQuery, d: dict) -> None:
    if d.get("pv"):
        try:
            await ctx.bot.delete_message(cb.message.chat.id, d["pv"])
        except TelegramAPIError:
            pass


@router.callback_query(F.data == "a:t:apply")
async def apply(cb: CallbackQuery, state: FSMContext, ctx, t) -> None:
    d = await state.get_data()
    if "field" not in d or "value" not in d or d["field"] not in PREVIEWED:
        await cb.answer(t("err_flow_expired"), show_alert=True)
        return
    await ctx.repos.settings.update(**{d["field"]: d["value"]})
    await _drop_preview(ctx, cb, d)
    await state.clear()
    await show(cb, t("a_saved"), kb([btn("↩️ Settings", "a:t")]))
    await cb.answer()


@router.callback_query(F.data == "a:t:cancel")
async def cancel_change(cb: CallbackQuery, state: FSMContext, ctx, t) -> None:
    d = await state.get_data()
    await _drop_preview(ctx, cb, d)
    await state.clear()
    await show(cb, t("a_settings_menu"), settings_menu())
    await cb.answer()


# ------------------------------------------------------------------ protection / auto-delete
@router.callback_query(F.data.in_({"a:t:prot", "a:t:prot:tg"}))
async def protection(cb: CallbackQuery, ctx, t) -> None:
    s = await ctx.repos.settings.get()
    if cb.data.endswith(":tg"):
        await ctx.repos.settings.update(default_protect=not s["default_protect"])
        s = await ctx.repos.settings.get()
    st = "ON" if s["default_protect"] else "OFF"
    await show(cb, t("a_protection", state=st), kb([btn(f"🔐 Default: {st} (tap to toggle)", "a:t:prot:tg")],
                                                   [btn("↩️ Settings", "a:t")]))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^a:t:ad(:\d+)?$"))
async def autodelete(cb: CallbackQuery, ctx, t) -> None:
    parts = cb.data.split(":")
    if len(parts) == 4 and int(parts[3]) in (0, 30, 60, 120, 300):
        await ctx.repos.settings.update(autodelete_seconds=int(parts[3]))
    cur = (await ctx.repos.settings.get())["autodelete_seconds"]
    opts = [(0, "Off"), (30, "30s"), (60, "60s"), (120, "2m"), (300, "5m")]
    await show(cb, t("a_autodelete", cur=cur or "off"),
               kb([btn(("● " if cur == v else "") + lbl, f"a:t:ad:{v}") for v, lbl in opts], [btn("↩️ Settings", "a:t")]))
    await cb.answer()


# ------------------------------------------------------------------ languages
async def _lang_screen(cb, ctx, t) -> None:
    s = await ctx.repos.settings.get()
    rows = []
    for code, name in LANGUAGES.items():
        on = code in s["enabled_languages"]
        rows.append([btn(f"{'✅' if on else '⬜'} {name}", f"a:t:lt:{code}"),
                     btn(("⭐ default" if s["default_language"] == code else "set default"), f"a:t:ld:{code}")])
    rows.append([btn("↩️ Settings", "a:t")])
    await show(cb, t("a_languages"), kb(*rows))


@router.callback_query(F.data == "a:t:lang")
async def languages(cb: CallbackQuery, ctx, t) -> None:
    await _lang_screen(cb, ctx, t)
    await cb.answer()


@router.callback_query(F.data.startswith("a:t:lt:"))
async def lang_toggle(cb: CallbackQuery, ctx, t) -> None:
    code = cb.data.split(":")[-1]
    s = await ctx.repos.settings.get()
    en = list(s["enabled_languages"])
    if code in en and len(en) > 1 and code != s["default_language"]:
        en.remove(code)
    elif code not in en and code in LANGUAGES:
        en.append(code)
    else:
        await cb.answer(t("a_lang_keep"), show_alert=True)
        return
    await ctx.repos.settings.update(enabled_languages=en)  # users keep their stored language
    await _lang_screen(cb, ctx, t)
    await cb.answer()


@router.callback_query(F.data.startswith("a:t:ld:"))
async def lang_default(cb: CallbackQuery, ctx, t) -> None:
    code = cb.data.split(":")[-1]
    if code in LANGUAGES:
        s = await ctx.repos.settings.get()
        en = list(s["enabled_languages"])
        if code not in en:
            en.append(code)
        await ctx.repos.settings.update(default_language=code, enabled_languages=en)
    await _lang_screen(cb, ctx, t)
    await cb.answer()
