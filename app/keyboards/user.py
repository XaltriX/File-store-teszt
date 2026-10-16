from aiogram.types import InlineKeyboardButton as _B, InlineKeyboardMarkup as M

from app.utils.fancy import bold

from app.localization import LANGUAGES


def B(text: str, **kw) -> _B:
    return _B(text=bold(text), **kw)


def language_keyboard(enabled: list[str], token: str | None = None) -> M:
    """token: pending file-link token, carried in the button so the file flow resumes after choosing."""
    suffix = f":{token}" if token else ""
    codes = [c for c in LANGUAGES if c in enabled] or ["en"]
    rows = [[B(LANGUAGES[c], callback_data=f"l:{c}{suffix}") for c in codes[i:i + 2]] for i in range(0, len(codes), 2)]
    return M(inline_keyboard=rows)


def welcome_keyboard(t, s: dict, is_admin: bool = False) -> M:
    rows = []
    mv = s.get("more_videos_url")
    rows.append([B(t("btn_more"), url=mv) if mv else B(t("btn_more"), callback_data="u:nc")])
    dv = s.get("developer_url")
    dev = B(t("btn_dev"), url=dv) if dv else B(t("btn_dev"), callback_data="u:nc")
    rows.append([dev, B(t("btn_channels"), callback_data="u:ch")])
    rows.append([B(t("btn_about"), callback_data="u:about"), B(t("btn_lang"), callback_data="u:lang")])
    for b in s.get("welcome_buttons") or []:
        rows.append([B(b["text"], url=b["url"])])
    if is_admin:  # visible to admins only; every admin callback is still authorized server-side
        rows.append([B("🛠 Admin Panel", callback_data="a:home")])
    return M(inline_keyboard=rows)


def back_keyboard(t) -> M:
    return M(inline_keyboard=[[B(t("btn_back"), callback_data="u:home")]])


def verify_keyboard(t, flow: dict, pending: list[dict], flow_id: str) -> M:
    """One button per channel, in the order of the flow. Finished channels show '✅ Joined'."""
    ids = flow["channel_ids"]
    total = len(ids)
    by_id = {c["_id"]: c for c in pending}
    rows = []
    for i, cid in enumerate(ids, 1):
        n = f" {i}" if total > 1 else ""
        ch = by_id.get(cid)
        if ch is None:
            rows.append([B(f'{t("btn_joined")}{n}', callback_data="u:noop")])
        else:
            key = "btn_request" if ch["mode"] == "request" else "btn_join"
            rows.append([B(f"{t(key)}{n}", url=ch["url"])])
    rows.append([B(t("btn_verify"), callback_data=f"v:{flow_id}")])
    return M(inline_keyboard=rows)


def retry_keyboard(t, flow_id: str) -> M:
    return M(inline_keyboard=[[B(t("btn_verify"), callback_data=f"v:{flow_id}")]])
