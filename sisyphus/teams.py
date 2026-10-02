"""/teams: balance custom-game teams from tracked ranks or typed-in ranks."""
from __future__ import annotations

import re

import discord

from .balance import (
    APEX_BASE,
    ROLE_ALIASES,
    ROLE_LABELS,
    Player,
    Split,
    balance,
    parse_rank,
    parse_roles,
)
from .ranks import format_total_lp, tier_emoji, tier_for_total_lp
from .state import data
from .utils import now_ist

MENTION_RE = re.compile(r"^<@!?(\d+)>$")
MAX_LABEL = 32  # display cap for names, keeps embed fields far below Discord limits


def _stored_rating(riot_id: str) -> int | None:
    entry = data.get("tracked", {}).get(riot_id, {})
    try:
        total = int(entry.get("last_known_lp"))
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    if total >= APEX_BASE:
        # Apex totals may carry a legacy phantom division; raw LP is the truth.
        try:
            return APEX_BASE + max(0, int(entry.get("last_known_raw_lp")))
        except (TypeError, ValueError):
            legacy = 300 if str(entry.get("last_known_rank")) == "1" else 0
            return max(APEX_BASE, total - legacy)
    return total


def _parse_entry(entry: str) -> tuple[str, str | None, tuple[str, ...]]:
    """'target[=rank][:role,role]' -> (target, rank_text, roles)."""
    roles: tuple[str, ...] = ()
    if ":" in entry:
        entry, role_text = entry.rsplit(":", 1)
        roles = parse_roles(role_text)
    rank_text = None
    if "=" in entry:
        entry, rank_text = entry.split("=", 1)
        rank_text = rank_text.strip()
    return entry.strip(), rank_text, roles


def _resolve(entry: str) -> tuple[Player, str]:
    """Return the player plus an identity key used to catch duplicates."""
    target, rank_text, roles = _parse_entry(entry)
    if not target:
        raise ValueError("Found an empty player entry.")
    tracked = data.get("tracked", {})
    key = None
    label = target

    mention = MENTION_RE.match(target)
    if mention:
        user_id = mention.group(1)
        label = f"<@{user_id}>"
        linked = data.get("links", {}).get(user_id)
        key = linked if linked in tracked else None
    elif target in tracked:
        key = target
        label = target.split("#", 1)[0]

    if rank_text is not None:
        if not rank_text:
            raise ValueError(f"{label} is missing a rank after `=`. Try `{target}=g2`.")
        rating = parse_rank(rank_text)
    elif key:
        rating = _stored_rating(key)
        if rating is None:
            raise ValueError(f"{label} has no recorded rank yet. Add one, e.g. `{target}=g2`.")
    else:
        raise ValueError(
            f"{label} isn't tracked or linked. Add a rank, e.g. `{target}=g2`."
        )
    identity = key.lower() if key else label.lower()
    return Player(label=label, rating=rating, prefs=roles), identity


def _is_role_list(piece: str) -> bool:
    if "=" in piece or ":" in piece:
        return False
    words = [w for w in re.split(r"[/\s]+", piece.lower()) if w]
    return bool(words) and all(w in ROLE_ALIASES for w in words)


def _split_entries(raw: str) -> list[str]:
    """Split on newlines, semicolons and commas, keeping `:mid,top` role lists together."""
    entries: list[str] = []
    for chunk in re.split(r"[\n;]+", raw):
        current: str | None = None
        for piece in chunk.split(","):
            piece = piece.strip()
            if not piece:
                continue
            if current is not None and ":" in current and _is_role_list(piece):
                current += "," + piece  # continues the previous player's roles
                continue
            if current is not None:
                entries.append(current)
            current = piece
        if current is not None:
            entries.append(current)
    return entries


def build_roster(raw: str) -> list[Player]:
    entries = _split_entries(raw)
    if not entries:
        raise ValueError("List the players, separated by commas.")
    players: list[Player] = []
    seen: set[str] = set()
    for entry in entries:
        player, identity = _resolve(entry)
        if identity in seen:
            raise ValueError(f"{player.label} is listed more than once.")
        seen.add(identity)
        players.append(player)
    return players


def _rank_text(rating: int) -> str:
    tier = tier_for_total_lp(rating)
    if rating >= APEX_BASE:
        # Ratings here are on the raw scale (APEX_BASE + LP), so no legacy offset.
        text = f"{tier.title()} ({rating - APEX_BASE} LP)"
    else:
        text = format_total_lp(rating).split(" \u2014 ")[0].title()
    return f"{tier_emoji(tier)} {text}"


def _display_label(label: str) -> str:
    if MENTION_RE.match(label) or len(label) <= MAX_LABEL:
        return label  # mentions are never cut in half
    return label[: MAX_LABEL - 1].rstrip() + "\u2026"


def _team_lines(slots) -> str:
    lines = []
    for slot in slots:
        role = f"**{ROLE_LABELS[slot.role]}** " if slot.role else ""
        label = _display_label(slot.player.label)
        lines.append(f"{role}{label} ({_rank_text(slot.player.rating)})")
    return "\n".join(lines)


def _team_avg(slots) -> int:
    return round(sum(s.player.rating for s in slots) / len(slots))


class TeamsView(discord.ui.View):
    def __init__(self, author_id: int, splits: list[Split]):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.splits = splits
        self.index = 0
        self.flipped = False
        self.message: discord.Message | None = None
        if len(splits) < 2:
            self.reroll.disabled = True

    def current_embed(self) -> discord.Embed:
        split = self.splits[self.index]
        blue, red = (split.team_b, split.team_a) if self.flipped else (split.team_a, split.team_b)
        embed = discord.Embed(
            title="Custom Game Teams",
            color=0x5865F2,
            timestamp=now_ist(),
        )
        gave_roles = any(s.player.prefs for s in (*split.team_a, *split.team_b))
        if gave_roles and not split.roles_assigned:
            embed.description = "Role preferences were ignored: roles only apply to 5v5."
        embed.add_field(
            name=f"\U0001F7E6 Blue (avg {_rank_text(_team_avg(blue))})",
            value=_team_lines(blue),
            inline=True,
        )
        embed.add_field(
            name=f"\U0001F7E5 Red (avg {_rank_text(_team_avg(red))})",
            value=_team_lines(red),
            inline=True,
        )
        per_player = split.gap / len(split.team_a)
        embed.set_footer(
            text=f"Option {self.index + 1}/{len(self.splits)} | avg rating gap {per_player:.0f} LP per player"
        )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who ran `/teams` can change these.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Reroll", emoji="\U0001F3B2", style=discord.ButtonStyle.primary)
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = (self.index + 1) % len(self.splits)
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @discord.ui.button(label="Swap sides", emoji="\U0001F501", style=discord.ButtonStyle.secondary)
    async def swap(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.flipped = not self.flipped
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException as exc:
                print(f"[teams] timeout edit failed: {exc}")


def make_teams_message(author_id: int, raw: str) -> TeamsView:
    """Parse, balance, and return a ready-to-send view. Raises ValueError for bad input."""
    splits = balance(build_roster(raw))
    return TeamsView(author_id, splits)