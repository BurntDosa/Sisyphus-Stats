"""Build the latest-match recap view for !recap and on-poll posts."""
from __future__ import annotations

import aiohttp

from .opgg import (
    get_lp_info,
    get_match,
    get_ranked_stats,
    get_recent_matches,
)
from .outcome import match_outcome, parse_lp_change
from .state import data, save_data
from .views import ScoreboardView


async def build_latest_recap(
    session: aiohttp.ClientSession, riot_id: str, info: dict
):
    game_name = info.get("game_name")
    tag_line = info.get("tag_line")
    if not game_name or not tag_line:
        return None, None, "❌ Missing tracked Riot ID for this player."

    recent = await get_recent_matches(session, game_name, tag_line, count=20)
    recent_ranked = [m for m in recent if m.get("game_type") == "SOLORANKED"]
    if not recent_ranked:
        return None, None, "❌ No recent ranked solo matches found."

    latest = recent_ranked[0]
    match_id = latest.get("id")
    created_at = latest.get("created_at")
    if not match_id or not created_at:
        return None, None, "❌ Latest match data is incomplete."

    match = await get_match(session, match_id, created_at)
    if not match or match.get("info", {}).get("queueId") != 420:
        return None, None, "❌ Could not load the latest ranked solo match."

    # Re-fetch ranked LP AFTER we've identified the latest match — this avoids the
    # LP-race where the match feed updates faster than the ranked profile and we'd
    # otherwise compute lp_delta against pre-match LP.
    ranked = await get_ranked_stats(session, game_name, tag_line)
    if ranked is None:
        return None, None, "❌ OP.GG ranked stats fetch failed — try again."
    tier, rank, lp, total_lp = get_lp_info(ranked)

    puuid = info.get("puuid")
    participant = next(
        (p for p in match["info"]["participants"] if p.get("puuid") == puuid), None
    )
    if not participant:
        participant = next(
            (
                p
                for p in match["info"]["participants"]
                if (p.get("gameName") or "").lower() == game_name.lower()
                and (p.get("tagLine") or "").lower() == tag_line.lower()
            ),
            None,
        )
    if participant and participant.get("puuid") and participant["puuid"] != puuid:
        info["puuid"] = participant["puuid"]
        data["tracked"][riot_id]["puuid"] = participant["puuid"]
        save_data(data)

    history_rows = data.get("history", {}).get(riot_id, [])
    latest_history = next(
        (h for h in reversed(history_rows) if h.get("match_id") == match_id), None
    )

    old_lp = info.get("last_known_lp")
    recap_total_lp = total_lp
    if latest_history:
        stored_total = latest_history.get("lp_total")
        stored_before = latest_history.get("lp_before")
        stored_delta = parse_lp_change(latest_history.get("lp_change"))

        if isinstance(stored_total, int):
            recap_total_lp = stored_total
        if isinstance(stored_before, int):
            old_lp = stored_before
        elif stored_delta is not None:
            old_lp = recap_total_lp - stored_delta

    # Sanity check classification before constructing the view.
    if participant is not None:
        lp_delta = (recap_total_lp - old_lp) if old_lp is not None else 0
        outcome = match_outcome(
            participant.get("result_code"),
            lp_delta,
            match.get("info", {}).get("gameDuration"),
        )
        if outcome is None:
            return None, None, "❌ Match data incomplete from OP.GG — try again shortly."

    tracked_puuid = info.get("puuid") or puuid
    view = ScoreboardView(
        match, tracked_puuid, riot_id, tier, rank, lp, old_lp, recap_total_lp
    )
    await view.prepare(session)
    return view, view.get_overview_kwargs(), None
