from __future__ import annotations
import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
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
from app.utils.messaging import MessageLifecycle
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


async def build_ctx(cfg: Config) -> Ctx:
    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    mongo, repos = await connect(cfg.mongo_url, cfg.mongo_db)  # fails fast if DB unreachable
    try:
        me = await bot.get_me()  # fails fast on invalid token
    except Exception:
        await mongo.close()
        await bot.session.close()
        raise
    i18n = Localizer()
    links = LinkService(repos, me.username)
    return Ctx(
        cfg=cfg, bot=bot, bot_id=me.id, bot_username=me.username, repos=repos, i18n=i18n,
        lifecycle=MessageLifecycle(bot, repos.pending_deletes, repos.settings),
        links=links, files=FileService(repos, links),
        verification=VerificationService(bot, repos, links, ttl_seconds=cfg.flow_ttl_seconds),
        delivery=DeliveryService(bot, repos, i18n, links),
        broadcast=BroadcastService(bot, repos), stats=StatisticsService(repos),
        welcome=WelcomeService(bot, repos, i18n), mongo=mongo)
