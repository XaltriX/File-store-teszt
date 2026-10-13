from datetime import datetime, timedelta, timezone

from aiogram.exceptions import TelegramBadRequest

from app.utils.messaging import MessageLifecycle


def lc(env):
    return MessageLifecycle(env.bot, env.db["pending_deletes"], env.repos.settings)


async def test_default_delay_is_60_seconds_and_zero_disables(env):
    l = lc(env)
    await l.schedule(1, 10)
    due = env.db["pending_deletes"].docs[0]["due"]
    assert 55 < (due - datetime.now(timezone.utc)).total_seconds() <= 60
    await env.repos.settings.update(autodelete_seconds=0)
    await l.schedule(1, 11)
    assert len(env.db["pending_deletes"].docs) == 1


async def test_only_due_messages_are_deleted(env):
    l = lc(env)
    col = env.db["pending_deletes"]
    now = datetime.now(timezone.utc)
    await col.insert_one({"chat_id": 1, "message_id": 1, "due": now - timedelta(seconds=1)})
    await col.insert_one({"chat_id": 1, "message_id": 2, "due": now + timedelta(seconds=100)})
    assert await l.run_once() == 1
    assert [c[1]["args"] for c in env.bot.sent("delete_message")] == [(1, 1)]
    assert len(col.docs) == 1


async def test_failed_deletion_never_raises_and_record_is_cleared(env):
    l = lc(env)
    await env.db["pending_deletes"].insert_one(
        {"chat_id": 1, "message_id": 1, "due": datetime.now(timezone.utc) - timedelta(seconds=1)})
    env.bot.raise_on["delete_message"] = TelegramBadRequest("message to delete not found")
    assert await l.run_once() == 1
    assert env.db["pending_deletes"].docs == []


async def test_pending_deletes_survive_restart_because_they_are_persisted(env):
    await lc(env).schedule(1, 5, delay=1)
    fresh = lc(env)  # new instance == restarted process, same DB
    env.db["pending_deletes"].docs[0]["due"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert await fresh.run_once() == 1
