"""
i18n.py – Lightweight internationalisation helper.

Supported languages:
    en  – English (default)
    zh  – Simplified Chinese / 简体中文

Configure with BOT_LANGUAGE in .env (e.g. BOT_LANGUAGE=zh).

Usage in a plugin:
    from ..utils.i18n import t

    _S = {
        "greeting": {"en": "Hello, {name}!", "zh": "你好，{name}！"},
    }

    msg = t(_S["greeting"], name="world")
"""

import os
from typing import Any

# Resolved once at import time so every plugin sees the same value.
# Using BOT_LANGUAGE to avoid clashing with the POSIX locale variable LANGUAGE.
_language: str = os.getenv("BOT_LANGUAGE", "en").lower().strip()
if _language not in ("en", "zh"):
    _language = "en"


def t(strings: dict[str, str], **kwargs: Any) -> str:
    """Return the translation for the current language, formatted with kwargs.

    Falls back to the "en" entry if the current language has no entry.
    """
    text = strings.get(_language) or strings.get("en", "")
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
            return text
    return text
