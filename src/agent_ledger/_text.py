"""Text as a reader should see it, shared by every command that prints ledger text."""
from __future__ import annotations

import unicodedata

_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Mn", "Me", "Zs", "Zl", "Zp"})
_FILLERS = frozenset("ᅟᅠㅤﾠ")


def visible(text: str) -> str:
    """Invisible and bidi-affecting characters, line breaks included, written as \\uXXXX, so one
    row can't hide text, start a new line or pass for another. A plain space stays a space."""
    return "".join(
        f"\\u{ord(ch):04x}" if ch != " " and (unicodedata.category(ch) in _INVISIBLE_CATEGORIES
                                             or ch in _FILLERS) else ch
        for ch in text)
