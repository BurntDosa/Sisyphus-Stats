"""Optional Riot profile-icon lookup; no bot or runtime state dependencies."""
from __future__ import annotations

import asyncio
from time import monotonic
from urllib.parse import quote

import aiohttp

from . import config
from .ddragon import get_ddragon_version

# Refresh successful icons hourly and back off failed lookups for five minutes.
_cache: dict[tuple[str, str, str], tuple[float, str | None]] = {}


async def get_profile_icon_url(session, riot_id: str) -> str | None:
    if not config.RIOT_KEY or "#" not in riot_id:
        return None
    game_name, tag = riot_id.rsplit("#", 1)
    if not game_name or not tag:
        return None
    key = (config.PLATFORM, config.REGION, riot_id.casefold())
    cached = _cache.get(key)
    if cached and cached[0] > monotonic():
        return cached[1]
    url = None
    headers = {"X-Riot-Token": config.RIOT_KEY}
    try:
        # Resolve with Riot rather than assuming an OP.GG PUUID is interchangeable.
        async with asyncio.timeout(6):
            async with session.get(
                f"https://{config.REGION}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/"
                f"{quote(game_name, safe='')}/{quote(tag, safe='')}", headers=headers,
            ) as response:
                account = await response.json() if response.status == 200 else None
            puuid = account.get("puuid") if isinstance(account, dict) else None
            if isinstance(puuid, str) and puuid:
                async with session.get(
                    f"https://{config.PLATFORM}.api.riotgames.com/lol/summoner/v4/summoners/"
                    f"by-puuid/{quote(puuid, safe='')}", headers=headers,
                ) as response:
                    summoner = await response.json() if response.status == 200 else None
                icon = summoner.get("profileIconId") if isinstance(summoner, dict) else None
                if isinstance(icon, int) and not isinstance(icon, bool) and icon >= 0:
                    version = await get_ddragon_version(session)
                    asset = f"https://ddragon.leagueoflegends.com/cdn/{version}/img/profileicon/{icon}.png"
                    # New icons can precede the Data Dragon release. Avoid broken images.
                    async with session.get(asset) as response:
                        if response.status == 200 and response.headers.get("Content-Type", "").startswith("image/"):
                            url = asset
    except (aiohttp.ClientError, TimeoutError, ValueError, TypeError, KeyError, AttributeError):
        # Cosmetic failures must not suppress the result or expose credentials.
        pass
    successful = url is not None
    if not successful and cached:
        url = cached[1]
    if len(_cache) >= 512 and key not in _cache:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (monotonic() + (3600 if successful else 300), url)
    return url
