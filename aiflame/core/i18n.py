"""Presentation-only translations; protocol values and stored events stay unchanged."""

import json
import re
from functools import lru_cache
from aiflame.core.paths import PACKAGE

LANGUAGES = ("zh-CN", "en")


@lru_cache(maxsize=1)
def english_catalog():
    return json.loads((PACKAGE / "assets/web/locales/en.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def phrase_catalog():
    catalog = english_catalog()
    phrases = {key.strip(): value.strip() for key, value in catalog.items()}
    phrases.update({key: value for key, value in catalog.items() if key == key.strip()})
    return phrases


@lru_cache(maxsize=1)
def phrase_pattern():
    # Prefer complete sentences, then fragments used in composed status labels.
    keys = sorted((key for key in phrase_catalog() if len(key) > 1 or key in ("秒", "台")), key=len, reverse=True)
    return re.compile("|".join(re.escape(key) for key in keys))


def translate(text, language="zh-CN"):
    if not isinstance(text, str) or language != "en":
        return text
    catalog = english_catalog()
    if text in catalog:
        return catalog[text]
    return phrase_pattern().sub(lambda match: phrase_catalog()[match.group()], text)
