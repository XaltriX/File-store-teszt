"""How many MongoDB round-trips does a request cost, and does it stay flat under a crowd?"""
import asyncio
import random
from datetime import datetime, timezone

from aiogram.exceptions import TelegramRetryAfter

from app.database.mongo import build_repos
from app.middlewares.concurrency import ConcurrencyMiddleware
from app.services.link_service import LinkService
from app.services.verification_service import MembershipChecker, VerificationService
from app.utils.flood import FloodControlMiddleware
from app.utils.messaging import MessageLifecycle
from app.utils.writebehind import WriteBehind
from tests.fakes import CountingDB, FakeBot, FakeDB


def build(n_channels=1, writer_on=True):
    db = CountingDB()
    writer = WriteBehind(enabled=writer_on)
    repos = build_repos(db, writer)
    bot = FakeBot()
    links = LinkService(repos, "testbot")
    ver = VerificationService(bot, repos, links, rng=random.Random(1), member_cache_ttl=0)
    return db, repos, bot, links, ver, writer


async def seed(repos, links, n_channels=1):
    f, _ = await repos.files.create(file_id="F", file_unique_id="u", ftype="video", name="a", size=1, mime=None, caption=None)
    link = await links.get_or_create(f["_id"])
    for i in range(n_channels):
        await repos.channels.upsert(-1001 - i, title=f"C{i}", mode="public", username=f"c{i}", url="https://t.me/c")
    return f, link


def members(bot, users, channels):
    for u in users:
        for c in channels:
            bot.members[(c, u)] = type("CM", (), {"status": "member"})()


async def test_returning_member_costs_zero_db_round_trips_after_warmup():
    db, repos, bot, links, ver, writer = build()
    f, link = await seed(repos, links)
    await repos.users.touch(1, "A", None, "a")   # the middleware registers every user before any handler runs
    members(bot, [1], [-1001])
    await ver.start(1, link["token"])          # warm caches (settings, channels, link, user)
    repos.requests.reset_guards()
    before = db.ops
    step = await ver.start(1, link["token"])
    assert step.kind == "deliver"
    assert db.ops - before == 0                # file delivered with no DB access at all


async def test_300_returning_users_at_once_do_not_touch_the_db_and_counters_are_exact():
    db, repos, bot, links, ver, writer = build()
    f, link = await seed(repos, links)
    uids = list(range(1000, 1300))
    for u in uids:
        await repos.users.touch(u, "A", None, "a")
    members(bot, uids, [-1001])
    await writer.flush()
    await ver.start(uids[0], link["token"])    # warm up
    before = db.ops
    steps = await asyncio.gather(*(ver.start(u, link["token"]) for u in uids[1:]))
    assert all(s.kind == "deliver" for s in steps)
    assert db.ops - before == 0                # not one DB round-trip for 299 simultaneous requests
    await writer.flush()
    assert (await repos.stats.totals())["requests"] == 300      # nothing lost by buffering


async def test_new_user_first_open_stays_under_a_handful_of_round_trips():
    db, repos, bot, links, ver, writer = build(n_channels=2)
    f, link = await seed(repos, links, 2)
    await ver.start(1, link["token"])          # warm shared caches with another user
    before = db.ops
    step = await ver.start(2, link["token"])   # brand-new user: needs a flow
    assert step.kind == "prompt"
    assert db.ops - before <= 4                # find user, find active flow, create flow (+ margin)


async def test_verify_success_path_is_a_few_round_trips():
    db, repos, bot, links, ver, writer = build()
    f, link = await seed(repos, links)
    step = await ver.start(5, link["token"])
    members(bot, [5], [-1001])
    before = db.ops
    r = await ver.verify(step.flow["_id"], 5)
    assert r.kind == "deliver"
    assert db.ops - before <= 7                # get flow, mark done, claim, file+link, consume request, set_fsub


# ---------------------------------------------------------------- write-behind
async def test_writer_aggregates_counters_into_one_write_per_key():
    db = CountingDB()
    w = WriteBehind(enabled=True)
    col = db["stats"]
    for _ in range(500):
        await w.inc(col, "global", "requests", upsert=True)
    assert db.ops == 0
    await w.flush()
    assert db.ops == 1 and col.docs[0]["requests"] == 500


async def test_writer_batches_inserts_and_never_drops_on_failure():
    db = CountingDB()
    w = WriteBehind(enabled=True)
    col = db["pending_deletes"]
    for i in range(50):
        await w.insert(col, {"chat_id": 1, "message_id": i, "due": datetime.now(timezone.utc)})
    await w.flush()
    assert db.ops == 1 and len(col.docs) == 50

    stats = db["stats"]
    real = stats.update_one

    async def boom(*a, **k):
        raise RuntimeError("db down")
    stats.update_one = boom
    await w.inc(stats, "g", "x", 3, upsert=True)
    await w.flush()                              # fails -> merged back, not lost
    stats.update_one = real
    await w.inc(stats, "g", "x", 2, upsert=True)  # more arrives meanwhile
    await w.flush()
    assert stats.docs[0]["x"] == 5


async def test_join_request_state_survives_restart_via_load_pending():
    db, repos, bot, links, ver, writer = build()
    await repos.requests.record_join_request(-1001, 7, 1)
    fresh = build_repos(db, writer)                     # "restarted" process: empty in-memory set
    assert not await fresh.requests.has_join_request(-1001, 7)
    assert await fresh.requests.load_pending() == 1
    assert await fresh.requests.has_join_request(-1001, 7)
    await fresh.requests.consume_join_requests(-1001, 7)
    assert not await fresh.requests.has_join_request(-1001, 7)


async def test_writer_disabled_is_write_through():
    db = CountingDB()
    w = WriteBehind(enabled=False)
    await w.inc(db["stats"], "global", "requests", upsert=True)
    assert db.ops == 1 and db["stats"].docs[0]["requests"] == 1


async def test_writer_stop_flushes_pending_data():
    db = CountingDB()
    w = WriteBehind(interval=60)
    w.start()
    await w.inc(db["stats"], "global", "requests", 7, upsert=True)
    await w.stop()
    assert db["stats"].docs[0]["requests"] == 7


async def test_auto_delete_schedules_are_batched_through_the_writer():
    db, repos, bot, links, ver, writer = build()
    lc = MessageLifecycle(bot, db["pending_deletes"], repos.settings, writer=writer)
    for i in range(20):
        await lc.schedule(1, i, 60)
    assert db.ops == 1 or len(db["pending_deletes"].docs) == 0   # at most the one settings read; inserts buffered
    await writer.flush()
    assert len(db["pending_deletes"].docs) == 20


# ---------------------------------------------------------------- caches stay correct
async def test_channel_cache_sees_admin_changes_and_local_counter_bumps():
    db, repos, bot, links, ver, writer = build()
    await seed(repos, links, 1)
    assert [c["_id"] for c in await repos.channels.eligible()] == [-1001]
    await repos.channels.inc(-1001, "presented")
    assert (await repos.channels.get(-1001))["presented"] == 1       # visible before any flush
    await repos.channels.set(-1001, active=False)
    assert await repos.channels.eligible() == []                     # admin change invalidates immediately
    await repos.channels.set(-1001, active=True)
    await repos.channels.soft_remove(-1001)
    assert await repos.channels.eligible() == []


async def test_link_cache_respects_revoke_archive_and_protect_changes():
    db, repos, bot, links, ver, writer = build()
    f, link = await seed(repos, links, 0)
    assert (await links.resolve(link["token"])).status == "ok"
    assert (await links.resolve(link["token"])).status == "ok"
    await repos.links.revoke(link["token"])
    assert (await links.resolve(link["token"])).status == "revoked"
    l2 = await links.create(f["_id"])
    assert (await links.resolve(l2["token"])).status == "ok"
    await repos.files.set_status(f["_id"], "archived")
    assert (await links.resolve(l2["token"])).status == "unavailable"
    await repos.files.set_status(f["_id"], "active")
    await repos.files.set_protect(f["_id"], True)
    assert (await links.resolve(l2["token"])).file["protect"] is True


# ---------------------------------------------------------------- Telegram protection
async def test_membership_checker_caps_concurrency_and_caches_positives():
    bot = FakeBot()
    running, peak = [0], [0]

    async def slow(chat, user):
        running[0] += 1
        peak[0] = max(peak[0], running[0])
        await asyncio.sleep(0.02)
        running[0] -= 1
        return type("CM", (), {"status": "member"})()
    bot.get_chat_member = slow
    chk = MembershipChecker(bot, concurrency=3, positive_ttl=5)
    assert all(await asyncio.gather(*(chk.is_member(-1, u) for u in range(12))))
    assert peak[0] == 3
    calls = [0]

    async def counting(chat, user):
        calls[0] += 1
        return type("CM", (), {"status": "member"})()
    bot.get_chat_member = counting
    await chk.is_member(-1, 1)           # cached from above -> no call
    assert calls[0] == 0


async def test_flood_wait_is_waited_out_and_retried_not_surfaced():
    waits = []

    async def sleeper(s): waits.append(s)
    mw = FloodControlMiddleware(sleeper=sleeper)
    attempts = [0]

    async def make_request(bot, method):
        attempts[0] += 1
        if attempts[0] == 1:
            raise TelegramRetryAfter("flood", retry_after=4)
        return "sent"
    assert await mw(make_request, None, type("M", (), {"__api_method__": "sendMessage"})()) == "sent"
    assert attempts[0] == 2 and waits == [4.5]


async def test_flood_wait_too_long_is_raised_and_getupdates_bypasses_the_cap():
    mw = FloodControlMiddleware(max_wait=10, sleeper=lambda s: asyncio.sleep(0))

    async def too_long(bot, method):
        raise TelegramRetryAfter("flood", retry_after=60)
    try:
        await mw(too_long, None, type("M", (), {"__api_method__": "sendMessage"})())
        assert False
    except TelegramRetryAfter:
        pass

    async def ok(bot, method): return "updates"
    mw.sem = asyncio.Semaphore(0)               # would deadlock any non-exempt call
    assert await mw(ok, None, type("M", (), {"__api_method__": "getUpdates"})()) == "updates"


async def test_admin_updates_are_not_queued_behind_the_user_crowd():
    from types import SimpleNamespace as NS
    mw = ConcurrencyMiddleware(max_concurrent=1, timeout=5, admin_ids={99})
    gate = asyncio.Event()
    done = []

    async def slow_user(e, d):
        await gate.wait()

    async def admin(e, d):
        done.append("admin")
    t1 = asyncio.create_task(mw(slow_user, 1, {"event_from_user": NS(id=1)}))
    await asyncio.sleep(0.01)
    t2 = asyncio.create_task(mw(slow_user, 2, {"event_from_user": NS(id=2)}))  # queued: cap is 1
    await asyncio.sleep(0.01)
    await asyncio.wait_for(mw(admin, 3, {"event_from_user": NS(id=99)}), 1)    # admin passes immediately
    assert done == ["admin"]
    gate.set()
    await asyncio.gather(t1, t2)
