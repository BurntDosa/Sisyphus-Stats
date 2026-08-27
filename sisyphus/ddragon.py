"""Riot DDragon CDN helpers — version fetch, item image composite, champion icons."""
from __future__ import annotations

import asyncio
import io

import aiohttp
import discord
from PIL import Image

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
    session: aiohttp.ClientSession, version: str, item_id: int
) -> Image.Image | None:
    if not item_id or item_id == 0:
        return None
    url = f"https://ddragon.leagueoflegends.com/cdn/{version}/img/item/{item_id}.png"
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                img = Image.open(io.BytesIO(data)).convert("RGBA")
                return img.resize((32, 32), Image.LANCZOS)
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


