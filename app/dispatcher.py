from __future__ import annotations
import logging

from aiogram import Dispatcher, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent
from pymongo.errors import PyMongoError

from app.handlers import callbacks, files, language, start, verification
from app.handlers.admin import (broadcasts, channel_management, dashboard, file_management,
                                settings, statistics)
from app.handlers.admin.broadcasts import make_notifier
from app.middlewares.admin_auth import AdminAuthMiddleware
from app.middlewares.concurrency import ConcurrencyMiddleware
from app.middlewares.language import UserContextMiddleware
from app.middlewares.throttle import ThrottleMiddleware

log = logging.getLogger(__name__)


def build_dispatcher(ctx) -> Dispatcher:
    # FSM storage is in-memory by design: it only holds short admin input flows. Verification
    # flows, links, settings and broadcast jobs are all persisted in MongoDB.
    dp = Dispatcher(storage=MemoryStorage(), ctx=ctx)
    throttle = ThrottleMiddleware(ctx.cfg.admin_ids)
    concurrency = ConcurrencyMiddleware(admin_ids=ctx.cfg.admin_ids)
    users = UserContextMiddleware(ctx)
    for obs in (dp.message, dp.callback_query):
        obs.outer_middleware(throttle)
        obs.outer_middleware(concurrency)
        obs.outer_middleware(users)

    admin = Router(name="admin")
    auth = AdminAuthMiddleware(ctx.cfg.admin_ids)
    admin.message.middleware(auth)
    admin.callback_query.middleware(auth)
    for r in (dashboard, file_management, channel_management, broadcasts, settings, statistics):
        admin.include_router(r.router)

    ctx.broadcast.notify = make_notifier(ctx, lambda admin_id: (lambda k, **kw: ctx.i18n.t("en", k, **kw)))

    dp.include_routers(start.router, admin, language.router, callbacks.router, verification.router, files.router)
    dp.errors.register(on_error)
    return dp


async def on_error(event: ErrorEvent, ctx) -> bool:
    exc = event.exception
    upd = event.update
    target = (upd.message and upd.message.chat.id) or (upd.callback_query and upd.callback_query.from_user.id)
    if isinstance(exc, PyMongoError):
        log.error("database error: %s", type(exc).__name__)
        key = "err_db"
    else:
        log.exception("unhandled error in update %s", upd.update_id)
        key = "err_generic"
    if target:
        try:
            await ctx.bot.send_message(target, ctx.i18n.t(None, key))
        except TelegramAPIError:
            pass
    if upd.callback_query:
        try:
            await upd.callback_query.answer()
        except TelegramAPIError:
            pass
    return True
