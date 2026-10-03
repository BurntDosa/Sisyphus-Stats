"""Check private emoji fallbacks and optional Riot profile-icon presentation."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sisyphus import display_icons as icons, summoner_icons as profiles
from sisyphus.preview_fixtures import solo_fixture
from sisyphus.ranks import tier_emoji, tier_image_url
from sisyphus.views import ScoreboardView, StatsTabsView


class Response:
    def __init__(self, status, body=None, content_type="application/json"):
        self.status, self.body = status, body
        self.headers = {"Content-Type": content_type}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return self.body


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "api.riotgames.com" in url:
            assert kwargs["headers"] == {"X-Riot-Token": "synthetic-key"}
        else:
            assert "headers" not in kwargs  # Never send credentials to the image CDN.
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


async def checks():
    with patch.dict(os.environ, {"RANK_EMOJIS": json.dumps({"GOLD": "<:rank_gold:101>"}),
                                 "ROLE_EMOJIS": json.dumps({"MID": "<:role_mid:102>"})}), \
         patch.object(icons, "_validator", lambda emoji_id: emoji_id in {101, 102}):
        assert tier_emoji("gold") == "<:rank_gold:101>"
        assert tier_image_url("GOLD") == "https://cdn.discordapp.com/emojis/101.png?size=128"
        assert icons.role_display("MIDDLE") == "<:role_mid:102> Mid"
        with patch.object(icons, "_validator", lambda _: False):
            assert tier_emoji("GOLD") == "🟡"
            assert icons.role_display("MID") == "Mid"
        for malformed in ("oops", "[]", '{"GOLD":"<:bad:0>"}'):
            with patch.dict(os.environ, {"RANK_EMOJIS": malformed}):
                assert tier_emoji("GOLD") == "🟡"

        fixture = solo_fixture("victory")
        member = fixture["members"][0]
        args = (fixture["match"], member["puuid"], member["riot_id"], "GOLD", "II", 65, 1844, 1865)
        view = ScoreboardView(*args)
        assert "icon_url" not in view._overview_embed().to_dict()["author"]
        url = "https://ddragon.leagueoflegends.com/cdn/16.19.1/img/profileicon/6084.png"
        view.profile_icon_url = url
        assert view._overview_embed().author.icon_url == url
        assert "<:rank_gold:101>" in view._overview_embed().description
        assert view._team_embed(view.blue_team, "Blue", "Victory", 0).author.icon_url == url
        view.stop()
        # Stats pages retain their separate rank identity; no solo-only attribute leaks.
        assert "self.profile_icon_url" not in __import__("inspect").getsource(StatsTabsView)

    with patch.object(profiles.config, "RIOT_KEY", "synthetic-key"), \
         patch.object(profiles.config, "PLATFORM", "sg2"), \
         patch.object(profiles.config, "REGION", "asia"), \
         patch.object(profiles, "get_ddragon_version", AsyncMock(return_value="16.19.1")), \
         patch.object(profiles, "_cache", {}):
        good = Session([Response(200, {"puuid": "verified"}),
                        Response(200, {"profileIconId": 6084}), Response(200, content_type="image/png")])
        assert await profiles.get_profile_icon_url(good, "Player#TEST") == url
        assert len(good.calls) == 3
        assert "by-riot-id/Player/TEST" in good.calls[0][0]
        assert "by-puuid/verified" in good.calls[1][0]
        assert await profiles.get_profile_icon_url(Session([]), "Player#TEST") == url
        key = ("sg2", "asia", "player#test")
        profiles._cache[key] = (0, url)
        assert await profiles.get_profile_icon_url(Session([Response(403)]), "Player#TEST") == url
        for responses in ([Response(403)], [Response(429)], [TimeoutError()],
                          [Response(200, {})], [Response(200, [])],
                          [Response(200, {"puuid": "verified"}), Response(429)],
                          [Response(200, {"puuid": "verified"}), Response(200, {"profileIconId": True})],
                          [Response(200, {"puuid": "verified"}), Response(200, {"profileIconId": -1})],
                          [Response(200, {"puuid": "verified"}), Response(200, {"profileIconId": 6084}), Response(404)]):
            profiles._cache.clear()
            failed = Session(responses)
            assert await profiles.get_profile_icon_url(failed, "Player#TEST") is None
            assert await profiles.get_profile_icon_url(Session([]), "Player#TEST") is None
        profiles._cache.clear()
        with patch.object(profiles.config, "RIOT_KEY", None):
            assert await profiles.get_profile_icon_url(Session([]), "Player#TEST") is None
        assert await profiles.get_profile_icon_url(Session([]), "Malformed") is None
    print("Display icons smoke passed: mappings, inaccessible emojis, solo/stats scope, Riot identity, cache, 403/429/timeout/missing assets, credential isolation.")


if __name__ == "__main__":
    asyncio.run(checks())
