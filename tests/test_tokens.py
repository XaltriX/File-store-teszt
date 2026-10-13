import re

from app.utils.tokens import generate_token, is_valid_token


def test_generate_is_urlsafe_and_short_enough_for_start_payload():
    t = generate_token()
    assert re.fullmatch(r"[A-Za-z0-9_-]+", t) and len(t) <= 64
    assert is_valid_token(t)


def test_tokens_are_unique_and_not_sequential():
    toks = {generate_token() for _ in range(5000)}
    assert len(toks) == 5000


def test_invalid_tokens_rejected():
    for bad in (None, "", "short", "a" * 21, "a" * 23, "bad token with spaces!!", "../../etc/passwd" + "x" * 8):
        assert not is_valid_token(bad)
