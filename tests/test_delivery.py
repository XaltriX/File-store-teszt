from urllib.parse import parse_qs, urlparse

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError

from tests.conftest import make_file


async def deliver(env, f, link, lang="en"):
    return await env.delivery.deliver(7, lang, f, link)


async def test_sends_by_type_with_protect_and_caption(env):
    f, link = await make_file(env, ftype="video")
    await env.repos.files.set_protect(f["_id"], True)
    f = await env.repos.files.get(f["_id"])
    assert await deliver(env, f, link) is None
    name, kw = env.bot.sent("send_video")[0]
    assert kw["protect_content"] is True and "File Ready" in kw["caption"] and kw["video"] == f["file_id"]


async def test_protection_falls_back_to_global_default_and_file_override_wins(env):
    f, link = await make_file(env)
    await env.repos.settings.update(default_protect=True)
    assert await env.delivery.effective_protect(f) is True
    f["protect"] = False
    assert await env.delivery.effective_protect(f) is False


async def test_share_button_shares_original_permanent_link(env):
    f, link = await make_file(env)
    await deliver(env, f, link)
    kb = env.bot.sent("send_video")[0][1]["reply_markup"]
    url = kb.inline_keyboard[0][0].url
    shared = parse_qs(urlparse(url).query)["url"][0]
    assert url.startswith("https://t.me/share/url?url=") and shared == env.links.deep_link(link["token"])


async def test_sticker_has_no_caption(env):
    f, link = await make_file(env, "s1", ftype="sticker")
    await deliver(env, f, link)
    assert "caption" not in env.bot.sent("send_sticker")[0][1]


async def test_blocked_user_marks_user_and_reports_failure(env):
    f, link = await make_file(env)
    await env.repos.users.col.insert_one({"_id": 7, "blocked": False})
    env.bot.raise_on["send_video"] = TelegramForbiddenError("bot was blocked by the user")
    assert await deliver(env, f, link) == "err_delivery"
    assert (await env.repos.users.get(7))["blocked"] is True
    assert (await env.repos.stats.totals())["deliveries_failed"] == 1


async def test_missing_file_reference_maps_to_unavailable(env):
    f, link = await make_file(env)
    env.bot.raise_on["send_video"] = TelegramBadRequest("wrong file identifier")
    assert await deliver(env, f, link) == "err_unavailable"


async def test_transient_error_is_retried_then_succeeds(env, monkeypatch):
    import asyncio
    f, link = await make_file(env)
    env.bot.raise_on["send_video"] = [TelegramNetworkError("timeout"), None]
    orig = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_a, **_k: orig(0))
    assert await deliver(env, f, link) is None
    assert (await env.repos.stats.totals())["deliveries_ok"] == 1


async def test_unsupported_type_is_reported(env):
    f, link = await make_file(env)
    f["type"] = "weird"
    assert await deliver(env, f, link) == "err_unsupported"
