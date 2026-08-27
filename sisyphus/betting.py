"""Betting state, wallet logic, market lifecycle, and market UI."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta

import discord

from .config import MARKET_ROLE_ID
from .outcome import canonical_outcome
from .ranks import tier_emoji
from .state import data, save_data
from .utils import now_ist, today_ist

INITIAL_BALANCE = 5000
DAILY_FLOOR = 300
MIN_STAKE = 10
MAX_STAKE = 500
INSURANCE_RESET_WEEKDAY = 0  # Monday
MARKET_TIMEOUT_MINUTES = 90
DEFAULT_LOCK_MINUTES = 2

_user_locks: dict[str, asyncio.Lock] = defaultdict(lambda: asyncio.Lock())
_market_locks: dict[str, asyncio.Lock] = defaultdict(lambda: asyncio.Lock())


def _betting():
    betting = data.setdefault("betting", {})
    betting.setdefault("wallets", {})
    betting.setdefault("markets", {})
    betting.setdefault("bets", {})
    betting.setdefault("audit", [])
    betting.setdefault("meta", {})
    return betting


def _wallets():
    return _betting()["wallets"]


def _markets():
    return _betting()["markets"]


def _bets():
    return _betting()["bets"]


def _audit():
    return _betting()["audit"]


def _meta():
    return _betting()["meta"]


def _user_key(user_id) -> str:
    return str(user_id)


def _today_key() -> str:
    return str(today_ist())


def _week_key() -> str:
    y, w, _ = today_ist().isocalendar()
    return f"{y}-W{w:02d}"


def _default_wallet():
    return {
        "balance": INITIAL_BALANCE,
        "reserved": 0,
        "lifetime_profit": 0,
        "lifetime_wagered": 0,
        "bets_placed": 0,
        "wins": 0,
        "losses": 0,
        "voids": 0,
        "current_streak": 0,
        "best_streak": 0,
        "weekly_insurance_tokens": 1,
        "insurance_week": _week_key(),
        "last_floor_day": _today_key(),
        "created_at": now_ist().isoformat(),
        "last_seen_at": now_ist().isoformat(),
    }


def _touch_wallet(wallet: dict):
    wallet["last_seen_at"] = now_ist().isoformat()


def _record_audit(
    user_id,
    event_type: str,
    amount_delta: int,
    before: int,
    after: int,
    market_id: str | None = None,
    actor: str = "SYSTEM",
    reason: str | None = None,
):
    _audit().append(
        {
            "timestamp": now_ist().isoformat(),
            "user_id": str(user_id),
            "event_type": event_type,
            "amount_delta": amount_delta,
            "balance_before": before,
            "balance_after": after,
            "market_id": market_id,
            "actor": actor,
            "reason": reason,
        }
    )


def _ensure_wallet(user_id):
    key = _user_key(user_id)
    wallets = _wallets()
    if key not in wallets:
        wallets[key] = _default_wallet()
        save_data(data)
        return wallets[key]

    wallet = wallets[key]
    for k, v in _default_wallet().items():
        wallet.setdefault(k, v)
    _touch_wallet(wallet)
    return wallet


def _refresh_daily_floor(wallet: dict, user_id):
    today_key = _today_key()
    if wallet.get("last_floor_day") != today_key and wallet["balance"] < DAILY_FLOOR:
        before = wallet["balance"]
        wallet["balance"] = DAILY_FLOOR
        wallet["last_floor_day"] = today_key
        _record_audit(
            user_id,
            "DAILY_FLOOR_REFRESH",
            DAILY_FLOOR - before,
            before,
            wallet["balance"],
        )
        return True
    if wallet.get("last_floor_day") != today_key:
        wallet["last_floor_day"] = today_key
        return True
    return False


def _grant_weekly_insurance(wallet: dict, user_id):
    week_key = _week_key()
    if wallet.get("insurance_week") != week_key:
        wallet["insurance_week"] = week_key
        wallet["weekly_insurance_tokens"] = 1
        _record_audit(
            user_id,
            "WEEKLY_INSURANCE_GRANT",
            0,
            wallet["balance"],
            wallet["balance"],
        )
        return True
    return False


def _market_bets(market_id: str):
    return _bets().setdefault(market_id, {})


def _user_display_name(user_id: str):
    try:
        return f"<@{int(user_id)}>"
    except Exception:
        return str(user_id)


def _win_rate(wallet: dict) -> float:
    bets = int(wallet.get("bets_placed", 0))
    if bets <= 0:
        return 0.0
    return wallet.get("wins", 0) / bets


def _period_start(range_name: str | None):
    range_name = (range_name or "all").lower()
    now = now_ist()
    if range_name == "today":
        return datetime.combine(today_ist(), datetime.min.time(), tzinfo=now.tzinfo)
    if range_name == "week":
        return now - timedelta(days=7)
    if range_name == "month":
        return now - timedelta(days=30)
    return None


def _audit_delta_since(user_id: str, since_dt):
    total = 0
    for row in _audit():
        if row.get("user_id") != str(user_id):
            continue
        if since_dt is not None:
            try:
                ts = datetime.fromisoformat(row.get("timestamp"))
            except Exception:
                continue
            if ts < since_dt:
                continue
        total += int(row.get("amount_delta") or 0)
    return total


def _biggest_profit_for_user(user_id: str):
    best = 0
    for market_id, bets in _bets().items():
        bet = bets.get(_user_key(user_id))
        if not bet or bet.get("status") != "settled":
            continue
        if bet.get("outcome") != "WIN":
            continue
        stake = int(bet.get("stake", 0))
        odds = float(bet.get("odds", 1.0))
        profit = int(round(stake * odds)) - stake
        best = max(best, profit)
    return best


def _user_market_bets(user_id: str):
    out = []
    key = _user_key(user_id)
    for market_id, bets in _bets().items():
        bet = bets.get(key)
        if not bet:
            continue
        market = _markets().get(market_id)
        out.append((market_id, market, bet))
    return out


def _created_sort_key(wallet: dict):
    raw = wallet.get("created_at")
    try:
        return datetime.fromisoformat(raw) if raw else datetime.max
    except Exception:
        return datetime.max


def _tracked_subjects(tracked_key: str) -> frozenset[str]:
    return frozenset(
        part.strip()
        for part in str(tracked_key or "").split(" & ")
        if part.strip()
    )


def _same_tracked_subject(left: str, right: str) -> bool:
    return _tracked_subjects(left) == _tracked_subjects(right)


def _overlapping_tracked_subject(left: str, right: str) -> bool:
    return bool(_tracked_subjects(left) & _tracked_subjects(right))


def _market_player_label(tracked_key: str) -> str:
    names = []
    for part in _tracked_subjects(tracked_key):
        names.append(part.split("#", 1)[0] if "#" in part else part)
    return " & ".join(sorted(names)) or str(tracked_key)


def _refresh_live_room_for_market(market_id: str) -> None:
    market = get_market(market_id)
    if not market:
        return
    try:
        from .live import refresh_rooms_for_market

        refresh_rooms_for_market(market.get("tracked_key", ""))
    except Exception as exc:
        print(f"[betting] live-room refresh failed for {market_id}: {exc}")


async def refresh_daily_floor_if_needed(user_id):
    key = _user_key(user_id)
    lock = _user_locks[key]
    async with lock:
        wallet = _ensure_wallet(user_id)
        if _refresh_daily_floor(wallet, user_id):
            save_data(data)
        return wallet


async def grant_weekly_insurance_if_needed(user_id):
    key = _user_key(user_id)
    lock = _user_locks[key]
    async with lock:
        wallet = _ensure_wallet(user_id)
        if _grant_weekly_insurance(wallet, user_id):
            save_data(data)
        return wallet


async def get_wallet(user_id):
    await refresh_daily_floor_if_needed(user_id)
    await grant_weekly_insurance_if_needed(user_id)
    wallet = _ensure_wallet(user_id)
    return wallet


def _active_market_conflict(tracked_key: str, ignore_market_id: str | None = None):
    for market_id, market in _markets().items():
        if ignore_market_id and market_id == ignore_market_id:
            continue
        mk = market.get("tracked_key", "")
        if not _overlapping_tracked_subject(mk, tracked_key):
            continue
        if market.get("status") in {"open", "locked"}:
            return market
    return None


def _market_counter() -> int:
    meta = _meta()
    meta["market_counter"] = int(meta.get("market_counter", 0)) + 1
    return meta["market_counter"]


def _clamp_probability(prob: float) -> float:
    return max(0.30, min(0.70, prob))


def compute_odds(win_prob: float) -> tuple[float, float]:
    p = _clamp_probability(win_prob)
    win_odds = 0.95 / p
    lose_odds = 0.95 / (1 - p)
    return round(win_odds, 2), round(lose_odds, 2)


async def calculate_odds_breakdown(
    riot_id: str, champion: str | None = None, session = None
) -> dict:
    """Computes the win probability and breakdown according to the odds calculation formula."""
    import aiohttp
    if " & " in riot_id:
        keys = [k.strip() for k in riot_id.split("&")]
        champs = [c.strip() for c in champion.split("&")] if champion and " & " in champion else [None, None]
        if len(champs) < len(keys):
            champs = champs + [None] * (len(keys) - len(champs))
            
        local_session = session or aiohttp.ClientSession()
        try:
            breakdowns = []
            for k, c in zip(keys, champs):
                bd = await calculate_odds_breakdown(k, c, local_session)
                breakdowns.append(bd)
        finally:
            if session is None:
                await local_session.close()
                
        p_raw = sum(b["p_raw"] for b in breakdowns) / len(breakdowns) + 0.02
        p_final = max(0.30, min(0.70, p_raw))
        win_odds = round(0.95 / p_final, 2)
        lose_odds = round(0.95 / (1.0 - p_final), 2)
        
        return {
            "is_duo": True,
            "breakdown1": breakdowns[0],
            "breakdown2": breakdowns[1],
            "p_raw": p_raw,
            "p_final": p_final,
            "win_odds": win_odds,
            "lose_odds": lose_odds,
            "champion_name": champion,
        }

    history_all = data.get("history", {}).get(riot_id, [])
    recent_games = [g for g in history_all if g.get("result") in ("WIN", "LOSS")]
    
    # 1. wr_recent (last 10 games, WIN/LOSS only)
    recent_games_10 = recent_games[-10:]
    if recent_games_10:
        wins_recent = sum(1 for g in recent_games_10 if g.get("result") == "WIN")
        wr_recent = wins_recent / len(recent_games_10)
    else:
        wr_recent = 0.5

    # 2. wr_all (all games with WIN/LOSS)
    total_games = len(recent_games)
    if total_games > 0:
        total_wins = sum(1 for g in recent_games if g.get("result") == "WIN")
        wr_all = total_wins / total_games
    else:
        wr_all = 0.5

    # 3. streak_mod (unbroken streak of WIN or LOSS from end of history, capped at ±0.08)
    streak_count = 0
    streak_type = None
    streak_mod = 0.0
    if recent_games:
        streak_type = recent_games[-1].get("result")
        for g in reversed(recent_games):
            if g.get("result") == streak_type:
                streak_count += 1
            else:
                break
        if streak_type == "WIN":
            streak_mod = min(streak_count * 0.02, 0.08)
        elif streak_type == "LOSS":
            streak_mod = max(streak_count * -0.02, -0.08)
            streak_mod = round(streak_mod, 2)
            
    # 4. champ_mod (only if player has 5 or more games on champion, range ±5%)
    champ_mod = 0.0
    champ_wins = 0
    champ_total = 0
    if champion:
        champ_games = [g for g in history_all if g.get("champion") == champion and g.get("result") in ("WIN", "LOSS")]
        champ_total = len(champ_games)
        if champ_total >= 5:
            champ_wins = sum(1 for g in champ_games if g.get("result") == "WIN")
            champ_wr = champ_wins / champ_total
            champ_mod = (champ_wr - 0.50) * 0.10

    # 5. lp_mod (7-day LP trend from daily_lp, +0.03 if >+50, -0.03 if <-50, else 0)
    lp_mod = 0.0
    lp_history = data.get("daily_lp", {}).get(riot_id, {})
    today_date = today_ist()
    today_str = str(today_date)
    seven_ago_str = str(today_date - timedelta(days=7))
    today_lp = lp_history.get(today_str)
    if today_lp is None:
        today_lp = data.get("tracked", {}).get(riot_id, {}).get("last_known_lp")
    seven_lp = lp_history.get(seven_ago_str)
    
    lp_trend = None
    if today_lp is not None and seven_lp is not None:
        lp_trend = today_lp - seven_lp
        if lp_trend > 50:
            lp_mod = 0.03
        elif lp_trend < -50:
            lp_mod = -0.03

    # 6. Champion Mastery Modifier
    mastery_mod = 0.0
    mastery_points = 0
    if champion:
        local_session = session or aiohttp.ClientSession()
        try:
            from .ddragon import get_champion_id
            champion_id = await get_champion_id(local_session, champion)
            puuid = data.get("tracked", {}).get(riot_id, {}).get("puuid")
            if puuid and champion_id:
                from .opgg import get_champion_mastery
                mastery_points = await get_champion_mastery(local_session, puuid, champion_id)
                if mastery_points >= 100000:
                    mastery_mod = 0.03
                elif mastery_points == 0:
                    mastery_mod = -0.03
        except Exception as e:
            print(f"[betting] failed to calculate mastery modifier: {e}")
        finally:
            if session is None:
                await local_session.close()

    # 7. p_raw
    p_raw = (0.50 * wr_recent) + (0.30 * wr_all) + 0.10 + streak_mod + champ_mod + lp_mod + mastery_mod
    
    # 8. p_final
    p_final = max(0.30, min(0.70, p_raw))

    # 9. WIN_odds, LOSE_odds
    win_odds = round(0.95 / p_final, 2)
    lose_odds = round(0.95 / (1.0 - p_final), 2)
    
    return {
        "wr_recent": wr_recent,
        "wr_all": wr_all,
        "streak_count": streak_count,
        "streak_type": streak_type,
        "streak_mod": streak_mod,
        "champion_name": champion,
        "champ_wins": champ_wins,
        "champ_total": champ_total,
        "champ_mod": champ_mod,
        "lp_trend": lp_trend,
        "lp_mod": lp_mod,
        "mastery_points": mastery_points,
        "mastery_mod": mastery_mod,
        "p_raw": p_raw,
        "p_final": p_final,
        "win_odds": win_odds,
        "lose_odds": lose_odds,
    }


async def create_market(
    tracked_key: str,
    title: str,
    creator_id,
    champion: str | None = None,
    win_prob: float | None = None,
    rationale: str = "",
    channel_id: int | None = None,
    message_id: int | None = None,
    lock_minutes: int = DEFAULT_LOCK_MINUTES,
):
    conflict = _active_market_conflict(tracked_key)
    if conflict:
        return None, f"An active market already exists for {tracked_key}."

    if win_prob is not None:
        win_odds, lose_odds = compute_odds(win_prob)
        breakdown = {
            "p_raw": win_prob,
            "p_final": win_prob,
            "win_odds": win_odds,
            "lose_odds": lose_odds,
            "rationale": rationale,
        }
    else:
        breakdown = await calculate_odds_breakdown(tracked_key, champion)
        win_prob = breakdown["p_final"]
        win_odds = breakdown["win_odds"]
        lose_odds = breakdown["lose_odds"]

    market_id = f"m{_market_counter()}"
    now = now_ist()
    market = {
        "market_id": market_id,
        "tracked_key": tracked_key,
        "title": title,
        "creator_id": str(creator_id),
        "channel_id": channel_id,
        "message_id": message_id,
        "status": "open",
        "created_at": now.isoformat(),
        "lock_at": (now + timedelta(minutes=lock_minutes)).isoformat(),
        "timeout_at": (now + timedelta(minutes=MARKET_TIMEOUT_MINUTES)).isoformat(),
        "win_prob": round(win_prob, 3),
        "win_odds": win_odds,
        "lose_odds": lose_odds,
        "breakdown": breakdown,
        "rationale": rationale,
        "result": None,
        "resolved_at": None,
        "settle_reason": None,
        "total_staked": 0,
        "winner_stake": 0,
        "winner_count": 0,
        "biggest_profit": 0,
        "biggest_profit_user": None,
    }
    _markets()[market_id] = market
    _bets()[market_id] = {}
    save_data(data)
    return market, None


def get_market(market_id: str):
    return _markets().get(market_id)


def list_open_markets():
    return [
        market
        for market in _markets().values()
        if market.get("status") in {"open", "locked"}
    ]


def get_market_for_tracked_key(tracked_key: str):
    for market in _markets().values():
        same_subject = _same_tracked_subject(
            market.get("tracked_key", ""), tracked_key
        )
        if same_subject and market.get("status") in {"open", "locked"}:
            return market
    return None


def get_conflicting_market_for_tracked_key(tracked_key: str):
    return _active_market_conflict(tracked_key)


def get_user_bet(user_id, market_id: str):
    return _bets().get(market_id, {}).get(_user_key(user_id))


def _place_bet_locked(
    user_id,
    market_id: str,
    side: str,
    stake: int,
    use_insurance: bool,
    *,
    all_in: bool = False,
):
    market = get_market(market_id)
    if not market:
        return None, "Market not found."

    key = _user_key(user_id)
    wallet = _ensure_wallet(user_id)
    if _refresh_daily_floor(wallet, user_id) or _grant_weekly_insurance(wallet, user_id):
        save_data(data)
    if get_user_bet(user_id, market_id):
        return None, "You already have a bet on this market."
    if wallet["balance"] < stake:
        return None, "Insufficient balance."

    insurance_available = wallet.get("weekly_insurance_tokens", 0) > 0
    if use_insurance and not insurance_available:
        return None, "No insurance tokens available."
    if use_insurance:
        wallet["weekly_insurance_tokens"] -= 1

    before = wallet["balance"]
    now = now_ist()
    wallet["balance"] -= stake
    wallet["reserved"] += stake
    wallet["bets_placed"] += 1
    wallet["lifetime_wagered"] += stake
    _market_bets(market_id)[key] = {
        "user_id": key,
        "side": side,
        "stake": stake,
        "odds": market["win_odds"] if side == "WIN" else market["lose_odds"],
        "use_insurance": bool(use_insurance),
        "all_in": bool(all_in),
        "placed_at": now.isoformat(),
        "status": "active",
    }
    market["total_staked"] += stake
    _record_audit(
        user_id,
        "BET_PLACED",
        -stake,
        before,
        wallet["balance"],
        market_id=market_id,
    )
    save_data(data)
    return _market_bets(market_id)[key], None


async def place_bet(user_id, market_id: str, side: str, stake, use_insurance: bool = False):
    side = str(side or "").upper()
    if side not in {"WIN", "LOSE"}:
        return None, "Side must be WIN or LOSE."
    try:
        stake = int(stake)
    except (TypeError, ValueError):
        return None, "Stake must be a number."
    if stake < MIN_STAKE or stake > MAX_STAKE:
        return None, f"Stake must be between {MIN_STAKE} and {MAX_STAKE}."

    market = get_market(market_id)
    if not market:
        return None, "Market not found."

    if market.get("status") not in {"open", "locked"}:
        return None, f"Market is {market.get('status', 'closed')}."

    now = now_ist()
    lock_at = datetime.fromisoformat(market["lock_at"])
    if market["status"] == "open" and now >= lock_at:
        return None, "Market is locked."

    key = _user_key(user_id)
    async with _user_locks[key]:
        bet, err = _place_bet_locked(user_id, market_id, side, stake, use_insurance)
    if bet:
        _refresh_live_room_for_market(market_id)
    return bet, err


async def place_all_in_bet(user_id, market_id: str, side: str):
    side = str(side or "").upper()
    if side not in {"WIN", "LOSE"}:
        return None, "Side must be WIN or LOSE."

    market = get_market(market_id)
    if not market:
        return None, "Market not found."

    if market.get("status") not in {"open", "locked"}:
        return None, f"Market is {market.get('status', 'closed')}."

    now = now_ist()
    lock_at = datetime.fromisoformat(market["lock_at"])
    if market["status"] == "open" and now >= lock_at:
        return None, "Market is locked."

    key = _user_key(user_id)
    async with _user_locks[key]:
        wallet = _ensure_wallet(user_id)
        if _refresh_daily_floor(wallet, user_id) or _grant_weekly_insurance(wallet, user_id):
            save_data(data)
        stake = int(wallet.get("balance", 0))
        if stake < MIN_STAKE:
            return None, f"You need at least {MIN_STAKE} pts to go all-in."
        bet, err = _place_bet_locked(
            user_id,
            market_id,
            side,
            stake,
            use_insurance=False,
            all_in=True,
        )
    if bet:
        _refresh_live_room_for_market(market_id)
    return bet, err


def _cancel_bet_locked(user_id, market_id: str):
    market = get_market(market_id)
    if not market:
        return None, "Market not found."
    bet = get_user_bet(user_id, market_id)
    if not bet or bet.get("status") != "active":
        return None, "No active bet on that market."
    if market.get("status") not in {"open", "locked"}:
        return None, "Bet cancellation is only available before settlement."

    wallet = _ensure_wallet(user_id)
    if _refresh_daily_floor(wallet, user_id) or _grant_weekly_insurance(wallet, user_id):
        save_data(data)
    stake = int(bet["stake"])
    before = wallet["balance"]
    wallet["balance"] += stake
    wallet["reserved"] -= stake
    bet["status"] = "cancelled"
    bet["cancelled_at"] = now_ist().isoformat()
    wallet["voids"] += 1
    _record_audit(
        user_id,
        "BET_REFUNDED",
        stake,
        before,
        wallet["balance"],
        market_id=market_id,
    )
    save_data(data)
    return bet, None


async def cancel_bet(user_id, market_id: str):
    key = _user_key(user_id)
    async with _user_locks[key]:
        bet, err = _cancel_bet_locked(user_id, market_id)
    if bet:
        _refresh_live_room_for_market(market_id)
    return bet, err


async def edit_bet(user_id, market_id: str, side: str, stake, use_insurance: bool = False):
    side = str(side or "").upper()
    if side not in {"WIN", "LOSE"}:
        return None, "Side must be WIN or LOSE."
    try:
        stake = int(stake)
    except (TypeError, ValueError):
        return None, "Stake must be a number."
    if stake < MIN_STAKE or stake > MAX_STAKE:
        return None, f"Stake must be between {MIN_STAKE} and {MAX_STAKE}."

    market = get_market(market_id)
    if not market:
        return None, "Market not found."
    if market.get("status") not in {"open", "locked"}:
        return None, f"Market is {market.get('status', 'closed')}."
    if market.get("status") == "open" and now_ist() >= datetime.fromisoformat(market["lock_at"]):
        return None, "Market is locked."

    key = _user_key(user_id)
    async with _user_locks[key]:
        existing = get_user_bet(user_id, market_id)
        if not existing or existing.get("status") != "active":
            return None, "No active bet on that market."
        wallet = _ensure_wallet(user_id)
        if _refresh_daily_floor(wallet, user_id) or _grant_weekly_insurance(wallet, user_id):
            save_data(data)

        old_stake = int(existing["stake"])
        old_used_insurance = bool(existing.get("use_insurance"))
        old_all_in = bool(existing.get("all_in"))
        if old_all_in and use_insurance:
            return None, "All-in bets cannot use insurance."
        before = wallet["balance"]
        wallet["balance"] += old_stake
        wallet["reserved"] -= old_stake
        if old_used_insurance:
            wallet["weekly_insurance_tokens"] += 1
        existing["status"] = "edited"
        existing["edited_at"] = now_ist().isoformat()
        del _market_bets(market_id)[key]
        market["total_staked"] -= old_stake
        _record_audit(
            user_id,
            "BET_REFUNDED",
            old_stake,
            before,
            wallet["balance"],
            market_id=market_id,
        )
        save_data(data)
        bet, err = _place_bet_locked(user_id, market_id, side, stake, use_insurance)
    if bet:
        _refresh_live_room_for_market(market_id)
    return bet, err


def _apply_streak(wallet: dict, outcome: str):
    if outcome == "WIN":
        wallet["wins"] += 1
        wallet["current_streak"] += 1
        wallet["best_streak"] = max(wallet["best_streak"], wallet["current_streak"])
    elif outcome == "LOSS":
        wallet["losses"] += 1
        wallet["current_streak"] = 0
    elif outcome == "VOID":
        wallet["voids"] += 1


async def settle_market(market_id: str, result: str, reason: str | None = None):
    market = get_market(market_id)
    if not market:
        return None, "Market not found."
    if market.get("status") in {"settled", "void"}:
        return market, None

    result = str(result or "").upper()
    if result not in {"WIN", "LOSE", "VOID"}:
        return None, "Invalid market result."

    market["status"] = "void" if result == "VOID" else "settled"
    market["result"] = result
    market["resolved_at"] = now_ist().isoformat()
    market["settle_reason"] = reason

    bets = _bets().get(market_id, {})
    winner_side = None
    if result in {"WIN", "LOSE"}:
        winner_side = result
    market["winner_side"] = winner_side

    biggest_profit = 0
    biggest_profit_user = None
    winner_count = 0
    winner_stake = 0

    for user_id, bet in bets.items():
        if bet.get("status") != "active":
            continue
        wallet = await get_wallet(user_id)
        stake = int(bet["stake"])
        odds = float(bet["odds"])
        before = wallet["balance"]
        wallet["reserved"] -= stake
        delta = 0
        outcome = "VOID"

        if result == "VOID":
            wallet["balance"] += stake
            if bet.get("use_insurance"):
                wallet["weekly_insurance_tokens"] += 1
            delta = stake
            outcome = "VOID"
        else:
            side_won = bet["side"] == result
            if side_won:
                payout = int(round(stake * odds))
                profit = payout - stake
                wallet["balance"] += payout
                wallet["lifetime_profit"] += profit
                if bet.get("use_insurance"):
                    wallet["weekly_insurance_tokens"] += 1
                delta = payout
                outcome = "WIN"
                winner_count += 1
                winner_stake += stake
                if profit > biggest_profit:
                    biggest_profit = profit
                    biggest_profit_user = user_id
            else:
                if bet.get("use_insurance"):
                    refund = stake // 2
                    wallet["balance"] += refund
                    wallet["lifetime_profit"] -= stake - refund
                    delta = refund
                else:
                    wallet["lifetime_profit"] -= stake
                outcome = "LOSS"

        bet["status"] = "settled"
        bet["settled_at"] = now_ist().isoformat()
        bet["result"] = result
        bet["outcome"] = outcome
        _apply_streak(wallet, outcome if result != "VOID" else "VOID")
        _record_audit(
            user_id,
            "BET_WON" if outcome == "WIN" else ("BET_LOST" if outcome == "LOSS" else "BET_REFUNDED"),
            delta,
            before,
            wallet["balance"],
            market_id=market_id,
        )

    market["winner_count"] = winner_count
    market["winner_stake"] = winner_stake
    market["biggest_profit"] = biggest_profit
    market["biggest_profit_user"] = biggest_profit_user
    save_data(data)
    return market, None


async def void_market(market_id: str, reason: str):
    return await settle_market(market_id, "VOID", reason=reason)


async def timeout_stale_markets():
    now = now_ist()
    changed = False
    for market in list(_markets().values()):
        if market.get("status") not in {"open", "locked"}:
            continue
        timeout_at = datetime.fromisoformat(market["timeout_at"])
        if now < timeout_at:
            continue
        await void_market(market["market_id"], "MARKET_TIMEOUT")
        changed = True
    if changed:
        save_data(data)
    return changed


def resolve_match_result(result_code: str):
    outcome = canonical_outcome(result_code)
    if outcome == "DRAW":
        return "VOID"
    if outcome == "LOSS":
        return "LOSE"
    if outcome == "WIN":
        return "WIN"
    return None


def market_lock_needed(market: dict) -> bool:
    if market.get("status") != "open":
        return False
    return now_ist() >= datetime.fromisoformat(market["lock_at"])


def market_timeout_needed(market: dict) -> bool:
    if market.get("status") not in {"open", "locked"}:
        return False
    return now_ist() >= datetime.fromisoformat(market["timeout_at"])


def _format_lp(delta: int) -> str:
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta} LP"


def wallet_summary(user_id):
    wallet = _ensure_wallet(user_id)
    return wallet


def discord_timestamp(dt: datetime, style: str = "R") -> str:
    return f"<t:{int(dt.timestamp())}:{style}>"


def market_lock_display(market: dict, status: str) -> str:
    if status != "open":
        return "locked"
    try:
        lock_at = datetime.fromisoformat(market["lock_at"])
    except Exception:
        return "unknown"
    return f"{discord_timestamp(lock_at, 't')} · {discord_timestamp(lock_at, 'R')}"


def market_to_embed(market: dict, locked: bool = False):
    status = market.get("status", "open")
    if locked and status == "open":
        status = "locked"
    color = 0x57F287 if status == "open" else 0xFEE75C if status == "locked" else 0x99AAB5
    
    title = "🪨 Sisyphus' Daily Data"
    tracked_key = market["tracked_key"]
    player_name = _market_player_label(tracked_key)
    
    win_prob_percent = int(round(market["win_prob"] * 100))
    win_odds = market["win_odds"]
    lose_odds = market["lose_odds"]
    
    locks_val = market_lock_display(market, status)
    total_staked = market.get("total_staked", 0)
    
    desc = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {player_name} is in a ranked game\n\n"
        f"Model confidence: {win_prob_percent}% WIN\n\n"
        f"✅ BET WIN   →  odds {win_odds:.2f}\n"
        f"❌ BET LOSE  →  odds {lose_odds:.2f}\n\n"
        f"🔒 Locks  {locks_val}\n"
        f"💰 Pool: {total_staked} pts staked\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    
    e = discord.Embed(title=title, description=desc, color=color, timestamp=now_ist())
    return e


def settlement_embed(market: dict):
    result = market.get("result", "VOID")
    if result == "WIN":
        heading = "✅ Market Settled — WIN"
    elif result == "LOSE":
        heading = "❌ Market Settled — LOSE"
    else:
        heading = "⚪ Market Voided"
    e = discord.Embed(title=heading, color=0x5865F2, timestamp=now_ist())
    e.add_field(name="Market", value=f"`{market['title']}`", inline=False)
    e.add_field(name="Tracked Player", value=f"`{market['tracked_key']}`", inline=True)
    e.add_field(name="Total Staked", value=f"`{market.get('total_staked', 0)} pts`", inline=True)
    e.add_field(name="Winners", value=f"`{market.get('winner_count', 0)}`", inline=True)
    if market.get("biggest_profit_user"):
        e.add_field(
            name="Biggest Profit",
            value=f"`{market.get('biggest_profit', 0)} pts`",
            inline=True,
        )
    if market.get("settle_reason"):
        e.add_field(name="Reason", value=market["settle_reason"], inline=False)
    return e


def wallet_embed(user_id, name: str | None = None):
    wallet = _ensure_wallet(user_id)
    _touch_wallet(wallet)
    e = discord.Embed(title="💼 Wallet", color=0x5865F2, timestamp=now_ist())
    if name:
        e.set_author(name=name)
    today_net = _audit_delta_since(user_id, _period_start("today"))
    e.add_field(name="Balance", value=f"`{wallet['balance']} pts`", inline=True)
    e.add_field(name="Reserved", value=f"`{wallet['reserved']} pts`", inline=True)
    e.add_field(name="Lifetime Profit", value=f"`{_format_lp(wallet['lifetime_profit'])}`", inline=True)
    e.add_field(name="Today Net P/L", value=f"`{_format_lp(today_net)}`", inline=True)
    e.add_field(name="Current Streak", value=f"`{wallet['current_streak']}`", inline=True)
    e.add_field(name="Best Streak", value=f"`{wallet['best_streak']}`", inline=True)
    e.add_field(name="Insurance Tokens", value=f"`{wallet['weekly_insurance_tokens']}`", inline=True)
    return e


def leaderboard_embed(metric: str = "balance", range_name: str = "all"):
    metric = (metric or "balance").lower()
    range_name = (range_name or "all").lower()
    since_dt = _period_start(range_name)
    wallets = [(uid, wallet) for uid, wallet in _wallets().items()]

    def value_for(wallet: dict, uid: str):
        if metric == "profit":
            return _audit_delta_since(uid, since_dt)
        if metric == "streak":
            return int(wallet.get("current_streak", 0))
        if metric == "winrate":
            return _win_rate(wallet)
        return int(wallet.get("balance", 0))

    def sort_key(item):
        uid, wallet = item
        bets = int(wallet.get("bets_placed", 0))
        return (
            value_for(wallet, uid),
            _win_rate(wallet),
            bets,
            -_created_sort_key(wallet).timestamp(),
        )

    sorted_rows = sorted(wallets, key=sort_key, reverse=True)
    if metric == "profit":
        title = f"🏆 Profit Leaderboard · {range_name.title()}"
        fmt = lambda uid, w: _format_lp(_audit_delta_since(uid, since_dt))
    elif metric == "streak":
        title = f"🔥 Streak Leaderboard · {range_name.title()}"
        fmt = lambda uid, w: f"{w.get('current_streak', 0)} (best {w.get('best_streak', 0)})"
    elif metric == "winrate":
        title = f"📈 Win Rate Leaderboard · {range_name.title()}"
        fmt = lambda uid, w: f"{_win_rate(w) * 100:.1f}% · {w.get('wins', 0)}/{w.get('bets_placed', 0)}"
    else:
        title = f"💰 Balance Leaderboard · {range_name.title()}"
        fmt = lambda uid, w: f"{w.get('balance', 0)} pts"

    e = discord.Embed(title=title, color=0xFEE75C, timestamp=now_ist())
    lines = []
    for i, (uid, wallet) in enumerate(sorted_rows[:10], 1):
        lines.append(f"`{i}.` <@{uid}> — {fmt(uid, wallet)}")
    if not lines:
        lines = ["No wallets yet."]
    e.description = "\n".join(lines)
    return e


def active_bets_embed(user_id):
    key = _user_key(user_id)
    lines = []
    for market_id, bets in _bets().items():
        bet = bets.get(key)
        if not bet or bet.get("status") != "active":
            continue
        market = _markets().get(market_id)
        if not market:
            continue
        lines.append(
            f"`{market_id}` {market['title']} · {bet['side']} · {bet['stake']} pts · odds {bet['odds']:.2f}"
        )
    e = discord.Embed(title="🎯 Active Bets", color=0x5865F2, timestamp=now_ist())
    e.description = "\n".join(lines) if lines else "No active bets."
    return e


def betting_profile_embed(user_id):
    wallet = _ensure_wallet(user_id)
    total_bets = int(wallet.get("bets_placed", 0))
    wins = int(wallet.get("wins", 0))
    losses = int(wallet.get("losses", 0))
    win_rate = (wins / total_bets * 100) if total_bets else 0.0
    best_profit = _biggest_profit_for_user(user_id)
    e = discord.Embed(title="👤 Betting Profile", color=0x5865F2, timestamp=now_ist())
    e.add_field(name="Balance", value=f"`{wallet['balance']} pts`", inline=True)
    e.add_field(name="Lifetime Profit", value=f"`{_format_lp(wallet['lifetime_profit'])}`", inline=True)
    e.add_field(name="Bets Placed", value=f"`{total_bets}`", inline=True)
    e.add_field(name="Win Rate", value=f"`{win_rate:.1f}%`", inline=True)
    e.add_field(name="Current Streak", value=f"`{wallet['current_streak']}`", inline=True)
    e.add_field(name="Best Streak", value=f"`{wallet['best_streak']}`", inline=True)
    e.add_field(name="Wins / Losses / Voids", value=f"`{wins} / {losses} / {wallet.get('voids', 0)}`", inline=True)
    e.add_field(name="Biggest Single Profit", value=f"`{best_profit} pts`", inline=True)
    e.add_field(name="Reserved", value=f"`{wallet['reserved']} pts`", inline=True)
    return e


def insurance_embed(user_id):
    wallet = _ensure_wallet(user_id)
    week_key = wallet.get("insurance_week") or _week_key()
    resets_at = datetime.combine(today_ist(), datetime.min.time()).isoformat()
    uses = []
    key = _user_key(user_id)
    for market_id, bets in _bets().items():
        bet = bets.get(key)
        if not bet or not bet.get("use_insurance"):
            continue
        market = _markets().get(market_id)
        label = market["title"] if market else market_id
        uses.append(f"`{label}` · `{bet.get('side')}` · `{bet.get('stake')} pts`")
    e = discord.Embed(title="🛡️ Insurance", color=0x5865F2, timestamp=now_ist())
    e.add_field(name="Tokens Remaining", value=f"`{wallet['weekly_insurance_tokens']}`", inline=True)
    e.add_field(name="Week", value=f"`{week_key}`", inline=True)
    e.add_field(name="Reset", value="Monday 00:00 IST", inline=True)
    e.description = "\n".join(uses[:10]) if uses else "No past insurance usage."
    return e


def market_bets_embed(market_id: str):
    market = get_market(market_id)
    e = discord.Embed(title="🎟️ Market Bets", color=0x5865F2, timestamp=now_ist())
    if not market:
        e.description = "Market not found."
        return e
    bets = _market_bets(market_id)
    if not bets:
        e.description = "No bets placed yet."
        return e
    lines = []
    for uid, bet in bets.items():
        lines.append(
            f"<@{uid}> · **{bet['side']}** · `{bet['stake']} pts` · `{bet['odds']:.2f}` · insurance `{ 'yes' if bet.get('use_insurance') else 'no' }`"
        )
    e.description = "\n".join(lines)
    e.add_field(name="Tracked Player", value=f"`{market['tracked_key']}`", inline=True)
    e.add_field(name="Status", value=market.get("status", "open").upper(), inline=True)
    e.add_field(name="Total Staked", value=f"`{market.get('total_staked', 0)} pts`", inline=True)
    return e


def audit_embed(user_id):
    e = discord.Embed(title="🧾 Audit Log", color=0x5865F2, timestamp=now_ist())
    rows = [r for r in _audit() if r.get("user_id") == str(user_id)]
    if not rows:
        e.description = "No audit entries."
        return e
    lines = []
    for row in rows[-15:][::-1]:
        lines.append(
            f"`{row['timestamp']}` `{row['event_type']}` `{row['amount_delta']:+}` → `{row['balance_after']}`"
        )
    e.description = "\n".join(lines)
    return e


def market_status_embed():
    e = discord.Embed(title="🛠️ Market Status", color=0x5865F2, timestamp=now_ist())
    markets = list(_markets().values())
    if not markets:
        e.description = "No markets yet."
        return e
    lines = []
    for market in markets[-15:][::-1]:
        lines.append(
            f"`{market['market_id']}` `{market.get('status', 'open').upper()}` · {market['title']} · `{market['tracked_key']}`"
        )
    e.description = "\n".join(lines)
    return e


async def admin_refund(user_id, amount: int, reason: str = "ADMIN_ADJUSTMENT"):
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return None, "Amount must be a number."
    if amount <= 0:
        return None, "Amount must be positive."
    key = _user_key(user_id)
    async with _user_locks[key]:
        wallet = _ensure_wallet(user_id)
        before = wallet["balance"]
        wallet["balance"] += amount
        wallet["lifetime_profit"] += amount
        _record_audit(
            user_id,
            "ADMIN_ADJUSTMENT",
            amount,
            before,
            wallet["balance"],
            actor="ADMIN",
            reason=reason,
        )
        save_data(data)
        return wallet, None


def _lock_countdown(market: dict):
    try:
        lock_at = datetime.fromisoformat(market["lock_at"])
    except Exception:
        return "unknown"
    delta = lock_at - now_ist()
    if delta.total_seconds() <= 0:
        return "locked"
    minutes, seconds = divmod(int(delta.total_seconds()), 60)
    return f"{minutes:02d}:{seconds:02d}"


def _timeout_countdown(market: dict):
    try:
        timeout_at = datetime.fromisoformat(market["timeout_at"])
    except Exception:
        return "unknown"
    delta = timeout_at - now_ist()
    if delta.total_seconds() <= 0:
        return "expired"
    minutes, seconds = divmod(int(delta.total_seconds()), 60)
    return f"{minutes}m {seconds:02d}s"


class MarketBetModal(discord.ui.Modal):
    def __init__(self, market_id: str, side: str):
        super().__init__(title=f"Bet {side}")
        self.market_id = market_id
        self.side = side
        self.stake_input = discord.ui.TextInput(
            label="Stake",
            placeholder=f"{MIN_STAKE}-{MAX_STAKE}",
            max_length=len(str(MAX_STAKE)),
        )
        self.insurance_input = discord.ui.TextInput(
            label="Use insurance token? (y/n)",
            placeholder="n",
            required=False,
            max_length=3,
        )
        self.add_item(self.stake_input)
        self.add_item(self.insurance_input)

    async def on_submit(self, interaction: discord.Interaction):
        use_insurance = str(self.insurance_input.value or "").strip().lower() in {
            "y",
            "yes",
            "true",
            "1",
        }
        bet, err = await place_bet(
            interaction.user.id,
            self.market_id,
            self.side,
            self.stake_input.value,
            use_insurance=use_insurance,
        )
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return
        market = get_market(self.market_id)
        await interaction.response.send_message(
            (
                f"✅ Bet placed on **{market['title']}**\n"
                f"Side: **{bet['side']}** · Stake: **{bet['stake']}** · "
                f"Odds: **{bet['odds']:.2f}** · Insurance: **{'yes' if bet['use_insurance'] else 'no'}**"
            ),
            ephemeral=True,
        )


class MarketView(discord.ui.View):
    def __init__(self, market_id: str):
        super().__init__(timeout=None)
        self.market_id = market_id
        self.message = None
        market = get_market(market_id) or {}
        self.locked = market.get("status") != "open"

        bet_win = discord.ui.Button(
            label="Bet WIN",
            style=discord.ButtonStyle.success,
            custom_id=f"betwin:{market_id}",
            disabled=self.locked,
            row=0,
        )
        bet_lose = discord.ui.Button(
            label="Bet LOSE",
            style=discord.ButtonStyle.danger,
            custom_id=f"betlose:{market_id}",
            disabled=self.locked,
            row=0,
        )
        all_in_win = discord.ui.Button(
            label="All-in WIN",
            style=discord.ButtonStyle.success,
            custom_id=f"allinwin:{market_id}",
            disabled=self.locked,
            row=0,
        )
        all_in_lose = discord.ui.Button(
            label="All-in LOSE",
            style=discord.ButtonStyle.danger,
            custom_id=f"allinlose:{market_id}",
            disabled=self.locked,
            row=0,
        )
        why = discord.ui.Button(
            label="Why These Odds?",
            style=discord.ButtonStyle.secondary,
            custom_id=f"why:{market_id}",
            row=1,
        )
        leaderboard = discord.ui.Button(
            label="Leaderboard",
            style=discord.ButtonStyle.secondary,
            custom_id=f"leaderboard:{market_id}",
            row=1,
        )
        balance = discord.ui.Button(
            label="My Balance",
            style=discord.ButtonStyle.secondary,
            custom_id=f"balance:{market_id}",
            row=1,
        )

        bet_win.callback = self._bet_win
        bet_lose.callback = self._bet_lose
        all_in_win.callback = self._all_in_win
        all_in_lose.callback = self._all_in_lose
        why.callback = self._why
        leaderboard.callback = self._leaderboard
        balance.callback = self._balance

        self.add_item(bet_win)
        self.add_item(bet_lose)
        self.add_item(all_in_win)
        self.add_item(all_in_lose)
        self.add_item(why)
        self.add_item(leaderboard)
        self.add_item(balance)

    def _market(self):
        return get_market(self.market_id)

    async def _guard(self, interaction: discord.Interaction):
        market = self._market()
        if not market:
            await interaction.response.send_message("❌ Market no longer exists.", ephemeral=True)
            return None
        if market.get("status") not in {"open", "locked"}:
            await interaction.response.send_message(
                f"❌ Market is {market.get('status', 'closed')}.",
                ephemeral=True,
            )
            return None
        if market.get("status") == "open" and market_lock_needed(market):
            await interaction.response.send_message("❌ Market is locked.", ephemeral=True)
            return None
        return market

    async def _bet_win(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        await interaction.response.send_modal(MarketBetModal(self.market_id, "WIN"))

    async def _bet_lose(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        await interaction.response.send_modal(MarketBetModal(self.market_id, "LOSE"))

    async def _place_all_in(self, interaction: discord.Interaction, side: str):
        market = await self._guard(interaction)
        if not market:
            return
        bet, err = await place_all_in_bet(interaction.user.id, self.market_id, side)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return
        await interaction.response.send_message(
            (
                f"✅ All-in bet placed on **{market['title']}**\n"
                f"Side: **{bet['side']}** · Stake: **{bet['stake']}** · "
                f"Odds: **{bet['odds']:.2f}**"
            ),
            ephemeral=True,
        )

    async def _all_in_win(self, interaction: discord.Interaction):
        await self._place_all_in(interaction, "WIN")

    async def _all_in_lose(self, interaction: discord.Interaction):
        await self._place_all_in(interaction, "LOSE")

    async def _why(self, interaction: discord.Interaction):
        market = self._market()
        if not market:
            await interaction.response.send_message("❌ Market no longer exists.", ephemeral=True)
            return
        
        breakdown = market.get("breakdown", {})
        if not breakdown:
            await interaction.response.send_message("❌ No detailed breakdown available for this market.", ephemeral=True)
            return

        if breakdown.get("is_duo"):
            e = discord.Embed(title="🎲 Why These Joint Odds? (Duo Queue)", color=0x5865F2, timestamp=now_ist())
            b1 = breakdown["breakdown1"]
            b2 = breakdown["breakdown2"]
            
            p1_name, p2_name = [k.strip() for k in market["tracked_key"].split("&")]
            
            s1_type = b1.get("streak_type")
            s1_sign = "+" if b1.get("streak_mod", 0.0) >= 0 else ""
            s1_txt = f"{s1_type} x{b1.get('streak_count')} ({s1_sign}{b1.get('streak_mod', 0.0):.2f})" if s1_type else "No streak"
            
            s2_type = b2.get("streak_type")
            s2_sign = "+" if b2.get("streak_mod", 0.0) >= 0 else ""
            s2_txt = f"{s2_type} x{b2.get('streak_count')} ({s2_sign}{b2.get('streak_mod', 0.0):.2f})" if s2_type else "No streak"
            
            m1_txt = f"Never played (First time: -0.03)" if b1.get("mastery_points", 0) == 0 else (f"{b1.get('mastery_points', 0):,} Mastery pts (Comfort: +0.03)" if b1.get("mastery_points", 0) >= 100000 else f"{b1.get('mastery_points', 0):,} Mastery pts")
            m2_txt = f"Never played (First time: -0.03)" if b2.get("mastery_points", 0) == 0 else (f"{b2.get('mastery_points', 0):,} Mastery pts (Comfort: +0.03)" if b2.get("mastery_points", 0) >= 100000 else f"{b2.get('mastery_points', 0):,} Mastery pts")

            e.add_field(name=f"👤 {p1_name} Rationale", value=(
                f"• Recent 10 WR: `{b1.get('wr_recent', 0.5)*100:.1f}%` (Weight: 50%)\n"
                f"• All-Time WR: `{b1.get('wr_all', 0.5)*100:.1f}%` (Weight: 30%)\n"
                f"• Streak: `{s1_txt}`\n"
                f"• Mastery: `{m1_txt}`\n"
                f"• LP Trend Mod: `{b1.get('lp_mod', 0.0):+.2f}`\n"
                f"• **Raw Prob**: `{b1.get('p_raw', 0.5):.3f}`"
            ), inline=False)
            
            e.add_field(name=f"👤 {p2_name} Rationale", value=(
                f"• Recent 10 WR: `{b2.get('wr_recent', 0.5)*100:.1f}%` (Weight: 50%)\n"
                f"• All-Time WR: `{b2.get('wr_all', 0.5)*100:.1f}%` (Weight: 30%)\n"
                f"• Streak: `{s2_txt}`\n"
                f"• Mastery: `{m2_txt}`\n"
                f"• LP Trend Mod: `{b2.get('lp_mod', 0.0):+.2f}`\n"
                f"• **Raw Prob**: `{b2.get('p_raw', 0.5):.3f}`"
            ), inline=False)
            
            e.add_field(name="Duo Communication Bonus", value="`+0.02` probability added for playing together.", inline=False)
            
            e.add_field(name="Raw Joint Prob", value=f"`{breakdown.get('p_raw', 0.5):.3f}`", inline=True)
            e.add_field(name="Clamped Prob", value=f"`{breakdown.get('p_final', 0.5):.3f}`", inline=True)
            e.add_field(name="House Margin", value="`5%`", inline=True)
            e.add_field(name="WIN Odds", value=f"`{market['win_odds']:.2f}`", inline=True)
            e.add_field(name="LOSE Odds", value=f"`{market['lose_odds']:.2f}`", inline=True)
            
            await interaction.response.send_message(embed=e, ephemeral=True)
            return

        e = discord.Embed(title="🎲 Why These Odds?", color=0x5865F2, timestamp=now_ist())
        
        # Recent 10 WR
        wr_recent = breakdown.get("wr_recent", 0.5)
        e.add_field(name="Recent 10 WR", value=f"`{wr_recent * 100:.1f}%` (Weight: 50%)", inline=True)
        
        # All-time WR
        wr_all = breakdown.get("wr_all", 0.5)
        e.add_field(name="All-Time WR", value=f"`{wr_all * 100:.1f}%` (Weight: 30%)", inline=True)
        
        # Current streak
        streak_count = breakdown.get("streak_count", 0)
        streak_type = breakdown.get("streak_type")
        streak_mod = breakdown.get("streak_mod", 0.0)
        streak_sign = "+" if streak_mod >= 0 else ""
        if streak_type:
            streak_text = f"{streak_type} x{streak_count} ({streak_sign}{streak_mod:.2f})"
        else:
            streak_text = "No streak"
        e.add_field(name="Current Streak", value=f"`{streak_text}`", inline=True)
        
        # Champion note (if applicable)
        champion_name = breakdown.get("champion_name")
        champ_mod = breakdown.get("champ_mod", 0.0)
        champ_sign = "+" if champ_mod >= 0 else ""
        if champion_name:
            champ_wins = breakdown.get("champ_wins", 0)
            champ_total = breakdown.get("champ_total", 0)
            if champ_total >= 5:
                champ_text = f"{champion_name}: {champ_wins}W/{champ_total - champ_wins}L ({champ_sign}{champ_mod:.2f})"
            else:
                champ_text = f"{champion_name}: {champ_wins}W/{champ_total - champ_wins}L (Fewer than 5 games, no mod)"
        else:
            champ_text = "No champion selected yet"
        e.add_field(name="Champion Modifier", value=f"`{champ_text}`", inline=False)

        # Champion Mastery (if applicable)
        mastery_mod = breakdown.get("mastery_mod", 0.0)
        mastery_points = breakdown.get("mastery_points", 0)
        mastery_sign = "+" if mastery_mod >= 0 else ""
        if champion_name:
            if mastery_points >= 100000:
                mastery_text = f"{mastery_points:,} Mastery Points (Comfort Pick: {mastery_sign}{mastery_mod:.2f})"
            elif mastery_points == 0:
                mastery_text = f"0 Mastery Points (First Time Pick: {mastery_sign}{mastery_mod:.2f})"
            else:
                mastery_text = f"{mastery_points:,} Mastery Points (No modifier)"
        else:
            mastery_text = "No champion selected yet"
        e.add_field(name="Champion Mastery", value=f"`{mastery_text}`", inline=False)
        
        # LP trend note (if applicable)
        lp_trend = breakdown.get("lp_trend")
        lp_mod = breakdown.get("lp_mod", 0.0)
        lp_sign = "+" if lp_mod >= 0 else ""
        if lp_trend is not None:
            lp_text = f"7-day trend: {lp_trend:+} LP ({lp_sign}{lp_mod:.2f})"
        else:
            lp_text = "No daily LP trend history (Fewer than 7 days)"
        e.add_field(name="LP Trend Modifier", value=f"`{lp_text}`", inline=False)
        
        # Win Prob and final odds
        e.add_field(name="Raw Prob", value=f"`{breakdown.get('p_raw', 0.5):.3f}`", inline=True)
        e.add_field(name="Clamped Prob", value=f"`{breakdown.get('p_final', 0.5):.3f}`", inline=True)
        e.add_field(name="House Margin", value="`5%`", inline=True)
        e.add_field(name="WIN Odds", value=f"`{market['win_odds']:.2f}`", inline=True)
        e.add_field(name="LOSE Odds", value=f"`{market['lose_odds']:.2f}`", inline=True)
        
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def _leaderboard(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=leaderboard_embed("balance"), ephemeral=True)

    async def _balance(self, interaction: discord.Interaction):
        await get_wallet(interaction.user.id)
        await interaction.response.send_message(
            embed=wallet_embed(interaction.user.id, interaction.user.display_name),
            ephemeral=True,
        )


def build_market_view(market_id: str):
    return MarketView(market_id)


def market_public_message(market: dict):
    payload = {
        "embed": market_to_embed(market),
        "view": build_market_view(market["market_id"]),
    }
    if MARKET_ROLE_ID:
        payload["content"] = f"<@&{MARKET_ROLE_ID}>"
    return payload


def locked_market_message(market: dict):
    view = build_market_view(market["market_id"])
    for child in view.children:
        if isinstance(child, discord.ui.Button) and child.label in {
            "Bet WIN",
            "Bet LOSE",
            "All-in WIN",
            "All-in LOSE",
        }:
            child.disabled = True
    return {"embed": market_to_embed(market, locked=True), "view": view}


def settled_market_message(market: dict):
    return {"embed": settlement_embed(market), "view": None}


def register_persistent_market_views(bot):
    for market in list_open_markets():
        if not market.get("message_id"):
            continue
        bot.add_view(build_market_view(market["market_id"]), message_id=market["message_id"])


def _auto_market_title(tracked_key: str):
    return f"Next Ranked Match — {tracked_key}"


async def seed_market_for_tracked_key(
    tracked_key: str,
    destination,
    creator_id="SYSTEM",
    champion: str | None = None,
):
    if _active_market_conflict(tracked_key):
        return None, None, None

    market, err = await create_market(
        tracked_key=tracked_key,
        title=_auto_market_title(tracked_key),
        creator_id=creator_id,
        champion=champion,
        rationale="Auto-opened for the next ranked solo match.",
        channel_id=getattr(destination, "id", None),
    )
    if err:
        return None, None, err

    msg = await destination.send(**market_public_message(market))
    market["channel_id"] = msg.channel.id
    market["message_id"] = msg.id
    save_data(data)
    return market, msg, None


async def seed_markets_for_tracked_players(destination, creator_id="SYSTEM"):
    seeded = []
    for tracked_key in list(data.get("tracked", {}).keys()):
        market, msg, err = await seed_market_for_tracked_key(
            tracked_key, destination, creator_id=creator_id
        )
        if market:
            seeded.append((market, msg))
        elif err:
            print(f"[betting] seed failed for {tracked_key}: {err}")
    return seeded


async def lock_due_markets(destination_lookup):
    now = now_ist()
    changed = False
    for market in list(_markets().values()):
        if market.get("status") != "open":
            continue
        if now < datetime.fromisoformat(market["lock_at"]):
            continue
        market["status"] = "locked"
        changed = True
        save_data(data)
        if market.get("channel_id") and market.get("message_id"):
            channel = await destination_lookup(market["channel_id"])
            if channel:
                try:
                    msg = await channel.fetch_message(market["message_id"])
                    await msg.edit(**locked_market_message(market))
                except Exception as exc:
                    print(f"[betting] lock edit failed for {market['market_id']}: {exc}")
    return changed


async def expire_stale_markets(destination_lookup):
    changed = False
    for market in list(_markets().values()):
        if market.get("status") not in {"open", "locked"}:
            continue
        if not market_timeout_needed(market):
            continue
        settled_market, err = await void_market(market["market_id"], "MARKET_TIMEOUT")
        if err:
            print(f"[betting] timeout void failed for {market['market_id']}: {err}")
            continue
        changed = True
        if market.get("channel_id") and market.get("message_id"):
            channel = await destination_lookup(market["channel_id"])
            if channel:
                try:
                    msg = await channel.fetch_message(market["message_id"])
                    await msg.edit(**settled_market_message(settled_market))
                except Exception as exc:
                    print(f"[betting] timeout edit failed for {market['market_id']}: {exc}")
    return changed


async def void_single_markets_for_duo(tracked_keys: list[str], destination_lookup):
    duo_subjects = set(tracked_keys)
    voided = []
    for market in list(_markets().values()):
        market_subjects = _tracked_subjects(market.get("tracked_key", ""))
        if len(market_subjects) != 1:
            continue
        if not market_subjects.issubset(duo_subjects):
            continue
        if market.get("status") not in {"open", "locked"}:
            continue

        settled_market, err = await void_market(
            market["market_id"], "DUO_MARKET_REPLACED"
        )
        if err:
            print(
                f"[betting] duo replacement void failed for "
                f"{market['market_id']}: {err}"
            )
            continue
        voided.append(settled_market)

        if market.get("channel_id") and market.get("message_id"):
            channel = await destination_lookup(market["channel_id"])
            if channel:
                try:
                    msg = await channel.fetch_message(market["message_id"])
                    await msg.edit(**settled_market_message(settled_market))
                except Exception as exc:
                    print(
                        f"[betting] duo replacement edit failed for "
                        f"{market['market_id']}: {exc}"
                    )
    return voided


async def settle_markets_for_match(riot_id: str, result_code: str, destination_lookup):
    market_result = resolve_match_result(result_code)
    if market_result is None:
        return []

    settled = []
    for market in list(_markets().values()):
        mk = market.get("tracked_key", "")
        if not _overlapping_tracked_subject(mk, riot_id):
            continue
        if market.get("status") not in {"open", "locked"}:
            continue
        settled_market, err = await settle_market(
            market["market_id"], market_result, reason="MATCH_RESOLVED"
        )
        if err:
            print(f"[betting] settle failed for {market['market_id']}: {err}")
            continue
        settled.append(settled_market)
        if market.get("channel_id") and market.get("message_id"):
            channel = await destination_lookup(market["channel_id"])
            if channel:
                try:
                    msg = await channel.fetch_message(market["message_id"])
                    await msg.delete()
                except Exception as exc:
                    print(
                        f"[betting] settle original delete failed for "
                        f"{market['market_id']}: {exc}"
                    )

                try:
                    bets_dict = _bets().get(market["market_id"], {})
                    bettors = [
                        uid
                        for uid, bet in bets_dict.items()
                        if bet.get("status") == "settled"
                    ]
                    pings = " ".join(f"<@{uid}>" for uid in bettors)
                    content_str = (
                        f"🔔 Market Resolution Notification! {pings}"
                        if pings
                        else "🔔 Market Resolution Notification!"
                    )

                    await channel.send(
                        content=content_str,
                        embed=settlement_embed(settled_market),
                        allowed_mentions=discord.AllowedMentions(
                            users=True,
                            roles=False,
                            everyone=False,
                        ),
                    )
                except Exception as exc:
                    print(f"[betting] settle notify failed for {market['market_id']}: {exc}")
    return settled
