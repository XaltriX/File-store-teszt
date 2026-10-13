from types import SimpleNamespace as NS

from app.handlers.admin.file_management import direct_save
from app.handlers.files import auto_import
from app.services.welcome_service import WelcomeService


def doc_msg(chat_id=-100500, mid=9, uid="du1", name="x.zip"):
    return NS(chat=NS(id=chat_id, type="channel"), message_id=mid, forward_origin=None, caption=None,
              document=NS(file_id="d1", file_unique_id=uid, file_name=name, file_size=5, mime_type="a/b"),
              video=None, audio=None, voice=None, animation=None, video_note=None, sticker=None, photo=None)


def ctx_of(env):
    return NS(repos=env.repos, files=env.files, links=env.links, bot=env.bot, i18n=env.i18n,
              cfg=NS(admin_ids=frozenset({1})))


# ---- welcome media
async def test_welcome_media_dispatches_by_type(env):
    w = WelcomeService(env.bot, env.repos, env.i18n)
    for typ, method in (("video", "send_video"), ("animation", "send_animation"), ("photo", "send_photo")):
        env.bot.calls.clear()
        await w.send(5, "en", "Ann", {**await env.repos.settings.get(), "welcome_media": {"type": typ, "file_id": "M"}})
        name, kw = env.bot.calls[0]
        assert name == method and kw[typ] == "M" and "Ann" in kw["caption"]


async def test_legacy_welcome_image_still_works_and_no_media_sends_text(env):
    w = WelcomeService(env.bot, env.repos, env.i18n)
    s = await env.repos.settings.get()
    await w.send(5, "en", "A", {**s, "welcome_image": "OLD"})
    assert env.bot.calls[0][0] == "send_photo"
    env.bot.calls.clear()
    await w.send(5, "en", "A", s)
    assert env.bot.calls[0][0] == "send_message"


async def test_custom_welcome_text_is_escaped_and_personalised(env):
    w = WelcomeService(env.bot, env.repos, env.i18n)
    s = {**await env.repos.settings.get(), "welcome_text": "Hi <script>\nBye {first_name}"}
    out = w.render_text("en", s, "A&B")
    assert "<script>" not in out and "&lt;script&gt;" in out and "A&amp;B" in out and out.startswith("<b>")


# ---- DB channel
async def test_db_channel_post_is_saved_dm_sent_and_reply_posted(env):
    await env.repos.settings.update(db_channel={"id": -100500, "title": "DB", "admin": 77}, db_reply=True)
    await auto_import(doc_msg(), ctx_of(env))
    assert await env.repos.files.count() == 1
    dm = env.bot.sent("send_message")
    assert dm[0][1]["args"][0] == 77 and "https://t.me/testbot?start=" in dm[0][1]["args"][1]
    assert dm[1][1]["args"][0] == -100500 and dm[1][1]["reply_to_message_id"] == 9


async def test_db_reply_can_be_switched_off(env):
    await env.repos.settings.update(db_channel={"id": -100500, "title": "DB", "admin": 77}, db_reply=False)
    await auto_import(doc_msg(), ctx_of(env))
    assert [c[1]["args"][0] for c in env.bot.sent("send_message")] == [77]


async def test_other_channels_and_non_file_posts_are_ignored(env):
    await env.repos.settings.update(db_channel={"id": -100500, "title": "DB", "admin": 77})
    await auto_import(doc_msg(chat_id=-1), ctx_of(env))
    text_only = doc_msg()
    text_only.document = None
    await auto_import(text_only, ctx_of(env))
    assert await env.repos.files.count() == 0 and env.bot.sent("send_message") == []


async def test_duplicate_db_post_reuses_file_and_link(env):
    await env.repos.settings.update(db_channel={"id": -100500, "title": "DB", "admin": 77})
    await auto_import(doc_msg(mid=1), ctx_of(env))
    await auto_import(doc_msg(mid=2), ctx_of(env))
    assert await env.repos.files.count() == 1 and await env.repos.links.count_active() == 1


async def test_watched_source_imports_silently(env):
    await env.repos.settings.update(import_sources=[-100500])
    await auto_import(doc_msg(), ctx_of(env))
    assert await env.repos.files.count() == 1 and env.bot.sent("send_message") == []


# ---- admin sends a file directly
async def test_admin_direct_file_is_saved_and_link_returned(env):
    sent = []

    async def answer(text, reply_markup=None):
        sent.append((text, reply_markup))
    m = doc_msg(chat_id=1)
    m.chat.type = "private"
    m.answer = answer
    await direct_save(m, ctx_of(env), lambda k, **kw: env.i18n.t("en", k, **kw))
    assert await env.repos.files.count() == 1 and "https://t.me/testbot?start=" in sent[0][0]


async def test_admin_plain_text_is_ignored_by_direct_save(env):
    m = doc_msg(chat_id=1)
    m.document = None
    m.answer = None
    await direct_save(m, ctx_of(env), lambda k, **kw: k)
    assert await env.repos.files.count() == 0
