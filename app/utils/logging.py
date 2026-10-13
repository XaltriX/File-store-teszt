import logging
import re

_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_MONGO_RE = re.compile(r"(mongodb(?:\+srv)?://)[^@\s]+@")


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        msg = _TOKEN_RE.sub("<redacted-token>", msg)
        msg = _MONGO_RE.sub(r"\1<redacted>@", msg)
        record.msg, record.args = msg, ()
        return True


def setup_logging(level: str = "INFO") -> None:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    h.addFilter(RedactFilter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(getattr(logging, level, logging.INFO))
