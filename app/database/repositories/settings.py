from __future__ import annotations
DEFAULTS: dict = {
    "welcome_text": None,          # None -> localized default
    "welcome_image": None,         # Telegram file_id
    "welcome_buttons": [],         # [{"text","url"}] extra URL buttons
    "more_videos_url": None,
    "developer_url": None,
    "official_channels": [],       # [{"title","url"}]
    "about_text": None,
    "default_protect": False,
    "autodelete_seconds": 60,      # 0 disables
    "required_steps": 1,
    "balancing": "least",          # least | random
    "enabled_languages": ["en", "hi", "bn", "es", "ar", "pt", "zh"],
    "default_language": "en",
    "import_sources": [],          # chat ids auto-imported from channel_post
}


class SettingsRepo:
    def __init__(self, col):
        self.col = col

    async def get(self) -> dict:
        doc = await self.col.find_one({"_id": "global"}) or {}
        out = {**DEFAULTS, **{k: v for k, v in doc.items() if k != "_id"}}
        return out

    async def update(self, **fields) -> None:
        bad = set(fields) - set(DEFAULTS)
        if bad:
            raise KeyError(f"unknown settings: {bad}")
        await self.col.update_one({"_id": "global"}, {"$set": fields}, upsert=True)
