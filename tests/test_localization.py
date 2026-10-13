import json
from pathlib import Path

from app.localization import LANGUAGES, Localizer

DIR = Path(__file__).resolve().parent.parent / "app" / "localization"


def load(code):
    return json.loads((DIR / f"{code}.json").read_text(encoding="utf-8"))


def test_all_seven_languages_cover_every_user_facing_key():
    en = {k for k in load("en") if not k.startswith("a_")}
    assert set(LANGUAGES) == {"en", "hi", "bn", "mr", "te", "ta", "gu"}
    for code in LANGUAGES:
        assert {k for k in load(code) if not k.startswith("a_")} == en, code


def test_no_untranslated_english_leftovers_in_non_english():
    en = load("en")
    for code in LANGUAGES:
        if code == "en":
            continue
        d = load(code)
        same = [k for k in d if d[k] == en[k] and k not in ("btn_dev",)]
        assert not same, (code, same)


def test_placeholders_match_english():
    import re
    en = load("en")
    for code in LANGUAGES:
        for k, v in load(code).items():
            assert set(re.findall(r"\{(\w+)\}", v)) == set(re.findall(r"\{(\w+)\}", en[k])), (code, k)


def test_html_tags_balanced():
    import re
    for code in LANGUAGES:
        for k, v in load(code).items():
            for tag in ("b", "i", "code", "blockquote"):
                assert len(re.findall(f"<{tag}>", v)) == len(re.findall(f"</{tag}>", v)), (code, k)


def test_fallback_to_english_for_missing_key_and_unknown_language():
    i = Localizer()
    i.data["hi"].pop("err_invalid")
    assert i.t("hi", "err_invalid") == i.t("en", "err_invalid")
    assert i.t("xx", "err_invalid") == i.t("en", "err_invalid")
    assert i.t("en", "no_such_key") == "no_such_key"


def test_formatting_and_brand_name_in_every_language():
    i = Localizer()
    for code in LANGUAGES:
        w = i.t(code, "welcome", name="Ann")
        assert "Ann" in w and "𝗟𝗨𝗦𝗧 𝗧𝗘𝗔𝗖𝗛𝗘𝗥 𝗕𝗢𝗧" in w and "<blockquote>" in w
        assert "𝗟𝗨𝗦𝗧 𝗧𝗘𝗔𝗖𝗛𝗘𝗥 𝗕𝗢𝗧" in i.t(code, "about")


def test_removed_languages_fall_back_to_default():
    i = Localizer()
    assert i.normalize("es") == "en" and i.normalize("zh-CN") == "en" and i.normalize("ta-IN") == "ta"


def test_normalize_respects_enabled_languages():
    i = Localizer()
    assert i.normalize("mr-IN", ["en", "mr"]) == "mr"
    assert i.normalize("mr-IN", ["en"]) == "en"
    assert i.normalize(None) == "en"


async def test_language_persists_across_touches_and_missing_value_falls_back(env):
    u = env.repos.users
    doc, new = await u.touch(5, "A", None, "a")
    assert new and doc["language"] is None
    await u.set_language(5, "gu")
    doc2, new2 = await u.touch(5, "A2", None, "a")
    assert not new2 and doc2["language"] == "gu"
    assert (await u.get(5))["language"] == "gu"
