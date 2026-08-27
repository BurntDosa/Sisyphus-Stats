"""Entry point: `python -m sisyphus`.

Loads .env (via config), creates the bot instance, registers commands, runs.
"""
from __future__ import annotations

import os
import socket

from .config import APP_VERSION, DISCORD_TOKEN
from .process_lock import BotInstanceAlreadyRunning, acquire_bot_instance_lock


def main() -> None:
    if not DISCORD_TOKEN:
        raise SystemExit(
            "DISCORD_TOKEN not set. Copy env.example_v2 to .env and fill it in."
        )
    try:
        instance_lock = acquire_bot_instance_lock()
    except BotInstanceAlreadyRunning as exc:
        raise SystemExit(str(exc)) from exc

    print(
        f"[startup] Sisyphus v{APP_VERSION} starting "
        f"host={socket.gethostname()} pid={os.getpid()}"
    )

    # Import side effects: commands.py registers all @bot.command handlers,
    # polling.py defines the background tasks (started by bot.on_ready).
    from .bot import bot
    from . import commands  # noqa: F401
    from . import polling  # noqa: F401

    try:
        bot.run(DISCORD_TOKEN)
    finally:
        instance_lock.release()


if __name__ == "__main__":
    main()
