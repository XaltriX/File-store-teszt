from __future__ import annotations
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery


class AdminAuthMiddleware(BaseMiddleware):
    """Server-side authorization on EVERY admin message/callback. Hidden buttons are not a control."""

    def __init__(self, admin_ids):
        self.admin_ids = frozenset(admin_ids)

    async def __call__(self, handler, event, data):
        u = data.get("event_from_user")
        if u is None or u.id not in self.admin_ids:
            if isinstance(event, CallbackQuery):
                await event.answer("⛔ Not authorized", show_alert=True)
            return None
        return await handler(event, data)
