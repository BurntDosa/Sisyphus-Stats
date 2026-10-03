"""Private custom rank/role emoji mappings with readable display fallbacks."""
from __future__ import annotations

import json
import os
import re
from typing import Callable

_EMOJI = re.compile(r"<(?P<animated>a?):(?P<name>[A-Za-z0-9_]{2,32}):(?P<id>[0-9]{1,20})>")
_validator: Callable[[int], bool] | None = None
_ROLE_NAMES = {"TOP": "Top", "JUNGLE": "Jungle", "MID": "Mid", "BOTTOM": "ADC", "SUPPORT": "Support"}
_ROLE_ALIASES = {"JGL": "JUNGLE", "JG": "JUNGLE", "MIDDLE": "MID", "ADC": "BOTTOM", "BOT": "BOTTOM", "SUPP": "SUPPORT", "SUP": "SUPPORT", "UTILITY": "SUPPORT"}


def _mapping(variable: str) -> dict[str, str]:
    try:
        value = json.loads(os.getenv(variable, "{}"))
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(key).upper(): emoji for key, emoji in value.items()
            if isinstance(emoji, str) and _EMOJI.fullmatch(emoji)
            and int(_EMOJI.fullmatch(emoji)["id"]) > 0}


def set_emoji_validator(validator: Callable[[int], bool]) -> None:
    """Use the current Discord cache to reject deleted or inaccessible emojis."""
    global _validator
    _validator = validator


def _custom(mapping: dict[str, str], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    emoji_id = int(_EMOJI.fullmatch(value)["id"])
    if _validator is not None and not _validator(emoji_id):
        return None
    return value


def rank_emoji(tier: str | None) -> str | None:
    return _custom(_mapping("RANK_EMOJIS"), str(tier or "UNRANKED").upper())


def rank_image_url(tier: str | None) -> str | None:
    value = rank_emoji(tier)
    if not value:
        return None
    match = _EMOJI.fullmatch(value)
    extension = "gif" if match["animated"] else "png"
    return f'https://cdn.discordapp.com/emojis/{match["id"]}.{extension}?size=128'


def role_emoji(role: str | None) -> str | None:
    key = str(role or "").upper()
    return _custom(_mapping("ROLE_EMOJIS"), _ROLE_ALIASES.get(key, key))


def role_display(role: str | None) -> str:
    key = str(role or "").upper()
    key = _ROLE_ALIASES.get(key, key)
    label = _ROLE_NAMES.get(key, "Flexible" if key in {"", "FLEXIBLE"} else str(role))
    icon = role_emoji(key)
    return f"{icon} {label}" if icon else label
