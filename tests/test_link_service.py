from datetime import datetime, timedelta, timezone

from tests.conftest import make_file


async def test_deep_link_format_and_resolution(env):
    f, link = await make_file(env)
    assert env.links.deep_link(link["token"]) == f"https://t.me/testbot?start={link['token']}"
    res = await env.links.resolve(link["token"])
    assert res.status == "ok" and res.file["_id"] == f["_id"]


async def test_invalid_and_unknown_tokens(env):
    assert (await env.links.resolve("nope")).status == "invalid"
    assert (await env.links.resolve(None)).status == "invalid"
    assert (await env.links.resolve("A" * 22)).status == "invalid"  # well-formed but unknown


async def test_revoked_link_only_affects_that_token(env):
    f, l1 = await make_file(env)
    l2 = await env.links.create(f["_id"])
    assert await env.repos.links.revoke(l1["token"])
    assert (await env.links.resolve(l1["token"])).status == "revoked"
    assert (await env.links.resolve(l2["token"])).status == "ok"


async def test_archived_file_is_unavailable(env):
    f, l = await make_file(env)
    await env.repos.files.set_status(f["_id"], "archived")
    assert (await env.links.resolve(l["token"])).status == "unavailable"


async def test_expired_link_is_rejected(env):
    f, _ = await make_file(env)
    l = await env.repos.links.create(f["_id"], expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert (await env.links.resolve(l["token"])).status == "revoked"
    assert (await env.links.resolve(l["token"])).file is None
