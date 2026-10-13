from __future__ import annotations
import logging

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from app.utils.telegram_errors import is_not_modified

log = logging.getLogger(__name__)


async def show(target: CallbackQuery | Message, text: str, kb=None) -> None:
    """Edit the persistent panel message in place; send a new one only if editing is impossible."""
    msg = target.message if isinstance(target, CallbackQuery) else target
    try:
        if msg.text is not None:
            await msg.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
        else:
            await msg.edit_caption(caption=text, reply_markup=kb)
    except TelegramBadRequest as e:
        if is_not_modified(e):
            return
        try:
            await msg.delete()
        except TelegramAPIError:
            pass
        await msg.answer(text, reply_markup=kb, disable_web_page_preview=True)
    except TelegramAPIError as e:
        log.warning("panel edit failed: %s", type(e).__name__)


async def edit_panel(bot, chat_id: int, message_id: int, text: str, kb=None) -> None:
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=kb,
                                    disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if not is_not_modified(e):
            await bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)
    except TelegramAPIError as e:
        log.warning("panel edit failed: %s", type(e).__name__)


async def temp(ctx, chat_id: int, text: str, delay: int | None = None) -> None:
    try:
        m = await ctx.bot.send_message(chat_id, text)
        await ctx.lifecycle.schedule(chat_id, m.message_id, delay)
    except TelegramAPIError as e:
        log.warning("temp send failed: %s", type(e).__name__)


async def drop(msg: Message) -> None:
    try:
        await msg.delete()
    except TelegramAPIError:
        pass


def page_of(data: str, idx: int = -1) -> int:
    try:
        return max(0, int(data.split(":")[idx]))
    except (ValueError, IndexError):
        return 0
