"""Startup release changelog prompt."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import aiohttp
import discord

from .config import (
    APP_VERSION,
    DEVELOPER_DISCORD_ID,
    DESTINATION_ID,
    MISTRAL_API_KEY,
    MISTRAL_MODEL,
    MISTRAL_TIMEOUT_SECONDS,
)
from .utils import now_ist

MAX_EMBED_DESCRIPTION_CHARS = 3600
MAX_RAW_LOG_CHARS = 8000
PROJECT_ROOT = Path(__file__).resolve().parents[1]
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
    "rivalry",
    "settlebet",
    "squadgoal",
    "stats",
    "status",
    "track",
    "unlink",
    "untrack",
    "voidbet",
    "wallet",
    "weeklyrecap",
    "whoami",
}

CURATED_RELEASE_VERSIONS = {"v2.0.0", "v2.1.0", "v2.1.6"}


def run_git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT).decode().strip()


def get_git_sha(ref: str = "HEAD") -> str:
    if not DEVELOPER_DISCORD_ID:
        print("[changelog] Developer changelog prompt is disabled: DEVELOPER_DISCORD_ID is not configured.")
        return

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


def get_commit_messages_since(last_sha: str | None) -> tuple[str, int]:
    """Return raw commit messages since the last processed SHA."""
    try:
        head = get_git_sha("HEAD")
        if last_sha and last_sha != "unknown":
            try:
                base_check = run_git(["merge-base", "--is-ancestor", last_sha, "HEAD"])
            except subprocess.CalledProcessError:
                base_check = ""
            except Exception:
                base_check = ""
            if base_check == "":
                rev_range = f"{last_sha}..HEAD"
                count = int(run_git(["rev-list", "--count", rev_range]) or "0")
                if count <= 0:
                    return "", 0
                log = run_git(
                    [
                        "log",
                        rev_range,
                        "--reverse",
                        "--pretty=format:%h %s%n%b%n---",
                    ]
                )
                return truncate_text(log, MAX_RAW_LOG_CHARS), count

        count = min(int(run_git(["rev-list", "--count", "HEAD"]) or "0"), 20)
        log = run_git(
            ["log", f"-{count}", "--reverse", "--pretty=format:%h %s%n%b%n---"]
        )
        return truncate_text(log, MAX_RAW_LOG_CHARS), count
    except Exception as exc:
        print(f"[changelog] Failed to get commit messages: {exc}")
        return "chore: general updates and minor bug fixes", 1


def fallback_changelog(raw_log: str) -> str:
    hidden_prefixes = (
        "chore:",
        "refactor:",
        "test:",
        "ci:",
        "docs:",
        "style:",
    )
    lines = []
    for raw in raw_log.splitlines():
        line = raw.strip()
        if not line or line == "---":
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2 and len(parts[0]) <= 12:
            line = parts[1]
        if line.lower().startswith(hidden_prefixes):
            continue
        if "bump version" in line.lower():
            continue
        line = (
            line.replace("feat:", "")
            .replace("fix:", "")
            .replace("perf:", "")
            .strip()
            .capitalize()
        )
        if len(line) > 140:
            line = line[:137].rstrip() + "..."
        lines.append(f"• {line}")
        if len(lines) >= 8:
            break
    if not lines:
        lines = ["• Cleaner recaps, smoother commands, and small quality-of-life fixes."]
    return "\n".join(lines)


def changelog_content_is_safe(content: str) -> bool:
    import re

    lowered = content.lower()
    if any(phrase in lowered for phrase in BLOCKED_CHANGELOG_PHRASES):
        return False
    mentioned_commands = set(re.findall(r"/([a-z][a-z0-9_-]*)", lowered))
    return mentioned_commands <= ALLOWED_SLASH_COMMANDS


async def generate_changelog_from_commit(raw_log: str) -> str:
    """Use Mistral to convert raw git messages into a Discord changelog."""
    if not MISTRAL_API_KEY:
        print("[changelog] Missing MISTRAL_API_KEY, using local fallback.")
        return fallback_changelog(raw_log)

    url = "https://api.mistral.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MISTRAL_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Write a Discord server announcement for a League of Legends friend-group bot. "
                    "Audience: normal Discord server members, not developers. "
                    "Do not mention commits, revisions, SHAs, code internals, APIs, config, or implementation details. "
                    "Do not say 'git' or 'developer'. "
                    "Use short friendly sections with bold headings and bullets. "
                    "Only describe features explicitly present in the provided notes. Do not invent filters, roles, queue types, other squads, real-time dashboards, apps, or moderation features. "
                    "Focus on what members can use: community features, better recaps, betting/points, help commands, reliability. "
                    "Keep it under 800 characters. No code fences, no meta commentary."
                ),
            },
            {"role": "user", "content": raw_log},
        ],
        "temperature": 0.3,
        "max_tokens": 700,
    }

    try:
        timeout = aiohttp.ClientTimeout(total=MISTRAL_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                text = await resp.text()
                if resp.status != 200:
                    print(f"[changelog] Mistral API status {resp.status}: {text[:500]}")
                    return fallback_changelog(raw_log)
                body = await resp.json()
                choices = body.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "").strip()
                    if content:
                        if not changelog_content_is_safe(content):
                            print("[changelog] Mistral changelog invented unsupported details; using fallback.")
                            return fallback_changelog(raw_log)
                        return content
    except Exception as exc:
        print(f"[changelog] Mistral AI generation error: {exc}")

    return fallback_changelog(raw_log)


def build_release_embed(
    version: str, sha: str, changelog_content: str, commit_count: int
) -> discord.Embed:
    if version == "v2.0.0":
        return build_curated_v2_embed(version)
    if version == "v2.1.0":
        return build_curated_v21_embed(version)
    if version == "v2.1.6":
        return build_curated_v216_embed(version)

    embed = discord.Embed(
        title=f"Sisyphus {version} is live",
        color=0x57F287,
        timestamp=now_ist(),
    )
    intro = (
        "The boulder got a proper community upgrade. Here is what changed for the server:\n\n"
    )
    embed.description = truncate_text(
        intro + changelog_content,
        MAX_EMBED_DESCRIPTION_CHARS,
        "\n\n... trimmed to fit Discord.",
    )
    embed.set_footer(text="Ranked Solo/Duo only")
    return embed


class ChangelogPublishView(discord.ui.View):
    def __init__(self, bot: discord.Client, embed: discord.Embed, version: str, sha: str):
        super().__init__(timeout=1800)
        self.bot = bot
        self.embed = embed
        self.version = version
        self.sha = sha
        self.message: discord.Message | None = None
        self.done_event = asyncio.Event()

    async def _disable_all(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message:
            await self.message.edit(view=self)

    def _mark_processed(self):
        from .state import data, save_data

        state = changelog_state()
        state["last_processed_version"] = self.version
        state["last_processed_sha"] = self.sha
        if self.version == "v2.0.0":
            state["v2_curated_processed_sha"] = self.sha
        if self.version in CURATED_RELEASE_VERSIONS:
            state.setdefault("curated_processed_versions", {})[self.version] = self.sha
        save_data(data)

    @discord.ui.button(label="Publish Changelog", style=discord.ButtonStyle.success)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self._disable_all()

        channel = self.bot.get_channel(DESTINATION_ID)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(DESTINATION_ID)
            except Exception:
                channel = None

        if channel:
            try:
                await channel.send(embed=self.embed)
                await interaction.followup.send(
                    f"Published `{self.version}` changelog to <#{DESTINATION_ID}>.",
                    ephemeral=True,
                )
            except Exception as exc:
                await interaction.followup.send(f"Failed to publish: {exc}", ephemeral=True)
        else:
            await interaction.followup.send("Could not resolve the target channel/thread.", ephemeral=True)

        self._mark_processed()
        self.done_event.set()

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self._disable_all()
        await interaction.followup.send(f"Skipped changelog for `{self.version}`.", ephemeral=True)
        self._mark_processed()
        self.done_event.set()

    async def on_timeout(self):
        self._mark_processed()
        await self._disable_all()
        self.done_event.set()


async def prompt_changelog_on_startup(bot: discord.Client):
    """Prompt the developer to publish a release changelog once per HEAD SHA."""
    print("[changelog] Running startup changelog prompt check...")
    state = changelog_state()
    version = get_git_version()
    head_sha = get_git_sha("HEAD")
    curated_prompt_needed = needs_curated_prompt(version, head_sha, state)

    if version in CURATED_RELEASE_VERSIONS and not curated_prompt_needed:
        if (
            state.get("last_processed_version") != version
            or state.get("last_processed_sha") != head_sha
        ):
            state["last_processed_version"] = version
            state["last_processed_sha"] = head_sha
            from .state import data, save_data

            save_data(data)
        print(f"[changelog] {version} already processed. Skipping prompt.")
        return

    if (
        state.get("last_processed_version") == version
        and state.get("last_processed_sha") == head_sha
        and not curated_prompt_needed
    ):
        print(f"[changelog] {version} @ {head_sha} already processed. Skipping prompt.")
        return

    raw_log, commit_count = get_commit_messages_since(state.get("last_processed_sha"))
    if curated_prompt_needed:
        raw_log = f"curated {version} release"
        commit_count = max(commit_count, 1)
    if commit_count <= 0:
        print("[changelog] No new commits detected. Marking current HEAD processed.")
        state["last_processed_version"] = version
        state["last_processed_sha"] = head_sha
        from .state import data, save_data

        save_data(data)
        return

    print(
        f"[changelog] Preparing {version} changelog from {commit_count} commits "
        f"({state.get('last_processed_sha') or 'initial'} -> {head_sha})."
    )
    changelog_content = await generate_changelog_from_commit(raw_log)
    embed = build_release_embed(version, head_sha, changelog_content, commit_count)

    try:
        developer = await bot.fetch_user(DEVELOPER_DISCORD_ID)
        view = ChangelogPublishView(bot, embed, version, head_sha)
        msg = await developer.send(
            content=(
                f"**Sisyphus startup check**\n"
                f"Publish release changelog for `{version}`?"
            ),
            embed=embed,
            view=view,
        )
        view.message = msg
        print(f"[changelog] Prompt sent to developer DM for {version} @ {head_sha}.")
        await view.done_event.wait()
        print("[changelog] Decision resolved, resuming bot startup.")
    except Exception as exc:
        print(f"[changelog] Failed to send developer startup DM prompt: {exc}")
