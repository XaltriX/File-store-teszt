import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.mongo import build_repos  # noqa: E402
from app.localization import Localizer  # noqa: E402
from app.services.broadcast_service import BroadcastService  # noqa: E402
from app.services.delivery_service import DeliveryService  # noqa: E402
from app.services.file_service import FileService  # noqa: E402
from app.services.link_service import LinkService  # noqa: E402
from app.services.verification_service import VerificationService  # noqa: E402
from tests.fakes import FakeBot, FakeDB  # noqa: E402


class Env:
    pass


@pytest.fixture
def env():
    e = Env()
    e.db = FakeDB()
    e.repos = build_repos(e.db)
    e.bot = FakeBot()
    e.i18n = Localizer()
    e.links = LinkService(e.repos, "testbot")
    e.files = FileService(e.repos, e.links)
    e.verification = VerificationService(e.bot, e.repos, e.links, rng=__import__("random").Random(1),
                                         member_cache_ttl=0)
    e.delivery = DeliveryService(e.bot, e.repos, e.i18n, e.links)

    async def _noop(_):
        return None
    e.sleep = _noop
    e.broadcast = BroadcastService(e.bot, e.repos, rate=5, sleeper=_noop)
    return e


async def make_file(env, uid="u1", name="a.mp4", ftype="video", duration=None):
    f, _ = await env.repos.files.create(file_id=f"F-{uid}", file_unique_id=uid, ftype=ftype, name=name,
                                        size=10, mime="video/mp4", caption=None, duration=duration)
    link = await env.links.get_or_create(f["_id"])
    return f, link


async def make_channel(env, cid, mode="public", presented=0, active=True):
    c = await env.repos.channels.upsert(cid, title=f"C{cid}", mode=mode,
                                        username=f"c{abs(cid)}" if mode == "public" else None,
                                        url=f"https://t.me/c{abs(cid)}" if mode == "public" else "https://t.me/+abc")
    await env.repos.channels.set(cid, presented=presented, active=active)
    return c


def member(env, chat_id, user_id, status="member"):
    env.bot.members[(chat_id, user_id)] = type("CM", (), {"status": status})()
