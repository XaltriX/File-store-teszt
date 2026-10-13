import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from app.database.repositories.settings import SettingsRepo
from app.database.repositories.users import UsersRepo
from app.handlers.admin.file_management import direct_save
from app.handlers.files import auto_import
from app.middlewares.concurrency import ConcurrencyMiddleware
from app.services.delivery_service import DeliveryService
from app.utils.messaging import MessageLifecycle
from app.utils.writebehind import WriteBehind
from tests.conftest import make_channel, make_file, member
from tests.fakes import FakeCollection


# ---------------------------------------------------------------- caches
class CountingCol(FakeCollection):
    reads = 0

    async def find_one(self, *a, **k):
        CountingCol.reads += 1
        return await super().find_one(*a, **k)


async def test_settings_are_cached_and_invalidated_on_write():
    CountingCol.reads = 0
    t = [0.0]
    repo = SettingsRepo(CountingCol(), ttl=5, clock=lambda: t[0])
    await repo.get(); await repo.get(); await repo.get()
    assert CountingCol.reads == 1
    await repo.update(required_steps=3)           # own write is visible immediately
    assert (await repo.get())["required_steps"] == 3 and CountingCol.reads == 2
    t[0] = 6
    await repo.get()
    assert CountingCol.reads == 3                 # expired -> re-read


async def test_cached_settings_cannot_be_mutated_by_callers():
    repo = SettingsRepo(FakeCollection())
    s = await repo.get()
    s["enabled_languages"].append("zz")
    assert "zz" not in (await repo.get())["enabled_languages"]


async def test_user_touch_hits_db_once_and_language_change_is_visible():
    CountingCol.reads = 0
    repo = UsersRepo(CountingCol(), WriteBehind())
    await repo.touch(1, "A", None, "a")
    await repo.touch(1, "A", None, "a")
    await repo.touch(1, "A", None, "a")
    assert CountingCol.reads == 1
    await repo.set_language(1, "ta")
    doc, new = await repo.touch(1, "A", None, "a")
    assert doc["language"] == "ta" and not new


# ---------------------------------------------------------------- verification speed / safety
async def test_start_checks_all_channels_once_each_in_parallel(env):
    f, link = await make_file(env)
    for i in range(3):
        await make_channel(env, -1001 - i)
    await env.repos.settings.update(required_steps=3)
    await env.verification.start(5, link["token"])
    assert len(env.bot.sent("get_chat_member")) == 3


async def test_membership_lookup_failure_never_trusts_a_stored_request(env):
    f, link = await make_file(env)
    await make_channel(env, -1001, "request")
    step = await env.verification.start(5, link["token"])
    await env.verification.on_join_request(-1001, 5, 1)  # completes the first flow (event is live proof)
    env.repos.requests.reset_guards()
    f2, l2 = await make_file(env, "z", "z.mp4")
    env.bot.raise_on["get_chat_member"] = TelegramBadRequest("chat not found")
    step = await env.verification.start(5, l2["token"])
    assert step.kind == "prompt"                                    # not delivered on a stale request
    r = await env.verification.verify(step.flow["_id"], 5)
    assert r.kind == "retry" and r.key == "verify_api_error"


async def test_old_unconsumed_join_request_expires(env):
    f, link = await make_file(env)
    await make_channel(env, -1001, "request")
    await env.verification.on_join_request(-1001, 5, 1)
    env.db["join_requests"].docs[0]["created_at"] = datetime.now(timezone.utc) - timedelta(days=8)
    assert (await env.verification.start(5, link["token"])).kind == "prompt"


# ---------------------------------------------------------------- delivery from DB copy + auto-delete
def delivery(env, with_lifecycle=True):
    lc = MessageLifecycle(env.bot, env.db["pending_deletes"], env.repos.settings) if with_lifecycle else None
    return DeliveryService(env.bot, env.repos, env.i18n, env.links, lc)


async def test_delivery_copies_from_db_channel_and_falls_back_to_file_id(env):
    f, link = await make_file(env)
    f["store"] = {"chat_id": -100777, "message_id": 42}
    d = delivery(env, False)
    assert await d.deliver(7, "en", f, link) is None
    name, kw = env.bot.calls[0]
    assert name == "copy_message" and kw["from_chat_id"] == -100777 and kw["message_id"] == 42
    assert kw["protect_content"] is False and "File Ready" in kw["caption"]
    env.bot.calls.clear()
    env.bot.raise_on["copy_message"] = TelegramBadRequest("message to copy not found")
    assert await d.deliver(7, "en", f, link) is None
    assert env.bot.sent("send_video")                          # fell back to the stored file_id


async def test_blocked_user_is_not_retried_via_fallback(env):
    f, link = await make_file(env)
    f["store"] = {"chat_id": -1, "message_id": 1}
    env.bot.raise_on["copy_message"] = TelegramForbiddenError("bot was blocked by the user")
    assert await delivery(env, False).deliver(7, "en", f, link) == "err_delivery"
    assert env.bot.sent("send_video") == []


async def test_delivered_file_and_notice_are_scheduled_for_deletion(env):
    f, link = await make_file(env)
    assert (await env.repos.settings.get())["file_autodelete_seconds"] == -1   # auto by default
    await delivery(env).deliver(7, "en", f, link)
    rows = env.db["pending_deletes"].docs
    assert len(rows) == 2 and all(r["chat_id"] == 7 for r in rows)
    assert 290 < (rows[0]["due"] - datetime.now(timezone.utc)).total_seconds() <= 300
    assert "5 min" in env.bot.sent("send_message")[0][1]["args"][1]


async def test_file_autodelete_can_be_switched_off(env):
    await env.repos.settings.update(file_autodelete_seconds=0)
    f, link = await make_file(env)
    await delivery(env).deliver(7, "en", f, link)
    assert env.db["pending_deletes"].docs == [] and env.bot.sent("send_message") == []


async def test_failed_delivery_schedules_nothing(env):
    f, link = await make_file(env)
    env.bot.raise_on["send_video"] = TelegramBadRequest("wrong file identifier")
    assert await delivery(env).deliver(7, "en", f, link) == "err_unavailable"
    assert env.db["pending_deletes"].docs == []


async def test_lifecycle_deletes_many_concurrently_and_keeps_rows_on_flood_wait(env):
    lc = MessageLifecycle(env.bot, env.db["pending_deletes"], env.repos.settings)
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    for i in range(60):
        await env.db["pending_deletes"].insert_one({"chat_id": 1, "message_id": i, "due": past})
    assert await lc.run_once() == 60 and env.db["pending_deletes"].docs == []
    await env.db["pending_deletes"].insert_one({"chat_id": 1, "message_id": 99, "due": past})
    env.bot.raise_on["delete_message"] = TelegramRetryAfter("flood", retry_after=3)
    assert await lc.run_once() == 0 and len(env.db["pending_deletes"].docs) == 1  # retried next tick


# ---------------------------------------------------------------- DB channel copy
def file_msg(chat_id=1, mid=5, uid="dd1"):
    return NS(chat=NS(id=chat_id, type="private"), message_id=mid, forward_origin=None, caption=None,
              document=NS(file_id="d1", file_unique_id=uid, file_name="x.zip", file_size=5, mime_type="a/b"),
              video=None, audio=None, voice=None, animation=None, video_note=None, sticker=None, photo=None)


def ctx_of(env):
    return NS(repos=env.repos, files=env.files, links=env.links, bot=env.bot, i18n=env.i18n,
              lifecycle=NS(schedule=lambda *a, **k: asyncio.sleep(0)), cfg=NS(admin_ids=frozenset({1})))


async def test_file_sent_to_bot_is_copied_into_db_channel_and_stored(env):
    await env.repos.settings.update(db_channel={"id": -100500, "title": "DB", "admin": 1})
    m = file_msg()
    m.answer = lambda *a, **k: asyncio.sleep(0)
    await direct_save(m, ctx_of(env), lambda k, **kw: env.i18n.t("en", k, **kw))
    f = await env.repos.files.get_by_unique("dd1")
    assert f["store"]["chat_id"] == -100500 and env.bot.sent("copy_message")
    await direct_save(m, ctx_of(env), lambda k, **kw: env.i18n.t("en", k, **kw))  # same file again
    assert len(env.bot.sent("copy_message")) == 1                                   # not copied twice


async def test_copy_failure_still_saves_the_file_and_warns(env):
    await env.repos.settings.update(db_channel={"id": -100500, "title": "DB", "admin": 1})
    env.bot.raise_on["copy_message"] = TelegramBadRequest("not enough rights")
    m = file_msg(uid="dd2")
    m.answer = lambda *a, **k: asyncio.sleep(0)
    await direct_save(m, ctx_of(env), lambda k, **kw: env.i18n.t("en", k, **kw))
    f = await env.repos.files.get_by_unique("dd2")
    assert f is not None and not f.get("store")


async def test_db_channel_post_stores_its_own_message_as_delivery_source(env):
    await env.repos.settings.update(db_channel={"id": -100500, "title": "DB", "admin": 1}, db_reply=False)
    post = file_msg(chat_id=-100500, mid=77, uid="dd3")
    post.chat.type = "channel"
    await auto_import(post, ctx_of(env))
    assert (await env.repos.files.get_by_unique("dd3"))["store"] == {"chat_id": -100500, "message_id": 77}


# ---------------------------------------------------------------- concurrency middleware
async def test_different_users_run_in_parallel_but_same_user_is_serialised():
    mw = ConcurrencyMiddleware(max_concurrent=10, timeout=5)
    running, peak, order = [0], [0], []

    async def handler(e, d):
        running[0] += 1
        peak[0] = max(peak[0], running[0])
        order.append((d["event_from_user"].id, e))
        await asyncio.sleep(0.05)
        running[0] -= 1

    await asyncio.gather(*(mw(handler, i, {"event_from_user": NS(id=i)}) for i in range(5)))
    assert peak[0] == 5
    running[0], peak[0], order[:] = 0, 0, []
    await asyncio.gather(*(mw(handler, i, {"event_from_user": NS(id=9)}) for i in range(4)))
    assert peak[0] == 1 and [e for _, e in order] == [0, 1, 2, 3]
    assert mw._locks == {}                                      # no lock leak


async def test_a_hung_handler_is_cut_off_and_does_not_block_the_user_forever():
    mw = ConcurrencyMiddleware(timeout=0.05)

    async def hang(e, d):
        await asyncio.sleep(10)

    async def ok(e, d):
        return "ran"
    assert await mw(hang, 1, {"event_from_user": NS(id=1)}) is None
    assert await mw(ok, 1, {"event_from_user": NS(id=1)}) == "ran"


async def test_global_cap_limits_simultaneous_handlers():
    mw = ConcurrencyMiddleware(max_concurrent=2, timeout=5)
    running, peak = [0], [0]

    async def handler(e, d):
        running[0] += 1
        peak[0] = max(peak[0], running[0])
        await asyncio.sleep(0.03)
        running[0] -= 1
    await asyncio.gather(*(mw(handler, i, {"event_from_user": NS(id=i)}) for i in range(8)))
    assert peak[0] == 2
