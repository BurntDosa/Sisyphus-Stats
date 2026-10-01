"""OP.GG MCP client and Riot-shaped data transforms.

All error paths log to stdout so failures show up in the active process logs.
"""
from __future__ import annotations

import ast
import re

import aiohttp

from .config import OPGG_MCP_URL, OPGG_REGION
from .outcome import canonical_outcome
from .ranks import DIV_ORDER, TIER_ORDER
from .utils import as_list, match_day_ist, now_ist, today_ist
from .vision import enrich_vision, optional_score

CLASS_DEF_RE = re.compile(r"^class ([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")
QUEUE_ID_MAP = {"SOLORANKED": 420, "FLEXRANKED": 440}
OPGG_LEADERBOARD_URL = "https://op.gg/lol/leaderboards/tier"

_mcp_request_id = 0


def _next_mcp_request_id() -> int:
    global _mcp_request_id
    _mcp_request_id += 1
    return _mcp_request_id


_NAME_CONSTANTS = {"null": None, "true": True, "false": False}


def parse_mcp_content_text(text: str):
    """Parse OP.GG MCP's Python-class-constructor response into nested dicts.

    OP.GG MCP returns responses as Python-class-constructor calls (e.g.
    `RankedInfo(GOLD, II, 42)`) rather than JSON. We safe-parse it via
    `ast.parse` + a strict whitelist walker that only allows: positional
    calls to declared class names, constants, lists/tuples, unary-minus on
    numbers, and the literal names `null`/`true`/`false`. Any other AST node
    (attribute access, subscript, lambda, comprehension, dunder, etc.)
    causes the parse to fail closed.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    class_fields: dict[str, list[str]] = {}
    expression = None

    for line in lines:
        m = CLASS_DEF_RE.match(line)
        if m:
            name, fields_blob = m.groups()
            fields = [f.strip() for f in fields_blob.split(",") if f.strip()]
            class_fields[name] = fields
            continue
        if ("(" in line and line.endswith(")")) or (line.startswith("[") and line.endswith("]")):
            expression = line

    if not expression:
        return None

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        print(f"[opgg] parse_mcp_content_text syntax error: {exc}")
        return None

    def walk(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            inner = walk(node.operand)
            if isinstance(inner, (int, float)):
                return -inner
            raise ValueError("unary minus on non-number")
        if isinstance(node, (ast.List, ast.Tuple)):
            return [walk(elt) for elt in node.elts]
        if isinstance(node, ast.Name):
            if node.id in _NAME_CONSTANTS:
                return _NAME_CONSTANTS[node.id]
            raise ValueError(f"unknown name {node.id!r}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("call target must be a bare Name")
            class_name = node.func.id
            if class_name not in class_fields:
                raise ValueError(f"unknown class {class_name!r}")
            if node.keywords:
                raise ValueError(f"keyword args not allowed in {class_name!r}")
            args = [walk(a) for a in node.args]
            fields = class_fields[class_name]
            out = {}
            for i, arg in enumerate(args):
                key = fields[i] if i < len(fields) else f"field_{i}"
                out[key] = arg
            return out
        raise ValueError(f"AST node {type(node).__name__} not allowed")

    try:
        return walk(tree.body)
    except (ValueError, RecursionError) as exc:
        print(f"[opgg] parse_mcp_content_text rejected: {exc}")
        return None


async def opgg_call_tool(session: aiohttp.ClientSession, name: str, arguments: dict):
    from .health import mark_dependency

    payload = {
        "jsonrpc": "2.0",
        "id": _next_mcp_request_id(),
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    try:
        async with session.post(OPGG_MCP_URL, json=payload) as r:
            body = await r.json(content_type=None)
    except Exception as e:
        msg = f"Network error: {type(e).__name__}: {e}"
        print(f"[opgg] tool={name} {msg}")
        mark_dependency(
            "opgg",
            False,
            success_message="OP.GG responding",
            failure_message="OP.GG connection failed",
        )
        return None, msg

    if "error" in body:
        msg = body["error"].get("message", "Unknown MCP error")
        print(f"[opgg] tool={name} server error: {msg}")
        expected = "summoner not found" in msg.lower()
        mark_dependency(
            "opgg",
            expected,
            success_message="OP.GG responding",
            failure_message="OP.GG request failed",
        )
        return None, msg

    content = body.get("result", {}).get("content", [])
    text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
    parsed = parse_mcp_content_text(text)
    if parsed is None:
        print(f"[opgg] tool={name} malformed response: {text[:200]!r}")
        mark_dependency(
            "opgg",
            False,
            success_message="OP.GG responding",
            failure_message="OP.GG response invalid",
        )
        return None, "Malformed MCP tool response"
    mark_dependency(
        "opgg",
        True,
        success_message="OP.GG responding",
        failure_message="OP.GG request failed",
    )
    return parsed, None


def ranked_entries_from_profile(summoner: dict):
    entries = []
    for stat in as_list(summoner.get("league_stats")):
        if stat.get("game_type") != "SOLORANKED":
            continue
        tier_info = stat.get("tier_info") or {}
        entries.append(
            {
                "queueType": "RANKED_SOLO_5x5",
                "tier": (tier_info.get("tier") or "UNRANKED").upper(),
                "rank": str(tier_info.get("division") or ""),
                "leaguePoints": int(tier_info.get("lp") or 0),
                "wins": int(stat.get("win") or 0),
                "losses": int(stat.get("lose") or 0),
            }
        )
    return entries


def parse_opgg_leaderboard_rank(page: str, game_name: str, tag_line: str) -> int | None:
    """Extract a highlighted summoner's position from OP.GG's leaderboard page."""
    summoner_id = f"{game_name}-{tag_line}"
    # The Next.js flight payload stores the row as escaped JSON inside a script.
    normalized = (page or "").replace(r'\"', '"').replace(r"\u0026", "&")
    row_pattern = re.compile(
        rf'"id":"{re.escape(summoner_id)}".{{0,1200}}?"children":(\d+)',
        re.DOTALL,
    )
    match = row_pattern.search(normalized)
    if not match:
        return None
    try:
        rank = int(match.group(1))
    except ValueError:
        return None
    return rank if rank > 0 else None


async def get_opgg_leaderboard_rank(s, game_name: str, tag_line: str) -> tuple[int | None, str | None]:
    """Return an exact SEA leaderboard position for an apex Riot ID."""
    if not game_name or not tag_line:
        return None, "Missing Riot ID"
    try:
        async with s.get(
            OPGG_LEADERBOARD_URL,
            params={"region": OPGG_REGION.lower(), "summoner": f"{game_name}-{tag_line}"},
            headers={"User-Agent": "Sisyphus-Bot apex leaderboard monitor"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status != 200:
                return None, f"Leaderboard HTTP {response.status}"
            rank = parse_opgg_leaderboard_rank(await response.text(), game_name, tag_line)
    except (aiohttp.ClientError, TimeoutError) as exc:
        return None, f"Leaderboard request failed: {type(exc).__name__}"
    if rank is None:
        return None, "Leaderboard position not found"
    return rank, None


async def get_summoner_profile(s, game_name, tag_line):
    parsed, err = await opgg_call_tool(
        s,
        "lol_get_summoner_profile",
        {
            "game_name": game_name,
            "tag_line": tag_line,
            "region": OPGG_REGION,
            "desired_output_fields": [
                "data.summoner.{game_name,tagline,puuid,internal_name}",
                "data.summoner.league_stats[].{game_type,win,lose}",
                "data.summoner.league_stats[].tier_info.{tier,division,lp}",
            ],
        },
    )
    if err:
        return None, err
    summoner = ((parsed.get("data") or {}).get("summoner")) if parsed else None
    if not isinstance(summoner, dict):
        print(f"[opgg] summoner payload missing for {game_name}#{tag_line}")
        return None, "Summoner payload missing"
    return summoner, None


async def get_ranked_stats(s, game_name, tag_line):
    """Returns a list of ranked-queue entries.

    - `[]` means the player has no ranked games this season.
    - `None` means the OP.GG fetch failed — caller should treat as transient
      and skip this polling cycle for this player.
    """
    if not game_name or not tag_line:
        return None
    summoner, err = await get_summoner_profile(s, game_name, tag_line)
    if err:
        print(f"[opgg] get_ranked_stats {game_name}#{tag_line} failed: {err}")
        return None
    if not summoner:
        return None
    return ranked_entries_from_profile(summoner)


def _analysis_item_set(value) -> dict:
    if not isinstance(value, dict):
        return {"ids": [], "names": []}
    ids = []
    for item_id in as_list(value.get("ids")):
        try:
            parsed = int(item_id)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            ids.append(parsed)
    names = [str(name) for name in as_list(value.get("ids_names")) if name]
    if len(names) != len(ids):
        names = [f"Item {item_id}" for item_id in ids]
    return {"ids": ids, "names": names}


STAT_SHARD_NAMES = {
    5001: "Health Scaling",
    5002: "Armor",
    5003: "Magic Resist",
    5005: "Attack Speed",
    5007: "Ability Haste",
    5008: "Adaptive Force",
}


def _analysis_item_options(data: dict, field: str) -> list[dict]:
    """Keep each OP.GG item choice separate instead of inventing a fixed path."""
    options: list[dict] = []
    seen: set[tuple[int, ...]] = set()
    for value in as_list(data.get(field)):
        item_set = _analysis_item_set(value)
        signature = tuple(item_set["ids"])
        if not signature or signature in seen:
            continue
        seen.add(signature)
        options.append(item_set)
    return options


def _opgg_champion_slug(champion: str) -> str:
    cleaned = champion.upper().replace("'", "").replace(".", "").replace("&", "AND")
    return re.sub(r"[^A-Z0-9]+", "_", cleaned).strip("_")


async def get_champion_analysis(s, champion: str, position: str):
    """Return the primary OP.GG build for a champion and verified lane."""
    parsed, err = await opgg_call_tool(
        s,
        "lol_get_champion_analysis",
        {
            "game_mode": "ranked",
            "champion": _opgg_champion_slug(champion),
            "position": position,
            "lang": "en_US",
            "desired_output_fields": [
                "champion",
                "position",
                "data.runes.{primary_page_id,primary_page_name,primary_rune_ids[],primary_rune_names[],secondary_page_id,secondary_page_name,secondary_rune_ids[],secondary_rune_names[],stat_mod_ids[],stat_mod_names[]}",
                "data.summoner_spells.{ids[],ids_names[]}",
                "data.starter_items.{ids[],ids_names[]}",
                "data.boots.{ids[],ids_names[]}",
                "data.core_items.{ids[],ids_names[]}",
                "data.fourth_items[].{ids[],ids_names[]}",
                "data.fifth_items[].{ids[],ids_names[]}",
                "data.sixth_items[].{ids[],ids_names[]}",
                "data.skills.{order[]}",
                "data.skill_masteries.{ids[]}",
            ],
        },
    )
    if err:
        return None, err
    data = (parsed or {}).get("data")
    if not isinstance(data, dict):
        return None, "Champion build payload missing"
    runes = data.get("runes")
    if not isinstance(runes, dict):
        return None, "Champion rune recommendation missing"
    stat_ids = [int(value) for value in as_list(runes.get("stat_mod_ids")) if value]
    normalized_runes = {
        "primary_page": str(runes.get("primary_page_name") or "Primary"),
        "primary_ids": [int(value) for value in as_list(runes.get("primary_rune_ids")) if value],
        "primary_names": [str(value) for value in as_list(runes.get("primary_rune_names")) if value],
        "secondary_page": str(runes.get("secondary_page_name") or "Secondary"),
        "secondary_ids": [int(value) for value in as_list(runes.get("secondary_rune_ids")) if value],
        "secondary_names": [str(value) for value in as_list(runes.get("secondary_rune_names")) if value],
        "stat_ids": stat_ids,
        # OP.GG can return raw shard IDs in stat_mod_names. Use the canonical
        # game-data labels so a loadout never exposes those implementation IDs.
        "stat_names": [STAT_SHARD_NAMES.get(value, f"Stat shard {value}") for value in stat_ids],
    }
    spells = _analysis_item_set(data.get("summoner_spells"))
    starter = _analysis_item_set(data.get("starter_items"))
    boots = _analysis_item_set(data.get("boots"))
    core = _analysis_item_set(data.get("core_items"))
    skills = [str(value) for value in as_list((data.get("skills") or {}).get("order")) if value]
    masteries = [
        str(value) for value in as_list((data.get("skill_masteries") or {}).get("ids")) if value
    ]
    required = (
        normalized_runes["primary_ids"],
        normalized_runes["secondary_ids"],
        normalized_runes["stat_ids"],
        spells["ids"],
        starter["ids"],
        boots["ids"],
        core["ids"],
        skills,
        masteries,
    )
    if not all(required):
        return None, "OP.GG returned an incomplete champion loadout"
    return {
        "champion": str((parsed or {}).get("champion") or champion),
        "position": str((parsed or {}).get("position") or position),
        "runes": normalized_runes,
        "spell_ids": spells["ids"],
        "starter": starter,
        "boots": boots,
        "core": core,
        "later_items": {
            "4th": _analysis_item_options(data, "fourth_items"),
            "5th": _analysis_item_options(data, "fifth_items"),
            "6th": _analysis_item_options(data, "sixth_items"),
        },
        "skills": skills,
        "skill_masteries": masteries,
    }, None


async def get_recent_matches(s, game_name, tag_line, count=1):
    if not game_name or not tag_line:
        return []
    parsed, err = await opgg_call_tool(
        s,
        "lol_list_summoner_matches",
        {
            "game_name": game_name,
            "tag_line": tag_line,
            "region": OPGG_REGION,
            "limit": max(5, min(20, int(count) if count else 5)),
            "desired_output_fields": [
                "data.game_history[].{id,game_type,created_at,game_length_second}",
                "data.game_history[].participants[].summoner.{game_name,tagline,puuid}",
                "data.game_history[].participants[].{champion_id,champion_name,team_key,items[],items_names[],position}",
                "data.game_history[].participants[].stats.{kill,death,assist,result,minion_kill,neutral_minion_kill,total_damage_dealt_to_champions,gold_earned,champion_level,vision_score,vision_wards_bought_in_game,ward_place,ward_kill}",
            ],
        },
    )
    if err:
        print(f"[opgg] get_recent_matches {game_name}#{tag_line} failed: {err}")
        return []
    history = ((parsed.get("data") or {}).get("game_history")) if parsed else None
    return as_list(history)


def opgg_participant_to_riot(participant: dict):
    stats = participant.get("stats") or {}
    summoner = participant.get("summoner") or {}
    result_code = str(stats.get("result") or "").upper()
    outcome = canonical_outcome(result_code) or "DRAW"
    vision_score = stats.get("vision_score")
    items = as_list(participant.get("items"))
    item_names = [str(name) for name in as_list(participant.get("items_names")) if name]
    mapped = {
        "puuid": summoner.get("puuid"),
        "gameName": summoner.get("game_name"),
        "tagLine": summoner.get("tagline"),
        "championId": int(participant.get("champion_id") or 0),
        "championName": participant.get("champion_name"),
        "position": participant.get("position"),
        "itemNames": item_names,
        "teamId": 100 if participant.get("team_key") == "BLUE" else 200,
        "win": outcome == "WIN",
        "result_code": result_code,
        "outcome": outcome,
        "kills": int(stats.get("kill") or 0),
        "deaths": int(stats.get("death") or 0),
        "assists": int(stats.get("assist") or 0),
        "totalMinionsKilled": int(stats.get("minion_kill") or 0),
        "neutralMinionsKilled": int(stats.get("neutral_minion_kill") or 0),
        "totalDamageDealtToChampions": int(
            stats.get("total_damage_dealt_to_champions") or 0
        ),
        "goldEarned": int(stats.get("gold_earned") or 0),
        "champLevel": int(stats.get("champion_level") or 0),
        "visionScore": optional_score(vision_score),
        "visionScoreSource": "opgg" if optional_score(vision_score) is not None else None,
        "controlWardsBought": int(stats.get("vision_wards_bought_in_game") or 0),
        "wardsPlaced": int(stats.get("ward_place") or 0),
        "wardsKilled": int(stats.get("ward_kill") or 0),
    }
    for i in range(7):
        mapped[f"item{i}"] = int(items[i]) if i < len(items) and items[i] else 0
    return mapped


async def get_match(s, match_id, created_at):
    parsed, err = await opgg_call_tool(
        s,
        "lol_get_summoner_game_detail",
        {
            "region": OPGG_REGION,
            "game_id": match_id,
            "created_at": created_at,
            "desired_output_fields": [
                "data.game_detail.{id,game_type,created_at,game_length_second}",
                "data.game_detail.teams[].{team_key,objectives}",
                "data.game_detail.teams[].participants[].summoner.{game_name,tagline,puuid}",
                "data.game_detail.teams[].participants[].{champion_id,champion_name,team_key,items[],items_names[],position}",
                "data.game_detail.teams[].participants[].stats.{kill,death,assist,result,minion_kill,neutral_minion_kill,total_damage_dealt_to_champions,gold_earned,champion_level,vision_score,vision_wards_bought_in_game,ward_place,ward_kill}",
            ],
        },
    )
    if err:
        print(f"[opgg] get_match {match_id} failed: {err}")
        return None

    game_detail = ((parsed.get("data") or {}).get("game_detail")) if parsed else None
    if not isinstance(game_detail, dict):
        print(f"[opgg] get_match {match_id} returned no game_detail")
        return None

    participants = []
    teams = []
    for team in as_list(game_detail.get("teams")):
        team_id = 100 if team.get("team_key") == "BLUE" else 200
        team_obj = _team_objectives_to_riot(team.get("objectives") or {})
        if team_obj:
            teams.append({"teamId": team_id, "objectives": team_obj})
        for participant in as_list(team.get("participants")):
            participants.append(opgg_participant_to_riot(participant))

    queue_id = QUEUE_ID_MAP.get(game_detail.get("game_type"), 0)
    match = {
        "metadata": {"matchId": game_detail.get("id", match_id)},
        "info": {
            "queueId": queue_id,
            "gameDuration": int(game_detail.get("game_length_second") or 0),
            "gameCreation": game_detail.get("created_at"),
            "participants": participants,
            "teams": teams,
        },
    }
    await enrich_vision(s, match)
    return match


def _team_objectives_to_riot(objectives: dict):
    if not isinstance(objectives, dict):
        return {}
    out = {}
    aliases = {
        "champion": ("champion", "kill", "kills"),
        "tower": ("tower", "turret"),
        "dragon": ("dragon", "drake"),
        "baron": ("baron",),
        "inhibitor": ("inhibitor",),
        "riftHerald": ("rift_herald", "herald", "riftHerald"),
    }
    for riot_key, possible_keys in aliases.items():
        raw = None
        for key in possible_keys:
            if key in objectives:
                raw = objectives.get(key)
                break
        if raw is None:
            continue
        if isinstance(raw, dict):
            kills = raw.get("kills", raw.get("kill", raw.get("count", 0)))
            first = bool(raw.get("first", False))
        else:
            kills = raw
            first = False
        try:
            kills = int(kills or 0)
        except (TypeError, ValueError):
            kills = 0
        out[riot_key] = {"kills": kills, "first": first}
    return out


def get_lp_info(ranked_data):
    """Returns (tier, rank, lp, total_lp_int) for RANKED_SOLO_5x5.

    Returns ("UNRANKED", "", 0, 0) for empty input — but callers should
    use `ranked_data is None` to distinguish a failed fetch from an
    unranked player.
    """
    if not ranked_data:
        return "UNRANKED", "", 0, 0
    for e in ranked_data:
        if e.get("queueType") == "RANKED_SOLO_5x5":
            tier = e.get("tier", "UNRANKED")
            rank = e.get("rank", "")
            lp = e.get("leaguePoints", 0)
            total = (
                TIER_ORDER.get(tier, 0) * 400 + DIV_ORDER.get(str(rank), 0) * 100 + lp
            )
            return tier, rank, lp, total
    return "UNRANKED", "", 0, 0


def find_history_participant(
    match_entry: dict, game_name: str, tag_line: str, puuid=None
):
    for participant in as_list(match_entry.get("participants")):
        summoner = participant.get("summoner") or {}
        if puuid and summoner.get("puuid") == puuid:
            return participant
        if (summoner.get("game_name") or "").lower() == (game_name or "").lower() and (
            summoner.get("tagline") or ""
        ).lower() == (tag_line or "").lower():
            return participant
    return None


def build_history_entry(match_entry: dict, participant: dict, fallback_lp=None):
    stats = participant.get("stats") or {}
    day = match_day_ist(match_entry.get("created_at"))
    result = canonical_outcome(stats.get("result")) or "DRAW"
    duration = int(match_entry.get("game_length_second") or 0)
    kills = int(stats.get("kill") or 0)
    deaths = int(stats.get("death") or 0)
    assists = int(stats.get("assist") or 0)
    cs = int(stats.get("minion_kill") or 0) + int(stats.get("neutral_minion_kill") or 0)
    minutes = duration / 60 if duration else 0
    vision_score = stats.get("vision_score")
    return {
        "date": str(day) if day else str(today_ist()),
        "match_id": match_entry.get("id"),
        "champion": participant.get("champion_name", "Unknown"),
        "champion_id": participant.get("champion_id"),
        "position": participant.get("position"),
        "result": result,
        "lp_change": None,
        "lp_total": fallback_lp,
        "lp_before": None,
        "recorded_at": now_ist().isoformat(),
        "reconciled": True,
        "duration": duration,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "kda": round((kills + assists) / max(deaths, 1), 2),
        "cs": cs,
        "cs_per_min": round(cs / minutes, 1) if minutes else 0.0,
        "damage": int(stats.get("total_damage_dealt_to_champions") or 0),
        "vision": optional_score(vision_score),
        "vision_source": "opgg" if optional_score(vision_score) is not None else None,
        "gold": int(stats.get("gold_earned") or 0),
        "level": int(stats.get("champion_level") or 0),
        "controlWardsBought": int(stats.get("vision_wards_bought_in_game") or 0),
        "wardsPlaced": int(stats.get("ward_place") or 0),
        "wardsKilled": int(stats.get("ward_kill") or 0),
        "queue": "RANKED_SOLO_5x5",
    }


def recent_today_history(
    recent_ranked, game_name: str, tag_line: str, puuid=None, history_all=None
):
    today = today_ist()
    items = []
    stored_by_id = {
        h.get("match_id"): h for h in as_list(history_all) if h.get("match_id")
    }
    for match_entry in recent_ranked:
        if match_day_ist(match_entry.get("created_at")) != today:
            continue
        participant = find_history_participant(match_entry, game_name, tag_line, puuid)
        if not participant:
            continue
        entry = build_history_entry(match_entry, participant)
        stored = stored_by_id.get(entry.get("match_id"))
        if stored:
            entry["result"] = stored.get("result", entry["result"])
            entry["lp_change"] = stored.get("lp_change", entry["lp_change"])
            entry["lp_total"] = stored.get("lp_total", entry["lp_total"])
            entry["lp_before"] = stored.get("lp_before", entry["lp_before"])
            entry["recorded_at"] = stored.get("recorded_at", entry["recorded_at"])
            entry["reconciled"] = stored.get("reconciled", entry["reconciled"])
        items.append(entry)
    return items


async def get_champion_mastery(session: aiohttp.ClientSession, puuid: str, champion_id: int) -> int:
    """Returns the total champion mastery points for the player on a specific champion."""
    from .config import RIOT_KEY, PLATFORM
    if not RIOT_KEY or not puuid or not champion_id:
        return 0
    url = f"https://{PLATFORM}.api.riotgames.com/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/by-champion/{champion_id}"
    headers = {"X-Riot-Token": RIOT_KEY}
    try:
        async with session.get(url, headers=headers) as r:
            if r.status == 200:
                body = await r.json()
                return int(body.get("championPoints", 0))
            elif r.status == 404:
                return 0
            else:
                text = await r.text()
                print(f"[opgg] champion mastery fetch status {r.status}: {text}")
                return 0
    except Exception as e:
        print(f"[opgg] failed to fetch champion mastery: {e}")
        return 0
