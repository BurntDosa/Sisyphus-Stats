"""Collect all feeds before processing a completed match's tracked roster."""
from __future__ import annotations

from datetime import timedelta

from .match_processing import MatchProcessor, parsed, tracked_participants


async def process_completed_matches(state, *, recent_fetch, match_fetch, rank_fetch,
                                    processor: MatchProcessor, destination=None, enrich_match=None):
    baselines = {rid: info.get("last_known_lp") for rid, info in state["tracked"].items()}
    feeds, unseen_ids = {}, {}
    fetches = state.setdefault("match_fetches", {})
    for rid, tracked in list(state["tracked"].items()):
        recent = await recent_fetch(rid)
        if recent is None:
            continue
        ranked = [e for e in recent if e.get("game_type") == "SOLORANKED" and e.get("id")]
        feeds[rid] = ranked
        ids = set()
        for entry in ranked:
            if str(entry["id"]) == str(tracked.get("last_match_id")):
                tracked.setdefault("last_match_created_at", entry.get("created_at"))
                break
            ids.add(str(entry["id"]))
            if entry.get("created_at"):
                pending = fetches.setdefault(str(entry["id"]), {"created_at": entry["created_at"], "sources": []})
                if rid not in pending["sources"]:
                    pending["sources"].append(rid)
        unseen_ids[rid] = ids
    processor.save(state)
    matches = []
    def staged(match):
        mid = str(match["metadata"]["matchId"])
        teams = {p["teamId"] for p in tracked_participants(state, match).values()}
        return bool(teams) and all(f"{mid}:{team}" in processor.receipts for team in teams)

    for mid, reference in sorted(list(fetches.items()), key=lambda entry: entry[1]["created_at"]):
        # Durable receipts, rather than the first player's feed, deduplicate work.
        saved = next((r for r in processor.receipts.values() if r.get("match_id") == mid), None)
        if saved and (not saved.get("match") or staged(saved["match"])):
            fetches.pop(mid, None)
            continue
        retry = parsed(reference.get("retry_at"))
        if retry and retry > processor.now():
            continue
        match = await match_fetch(mid, reference["created_at"])
        reference["attempts"] = reference.get("attempts", 0) + 1
        reference["retry_at"] = (processor.now() + timedelta(seconds=min(3600, 30 * 2 ** min(reference["attempts"], 7)))).isoformat()
        if match and str(match.get("metadata", {}).get("matchId")) != mid:
            continue
        if match and match.get("info", {}).get("queueId") == 420 and parsed(match["info"].get("gameCreation")):
            matches.append(match)
        elif match and match.get("info", {}).get("queueId") != 420:
            fetches.pop(mid, None)
    matches.sort(key=lambda m: parsed(m["info"]["gameCreation"]))
    missing = {rid: set(ids) for rid, ids in unseen_ids.items()}
    for mid, reference in fetches.items():
        for rid in reference["sources"]:
            missing.setdefault(rid, set()).add(mid)
    for match in matches:
        mid = str(match["metadata"]["matchId"])
        if enrich_match:
            await enrich_match(match)
        for rid in tracked_participants(state, match):
            missing.setdefault(rid, set()).add(mid)
    counts = {rid: len(ids) for rid, ids in missing.items()}
    for rid, tracked in state["tracked"].items():
        # An unresolved preceding game means the next rank movement covers a
        # backlog even if the recent feed now exposes only one new match.
        previous_pending = any(m.get("can_reconcile") and m.get("lp_status") != "known"
            for record in processor.receipts.values() if record.get("match_id") == tracked.get("last_match_id")
            for m in record["members"] if m["riot_id"] == rid)
        if missing.get(rid) and previous_pending:
            counts[rid] = max(2, counts.get(rid, 0))
    ranks = {}
    for rid in state["tracked"]:
        if missing.get(rid):
            ranks[rid] = await rank_fetch(rid) or {}
    for match in matches:
        mid = str(match["metadata"]["matchId"])
        per_match = {rid: dict(rank, ready_for_match=bool(feeds.get(rid) and str(feeds[rid][0]["id"]) == mid))
                     for rid, rank in ranks.items()}
        processor.stage(match, per_match, baselines, counts)
        if staged(match):
            fetches.pop(mid, None)

    async def latest_rank(rid, mid):
        # Refresh the partner feed even when it lagged at the discovery stage.
        recent = await recent_fetch(rid)
        if recent is None:
            return None
        ranked = [e for e in recent if e.get("game_type") == "SOLORANKED" and e.get("id")]
        if not ranked or str(ranked[0]["id"]) != mid:
            return None
        rank = await rank_fetch(rid)
        return dict(rank, ready_for_match=True) if rank else None

    await processor.refresh_lp(latest_rank)
    await processor.flush(destination)
    # Move the baseline to a verified current snapshot after an unattributable
    # backlog. Its aggregate movement is never copied to the historical rows.
    for rid, rank in ranks.items():
        tracked = state["tracked"][rid]
        if counts.get(rid, 0) > 1 and feeds.get(rid) and str(tracked.get("last_match_id")) == str(feeds[rid][0]["id"]):
            pending = any(m.get("can_reconcile") and m.get("lp_status") != "known"
                for r in processor.receipts.values() if r.get("match_id") == tracked["last_match_id"]
                for m in r["members"] if m["riot_id"] == rid)
            if not pending and rank.get("total_lp") is not None:
                tracked["last_known_lp"] = rank["total_lp"]
    processor.save(state)
    return feeds, unseen_ids
