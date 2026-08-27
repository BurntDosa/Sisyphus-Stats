"""Persistent state — data.json load/save and in-memory singletons."""
from __future__ import annotations

import json
import os

from .config import DATA_FILE


def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            loaded = json.load(f)
            if isinstance(loaded, dict):
                loaded.setdefault("tracked", {})
                loaded.setdefault("daily_lp", {})
                loaded.setdefault("history", {})
                loaded.setdefault("links", {})
                changelog = loaded.setdefault("changelog", {})
                changelog.setdefault("last_processed_version", None)
                changelog.setdefault("last_processed_sha", None)
                changelog.setdefault("v2_curated_processed_sha", None)
                changelog.setdefault("curated_processed_versions", {})
                community = loaded.setdefault("community", {})
                community.setdefault("queue_board", {})
                community.setdefault("rivalries", {})
                community.setdefault("rivalry_invites", {})
                community.setdefault("squad_goals", {})
                community.setdefault("active_announcements", {})
                community.setdefault("weekly_recaps", {})
                community.setdefault("records", {})
                live = community.setdefault("live_rooms", {})
                live.setdefault("active", {})
                live.setdefault("history", {})
                community.setdefault("memories", {})
                community.setdefault("milestones", {})
                community.setdefault("monthly_recaps", {})
                community.setdefault("historical_events", [])
                betting = loaded.setdefault("betting", {})
                betting.setdefault("wallets", {})
                betting.setdefault("markets", {})
                betting.setdefault("bets", {})
                betting.setdefault("audit", [])
                betting.setdefault("meta", {})
                loaded.pop("gif_state", None)  # legacy field, no longer used
                return loaded
    return {
        "tracked": {},
        "daily_lp": {},
        "history": {},
        "links": {},
        "changelog": {
            "last_processed_version": None,
            "last_processed_sha": None,
            "v2_curated_processed_sha": None,
            "curated_processed_versions": {},
        },
        "community": {
            "queue_board": {},
            "rivalries": {},
            "rivalry_invites": {},
            "squad_goals": {},
            "active_announcements": {},
            "weekly_recaps": {},
            "records": {},
            "live_rooms": {
                "active": {},
                "history": {},
            },
            "memories": {},
            "milestones": {},
            "monthly_recaps": {},
            "historical_events": [],
        },
        "betting": {
            "wallets": {},
            "markets": {},
            "bets": {},
            "audit": [],
            "meta": {},
        },
    }


def save_data(d: dict) -> None:
    d.pop("gif_state", None)  # never write the legacy field back
    with open(DATA_FILE, "w") as f:
        json.dump(d, f, indent=2)


def migrate_tracked_data() -> None:
    changed = False
    for riot_id, info in data.get("tracked", {}).items():
        if "game_name" not in info or "tag_line" not in info:
            if "#" in riot_id:
                game_name, tag_line = riot_id.split("#", 1)
                info.setdefault("game_name", game_name)
                info.setdefault("tag_line", tag_line)
                changed = True
    if changed:
        save_data(data)


data: dict = load_data()
posted_matches: set[str] = set()

migrate_tracked_data()
