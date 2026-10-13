from types import SimpleNamespace as NS

from app.handlers.admin.file_management import direct_save
from app.handlers.admin.common import show
from app.handlers.admin.settings import got_value
from app.handlers.files import auto_import
from app.handlers.start import cmd_start
from app.keyboards.admin import files_menu
from app.keyboards.user import welcome_keyboard
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


# ---- welcome media must survive /start (regression: preview shown once, then gone)
class FakeState:
    def __init__(self, data):
        self.data = dict(data)
        self.cleared = False

    async def get_data(self): return dict(self.data)
    async def update_data(self, **kw): self.data.update(kw)
    async def clear(self): self.cleared, self.data = True, {}


async def _noop(*a, **k): return None


async def test_welcome_video_is_saved_immediately_and_shown_on_next_start(env):
    ctx = NS(repos=env.repos, bot=env.bot, welcome=WelcomeService(env.bot, env.repos, env.i18n),
             lifecycle=NS(schedule=_noop), cfg=NS(admin_ids=frozenset({1})))
    msg = NS(chat=NS(id=1), from_user=NS(id=1, first_name="Admin"), text=None, photo=None, animation=None,
             video=NS(file_id="VID1"), delete=_noop)
    state = FakeState({"field": "welcome_media", "pc": 1, "pm": 2})
    t = lambda k, **kw: env.i18n.t("en", k, **kw)  # noqa: E731
    await got_value(msg, state, ctx, t, "en", await env.repos.settings.get())
    assert (await env.repos.settings.get())["welcome_media"] == {"type": "video", "file_id": "VID1"}
    assert state.cleared  # nothing left pending, so a /start can't lose it
    env.bot.calls.clear()

    start_msg = NS(chat=NS(id=5), from_user=NS(id=5, first_name="Ann", language_code="en"))
    s = await env.repos.settings.get()
    await cmd_start(start_msg, NS(args=None), FakeState({}), ctx, {"language": "en", "lang_chosen": True}, "en", t, s)
    name, kw = env.bot.calls[0]
    assert name == "send_video" and kw["video"] == "VID1" and "Ann" in kw["caption"]


async def test_bad_media_value_is_not_saved_when_preview_fails(env):
    from aiogram.exceptions import TelegramBadRequest
    env.bot.raise_on["send_video"] = TelegramBadRequest("caption too long")
    ctx = NS(repos=env.repos, bot=env.bot, welcome=WelcomeService(env.bot, env.repos, env.i18n),
             lifecycle=NS(schedule=_noop), cfg=NS(admin_ids=frozenset({1})))
    msg = NS(chat=NS(id=1), from_user=NS(id=1, first_name="A"), text=None, photo=None, animation=None,
             video=NS(file_id="V"), delete=_noop)
    state = FakeState({"field": "welcome_media", "pc": 1, "pm": 2})
    await got_value(msg, state, ctx, lambda k, **kw: k, "en", await env.repos.settings.get())
    assert (await env.repos.settings.get())["welcome_media"] is None


# ---- admin panel button on /start, menu cleanup
def _texts(markup):
    return [b.text for r in markup.inline_keyboard for b in r]


def test_admin_panel_button_only_for_admins():
    t = lambda k: k  # noqa: E731
    s = {"welcome_buttons": []}
    admin = welcome_keyboard(t, s, True).inline_keyboard[-1][0]
    assert admin.callback_data == "a:home"
    assert all(b.callback_data != "a:home" for r in welcome_keyboard(t, s, False).inline_keyboard for b in r)


async def test_start_for_admin_includes_panel_button_and_user_does_not(env):
    ctx = NS(repos=env.repos, bot=env.bot, welcome=WelcomeService(env.bot, env.repos, env.i18n),
             cfg=NS(admin_ids=frozenset({1})), verification=None)
    t = lambda k, **kw: env.i18n.t("en", k, **kw)  # noqa: E731
    s = await env.repos.settings.get()
    for uid, expect in ((1, True), (2, False)):
        env.bot.calls.clear()
        m = NS(chat=NS(id=uid), from_user=NS(id=uid, first_name="X", language_code="en"))
        await cmd_start(m, NS(args=None), FakeState({}), ctx, {"language": "en", "lang_chosen": True}, "en", t, s)
        kb = env.bot.calls[0][1]["reply_markup"]
        assert any(b.callback_data == "a:home" for r in kb.inline_keyboard for b in r) is expect


def test_files_menu_no_longer_has_manual_add_buttons():
    data = [b.callback_data for r in files_menu().inline_keyboard for b in r]
    assert not any(d.startswith(("a:f:add", "a:f:batch", "a:f:gen", "a:f:imp")) for d in data)
    assert "a:f:db" in data and "a:f:list:0" in data


async def test_admin_panel_replaces_a_media_message_instead_of_captioning_it():
    calls = []

    class Media:
        text = None
        async def delete(self): calls.append("delete")
        async def answer(self, text, **k): calls.append("answer")
        async def edit_caption(self, **k): calls.append("caption")
    await show(Media(), "panel")
    assert calls == ["delete", "answer"]


# ---- language first, also for direct file links
from app.handlers.language import choose_language  # noqa: E402
from app.keyboards.user import language_keyboard  # noqa: E402
from app.utils.tokens import generate_token  # noqa: E402
from tests.conftest import make_file  # noqa: E402


def _lang_ctx(env):
    return NS(repos=env.repos, bot=env.bot, i18n=env.i18n, verification=env.verification, delivery=env.delivery,
              welcome=WelcomeService(env.bot, env.repos, env.i18n), lifecycle=NS(schedule=_noop),
              cfg=NS(admin_ids=frozenset({1}), flow_ttl_seconds=1800))


async def test_new_user_opening_file_link_is_asked_for_language_first(env):
    f, link = await make_file(env)
    answered = []

    async def answer(text, reply_markup=None): answered.append((text, reply_markup))
    msg = NS(chat=NS(id=5), from_user=NS(id=5, first_name="Ann", language_code="hi"), answer=answer)
    t = lambda k, **kw: env.i18n.t("en", k, **kw)  # noqa: E731
    s = await env.repos.settings.get()
    await cmd_start(msg, NS(args=link["token"]), FakeState({}), _lang_ctx(env), {"language": None}, "en", t, s)
    assert len(answered) == 1 and env.bot.calls == []          # prompt only: no file, no verification yet
    datas = [b.callback_data for r in answered[0][1].inline_keyboard for b in r]
    assert len(datas) == 7 and all(d.endswith(":" + link["token"]) and len(d.encode()) <= 64 for d in datas)


async def test_choosing_language_continues_into_the_file_flow(env):
    f, link = await make_file(env)
    answered = []

    async def cb_answer(text=None, **k): answered.append(text)
    cb = NS(data=f"l:ta:{link['token']}", from_user=NS(id=5, first_name="Ann"), answer=cb_answer,
            message=NS(delete=_noop))
    await choose_language(cb, _lang_ctx(env), await env.repos.settings.get())
    assert env.bot.sent("send_video")                               # no channels -> file delivered right away
    assert env.bot.sent("send_video")[0][1]["caption"].startswith("⚡")


async def test_choosing_language_without_token_shows_welcome(env):
    cb = NS(data="l:mr", from_user=NS(id=5, first_name="Ann"), answer=_noop, message=NS(delete=_noop))
    await choose_language(cb, _lang_ctx(env), await env.repos.settings.get())
    assert env.bot.sent("send_message") and not env.bot.sent("send_video")


async def test_language_for_known_user_goes_straight_to_the_file(env):
    f, link = await make_file(env)
    m = NS(chat=NS(id=5), from_user=NS(id=5, first_name="A", language_code="en"))
    t = lambda k, **kw: env.i18n.t("ta", k, **kw)  # noqa: E731
    await cmd_start(m, NS(args=link["token"]), FakeState({}), _lang_ctx(env), {"language": "ta", "lang_chosen": True}, "ta", t,
                    await env.repos.settings.get())
    assert env.bot.sent("send_video")


def test_language_keyboard_without_token_is_plain():
    d = [b.callback_data for r in language_keyboard(["en", "hi"]).inline_keyboard for b in r]
    assert d == ["l:en", "l:hi"]
