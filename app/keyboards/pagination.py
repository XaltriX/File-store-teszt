from aiogram.types import InlineKeyboardButton as B


def pager_row(prefix: str, page: int, total: int, size: int) -> list[B]:
    """prefix e.g. 'a:f:list' -> buttons 'a:f:list:<page>'."""
    row = []
    if page > 0:
        row.append(B(text="⬅️", callback_data=f"{prefix}:{page - 1}"))
    pages = max(1, -(-total // size))
    row.append(B(text=f"{page + 1}/{pages}", callback_data="a:noop"))
    if (page + 1) * size < total:
        row.append(B(text="➡️", callback_data=f"{prefix}:{page + 1}"))
    return row
