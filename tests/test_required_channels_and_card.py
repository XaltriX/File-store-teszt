"""Required steps = how many channels the user must join for a file; one card shows them all."""
import random
from types import SimpleNamespace as NS

from app.handlers.admin.file_management import db_failure_key, direct_save, is_file_to_save
from app.handlers.start import cmd_id
from app.handlers.verification import present_step
from app.keyboards.user import verify_keyboard
from app.states.broadcast import BroadcastFlow
from app.states.file_upload import FileUpload
from tests.conftest import make_channel, make_file, member

U = 77


async def pool(env, n, steps):
    for i in range(n):
        await make_channel(env, -1001 - i)
    await env.repos.settings.update(required_steps=steps)


def join(env, ids):
    for c in ids:
        member(env, c, U)


async def test_six_channels_two_steps_every_file_asks_two_new_channels_then_free(env):
    await pool(env, 6, 2)
    files = [await make_file(env, f"f{i}", f"{i}.mp4") for i in range(4)]
    joined = set()
    for i in range(3):                                     # files 1-3: two NEW channels each
        env.repos.requests.reset_guards()
        step = await env.verification.start(U, files[i][1]["token"])
        ids = step.flow["channel_ids"]
        assert step.kind == "prompt" and len(ids) == 2 and len(step.channels) == 2
        assert not (set(ids) & joined)                     # never a channel the user is already in
        join(env, ids)
        assert (await env.verification.verify(step.flow["_id"], U)).kind == "deliver"
        joined |= set(ids)
    assert len(joined) == 6
    env.repos.requests.reset_guards()
    assert (await env.verification.start(U, files[3][1]["token"])).kind == "deliver"   # in all 6: file at once


async def test_one_step_means_one_channel_and_three_steps_three(env):
    for steps in (1, 3):
        env2 = env
        await pool(env2, 6, steps)
        f, link = await make_file(env2, f"s{steps}", f"{steps}.mp4")
        step = await env2.verification.start(1000 + steps, link["token"])
        assert len(step.flow["channel_ids"]) == steps and len(step.channels) == steps


async def test_fewer_missing_channels_than_steps_only_asks_for_the_missing_ones(env):
    await pool(env, 6, 3)
    join(env, [-1001, -1002, -1003, -1004, -1005])         # only -1006 left
    f, link = await make_file(env)
    step = await env.verification.start(U, link["token"])
    assert step.flow["channel_ids"] == [-1006]


async def test_channel_the_user_left_is_missing_again_for_the_next_file(env):
    await pool(env, 2, 1)
    join(env, [-1001, -1002])
    f, link = await make_file(env)
    assert (await env.verification.start(U, link["token"])).kind == "deliver"
    env.bot.members.pop((-1002, U))
    env.repos.requests.reset_guards()
    step = await env.verification.start(U, link["token"])
    assert step.kind == "prompt" and step.channel["_id"] == -1002


async def test_partial_progress_updates_the_same_card_and_counts_done(env):
    await pool(env, 3, 3)
    f, link = await make_file(env)
    step = await env.verification.start(U, link["token"])
    member(env, step.flow["channel_ids"][0], U)
    r = await env.verification.verify(step.flow["_id"], U)
    assert r.kind == "prompt" and len(r.channels) == 2     # one done, two still pending


# ---------------------------------------------------------------- card UI
def texts(markup):
    return [(b.text, b.url, b.callback_data) for row in markup.inline_keyboard for b in row]


def test_card_shows_all_channels_numbered_with_done_ones_ticked():
    t = lambda k: {"btn_join": "JOIN", "btn_request": "REQ", "btn_joined": "OK", "btn_verify": "V"}[k]  # noqa: E731
    flow = {"channel_ids": [1, 2, 3]}
    pending = [{"_id": 2, "mode": "public", "url": "u2"}, {"_id": 3, "mode": "request", "url": "u3"}]
    rows = texts(verify_keyboard(t, flow, pending, "F"))
    from app.utils.fancy import bold
    assert [r[0] for r in rows] == [bold("OK 1"), bold("JOIN 2"), bold("REQ 3"), bold("V")]
    assert rows[1][1] == "u2" and rows[2][1] == "u3" and rows[3][2] == "v:F" and rows[0][2] == "u:noop"


def test_single_channel_card_has_no_numbering():
    t = lambda k: {"btn_join": "JOIN", "btn_verify": "V"}[k]  # noqa: E731
    rows = texts(verify_keyboard(t, {"channel_ids": [1]}, [{"_id": 1, "mode": "public", "url": "u"}], "F"))
    from app.utils.fancy import bold
    assert rows[0][0] == bold("JOIN")


async def test_card_text_shows_progress_and_existing_card_is_edited_not_resent(env):
    await pool(env, 3, 3)
    f, link = await make_file(env)
    step = await env.verification.start(U, link["token"])
    ctx = NS(bot=env.bot, repos=env.repos, i18n=env.i18n, cfg=NS(flow_ttl_seconds=1800),
             lifecycle=NS(schedule=lambda *a, **k: __import__("asyncio").sleep(0)))
    await present_step(ctx, U, "en", step)                              # first time: a new card is sent
    assert len(env.bot.sent("send_message")) == 1
    assert "0/3" in env.bot.sent("send_message")[0][1]["args"][1]
    member(env, step.flow["channel_ids"][0], U)
    step2 = await env.verification.verify(step.flow["_id"], U)
    step2.flow = await env.repos.requests.get_flow(step.flow["_id"])    # message ids were stored on the flow
    await present_step(ctx, U, "en", step2)
    assert len(env.bot.sent("send_message")) == 1                       # no second message
    edit = env.bot.sent("edit_message_text")[0]
    assert "1/3" in edit[1]["args"][0]


# ---------------------------------------------------------------- DB channel: files sent to the bot
class St:
    def __init__(self, s): self.s = s
    async def get_state(self): return self.s


def file_msg(uid="g1"):
    return NS(chat=NS(id=1, type="private"), message_id=5, forward_origin=None, caption=None,
              document=NS(file_id="d", file_unique_id=uid, file_name="x.mp4", file_size=5, mime_type="video/mp4"),
              video=None, audio=None, voice=None, animation=None, video_note=None, sticker=None, photo=None)


async def test_a_file_is_saved_in_any_leftover_menu_state_except_those_that_expect_media():
    m = file_msg()
    for state in (None, FileUpload.search.state, FileUpload.duration.state):
        assert await is_file_to_save(m, St(state)) is True            # a leftover state can't swallow it
    for state in (BroadcastFlow.message.state, FileUpload.dbch.state):
        assert await is_file_to_save(m, St(state)) is False           # these consume the media themselves
    text_only = file_msg()
    text_only.document = None
    assert await is_file_to_save(text_only, St(None)) is False


async def test_confirmation_always_says_what_happened_with_the_db_channel(env):
    from aiogram.exceptions import TelegramBadRequest
    ctx = NS(repos=env.repos, files=env.files, links=env.links, bot=env.bot, i18n=env.i18n,
             lifecycle=NS(schedule=lambda *a, **k: None), cfg=NS(admin_ids=frozenset({1})))
    t = lambda k, **kw: env.i18n.t("en", k, **kw)  # noqa: E731
    sent = []

    async def answer(text, reply_markup=None): sent.append(text)

    async def go(uid):
        m = file_msg(uid)
        m.answer = answer
        await direct_save(m, ctx, t)
        return sent[-1]
    assert "not set" in await go("a1")                                   # no DB channel configured
    await env.repos.settings.update(db_channel={"id": -100500, "title": "DB", "admin": 1})
    assert "✅ copied" in await go("a2")
    env.bot.raise_on["copy_message"] = TelegramBadRequest("not enough rights to send messages")
    assert "Post messages" in await go("a3")
    env.bot.raise_on["copy_message"] = TelegramBadRequest("Bad Request: chat not found")
    assert "isn't in that channel" in await go("a4")
    assert "already stored" in await go("a2")                            # not copied twice
    assert db_failure_key(TelegramBadRequest("whatever")) == "a_db_fail"


async def test_id_command_tells_the_user_whether_they_are_in_admin_ids(env):
    out = []

    async def answer(text, **k): out.append(text)
    ctx = NS(cfg=NS(admin_ids=frozenset({1})))
    await cmd_id(NS(from_user=NS(id=1), answer=answer), ctx)
    await cmd_id(NS(from_user=NS(id=2), answer=answer), ctx)
    assert "admin" in out[0] and "not in ADMIN_IDS" in out[1]


async def test_unknown_length_video_is_sent_by_file_id_once_and_its_length_is_learned(env):
    from app.services.delivery_service import DeliveryService
    from app.utils.messaging import MessageLifecycle
    f, link = await make_file(env, "lv")
    f["store"] = {"chat_id": -100777, "message_id": 42}
    env.bot.reply_duration = 120
    d = DeliveryService(env.bot, env.repos, env.i18n, env.links,
                        MessageLifecycle(env.bot, env.db["pending_deletes"], env.repos.settings))
    await d.deliver(7, "en", f, link)
    assert env.bot.sent("send_video") and not env.bot.sent("copy_message")   # learning delivery
    assert (await env.repos.files.get(f["_id"]))["duration"] == 120
    rows = env.db["pending_deletes"].docs
    assert len(rows) == 2 and "3 min" in env.bot.sent("send_message")[0][1]["args"][1]   # this very delivery: 120 s + 1 min
    env.bot.calls.clear()
    f2 = await env.repos.files.get(f["_id"])
    f2["store"] = {"chat_id": -100777, "message_id": 42}
    await d.deliver(7, "en", f2, link)
    assert env.bot.sent("copy_message") and not env.bot.sent("send_video")   # next time: from the DB copy
