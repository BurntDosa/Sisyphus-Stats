"""Offline regression checks for current-rank profile presentation."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sisyphus import community, profiles, ranks  # noqa: E402


def main() -> None:
    original_profile_data = profiles.data
    original_community_data = community.data
    fixture = {
        "tracked": {
            "GoldCurrent#TEST": {"game_name": "GoldCurrent", "last_known_lp": 1201},
            "AntiChungleVenom#SG2": {
                "game_name": "AntiChungleVenom",
                "last_known_lp": 3919,
                "last_known_tier": "GRANDMASTER",
                "last_known_rank": "1",
                "last_known_raw_lp": 419,
                "leaderboard_rank": 1027,
            },
        },
        "history": {
            "GoldCurrent#TEST": [
                {"date": "2026-08-25", "result": "WIN", "lp_change": "+20", "lp_total": 1185}
            ],
            "MissingCurrent#TEST": [
                {"date": "2026-08-25", "result": "WIN", "lp_change": "+20", "lp_total": 1185}
            ],
            "AntiChungleVenom#SG2": [
                {"date": "2026-09-07", "result": "WIN", "lp_change": "+20", "lp_total": 3919}
            ],
        },
        "community": {},
    }
    try:
        profiles.data = fixture
        community.data = fixture
        ranks.set_apex_cutoffs(challenger=1421, grandmaster=751)

        gold = profiles.player_profile_view("GoldCurrent#TEST").overview_embed()
        assert gold.color.value == 0xFFD700, "profile accent should follow current Gold LP"
        assert "GOLD 4" in gold.description, "current Gold rank should be visible"
        assert "Peak:** `GOLD 4" in gold.description, "peak should include current LP"
        assert str(gold.author.icon_url) == ranks.tier_image_url("GOLD"), "badge should follow current Gold LP"

        apex = profiles.player_profile_view("AntiChungleVenom#SG2").overview_embed()
        assert "GRANDMASTER #1027 — 419 LP" in apex.description
        assert "GRANDMASTER 1" not in apex.description
        assert "Peak:** `GRANDMASTER — 419 LP`" in apex.description
        assert apex.color.value == 0xFF6D00
        assert str(apex.author.icon_url) == ranks.tier_image_url("GRANDMASTER")

        missing = profiles.player_profile_view("MissingCurrent#TEST").overview_embed()
        assert missing.color.value == 0xC0C0C0, "missing current LP should use historical badge"
        assert "SILVER 1" in missing.description, "historical fallback should remain readable"
    finally:
        profiles.data = original_profile_data
        community.data = original_community_data

    print("Profile rank smoke checks passed.")


if __name__ == "__main__":
    main()
