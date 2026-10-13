from html import escape as _esc
from urllib.parse import quote, urlparse


def esc(value) -> str:
    return _esc("" if value is None else str(value), quote=False)


def human_size(n: int | None) -> str:
    if not n:
        return "-"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return "-"


def is_valid_url(url: str) -> bool:
    try:
        p = urlparse(url.strip())
    except ValueError:
        return False
    if p.scheme in ("http", "https"):
        return bool(p.netloc and "." in p.netloc)
    return p.scheme == "tg" and bool(p.netloc)


def share_url(deep_link: str) -> str:
    return f"https://t.me/share/url?url={quote(deep_link, safe='')}"


def chunk_text(text: str, limit: int = 3800) -> list[str]:
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit and cur:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        out.append(cur)
    return out
