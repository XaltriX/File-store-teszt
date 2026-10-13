from datetime import datetime, timezone
from types import SimpleNamespace as NS

from app.handlers.start import cmd_start
from app.services.delivery_service import DeliveryService
from app.services.file_service import extract_file_info
from app.utils.messaging import MessageLifecycle
from tests.conftest import make_channel, make_file


def delivery(env):
    return DeliveryService(env.bot, env.repos, env.i18n, env.links,
                           MessageLifecycle(env.bot, env.db["pending_deletes"], env.repos.settings))


def delays(env):
    now = datetime.now(timezone.utc)
    return sorted(round((r["due"] - now).total_seconds()) for r in env.db["pending_deletes"].docs)


# ------------------------------------------------------------ auto-delete = media length + 1 minute
async def test_two_minute_video_is_deleted_after_three_minutes(env):
    f, link = await make_file(env, "v2", duration=120)
    await delivery(env).deliver(7, "en", f, link)
    d = delays(env)
    assert len(d) == 2 and all(178 <= x <= 180 for x in d)
    assert "3 min" in env.bot.sent("send_message")[0][1]["args"][1]


async def test_each_file_gets_its_own_timer_from_its_own_length(env):
    d = delivery(env)
    for uid, dur, expect in (("a", 30, 90), ("b", 600, 660), ("c", 3600, 3660)):
        env.db["pending_deletes"].docs.clear()
        f, link = await make_file(env, uid, duration=dur)
        await d.deliver(7, "en", f, link)
        assert all(expect - 2 <= x <= expect for x in delays(env)), (uid, delays(env))


async def test_files_without_a_length_use_the_five_minute_fallback(env):
    f, link = await make_file(env, "doc", ftype="document")
    await delivery(env).deliver(7, "en", f, link)
    assert all(298 <= x <= 300 for x in delays(env))


async def test_admin_can_force_a_fixed_timer_or_turn_it_off(env):
    f, link = await make_file(env, "fx", duration=120)
    await env.repos.settings.update(file_autodelete_seconds=600)
    await delivery(env).deliver(7, "en", f, link)
    assert all(598 <= x <= 600 for x in delays(env))
    env.db["pending_deletes"].docs.clear()
    await env.repos.settings.update(file_autodelete_seconds=0)
    await delivery(env).deliver(7, "en", f, link)
    assert env.db["pending_deletes"].docs == []


def test_very_long_media_is_capped_and_formula_is_exact():
    assert DeliveryService.cleanup_delay(-1, {"duration": 120}) == 180
    assert DeliveryService.cleanup_delay(-1, {"duration": 999999}) == 24 * 3600
    assert DeliveryService.cleanup_delay(-1, {}) == 300 and DeliveryService.cleanup_delay(0, {"duration": 5}) == 0


def test_duration_is_read_from_video_audio_voice_animation_and_video_note():
    base = dict(document=None, video=None, audio=None, voice=None, animation=None, video_note=None,
                sticker=None, photo=None, caption=None)
    obj = lambda: NS(file_id="x", file_unique_id="u", file_size=1, mime_type="m", duration=95)  # noqa: E731
    for kind in ("video", "audio", "voice", "animation", "video_note"):
        assert extract_file_info(NS(**{**base, kind: obj()}))["duration"] == 95
    assert extract_file_info(NS(**{**base, "document": NS(file_id="x", file_unique_id="u", file_name="a", file_size=1, mime_type="m")}))["duration"] is None


async def test_duration_is_stored_with_the_file(env):
    f, _ = await make_file(env, "st", duration=77)
    assert (await env.repos.files.get(f["_id"]))["duration"] == 77


# ------------------------------------------------------------ language first
def start_ctx(env):
    return NS(repos=env.repos, bot=env.bot, i18n=env.i18n, verification=env.verification, delivery=env.delivery,
              welcome=None, lifecycle=NS(schedule=lambda *a, **k: None), cfg=NS(admin_ids=frozenset(), flow_ttl_seconds=1800))


class St:
    async def clear(self): pass


async def start_with(env, user, args):
    answered = []

    async def answer(text, reply_markup=None): answered.append((text, reply_markup))
    m = NS(chat=NS(id=5), from_user=NS(id=5, first_name="A", language_code="hi"), answer=answer)
    t = lambda k, **kw: env.i18n.t("en", k, **kw)  # noqa: E731
    await cmd_start(m, NS(args=args), St(), start_ctx(env), user, "en", t, await env.repos.settings.get())
    return answered


async def test_user_whose_language_was_only_auto_detected_is_asked_again_with_the_file_link(env):
    f, link = await make_file(env)
    out = await start_with(env, {"language": "en"}, link["token"])      # legacy: language set, never chosen
    assert len(out) == 1 and env.bot.calls == []
    assert all(b.callback_data.endswith(link["token"]) for r in out[0][1].inline_keyboard for b in r)


async def test_plain_start_without_a_chosen_language_asks_first(env):
    out = await start_with(env, {"language": None}, None)
    assert len(out) == 1 and len(out[0][1].inline_keyboard) == 4          # 7 languages, 2 per row


async def test_choosing_a_language_marks_it_as_chosen_for_good(env):
    await env.repos.users.touch(5, "A", None, "a")
    assert not (await env.repos.users.get(5)).get("lang_chosen")
    await env.repos.users.set_language(5, "te")
    doc = await env.repos.users.get(5)
    assert doc["language"] == "te" and doc["lang_chosen"] is True


# ------------------------------------------------------------ join-request statistics
OURS = "https://t.me/+abc"


async def req_channel(env):
    await make_channel(env, -1001, "request")
    await env.repos.channels.set(-1001, url=OURS)
    return env.verification


async def counts(env):
    c = await env.repos.channels.get(-1001)
    return c["requests_received"], c["requests_repeat"], c["requests_other"], (await env.repos.stats.totals())["private_requests"]


async def test_each_user_counts_once_even_if_they_request_several_times(env):
    v = await req_channel(env)
    for ts in (1, 2, 3):                       # same user cancels and re-requests
        await v.on_join_request(-1001, 7, ts, OURS)
    await v.on_join_request(-1001, 8, 1, OURS)
    assert await counts(env) == (2, 2, 0, 2)   # 2 unique users, 2 repeats


async def test_redelivered_event_is_not_counted_at_all(env):
    v = await req_channel(env)
    await v.on_join_request(-1001, 7, 1, OURS)
    await v.on_join_request(-1001, 7, 1, OURS)
    assert await counts(env) == (1, 0, 0, 1)


async def test_requests_through_other_or_old_links_are_separated_from_ours(env):
    v = await req_channel(env)
    await v.on_join_request(-1001, 1, 1, OURS)
    await v.on_join_request(-1001, 2, 1, "https://t.me/+zzzOTHER")
    await v.on_join_request(-1001, 3, 1, "https://t.me/+ab…")          # Telegram-shortened form of a link that starts like ours
    await v.on_join_request(-1001, 4, 1, None)                         # public username join: no link, counts as ours
    assert await counts(env) == (3, 0, 1, 3)


async def test_stats_still_complete_the_users_flow(env):
    v = await req_channel(env)
    f, link = await make_file(env)
    await v.start(7, link["token"])
    steps = await v.on_join_request(-1001, 7, 1, "https://t.me/+other")  # other link: not counted, still unlocks
    assert [s.kind for s in steps] == ["deliver"]
    assert await counts(env) == (0, 0, 1, 0)
