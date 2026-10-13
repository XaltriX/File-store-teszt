from __future__ import annotations
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware

log = logging.getLogger(__name__)


class UserContextMiddleware(BaseMiddleware):
    """Registers/refreshes the user and injects user, lang, t into handler data."""

    def __init__(self, ctx):
        self.ctx = ctx

    async def __call__(self, handler: Callable[[Any, dict], Awaitable[Any]], event, data: dict):
        u = data.get("event_from_user")
        if u is None or u.is_bot:
            return await handler(event, data)
        ctx = self.ctx
        doc, is_new = await ctx.repos.users.touch(u.id, u.first_name, u.last_name, u.username)
        if is_new:
            await ctx.repos.stats.inc_new_user()
        s = await ctx.repos.settings.get()
        lang = ctx.i18n.normalize(doc.get("language"), s["enabled_languages"])
        if doc.get("language") is None:
            lang = ctx.i18n.normalize(s["default_language"])
        data.update(user=doc, lang=lang, t=lambda key, **kw: ctx.i18n.t(lang, key, **kw), ctx=ctx, settings=s)
        return await handler(event, data)
