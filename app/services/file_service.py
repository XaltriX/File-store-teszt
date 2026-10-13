from __future__ import annotations


def extract_file_info(m) -> dict | None:
    """Map an aiogram Message to storable metadata, or None if it carries no file."""
    if m.document:
        o, t = m.document, "document"
    elif m.video:
        o, t = m.video, "video"
    elif m.audio:
        o, t = m.audio, "audio"
    elif m.voice:
        o, t = m.voice, "voice"
    elif m.animation:
        o, t = m.animation, "animation"
    elif m.video_note:
        o, t = m.video_note, "video_note"
    elif m.sticker:
        o, t = m.sticker, "sticker"
    elif m.photo:
        o, t = m.photo[-1], "photo"
    else:
        return None
    name = getattr(o, "file_name", None) or getattr(o, "title", None)
    dur = getattr(o, "duration", None)  # video / audio / voice / animation / video_note only
    return {"file_id": o.file_id, "file_unique_id": o.file_unique_id, "ftype": t, "name": name,
            "size": getattr(o, "file_size", None), "mime": getattr(o, "mime_type", None),
            "caption": m.caption, "duration": int(dur) if isinstance(dur, (int, float)) and dur > 0 else None}


def source_info(m) -> dict | None:
    fo = getattr(m, "forward_origin", None)
    chat = getattr(fo, "chat", None)
    if chat is not None and getattr(fo, "message_id", None):
        return {"chat_id": chat.id, "message_id": fo.message_id}
    if m.chat and m.chat.type == "channel":
        return {"chat_id": m.chat.id, "message_id": m.message_id}
    return None


class FileService:
    def __init__(self, repos, links):
        self.repos, self.links = repos, links

    async def ingest(self, message, source: dict | None = None, order: int = 0, store: dict | None = None):
        """Returns (file, link, created) or None when the message has no file.
        store = {chat_id, message_id} of the copy kept in the DB channel (preferred delivery source)."""
        info = extract_file_info(message)
        if info is None:
            return None
        file, created = await self.repos.files.create(**info, source=source or source_info(message), order=order, store=store)
        link = await self.links.get_or_create(file["_id"])
        return file, link, created
