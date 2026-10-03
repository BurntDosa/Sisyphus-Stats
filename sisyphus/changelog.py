"""Startup release changelog prompt."""
from __future__ import annotations

import asyncio
import subprocess
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord

from .config import (
    APP_VERSION,
    DEVELOPER_DISCORD_ID,
    DESTINATION_ID,
)
from .utils import now_ist

MAX_EMBED_DESCRIPTION_CHARS = 3600
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_NOTES_DIR = PROJECT_ROOT / "release_notes"
_publish_lock = asyncio.Lock()
BLOCKED_CHANGELOG_PHRASES = (
    "filter by role",
    "queue type",
    "other squads",
    "another squad",
    "let us know",
    "real-time",
    "monday",
    "best players",
    "needs a coffee",
    "lfg",
)
ALLOWED_SLASH_COMMANDS = {
    "audit",
    "balance",
    "bet",
    "bprofile",
    "cancelbet",
    "dailyreport",
    "dashboard",
    "editbet",
    "halloffame",
    "help",
    "insurance",
    "leaderboard",
    "link",
    "list",
    "marketbets",
    "marketopen",
    "markets",
    "marketstatus",
    "monthlyrecap",
    "mybets",
    "profile",
    "queueboard",
    "queueclear",
    "queueup",
    "recap",
    "refund",
    "report",
    "role",
    "rivalry",
    "settlebet",
    "squadgoal",
    "stats",
    "status",
    "teams",
    "track",
    "unlink",
    "untrack",
    "voidbet",
    "wallet",
    "weeklyrecap",
    "whoami",
}

CURATED_RELEASE_VERSIONS = {"v2.0.0", "v2.1.0", "v2.1.6", "v2.1.11"}


def run_git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT).decode().strip()


def get_git_sha(ref: str = "HEAD") -> str:
    try:
        return run_git(["rev-parse", "--short=12", ref])
    except Exception as exc:
        print(f"[changelog] Failed to query git sha: {exc}")
        return "unknown"


def get_git_version() -> str:
    return f"v{APP_VERSION}"


def truncate_text(text: str, limit: int, marker: str = "\n\n... truncated.") -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(marker))].rstrip() + marker


def changelog_state() -> dict:
    from .state import data

    state = data.setdefault("changelog", {})
    state.setdefault("last_processed_version", None)
    state.setdefault("last_processed_sha", None)
    state.setdefault("v2_curated_processed_sha", None)
    state.setdefault("curated_processed_versions", {})
    state.setdefault("releases", {})
    return state


def needs_curated_prompt(version: str, sha: str, state: dict) -> bool:
    if version not in CURATED_RELEASE_VERSIONS:
        return False
    processed = state.setdefault("curated_processed_versions", {})
    # Curated releases are explicitly one-time announcements. Their decision
    # must survive the release commit that follows the startup prompt.
    if version == "v2.0.0" and state.get("v2_curated_processed_sha"):
        return False
    return version not in processed


def build_curated_v2_embed(version: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"Sisyphus {version} is live",
        description=(
            "The v2 update turns tracked ranked games into shared server moments, "
            "then saves the good parts into Sisyphus history."
        ),
        color=0x57F287,
        timestamp=now_ist(),
    )
    embed.add_field(
        name="Live Game Room",
        value=(
            "Queue Beacon now edits in place while a tracked Solo/Duo game is live: "
            "stream status, voice-channel watchers, and watcher intent all update on the same message."
        ),
        inline=False,
    )
    embed.add_field(
        name="Player Profiles",
        value=(
            "`/profile` now shows Sisyphus-observed history: Overview, Journey, "
            "Identity, Records, and Memories. The old betting profile moved to `/bprofile`."
        ),
        inline=False,
    )
    embed.add_field(
        name="Match Memories",
        value=(
            "Fresh recap cards now have a Remember button. Linked players can name "
            "their own matches and save them to their profile."
        ),
        inline=False,
    )
    embed.add_field(
        name="Monthly Recaps",
        value=(
            "Sisyphus now prepares monthly server recaps and private player DMs "
            "from the ranked games it actually witnessed."
        ),
        inline=False,
    )
    embed.add_field(
        name="Recaps & Help",
        value=(
            "Post-game recaps got a short story line, and `!help`/`/help` now list "
            "the new profile, monthly recap, and betting-profile commands."
        ),
        inline=False,
    )
    embed.set_footer(text="Ranked Solo/Duo only")
    return embed


def build_curated_v21_embed(version: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"Sisyphus {version} is live",
        description=(
            "Sisyphus now reports its own health, so you can tell the difference "
            "between a quiet ranked night and a service problem."
        ),
        color=0x57F287,
        timestamp=now_ist(),
    )
    embed.add_field(
        name="Public Status Page",
        value=(
            "A dedicated uptime page now tracks bot availability, Discord connectivity, "
            "ranked polling, Riot live detection, OP.GG match data, and points markets."
        ),
        inline=False,
    )
    embed.add_field(
        name="Status In Discord",
        value=(
            "Use `/status` or `!status` for the current version, process uptime, "
            "latest ranked poll, component health, and the public status-page link."
        ),
        inline=False,
    )
    embed.add_field(
        name="Independent Monitoring",
        value=(
            "The status monitor runs away from the bot itself, so crashes and missed "
            "heartbeats can be recorded even when Sisyphus cannot answer in Discord."
        ),
        inline=False,
    )
    embed.set_footer(text="Ranked Solo/Duo only")
    return embed


def build_curated_v216_embed(version: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"Sisyphus {version} is live",
        description=(
            "Sisyphus is now running as one clearly identified Mac service, "
            "with a private analytics dashboard for the server."
        ),
        color=0x57F287,
        timestamp=now_ist(),
    )
    embed.add_field(
        name="One Bot, One Reply",
        value=(
            "The retired Oracle bot no longer connects to Discord. A Mac process lock "
            "also prevents accidental second launches, while event deduplication remains enabled."
        ),
        inline=False,
    )
    embed.add_field(
        name="Profiles Corrected",
        value=(
            "Current rank now controls the profile badge and accent. Peak LP includes the "
            "current rank, even when older history has not caught up."
        ),
        inline=False,
    )
    embed.add_field(
        name="Authenticated Dashboard",
        value=(
            "Use `/dashboard` or `!dashboard` to open the Discord-member-only analytics site "
            "for player journeys, betting, and community history."
        ),
        inline=False,
    )
    embed.add_field(
        name="Reliability",
        value=(
            "Startup logs now identify the version, host, and PID. The Mac publishes a sanitized "
            "dashboard export without raw Discord identifiers or private bot state."
        ),
        inline=False,
    )
    embed.set_footer(text="Ranked Solo/Duo only")
    return embed


def build_curated_v2111_embed(version: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"Sisyphus {version} is live",
        description="A private champion roll is ready whenever the squad needs a fresh queue idea.",
        color=0xA0283B,
        timestamp=now_ist(),
    )
    embed.add_field(
        name="Champion Rolls",
        value=(
            "Use `/role top`, `/role mid`, `/role jgl`, `/role adc`, or `/role supp` "
            "to get a random champion for that lane. `/role wild` can choose any champion."
        ),
        inline=False,
    )
    embed.add_field(
        name="Private Loadouts",
        value=(
            "Sisyphus sends the result in a DM with current OP.GG runes, summoner spells, "
            "item path, and skill order, so the channel stays clear."
        ),
        inline=False,
    )
    embed.add_field(
        name="Complete Role Pools",
        value=(
            "Lane rolls use a verified snapshot that covers every currently available League champion, "
            "including off-meta picks."
        ),
        inline=False,
    )
    embed.set_footer(text="Ranked Solo/Duo only")
    return embed


def changelog_content_is_safe(content: str) -> bool:
    lowered = content.lower()
    if any(phrase in lowered for phrase in BLOCKED_CHANGELOG_PHRASES):
        return False
    mentioned_commands = set(re.findall(r"/([a-z][a-z0-9_-]*)", lowered))
    return mentioned_commands <= ALLOWED_SLASH_COMMANDS


def load_release_notes(version: str) -> dict | None:
    """Load reviewed notes. Missing notes never trigger generated announcements."""
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version):
        raise ValueError("Release version must use vX.Y.Z.")
    path = RELEASE_NOTES_DIR / f"{version}.json"
    if not path.exists():
        return None
    notes = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(notes, dict) or notes.get("version") != version:
        raise ValueError(f"Release notes do not match {version}.")
    if notes.get("author_model") != "gpt-6-luna":
        raise ValueError("Release notes must record GPT-6 Luna as their author.")
    body = notes.get("body")
    if not isinstance(body, str) or not body.strip() or len(body) > 800:
        raise ValueError("Release notes need a body of 1 to 800 characters.")
    if not changelog_content_is_safe(body):
        raise ValueError("Release notes contain unsupported commands or claims.")
    covers = notes.get("covers", [version])
    if (not isinstance(covers, list) or version not in covers
            or any(not isinstance(v, str) or not re.fullmatch(r"v\d+\.\d+\.\d+", v) for v in covers)
            or len(covers) != len(set(covers))):
        raise ValueError("Release notes need a unique list of covered versions.")
    notes["covers"] = covers
    return notes


def release_is_processed(version: str, state: dict) -> bool:
    return state.get("releases", {}).get(version, {}).get("status") in {
        "published", "skipped", "superseded",
    }


def mark_release_decision(version: str, sha: str, status: str, *, message=None, covers=()):
    from .state import data, save_data

    state = changelog_state()
    receipt = state["releases"].setdefault(version, {})
    receipt.update(status=status, sha=sha, decided_at=now_ist().isoformat())
    if message is not None:
        receipt.update(message_id=message.id, channel_id=message.channel.id,
                       message_url=message.jump_url)
    state["last_processed_version"] = version
    state["last_processed_sha"] = sha
    if version == "v2.0.0":
        state["v2_curated_processed_sha"] = sha
    if version in CURATED_RELEASE_VERSIONS:
        state["curated_processed_versions"][version] = sha
    if status == "published":
        for covered in covers:
            if covered != version and not release_is_processed(covered, state):
                state["releases"][covered] = {"status": "superseded", "by": version}
    save_data(data)


async def publish_release(bot: discord.Client, embed: discord.Embed, version: str,
                          sha: str, *, covers=()) -> str:
    """Publish once, with a saved receipt and recovery for uncertain sends."""
    from .state import data, save_data

    async with _publish_lock:
        state = changelog_state()
        receipt = state["releases"].setdefault(version, {})
        if release_is_processed(version, state):
            if receipt.get("status") == "published":
                return receipt.get("message_url", "already published")
            raise ValueError(f"{version} was already {receipt['status']}.")
        channel = bot.get_channel(DESTINATION_ID) or await bot.fetch_channel(DESTINATION_ID)
        if receipt.get("channel_id") and receipt["channel_id"] != channel.id:
            raise ValueError("Pending release destination changed. Recover it in its original channel first.")
        digest = hashlib.sha256(json.dumps({"title": embed.title, "description": embed.description,
                                          "footer": embed.footer.text,
                                          "fields": embed.to_dict().get("fields", [])},
                                         sort_keys=True).encode()).hexdigest()
        if receipt.get("notes_hash") and receipt["notes_hash"] != digest:
            raise ValueError("Pending release text changed. Resolve its saved delivery before replacing it.")
        if receipt.get("started_at"):
            # Scan the entire delivery interval so recovery survives the nonce window.
            since = datetime.fromisoformat(receipt["started_at"]) - timedelta(seconds=60)
            async for message in channel.history(limit=None, after=since):
                if bot.user and message.author.id == bot.user.id:
                    if any(e.title == embed.title and e.footer.text == embed.footer.text
                           for e in message.embeds):
                        mark_release_decision(version, sha, "published", message=message, covers=covers)
                        return message.jump_url
        receipt.update(status="pending", notes_hash=digest, channel_id=channel.id,
                       started_at=receipt.get("started_at") or datetime.now(timezone.utc).isoformat())
        save_data(data)
        nonce = hashlib.sha256(f"release:{channel.id}:{version}".encode()).hexdigest()[:24]
        try:
            message = await channel.send(embed=embed, nonce=nonce,
                                         allowed_mentions=discord.AllowedMentions.none())
        except Exception as exc:
            receipt["last_error"] = type(exc).__name__
            save_data(data)
            raise
        mark_release_decision(version, sha, "published", message=message, covers=covers)
        return message.jump_url


def build_release_embed(
    version: str, sha: str, changelog_content: str, commit_count: int
) -> discord.Embed:
    if version == "v2.0.0":
        return build_curated_v2_embed(version)
    if version == "v2.1.0":
        return build_curated_v21_embed(version)
    if version == "v2.1.6":
        return build_curated_v216_embed(version)
    if version == "v2.1.11":
        return build_curated_v2111_embed(version)

    notes = load_release_notes(version)
    embed = discord.Embed(
        title=f"Sisyphus {version} is live",
        color=0x57F287,
        timestamp=now_ist(),
    )
    embed.description = truncate_text(
        notes["body"] if notes else changelog_content,
        MAX_EMBED_DESCRIPTION_CHARS,
        "\n\n... trimmed to fit Discord.",
    )
    covered = [v for v in notes["covers"] if v != version] if notes else []
    suffix = " · Includes " + ", ".join(covered) if covered else ""
    embed.set_footer(text=f"Release {version}{suffix}")
    return embed


class ChangelogPublishView(discord.ui.View):
    def __init__(self, bot: discord.Client, embed: discord.Embed, version: str, sha: str, *, covers=()):
        super().__init__(timeout=1800)
        self.bot, self.embed, self.version, self.sha = bot, embed, version, sha
        self.covers = covers
        self.message: discord.Message | None = None
        self.done_event = asyncio.Event()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == DEVELOPER_DISCORD_ID:
            return True
        await interaction.response.send_message("Only the release owner can decide.", ephemeral=True)
        return False

    async def _disable_all(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Publish Changelog", style=discord.ButtonStyle.success)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            url = await publish_release(self.bot, self.embed, self.version, self.sha, covers=self.covers)
        except Exception as exc:
            await interaction.followup.send(f"Publication failed ({type(exc).__name__}). You can retry.", ephemeral=True)
            return
        self.stop()
        await self._disable_all()
        self.done_event.set()
        await interaction.followup.send(f"Published `{self.version}`: {url}", ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        async with _publish_lock:
            receipt = changelog_state()["releases"].get(self.version, {})
            if receipt.get("status") == "pending":
                await interaction.followup.send("Delivery is unresolved. Retry Publish to recover it before skipping.", ephemeral=True)
                return
            if not release_is_processed(self.version, changelog_state()):
                mark_release_decision(self.version, self.sha, "skipped")
        self.stop()
        await self._disable_all()
        self.done_event.set()
        await interaction.followup.send(f"Closed changelog review for `{self.version}`.", ephemeral=True)

    async def on_timeout(self):
        # An expired review is not a decision. A later startup can offer it again.
        await self._disable_all()
        self.done_event.set()


async def prompt_changelog_on_startup(bot: discord.Client):
    """Offer reviewed release notes once per version, without an AI API call."""
    state = changelog_state()
    version, head_sha = get_git_version(), get_git_sha()
    if release_is_processed(version, state):
        print(f"[changelog] {version} already processed. Skipping prompt.")
        return
    try:
        notes = load_release_notes(version)
    except (ValueError, OSError) as exc:
        print(f"[changelog] Cannot load reviewed notes: {exc}")
        return
    if notes is None:
        if version not in CURATED_RELEASE_VERSIONS:
            print(f"[changelog] No reviewed notes for {version}. Skipping announcement.")
            return
        if not needs_curated_prompt(version, head_sha, state):
            return
    if not DEVELOPER_DISCORD_ID:
        print("[changelog] Release review disabled: no release owner configured.")
        return
    embed = build_release_embed(version, head_sha, notes["body"] if notes else "", 0)
    try:
        developer = await bot.fetch_user(DEVELOPER_DISCORD_ID)
        view = ChangelogPublishView(bot, embed, version, head_sha,
                                    covers=notes["covers"] if notes else [version])
        view.message = await developer.send(
            content=f"**Sisyphus startup check**\nPublish reviewed release notes for `{version}`?",
            embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none(),
        )
        print(f"[changelog] Reviewed notes sent to owner for {version}.")
        await view.done_event.wait()
    except Exception as exc:
        print(f"[changelog] Failed to send release review: {type(exc).__name__}")
