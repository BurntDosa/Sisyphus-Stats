"""Monthly public and private recap generation."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

import discord

from .community import ensure_community, parse_lp_delta, player_label
from .profiles import player_memories
from .state import data, save_data
from .utils import now_ist

GOLD = 0xFEE75C
GOOD = 0x57F287
ACCENT = 0x5865F2


def previous_month(today: date | None = None) -> tuple[int, int]:
    today = today or now_ist().date()
    first = today.replace(day=1)
    last_prev = first - timedelta(days=1)
    return last_prev.year, last_prev.month


def month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def month_label(year: int, month: int) -> str:
    return date(year, month, 1).strftime("%B %Y")


def _row_date(row: dict) -> date | None:
    try:
        return date.fromisoformat(str(row.get("date")))
    except (TypeError, ValueError):
        return None


def rows_for_month(year: int, month: int) -> list[tuple[str, dict]]:
    out = []
    for riot_id, rows in data.get("history", {}).items():
        for row in rows:
            day = _row_date(row)
            if day and day.year == year and day.month == month:
                out.append((riot_id, row))
    return out


def _duration_hours(rows: list[dict]) -> float:
    seconds = sum(int(row.get("duration") or 0) for row in rows)
    return seconds / 3600


def _live_rows_for_month(year: int, month: int) -> list[dict]:
    live = ensure_community().setdefault("live_rooms", {}).setdefault("history", {})
    rooms = []
    for room in live.values():
        ended = room.get("ended_at") or room.get("announced_at")
        try:
            day = datetime.fromisoformat(ended).date()
        except Exception:
            continue
        if day.year == year and day.month == month:
            rooms.append(room)
    return rooms


def monthly_model(year: int, month: int) -> dict:
    rows = rows_for_month(year, month)
    by_player: dict[str, list[dict]] = defaultdict(list)
    champs = Counter()
    roles = Counter()
    for riot_id, row in rows:
        by_player[riot_id].append(row)
        if row.get("champion"):
            champs[row["champion"]] += 1
        if row.get("position"):
            roles[row["position"]] += 1

    all_rows = [row for _riot_id, row in rows]
    wins = sum(1 for row in all_rows if row.get("result") == "WIN")
    losses = sum(1 for row in all_rows if row.get("result") == "LOSS")
    draws = sum(1 for row in all_rows if row.get("result") == "DRAW")
    net_lp = sum(parse_lp_delta(row) for row in all_rows)
    live_rooms = _live_rows_for_month(year, month)
    memories = []
    for riot_id in data.get("tracked", {}):
        for memory in player_memories(riot_id):
            try:
                created = datetime.fromisoformat(memory.get("created_at")).date()
            except Exception:
                continue
            if created.year == year and created.month == month:
                memories.append(memory)

    return {
        "year": year,
        "month": month,
        "key": month_key(year, month),
        "label": month_label(year, month),
        "rows": rows,
        "all_rows": all_rows,
        "by_player": dict(by_player),
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "net_lp": net_lp,
        "hours": _duration_hours(all_rows),
        "champs": champs,
        "roles": roles,
        "live_rooms": live_rooms,
        "memories": memories,
    }


def public_monthly_embeds(model: dict) -> list[discord.Embed]:
    label = model["label"].upper()
    e = discord.Embed(
        title=f"THE BOULDER — {label}",
        color=GOLD,
        timestamp=now_ist(),
    )
    e.description = "A month of ranked Solo/Duo games Sisyphus witnessed."
    e.add_field(name="Games", value=f"`{len(model['all_rows'])}`", inline=True)
    e.add_field(
        name="W / L / D",
        value=f"`{model['wins']} / {model['losses']} / {model['draws']}`",
        inline=True,
    )
    e.add_field(name="Collective LP", value=f"`{model['net_lp']:+} LP`", inline=True)
    e.add_field(name="Ranked Hours", value=f"`{model['hours']:.1f}`", inline=True)
    e.add_field(name="Active Players", value=f"`{len(model['by_player'])}`", inline=True)
    if model["champs"]:
        champ, count = model["champs"].most_common(1)[0]
        e.add_field(name="Favorite Champion", value=f"**{champ}** `{count} games`", inline=True)

    player_lines = []
    for riot_id, rows in sorted(model["by_player"].items(), key=lambda item: len(item[1]), reverse=True):
        wins = sum(1 for row in rows if row.get("result") == "WIN")
        losses = sum(1 for row in rows if row.get("result") == "LOSS")
        lp = sum(parse_lp_delta(row) for row in rows)
        champ_counts = Counter(row.get("champion") for row in rows if row.get("champion"))
        champ = champ_counts.most_common(1)[0][0] if champ_counts else "Unknown"
        player_lines.append(
            f"• **{player_label(riot_id)}** `{lp:+} LP` `{wins}W/{losses}L` · {champ}"
        )
    if player_lines:
        e.add_field(name="Player Journeys", value="\n".join(player_lines[:12])[:1024], inline=False)

    moments = []
    enriched = [
        row
        for row in model["all_rows"]
        if isinstance(row.get("kda"), (int, float)) or isinstance(row.get("damage"), int)
    ]
    if enriched:
        clean = max(enriched, key=lambda row: (row.get("kda") or 0, row.get("damage") or 0))
        moments.append(
            f"Cleanest game: **{clean.get('champion')}** `{clean.get('kda', 0):.2f} KDA`"
        )
        damage = max(enriched, key=lambda row: row.get("damage") or 0)
        moments.append(f"Big damage: **{damage.get('champion')}** `{damage.get('damage', 0):,}`")
    if model["live_rooms"]:
        peak = max(model["live_rooms"], key=lambda room: int(room.get("peak_watchers") or 0))
        moments.append(f"Biggest watch party: `{peak.get('peak_watchers', 0)}` watchers")
    if model["memories"]:
        moments.append(f"Memories saved: `{len(model['memories'])}`")
    if moments:
        e.add_field(name="Best Moments", value="\n".join(f"• {line}" for line in moments), inline=False)

    e.set_footer(text="Tracked history only")
    return [e]


def personal_monthly_embed(riot_id: str, rows: list[dict], model: dict) -> discord.Embed:
    wins = sum(1 for row in rows if row.get("result") == "WIN")
    losses = sum(1 for row in rows if row.get("result") == "LOSS")
    draws = sum(1 for row in rows if row.get("result") == "DRAW")
    net_lp = sum(parse_lp_delta(row) for row in rows)
    champs = Counter(row.get("champion") for row in rows if row.get("champion"))
    roles = Counter(row.get("position") for row in rows if row.get("position"))
    best = max(rows, key=lambda row: (row.get("kda") or 0, row.get("damage") or 0), default=None)
    e = discord.Embed(
        title=f"Your {model['label']}",
        color=GOOD if net_lp >= 0 else ACCENT,
        timestamp=now_ist(),
    )
    e.set_author(name=riot_id)
    e.add_field(name="Games", value=f"`{len(rows)}`", inline=True)
    e.add_field(name="W / L / D", value=f"`{wins} / {losses} / {draws}`", inline=True)
    e.add_field(name="Net LP", value=f"`{net_lp:+} LP`", inline=True)
    if champs:
        champ, count = champs.most_common(1)[0]
        e.add_field(name="Most Played", value=f"**{champ}** `{count}`", inline=True)
    if roles:
        role, count = roles.most_common(1)[0]
        e.add_field(name="Main Role", value=f"`{role}` · `{count}`", inline=True)
    if best:
        e.add_field(
            name="Best Game",
            value=f"**{best.get('champion')}** `{best.get('kills', 0)}/{best.get('deaths', 0)}/{best.get('assists', 0)}`",
            inline=False,
        )
    memories = [
        memory
        for memory in player_memories(riot_id)
        if memory.get("date", "").startswith(model["key"])
    ]
    if memories:
        e.add_field(
            name="Memories",
            value="\n".join(f"• **{m.get('name')}** on {m.get('champion')}" for m in memories[:5]),
            inline=False,
        )
    e.description = (
        f"Sisyphus watched **{player_label(riot_id)}** play {len(rows)} ranked games "
        f"and finish the month at **{net_lp:+} LP**."
    )
    e.set_footer(text="Sisyphus-observed ranked Solo/Duo history only")
    return e


def record_historical_events(model: dict) -> list[dict]:
    community = ensure_community()
    events = community.setdefault("historical_events", [])
    seen = {event.get("key") for event in events}
    created = []
    total_games = sum(len(rows) for rows in data.get("history", {}).values())
    for threshold in (100, 250, 500, 1000, 1500, 2000):
        key = f"server_games_{threshold}"
        if total_games >= threshold and key not in seen:
            event = {
                "key": key,
                "date": now_ist().date().isoformat(),
                "label": f"The server has now watched {threshold} ranked games.",
                "kind": "server_threshold",
                "created_at": now_ist().isoformat(),
            }
            events.append(event)
            created.append(event)
            seen.add(key)
    if model["all_rows"]:
        active_key = f"most_active_month_{model['key']}"
        previous_best = max(
            (
                len(rows_for_month(int(key[:4]), int(key[5:7])))
                for key in data.get("community", {}).get("monthly_recaps", {})
                if len(key) == 7 and key != model["key"]
            ),
            default=0,
        )
        if len(model["all_rows"]) > previous_best and active_key not in seen:
            event = {
                "key": active_key,
                "date": now_ist().date().isoformat(),
                "label": f"{model['label']} became the most active month in Sisyphus history.",
                "kind": "monthly_record",
                "created_at": now_ist().isoformat(),
            }
            events.append(event)
            created.append(event)
    if created:
        save_data(data)
    return created


async def run_monthly_recap(destination, bot, year: int | None = None, month: int | None = None) -> bool:
    if year is None or month is None:
        year, month = previous_month()
    model = monthly_model(year, month)
    community = ensure_community()
    posted = community.setdefault("monthly_recaps", {})
    key = model["key"]
    if posted.get(key, {}).get("public_posted_at"):
        return False
    if not model["all_rows"]:
        posted[key] = {
            "public_posted_at": now_ist().isoformat(),
            "skipped": "no_ranked_games",
            "dm_results": {},
        }
        save_data(data)
        return False

    messages = []
    for embed in public_monthly_embeds(model):
        msg = await destination.send(embed=embed)
        messages.append(str(msg.id))
    record_historical_events(model)

    dm_results = {}
    reverse_links = {riot_id: user_id for user_id, riot_id in data.get("links", {}).items()}
    for riot_id, rows in model["by_player"].items():
        user_id = reverse_links.get(riot_id)
        if not user_id:
            continue
        try:
            user = bot.get_user(int(user_id)) or await bot.fetch_user(int(user_id))
            await user.send(embed=personal_monthly_embed(riot_id, rows, model))
            dm_results[user_id] = "sent"
        except Exception as exc:
            dm_results[user_id] = f"failed: {type(exc).__name__}"

    posted[key] = {
        "public_posted_at": now_ist().isoformat(),
        "message_ids": messages,
        "dm_results": dm_results,
        "games": len(model["all_rows"]),
    }
    save_data(data)
    return True
