"""Optional tiny HTTP endpoint. Needed only when the platform requires a bound $PORT (Heroku *web*
dyno, Render, Koyeb, ...). On a Heroku *worker* dyno it is not used."""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)


async def start_health_server(port: int):
    from aiohttp import web  # aiohttp ships with aiogram

    async def ok(_request):
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", ok)
    app.router.add_get("/health", ok)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("health endpoint on :%s", port)
    return runner
