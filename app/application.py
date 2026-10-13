from __future__ import annotations
import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from app.database.mongo import Repos, connect
from app.localization import Localizer
from app.services.broadcast_service import BroadcastService
from app.services.delivery_service import DeliveryService
from app.services.file_service import FileService
from app.services.link_service import LinkService
from app.services.statistics_service import StatisticsService
from app.services.verification_service import VerificationService
from app.services.welcome_service import WelcomeService
from app.utils.flood import FloodControlMiddleware
from app.utils.messaging import MessageLifecycle
from app.utils.writebehind import WriteBehind
from config import Config

log = logging.getLogger(__name__)


@dataclass
class Ctx:
    cfg: Config
    bot: Bot
    bot_id: int
    bot_username: str
    repos: Repos
    i18n: Localizer
    lifecycle: MessageLifecycle
    links: LinkService
    files: FileService
    verification: VerificationService
    delivery: DeliveryService
    broadcast: BroadcastService
    stats: StatisticsService
    welcome: WelcomeService
    mongo: object
    writer: WriteBehind


async def build_ctx(cfg: Config) -> Ctx:
    bot = Bot(cfg.bot_token, session=AiohttpSession(timeout=25),  # no Telegram call may hang the loop
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    writer = WriteBehind(interval=1.5)  # counters/last-activity/delete-schedules leave the request path
    mongo, repos = await connect(cfg.mongo_url, cfg.mongo_db, writer)  # fails fast if DB unreachable
    bot.session.middleware(FloodControlMiddleware())
    log.info("pending join requests loaded: %d", await repos.requests.load_pending())
    try:
        me = await bot.get_me()  # fails fast on invalid token
    except Exception:
        await mongo.close()
        await bot.session.close()
        raise
    i18n = Localizer()
    links = LinkService(repos, me.username)
    lifecycle = MessageLifecycle(bot, repos.pending_deletes, repos.settings, writer=writer)
    return Ctx(
        cfg=cfg, bot=bot, bot_id=me.id, bot_username=me.username, repos=repos, i18n=i18n,
        lifecycle=lifecycle,
        links=links, files=FileService(repos, links),
        verification=VerificationService(bot, repos, links, ttl_seconds=cfg.flow_ttl_seconds),
        delivery=DeliveryService(bot, repos, i18n, links, lifecycle),
        broadcast=BroadcastService(bot, repos), stats=StatisticsService(repos),
        welcome=WelcomeService(bot, repos, i18n), mongo=mongo, writer=writer)
