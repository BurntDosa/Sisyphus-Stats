"""Outcome classification, LP math, streak detection, delayed-LP reconciliation."""
from __future__ import annotations

from datetime import timedelta

from .config import LP_RECONCILE_DELAY_MINUTES
from .state import data, save_data
from .utils import now_ist, parse_iso_datetime

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
    peak_lp_total = max((h.get("lp_total", 0) for h in history_all), default=0)
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


def reconcile_delayed_lp(riot_id: str, current_total_lp: int, today_str: str):
    tracked_info = data.get("tracked", {}).get(riot_id)
    if not tracked_info:
        return False

    history_rows = data.get("history", {}).get(riot_id, [])
    last_known_lp = tracked_info.get("last_known_lp")
    now = now_ist()
    changed = False

    candidate = None
    for row in reversed(history_rows):
        if row.get("result") != "DRAW":
            continue
        if row.get("reconciled", False):
            continue
        if parse_lp_change(row.get("lp_change")) not in {0, None}:
            continue
        lp_before = row.get("lp_before")
        if lp_before is None:
            continue
        seen_at = parse_iso_datetime(row.get("recorded_at"))
        if seen_at and (now - seen_at) < timedelta(minutes=LP_RECONCILE_DELAY_MINUTES):
            continue
        candidate = row
        break

    if candidate:
        previous_total = candidate.get("lp_total")
        candidate["reconciled"] = True
        candidate["reconciled_at"] = now.isoformat()
        if previous_total != current_total_lp:
            lp_before = candidate.get("lp_before")
            delta = current_total_lp - lp_before
            candidate["lp_change"] = f"{delta:+d}"
            candidate["lp_total"] = current_total_lp
            candidate["reconcile_reason"] = "delayed_lp_adjustment"
        tracked_info["last_known_lp"] = current_total_lp
        data["daily_lp"][riot_id][today_str] = current_total_lp
        changed = True
    elif last_known_lp is not None and last_known_lp != current_total_lp:
        tracked_info["last_known_lp"] = current_total_lp
        data["daily_lp"][riot_id][today_str] = current_total_lp
        changed = True

    if changed:
        save_data(data)
    return changed
