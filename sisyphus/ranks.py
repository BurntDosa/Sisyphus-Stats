"""LoL rank tables, division math, tier display helpers."""
from __future__ import annotations

TIER_EMOJI = {
    "IRON": "⬛",
    "BRONZE": "🥉",
    "SILVER": "🥈",
    "GOLD": "🥇",
    "PLATINUM": "💠",
    "EMERALD": "💚",
    "DIAMOND": "💎",
    "MASTER": "🔮",
    "GRANDMASTER": "🔥",
    "CHALLENGER": "🏆",
    "UNRANKED": "—",
}

TIER_COLOR = {
    "IRON": 0x7F7F7F,
    "BRONZE": 0xCD7F32,
    "SILVER": 0xC0C0C0,
    "GOLD": 0xFFD700,
    "PLATINUM": 0x00B4D8,
    "EMERALD": 0x00C853,
    "DIAMOND": 0x00B0FF,
    "MASTER": 0xAA00FF,
    "GRANDMASTER": 0xFF6D00,
    "CHALLENGER": 0xFFD700,
    "UNRANKED": 0x5865F2,
}

TIER_ORDER = {
    "IRON": 0,
    "BRONZE": 1,
    "SILVER": 2,
    "GOLD": 3,
    "PLATINUM": 4,
    "EMERALD": 5,
    "DIAMOND": 6,
    "MASTER": 7,
    "GRANDMASTER": 8,
    "CHALLENGER": 9,
}

DIV_ORDER = {"IV": 0, "III": 1, "II": 2, "I": 3, "": 0}
DIV_ORDER.update({"4": 0, "3": 1, "2": 2, "1": 3})

DIVISION_BY_INDEX = {0: "4", 1: "3", 2: "2", 3: "1"}
TIER_BY_INDEX = {index: tier for tier, index in TIER_ORDER.items()}


def format_total_lp(total_lp):
    if total_lp is None or total_lp <= 0:
        return "UNRANKED — 0 LP"
    tier_index = total_lp // 400
    tier = TIER_BY_INDEX.get(tier_index, "UNRANKED")
    remainder = total_lp % 400
    division_index = min(3, remainder // 100)
    lp = remainder % 100
    division = DIVISION_BY_INDEX.get(division_index, "4")
    return f"{tier} {division} — {lp} LP"


def tier_emoji(tier):
    icons = {
        "IRON": "⬛",
        "BRONZE": "🟫",
        "SILVER": "⬜",
        "GOLD": "🟡",
        "PLATINUM": "🔵",
        "EMERALD": "🟢",
        "DIAMOND": "💎",
        "MASTER": "🔮",
        "GRANDMASTER": "🔥",
        "CHALLENGER": "🏆",
        "UNRANKED": "❓",
    }
    return icons.get(tier, "❓")


def tier_image_url(tier):
    if isinstance(tier, int):
        tier = TIER_BY_INDEX.get(tier, "UNRANKED")
    if not tier or tier == "UNRANKED":
        return "https://opgg-static.akamaized.net/images/medals_new/default.png"
    return f"https://opgg-static.akamaized.net/images/medals_new/{tier.lower()}.png"
