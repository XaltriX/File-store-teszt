from types import SimpleNamespace as NS

from app.middlewares.admin_auth import AdminAuthMiddleware
from app.middlewares.throttle import ThrottleMiddleware


class FakeCallback:
    answered = None

    async def answer(self, text=None, show_alert=False):
        self.answered = (text, show_alert)


def patch_cb_type(monkeypatch=None):
    import app.middlewares.admin_auth as m
    m.CallbackQuery = FakeCallback


async def test_non_admin_is_blocked_server_side_and_alerted():
    patch_cb_type()
    called = []

    async def handler(e, d):
        called.append(1)

    mw = AdminAuthMiddleware({1})
    cb = FakeCallback()
    await mw(handler, cb, {"event_from_user": NS(id=2)})
    assert not called and cb.answered[1] is True


async def test_admin_passes_and_missing_user_is_denied():
    patch_cb_type()
    called = []

    async def handler(e, d):
        called.append(1)
        return "ok"

    mw = AdminAuthMiddleware({1})
    assert await mw(handler, object(), {"event_from_user": NS(id=1)}) == "ok"
    assert await mw(handler, object(), {"event_from_user": None}) is None
    assert len(called) == 1


async def test_forged_admin_callback_from_regular_user_never_reaches_handler():
    patch_cb_type()
    hit = []

    async def handler(e, d):
        hit.append(e)

    cb = FakeCallback()
    cb.data = "a:f:delc:abc"
    await AdminAuthMiddleware({10})(handler, cb, {"event_from_user": NS(id=99)})
    assert hit == []


async def test_throttle_limits_users_but_not_admins():
    t = [0.0]
    mw = ThrottleMiddleware({1}, limit=3, window=10, clock=lambda: t[0])
    ok = [mw.allow(5) for _ in range(5)]
    assert ok == [True, True, True, False, False]
    t[0] = 11
    assert mw.allow(5)

    async def handler(e, d):
        return "ran"
    assert await mw(handler, object(), {"event_from_user": NS(id=1)}) == "ran"
