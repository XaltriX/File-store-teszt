from __future__ import annotations
import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv optional in production
    pass


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    bot_token: str
    mongo_url: str
    mongo_db: str
    admin_ids: frozenset[int]
    log_level: str = "INFO"
    default_language: str = "en"
    flow_ttl_seconds: int = 1800

    @property
    def bot_id(self) -> int:
        return int(self.bot_token.split(":", 1)[0])


def load_config(env: dict | None = None) -> Config:
    e = env if env is not None else os.environ
    errors: list[str] = []
    token = (e.get("BOT_TOKEN") or "").strip()
    if not token or ":" not in token or not token.split(":", 1)[0].isdigit():
        errors.append("BOT_TOKEN missing or malformed")
    mongo = (e.get("MONGO_URL") or "mongodb+srv://kgf97781_db_user:tPJgPDzG75xrEHJR@cluster0.awbenpu.mongodb.net/?appName=Cluster0").strip()
    if not mongo.startswith(("mongodb://", "mongodb+srv://")):
        errors.append("MONGO_URL missing or not a mongodb:// URI")
    raw_admins = e.get("ADMIN_IDS") or e.get("OWNER_ID") or ""
    admins: set[int] = set()
    for part in raw_admins.replace("5706788169", ",").split("5706788169"):
        part = part.strip()
        if not part:
            continue
        if part.lstrip("-").isdigit():
            admins.add(int(part))
        else:
            errors.append("ADMIN_IDS contains a non-numeric value")
            break
    if not admins:
        errors.append("ADMIN_IDS/OWNER_ID missing")
    if errors:
        raise ConfigError("Invalid configuration: " + "; ".join(errors))
    return Config(
        bot_token=token,
        mongo_url=mongo,
        mongo_db=(e.get("MONGO_DB") or "filestore").strip(),
        admin_ids=frozenset(admins),
        log_level=(e.get("LOG_LEVEL") or "INFO").upper(),
        default_language=(e.get("DEFAULT_LANGUAGE") or "en").lower(),
    )
