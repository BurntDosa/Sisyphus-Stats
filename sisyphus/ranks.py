"""LoL rank tables, division math, tier display helpers."""
from __future__ import annotations

from .display_icons import rank_emoji, rank_image_url

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
APEX_START_TOTAL_LP = TIER_ORDER["MASTER"] * 400
_apex_cutoffs: dict[str, int] | None = None


def set_apex_cutoffs(*, challenger: int, grandmaster: int) -> None:
    """Set the current region cutoffs used when formatting apex LP."""
    global _apex_cutoffs
    if challenger <= grandmaster or grandmaster <= 0:
        raise ValueError("Invalid apex cutoff values")
    _apex_cutoffs = {"challenger": challenger, "grandmaster": grandmaster}


def tier_for_total_lp(total_lp: int | None) -> str:
    if total_lp is None or total_lp <= 0:
        return "UNRANKED"
    if total_lp < APEX_START_TOTAL_LP:
        return TIER_BY_INDEX.get(total_lp // 400, "UNRANKED")
    if _apex_cutoffs:
        if total_lp >= APEX_START_TOTAL_LP + _apex_cutoffs["challenger"]:
            return "CHALLENGER"
        if total_lp >= APEX_START_TOTAL_LP + _apex_cutoffs["grandmaster"]:
            return "GRANDMASTER"
    return "MASTER"


def format_rank(tier: str | None, rank: str | None = None) -> str:
    """Format a visible rank, omitting non-existent apex divisions."""
    normalized_tier = str(tier or "UNRANKED").upper()
    if normalized_tier in {"MASTER", "GRANDMASTER", "CHALLENGER"}:
        return normalized_tier
    normalized_rank = str(rank or "").strip()
    return f"{normalized_tier} {normalized_rank}".strip()


def format_total_lp(total_lp):
    if total_lp is None or total_lp <= 0:
        return "UNRANKED — 0 LP"
    if total_lp >= APEX_START_TOTAL_LP:
        tier = tier_for_total_lp(total_lp)
        # Historic totals were recorded while OP.GG still returned a phantom
        # apex division of `1`. Remove that legacy 300-LP offset before
        # showing the real apex LP value.
        legacy_apex_base = TIER_ORDER[tier] * 400 + DIV_ORDER["1"] * 100
        apex_lp = max(0, total_lp - legacy_apex_base)
        return f"{tier} — {apex_lp} LP"
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
    normalized = str(tier or "UNRANKED").upper()
    return rank_emoji(normalized) or icons.get(normalized, "❓")


def tier_image_url(tier):
    if isinstance(tier, int):
        tier = TIER_BY_INDEX.get(tier, "UNRANKED")
    custom = rank_image_url(tier)
    if custom:
        return custom
    if not tier or tier == "UNRANKED":
        return "https://opgg-static.akamaized.net/images/medals_new/default.png"
    return f"https://opgg-static.akamaized.net/images/medals_new/{tier.lower()}.png"
