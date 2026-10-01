"""Latest-match overview shared by tracked teammates, for either member's /recap."""
from __future__ import annotations

from copy import deepcopy

from .match_processing import history_row, lp_value, outcome, tracked_participants
from .opgg import get_lp_info, get_match, get_ranked_stats, get_recent_matches
from .recap_delivery import build_recap_view
from .state import data


async def build_latest_recap(session, riot_id: str, info: dict):
    if not info.get("game_name") or not info.get("tag_line"):
        return None, None, "❌ Missing tracked Riot ID for this player."
    recent = await get_recent_matches(session, info["game_name"], info["tag_line"], count=20)
    ranked = [m for m in (recent or []) if m.get("game_type") == "SOLORANKED"]
    if not ranked:
        return None, None, "❌ No recent ranked Solo/Duo matches found."
    latest = ranked[0]
    match = await get_match(session, latest.get("id"), latest.get("created_at"))
    if not match or match.get("info", {}).get("queueId") != 420:
        return None, None, "❌ Could not load the latest ranked Solo/Duo match."
    participants = tracked_participants(data, match)
    if riot_id not in participants:
        return None, None, "❌ This player could not be verified in the match."
    team_id = participants[riot_id]["teamId"]
    mid = str(match["metadata"]["matchId"])
    key = f"{mid}:{team_id}"
    saved = data.get("match_recaps", {}).get(key)
    if saved and saved.get("match"):
        snapshot = deepcopy(saved)
    else:
        roster = []
        for rid, part in sorted(participants.items(), key=lambda item: item[0].casefold()):
            if part["teamId"] != team_id:
                continue
            tracked = data["tracked"][rid]
            raw = await get_ranked_stats(session, tracked["game_name"], tracked["tag_line"])
            tier, rank, lp, _ = get_lp_info(raw) if raw is not None else (None, None, None, None)
            row = history_row(data, rid, mid) or {}
            delta = lp_value(row.get("lp_change"))
            roster.append({"riot_id": rid, "puuid": part.get("puuid"), "participant": part,
                "tier": tier, "rank": rank, "lp": lp, "old_lp": row.get("lp_before"),
                "total_lp": row.get("lp_total"), "lp_delta": delta,
                "lp_status": "known" if delta is not None else "unavailable",
                "story_headline": row.get("story_headline"), "spotlights": row.get("spotlights", [])})
        result = outcome(participants[riot_id], int(match["info"].get("gameDuration") or 0))
        if result is None:
            return None, None, "❌ Match result is incomplete — try again shortly."
        snapshot = {"key": key, "match": match, "team_id": team_id, "outcome": result, "members": roster}
    view = await build_recap_view(snapshot, session)
    return view, view.get_overview_kwargs(), None
