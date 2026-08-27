"""Non-network smoke check for Discord embed sizes."""
from __future__ import annotations

import sys
import asyncio
from pathlib import Path
from datetime import timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sisyphus import betting
from sisyphus import config
from sisyphus.changelog import build_curated_v21_embed, build_curated_v216_embed
from sisyphus.community import (
    halloffame_embed,
    queue_beacon_embed,
    queueboard_embed,
    rivalry_embed,
    squad_goals_embed,
    weekly_recap_embed,
)
from sisyphus.outcome import match_outcome
from sisyphus.monthly import monthly_model, personal_monthly_embed, public_monthly_embeds
from sisyphus.profiles import player_profile_view
from sisyphus.utils import now_ist
from sisyphus.views import DailyReportView, HelpView, ScoreboardView, StatsTabsView


def embed_size(embed) -> int:
    total = len(embed.title or "") + len(embed.description or "")
    for field in embed.fields:
        total += len(field.name or "") + len(field.value or "")
    footer = getattr(embed.footer, "text", None)
    author = getattr(embed.author, "name", None)
    total += len(footer or "") + len(author or "")
    return total


def assert_embed_ok(name: str, embed) -> None:
    size = embed_size(embed)
    if size > 6000:
        raise AssertionError(f"{name} embed is too large: {size}")


async def assert_all_in_uses_full_balance() -> None:
    old_data = betting.data
    old_save_data = betting.save_data
    try:
        betting.data = {
            "betting": {
                "wallets": {},
                "markets": {
                    "m1": {
                        "market_id": "m1",
                        "title": "Next Ranked Match",
                        "tracked_key": "TestPlayer#SG2",
                        "status": "open",
                        "lock_at": (now_ist() + timedelta(minutes=5)).isoformat(),
                        "timeout_at": (now_ist() + timedelta(minutes=90)).isoformat(),
                        "win_odds": 1.9,
                        "lose_odds": 1.9,
                        "win_prob": 0.5,
                        "total_staked": 0,
                    }
                },
                "bets": {},
                "audit": [],
                "meta": {},
            }
        }
        betting.save_data = lambda _: None
        bet, err = await betting.place_all_in_bet(12345, "m1", "WIN")
        if err:
            raise AssertionError(err)
        if int(bet["stake"]) != betting.INITIAL_BALANCE:
            raise AssertionError(f"all-in stake was {bet['stake']}, expected full balance")
        if bet.get("use_insurance"):
            raise AssertionError("all-in bet used insurance")
        if not bet.get("all_in"):
            raise AssertionError("all-in bet was not tagged")
        if betting.resolve_match_result("VOID") != "VOID":
            raise AssertionError("VOID result should void/refund market")
        market, err = await betting.settle_market("m1", "VOID", reason="SMOKE_REMAKE")
        if err:
            raise AssertionError(err)
        wallet = betting.data["betting"]["wallets"]["12345"]
        if int(wallet["balance"]) != betting.INITIAL_BALANCE:
            raise AssertionError("voided all-in bet did not refund full stake")
        if market["status"] != "void":
            raise AssertionError("voided market was not marked void")
    finally:
        betting.data = old_data
        betting.save_data = old_save_data


def fake_participant(puuid, team_id, champ, kills, deaths, assists, cs, dmg, gold, vision):
    return {
        "puuid": puuid,
        "teamId": team_id,
        "championName": champ,
        "championId": 1,
        "position": "MID",
        "win": team_id == 100,
        "result_code": "WIN" if team_id == 100 else "LOSS",
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "totalMinionsKilled": cs,
        "neutralMinionsKilled": 0,
        "totalDamageDealtToChampions": dmg,
        "goldEarned": gold,
        "champLevel": 16,
        "visionScore": vision,
        "wardsPlaced": 12,
        "wardsKilled": 3,
        "controlWardsBought": 2,
        "itemNames": ["Infinity Edge", "The Collector", "Boots"],
    }


def main() -> None:
    if config.RIOT_KEY_DAILY_REMINDER_ENABLED:
        raise AssertionError("daily Riot key reminders should default off")
    if config.TELEGRAM_POLLING_ENABLED:
        raise AssertionError("Telegram polling should default off")

    if match_outcome("DEFEAT", 0, 76) != "DRAW":
        raise AssertionError("sub-2-minute match should be classified as DRAW")
    asyncio.run(assert_all_in_uses_full_balance())

    parts = [
        fake_participant("tracked", 100, "Akshan", 12, 2, 4, 210, 28000, 15000, 22),
        fake_participant("ally1", 100, "Sejuani", 2, 4, 14, 150, 12000, 11000, 31),
        fake_participant("ally2", 100, "Jinx", 9, 3, 8, 245, 31000, 16000, 14),
        fake_participant("ally3", 100, "Ornn", 1, 5, 15, 170, 9000, 10000, 18),
        fake_participant("ally4", 100, "Lulu", 0, 2, 21, 28, 5000, 7800, 46),
        fake_participant("enemy1", 200, "Yasuo", 5, 9, 4, 190, 18000, 12000, 16),
        fake_participant("enemy2", 200, "Lee Sin", 4, 8, 9, 134, 16000, 10500, 24),
        fake_participant("enemy3", 200, "Caitlyn", 8, 6, 3, 230, 26000, 14500, 12),
        fake_participant("enemy4", 200, "Malphite", 2, 7, 8, 150, 11000, 9800, 15),
        fake_participant("enemy5", 200, "Nami", 1, 6, 13, 19, 6000, 7200, 39),
    ]
    match = {
        "metadata": {"matchId": "SG2_123"},
        "info": {
            "queueId": 420,
            "gameDuration": 1840,
            "participants": parts,
            "teams": [
                {
                    "teamId": 100,
                    "objectives": {
                        "champion": {"kills": 24, "first": True},
                        "tower": {"kills": 9, "first": True},
                        "dragon": {"kills": 3, "first": False},
                        "baron": {"kills": 1, "first": False},
                    },
                },
                {
                    "teamId": 200,
                    "objectives": {
                        "champion": {"kills": 18, "first": False},
                        "tower": {"kills": 3, "first": False},
                        "dragon": {"kills": 1, "first": True},
                        "baron": {"kills": 0, "first": False},
                    },
                },
            ],
        },
    }
    score = ScoreboardView(match, "tracked", "TestPlayer#SG2", "GOLD", "III", 44, 1020, 1044)
    history = [
        {
            "date": "2026-07-03",
            "champion": "Akshan",
            "result": "WIN",
            "lp_change": "+24",
            "kills": 12,
            "deaths": 2,
            "assists": 4,
            "cs_per_min": 6.8,
            "kill_participation": 66.7,
        }
    ]
    daily = DailyReportView("TestPlayer#SG2", 1044, 1020, history, history)
    stats = StatsTabsView("TestPlayer#SG2", "GOLD", "III", 44, 24, 1, 0, 0, 12, 9, 0, 110, 1044)
    help_view = HelpView()
    profile = player_profile_view("TestPlayer#SG2")
    month_model = monthly_model(2026, 7)
    queue_beacon = queue_beacon_embed("SG2_123", [("TestPlayer#SG2", "Akshan", 100)])
    if any(field.name == "Game" for field in queue_beacon.fields):
        raise AssertionError("queue beacon should not expose game id")

    market = {
        "market_id": "m1",
        "title": "Next Ranked Match",
        "tracked_key": "TestPlayer#SG2",
        "status": "open",
        "lock_at": (now_ist() + timedelta(minutes=2)).isoformat(),
        "win_prob": 0.5,
        "win_odds": 1.9,
        "lose_odds": 1.9,
        "total_staked": 0,
    }
    market_embed = betting.market_to_embed(market)
    if "<t:" not in (market_embed.description or ""):
        raise AssertionError("market embed should use Discord dynamic timestamps")

    embeds = {
        "score_overview": score._overview_embed(),
        "score_blue": score._team_embed(score.blue_team, "Blue", "Victory", 0x5865F2),
        "daily": daily._summary_embed(),
        "daily_history": daily._history_embed(),
        "stats_today": stats._today_embed(),
        "stats_alltime": stats._all_time_embed(),
        "queue_beacon": queue_beacon,
        "market": market_embed,
        "queue_board": queueboard_embed(),
        "rivalries": rivalry_embed(),
        "squad_goals": squad_goals_embed(),
        "weekly": weekly_recap_embed(),
        "halloffame": halloffame_embed(),
        "help_overview": help_view._overview_embed(),
        "help_tracking": help_view._tracking_embed(),
        "help_community": help_view._community_embed(),
        "help_betting": help_view._betting_embed(),
        "help_reporting": help_view._reporting_embed(),
        "changelog_v21": build_curated_v21_embed("v2.1.0"),
        "changelog_v216": build_curated_v216_embed("v2.1.6"),
        "profile_overview": profile.overview_embed(),
        "profile_journey": profile.journey_embed(),
        "profile_identity": profile.identity_embed(),
        "profile_records": profile.records_embed(),
        "profile_memories": profile.memories_embed(),
    }
    for i, embed in enumerate(public_monthly_embeds(month_model), 1):
        embeds[f"monthly_public_{i}"] = embed
    embeds["monthly_personal"] = personal_monthly_embed(
        "TestPlayer#SG2", history, month_model
    )
    for name, embed in embeds.items():
        assert_embed_ok(name, embed)
    print(f"Checked {len(embeds)} embeds under Discord limits.")


if __name__ == "__main__":
    main()
