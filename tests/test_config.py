import pytest

from config import ConfigError, load_config

GOOD = {"BOT_TOKEN": "123456:" + "A" * 35, "MONGO_URL": "mongodb://localhost:27017", "ADMIN_IDS": "1, 2"}


def test_valid_config():
    c = load_config(GOOD)
    assert c.admin_ids == frozenset({1, 2}) and c.mongo_db == "filestore" and c.bot_id == 123456


def test_owner_id_alias_and_missing_values():
    c = load_config({**{k: v for k, v in GOOD.items() if k != "ADMIN_IDS"}, "OWNER_ID": "9"})
    assert c.admin_ids == frozenset({9})
    with pytest.raises(ConfigError) as e:
        load_config({})
    msg = str(e.value)
    assert "BOT_TOKEN" in msg and "MONGO_URL" in msg and "ADMIN_IDS" in msg


def test_errors_never_leak_secret_values():
    bad = {"BOT_TOKEN": "SECRETTOKENVALUE", "MONGO_URL": "mongodb://user:hunter2@h", "ADMIN_IDS": "abc"}
    with pytest.raises(ConfigError) as e:
        load_config(bad)
    assert "SECRETTOKENVALUE" not in str(e.value) and "hunter2" not in str(e.value)


async def test_mongo_unavailable_fails_clearly_at_startup(monkeypatch):
    import app.database.mongo as m

    class DeadClient:
        def __init__(self, *a, **k):
            self.admin = self
            self.closed = False

        async def command(self, *_):
            raise RuntimeError("no server")

        async def close(self):
            self.closed = True

    monkeypatch.setattr(m, "AsyncMongoClient", DeadClient)
    with pytest.raises(m.DatabaseUnavailable) as e:
        await m.connect("mongodb://x", "db")
    assert "hunter2" not in str(e.value)


def test_log_redaction():
    import logging
    from app.utils.logging import RedactFilter
    rec = logging.LogRecord("x", logging.INFO, "", 0, "tok 123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA uri mongodb+srv://u:pw@h/db", (), None)
    RedactFilter().filter(rec)
    assert "AAAA" not in rec.msg and "pw@" not in rec.msg
