"""'Today' for statistics follows the admin's timezone (default India), not UTC."""
from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone


def _tz(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - tzdata missing: fixed offset for India, UTC otherwise
        return timezone(timedelta(hours=5, minutes=30)) if name in ("Asia/Kolkata", "Asia/Calcutta", "IST") else timezone.utc


TZ_NAME = os.environ.get("TIMEZONE", "Asia/Kolkata")
TZ = _tz(TZ_NAME)


def today(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(TZ).strftime("%Y-%m-%d")
