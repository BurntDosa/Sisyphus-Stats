"""Live Game Room state and Queue Beacon message updates."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime

import discord

from .state import data, save_data
from .utils import now_ist, parse_iso_datetime

ACCENT = 0x5865F2
GOOD = 0x57F287
SOFT = 0x99AAB5

_room_locks: dict[str, asyncio.Lock] = {}
_refresh_tasks: dict[str, asyncio.Task] = {}


def ensure_live_rooms() -> dict:
    community = data.setdefault("community", {})
    community.setdefault("active_announcements", {})
    live = community.setdefault("live_rooms", {})
    live.setdefault("active", {})
    live.setdefault("history", {})
    return live


def _lock_for(game_id: str) -> asyncio.Lock:
    if game_id not in _room_locks:
        _room_locks[game_id] = asyncio.Lock()
    return _room_locks[game_id]


def _iso_now() -> str:
    return now_ist().isoformat()


def _seconds_between(start: str | None, end: datetime | None = None) -> int:
    started = parse_iso_datetime(start)
    if not started:
        return 0
    end = end or now_ist()
    if started.tzinfo is None:
        started = started.replace(tzinfo=end.tzinfo)
    return max(0, int((end - started.astimezone(end.tzinfo)).total_seconds()))


def _display_name(riot_id: str) -> str:
    info = data.get("tracked", {}).get(riot_id, {})
    return str(info.get("game_name") or riot_id.split("#", 1)[0]).strip()


def _linked_user_id(riot_id: str) -> str | None:
    for user_id, linked in data.get("links", {}).items():
        if linked == riot_id:
            return str(user_id)
    return None


def _tracked_key(players: list[str]) -> str:
    return " & ".join(players)


def _subject(room: dict) -> str:
    return " & ".join(_display_name(player) for player in room.get("players", []))


def _champions(room: dict) -> str:
    champs = [
        champ
        for champ in room.get("champions", {}).values()
        if champ and champ != "Unknown"
    ]
    return " / ".join(champs) or "ranked Solo/Duo"


def _voice_channel_for_room(room: dict) -> discord.VoiceChannel | discord.StageChannel | None:
    from .bot import bot

    linked_ids = {
        int(user_id)
        for user_id in (_linked_user_id(player) for player in room.get("players", []))
        if user_id and str(user_id).isdigit()
    }
    if not linked_ids:
        return None

    guilds = []
    guild_id = int(room.get("guild_id") or 0)
    if guild_id:
        guild = bot.get_guild(guild_id)
        if guild:
            guilds.append(guild)
    guilds.extend(g for g in bot.guilds if g not in guilds)

    for guild in guilds:
        for user_id in linked_ids:
            member = guild.get_member(user_id)
            if member and member.voice and member.voice.channel:
                return member.voice.channel
    return None


def _room_members(room: dict) -> tuple[list[discord.Member], list[discord.Member]]:
    channel = _voice_channel_for_room(room)
    if channel is None:
        return [], []

    tracked_ids = {
        int(user_id)
        for user_id in (_linked_user_id(player) for player in room.get("players", []))
        if user_id and str(user_id).isdigit()
    }
    tracked_members = [
        member for member in channel.members if member.id in tracked_ids and not member.bot
    ]
    watchers = [
        member
        for member in channel.members
        if not member.bot and member.id not in tracked_ids
    ]
    return tracked_members, watchers


def _active_market_for_room(room: dict) -> dict | None:
    try:
        from .betting import get_market_for_tracked_key

        return get_market_for_tracked_key(_tracked_key(room.get("players", [])))
    except Exception as exc:
        print(f"[live] market lookup failed: {exc}")
        return None


def _bet_side_for(user_id: int, market: dict | None) -> str | None:
    if not market:
        return None
    try:
        from .betting import get_user_bet

        bet = get_user_bet(user_id, market["market_id"])
    except Exception:
        return None
    if not bet or bet.get("status") != "active":
        return None
    return str(bet.get("side") or "").upper()


def _update_intervals(room: dict, watchers: list[discord.Member], streaming: bool) -> None:
    now = now_ist()
    current_ids = {str(member.id) for member in watchers}
    previous_ids = set(room.get("current_watchers", []))
    sessions = room.setdefault("watcher_sessions", {})

    for user_id in current_ids - previous_ids:
        session = sessions.setdefault(user_id, {"seconds": 0})
        session["joined_at"] = now.isoformat()
    for user_id in previous_ids - current_ids:
        session = sessions.setdefault(user_id, {"seconds": 0})
        session["seconds"] = int(session.get("seconds") or 0) + _seconds_between(
            session.get("joined_at"), now
        )
        session["joined_at"] = None

    room["current_watchers"] = sorted(current_ids)
    room["peak_watchers"] = max(int(room.get("peak_watchers") or 0), len(current_ids))

    was_streaming = bool(room.get("streaming"))
    if streaming and not was_streaming:
        room["stream_started_at"] = now.isoformat()
    elif was_streaming and not streaming:
        room["stream_seconds"] = int(room.get("stream_seconds") or 0) + _seconds_between(
            room.get("stream_started_at"), now
        )
        room["stream_started_at"] = None
    room["streaming"] = bool(streaming)
    room["last_updated_at"] = now.isoformat()


def _watcher_counts(watchers: list[discord.Member], market: dict | None) -> Counter:
    counts: Counter = Counter({"WIN": 0, "LOSE": 0, "NONE": 0})
    for watcher in watchers:
        side = _bet_side_for(watcher.id, market)
        if side == "WIN":
            counts["WIN"] += 1
        elif side in {"LOSE", "LOSS"}:
            counts["LOSE"] += 1
        else:
            counts["NONE"] += 1
    return counts


def live_room_embed(room: dict) -> discord.Embed:
    tracked_members, watchers = _room_members(room)
    streaming = any(member.voice and member.voice.self_stream for member in tracked_members)
    market = _active_market_for_room(room)
    _update_intervals(room, watchers, streaming)
    counts = _watcher_counts(watchers, market)

    color = GOOD if streaming else ACCENT
    status = "Streaming" if streaming else "No stream yet"
    subject = _subject(room) or "A tracked player"
    champ_text = _champions(room)
    watcher_count = len(watchers)
    intention = (
        f"🫡 {counts['WIN']} · 💀 {counts['LOSE']} · 👀 {counts['NONE']}"
        if watcher_count
        else "No watchers yet"
    )

    e = discord.Embed(
        title="Queue Beacon",
        description=f"**{subject}** is pushing the boulder on **{champ_text}**.",
        color=color,
        timestamp=now_ist(),
    )
    e.add_field(name="Queue", value="Ranked Solo/Duo", inline=True)
    e.add_field(name="Stream", value=status, inline=True)
    e.add_field(name="Watching", value=f"{watcher_count} watching", inline=True)
    e.add_field(name="Watcher Intent", value=intention, inline=False)
    if market:
        e.add_field(
            name="Market",
            value=f"`{market['market_id']}` · {market.get('status', 'open').upper()}",
            inline=True,
        )
    e.set_footer(text="No scouting. No spoilers. Just the room around the game.")
    return e


def _mention_content(room: dict) -> tuple[str, discord.AllowedMentions]:
    mentions = []
    for player in room.get("players", []):
        user_id = _linked_user_id(player)
        if user_id and str(user_id).isdigit():
            mentions.append(f"<@{user_id}>")
    if mentions:
        content = f"{' '.join(mentions)} queue popped. Stream the climb?"
    else:
        content = f"{_subject(room) or 'A tracked player'} is pushing the boulder."
    return content, discord.AllowedMentions(users=True, roles=False, everyone=False)


async def _resolve_message(room: dict) -> discord.Message | None:
    from .bot import bot

    channel_id = int(room.get("channel_id") or 0)
    message_id = int(room.get("message_id") or 0)
    if not channel_id or not message_id:
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception as exc:
            print(f"[live] could not fetch channel {channel_id}: {exc}")
            return None
    try:
        return await channel.fetch_message(message_id)
    except Exception as exc:
        print(f"[live] could not fetch room message {message_id}: {exc}")
        return None


async def render_room(game_id: str) -> None:
    live = ensure_live_rooms()
    room = live["active"].get(str(game_id))
    if not room:
        return
    async with _lock_for(str(game_id)):
        embed = live_room_embed(room)
        msg = await _resolve_message(room)
        if msg:
            try:
                await msg.edit(embed=embed)
            except Exception as exc:
                print(f"[live] room edit failed for {game_id}: {exc}")
        save_data(data)


def schedule_room_refresh(game_id: str, delay: float = 1.0) -> None:
    game_id = str(game_id)
    task = _refresh_tasks.get(game_id)
    if task and not task.done():
        task.cancel()

    async def _delayed():
        try:
            await asyncio.sleep(delay)
            await render_room(game_id)
        except asyncio.CancelledError:
            return

    _refresh_tasks[game_id] = asyncio.create_task(_delayed())


def refresh_rooms_for_market(tracked_key: str) -> None:
    subjects = {part.strip() for part in str(tracked_key or "").split(" & ") if part.strip()}
    for game_id, room in ensure_live_rooms()["active"].items():
        if subjects & set(room.get("players", [])):
            schedule_room_refresh(game_id)


async def refresh_all_rooms() -> None:
    for game_id in list(ensure_live_rooms()["active"]):
        schedule_room_refresh(game_id, delay=0.2)


async def handle_voice_state_update(member: discord.Member) -> None:
    if member.bot:
        return
    await refresh_all_rooms()


def _room_from_active_game(game_id: str, players: list[tuple]) -> dict:
    clean_players = []
    champions = {}
    team_ids = {}
    platforms = {}
    for riot_id, _puuid, champion, team_id, _info, platform in players:
        clean_players.append(riot_id)
        champions[riot_id] = champion
        team_ids[riot_id] = team_id
        platforms[riot_id] = platform
    return {
        "game_id": str(game_id),
        "players": clean_players,
        "champions": champions,
        "team_ids": team_ids,
        "platforms": platforms,
        "announced_at": _iso_now(),
        "last_updated_at": _iso_now(),
        "current_watchers": [],
        "watcher_sessions": {},
        "peak_watchers": 0,
        "streaming": False,
        "stream_seconds": 0,
        "stream_started_at": None,
    }


async def announce_or_update_live_rooms(destination, active_games: dict) -> None:
    live = ensure_live_rooms()
    active = live["active"]
    history = live["history"]
    legacy = data.setdefault("community", {}).setdefault("active_announcements", {})
    live_ids = {str(game_id) for game_id in active_games}
    changed = False

    for game_id in list(active):
        if game_id not in live_ids:
            await finalize_room(game_id)
            changed = True

    for old_game_id in list(legacy):
        if old_game_id not in live_ids:
            del legacy[old_game_id]
            changed = True

    for game_id, players in active_games.items():
        game_id = str(game_id)
        if game_id in history:
            continue
        if game_id not in active:
            room = _room_from_active_game(game_id, players)
            room["channel_id"] = getattr(destination, "id", None)
            room["guild_id"] = getattr(getattr(destination, "guild", None), "id", None)
            content, mentions = _mention_content(room)
            msg = await destination.send(
                content=content,
                embed=live_room_embed(room),
                allowed_mentions=mentions,
            )
            room["message_id"] = msg.id
            room["channel_id"] = msg.channel.id
            if getattr(msg, "guild", None):
                room["guild_id"] = msg.guild.id
            active[game_id] = room
            legacy[game_id] = {
                "announced_at": room["announced_at"],
                "players": room["players"],
                "message_id": msg.id,
                "channel_id": msg.channel.id,
            }
            changed = True
        else:
            room = active[game_id]
            updated = _room_from_active_game(game_id, players)
            room["players"] = updated["players"]
            room["champions"] = updated["champions"]
            room["team_ids"] = updated["team_ids"]
            room["platforms"] = updated["platforms"]
            schedule_room_refresh(game_id, delay=0.2)
            changed = True

    if changed:
        save_data(data)


async def finalize_room(game_id: str) -> dict | None:
    live = ensure_live_rooms()
    active = live["active"]
    room = active.get(str(game_id))
    if not room:
        return None
    async with _lock_for(str(game_id)):
        tracked_members, watchers = _room_members(room)
        streaming = any(member.voice and member.voice.self_stream for member in tracked_members)
        _update_intervals(room, watchers, streaming)
        if room.get("streaming"):
            room["stream_seconds"] = int(room.get("stream_seconds") or 0) + _seconds_between(
                room.get("stream_started_at")
            )
            room["stream_started_at"] = None
            room["streaming"] = False
        for session in room.setdefault("watcher_sessions", {}).values():
            if session.get("joined_at"):
                session["seconds"] = int(session.get("seconds") or 0) + _seconds_between(
                    session.get("joined_at")
                )
                session["joined_at"] = None
        room["ended_at"] = _iso_now()
        room["final_watchers"] = list(room.get("current_watchers", []))
        room["current_watchers"] = []
        embed = live_room_embed(room)
        embed.color = SOFT
        embed.set_footer(text="Final room snapshot. The match recap is the conclusion.")
        msg = await _resolve_message(room)
        if msg:
            try:
                await msg.edit(embed=embed)
            except Exception as exc:
                print(f"[live] final room edit failed for {game_id}: {exc}")
        live["history"][str(game_id)] = room
        del active[str(game_id)]
        data.setdefault("community", {}).setdefault("active_announcements", {}).pop(
            str(game_id), None
        )
        save_data(data)
        return room
