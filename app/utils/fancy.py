"""Telegram inline-button labels cannot carry HTML. Bold is done with Unicode Mathematical
Sans-Serif Bold glyphs: only ASCII letters/digits are mapped; emoji and Indic scripts stay as-is
(Indic scripts have no bold glyphs)."""


def bold(text: str) -> str:
    out = []
    for ch in text:
        o = ord(ch)
        if 65 <= o <= 90:
            out.append(chr(0x1D5D4 + o - 65))
        elif 97 <= o <= 122:
            out.append(chr(0x1D5EE + o - 97))
        elif 48 <= o <= 57:
            out.append(chr(0x1D7EC + o - 48))
        else:
            out.append(ch)
    return "".join(out)
