"""Offline privacy and shape checks for the dashboard export."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.export_model import build_export, forbidden_keys, member_key  # noqa: E402


def main() -> None:
    discord_id = "123456789012345678"
    source = {
        "tracked": {
            "GoldCurrent#TEST": {
                "puuid": "private-puuid",
                "game_name": "GoldCurrent",
                "tag_line": "TEST",
                "last_known_lp": 1201,
                "history_backfilled": True,
            }
        },
        "links": {discord_id: "GoldCurrent#TEST"},
        "history": {
            "GoldCurrent#TEST": [
                {
                    "match_id": "private-match-id",
                    "puuid": "private-puuid",
                    "date": "2026-08-25",
                    "champion": "LeBlanc",
                    "result": "WIN",
                    "lp_change": "+20",
                    "lp_total": 1185,
                    "recap_jump_url": "https://discord.com/channels/private",
                    "kills": 5,
                    "deaths": 1,
                    "assists": 4,
                    "backfilled": True,
                }
            ]
        },
        "daily_lp": {"GoldCurrent#TEST": {"2026-08-25": 1185, "2026-08-27": 1201}},
        "betting": {
            "wallets": {discord_id: {"balance": 5000, "lifetime_profit": 200, "lifetime_wagered": 1000, "bets_placed": 2, "wins": 1, "losses": 1, "voids": 0, "reserved": 0, "current_streak": 0, "best_streak": 1}},
            "markets": {"m1": {"market_id": "m1", "tracked_key": "GoldCurrent#TEST", "title": f"Next game <@{discord_id}> {discord_id} https://discord.com/private", "creator_id": discord_id, "channel_id": 1, "message_id": 2, "status": "settled", "total_staked": 1000, "result": "WIN"}},
            "bets": {"m1": {discord_id: {"user_id": discord_id, "side": "WIN", "stake": 1000, "odds": 2.0, "outcome": "WIN", "status": "settled", "placed_at": "2026-08-25T10:00:00+00:00"}}},
        },
        "community": {
            "records": {"one": {"label": "Best game", "value": 4.2, "player": "GoldCurrent", "champion": "LeBlanc", "match_id": "private-match-id"}},
            "milestones": {"GoldCurrent#TEST": [{"key": "first", "label": "First game", "match_id": "private-match-id"}]},
            "memories": {"GoldCurrent#TEST": {"private-match-id": {"owner_id": discord_id, "match_id": "private-match-id", "name": "Good game", "date": "2026-08-25", "recap_url": "https://discord.com/private"}}},
            "squad_goals": {},
            "weekly_recaps": {},
            "monthly_recaps": {},
            "historical_events": [],
        },
        "reports": [{"reporter_id": discord_id, "description": "must not appear"}],
        "audit": [{"actor": discord_id}],
    }
    payload = build_export(source, export_secret="a" * 32, member_names={discord_id: "Dosa"}, source_version="2.1.6")
    serialized = json.dumps(payload, sort_keys=True)
    assert not forbidden_keys(payload), f"forbidden keys leaked: {forbidden_keys(payload)}"
    assert "private-puuid" not in serialized
    assert "private-match-id" not in serialized
    assert discord_id not in serialized
    assert "Dosa" in serialized
    assert discord_id not in payload["betting"]["markets"][0]["title"]
    assert "https://" not in payload["betting"]["markets"][0]["title"]
    assert payload["players"][0]["current_rank"].startswith("GOLD")
    assert payload["players"][0]["peak_lp"] == 1201
    assert payload["players"][0]["daily_lp"][1]["value"] is None
    assert payload["betting"]["wallets"][0]["member_key"] == member_key(discord_id, "a" * 32)
    assert payload["community"]["memories"][0]["items"][0].get("recap_url") is None
    print("Dashboard export privacy smoke checks passed.")


if __name__ == "__main__":
    main()
