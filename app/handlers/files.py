from __future__ import annotations
import logging

from aiogram import Router
from aiogram.types import Message

log = logging.getLogger(__name__)
router = Router(name="channel-import")


@router.channel_post()
async def auto_import(post: Message, ctx) -> None:
    """Auto-import media from channels the admin marked as watched (Settings-independent, no user context)."""
    s = await ctx.repos.settings.get()
    if post.chat.id not in s["import_sources"]:
        return
    try:
        res = await ctx.files.ingest(post)
    except Exception:  # noqa: BLE001
        log.exception("auto-import failed chat=%s", post.chat.id)
        return
    if res:
        log.info("imported file from channel %s (created=%s)", post.chat.id, res[2])
