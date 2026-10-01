"""Riot DDragon CDN helpers — version fetch, item image composite, champion icons."""
from __future__ import annotations

import asyncio
import io

import aiohttp
import discord
from PIL import Image, ImageDraw


def compose_champion_roster(images: list[Image.Image], size: int = 256) -> bytes:
    """Two equal diagonal portraits, in the same order as the recap's players."""
    if len(images) == 2:
        enlarged = size * 7 // 5
        offset = enlarged - size
        first = images[0].convert("RGBA").resize((enlarged, enlarged), Image.LANCZOS)
        second = images[1].convert("RGBA").resize((enlarged, enlarged), Image.LANCZOS)
        # Move the faces into their own triangular halves instead of cutting
        # both portraits through the center of their faces.
        first = first.crop((offset, offset, enlarged, enlarged))
        second = second.crop((0, 0, size, size))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).polygon([(0, 0), (size - 1, 0), (0, size - 1)], fill=255)
        image = Image.composite(first, second, mask)
        ImageDraw.Draw(image).line((0, size - 1, size - 1, 0), fill=(24, 27, 35, 255), width=3)
    else:
        columns = 1 if len(images) == 1 else 2 if len(images) <= 4 else 3
        rows = max(1, (len(images) + columns - 1) // columns)
        tile = size // columns
        image = Image.new("RGBA", (size, rows * tile), (24, 27, 35, 255))
        for index, source in enumerate(images):
            image.paste(source.convert("RGBA").resize((tile, tile), Image.LANCZOS),
                ((index % columns) * tile, (index // columns) * tile))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


_champion_roster_tiles: dict[int, Image.Image] = {}


async def build_champion_roster_thumbnail(session, champion_ids) -> bytes | None:
    async def fetch(champion_id):
        if champion_id in _champion_roster_tiles:
            return _champion_roster_tiles[champion_id]
        try:
            async with asyncio.timeout(10):
                image = await _fetch_png(session, champion_icon_url(champion_id), 256)
        except TimeoutError:
            image = None
        if image:
            _champion_roster_tiles[champion_id] = image
        return image or _placeholder_tile(256, 0)
    if not champion_ids:
        return None
    return compose_champion_roster(await asyncio.gather(*(fetch(cid) for cid in champion_ids)))

_ddragon_version: str | None = None


def champion_icon_url(champion_id):
    if not champion_id:
        return None
    return (
        "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/"
        f"global/default/v1/champion-icons/{champion_id}.png"
    )


async def get_ddragon_version(session: aiohttp.ClientSession) -> str:
    global _ddragon_version
    if _ddragon_version is None:
        try:
            async with session.get(
                "https://ddragon.leagueoflegends.com/api/versions.json"
            ) as resp:
                versions = await resp.json()
                _ddragon_version = versions[0]
        except Exception as exc:
            print(f"[ddragon] version fetch failed, using fallback: {exc}")
            _ddragon_version = "14.8.1"
    return _ddragon_version


async def fetch_item_image(
    session: aiohttp.ClientSession, version: str, item_id: int, size: int = 32
) -> Image.Image | None:
    if not item_id or item_id == 0:
        return None
    url = f"https://ddragon.leagueoflegends.com/cdn/{version}/img/item/{item_id}.png"
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                img = Image.open(io.BytesIO(data)).convert("RGBA")
                return img.resize((size, size), Image.LANCZOS)
    except Exception as exc:
        print(f"[ddragon] item {item_id} fetch failed: {exc}")
    return None


async def build_composite_items_image(
    session: aiohttp.ClientSession, item_ids: list[int]
) -> discord.File | None:
    version = await get_ddragon_version(session)
    coros = [
        fetch_item_image(session, version, iid) for iid in item_ids if iid and iid != 0
    ]
    if not coros:
        return None

    images = await asyncio.gather(*coros)
    images = [img for img in images if img]
    if not images:
        return None

    width = sum(img.width for img in images) + (len(images) - 1) * 6
    height = 32

    comp = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    x_offset = 0
    for img in images:
        comp.paste(img, (x_offset, 0))
        x_offset += img.width + 6

    out = io.BytesIO()
    comp.save(out, format="PNG")
    out.seek(0)
    return discord.File(out, filename="items.png")


_champion_id_to_name: dict[int, str] = {}

async def get_champion_name(session: aiohttp.ClientSession, champion_id: int) -> str:
    global _champion_id_to_name
    if not _champion_id_to_name:
        try:
            version = await get_ddragon_version(session)
            url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for name, info in data.get("data", {}).items():
                        cid = int(info.get("key", 0))
                        _champion_id_to_name[cid] = info.get("name", name)
        except Exception as e:
            print(f"[ddragon] Failed to load champion mapping: {e}")
            
    return _champion_id_to_name.get(champion_id, f"Champion {champion_id}")


_champion_name_to_id: dict[str, int] = {}

async def get_champion_id(session: aiohttp.ClientSession, champion_name: str) -> int | None:
    global _champion_name_to_id
    if not _champion_name_to_id:
        try:
            version = await get_ddragon_version(session)
            url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for name, info in data.get("data", {}).items():
                        cid = int(info.get("key", 0))
                        cname = info.get("name", name).strip().lower()
                        _champion_name_to_id[cname] = cid
                        _champion_name_to_id[name.lower()] = cid
        except Exception as e:
            print(f"[ddragon] Failed to load champion ID mapping: {e}")
            
    return _champion_name_to_id.get(champion_name.strip().lower())


_rune_icon_paths: dict[int, str] = {}
_summoner_spell_info: dict[int, tuple[str, str]] = {}
_STAT_SHARD_ICON_PATHS = {
    5001: "perk-images/StatMods/StatModsHealthScalingIcon.png",
    5002: "perk-images/StatMods/StatModsArmorIcon.png",
    5003: "perk-images/StatMods/StatModsMagicResIcon.png",
    5005: "perk-images/StatMods/StatModsAttackSpeedIcon.png",
    5007: "perk-images/StatMods/StatModsCDRScalingIcon.png",
    5008: "perk-images/StatMods/StatModsAdaptiveForceIcon.png",
}


async def _load_rune_icon_paths(session: aiohttp.ClientSession) -> None:
    if _rune_icon_paths:
        return
    version = await get_ddragon_version(session)
    try:
        async with session.get(
            f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/runesReforged.json"
        ) as response:
            if response.status != 200:
                return
            trees = await response.json()
    except Exception as exc:
        print(f"[ddragon] rune metadata fetch failed: {exc}")
        return
    for tree in trees:
        for slot in tree.get("slots", []):
            for rune in slot.get("runes", []):
                rune_id = rune.get("id")
                icon = rune.get("icon")
                if isinstance(rune_id, int) and isinstance(icon, str):
                    _rune_icon_paths[rune_id] = icon


async def _load_summoner_spell_info(session: aiohttp.ClientSession) -> None:
    if _summoner_spell_info:
        return
    version = await get_ddragon_version(session)
    try:
        async with session.get(
            f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/summoner.json"
        ) as response:
            if response.status != 200:
                return
            spells = (await response.json()).get("data", {})
    except Exception as exc:
        print(f"[ddragon] summoner-spell metadata fetch failed: {exc}")
        return
    for spell in spells.values():
        try:
            spell_id = int(spell.get("key"))
        except (TypeError, ValueError):
            continue
        image_name = (spell.get("image") or {}).get("full")
        if image_name:
            _summoner_spell_info[spell_id] = (
                str(spell.get("name") or f"Spell {spell_id}"),
                f"https://ddragon.leagueoflegends.com/cdn/{version}/img/spell/{image_name}",
            )


async def get_summoner_spell_names(
    session: aiohttp.ClientSession, spell_ids: list[int]
) -> list[str]:
    await _load_summoner_spell_info(session)
    return [
        _summoner_spell_info.get(spell_id, (f"Spell {spell_id}", ""))[0]
        for spell_id in spell_ids
    ]


async def _fetch_png(
    session: aiohttp.ClientSession, url: str, size: int
) -> Image.Image | None:
    if not url:
        return None
    try:
        async with session.get(url) as response:
            if response.status != 200:
                return None
            image = Image.open(io.BytesIO(await response.read())).convert("RGBA")
            return image.resize((size, size), Image.LANCZOS)
    except Exception as exc:
        print(f"[ddragon] image fetch failed: {exc}")
        return None


def _placeholder_tile(size: int, index: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (53, 39, 34, 255))
    draw = ImageDraw.Draw(image)
    inset = max(3, size // 12)
    draw.rectangle(
        (inset, inset, size - inset, size - inset),
        outline=(160, 40, 59, 255),
        width=max(2, size // 16),
    )
    draw.text((size // 2 - 3, size // 2 - 6), "?", fill=(255, 243, 231, 255))
    return image


async def _rune_image(
    session: aiohttp.ClientSession, rune_id: int, size: int
) -> Image.Image:
    await _load_rune_icon_paths(session)
    icon_path = _rune_icon_paths.get(rune_id) or _STAT_SHARD_ICON_PATHS.get(rune_id)
    image = await _fetch_png(
        session,
        f"https://ddragon.leagueoflegends.com/cdn/img/{icon_path}" if icon_path else "",
        size,
    )
    return image or _placeholder_tile(size, rune_id)


async def _spell_image(
    session: aiohttp.ClientSession, spell_id: int, size: int
) -> Image.Image:
    await _load_summoner_spell_info(session)
    image = await _fetch_png(session, _summoner_spell_info.get(spell_id, ("", ""))[1], size)
    return image or _placeholder_tile(size, spell_id)


async def _item_or_placeholder(
    session: aiohttp.ClientSession, version: str, item_id: int, size: int
) -> Image.Image:
    return await fetch_item_image(session, version, item_id, size) or _placeholder_tile(size, item_id)


def _compose_rows(rows: list[tuple[str, list[Image.Image]]], filename: str) -> discord.File:
    tile_size = 56
    label_width = 124
    gap = 8
    width = max(400, label_width + max((len(images) for _label, images in rows), default=1) * (tile_size + gap) + 24)
    height = max(84, len(rows) * 76 + 18)
    image = Image.new("RGBA", (width, height), (34, 26, 23, 255))
    draw = ImageDraw.Draw(image)
    y = 12
    for index, (label, images) in enumerate(rows):
        draw.text((14, y + 20), label, fill=(255, 243, 231, 255))
        x = label_width
        for tile in images:
            image.paste(tile, (x, y), tile)
            x += tile_size + gap
        if index < len(rows) - 1:
            draw.line((14, y + 66, width - 14, y + 66), fill=(111, 88, 78, 255), width=1)
        y += 76
    out = io.BytesIO()
    image.save(out, format="PNG")
    out.seek(0)
    return discord.File(out, filename=filename)


async def build_role_runes_image(
    session: aiohttp.ClientSession,
    primary_ids: list[int],
    secondary_ids: list[int],
    stat_ids: list[int],
) -> discord.File:
    rows = []
    for label, ids in (("Primary", primary_ids), ("Secondary", secondary_ids), ("Shards", stat_ids)):
        images = await asyncio.gather(*[_rune_image(session, rune_id, 56) for rune_id in ids])
        rows.append((label, images))
    return _compose_rows(rows, "role-runes.png")


async def build_role_build_image(
    session: aiohttp.ClientSession,
    spell_ids: list[int],
    item_rows: list[tuple[str, list[int]]],
) -> discord.File:
    version = await get_ddragon_version(session)
    rows = [("Spells", await asyncio.gather(*[_spell_image(session, spell_id, 56) for spell_id in spell_ids]))]
    for label, item_ids in item_rows:
        images = await asyncio.gather(
            *[_item_or_placeholder(session, version, item_id, 56) for item_id in item_ids]
        )
        rows.append((label, images))
    return _compose_rows(rows, "role-build.png")
