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
    return c["requests_received"], c["requests_other"], (await env.repos.stats.totals())["private_requests"]


async def test_every_distinct_request_through_our_link_counts_once_like_the_links_own_counter(env):
    v = await req_channel(env)
    for ts in (1, 2, 3):                       # same user cancels and re-requests: three real requests
        await v.on_join_request(-1001, 7, ts, OURS)
    await v.on_join_request(-1001, 8, 1, OURS)
    assert await counts(env) == (4, 0, 4)


async def test_redelivered_event_is_not_counted_at_all(env):
    v = await req_channel(env)
    await v.on_join_request(-1001, 7, 1, OURS)
    await v.on_join_request(-1001, 7, 1, OURS)
    assert await counts(env) == (1, 0, 1)


async def test_requests_through_other_or_old_links_are_separated_from_ours(env):
    v = await req_channel(env)
    await v.on_join_request(-1001, 1, 1, OURS)
    await v.on_join_request(-1001, 2, 1, "https://t.me/+zzzOTHER")
    await v.on_join_request(-1001, 3, 1, "https://t.me/+ab…")          # Telegram-shortened form of a link that starts like ours
    await v.on_join_request(-1001, 4, 1, None)                         # public username join: no link, counts as ours
    assert await counts(env) == (3, 1, 3)


async def test_other_link_request_still_unlocks_the_users_file(env):
    v = await req_channel(env)
    f, link = await make_file(env)
    await v.start(7, link["token"])
    steps = await v.on_join_request(-1001, 7, 1, "https://t.me/+other")
    assert [s.kind for s in steps] == ["deliver"]
    assert await counts(env) == (0, 1, 0)


# ------------------------------------------------------------ simple per-channel stats: today + total
async def test_channel_today_and_total_joins_leaves_and_requests(env):
    await make_channel(env, -1001, "public")
    v = env.verification
    for uid in (1, 2, 3):
        await v.record_membership_change(-1001, uid, joined=True)
    await v.record_membership_change(-1001, 2, joined=False)
    await v.record_membership_change(-1001, 99, joined=True, is_bot=True)         # bots are never counted
    await v.record_membership_change(-5, 1, joined=True)                          # not a pool channel
    c = await env.repos.channels.get(-1001)
    day = (await env.repos.stats.channel_days([-1001]))[-1001]
    assert (c["joins_total"], c["leaves_total"]) == (3, 1) and (day["joins"], day["leaves"]) == (3, 1)


async def test_private_channel_requests_today_and_total(env):
    v = await req_channel(env)
    for uid in (1, 2, 3, 4, 5):
        await v.on_join_request(-1001, uid, 1, OURS)
    day = (await env.repos.stats.channel_days([-1001]))[-1001]
    assert day["requests"] == 5 and (await env.repos.channels.get(-1001))["requests_received"] == 5


def test_today_follows_india_time_not_utc():
    from datetime import datetime, timezone
    from app.utils.clock import today
    assert today(datetime(2026, 10, 15, 18, 29, tzinfo=timezone.utc)) == "2026-10-15"   # 23:59 IST
    assert today(datetime(2026, 10, 15, 18, 31, tzinfo=timezone.utc)) == "2026-10-16"   # 00:01 IST next day


async def test_reset_zeroes_channel_numbers_but_keeps_rotation(env):
    v = await req_channel(env)
    await v.on_join_request(-1001, 1, 1, OURS)
    await v.record_membership_change(-1001, 1, joined=True)
    await env.repos.channels.inc(-1001, "presented", 3)
    await env.repos.channels.reset_stats()
    await env.repos.stats.reset_channel_days()
    c = await env.repos.channels.get(-1001)
    assert (c["requests_received"], c["joins_total"], c["leaves_total"]) == (0, 0, 0) and c["presented"] == 3
    assert await env.repos.stats.channel_days([-1001]) == {}


async def test_global_today_block_counts_new_users_requests_and_deliveries(env):
    await env.repos.users.touch(1, "A", None, "a")
    await env.repos.stats.inc_new_user()
    await env.repos.stats.inc("requests")
    await env.repos.stats.inc("requests")
    await env.repos.stats.inc("deliveries_ok")
    t = await env.repos.stats.today_totals()
    assert (t["new_users"], t["requests"], t["deliveries_ok"]) == (1, 2, 1)


async def test_chat_member_handler_counts_real_joins_and_leaves_only(env):
    from app.handlers.verification import on_chat_member
    await make_channel(env, -1001, "public")
    ctx = NS(verification=env.verification, repos=env.repos, i18n=env.i18n, bot=env.bot, delivery=env.delivery,
             cfg=NS(flow_ttl_seconds=1800), lifecycle=NS(schedule=lambda *a, **k: None))

    def upd(old, new, uid=5, bot=False):
        mk = lambda st: NS(status=st, user=NS(id=uid, is_bot=bot))  # noqa: E731
        return NS(chat=NS(id=-1001), old_chat_member=mk(old), new_chat_member=mk(new))
    await on_chat_member(upd("left", "member"), ctx)
    await on_chat_member(upd("member", "administrator"), ctx)    # promotion: not a join
    await on_chat_member(upd("member", "left"), ctx)
    await on_chat_member(upd("left", "kicked"), ctx)             # never a member: not a leave
    await on_chat_member(upd("left", "member", uid=9, bot=True), ctx)
    c = await env.repos.channels.get(-1001)
    assert (c["joins_total"], c["leaves_total"]) == (1, 1)
