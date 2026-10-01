"""State-independent outcome classification, LP math, and streak detection."""
from __future__ import annotations

REMAKE_MAX_DURATION_SECONDS = 120


def is_remake_duration(duration_seconds) -> bool:
    try:
        duration = int(duration_seconds or 0)
    except (TypeError, ValueError):
        return False
    return 0 < duration < REMAKE_MAX_DURATION_SECONDS


def canonical_outcome(result_code):
    code = str(result_code or "").upper()
    if code == "WIN":
        return "WIN"
    if code in {"REMAKE", "DRAW", "TIE", "VOID"}:
        return "DRAW"
    if code in {"LOSE", "LOSS", "DEFEAT"}:
        return "LOSS"
    if not code:
        return None  # insufficient data — caller should skip
    return "LOSS"  # unknown non-empty code: be conservative


def effective_outcome(result_code: str, lp_delta: int):
    """Override draw/remake to WIN/LOSS based on actual LP change.

    Returns None when result_code is empty — callers should treat that as
    "match data incomplete, skip this match" rather than guessing DRAW.
    """
    code = str(result_code or "").upper()
    if not code:
        print(f"[effective_outcome] empty result_code (lp_delta={lp_delta}); skipping")
        return None
    if code in {"WIN", "LOSE", "LOSS", "DEFEAT"}:
        return "WIN" if code == "WIN" else "LOSS"
    if code in {"REMAKE", "DRAW", "TIE"}:
        if lp_delta > 0:
            return "WIN"
        if lp_delta < 0:
            return "LOSS"
        return "DRAW"
    return "LOSS"


def match_outcome(result_code: str, lp_delta: int, duration_seconds=None):
    if is_remake_duration(duration_seconds):
        return "DRAW"
    return effective_outcome(result_code, lp_delta)


def outcome_icon(result):
    return {"WIN": "✅", "LOSS": "❌", "DRAW": "➖"}.get(result, "➖")


def parse_lp_change(value):
    if value is None:
        return None
    text = str(value).strip()
    if text == "?" or not text:
        return None
    try:
        return int(text.replace("LP", "").strip())
    except ValueError:
        return None


def compute_all_time_stats(history_all):
    wins = sum(1 for h in history_all if h.get("result") == "WIN")
    losses = sum(1 for h in history_all if h.get("result") == "LOSS")
    draws = sum(1 for h in history_all if h.get("result") == "DRAW")
    deltas = [
        d
        for d in (parse_lp_change(h.get("lp_change")) for h in history_all)
        if d is not None
    ]
    net_lp = sum(deltas) if deltas else 0
    peak_lp_total = max((h["lp_total"] for h in history_all if isinstance(h.get("lp_total"), int)), default=0)
    return wins, losses, draws, net_lp, peak_lp_total


def compute_net_lp(history_today, fallback_diff=0):
    deltas = [
        d
        for d in (parse_lp_change(h.get("lp_change")) for h in history_today)
        if d is not None
    ]
    if deltas:
        return sum(deltas)
    return fallback_diff


def current_streak(history_rows):
    streak_result = None
    streak_count = 0
    for row in reversed(history_rows):
        result = row.get("result")
        if result not in {"WIN", "LOSS"}:
            continue
        if streak_result is None:
            streak_result = result
            streak_count = 1
            continue
        if result == streak_result:
            streak_count += 1
            continue
        break
    return streak_result, streak_count
