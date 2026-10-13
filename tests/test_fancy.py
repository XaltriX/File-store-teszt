from app.utils.fancy import bold


def test_ascii_letters_and_digits_become_bold_glyphs():
    assert bold("Files 2") == "𝗙𝗶𝗹𝗲𝘀 𝟮"
    assert len(bold("Add Channel")) == len("Add Channel")


def test_emoji_symbols_and_indic_scripts_are_untouched():
    assert bold("📂 ↗ ✅ ⬅️") == "📂 ↗ ✅ ⬅️"
    assert bold("चैनल జాయిన్ தமிழ் ગુજરાતી") == "चैनल జాయిన్ தமிழ் ગુજરાતી"
