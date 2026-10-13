from __future__ import annotations
import asyncio
import logging
import signal
import sys

from config import ConfigError, load_config
from app.utils.logging import setup_logging

log = logging.getLogger("bot")


async def main() -> int:
    try:
        cfg = load_config()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    setup_logging(cfg.log_level)

    from app.application import build_ctx
    from app.database.mongo import DatabaseUnavailable
    from app.dispatcher import build_dispatcher

    try:
        ctx = await build_ctx(cfg)
    except DatabaseUnavailable as e:
        log.critical("%s", e)
        return 3
    except Exception as e:  # noqa: BLE001 - e.g. invalid bot token; never log the token
        log.critical("startup failed: %s", type(e).__name__)
        return 4

    dp = build_dispatcher(ctx)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):  # Heroku sends SIGTERM on restart
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass

    # Drop any webhook so exactly one long-polling consumer exists.
    await ctx.bot.delete_webhook(drop_pending_updates=False)
    ctx.lifecycle.start()
    await ctx.broadcast.resume_unfinished()
    log.info("started as @%s", ctx.bot_username)

    polling = asyncio.create_task(dp.start_polling(
        ctx.bot, handle_as_tasks=False, handle_signals=False,
        allowed_updates=["message", "callback_query", "chat_join_request", "chat_member", "channel_post"]))
    waiter = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait({polling, waiter}, return_when=asyncio.FIRST_COMPLETED)
    log.info("shutting down")
    if polling in done and polling.exception():
        log.critical("polling crashed: %s", type(polling.exception()).__name__)
    if not polling.done():
        await dp.stop_polling()
    for t in (polling, waiter):
        t.cancel()
    await asyncio.gather(polling, waiter, return_exceptions=True)
    await ctx.broadcast.shutdown()
    await ctx.lifecycle.stop()
    await ctx.bot.session.close()
    await ctx.mongo.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
