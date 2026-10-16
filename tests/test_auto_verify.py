from tests.conftest import make_channel, make_file, member

U = 111


async def setup(env, mode="public", n=1, steps=1):
    f, link = await make_file(env)
    for i in range(n):
        await make_channel(env, -1001 - i, mode)
    await env.repos.settings.update(required_steps=steps)
    return f, link


async def test_already_joined_public_user_skips_card_entirely(env):
    f, link = await setup(env)
    member(env, -1001, U)
    step = await env.verification.start(U, link["token"])
    assert step.kind == "deliver"
    ch = await env.repos.channels.get(-1001)
    assert ch["joins_confirmed"] == 0 and ch["failed_checks"] == 0  # pre-existing member is not a join we caused


async def test_opening_link_as_non_member_shows_card_without_counting_a_failure(env):
    f, link = await setup(env)
    step = await env.verification.start(U, link["token"])
    assert step.kind == "prompt" and (await env.repos.channels.get(-1001))["failed_checks"] == 0


async def test_already_member_of_private_channel_skips_card(env):
    f, link = await setup(env, mode="request")
    member(env, -1001, U)
    assert (await env.verification.start(U, link["token"])).kind == "deliver"
    ch = await env.repos.channels.get(-1001)
    assert ch["joins_confirmed"] == 0 and ch["requests_received"] == 0  # not miscounted


async def test_join_request_sent_before_opening_link_auto_completes(env):
    f, link = await setup(env, mode="request")
    assert await env.verification.on_join_request(-1001, U, 42) == []  # no flow yet: just recorded
    assert (await env.verification.start(U, link["token"])).kind == "deliver"
    assert (await env.repos.channels.get(-1001))["requests_received"] == 1


async def test_chat_member_join_event_auto_delivers_and_is_idempotent(env):
    f, link = await setup(env)
    step = await env.verification.start(U, link["token"])
    assert step.kind == "prompt"
    steps = await env.verification.on_member_joined(-1001, U)
    assert [s.kind for s in steps] == ["deliver"]
    assert await env.verification.on_member_joined(-1001, U) == []
    assert (await env.repos.channels.get(-1001))["joins_confirmed"] == 1


async def test_chat_member_event_without_flow_or_for_unknown_channel_is_ignored(env):
    await setup(env)
    assert await env.verification.on_member_joined(-1001, 999) == []
    assert await env.verification.on_member_joined(-5, U) == []


async def test_join_request_on_public_mode_channel_with_approval_completes_step(env):
    f, link = await setup(env, mode="public")
    await env.verification.start(U, link["token"])
    steps = await env.verification.on_join_request(-1001, U, 7)
    assert [s.kind for s in steps] == ["deliver"]


async def test_auto_verify_advances_through_multiple_steps(env):
    f, link = await setup(env, n=2, steps=2)
    member(env, -1001, U)
    member(env, -1002, U)
    assert (await env.verification.start(U, link["token"])).kind == "deliver"


async def test_partially_satisfied_flow_shows_only_the_remaining_card(env):
    f, link = await setup(env, n=2, steps=2)
    member(env, -1001, U)
    member(env, -1002, U)
    env.bot.members.pop((-1002, U))
    step = await env.verification.start(U, link["token"])
    assert step.kind == "prompt" and step.channel["_id"] == -1002
    assert step.flow["channel_ids"] == [-1002]       # only the channel the user is NOT in is asked for
