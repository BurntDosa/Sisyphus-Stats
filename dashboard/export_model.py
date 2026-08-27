"""Build the private, sanitized dashboard export from bot state.

This module deliberately uses allow-lists. The source state contains Discord
identifiers, PUUIDs, message links, reports, and audit data that must never
cross the dashboard boundary.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from datetime import datetime, timezone
from typing import Any

from sisyphus.ranks import format_total_lp


EXPORT_SCHEMA_VERSION = 1
SAFE_MATCH_FIELDS = (
    "date",
    "recorded_at",
    "champion",
    "champion_id",
    "position",
    "result",
    "lp_change",
    "lp_before",
    "lp_total",
    "duration",
    "kills",
    "deaths",
    "assists",
    "kda",
    "cs",
    "cs_per_min",
    "damage",
    "damage_share",
    "gold",
    "gold_share",
    "kill_participation",
    "team_kills",
    "enemy_kills",
    "champion_mastery",
    "vision",
    "wardsKilled",
    "wardsPlaced",
    "controlWardsBought",
    "backfilled",
)
FORBIDDEN_FIELD_NAMES = {
    "puuid",
    "match_id",
    "recap_url",
    "recap_channel_id",
    "recap_message_id",
    "recap_jump_url",
    "owner_id",
    "creator_id",
    "channel_id",
    "message_id",
    "reporter_id",
    "actor",
    "user_id",
    "discord_id",
    "access_token",
    "client_secret",
    "session_secret",
    "token",
    "password",
    "secret",
    "private_key",
    "reports",
    "audit",
}
MENTION_RE = re.compile(r"<(@!?|#)(\d+)>")
BARE_DISCORD_ID_RE = re.compile(r"(?<!\d)\d{15,20}(?!\d)")
URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def _text(value: object, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _public_text(value: object, limit: int = 800) -> str:
    text = _text(value, limit)
    text = MENTION_RE.sub("@member", text)
    text = BARE_DISCORD_ID_RE.sub("@member", text)
    return URL_RE.sub("", text).strip()


def _int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(value: object) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return int(parsed) if parsed.is_integer() else parsed


def _lp_delta(row: dict) -> int:
    raw = row.get("lp_change")
    if raw is None:
        return 0
    try:
        return int(str(raw).replace("+", "").strip())
    except (TypeError, ValueError):
        return 0


def _row_date(row: dict) -> date | None:
    raw = row.get("date")
    try:
        return date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def _rank_label(lp: int | None) -> str | None:
    return format_total_lp(lp) if lp is not None else None


def member_key(user_id: object, secret: str) -> str:
    """Return a stable identifier with no reversible Discord ID in it."""
    digest = hmac.new(
        secret.encode("utf-8"),
        str(user_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:20]
    return f"member_{digest}"


def sanitize_match(row: dict) -> dict:
    result: dict[str, Any] = {}
    for field in SAFE_MATCH_FIELDS:
        if field not in row:
            continue
        value = row.get(field)
        if field in {"date", "recorded_at", "champion", "position", "result", "lp_change"}:
            result[field] = _text(value, 80) if value is not None else None
        elif field == "backfilled":
            result[field] = bool(value)
        elif field == "champion_id":
            result[field] = _int(value)
        else:
            result[field] = _number(value)
    return result


def _complete_daily_series(values: dict) -> list[dict]:
    parsed: dict[date, int | float | None] = {}
    for raw_day, value in values.items():
        try:
            day = date.fromisoformat(str(raw_day))
        except (TypeError, ValueError):
            continue
        parsed[day] = _number(value)
    if not parsed:
        return []
    first, last = min(parsed), max(parsed)
    output = []
    current = first
    while current <= last:
        output.append({"date": current.isoformat(), "value": parsed.get(current)})
        current += timedelta(days=1)
    return output


def _mean(rows: list[dict], field: str) -> float | None:
    values = [float(row[field]) for row in rows if isinstance(row.get(field), (int, float))]
    return round(sum(values) / len(values), 2) if values else None


def _player_stats(rows: list[dict]) -> dict:
    wins = sum(row.get("result") == "WIN" for row in rows)
    losses = sum(row.get("result") == "LOSS" for row in rows)
    draws = sum(row.get("result") == "DRAW" for row in rows)
    decisive = wins + losses
    return {
        "games": len(rows),
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "win_rate": round(wins / decisive * 100, 1) if decisive else 0.0,
        "net_lp": sum(_lp_delta(row) for row in rows),
        "avg_kda": _mean(rows, "kda"),
        "avg_cs_per_min": _mean(rows, "cs_per_min"),
        "avg_damage": _mean(rows, "damage"),
        "avg_damage_share": _mean(rows, "damage_share"),
        "avg_gold": _mean(rows, "gold"),
        "avg_kill_participation": _mean(rows, "kill_participation"),
        "avg_vision": _mean(rows, "vision"),
        "champion_pool": len({row.get("champion") for row in rows if row.get("champion")}),
        "role_counts": dict(Counter(row.get("position") for row in rows if row.get("position"))),
        "champion_counts": dict(Counter(row.get("champion") for row in rows if row.get("champion"))),
        "backfilled_matches": sum(bool(row.get("backfilled")) for row in rows),
    }


def _latest_date(rows: list[dict]) -> str | None:
    days = [day for row in rows if (day := _row_date(row)) is not None]
    return max(days).isoformat() if days else None


def _sanitize_player(
    riot_id: str,
    info: dict,
    rows: list[dict],
    daily_values: dict,
    linked_member_key: str | None,
) -> dict:
    current_lp = _int(info.get("last_known_lp"))
    historical_lps = [_int(row.get("lp_total")) for row in rows]
    historical_lps = [lp for lp in historical_lps if lp is not None]
    peak_lp = max([*historical_lps, *([current_lp] if current_lp is not None else [])], default=None)
    clean_rows = [sanitize_match(row) for row in rows]
    clean_rows.sort(key=lambda row: (str(row.get("date") or ""), str(row.get("recorded_at") or "")))
    return {
        "riot_id": _text(riot_id, 160),
        "game_name": _text(info.get("game_name") or riot_id.split("#", 1)[0], 80),
        "tag_line": _text(info.get("tag_line"), 80),
        "member_key": linked_member_key,
        "current_lp": current_lp,
        "current_rank": _rank_label(current_lp),
        "peak_lp": peak_lp,
        "peak_rank": _rank_label(peak_lp),
        "stats": _player_stats(rows),
        "daily_lp": _complete_daily_series(daily_values),
        "matches": clean_rows,
        "last_match_date": _latest_date(rows),
        "history_backfilled": bool(info.get("history_backfilled")),
    }


def _activity(history: dict[str, list[dict]]) -> list[dict]:
    by_day: dict[date, dict] = defaultdict(lambda: {"games": 0, "wins": 0, "losses": 0, "draws": 0, "lp_change": 0, "players": set()})
    for riot_id, rows in history.items():
        for row in rows:
            day = _row_date(row)
            if not day:
                continue
            entry = by_day[day]
            entry["games"] += 1
            entry["players"].add(riot_id)
            result = row.get("result")
            if result == "WIN":
                entry["wins"] += 1
            elif result == "LOSS":
                entry["losses"] += 1
            elif result == "DRAW":
                entry["draws"] += 1
            entry["lp_change"] += _lp_delta(row)
    if not by_day:
        return []
    output = []
    current = min(by_day)
    while current <= max(by_day):
        entry = by_day.get(current)
        output.append(
            {
                "date": current.isoformat(),
                "games": entry["games"] if entry else 0,
                "wins": entry["wins"] if entry else 0,
                "losses": entry["losses"] if entry else 0,
                "draws": entry["draws"] if entry else 0,
                "lp_change": entry["lp_change"] if entry else 0,
                "active_players": len(entry["players"]) if entry else 0,
            }
        )
        current += timedelta(days=1)
    return output


def _display_name(user_id: object, names: dict[str, str], key: str) -> str:
    return _public_text(names.get(str(user_id)) or f"Member {key[-6:]}", 80)


def _wallets(betting: dict, names: dict[str, str], secret: str) -> list[dict]:
    output = []
    for raw_user_id, wallet in (betting.get("wallets") or {}).items():
        key = member_key(raw_user_id, secret)
        wins = _int(wallet.get("wins")) or 0
        losses = _int(wallet.get("losses")) or 0
        decisive = wins + losses
        output.append(
            {
                "member_key": key,
                "display_name": _display_name(raw_user_id, names, key),
                "balance": _int(wallet.get("balance")) or 0,
                "reserved": _int(wallet.get("reserved")) or 0,
                "lifetime_profit": _int(wallet.get("lifetime_profit")) or 0,
                "lifetime_wagered": _int(wallet.get("lifetime_wagered")) or 0,
                "bets_placed": _int(wallet.get("bets_placed")) or 0,
                "wins": wins,
                "losses": losses,
                "voids": _int(wallet.get("voids")) or 0,
                "win_rate": round(wins / decisive * 100, 1) if decisive else 0.0,
                "current_streak": _int(wallet.get("current_streak")) or 0,
                "best_streak": _int(wallet.get("best_streak")) or 0,
            }
        )
    return sorted(output, key=lambda item: (-item["balance"], -item["lifetime_profit"], item["display_name"].lower()))


def _markets_and_bets(betting: dict, names: dict[str, str], secret: str) -> tuple[list[dict], list[dict]]:
    markets = betting.get("markets") or {}
    bets_by_market = betting.get("bets") or {}
    clean_markets = []
    clean_bets = []
    for raw_market_id, market in markets.items():
        market_id = _text(market.get("market_id") or raw_market_id, 80)
        market_bets = bets_by_market.get(raw_market_id) or bets_by_market.get(market_id) or {}
        result_counts = Counter()
        for raw_user_id, bet in market_bets.items():
            key = member_key(raw_user_id, secret)
            outcome = _text(bet.get("outcome"), 30) or None
            result_counts[outcome or "PENDING"] += 1
            clean_bets.append(
                {
                    "market_id": market_id,
                    "market_title": _public_text(market.get("title") or "Market", 180),
                    "member_key": key,
                    "display_name": _display_name(raw_user_id, names, key),
                    "side": _text(bet.get("side"), 20),
                    "stake": _int(bet.get("stake")) or 0,
                    "odds": _number(bet.get("odds")),
                    "use_insurance": bool(bet.get("use_insurance")),
                    "status": _text(bet.get("status"), 30),
                    "result": _text(bet.get("result"), 30) or None,
                    "outcome": outcome,
                    "placed_at": _text(bet.get("placed_at"), 80) or None,
                    "settled_at": _text(bet.get("settled_at"), 80) or None,
                }
            )
        clean_markets.append(
            {
                "market_id": market_id,
                "tracked_key": _text(market.get("tracked_key"), 240),
                "title": _public_text(market.get("title") or "Market", 180),
                "status": _text(market.get("status"), 30),
                "created_at": _text(market.get("created_at"), 80) or None,
                "lock_at": _text(market.get("lock_at"), 80) or None,
                "timeout_at": _text(market.get("timeout_at"), 80) or None,
                "resolved_at": _text(market.get("resolved_at"), 80) or None,
                "win_prob": _number(market.get("win_prob")),
                "win_odds": _number(market.get("win_odds")),
                "lose_odds": _number(market.get("lose_odds")),
                "total_staked": _int(market.get("total_staked")) or 0,
                "result": _text(market.get("result"), 20) or None,
                "winner_side": _text(market.get("winner_side"), 20) or None,
                "winner_count": _int(market.get("winner_count")) or 0,
                "winner_stake": _int(market.get("winner_stake")) or 0,
                "bet_count": len(market_bets),
                "outcomes": dict(result_counts),
            }
        )
    clean_markets.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    clean_bets.sort(key=lambda item: str(item.get("placed_at") or ""), reverse=True)
    return clean_markets, clean_bets


def _goal_progress(goal: dict, history: dict[str, list[dict]]) -> int:
    metric = goal.get("metric")
    try:
        start = date.fromisoformat(str(goal.get("week")))
    except (TypeError, ValueError):
        return 0
    days = {start + timedelta(days=offset) for offset in range(7)}
    rows = [row for player_rows in history.values() for row in player_rows if _row_date(row) in days]
    if metric == "wins":
        return sum(row.get("result") == "WIN" for row in rows)
    if metric == "games":
        return len(rows)
    if metric == "unique_champions":
        return len({row.get("champion") for row in rows if row.get("champion")})
    if metric == "positive_lp_days":
        totals: dict[str, int] = defaultdict(int)
        for row in rows:
            totals[str(row.get("date"))] += _lp_delta(row)
        return sum(total > 0 for total in totals.values())
    if metric == "streak":
        best = 0
        for player_rows in history.values():
            streak = 0
            for row in sorted(player_rows, key=lambda item: str(item.get("date") or "")):
                if _row_date(row) not in days:
                    continue
                if row.get("result") == "WIN":
                    streak += 1
                    best = max(best, streak)
                elif row.get("result") == "LOSS":
                    streak = 0
        return best
    return 0


def _community(community: dict, history: dict[str, list[dict]]) -> dict:
    records = []
    for record in (community.get("records") or {}).values():
        records.append(
            {
                "label": _public_text(record.get("label"), 120),
                "value": _number(record.get("value")),
                "player": _public_text(record.get("player"), 100),
                "champion": _public_text(record.get("champion"), 80),
                "date": _text(record.get("date"), 30) or None,
            }
        )

    milestones = []
    for riot_id, events in (community.get("milestones") or {}).items():
        clean_events = []
        for event in events if isinstance(events, list) else []:
            clean_events.append(
                {
                    "key": _text(event.get("key"), 120),
                    "label": _public_text(event.get("label"), 180),
                    "date": _text(event.get("date"), 30) or None,
                    "created_at": _text(event.get("created_at"), 80) or None,
                }
            )
        milestones.append({"riot_id": _text(riot_id, 160), "events": clean_events})

    memories = []
    for riot_id, bucket in (community.get("memories") or {}).items():
        clean_memories = []
        for memory in bucket.values() if isinstance(bucket, dict) else []:
            clean_memories.append(
                {
                    "name": _public_text(memory.get("name"), 120),
                    "date": _text(memory.get("date"), 30) or None,
                    "champion": _text(memory.get("champion"), 80) or None,
                    "role": _text(memory.get("role"), 40) or None,
                    "result": _text(memory.get("result"), 20) or None,
                    "lp_change": _text(memory.get("lp_change"), 20) or None,
                    "lp_before": _int(memory.get("lp_before")),
                    "lp_total": _int(memory.get("lp_total")),
                    "kills": _int(memory.get("kills")),
                    "deaths": _int(memory.get("deaths")),
                    "assists": _int(memory.get("assists")),
                    "duration": _int(memory.get("duration")),
                    "reason": _public_text(memory.get("reason"), 300) or None,
                    "created_at": _text(memory.get("created_at"), 80) or None,
                }
            )
        memories.append({"riot_id": _text(riot_id, 160), "items": clean_memories})

    weekly = [
        {"week": _text(key, 30), "summary": _public_text(value)}
        for key, value in (community.get("weekly_recaps") or {}).items()
    ]
    monthly = []
    for key, value in (community.get("monthly_recaps") or {}).items():
        if not isinstance(value, dict):
            continue
        monthly.append(
            {
                "month": _text(key, 30),
                "public_posted_at": _text(value.get("public_posted_at"), 80) or None,
                "games": _int(value.get("games")) or 0,
                "skipped": _text(value.get("skipped"), 60) or None,
            }
        )

    goals = []
    for goal in (community.get("squad_goals") or {}).values():
        if not isinstance(goal, dict):
            continue
        target = _int(goal.get("target")) or 0
        progress = _goal_progress(goal, history)
        goals.append(
            {
                "name": _public_text(goal.get("name"), 120),
                "metric": _text(goal.get("metric"), 60),
                "target": target,
                "progress": progress,
                "week": _text(goal.get("week"), 30),
                "created_at": _text(goal.get("created_at"), 80) or None,
            }
        )
    return {
        "records": sorted(records, key=lambda item: str(item.get("label") or "")),
        "milestones": milestones,
        "memories": memories,
        "weekly_summaries": sorted(weekly, key=lambda item: item["week"], reverse=True),
        "monthly_summaries": sorted(monthly, key=lambda item: item["month"], reverse=True),
        "squad_goals": goals,
        "historical_events": [
            {
                "key": _text(event.get("key"), 120),
                "date": _text(event.get("date"), 30) or None,
                "label": _public_text(event.get("label"), 220),
                "kind": _text(event.get("kind"), 60),
                "created_at": _text(event.get("created_at"), 80) or None,
            }
            for event in (community.get("historical_events") or [])
            if isinstance(event, dict)
        ],
    }


def build_export(
    source: dict,
    *,
    export_secret: str,
    member_names: dict[str, str] | None = None,
    generated_at: datetime | None = None,
    source_version: str | None = None,
) -> dict:
    """Return a JSON-serializable export containing only dashboard-safe data."""
    if not export_secret or len(export_secret) < 24:
        raise ValueError("DASHBOARD_EXPORT_SECRET must be at least 24 characters")
    member_names = {str(key): _text(value, 80) for key, value in (member_names or {}).items()}
    tracked = source.get("tracked") or {}
    history = source.get("history") or {}
    daily_lp = source.get("daily_lp") or {}
    links = source.get("links") or {}
    link_by_riot: dict[str, str] = {}
    for raw_user_id, riot_id in links.items():
        if str(raw_user_id).isdigit() and riot_id in tracked:
            link_by_riot[str(riot_id)] = member_key(raw_user_id, export_secret)

    players = []
    all_rows: list[dict] = []
    for riot_id, info in tracked.items():
        rows = list(history.get(riot_id) or [])
        all_rows.extend(rows)
        players.append(
            _sanitize_player(
                str(riot_id),
                info if isinstance(info, dict) else {},
                rows,
                daily_lp.get(riot_id) or {},
                link_by_riot.get(str(riot_id)),
            )
        )
    players.sort(key=lambda item: item["game_name"].lower())
    wins = sum(row.get("result") == "WIN" for row in all_rows)
    losses = sum(row.get("result") == "LOSS" for row in all_rows)
    draws = sum(row.get("result") == "DRAW" for row in all_rows)
    markets, bets = _markets_and_bets(source.get("betting") or {}, member_names, export_secret)
    wallets = _wallets(source.get("betting") or {}, member_names, export_secret)
    generated_at = generated_at or datetime.now(timezone.utc)
    latest_days = [day for row in all_rows if (day := _row_date(row)) is not None]
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "source_version": source_version or "unknown",
        "summary": {
            "players": len(players),
            "games": len(all_rows),
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "net_lp": sum(_lp_delta(row) for row in all_rows),
            "latest_match_date": max(latest_days).isoformat() if latest_days else None,
            "active_markets": sum(market.get("status") in {"open", "locked"} for market in markets),
            "markets": len(markets),
            "bets": len(bets),
        },
        "players": players,
        "activity": _activity(history),
        "betting": {
            "wallets": wallets,
            "markets": markets,
            "bets": bets,
        },
        "community": _community(source.get("community") or {}, history),
    }


def forbidden_keys(value: object) -> set[str]:
    """Find forbidden key names recursively for offline safety checks."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_FIELD_NAMES:
                found.add(str(key))
            found.update(forbidden_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(forbidden_keys(item))
    return found
