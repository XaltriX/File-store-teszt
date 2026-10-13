import pytest

from tests.conftest import make_channel, make_file, member

U = 111


async def setup_basic(env, mode="public", n_channels=1, steps=1):
    f, link = await make_file(env)
    for i in range(n_channels):
        await make_channel(env, -1001 - i, mode)
    await env.repos.settings.update(required_steps=steps)
    return f, link


async def test_no_channels_means_direct_delivery(env):
    f, link = await make_file(env)
    step = await env.verification.start(U, link["token"])
    assert step.kind == "deliver" and step.file["_id"] == f["_id"]


async def test_invalid_and_revoked_links(env):
    assert (await env.verification.start(U, "bad")).key == "err_invalid"
    f, link = await make_file(env)
    await env.repos.links.revoke(link["token"])
    assert (await env.verification.start(U, link["token"])).key == "err_revoked"


async def test_public_verify_requires_real_membership(env):
    f, link = await setup_basic(env)
    step = await env.verification.start(U, link["token"])
    assert step.kind == "prompt"
    fid = step.flow["_id"]
    r = await env.verification.verify(fid, U)  # not a member -> never completes on a click alone
    assert r.kind == "retry" and r.key == "verify_not_member"
    assert (await env.repos.channels.get(-1001))["failed_checks"] == 1
    member(env, -1001, U)
    r = await env.verification.verify(fid, U)
    assert r.kind == "deliver"
    ch = await env.repos.channels.get(-1001)
    assert ch["joins_confirmed"] == 1 and ch["requests_received"] == 0
    assert (await env.repos.stats.totals())["public_confirmed"] == 1


async def test_api_error_never_counts_as_success(env):
    from aiogram.exceptions import TelegramBadRequest
    f, link = await setup_basic(env)
    step = await env.verification.start(U, link["token"])
    env.bot.raise_on["get_chat_member"] = TelegramBadRequest("chat not found")
    r = await env.verification.verify(step.flow["_id"], U)
    assert r.kind == "retry" and r.key == "verify_api_error"
    flow = await env.repos.requests.get_flow(step.flow["_id"])
    assert flow["done"] == [] and flow["status"] == "active"


async def test_flow_resumes_with_same_channel_and_presented_counted_once(env):
    f, link = await setup_basic(env, n_channels=3)
    s1 = await env.verification.start(U, link["token"])
    env.db["guards"].docs.clear()  # simulate the 2s duplicate-/start window elapsing
    s2 = await env.verification.start(U, link["token"])
    assert s1.channel["_id"] == s2.channel["_id"]
    presented = [c["presented"] for c in await env.repos.channels.list()]
    assert sorted(presented) == [0, 0, 1]


async def test_duplicate_start_within_window_is_noop(env):
    f, link = await setup_basic(env)
    await env.verification.start(U, link["token"])
    assert (await env.verification.start(U, link["token"])).kind == "noop"


async def test_balancing_prefers_least_presented_across_users(env):
    f, link = await make_file(env)
    await make_channel(env, -1, presented=5)
    await make_channel(env, -2, presented=0)
    step = await env.verification.start(U, link["token"])
    assert step.channel["_id"] == -2


async def test_multiple_steps_use_distinct_channels_and_deliver_only_after_all(env):
    f, link = await setup_basic(env, n_channels=3, steps=2)
    step = await env.verification.start(U, link["token"])
    flow = step.flow
    assert len(set(flow["channel_ids"])) == 2
    for cid in flow["channel_ids"]:
        member(env, cid, U)
    r = await env.verification.verify(flow["_id"], U)
    assert r.kind == "deliver"
    assert (await env.repos.requests.get_flow(flow["_id"]))["status"] == "completed"


async def test_two_steps_second_prompt_shown_after_first(env):
    f, link = await setup_basic(env, n_channels=2, steps=2)
    step = await env.verification.start(U, link["token"])
    first = step.channel["_id"]
    member(env, first, U)
    r = await env.verification.verify(step.flow["_id"], U)
    assert r.kind == "prompt" and r.channel["_id"] != first


async def test_repeated_verify_callbacks_deliver_once(env):
    f, link = await setup_basic(env)
    step = await env.verification.start(U, link["token"])
    member(env, -1001, U)
    results = [await env.verification.verify(step.flow["_id"], U) for _ in range(4)]
    assert [r.kind for r in results].count("deliver") == 1
    assert all(r.kind == "noop" for r in results[1:])


async def test_verify_ownership_check(env):
    f, link = await setup_basic(env)
    step = await env.verification.start(U, link["token"])
    r = await env.verification.verify(step.flow["_id"], 999)
    assert r.kind == "error" and r.key == "err_flow_expired"
    assert (await env.verification.verify("missing", U)).key == "err_flow_expired"


# ---- private join-request channels
async def test_private_requires_actual_request_event_and_does_not_count_as_join(env):
    f, link = await setup_basic(env, mode="request")
    step = await env.verification.start(U, link["token"])
    r = await env.verification.verify(step.flow["_id"], U)
    assert r.kind == "retry" and r.key == "verify_request_missing"
    steps = await env.verification.on_join_request(-1001, U, 1700000000)
    assert len(steps) == 1 and steps[0].kind == "deliver"  # automatic delivery, no /start needed
    ch = await env.repos.channels.get(-1001)
    assert ch["requests_received"] == 1 and ch["joins_confirmed"] == 0
    totals = await env.repos.stats.totals()
    assert totals["private_requests"] == 1 and totals["public_confirmed"] == 0


async def test_duplicate_join_request_events_are_idempotent(env):
    f, link = await setup_basic(env, mode="request")
    await env.verification.start(U, link["token"])
    a = await env.verification.on_join_request(-1001, U, 1700000000)
    b = await env.verification.on_join_request(-1001, U, 1700000000)
    assert len(a) == 1 and b == []
    assert (await env.repos.channels.get(-1001))["requests_received"] == 1
    assert [s.kind for s in a] == ["deliver"]


async def test_join_request_for_other_user_or_unknown_channel_is_ignored(env):
    f, link = await setup_basic(env, mode="request")
    step = await env.verification.start(U, link["token"])
    assert await env.verification.on_join_request(-1001, 222, 1) == []       # other user: no flow
    assert await env.verification.on_join_request(-9999, U, 1) == []         # not in pool
    assert (await env.repos.requests.get_flow(step.flow["_id"]))["done"] == []


async def test_join_request_never_approves_or_declines(env):
    f, link = await setup_basic(env, mode="request")
    await env.verification.start(U, link["token"])
    await env.verification.on_join_request(-1001, U, 5)
    assert env.bot.sent("approve_chat_join_request") == [] and env.bot.sent("decline_chat_join_request") == []


async def test_removed_channel_does_not_strand_active_flow(env):
    f, link = await setup_basic(env)
    step = await env.verification.start(U, link["token"])
    await env.repos.channels.soft_remove(-1001)
    r = await env.verification.verify(step.flow["_id"], U)
    assert r.kind == "deliver"
    assert (await env.repos.channels.get(-1001))["presented"] == 1  # stats preserved


async def test_completed_flow_allows_reaccess_via_same_link(env):
    f, link = await setup_basic(env)
    step = await env.verification.start(U, link["token"])
    member(env, -1001, U)
    await env.verification.verify(step.flow["_id"], U)
    env.db["guards"].docs.clear()
    again = await env.verification.start(U, link["token"])
    assert again.kind == "deliver" and again.flow is None
    assert (await env.repos.channels.get(-1001))["presented"] == 1  # no new presentation


async def test_reopen_after_delivery_failure_allows_retry(env):
    f, link = await setup_basic(env)
    step = await env.verification.start(U, link["token"])
    member(env, -1001, U)
    r = await env.verification.verify(step.flow["_id"], U)
    await env.verification.reopen(r.flow["_id"])
    r2 = await env.verification.verify(step.flow["_id"], U)
    assert r2.kind == "deliver"
