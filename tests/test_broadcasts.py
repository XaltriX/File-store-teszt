import asyncio

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError


async def add_users(env, n):
    for i in range(1, n + 1):
        await env.repos.users.col.insert_one({"_id": i, "blocked": False})


async def run_job(env, n_users=7):
    await add_users(env, n_users)
    job = await env.broadcast.create(1, 1, 10, "")
    await env.broadcast._run(job["_id"])
    return await env.repos.broadcasts.get(job["_id"])


async def test_broadcast_delivers_to_all_with_bounded_batches(env):
    job = await run_job(env, 7)
    assert job["status"] == "completed" and job["success"] == 7 and job["target"] == 7
    assert len(env.bot.sent("copy_message")) == 7
    assert job["failed"] == 0 and job["blocked"] == 0


async def test_blocked_users_are_recorded_not_retried(env):
    await add_users(env, 3)
    env.bot.raise_on["copy_message"] = [None, TelegramForbiddenError("bot was blocked by the user"), None]
    job = await env.broadcast.create(1, 1, 10, "")
    await env.broadcast._run(job["_id"])
    j = await env.repos.broadcasts.get(job["_id"])
    assert (j["success"], j["blocked"], j["failed"]) == (2, 1, 0)
    assert len(env.bot.sent("copy_message")) == 2  # blocked attempt not retried
    blocked = [u for u in env.repos.users.col.docs if u["blocked"]]
    assert len(blocked) == 1


async def test_retryable_errors_are_retried_then_succeed(env):
    await add_users(env, 1)
    env.bot.raise_on["copy_message"] = [TelegramNetworkError("t"), TelegramNetworkError("t"), None]
    job = await env.broadcast.create(1, 1, 10, "")
    await env.broadcast._run(job["_id"])
    assert (await env.repos.broadcasts.get(job["_id"]))["success"] == 1


async def test_retries_are_bounded_and_end_as_failure(env):
    await add_users(env, 1)
    env.bot.raise_on["copy_message"] = TelegramNetworkError("down")
    job = await env.broadcast.create(1, 1, 10, "")
    await env.broadcast._run(job["_id"])
    j = await env.repos.broadcasts.get(job["_id"])
    assert j["failed"] == 1 and j["status"] == "completed"


async def test_permanent_error_fails_immediately(env):
    await add_users(env, 1)
    env.bot.raise_on["copy_message"] = TelegramBadRequest("message to copy not found")
    job = await env.broadcast.create(1, 1, 10, "")
    await env.broadcast._run(job["_id"])
    assert (await env.repos.broadcasts.get(job["_id"]))["failed"] == 1


async def test_resume_does_not_resend_to_already_delivered_users(env):
    await add_users(env, 6)
    job = await env.broadcast.create(1, 1, 10, "")
    for uid in (1, 2, 3):
        await env.repos.broadcasts.record(job["_id"], uid, "success")
    await env.repos.broadcasts.set_status(job["_id"], "interrupted")
    await env.broadcast._run(job["_id"])
    j = await env.repos.broadcasts.get(job["_id"])
    assert j["success"] == 6 and len(env.bot.sent("copy_message")) == 3
    assert await env.repos.broadcasts.record(job["_id"], 1, "success") is False  # idempotent


async def test_cancel_stops_job(env):
    await add_users(env, 30)
    job = await env.broadcast.create(1, 1, 10, "")
    await env.broadcast.cancel(job["_id"])
    await env.broadcast._run(job["_id"])
    j = await env.repos.broadcasts.get(job["_id"])
    assert j["status"] == "cancelled" and j["success"] == 0


async def test_blocked_users_excluded_from_later_audience(env):
    await add_users(env, 3)
    await env.repos.users.mark_blocked(2)
    assert await env.repos.users.count_audience() == 2
    assert await env.repos.users.iter_audience_ids(0, 10) == [1, 3]


async def test_progress_notifier_called_with_final_flag(env):
    await add_users(env, 2)
    seen = []

    async def notify(job, final):
        seen.append((job["status"], final))
    env.broadcast.notify = notify
    job = await env.broadcast.create(1, 1, 10, "")
    await env.broadcast._run(job["_id"])
    assert seen[-1] == ("completed", True)
