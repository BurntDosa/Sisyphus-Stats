"""Verified Riot vision-score enrichment for OP.GG match details."""
from __future__ import annotations

import asyncio
from datetime import datetime
from urllib.parse import quote

import aiohttp

from .config import PLATFORM, REGION, RIOT_KEY
from .state import data


def optional_score(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def identity(participant, riot=False):
    name = participant.get("riotIdGameName" if riot else "gameName")
    tag = participant.get("riotIdTagline" if riot else "tagLine")
    if not name or not tag:
        return None
    return str(name).casefold(), str(tag).casefold()


def verified_scores(match, riot_match):
    """Return updates only when the game and every participant agree."""
    try:
        local = match["info"]
        remote = riot_match["info"]
        when = datetime.fromisoformat(local["gameCreation"].replace("Z", "+00:00"))
        if when.tzinfo is None or local["queueId"] != remote["queueId"]:
            return []
        # OP.GG can use match completion time for its created_at field.
        timestamps = [remote["gameCreation"]]
        if isinstance(remote.get("gameEndTimestamp"), (int, float)):
            timestamps.append(remote["gameEndTimestamp"])
        if min(abs(when.timestamp() - timestamp / 1000) for timestamp in timestamps) > 120:
            return []
        if abs(local["gameDuration"] - remote["gameDuration"]) > 5:
            return []
        indexed = {identity(p, True): p for p in remote["participants"]}
        if None in indexed or len(indexed) != len(remote["participants"]):
            return []
        updates = []
        if len(local["participants"]) != len(remote["participants"]):
            return []
        seen = set()
        for participant in local["participants"]:
            key = identity(participant)
            source = indexed.get(key)
            if source is None or key in seen:
                return []
            seen.add(key)
            if any(participant.get(k) != source.get(k) for k in ("championId", "kills", "deaths", "assists")):
                return []
            score = optional_score(source.get("visionScore"))
            if participant.get("visionScore") is None and score is not None:
                updates.append((participant, score))
        return updates
    except (KeyError, TypeError, ValueError, AttributeError):
        return []


async def enrich_vision(session, match):
    if not RIOT_KEY or match["info"].get("queueId") != 420 or not any(p.get("visionScore") is None for p in match["info"]["participants"]):
        return
    participants = match["info"]["participants"]
    tracked = next((t for t in data.get("tracked", {}).values()
                    if t.get("puuid") and any(identity(p) == (
                        str(t.get("game_name", "")).casefold(),
                        str(t.get("tag_line", "")).casefold()) for p in participants)), None)
    if not tracked:
        return
    try:
        when = datetime.fromisoformat(match["info"]["gameCreation"].replace("Z", "+00:00"))
        if when.tzinfo is None:
            return
        region = "sea" if PLATFORM.lower() == "sg2" else REGION
        base = f"https://{region}.api.riotgames.com/lol/match/v5/matches"
        headers = {"X-Riot-Token": RIOT_KEY}
        async with asyncio.timeout(15):
            async with session.get(
                f"{base}/by-puuid/{quote(tracked['puuid'], safe='')}/ids",
                params={"queue": match["info"]["queueId"], "startTime": int(when.timestamp()) - 120,
                        "endTime": int(when.timestamp()) + 120, "count": 3}, headers=headers,
            ) as response:
                if response.status != 200:
                    return
                ids = await response.json()
            if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str):
                return
            async with session.get(f"{base}/{quote(ids[0], safe='')}", headers=headers) as response:
                if response.status != 200:
                    return
                remote = await response.json()
            for participant, score in verified_scores(match, remote):
                participant["visionScore"] = score
                participant["visionScoreSource"] = "riot-match-v5"
    except (aiohttp.ClientError, TimeoutError, ValueError, TypeError, KeyError, AttributeError):
        # Missing enrichment must not block a recap or expose request credentials.
        return
