"""Provider-confirmed, additive history repair. No Discord or betting actions."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone

from .match_processing import outcome, parsed, tracked_participants


def verified_match(mid, match):
    info = (match or {}).get("info", {})
    parts = info.get("participants", [])
    return (str((match or {}).get("metadata", {}).get("matchId")) == str(mid)
        and info.get("queueId") == 420 and parsed(info.get("gameCreation")) is not None
        and len(parts) == 10 and len({p.get("puuid") for p in parts if p.get("puuid")}) == 10
        and Counter(p.get("teamId") for p in parts) == {100: 5, 200: 5})


def propose_repairs(state, confirmed_matches, history_builder):
    report = {"additions": [], "duplicates": [], "unavailable": [], "unknown_lp": []}
    indexes = {}
    for rid, rows in state.get("history", {}).items():
        counts = Counter(str(row["match_id"]) for row in rows if row.get("match_id"))
        indexes[rid] = set(counts)
        report["duplicates"].extend({"player": rid, "match_id": mid, "count": count}
            for mid, count in counts.items() if count > 1)
    for mid, match in confirmed_matches.items():
        if not verified_match(mid, match):
            report["unavailable"].append(str(mid))
            continue
        roster = tracked_participants(state, match)
        for rid, participant in roster.items():
            if str(mid) in indexes.get(rid, set()):
                continue
            result = outcome(participant, match["info"]["gameDuration"])
            if result is None:
                report["unavailable"].append(f"{mid}/{rid}: missing result")
                continue
            row = history_builder(match, participant, rid, result, 0, None, None)
            row.update(lp_change=None, lp_before=None, lp_total=None, lp_status="unavailable",
                reconciled=False, recovered=True, recovery_source="provider-confirmed match details",
                recovered_at=datetime.now(timezone.utc).isoformat(),
                match_created_at=match["info"]["gameCreation"], team_id=participant["teamId"])
            # Share an existing teammate's receipt; historical messages are not replayed.
            for other, other_part in roster.items():
                if other_part["teamId"] != participant["teamId"]:
                    continue
                old = next((r for r in state.get("history", {}).get(other, []) if str(r.get("match_id")) == str(mid)), {})
                if old.get("recap_message_id"):
                    row.update({k: old.get(k) for k in ("recap_channel_id", "recap_message_id", "recap_jump_url")})
                    break
            report["additions"].append({"player": rid, "match_id": str(mid), "row": row})
            report["unknown_lp"].append({"player": rid, "match_id": str(mid)})
    report["additions"].sort(key=lambda item: (item["row"]["match_created_at"], item["player"].casefold()))
    return report


def apply_additions(state, report):
    result = deepcopy(state)
    affected = set()
    for item in report["additions"]:
        rows = result.setdefault("history", {}).setdefault(item["player"], [])
        if any(str(r.get("match_id")) == item["match_id"] for r in rows):
            continue
        rows.append(deepcopy(item["row"]))
        affected.add(item["player"])
    for rid in affected:
        result["history"][rid].sort(key=lambda row: (
            str(row.get("date") or ""), str(row.get("match_created_at") or row.get("recorded_at") or "")))
    return result, affected


def recompute_derived(state, affected):
    if not affected:
        return
    # The existing derivation functions use module globals. Bind them temporarily
    # to the fresh offline snapshot and suppress their writes until final commit.
    from . import community, profiles
    original = [(mod, mod.data, mod.save_data) for mod in (community, profiles)]
    try:
        for mod, _, _ in original:
            mod.data = state
            mod.save_data = lambda _state: None
        state.setdefault("community", {})["records"] = {}
        ordered = [(rid, row) for rid, rows in state.get("history", {}).items() for row in rows]
        ordered.sort(key=lambda item: str(item[1].get("match_created_at") or item[1].get("date") or ""))
        for rid, row in ordered:
            community.update_records(rid, row)
        milestones = state["community"].setdefault("milestones", {})
        for rid in affected:
            milestones[rid] = [m for m in milestones.get(rid, []) if not (
                str(m.get("key", "")).startswith(("first_", "games_", "champ_", "wins_", "win_streak_", "peak_")))]
            profiles.ensure_player_milestones(rid)
    finally:
        for mod, old_data, old_save in original:
            mod.data, mod.save_data = old_data, old_save
