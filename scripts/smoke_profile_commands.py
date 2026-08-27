"""Offline regression check for personal and betting profile command routing."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sisyphus import betting  # noqa: E402
from sisyphus.commands import cmd_bprofile, cmd_profile  # noqa: E402
from sisyphus.state import data as state_data  # noqa: E402


class _Author:
    id = 987654321


class _Context:
    author = _Author()
    interaction = None

    def __init__(self):
        self.sent: list[dict] = []

    async def defer(self):
        return None

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return object()


async def main() -> None:
    target = next(iter(state_data.get("tracked", {})), None)
    if not target:
        raise AssertionError("profile smoke check requires one tracked player")

    original_data = betting.data
    original_save = betting.save_data
    try:
        betting.data = {
            "betting": {
                "wallets": {},
                "markets": {},
                "bets": {},
                "audit": [],
                "meta": {},
            }
        }
        betting.save_data = lambda _data: None

        personal = _Context()
        await cmd_profile.callback(personal, target=target)
        betting_context = _Context()
        await cmd_bprofile.callback(betting_context)

        personal_embed = personal.sent[0]["embed"]
        betting_embed = betting_context.sent[0]["embed"]
        assert len(personal.sent) == 1
        assert personal_embed.title != "👤 Betting Profile"
        assert len(betting_context.sent) == 1
        assert betting_embed.title == "👤 Betting Profile"
    finally:
        betting.data = original_data
        betting.save_data = original_save

    print("Profile command routing smoke check passed.")


if __name__ == "__main__":
    asyncio.run(main())
