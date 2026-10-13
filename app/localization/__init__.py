from __future__ import annotations
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

LANGUAGES: dict[str, str] = {
    "en": "English", "hi": "हिन्दी", "bn": "বাংলা", "es": "Español",
    "ar": "العربية", "pt": "Português", "zh": "简体中文",
}
RTL = {"ar"}
_RLM = "\u200f"


class Localizer:
    def __init__(self, directory: Path | None = None, default: str = "en"):
        self.default = default
        d = directory or Path(__file__).parent
        self.data: dict[str, dict[str, str]] = {}
        for code in LANGUAGES:
            p = d / f"{code}.json"
            self.data[code] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def normalize(self, code: str | None, allowed: list[str] | None = None) -> str:
        if code:
            code = code.lower().split("-")[0]
        if code in LANGUAGES and (allowed is None or code in allowed):
            return code
        return self.default

    def t(self, lang: str | None, key: str, **kw) -> str:
        lang = lang if lang in self.data else self.default
        text = self.data[lang].get(key)
        if text is None:
            text = self.data[self.default].get(key)
            if text is None:
                log.warning("missing translation key %s", key)
                return key
            if not key.startswith("a_"):  # admin labels are English-only by design
                log.warning("translation key %s missing for %s, using en", key, lang)
        try:
            text = text.format(**kw) if kw else text
        except (KeyError, IndexError):
            log.warning("bad format args for key %s", key)
        if lang in RTL:
            text = "\n".join((_RLM + ln if ln.strip() and not ln.startswith(_RLM) else ln) for ln in text.split("\n"))
        return text
