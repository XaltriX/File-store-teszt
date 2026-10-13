from types import SimpleNamespace as NS

from app.services.file_service import extract_file_info
from tests.conftest import make_file


async def test_file_record_persists_all_metadata(env):
    f, _ = await make_file(env, "x1", "movie.mp4")
    got = await env.repos.files.get(f["_id"])
    assert got["file_id"] == "F-x1" and got["type"] == "video" and got["status"] == "active"
    assert got["name"] == "movie.mp4" and got["created_at"] is not None


async def test_duplicate_upload_reuses_record_and_link(env):
    f1, l1 = await make_file(env, "dup")
    f2, created = await env.repos.files.create(file_id="OTHER", file_unique_id="dup", ftype="video", name="n",
                                               size=1, mime=None, caption=None)
    assert not created and f2["_id"] == f1["_id"]
    assert (await env.links.get_or_create(f1["_id"]))["token"] == l1["token"]
    assert await env.repos.files.count() == 1


async def test_archived_duplicate_is_reactivated(env):
    f, _ = await make_file(env, "re")
    await env.repos.files.set_status(f["_id"], "archived")
    f2, created = await env.repos.files.create(file_id="F", file_unique_id="re", ftype="video", name="n",
                                               size=1, mime=None, caption=None)
    assert not created and f2["status"] == "active"


def test_extract_picks_largest_photo_and_rejects_text():
    photo = NS(document=None, video=None, audio=None, voice=None, animation=None, video_note=None, sticker=None,
               photo=[NS(file_id="s", file_unique_id="us", file_size=1), NS(file_id="L", file_unique_id="uL", file_size=9)],
               caption="c")
    info = extract_file_info(photo)
    assert info["file_id"] == "L" and info["ftype"] == "photo"
    text = NS(document=None, video=None, audio=None, voice=None, animation=None, video_note=None, sticker=None,
              photo=None, caption=None)
    assert extract_file_info(text) is None
