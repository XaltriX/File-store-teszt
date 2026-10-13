import re
import secrets

TOKEN_BYTES = 16  # 128-bit; urlsafe b64 -> 22 chars, within 64-char /start limit
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def is_valid_token(value: str | None) -> bool:
    return bool(value) and bool(_TOKEN_RE.fullmatch(value))
