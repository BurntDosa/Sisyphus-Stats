"""Discord bot instance, intents, post-destination resolution, on_ready."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .config import (
    BETTING_ENABLED,
    DESTINATION_ID,
    OPGG_REGION,
    TELEGRAM_POLLING_ENABLED,
)
from .dispatch import DuplicateEventGuard


duplicate_event_guard = DuplicateEventGuard()


class SisyphusCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return duplicate_event_guard.claim(f"interaction:{interaction.id}")


class SisyphusBot(commands.Bot):
    async def process_commands(self, message: discord.Message, /) -> None:
        if message.author.bot:
            return

        ctx = await self.get_context(message)
        if ctx.command is None:
            return
        if not duplicate_event_guard.claim(f"message:{message.id}"):
            return
        await self.invoke(ctx)

intents = discord.Intents.default()
intents.message_content = True
intents.presences = True
intents.members = True
intents.voice_states = True
bot = SisyphusBot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    tree_cls=SisyphusCommandTree,
)

_post_destination: discord.abc.Messageable | None = None
_slash_commands_synced = False


async def get_post_destination() -> discord.abc.Messageable | None:
    global _post_destination
    if _post_destination is not None:
        return _post_destination
    if DESTINATION_ID == 0:
        return None

    destination = bot.get_channel(DESTINATION_ID)
    if destination is None:
        try:
            destination = await bot.fetch_channel(DESTINATION_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            print(f"[bot] could not fetch channel {DESTINATION_ID}: {exc}")
            return None

    if isinstance(destination, discord.abc.Messageable):
        _post_destination = destination
        return destination
    return None


@bot.event
async def on_ready():
    global _slash_commands_synced

    print(f"🏋️‍♂️ Sisyphus' Daily Data is online as {bot.user}")
    print(f"📡 Data source: OP.GG MCP ({OPGG_REGION})")
    from .health import discord_latency_ms, mark_bot_online, mark_discord

    mark_bot_online()
    latency_ms = discord_latency_ms(bot.latency)
    mark_discord(True, latency_ms)
    if DESTINATION_ID == 0:
        print("⚠️ Set THREAD_ID (preferred) or CHANNEL_ID in .env to post updates.")
    elif not await get_post_destination():
        print(f"⚠️ Could not access destination with ID {DESTINATION_ID}.")

    # Local imports avoid circular dependency at module load time.
    from .betting import register_persistent_market_views
    from .polling import (
        betting_housekeeping_task,
        check_key_expiry,
        daily_summary_task,
        monthly_recap_task,
        poll_players,
        tracked_presence_task,
        weekly_squad_recap_task,
    )

    # 1. Prompt developer in the background; do not block bot startup on a DM.
    try:
        from .changelog import prompt_changelog_on_startup
        bot.loop.create_task(prompt_changelog_on_startup(bot))
    except Exception as e:
        print(f"[bot] Failed to execute startup changelog prompt: {e}")

    # 2. Start functional tasks, loops, and views
    if not poll_players.is_running():
        poll_players.start()
    if not daily_summary_task.is_running():
        daily_summary_task.start()
    if not weekly_squad_recap_task.is_running():
        weekly_squad_recap_task.start()
    if not monthly_recap_task.is_running():
        monthly_recap_task.start()
    if BETTING_ENABLED and not betting_housekeeping_task.is_running():
        betting_housekeeping_task.start()
    if not check_key_expiry.is_running():
        check_key_expiry.start()
    if not tracked_presence_task.is_running():
        tracked_presence_task.start()
    if BETTING_ENABLED:
        register_persistent_market_views(bot)

    if TELEGRAM_POLLING_ENABLED:
        try:
            from .telegram import poll_telegram_updates
            bot.loop.create_task(poll_telegram_updates())
            print("[bot] Launched Telegram background polling task.")
        except Exception as e:
            print(f"[bot] Failed to start Telegram task: {e}")

    if not _slash_commands_synced:
        try:
            from .config import GUILD_ID

            if GUILD_ID > 0:
                guild = discord.Object(id=GUILD_ID)
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                print(
                    f"⚡ Synced {len(synced)} guild slash commands to guild ID {GUILD_ID}"
                )

                # Avoid duplicate command-picker entries caused by having both
                # guild-scoped and global copies of the same slash commands.
                bot.tree.clear_commands(guild=None)
                await bot.tree.sync(guild=None)
                print("🧹 Cleared global slash commands to avoid duplicates")
            else:
                synced = await bot.tree.sync()
                print(f"📡 Synced {len(synced)} global slash commands")
            _slash_commands_synced = True
        except Exception as exc:
            print(f"[bot] tree.sync failed: {exc}")


@bot.event
async def on_disconnect():
    from .health import mark_discord

    mark_discord(False)


@bot.event
async def on_resumed():
    from .health import discord_latency_ms, mark_discord

    latency_ms = discord_latency_ms(bot.latency)
    mark_discord(True, latency_ms)


@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel == after.channel and before.self_stream == after.self_stream:
        return
    try:
        from .live import handle_voice_state_update

        await handle_voice_state_update(member)
    except Exception as exc:
        print(f"[bot] voice-state live-room update failed: {exc}")
