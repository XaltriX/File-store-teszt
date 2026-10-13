from __future__ import annotations
import logging
from dataclasses import dataclass

from pymongo import AsyncMongoClient

from app.database.indexes import ensure_indexes
from app.database.repositories.broadcasts import BroadcastsRepo
from app.database.repositories.channels import ChannelsRepo
from app.database.repositories.files import FilesRepo, LinksRepo
from app.database.repositories.requests import RequestsRepo
from app.database.repositories.settings import SettingsRepo
from app.database.repositories.statistics import StatisticsRepo
from app.database.repositories.users import UsersRepo

log = logging.getLogger(__name__)


class DatabaseUnavailable(RuntimeError):
    pass


@dataclass
class Repos:
    users: UsersRepo
    files: FilesRepo
    links: LinksRepo
    channels: ChannelsRepo
    requests: RequestsRepo
    settings: SettingsRepo
    broadcasts: BroadcastsRepo
    stats: StatisticsRepo
    pending_deletes: object  # raw collection, used by MessageLifecycle


def build_repos(db) -> Repos:
    return Repos(
        users=UsersRepo(db["users"]),
        files=FilesRepo(db["files"]),
        links=LinksRepo(db["links"]),
        channels=ChannelsRepo(db["channels"]),
        requests=RequestsRepo(db["flows"], db["join_requests"], db["guards"]),
        settings=SettingsRepo(db["settings"]),
        broadcasts=BroadcastsRepo(db["broadcasts"], db["broadcast_deliveries"]),
        stats=StatisticsRepo(db["stats"]),
        pending_deletes=db["pending_deletes"],
    )


async def connect(url: str, db_name: str) -> tuple[AsyncMongoClient, Repos]:
    client = AsyncMongoClient(url, serverSelectionTimeoutMS=8000, appname="file-store-bot")
    try:
        await client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001 - surfaced as clear startup failure
        await client.close()
        raise DatabaseUnavailable(f"MongoDB unreachable: {type(exc).__name__}") from exc
    db = client[db_name]
    await ensure_indexes(db)
    log.info("MongoDB connected, indexes ensured")
    return client, build_repos(db)
