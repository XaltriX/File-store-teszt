from app.keyboards import admin as ak
from app.keyboards.user import verify_keyboard, welcome_keyboard


def all_buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


def test_admin_callback_data_within_telegram_limit():
    from app.utils.tokens import generate_token
    tok = generate_token()
    samples = [ak.dashboard(), ak.files_menu(), ak.channels_menu(), ak.settings_menu()]
    for m in samples:
        for b in all_buttons(m):
            assert len((b.callback_data or "x").encode()) <= 64
    longest = ak.btn("x", f"a:f:lrc:{tok}")
    assert len(longest.callback_data.encode()) <= 64
    assert len(ak.btn("x", "a:c:view:-1001234567890123").callback_data.encode()) <= 64


def test_dashboard_layout_matches_spec():
    rows = [[b.text for b in r] for r in ak.dashboard().inline_keyboard]
    assert rows == [["📂 Files", "📢 Channels"], ["📣 Broadcast", "⚙️ Settings"], ["📊 Statistics", "❌ Close"]]


def test_welcome_keyboard_uses_real_url_buttons_only_when_configured():
    t = lambda k: k  # noqa: E731
    m = welcome_keyboard(t, {"more_videos_url": "https://a.com", "developer_url": None, "welcome_buttons": []})
    btns = all_buttons(m)
    assert btns[0].url == "https://a.com"
    dev = [b for b in btns if b.text == "btn_dev"][0]
    assert dev.url is None and dev.callback_data == "u:nc"  # never invent a destination


def test_verify_keyboard_join_label_matches_action():
    pub = verify_keyboard(lambda k: k, {"mode": "public", "url": "https://t.me/x"}, "f1")
    req = verify_keyboard(lambda k: k, {"mode": "request", "url": "https://t.me/+abc"}, "f1")
    assert all_buttons(pub)[0].text == "btn_join" and all_buttons(req)[0].text == "btn_request"
    assert all_buttons(pub)[1].callback_data == "v:f1"
