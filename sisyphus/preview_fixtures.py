"""Representative fictional match snapshots for embed review; no runtime imports."""
from copy import deepcopy
from datetime import datetime, timezone


def duo_fixture(scenario="victory"):
    if scenario not in {"victory", "defeat", "remake", "missing-data"}:
        raise ValueError(f"Unknown scenario: {scenario}")
    outcome = {"defeat": "LOSS", "remake": "DRAW"}.get(scenario, "WIN")
    parts = []
    rows = [
        ("xZEUSx", "5775", "Renekton", 58, "TOP", 8, 3, 10, 25100, 14100, 225, 22),
        ("CantMarshall", "6005", "Anivia", 34, "MID", 3, 1, 14, 28400, 13500, 246, 28),
        ("Jungle", "DEMO", "Vi", 254, "JUNGLE", 3, 5, 10, 17000, 11000, 160, 31),
        ("Carry", "DEMO", "Jhin", 202, "BOTTOM", 5, 4, 7, 21000, 14000, 250, 15),
        ("Support", "DEMO", "Leona", 89, "SUPPORT", 5, 5, 12, 8100, 9000, 41, 54),
    ]
    for tid in (100, 200):
        for index, (game, tag, champion, cid, role, k, d, a, damage, gold, cs, vision) in enumerate(rows):
            win = (outcome == "WIN") == (tid == 100) and outcome != "DRAW"
            p = {"puuid": f"fixture-{tid}-{index}", "gameName": game if tid == 100 else f"Opponent {index+1}",
                 "tagLine": tag, "championName": champion, "championId": cid, "position": role,
                 "teamId": tid, "kills": k, "deaths": d, "assists": a, "win": win,
                 "result_code": "REMAKE" if outcome == "DRAW" else "WIN" if win else "LOSE",
                 "totalDamageDealtToChampions": damage, "goldEarned": gold,
                 "totalMinionsKilled": cs, "neutralMinionsKilled": 0, "visionScore": vision,
                 "visionScoreSource": "fixture", "wardsPlaced": 12, "wardsKilled": 3,
                 "controlWardsBought": 2, "champLevel": 17,
                 "itemNames": ["Boots", "Core item", "Situational item"]}
            p["itemNames"] = (["Plated Steelcaps", "Black Cleaver", "Sterak's Gage", "Death's Dance"]
                if index == 0 else ["Sorcerer's Shoes", "Malignance", "Seraph's Embrace", "Rabadon's Deathcap"])
            if tid == 200 and outcome != "DRAW":
                p.update(kills=2 + index, deaths=5 + index, assists=3 + index)
            if outcome == "DRAW":
                p.update(kills=0, deaths=0, assists=0, totalDamageDealtToChampions=40 + index * 12,
                    goldEarned=500 + index * 10, totalMinionsKilled=5 + index, visionScore=0,
                    wardsPlaced=0, wardsKilled=0, controlWardsBought=0, champLevel=1,
                    itemNames=["Doran's Ring", "Stealth Ward"])
            for n, item in enumerate([3047, 3071, 3053, 6333, 0, 0, 3340] if index == 0 else [3020, 6657, 3040, 3089, 0, 0, 3363]):
                p[f"item{n}"] = item
            if outcome == "DRAW":
                for n, item in enumerate([1056, 0, 0, 0, 0, 0, 3340]):
                    p[f"item{n}"] = item
            parts.append(p)
    match_id = "fixture-" + scenario
    match = {"metadata": {"matchId": match_id}, "info": {"queueId": 420,
        "gameCreation": datetime.now(timezone.utc).isoformat(), "gameDuration": 90 if outcome == "DRAW" else 1842,
        "participants": parts, "teams": [
            {"teamId": 100, "objectives": {"dragon": {"kills": 3}, "baron": {"kills": 1}, "champion": {"first": True}, "tower": {"first": True}}},
            {"teamId": 200, "objectives": {"dragon": {"kills": 1}, "baron": {"kills": 0}, "champion": {"first": False}, "tower": {"first": False}}},
        ]}}
    if outcome == "DRAW":
        for team in match["info"]["teams"]:
            team["objectives"] = {"dragon": {"kills": 0}, "baron": {"kills": 0}}
    roster = []
    for i, p in enumerate(parts[:2]):
        delta = 0 if outcome == "DRAW" else -21 if outcome == "LOSS" else 20 + i
        roster.append({"riot_id": p['gameName'] + "#" + p['tagLine'], "puuid": p['puuid'],
            "participant": deepcopy(p), "tier": "GOLD", "rank": "II", "lp": 64 + i,
            "old_lp": 1844, "total_lp": 1844 + delta, "lp_delta": delta, "lp_status": "known"})
    if scenario == "missing-data":
        roster[1].update(lp_delta=None, old_lp=None, lp_status="unavailable")
        roster[1]["participant"]["visionScore"] = None
        parts[1]["visionScore"] = None
    return {"key": f"{match_id}:100", "match": match, "team_id": 100,
            "outcome": outcome, "members": roster}
