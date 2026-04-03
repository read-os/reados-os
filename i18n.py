"""
ReadOS Internationalization (i18n)
Lightweight translation engine with JSON locale files.
"""

import json
import os
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("ReadOS.i18n")

SUPPORTED_LANGUAGES = {
    "en": "English",
    "pt": "Português",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "it": "Italiano",
    "ja": "日本語",
    "zh": "中文",
}

_translations: Dict[str, Dict] = {}
_locales_dir = Path(__file__).parent / "locales"


def load_translations():
    """Load all available translation files from the locales directory."""
    global _translations
    if not _locales_dir.exists():
        logger.warning(f"Locales dir not found: {_locales_dir}")
        return

    for lang_code in SUPPORTED_LANGUAGES:
        path = _locales_dir / f"{lang_code}.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                _translations[lang_code] = json.load(f)
                logger.debug(f"Loaded locale: {lang_code}")


def t(key: str, lang: str = "en", **kwargs) -> str:
    """
    Translate a key into the given language.
    Falls back to English, then to the key itself.
    Supports format placeholders: t("hello", name="World") → "Hello, World!"
    """
    load_translations_if_needed()
    translation = (
        _translations.get(lang, {}).get(key)
        or _translations.get("en", {}).get(key)
        or key
    )
    if kwargs:
        try:
            translation = translation.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return translation


def get_all_strings(lang: str = "en") -> Dict[str, str]:
    """Return full translation map for a language (for frontend)."""
    load_translations_if_needed()
    en = _translations.get("en", {})
    target = _translations.get(lang, {})
    # Merge: use target strings, fallback to English
    return {k: target.get(k, v) for k, v in en.items()}


def available_languages() -> Dict[str, str]:
    """Return dict of {code: name} for available translations."""
    load_translations_if_needed()
    return {
        code: name for code, name in SUPPORTED_LANGUAGES.items()
        if code in _translations or code == "en"
    }


_loaded = False

def load_translations_if_needed():
    global _loaded
    if not _loaded:
        load_translations()
        _loaded = True
