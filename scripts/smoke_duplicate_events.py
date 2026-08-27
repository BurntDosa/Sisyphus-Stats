"""Offline smoke checks for duplicate Discord event protection."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import discord

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sisyphus.bot import SisyphusBot, SisyphusCommandTree, duplicate_event_guard  # noqa: E402
from sisyphus.dispatch import DuplicateEventGuard  # noqa: E402


async def main() -> None:
    clock = [100.0]
    guard = DuplicateEventGuard(max_age_seconds=10, max_entries=2, clock=lambda: clock[0])
    assert guard.claim("message:1")
    assert not guard.claim("message:1")
    assert guard.claim("interaction:1")
    assert guard.claim("message:2")
    assert guard.claim("message:1")
    clock[0] = 111.0
    assert guard.claim("message:2")

    duplicate_event_guard.clear()
    test_bot = SisyphusBot(
        command_prefix="!",
        help_command=None,
        intents=discord.Intents.none(),
        tree_cls=SisyphusCommandTree,
    )
    invocations = []

    async def get_context(_message):
        return SimpleNamespace(command=object())

    async def invoke(_ctx):
        invocations.append(True)

    test_bot.get_context = get_context
    test_bot.invoke = invoke
    message = SimpleNamespace(
        id=42,
        author=SimpleNamespace(bot=False),
    )
    await test_bot.process_commands(message)
    await test_bot.process_commands(message)
    assert len(invocations) == 1

    tree = test_bot.tree
    interaction = SimpleNamespace(id=99)
    assert await tree.interaction_check(interaction)
    assert not await tree.interaction_check(interaction)
    await test_bot.close()
    duplicate_event_guard.clear()
    print("Duplicate event smoke checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
