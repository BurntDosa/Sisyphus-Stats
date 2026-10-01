"""Background tasks — match-poll loop and daily summary."""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from time import monotonic

import aiohttp
import discord
from discord.ext import tasks

from .bot import bot, get_post_destination
from .config import (
    BETTING_ENABLED,
    BACKFILL_DAYS,
    IST,
    MAX_RECAP_AGE_HOURS,
    POLL_INTERVAL,
    RIOT_KEY_DAILY_REMINDER_ENABLED,
)
from .community import (
    make_history_row,
    maybe_send_queue_beacon,
    update_records,
    weekly_recap_embed,
    week_key,
)
from .betting import (
    expire_stale_markets,
    lock_due_markets,
    seed_market_for_tracked_key,
    settle_markets_for_match,
)
from .opgg import (
    build_history_entry,
    find_history_participant,
    get_lp_info,
    get_match,
    get_champion_mastery,
    get_opgg_leaderboard_rank,
    get_ranked_stats,
    get_recent_matches,
)
from .outcome import is_remake_duration, match_outcome
from .state import data, posted_matches, save_data
from .utils import match_day_ist, now_ist, today_ist
from .health import (
    discord_latency_ms,
    mark_dependency,
    mark_discord,
    mark_markets,
    mark_poll_failure,
    mark_poll_started,
    mark_poll_success,
)
from .daily_report import DailyReportView, daily_boundaries, record_rank_snapshot


_PLATFORM_NETWORK_BACKOFF_UNTIL: dict[str, float] = {}
_PLATFORM_NETWORK_BACKOFF_SECONDS = 10 * 60
_ACTIVE_PRESENCE_NAMES: list[str] = []
_PRESENCE_INDEX = 0
_LAST_PRESENCE_NAME: str | None = None
_POLL_RESTART_HANDLE: asyncio.TimerHandle | None = None
_POLL_RESTART_DELAY_SECONDS = 5.0
_APEX_RETRY_DELAY = timedelta(hours=1)
_APEX_TIERS = {"MASTER", "GRANDMASTER", "CHALLENGER"}


def _store_current_rank(riot_id: str, tier: str, rank: str, lp: int, total_lp: int) -> bool:
    """Store OP.GG's exact current rank beside its historical LP total."""
    info = data["tracked"][riot_id]
    changed = False
    for key, value in (("last_known_tier", tier), ("last_known_rank", rank), ("last_known_raw_lp", lp)):
        if info.get(key) != value:
            info[key] = value
            changed = True
    if tier not in {"MASTER", "GRANDMASTER", "CHALLENGER"}:
        for key in ("leaderboard_rank", "leaderboard_rank_updated_at"):
            if key in info:
                info.pop(key, None)
                changed = True
    return changed


def _apex_retry_due(info: dict) -> bool:
    raw = info.get("apex_refresh_retry_at")
    if not raw:
        return False
    try:
        due_at = datetime.fromisoformat(str(raw))
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=IST)
    except (TypeError, ValueError):
        return True
    return due_at <= now_ist()


def _schedule_apex_retry(info: dict, reason: str) -> None:
    info["apex_refresh_retry_at"] = (now_ist() + _APEX_RETRY_DELAY).isoformat()
    info["apex_refresh_retry_reason"] = reason[:160]


def _clear_apex_retry(info: dict) -> bool:
    changed = False
    for key in ("apex_refresh_retry_at", "apex_refresh_retry_reason"):
        if key in info:
            info.pop(key, None)
            changed = True
    return changed


async def _refresh_apex_after_match(
    session: aiohttp.ClientSession,
    riot_id: str,
    info: dict,
    reported_tier: str,
    raw_lp: int,
) -> tuple[str, bool]:
    """Refresh cutoffs and placement before an apex player's recap is sent."""
    from .apex import refresh_sg_apex_cutoffs, tier_for_apex_lp

    if reported_tier not in _APEX_TIERS:
        return reported_tier, False

    if not await refresh_sg_apex_cutoffs(session):
        _schedule_apex_retry(info, "SG apex cutoff source unavailable")
        retained_tier = reported_tier
        print(f"[apex] {riot_id}: retained {retained_tier}; cutoff refresh will retry in one hour")
        return retained_tier, True

    tier = reported_tier
    cutoff_disagrees = tier_for_apex_lp(raw_lp) not in (None, reported_tier)
    placement, error = await get_opgg_leaderboard_rank(
        session, info.get("game_name"), info.get("tag_line")
    )
    changed = False
    if placement is None:
        _schedule_apex_retry(info, error or "Leaderboard position not found")
        print(f"[apex] {riot_id}: placement unavailable; retrying in one hour: {error}")
        return tier, True

    updated_at = now_ist().isoformat()
    if info.get("leaderboard_rank") != placement:
        info["leaderboard_rank"] = placement
        changed = True
    if info.get("leaderboard_rank_updated_at") != updated_at:
        info["leaderboard_rank_updated_at"] = updated_at
        changed = True
    if cutoff_disagrees:
        _schedule_apex_retry(info, "Cutoff estimate differs from reported tier")
        return tier, True
    return tier, _clear_apex_retry(info) or changed


def _platform_is_backed_off(platform: str) -> bool:
    return monotonic() < _PLATFORM_NETWORK_BACKOFF_UNTIL.get(platform, 0)


def _backoff_platform(platform: str, err: str) -> None:
    if not _platform_is_backed_off(platform):
        print(
            f"[poll] Temporarily skipping Riot platform {platform} for "
            f"{_PLATFORM_NETWORK_BACKOFF_SECONDS // 60} minutes: {err}"
        )
    _PLATFORM_NETWORK_BACKOFF_UNTIL[platform] = monotonic() + _PLATFORM_NETWORK_BACKOFF_SECONDS


def _platform_candidates(info: dict) -> list[str]:
    from .config import PLATFORM, RIOT_PLATFORMS

    candidates: list[str] = []
    cached_platform = str(info.get("platform") or "").lower()
    for platform in (cached_platform, PLATFORM):
        if platform and platform not in candidates:
            candidates.append(platform)

    probe_pool = [p for p in RIOT_PLATFORMS if p and p not in candidates]
    if probe_pool and not cached_platform:
        probe_index = int(info.get("platform_probe_index", 0)) % len(probe_pool)
        candidates.append(probe_pool[probe_index])
        info["platform_probe_index"] = (probe_index + 1) % len(probe_pool)

    return candidates


async def _resolve_channel(channel_id: int):
    channel = bot.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        return await bot.fetch_channel(channel_id)
    except Exception as exc:
        print(f"[poll] could not resolve channel {channel_id}: {exc}")
        return None


def _presence_name_for(riot_id: str, info: dict) -> str:
    return str(info.get("game_name") or riot_id.split("#", 1)[0]).strip()


def _set_active_presence_names(players: list[tuple[str, dict]]) -> None:
    global _ACTIVE_PRESENCE_NAMES, _PRESENCE_INDEX

    names: list[str] = []
    seen: set[str] = set()
    for riot_id, info in players:
        name = _presence_name_for(riot_id, info)
        key = name.casefold()
        if name and key not in seen:
            names.append(name)
            seen.add(key)

    if names != _ACTIVE_PRESENCE_NAMES:
        _ACTIVE_PRESENCE_NAMES = names
        _PRESENCE_INDEX = 0


@tasks.loop(seconds=30)
async def tracked_presence_task():
    """Cycle the bot presence through tracked players currently in game."""
    global _PRESENCE_INDEX, _LAST_PRESENCE_NAME

    names = _ACTIVE_PRESENCE_NAMES
    if not names:
        if _LAST_PRESENCE_NAME is not None:
            await bot.change_presence(activity=None)
            _LAST_PRESENCE_NAME = None
        return

    name = names[_PRESENCE_INDEX % len(names)]
    _PRESENCE_INDEX = (_PRESENCE_INDEX + 1) % len(names)
    activity_name = f"{name} is pushing the boulder"
    if _LAST_PRESENCE_NAME == activity_name:
        return
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=activity_name,
        )
    )
    _LAST_PRESENCE_NAME = activity_name


@tasks.loop(seconds=POLL_INTERVAL)
async def poll_players():
    poll_started = mark_poll_started()
    latency_ms = discord_latency_ms(bot.latency)
    mark_discord(bot.is_ready(), latency_ms)
    destination = await get_post_destination()
    if not destination:
        mark_poll_failure("Discord destination unavailable")

    # Check active games using Riot Spectator-V5 API
    try:
        from .config import REGION
        
        async def fetch_riot_api(session, url):
            import sisyphus.config as config
            headers = {"X-Riot-Token": config.RIOT_KEY}
            try:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        mark_dependency(
                            "riot",
                            True,
                            success_message="Riot API responding",
                            failure_message="Riot API request failed",
                        )
                        return await response.json(), None
                    elif response.status == 403:
                        mark_dependency(
                            "riot",
                            False,
                            success_message="Riot API responding",
                            failure_message="Riot API authorization failed",
                            immediate=True,
                        )
                        alert_sent = data.get("betting", {}).get("riot_key_403_alert_sent", False)
                        if not alert_sent:
                            from sisyphus.telegram import send_telegram_notification
                            await send_telegram_notification(
                                "⚠️ *Riot API Key Expired (403 Forbidden)!*\n\n"
                                "The bot is unable to query Riot API. Please regenerate the key:\n"
                                "👉 [Regenerate Key on Riot Portal](https://developer.riotgames.com/)\n\n"
                                "Reply to this bot with the new `RGAPI-...` key to restore service instantly."
                            )
                            if "betting" not in data:
                                data["betting"] = {}
                            data["betting"]["riot_key_403_alert_sent"] = True
                            save_data(data)
                        return None, "403"
                    elif response.status == 404:
                        mark_dependency(
                            "riot",
                            True,
                            success_message="Riot API responding",
                            failure_message="Riot API request failed",
                        )
                        return None, "404"
                    else:
                        body = await response.text()
                        mark_dependency(
                            "riot",
                            False,
                            success_message="Riot API responding",
                            failure_message=f"Riot API returned HTTP {response.status}",
                        )
                        return None, f"HTTP {response.status}: {body}"
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                mark_dependency(
                    "riot",
                    False,
                    success_message="Riot API responding",
                    failure_message="Riot API connection failed",
                )
                return None, f"NETWORK {type(exc).__name__}: {exc}"

        # 1. Fetch active games for all tracked players
        active_games = {} # maps game_id -> list of (riot_id, puuid, champion_name, team_id, info)
        async with aiohttp.ClientSession() as session:
            for riot_id, info in list(data["tracked"].items()):
                puuid = info.get("puuid")
                if not puuid:
                    # Resolve puuid dynamically on regional route if missing
                    try:
                        game_name, tag_line = info["game_name"], info["tag_line"]
                        account_url = f"https://{REGION}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
                        acc_data, err = await fetch_riot_api(session, account_url)
                        if acc_data and acc_data.get("puuid"):
                            puuid = acc_data["puuid"]
                            info["puuid"] = puuid
                            save_data(data)
                    except Exception as e:
                        print(f"[poll] Failed to dynamically resolve PUUID for {riot_id}: {e}")
                        continue
                if not puuid:
                    continue

                game_data = None
                active_platform = None
                last_err = None
                for platform in _platform_candidates(info):
                    if _platform_is_backed_off(platform):
                        continue
                    spectator_url = f"https://{platform}.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}"
                    game_data, err = await fetch_riot_api(session, spectator_url)
                    last_err = err
                    if err and err.startswith("NETWORK"):
                        _backoff_platform(platform, err)
                        continue

                    # Check for 400 Bad Request / decryption exceptions due to key-specific encrypted PUUIDs
                    if err and ("400" in err or "decrypt" in err.lower()):
                        print(f"[poll] Decryption error/400 for {riot_id} on {platform}. Resolving via Riot API...")
                        try:
                            game_name, tag_line = info["game_name"], info["tag_line"]
                            account_url = f"https://{REGION}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
                            acc_data, acc_err = await fetch_riot_api(session, account_url)
                            if acc_data and acc_data.get("puuid"):
                                new_puuid = acc_data["puuid"]
                                print(f"[poll] Successfully resolved new PUUID for {riot_id}: {new_puuid}")
                                info["puuid"] = new_puuid
                                puuid = new_puuid
                                save_data(data)
                                spectator_url = f"https://{platform}.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{new_puuid}"
                                game_data, err = await fetch_riot_api(session, spectator_url)
                                last_err = err
                        except Exception as e:
                            print(f"[poll] Failed to resolve PUUID on decryption failure for {riot_id}: {e}")

                    if game_data:
                        active_platform = platform
                        if info.get("platform") != platform:
                            info["platform"] = platform
                            save_data(data)
                        break

                    if err and err not in {"404", "403"}:
                        print(f"[poll] spectator check failed for {riot_id} on {platform}: {err}")

                if not game_data:
                    if last_err == "403":
                        print(f"[poll] spectator checks blocked by Riot 403 for {riot_id}")
                    continue

                queue_id = game_data.get("gameQueueConfigId")
                if queue_id != 420: # Ranked Solo/Duo only
                    continue

                game_id = str(game_data.get("gameId"))
                
                # Find participant details
                participant = None
                for p in game_data.get("participants", []):
                    if p.get("puuid") == puuid:
                        participant = p
                        break
                if not participant:
                    continue

                champion_id = participant.get("championId")
                from .ddragon import get_champion_name
                champion_name = await get_champion_name(session, champion_id)
                team_id = participant.get("teamId")
                
                active_games.setdefault(game_id, []).append((riot_id, puuid, champion_name, team_id, info, active_platform))

        _set_active_presence_names(
            [
                (riot_id, info)
                for players in active_games.values()
                for riot_id, puuid, champion_name, team_id, info, active_platform in players
            ]
        )

        if destination:
            await maybe_send_queue_beacon(destination, active_games)

        if BETTING_ENABLED and destination:
            # 2. Process active games and handle duo-queue pooling
            from .betting import (
                get_conflicting_market_for_tracked_key,
                get_market_for_tracked_key,
                seed_market_for_tracked_key,
                void_single_markets_for_duo,
            )
            
            for game_id, players in active_games.items():
                # Group players by team_id
                teams = {}
                for riot_id, puuid, champion_name, team_id, info, active_platform in players:
                    teams.setdefault(team_id, []).append((riot_id, champion_name))
                    
                for team_id, team_players in teams.items():
                    if len(team_players) > 1:
                        # Duo-Queue detected playing together!
                        joint_key = " & ".join(tp[0] for tp in team_players)
                        joint_champion = " & ".join(tp[1] for tp in team_players)
                        
                        market = get_market_for_tracked_key(joint_key)
                        if not market:
                            await void_single_markets_for_duo(
                                [tp[0] for tp in team_players], _resolve_channel
                            )
                            print(
                                f"[poll] Seeding duo-queue market for {joint_key} "
                                f"(champions: {joint_champion})"
                            )
                            await seed_market_for_tracked_key(
                                joint_key, destination, champion=joint_champion
                            )
                    else:
                        # Single player in active game
                        riot_id, champion_name = team_players[0]
                        market = get_conflicting_market_for_tracked_key(riot_id)
                        if not market:
                            print(f"[poll] Seeding single market for {riot_id} (champion: {champion_name})")
                            await seed_market_for_tracked_key(riot_id, destination, champion=champion_name)
    except Exception as e:
        print(f"[poll] Error checking active games: {e}")

    from .completed_matches import process_completed_matches
    from .match_processing import MatchProcessor, parsed, tracked_participants
    from .recap_delivery import build_recap_view, enrich_history, restore_recent_views

    async with aiohttp.ClientSession() as session:
        async def recent_fetch(riot_id):
            tracked = data["tracked"][riot_id]
            if not tracked.get("game_name") or not tracked.get("tag_line"):
                return None
            return await get_recent_matches(session, tracked["game_name"], tracked["tag_line"], count=20)

        async def rank_fetch(riot_id):
            tracked = data["tracked"][riot_id]
            ranked = await get_ranked_stats(session, tracked["game_name"], tracked["tag_line"])
            if ranked is None:
                return None
            tier, rank, lp, total_lp = get_lp_info(ranked)
            tier, _ = await _refresh_apex_after_match(session, riot_id, tracked, tier, lp)
            _store_current_rank(riot_id, tier, rank, lp, total_lp)
            record_rank_snapshot(data, riot_id, total_lp, now_ist())
            return {"tier": tier, "rank": rank, "lp": lp, "total_lp": total_lp}

        async def view_factory(snapshot):
            return await build_recap_view(snapshot, session, automatic=True)

        def select_markets(match, roster):
            from .betting import _overlapping_tracked_subject
            ended = parsed(match["info"]["gameCreation"])
            selected = []
            for market in data.get("betting", {}).get("markets", {}).values():
                opened = parsed(market.get("created_at"))
                # OP.GG's detail timestamp is the same timestamp used by its
                # completed-match feed. Never settle a market opened afterwards.
                if market.get("status") not in {"open", "locked"} or not opened or opened > ended:
                    continue
                if any(_overlapping_tracked_subject(market.get("tracked_key", ""), m["riot_id"]) for m in roster):
                    selected.append(market["market_id"])
            return sorted(selected)

        async def settle(record):
            if BETTING_ENABLED:
                for member in record["members"]:
                    await settle_markets_for_match(member["riot_id"],
                        "VOID" if record["outcome"] == "DRAW" else record["outcome"], _resolve_channel,
                        eligible_market_ids=record.get("market_ids", []))

        async def enrich_match(match):
            for participant in tracked_participants(data, match).values():
                participant["_mastery_points"] = await get_champion_mastery(
                    session, participant.get("puuid"), participant.get("championId"))

        def history_builder(match, participant, riot_id, outcome, delta, old, new):
            return make_history_row(match, participant, riot_id, outcome, delta, old, new,
                mastery_points=participant.get("_mastery_points", 0))

        processor = MatchProcessor(data, save=save_data, history_builder=history_builder,
            view_factory=view_factory, on_history=enrich_history, settle_record=settle,
            market_selector=select_markets,
            max_age_hours=MAX_RECAP_AGE_HOURS)
        feeds, unseen = await process_completed_matches(data, recent_fetch=recent_fetch,
            match_fetch=lambda mid, created: get_match(session, mid, created), rank_fetch=rank_fetch,
            processor=processor, destination=destination, enrich_match=enrich_match)
        await restore_recent_views(processor.receipts, bot, destination, session)
        for riot_id, recent_ranked in feeds.items():
            info = data["tracked"][riot_id]
            history_rows = data.setdefault("history", {}).setdefault(riot_id, [])
            if not info.get("history_backfilled"):
                known = {str(row.get("match_id")) for row in history_rows}
                cutoff = today_ist() - timedelta(days=BACKFILL_DAYS)
                for entry in reversed(recent_ranked):
                    mid = str(entry.get("id"))
                    day = match_day_ist(entry.get("created_at"))
                    if mid in known or mid in unseen.get(riot_id, set()) or not day or day < cutoff:
                        continue
                    participant = find_history_participant(entry, info["game_name"], info["tag_line"], info.get("puuid"))
                    if participant:
                        row = build_history_entry(entry, participant, None)
                        row.update(match_created_at=entry.get("created_at"), backfilled=True)
                        history_rows.append(row)
                        known.add(mid)
                history_rows.sort(key=lambda row: str(row.get("match_created_at") or row.get("date") or ""))
                info["history_backfilled"] = True
            # Rank observations refresh today's snapshot independently. Only
            # MatchProcessor can advance the baseline tied to a completed match.
            managed = any(m.get("can_reconcile") and m.get("lp_status") != "known"
                for receipt in processor.receipts.values() if receipt.get("match")
                for m in receipt["members"] if m["riot_id"] == riot_id)
            if not unseen.get(riot_id) and not managed:
                await rank_fetch(riot_id)
        save_data(data)

    mark_poll_success(poll_started)


@poll_players.error
async def poll_players_error(error: BaseException):
    global _POLL_RESTART_HANDLE

    mark_poll_failure("Polling task stopped unexpectedly")
    print(f"[poll] task error: {type(error).__name__}: {error}")

    # discord.py stops a loop after an unhandled exception. Schedule the
    # restart after its failed task has finished cleaning itself up.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if loop.is_closed() or bot.is_closed():
        return
    if _POLL_RESTART_HANDLE is None or _POLL_RESTART_HANDLE.cancelled():
        _POLL_RESTART_HANDLE = loop.call_later(
            _POLL_RESTART_DELAY_SECONDS,
            _restart_poll_players,
        )


def _restart_poll_players() -> None:
    global _POLL_RESTART_HANDLE

    _POLL_RESTART_HANDLE = None
    if bot.is_closed() or poll_players.is_running():
        return
    try:
        poll_players.start()
        print("[poll] restarted polling task after an unhandled error")
    except RuntimeError as exc:
        print(f"[poll] could not restart polling task: {type(exc).__name__}: {exc}")


@tasks.loop(time=time(0, 5, tzinfo=IST))
async def daily_summary_task():
    """Fires at 00:05 IST every day. Summarizes the day that just ended (yesterday)."""
    destination = await get_post_destination()
    if not destination:
        return

    # We're a few minutes past midnight. Today's calendar date is the NEW day;
    # the report is for yesterday's games.
    today_date = today_ist()
    yesterday_date = today_date - timedelta(days=1)
    yesterday_str = str(yesterday_date)

    for riot_id in list(data["tracked"]):
        start_lp, end_lp = daily_boundaries(data, riot_id, yesterday_date)

        history_all = data.get("history", {}).get(riot_id, [])
        history_yesterday = [
            h for h in history_all if h.get("date") == yesterday_str
        ]

        # Skip players who didn't play yesterday — no point posting an empty report.
        if not history_yesterday:
            continue

        view = DailyReportView(
            riot_id,
            end_lp,
            start_lp,
            history_yesterday,
            history_all,
            report_date=yesterday_date,
        )
        embed = view._summary_embed()
        msg = await destination.send(embed=embed, view=view)
        view.message = msg


@tasks.loop(time=time(21, 0, tzinfo=IST))
async def weekly_squad_recap_task():
    """Posts one ranked Solo/Duo squad recap on Sunday night."""
    if today_ist().weekday() != 6:
        return
    destination = await get_post_destination()
    if not destination:
        return
    community = data.setdefault("community", {})
    posted = community.setdefault("weekly_recaps", {})
    key = week_key()
    if posted.get(key):
        return
    await destination.send(embed=weekly_recap_embed(days=7))
    posted[key] = now_ist().isoformat()
    save_data(data)


@tasks.loop(time=time(6, 0, tzinfo=IST))
async def monthly_recap_task():
    """Posts the previous calendar month's public recap and personal DMs."""
    if today_ist().day != 1:
        return
    destination = await get_post_destination()
    if not destination:
        return
    from .monthly import run_monthly_recap

    await run_monthly_recap(destination, bot)


@tasks.loop(seconds=60)
async def betting_housekeeping_task():
    if not BETTING_ENABLED:
        mark_markets(True)
        return
    await lock_due_markets(_resolve_channel)
    await expire_stale_markets(_resolve_channel)
    mark_markets(True)


@betting_housekeeping_task.error
async def betting_housekeeping_error(error: BaseException):
    mark_markets(False)
    print(f"[betting] housekeeping task error: {type(error).__name__}: {error}")


@tasks.loop(time=time(21, 0, tzinfo=IST))
async def check_key_expiry():
    """Daily reminder at 9:00 PM IST to update the Riot API key."""
    if not RIOT_KEY_DAILY_REMINDER_ENABLED:
        return
    from sisyphus.telegram import send_telegram_notification
    print("[poll] Triggering daily 9:00 PM IST key update reminder...")
    await send_telegram_notification(
        "🔔 *Daily Riot API Key Reminder!*\n\n"
        "It's 9:00 PM IST. Please update your developer API key for the night:\n"
        "👉 [Regenerate Key on Riot Portal](https://developer.riotgames.com/)\n\n"
        "Copy/paste and reply to this bot with the new `RGAPI-...` key to reload it instantly."
    )


__all__ = [
    "poll_players",
    "daily_summary_task",
    "weekly_squad_recap_task",
    "monthly_recap_task",
    "betting_housekeeping_task",
    "check_key_expiry",
    "bot",
]
