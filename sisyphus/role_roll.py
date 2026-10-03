"""Private champion-role rolls backed by a verified OP.GG lane snapshot."""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

import aiohttp
import discord

from .display_icons import role_display, role_emoji
from .ddragon import (
    build_role_build_image,
    build_role_runes_image,
    champion_icon_url,
    get_champion_id,
    get_summoner_spell_names,
)
from .opgg import get_champion_analysis
from .utils import now_ist

ROLE_SNAPSHOT_SOURCE = "OP.GG lane meta"
ROLE_SNAPSHOT_CAPTURED_AT = "2026-09-04"
ROLE_SNAPSHOT_PATCH = "16.17"
ROLE_SNAPSHOT_CHAMPION_COUNT = 173
ROLE_ROLL_COOLDOWN_SECONDS = 10
BUILD_CACHE_SECONDS = 30 * 60

ROLE_LABELS = {
    "top": "Top",
    "mid": "Mid",
    "jgl": "Jungle",
    "adc": "ADC",
    "supp": "Support",
    "wild": "Wild",
}
ROLE_TO_OPGG_POSITION = {
    "top": "top",
    "mid": "mid",
    "jgl": "jungle",
    "adc": "adc",
    "supp": "support",
}
ROLE_ALIASES = {
    "top": "top",
    "mid": "mid",
    "middle": "mid",
    "jgl": "jgl",
    "jg": "jgl",
    "jungle": "jgl",
    "adc": "adc",
    "bot": "adc",
    "bottom": "adc",
    "supp": "supp",
    "sup": "supp",
    "support": "supp",
    "wild": "wild",
}

# Captured from OP.GG's all-position lane meta on 2026-09-04. This is a
# one-time role snapshot: it contains every Riot Data Dragon champion at capture.
ROLE_POOLS = {
    "top": (
        "Aatrox", "Akali", "Ambessa", "Anivia", "Camille", "Cassiopeia",
        "Cho'Gath", "Darius", "Dr. Mundo", "Fiora", "Gangplank", "Garen",
        "Gnar", "Gragas", "Gwen", "Heimerdinger", "Illaoi", "Irelia",
        "Jax", "Jayce", "K'Sante", "Kayle", "Kennen", "Kled", "Malphite",
        "Malzahar", "Master Yi", "Mordekaiser", "Nasus", "Olaf", "Ornn",
        "Pantheon", "Poppy", "Quinn", "Renekton", "Riven", "Rumble", "Ryze",
        "Sett", "Shen", "Singed", "Sion", "Swain", "Tahm Kench", "Teemo",
        "Trundle", "Tryndamere", "Udyr", "Urgot", "Varus", "Vayne", "Vladimir",
        "Volibear", "Warwick", "Wukong", "Yasuo", "Yone", "Yorick", "Zaahen", "Zac",
    ),
    "mid": (
        "Ahri", "Akali", "Akshan", "Anivia", "Annie", "Aurelion Sol", "Aurora",
        "Azir", "Brand", "Cassiopeia", "Cho'Gath", "Diana", "Ekko", "Fizz", "Galio",
        "Gangplank", "Garen", "Gwen", "Hwei", "Irelia", "Jayce", "Kassadin", "Katarina",
        "LeBlanc", "Lissandra", "Locke", "Lux", "Malphite", "Malzahar", "Mel", "Naafiri",
        "Nasus", "Orianna", "Pantheon", "Qiyana", "Riven", "Ryze", "Sion", "Smolder",
        "Swain", "Sylas", "Syndra", "Taliyah", "Talon", "Tristana", "Twisted Fate", "Veigar",
        "Vel'Koz", "Vex", "Viktor", "Vladimir", "Xerath", "Yasuo", "Yone", "Zed", "Ziggs", "Zoe",
    ),
    "jgl": (
        "Aatrox", "Ambessa", "Amumu", "Bel'Veth", "Briar", "Cho'Gath", "Darius", "Diana",
        "Ekko", "Elise", "Evelynn", "Fiddlesticks", "Fizz", "Gragas", "Graves", "Gwen", "Hecarim",
        "Ivern", "Jarvan IV", "Jax", "Jayce", "Karthus", "Kayn", "Kha'Zix", "Kindred", "Lee Sin",
        "Lillia", "Locke", "Malphite", "Maokai", "Master Yi", "Naafiri", "Nidalee", "Nocturne",
        "Nunu & Willump", "Pantheon", "Poppy", "Qiyana", "Quinn", "Rammus", "Rek'Sai", "Rengar",
        "Sejuani", "Shaco", "Shyvana", "Skarner", "Sylas", "Taliyah", "Talon", "Teemo", "Trundle",
        "Udyr", "Vi", "Viego", "Volibear", "Warwick", "Wukong", "Xin Zhao", "Zac", "Zed", "Zyra",
    ),
    "adc": (
        "Aphelios", "Ashe", "Aurelion Sol", "Brand", "Caitlyn", "Corki", "Draven", "Ezreal", "Hwei",
        "Jhin", "Jinx", "Kai'Sa", "Kalista", "Karthus", "Katarina", "Kog'Maw", "Lucian", "Lux", "Mel",
        "Miss Fortune", "Nilah", "Samira", "Senna", "Seraphine", "Sivir", "Smolder", "Swain", "Syndra",
        "Tristana", "Twitch", "Varus", "Vayne", "Veigar", "Vel'Koz", "Viktor", "Vladimir", "Xayah", "Xerath",
        "Yasuo", "Yunara", "Zeri", "Ziggs",
    ),
    "supp": (
        "Alistar", "Amumu", "Ashe", "Bard", "Blitzcrank", "Brand", "Braum", "Camille", "Elise",
        "Fiddlesticks", "Galio", "Hwei", "Janna", "Karma", "LeBlanc", "Leona", "Lulu", "Lux", "Maokai",
        "Mel", "Milio", "Morgana", "Nami", "Nautilus", "Neeko", "Pantheon", "Poppy", "Pyke", "Rakan",
        "Rell", "Renata Glasc", "Senna", "Seraphine", "Shaco", "Shen", "Sona", "Soraka", "Swain", "Sylas",
        "Tahm Kench", "Taric", "Teemo", "Thresh", "Veigar", "Vel'Koz", "Xerath", "Yuumi", "Zilean", "Zoe", "Zyra",
    ),
}
WILD_POOL = tuple(sorted(set().union(*ROLE_POOLS.values())))

_build_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_active_users: set[int] = set()
_last_roll_at: dict[int, float] = {}


class RoleRollError(ValueError):
    """A user-visible role-roll validation or provider error."""


@dataclass(frozen=True)
class RoleRoll:
    role: str
    champion: str
    build: dict


def normalize_role(value: str | None) -> str | None:
    if not value:
        return None
    return ROLE_ALIASES.get(value.strip().lower())


def role_pool(role: str) -> tuple[str, ...]:
    if role == "wild":
        return WILD_POOL
    return ROLE_POOLS[role]


def choose_champion(role: str) -> str:
    return secrets.choice(role_pool(role))


def begin_role_roll(user_id: int) -> str | None:
    now = time.monotonic()
    if user_id in _active_users:
        return "Your previous champion roll is still being prepared."
    elapsed = now - _last_roll_at.get(user_id, 0.0)
    if elapsed < ROLE_ROLL_COOLDOWN_SECONDS:
        remaining = max(1, int(ROLE_ROLL_COOLDOWN_SECONDS - elapsed))
        return f"Wait {remaining}s before another champion roll."
    _active_users.add(user_id)
    return None


def finish_role_roll(user_id: int, *, successful: bool) -> None:
    _active_users.discard(user_id)
    if successful:
        _last_roll_at[user_id] = time.monotonic()


async def get_role_build(
    session: aiohttp.ClientSession, champion: str, role: str
) -> tuple[dict | None, str | None]:
    position = ROLE_TO_OPGG_POSITION.get(role)
    if position is None:
        # Wild champions still need a real position for OP.GG. Choose one of
        # their verified lanes so the returned runes and build are meaningful.
        position = secrets.choice(
            [
                opgg_position
                for canonical, opgg_position in ROLE_TO_OPGG_POSITION.items()
                if champion in ROLE_POOLS[canonical]
            ]
        )
    key = (champion, position)
    cached = _build_cache.get(key)
    if cached and time.monotonic() - cached[0] < BUILD_CACHE_SECONDS:
        return cached[1], None

    build, error = await get_champion_analysis(session, champion, position)
    if error:
        return None, error
    if not build:
        return None, "OP.GG did not return a usable build."
    _build_cache[key] = (time.monotonic(), build)
    return build, None


def _names(values: list[str]) -> str:
    return " / ".join(values) if values else "Not recorded"


def _item_line(label: str, item_set: dict) -> str:
    names = item_set.get("names") or []
    return f"**{label}:** {_names(names)}"


def _option_ids(options: list[dict]) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for option in options:
        for item_id in option.get("ids") or []:
            if item_id not in seen:
                seen.add(item_id)
                ids.append(item_id)
    return ids


def _option_line(slot: str, options: list[dict]) -> str:
    choices = []
    for option in options:
        names = option.get("names") or []
        if names:
            choices.append(" + ".join(names))
    return f"**{slot} item - choose one:** {' / '.join(choices) or 'Not recorded'}"


def _later_item_rows(later_items: dict) -> list[tuple[str, list[int]]]:
    rows = []
    for slot in ("4th", "5th", "6th"):
        ids = _option_ids(later_items.get(slot) or [])
        if ids:
            rows.append((f"{slot} choices", ids))
    return rows


async def build_role_roll_message(
    session: aiohttp.ClientSession, roll: RoleRoll
) -> tuple[list[discord.Embed], list[discord.File]]:
    build = roll.build
    runes = build["runes"]
    files = []
    runes_file = await build_role_runes_image(
        session,
        runes["primary_ids"],
        runes["secondary_ids"],
        runes["stat_ids"],
    )
    later_items = build.get("later_items") or {}
    build_file = await build_role_build_image(
        session,
        build["spell_ids"],
        [
            ("Start", build["starter"]["ids"]),
            ("Boots", build["boots"]["ids"]),
            ("Core", build["core"]["ids"]),
            *_later_item_rows(later_items),
        ],
    )
    files.extend([runes_file, build_file])
    spell_names = await get_summoner_spell_names(session, build["spell_ids"])
    role_label = ROLE_LABELS[roll.role]
    title = f"Your {role_label} Roll: {roll.champion}"
    if roll.role == "wild":
        title = f"Your Wild Roll: {roll.champion}"

    overview = discord.Embed(
        title=title,
        description=(
            "A private champion assignment, with the current OP.GG loadout ready to take into queue."
        ),
        color=0xA0283B,
        timestamp=now_ist(),
    )
    champion_id = await get_champion_id(session, roll.champion)
    icon_url = champion_icon_url(champion_id)
    if icon_url:
        overview.set_thumbnail(url=icon_url)
    if role_emoji(roll.role):
        overview.description += f"\n{role_display(roll.role)}"
    overview.add_field(name="Summoner Spells", value=_names(spell_names), inline=False)
    if roll.role == "wild":
        overview.add_field(
            name="OP.GG Build Lane",
            value=str(build.get("position") or "Not recorded").title(),
            inline=True,
        )
    for level, skill in enumerate(build["skills"][:3], start=1):
        overview.add_field(name=f"Level {level}", value=skill or "Not recorded", inline=True)
    overview.add_field(
        name="Skill Priority",
        value=" > ".join(build["skill_masteries"]) or "Not recorded",
        inline=False,
    )
    overview.set_footer(
        text=(
            f"Role pool: {ROLE_SNAPSHOT_SOURCE}, {ROLE_SNAPSHOT_PATCH} snapshot "
            f"({ROLE_SNAPSHOT_CAPTURED_AT})"
        )
    )

    rune_embed = discord.Embed(
        title="Runes",
        description=(
            f"**Primary:** {runes['primary_page']}\n"
            f"{_names(runes['primary_names'])}\n\n"
            f"**Secondary:** {runes['secondary_page']}\n"
            f"{_names(runes['secondary_names'])}\n\n"
            f"**Stat Shards:** {_names(runes['stat_names'])}"
        ),
        color=0x496B55,
    )
    rune_embed.set_image(url="attachment://role-runes.png")

    build_lines = [
        _item_line("Start", build["starter"]),
        _item_line("Boots", build["boots"]),
        _item_line("Core", build["core"]),
    ]
    for slot in ("4th", "5th", "6th"):
        options = later_items.get(slot) or []
        if options:
            build_lines.append(_option_line(slot, options))

    build_embed = discord.Embed(
        title="Build",
        description="\n".join(build_lines),
        color=0x8A5613,
    )
    build_embed.set_image(url="attachment://role-build.png")
    build_embed.set_footer(text="Build and rune recommendations from OP.GG")
    return [overview, rune_embed, build_embed], files


def reset_role_roll_state() -> None:
    """Test-only reset for process-local cooldown and cache state."""
    _build_cache.clear()
    _active_users.clear()
    _last_roll_at.clear()
