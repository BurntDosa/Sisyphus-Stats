"""Community features for ranked Solo/Duo friend-group rituals."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta, timezone

import discord

from .state import data, save_data
from .utils import now_ist, parse_iso_datetime, today_ist

ACCENT = 0x5865F2
GOOD = 0x57F287
GOLD = 0xFEE75C
SOFT = 0x99AAB5

GOAL_PRESETS = {
    "wins": ("Ranked wins", "wins", 10),
    "games": ("Ranked games", "games", 20),
    "positive_lp_days": ("Positive LP days", "positive_lp_days", 3),
    "unique_champions": ("Unique champions", "unique_champions", 8),
    "streak": ("Win streak", "streak", 3),
}


def ensure_community() -> dict:
    community = data.setdefault("community", {})
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
    return community


def player_label(riot_id: str) -> str:
    info = data.get("tracked", {}).get(riot_id, {})
    return str(info.get("game_name") or riot_id.split("#", 1)[0]).strip()


def week_start(day: date | None = None) -> date:
    day = day or today_ist()
    return day - timedelta(days=day.weekday())


def week_key(day: date | None = None) -> str:
    return str(week_start(day))


def iso_now() -> str:
    return now_ist().isoformat()


def parse_lp_delta(row: dict) -> int:
    raw = row.get("lp_change")
    if raw is None:
        return 0
    try:
        return int(str(raw).replace("+", "").strip())
    except (TypeError, ValueError):
        return 0


def recent_rows(days: int = 7) -> list[tuple[str, dict]]:
    cutoff = today_ist() - timedelta(days=days - 1)
    out: list[tuple[str, dict]] = []
    for riot_id, rows in data.get("history", {}).items():
        for row in rows:
            try:
                row_day = date.fromisoformat(str(row.get("date")))
            except (TypeError, ValueError):
                continue
            if row_day >= cutoff and row.get("result") != "DRAW":
                out.append((riot_id, row))
    return out


def make_embed(title: str, color: int = ACCENT, description: str | None = None) -> discord.Embed:
    return discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=now_ist(),
    )


def metric_line(label: str, value: str) -> str:
    return f"**{label}**\n`{value}`"


def queue_beacon_embed(game_id: str, players: list[tuple[str, str, int | None]]) -> discord.Embed:
    names = []
    champs = []
    for riot_id, champion, _team_id in players:
        names.append(player_label(riot_id))
        if champion:
            champs.append(champion)
    subject = " & ".join(names) if names else "A tracked player"
    champ_text = " / ".join(champs) if champs else "ranked Solo/Duo"
    e = make_embed(
        "Queue Beacon",
        ACCENT,
        f"**{subject}** is pushing the boulder on **{champ_text}**.",
    )
    e.add_field(name="Queue", value="Ranked Solo/Duo", inline=True)
    e.set_footer(text="No spoilers, no scouting, just vibes.")
    return e


async def maybe_send_queue_beacon(destination, active_games: dict) -> None:
    from .live import announce_or_update_live_rooms

    ensure_community()
    await announce_or_update_live_rooms(destination, active_games)


def make_history_row(match: dict, participant: dict, riot_id: str, outcome: str, lp_delta: int, old_lp, new_lp, mastery_points: int = 0) -> dict:
    info = match.get("info", {})
    participants = info.get("participants", [])
    team_id = participant.get("teamId")
    team = [p for p in participants if p.get("teamId") == team_id]
    enemy = [p for p in participants if p.get("teamId") != team_id]
    team_kills = sum(int(p.get("kills") or 0) for p in team)
    enemy_kills = sum(int(p.get("kills") or 0) for p in enemy)
    team_damage = sum(int(p.get("totalDamageDealtToChampions") or 0) for p in team)
    team_gold = sum(int(p.get("goldEarned") or 0) for p in team)
    duration = int(info.get("gameDuration") or 0)
    kills = int(participant.get("kills") or 0)
    assists = int(participant.get("assists") or 0)
    deaths = int(participant.get("deaths") or 0)
    cs = int(participant.get("totalMinionsKilled") or 0) + int(participant.get("neutralMinionsKilled") or 0)
    damage = int(participant.get("totalDamageDealtToChampions") or 0)
    gold = int(participant.get("goldEarned") or 0)
    vision = int(participant.get("visionScore") or 0)
    minutes = duration / 60 if duration else 0
    day = str(today_ist())
    if info.get("gameCreation"):
        parsed = parse_iso_datetime(info.get("gameCreation"))
        if parsed:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            day = str(parsed.astimezone(now_ist().tzinfo).date())
    kp = ((kills + assists) / team_kills * 100) if team_kills else 0.0
    damage_share = (damage / team_damage * 100) if team_damage else 0.0
    gold_share = (gold / team_gold * 100) if team_gold else 0.0
    sign = "+" if lp_delta >= 0 else ""
    return {
        "date": day,
        "match_id": match.get("metadata", {}).get("matchId"),
        "champion": participant.get("championName", "Unknown"),
        "champion_id": participant.get("championId"),
        "position": participant.get("position"),
        "result": outcome,
        "lp_change": f"{sign}{lp_delta}",
        "lp_total": new_lp,
        "lp_before": old_lp,
        "recorded_at": iso_now(),
        "reconciled": True,
        "duration": duration,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "kda": round((kills + assists) / max(deaths, 1), 2),
        "cs": cs,
        "cs_per_min": round(cs / minutes, 1) if minutes else 0.0,
        "damage": damage,
        "damage_share": round(damage_share, 1),
        "vision": vision,
        "gold": gold,
        "gold_share": round(gold_share, 1),
        "level": int(participant.get("champLevel") or 0),
        "kill_participation": round(kp, 1),
        "team_kills": team_kills,
        "enemy_kills": enemy_kills,
        "champion_mastery": int(mastery_points or 0),
        "queue": "RANKED_SOLO_5x5",
    }


def _is_record_better(metric: str, old: dict | None, new_value: float, row: dict) -> bool:
    if old is None:
        return True
    old_value = old.get("value")
    if old_value is None:
        return True
    return new_value > old_value


def update_records(riot_id: str, row: dict) -> list[str]:
    community = ensure_community()
    records = community.setdefault("records", {})
    labels: list[str] = []
    metrics = [
        ("damage", "Highest damage", row.get("damage", 0)),
        ("kda", "Best KDA", row.get("kda", 0)),
        ("vision", "Best vision", row.get("vision", 0)),
        ("cs_per_min", "Best CS/min", row.get("cs_per_min", 0)),
        ("lp_gain", "Biggest LP gain", parse_lp_delta(row)),
    ]
    if row.get("result") == "WIN":
        duration = int(row.get("duration") or 0)
        if duration:
            fastest = records.get("fastest_win")
            if not fastest or duration < int(fastest.get("value") or 10**9):
                records["fastest_win"] = _record_payload(riot_id, row, duration, "Fastest win")
                labels.append("Fastest win")
            if _is_record_better("longest_win", records.get("longest_win"), duration, row):
                records["longest_win"] = _record_payload(riot_id, row, duration, "Longest win")
                labels.append("Longest win")
    for key, label, value in metrics:
        if not isinstance(value, (int, float)):
            continue
        if key == "lp_gain" and value <= 0:
            continue
        if _is_record_better(key, records.get(key), value, row):
            records[key] = _record_payload(riot_id, row, value, label)
            labels.append(label)
    if labels:
        save_data(data)
    return labels


def _record_payload(riot_id: str, row: dict, value, label: str) -> dict:
    return {
        "label": label,
        "value": value,
        "riot_id": riot_id,
        "player": player_label(riot_id),
        "champion": row.get("champion"),
        "match_id": row.get("match_id"),
        "date": row.get("date"),
        "recorded_at": iso_now(),
    }


def praise_lines(riot_id: str, row: dict, record_labels: list[str] | None = None) -> list[str]:
    lines: list[str] = []
    name = player_label(riot_id)
    if row.get("result") == "WIN" and parse_lp_delta(row) > 0:
        lines.append(f"{name} moved the boulder **+{parse_lp_delta(row)} LP**.")
    if row.get("kill_participation", 0) >= 60:
        lines.append(f"{name} joined **{row['kill_participation']:.0f}%** of team kills.")
    if row.get("cs_per_min", 0) >= 8:
        lines.append(f"{name} farmed at **{row['cs_per_min']:.1f} CS/min**.")
    if row.get("vision", 0) >= 20:
        lines.append(f"{name} lit the map with **{row['vision']} vision score**.")
    if record_labels:
        lines.append("New archive mark: **" + ", ".join(record_labels[:2]) + "**.")
    return lines[:3]


def queueup(user_id: int, display_name: str, note: str | None = None) -> discord.Embed:
    community = ensure_community()
    board = community.setdefault("queue_board", {})
    board[str(user_id)] = {
        "display_name": display_name,
        "note": (note or "").strip()[:120],
        "created_at": iso_now(),
    }
    save_data(data)
    e = make_embed("Ranked Queue Board", GOOD, f"**{display_name}** is looking for ranked Solo/Duo.")
    if note:
        e.add_field(name="Note", value=note[:120], inline=False)
    return e


def queueclear(user_id: int) -> bool:
    board = ensure_community().setdefault("queue_board", {})
    removed = board.pop(str(user_id), None)
    if removed:
        save_data(data)
        return True
    return False


def queueboard_embed() -> discord.Embed:
    board = ensure_community().setdefault("queue_board", {})
    e = make_embed("Ranked Queue Board", ACCENT)
    if not board:
        e.description = "Nobody is queued up right now."
        return e
    lines = []
    now = now_ist()
    for user_id, entry in list(board.items()):
        created = parse_iso_datetime(entry.get("created_at"))
        age = ""
        if created:
            minutes = max(0, int((now - created.astimezone(now.tzinfo)).total_seconds() // 60))
            age = f" · {minutes}m ago"
        note = f" — {entry.get('note')}" if entry.get("note") else ""
        lines.append(f"• <@{user_id}> **{entry.get('display_name', 'Player')}**{age}{note}")
    e.description = "\n".join(lines[:20])
    e.set_footer(text="Ranked Solo/Duo only")
    return e


def create_rivalry_invite(challenger_id: int, target_id: int, challenger_name: str) -> discord.Embed:
    community = ensure_community()
    invites = community.setdefault("rivalry_invites", {})
    invite_key = f"{min(challenger_id, target_id)}:{max(challenger_id, target_id)}"
    invites[invite_key] = {
        "challenger_id": str(challenger_id),
        "target_id": str(target_id),
        "challenger_name": challenger_name,
        "created_at": iso_now(),
    }
    save_data(data)
    return make_embed("Friendly Rivalry", GOLD, f"<@{challenger_id}> challenged <@{target_id}> to a weekly ranked Solo/Duo rivalry.")


def accept_rivalry(user_id: int, other_id: int) -> tuple[bool, discord.Embed]:
    community = ensure_community()
    key = f"{min(user_id, other_id)}:{max(user_id, other_id)}"
    invites = community.setdefault("rivalry_invites", {})
    invite = invites.get(key)
    if not invite or invite.get("target_id") != str(user_id):
        return False, make_embed("Friendly Rivalry", SOFT, "No pending rivalry invite found.")
    community.setdefault("rivalries", {})[key] = {
        "players": [str(user_id), str(other_id)],
        "created_at": iso_now(),
        "week": week_key(),
        "active": True,
    }
    del invites[key]
    save_data(data)
    return True, make_embed("Friendly Rivalry Accepted", GOOD, f"<@{user_id}> and <@{other_id}> are now in an opt-in ranked Solo/Duo rivalry.")


def end_rivalry(user_id: int, other_id: int) -> bool:
    rivalries = ensure_community().setdefault("rivalries", {})
    key = f"{min(user_id, other_id)}:{max(user_id, other_id)}"
    if key in rivalries:
        del rivalries[key]
        save_data(data)
        return True
    return False


def rivalry_embed() -> discord.Embed:
    rivalries = ensure_community().setdefault("rivalries", {})
    e = make_embed("Friendly Rivalries", GOLD)
    active = [r for r in rivalries.values() if r.get("active")]
    if not active:
        e.description = "No active rivalries yet."
        return e
    lines = []
    linked = data.get("links", {})
    for rival in active[:10]:
        p1, p2 = rival.get("players", ["", ""])[:2]
        s1 = _linked_week_stats(linked.get(str(p1)))
        s2 = _linked_week_stats(linked.get(str(p2)))
        lines.append(
            f"• <@{p1}> `{s1['lp']:+} LP` `{s1['wins']}W/{s1['games']}G` vs "
            f"<@{p2}> `{s2['lp']:+} LP` `{s2['wins']}W/{s2['games']}G`"
        )
    e.description = "\n".join(lines)
    return e


def _linked_week_stats(riot_id: str | None) -> dict:
    start = week_start()
    rows = []
    if riot_id:
        for row in data.get("history", {}).get(riot_id, []):
            try:
                row_day = date.fromisoformat(str(row.get("date")))
            except (TypeError, ValueError):
                continue
            if row_day >= start and row.get("result") != "DRAW":
                rows.append(row)
    return {
        "games": len(rows),
        "wins": sum(1 for row in rows if row.get("result") == "WIN"),
        "lp": sum(parse_lp_delta(row) for row in rows),
    }


def create_squad_goal(goal_type: str, target: int | None = None) -> tuple[bool, discord.Embed]:
    preset = GOAL_PRESETS.get(goal_type)
    if not preset:
        return False, make_embed("Squad Goals", SOFT, f"Unknown goal. Try: `{', '.join(GOAL_PRESETS)}`")
    name, metric, default_target = preset
    target = int(target or default_target)
    goal_id = f"{week_key()}:{metric}"
    goals = ensure_community().setdefault("squad_goals", {})
    goals[goal_id] = {
        "goal_id": goal_id,
        "name": name,
        "metric": metric,
        "target": target,
        "week": week_key(),
        "created_at": iso_now(),
    }
    save_data(data)
    e = make_embed("Squad Goal Set", GOOD, f"**{name}** — target `{target}` this week.")
    return True, e


def squad_goals_embed() -> discord.Embed:
    goals = ensure_community().setdefault("squad_goals", {})
    e = make_embed("Squad Goals", ACCENT)
    active = [g for g in goals.values() if g.get("week") == week_key()]
    if not active:
        e.description = "No squad goals set for this week."
        return e
    lines = []
    for goal in active:
        progress = squad_goal_progress(goal)
        target = int(goal.get("target") or 1)
        blocks = max(0, min(10, int((progress / target) * 10))) if target else 0
        bar = "■" * blocks + "□" * (10 - blocks)
        lines.append(f"• **{goal.get('name')}** `{progress}/{target}` `{bar}`")
    e.description = "\n".join(lines)
    return e


def squad_goal_progress(goal: dict) -> int:
    rows = [row for _riot_id, row in recent_rows(days=7)]
    metric = goal.get("metric")
    if metric == "wins":
        return sum(1 for row in rows if row.get("result") == "WIN")
    if metric == "games":
        return len(rows)
    if metric == "unique_champions":
        return len({row.get("champion") for row in rows if row.get("champion")})
    if metric == "positive_lp_days":
        by_day: dict[str, int] = {}
        for row in rows:
            by_day[row.get("date", "")] = by_day.get(row.get("date", ""), 0) + parse_lp_delta(row)
        return sum(1 for total in by_day.values() if total > 0)
    if metric == "streak":
        best = 0
        for riot_id in data.get("history", {}):
            streak = 0
            for row in reversed(data.get("history", {}).get(riot_id, [])):
                if row.get("result") == "WIN":
                    streak += 1
                    best = max(best, streak)
                elif row.get("result") == "LOSS":
                    streak = 0
        return best
    return 0


def weekly_recap_embed(days: int = 7) -> discord.Embed:
    rows = recent_rows(days)
    e = make_embed("Weekly Squad Recap", GOLD)
    if not rows:
        e.description = "No ranked Solo/Duo games recorded this week."
        return e
    by_player: dict[str, list[dict]] = {}
    champs = Counter()
    for riot_id, row in rows:
        by_player.setdefault(riot_id, []).append(row)
        if row.get("champion"):
            champs[row["champion"]] += 1

    climb = max(
        ((riot_id, sum(parse_lp_delta(r) for r in player_rows)) for riot_id, player_rows in by_player.items()),
        key=lambda item: item[1],
    )
    volume = max(by_player.items(), key=lambda item: len(item[1]))
    clean = max(rows, key=lambda item: (item[1].get("kda", 0), item[1].get("damage", 0)))
    vision = max(rows, key=lambda item: item[1].get("vision", 0))
    favorite = champs.most_common(1)[0]
    e.add_field(name="Biggest Climb", value=f"**{player_label(climb[0])}** `{climb[1]:+} LP`", inline=True)
    e.add_field(name="Most Games", value=f"**{player_label(volume[0])}** `{len(volume[1])}`", inline=True)
    e.add_field(name="Favorite Champion", value=f"**{favorite[0]}** `{favorite[1]} games`", inline=True)
    e.add_field(name="Cleanest Game", value=f"**{player_label(clean[0])}** on **{clean[1].get('champion')}** `{clean[1].get('kda', 0):.2f} KDA`", inline=False)
    e.add_field(name="Best Vision Game", value=f"**{player_label(vision[0])}** on **{vision[1].get('champion')}** `{vision[1].get('vision', 0)} vision`", inline=False)
    goals = squad_goals_embed()
    if goals.description:
        e.add_field(name="Squad Goals", value=goals.description[:1024], inline=False)
    e.set_footer(text="Ranked Solo/Duo only")
    return e


def halloffame_embed() -> discord.Embed:
    records = ensure_community().setdefault("records", {})
    e = make_embed("Boulder Archive", GOLD)
    if not records:
        e.description = "No records yet. Fresh ranked games will start filling this out."
        return e
    lines = []
    for key in ("damage", "kda", "vision", "cs_per_min", "lp_gain", "fastest_win", "longest_win"):
        rec = records.get(key)
        if not rec:
            continue
        value = rec.get("value")
        if key in {"fastest_win", "longest_win"}:
            value = f"{int(value) // 60}m {int(value) % 60:02d}s"
        elif isinstance(value, float):
            value = f"{value:.1f}"
        lines.append(f"• **{rec.get('label')}** — `{value}` by **{rec.get('player')}** on **{rec.get('champion')}**")
    e.description = "\n".join(lines[:10])
    return e
