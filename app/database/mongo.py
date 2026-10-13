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
from app.utils.writebehind import WriteBehind

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
    writer: WriteBehind


def build_repos(db, writer: WriteBehind | None = None) -> Repos:
    writer = writer or WriteBehind(enabled=False)
    return Repos(
        users=UsersRepo(db["users"], writer),
        files=FilesRepo(db["files"]),
        links=LinksRepo(db["links"]),
        channels=ChannelsRepo(db["channels"], writer),
        requests=RequestsRepo(db["flows"], db["join_requests"]),
        settings=SettingsRepo(db["settings"]),
        broadcasts=BroadcastsRepo(db["broadcasts"], db["broadcast_deliveries"]),
        stats=StatisticsRepo(db["stats"], writer),
        pending_deletes=db["pending_deletes"],
        writer=writer,
    )


async def connect(url: str, db_name: str, writer: WriteBehind | None = None) -> tuple[AsyncMongoClient, Repos]:
    # Explicit timeouts: without them a silently dropped connection can hang an await forever.
    client = AsyncMongoClient(url, serverSelectionTimeoutMS=8000, connectTimeoutMS=8000, socketTimeoutMS=15000,
                              waitQueueTimeoutMS=10000, maxPoolSize=50, maxIdleTimeMS=60000,
                              appname="file-store-bot")
    try:
        await client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001 - surfaced as clear startup failure
        await client.close()
        raise DatabaseUnavailable(f"MongoDB unreachable: {type(exc).__name__}") from exc
    db = client[db_name]
    await ensure_indexes(db)
    log.info("MongoDB connected, indexes ensured")
    return client, build_repos(db, writer)
