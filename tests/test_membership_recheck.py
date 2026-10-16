"""Regression: a user who LEFT a channel must not be treated as a member on the next file request."""
from tests.conftest import make_channel, make_file, member

U = 111


def reopen(env):
    env.repos.requests.reset_guards()  # the 2s duplicate-/start window elapsed


async def first_delivery(env, mode="public", extra_channels=0):
    f, link = await make_file(env)
    await make_channel(env, -1001, mode)
    for i in range(extra_channels):
        await make_channel(env, -2000 - i, mode, presented=0)
    await env.repos.channels.set(-1001, presented=0)
    for i in range(extra_channels):
        await env.repos.channels.set(-2000 - i, presented=5)  # -1001 is the least presented
    step = await env.verification.start(U, link["token"])
    assert step.kind == "prompt" and step.channel["_id"] == -1001
    member(env, -1001, U)
    assert (await env.verification.verify(step.flow["_id"], U)).kind == "deliver"
    return f, link


async def test_still_member_gets_file_instantly_on_second_request_without_new_card(env):
    f, link = await first_delivery(env)
    reopen(env)
    f2, link2 = await make_file(env, "u2", "b.mp4")
    assert (await env.verification.start(U, link2["token"])).kind == "deliver"
    assert (await env.repos.channels.get(-1001))["presented"] == 1  # same channel, not re-counted


async def test_user_who_left_is_asked_again_for_the_same_channel(env):
    f, link = await first_delivery(env, extra_channels=2)
    env.bot.members.pop((-1001, U))  # user leaves
    reopen(env)
    step = await env.verification.start(U, link["token"])
    assert step.kind == "prompt" and step.channel["_id"] == -1001  # sticky: rejoin the one they left
    r = await env.verification.verify(step.flow["_id"], U)
    assert r.kind == "retry" and r.key == "verify_not_member"
    member(env, -1001, U)  # rejoins
    assert (await env.verification.verify(step.flow["_id"], U)).kind == "deliver"


async def test_same_file_again_after_leaving_is_not_delivered_for_free(env):
    f, link = await first_delivery(env)
    env.bot.members.pop((-1001, U))
    reopen(env)
    assert (await env.verification.start(U, link["token"])).kind == "prompt"


async def test_private_channel_old_join_request_does_not_count_after_user_joined_and_left(env):
    f, link = await make_file(env)
    await make_channel(env, -1001, "request")
    step = await env.verification.start(U, link["token"])
    steps = await env.verification.on_join_request(-1001, U, 100)
    assert steps[0].kind == "deliver"
    member(env, -1001, U)                       # admin approved
    await env.verification.on_member_joined(-1001, U)  # request consumed by membership
    env.bot.members.pop((-1001, U))             # user leaves
    reopen(env)
    again = await env.verification.start(U, link["token"])
    assert again.kind == "prompt"
    r = await env.verification.verify(again.flow["_id"], U)
    assert r.key == "verify_request_missing"    # the old request must not be reused
    assert (await env.verification.on_join_request(-1001, U, 200))[0].kind == "deliver"  # a NEW request works


async def test_member_check_consumes_stored_request_even_without_chat_member_update(env):
    f, link = await make_file(env)
    await make_channel(env, -1001, "request")
    await env.verification.on_join_request(-1001, U, 1)   # request before any flow
    member(env, -1001, U)
    assert (await env.verification.start(U, link["token"])).kind == "deliver"
    env.bot.members.pop((-1001, U))
    reopen(env)
    f2, l2 = await make_file(env, "u9", "c.mp4")
    assert (await env.verification.start(U, l2["token"])).kind == "prompt"


async def test_still_pending_request_keeps_counting_for_later_files(env):
    f, link = await make_file(env)
    await make_channel(env, -1001, "request")
    await env.verification.start(U, link["token"])
    await env.verification.on_join_request(-1001, U, 1)   # pending, never approved
    reopen(env)
    f2, l2 = await make_file(env, "u3", "d.mp4")
    assert (await env.verification.start(U, l2["token"])).kind == "deliver"  # user can't re-request while pending


