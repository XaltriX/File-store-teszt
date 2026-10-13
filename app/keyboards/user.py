from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as M

from app.localization import LANGUAGES


def language_keyboard(enabled: list[str]) -> M:
    codes = [c for c in LANGUAGES if c in enabled] or ["en"]
    rows = [[B(text=LANGUAGES[c], callback_data=f"l:{c}") for c in codes[i:i + 2]] for i in range(0, len(codes), 2)]
    return M(inline_keyboard=rows)


def welcome_keyboard(t, s: dict) -> M:
    rows = []
    mv = s.get("more_videos_url")
    rows.append([B(text=t("btn_more"), url=mv) if mv else B(text=t("btn_more"), callback_data="u:nc")])
    dv = s.get("developer_url")
    dev = B(text=t("btn_dev"), url=dv) if dv else B(text=t("btn_dev"), callback_data="u:nc")
    rows.append([dev, B(text=t("btn_channels"), callback_data="u:ch")])
    rows.append([B(text=t("btn_about"), callback_data="u:about"), B(text=t("btn_lang"), callback_data="u:lang")])
    for b in s.get("welcome_buttons") or []:
        rows.append([B(text=b["text"], url=b["url"])])
    return M(inline_keyboard=rows)


def back_keyboard(t) -> M:
    return M(inline_keyboard=[[B(text=t("btn_back"), callback_data="u:home")]])


def verify_keyboard(t, channel: dict, flow_id: str) -> M:
    join_key = "btn_request" if channel["mode"] == "request" else "btn_join"
    return M(inline_keyboard=[[B(text=t(join_key), url=channel["url"])],
                              [B(text=t("btn_verify"), callback_data=f"v:{flow_id}")]])


def retry_keyboard(t, flow_id: str) -> M:
    return M(inline_keyboard=[[B(text=t("btn_verify"), callback_data=f"v:{flow_id}")]])
