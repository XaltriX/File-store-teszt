from aiogram.exceptions import (
    TelegramAPIError, TelegramBadRequest, TelegramForbiddenError,
    TelegramNetworkError, TelegramRetryAfter, TelegramServerError,
)

BLOCKED_MARKERS = ("bot was blocked", "user is deactivated", "chat not found", "bot can't initiate")


def is_blocked(exc: Exception) -> bool:
    if isinstance(exc, TelegramForbiddenError):
        return True
    return isinstance(exc, TelegramBadRequest) and any(m in str(exc).lower() for m in BLOCKED_MARKERS)


def is_retryable(exc: Exception) -> bool:
    return isinstance(exc, (TelegramRetryAfter, TelegramNetworkError, TelegramServerError))


def retry_after(exc: Exception) -> float:
    return float(getattr(exc, "retry_after", 1) or 1)


def is_not_modified(exc: Exception) -> bool:
    return isinstance(exc, TelegramBadRequest) and "not modified" in str(exc).lower()


__all__ = ["TelegramAPIError", "is_blocked", "is_retryable", "retry_after", "is_not_modified"]
