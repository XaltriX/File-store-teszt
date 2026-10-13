from __future__ import annotations
import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.utils.fancy import bold
from app.utils.formatting import esc, human_size, share_url

log = logging.getLogger(__name__)
router = Router(name="channel-import")


@router.channel_post()
async def auto_import(post: Message, ctx) -> None:
    """DB channel: every file posted there is saved and its link sent to the admin (and optionally
    replied in the channel). Watched import sources are saved silently."""
    s = await ctx.repos.settings.get()
    db = s.get("db_channel")
    is_db = bool(db) and post.chat.id == db["id"]
    if not (is_db or post.chat.id in s["import_sources"]):
        return
    try:
        res = await ctx.files.ingest(post)
    except Exception:  # noqa: BLE001
        log.exception("auto-import failed chat=%s", post.chat.id)
        return
    if not res or not is_db:
        return
    file, link, created = res
    url = ctx.links.deep_link(link["token"])
    t = lambda k, **kw: ctx.i18n.t("en", k, **kw)  # noqa: E731
    name = file.get("name") or f"{file['type']}-{file['_id'][:6]}"
    text = t("a_file_saved" if created else "a_file_duplicate", name=esc(name), size=human_size(file.get("size")),
             ftype=file["type"], url=url)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=bold("↗ Share"), url=share_url(url))]])
    recipients = {db["admin"]} if db.get("admin") else set(ctx.cfg.admin_ids)
    for admin in recipients:
        try:
            await ctx.bot.send_message(admin, text, reply_markup=kb)
        except TelegramAPIError as e:  # admin never opened the bot, etc.
            log.warning("DB-channel DM failed: %s", type(e).__name__)
    if s.get("db_reply"):
        try:
            await ctx.bot.send_message(post.chat.id, t("a_dbch_reply", url=url), reply_to_message_id=post.message_id)
        except TelegramAPIError as e:  # bot lacks "Post messages"
            log.warning("DB-channel reply failed: %s", type(e).__name__)
