
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from config import ConfigError, load_config
from app.utils.logging import setup_logging

log = logging.getLogger("bot")


async def main() -> int:
    # Step 1: Load and validate configuration.
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    setup_logging(cfg.log_level)

    # Step 2: Import application modules.
    from app.application import build_ctx
    from app.database.mongo import DatabaseUnavailable
    from app.dispatcher import build_dispatcher

    # Step 3: Initialize application and database.
    try:
        ctx = await build_ctx(cfg)

    except DatabaseUnavailable as exc:
        log.critical("Database unavailable: %s", exc)
        return 3

    except Exception:
        # Log the complete traceback to identify the exact failing line.
        log.exception("Startup failed during build_ctx()")
        return 4

    # Step 4: Build the Telegram dispatcher.
    try:
        dp = build_dispatcher(ctx)
    except Exception:
        log.exception("Failed to build Telegram dispatcher")

        try:
            await ctx.bot.session.close()
        except Exception:
            log.exception("Bot session cleanup failed")

        try:
            await ctx.mongo.close()
        except Exception:
            log.exception("MongoDB cleanup failed")

        return 4

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    # Heroku sends SIGTERM during restarts and dyno shutdown.
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            # Signal handlers may not be available on every platform.
            pass

    polling: asyncio.Task | None = None
    waiter: asyncio.Task | None = None

    try:
        # Remove any existing webhook before starting long polling.
        await ctx.bot.delete_webhook(drop_pending_updates=False)

        ctx.lifecycle.start()

        await ctx.broadcast.resume_unfinished()

        log.info("Started as @%s", ctx.bot_username)

        # Step 5: Start Telegram polling.
        polling = asyncio.create_task(
            dp.start_polling(
                ctx.bot,
                handle_as_tasks=False,
                handle_signals=False,
                allowed_updates=[
                    "message",
                    "callback_query",
                    "chat_join_request",
                    "chat_member",
                    "channel_post",
                ],
            ),
            name="telegram-polling",
        )

        waiter = asyncio.create_task(
            stop.wait(),
            name="shutdown-waiter",
        )

        done, _ = await asyncio.wait(
            {polling, waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Report unexpected polling failures with their full traceback.
        if polling in done:
            if polling.cancelled():
                log.warning("Telegram polling task was cancelled")
            else:
                error = polling.exception()

                if error is not None:
                    log.error(
                        "Telegram polling crashed",
                        exc_info=(
                            type(error),
                            error,
                            error.__traceback__,
                        ),
                    )
                    return 5

        log.info("Shutdown requested")

        if not polling.done():
            await dp.stop_polling()

        return 0

    except asyncio.CancelledError:
        log.info("Application cancellation received")
        raise

    except Exception:
        log.exception("Unexpected application runtime error")
        return 5

    finally:
        # Cancel and collect outstanding tasks.
        for task in (polling, waiter):
            if task is not None and not task.done():
                task.cancel()

        tasks = [
            task
            for task in (polling, waiter)
            if task is not None
        ]

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Clean up application resources.
        try:
            await ctx.broadcast.shutdown()
        except Exception:
            log.exception("Broadcast shutdown failed")

        try:
            await ctx.lifecycle.stop()
        except Exception:
            log.exception("Lifecycle shutdown failed")

        try:
            await ctx.bot.session.close()
        except Exception:
            log.exception("Bot session cleanup failed")

        try:
            await ctx.mongo.close()
        except Exception:
            log.exception("MongoDB cleanup failed")


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
    except KeyboardInterrupt:
        exit_code = 0
    except Exception:
        logging.getLogger("bot").exception(
            "Fatal error outside main()"
        )
        exit_code = 1

    sys.exit(exit_code)
