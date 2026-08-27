"""Sisyphus-scoped player profiles, memories, and milestones."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

import discord

from .community import ensure_community, parse_lp_delta, player_label
from .outcome import current_streak, outcome_icon
from .ranks import TIER_BY_INDEX, TIER_COLOR, format_total_lp, tier_image_url
from .state import data, save_data
from .utils import now_ist, parse_iso_datetime

ACCENT = 0x5865F2
GOOD = 0x57F287
GOLD = 0xFEE75C
SOFT = 0x99AAB5


def _rows(riot_id: str) -> list[dict]:
    return list(data.get("history", {}).get(riot_id, []))


def _decisive(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row.get("result") in {"WIN", "LOSS"}]


def _date_value(row: dict) -> date | None:
    raw = row.get("date")
    if not raw:
        parsed = parse_iso_datetime(row.get("recorded_at"))
        return parsed.date() if parsed else None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def _first_date(rows: list[dict]) -> str:
    days = [day for day in (_date_value(row) for row in rows) if day]
    return min(days).strftime("%b %-d, %Y") if days else "Not enough history yet"


def _avg(rows: list[dict], key: str) -> float | None:
    values = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]
    if not values:
        return None
    return sum(values) / len(values)


def _monthly_lp(rows: list[dict]) -> dict[str, int]:
    months: dict[str, int] = defaultdict(int)
    for row in rows:
        day = _date_value(row)
        if not day:
            continue
        months[day.strftime("%Y-%m")] += parse_lp_delta(row)
    return dict(months)


def _longest_win_streak(rows: list[dict]) -> int:
    best = streak = 0
    for row in rows:
        if row.get("result") == "WIN":
            streak += 1
            best = max(best, streak)
        elif row.get("result") == "LOSS":
            streak = 0
    return best


def _champ_stats(rows: list[dict]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for row in _decisive(rows):
        champ = row.get("champion")
        if not champ:
            continue
        entry = stats.setdefault(champ, {"games": 0, "wins": 0})
        entry["games"] += 1
        if row.get("result") == "WIN":
            entry["wins"] += 1
    return stats


def _role_stats(rows: list[dict]) -> Counter:
    return Counter(row.get("position") for row in rows if row.get("position"))


def _server_record_match_ids() -> dict[str, str]:
    records = ensure_community().setdefault("records", {})
    return {
        str(record.get("match_id")): record.get("label", key)
        for key, record in records.items()
        if record.get("match_id")
    }


def _as_lp(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _historical_lp(rows: list[dict]) -> list[int]:
    return [
        lp
        for row in rows
        if (lp := _as_lp(row.get("lp_total"))) is not None
    ]


def _current_lp(riot_id: str) -> int | None:
    return _as_lp(data.get("tracked", {}).get(riot_id, {}).get("last_known_lp"))


def _badge_lp(riot_id: str, rows: list[dict] | None = None) -> int | None:
    """Use the current rank for presentation, falling back to history if absent."""
    rows = _rows(riot_id) if rows is None else rows
    current = _current_lp(riot_id)
    if current is not None:
        return current
    return max(_historical_lp(rows), default=None)


def _profile_color(riot_id: str) -> int:
    badge_lp = _badge_lp(riot_id)
    if badge_lp is None:
        return ACCENT
    tier = TIER_BY_INDEX.get(max(0, badge_lp) // 400, "UNRANKED")
    return TIER_COLOR.get(tier, ACCENT)


def _memory_bucket(riot_id: str) -> dict:
    community = ensure_community()
    return community.setdefault("memories", {}).setdefault(riot_id, {})


def player_memories(riot_id: str) -> list[dict]:
    memories = list(_memory_bucket(riot_id).values())
    return sorted(memories, key=lambda item: item.get("created_at", ""), reverse=True)


def _linked_owner_id(riot_id: str) -> int | None:
    for user_id, linked in data.get("links", {}).items():
        if linked == riot_id and str(user_id).isdigit():
            return int(user_id)
    return None


def user_owns_player(user_id: int, riot_id: str) -> bool:
    return data.get("links", {}).get(str(user_id)) == riot_id


def _row_for_match(riot_id: str, match_id: str) -> dict | None:
    for row in _rows(riot_id):
        if str(row.get("match_id")) == str(match_id):
            return row
    return None


def save_memory(
    riot_id: str,
    match_id: str,
    name: str,
    user_id: int,
    *,
    reason: str | None = None,
    recap_url: str | None = None,
) -> tuple[bool, str]:
    if not user_owns_player(user_id, riot_id):
        return False, "Only the linked player can save this match as a memory."
    row = _row_for_match(riot_id, match_id)
    if not row:
        return False, "That match is not in Sisyphus history yet."
    memories = _memory_bucket(riot_id)
    if str(match_id) in memories:
        return False, "You already remembered this match."
    clean_name = " ".join(str(name or "").split())[:80]
    if not clean_name:
        return False, "Memory name cannot be empty."
    memories[str(match_id)] = {
        "player": riot_id,
        "owner_id": str(user_id),
        "match_id": str(match_id),
        "name": clean_name,
        "date": row.get("date"),
        "champion": row.get("champion"),
        "role": row.get("position"),
        "result": row.get("result"),
        "lp_change": row.get("lp_change"),
        "lp_before": row.get("lp_before"),
        "lp_total": row.get("lp_total"),
        "kills": row.get("kills"),
        "deaths": row.get("deaths"),
        "assists": row.get("assists"),
        "duration": row.get("duration"),
        "reason": reason,
        "recap_url": recap_url,
        "created_at": now_ist().isoformat(),
    }
    save_data(data)
    return True, f"Remembered **{clean_name}**."


def _add_milestone(events: list[dict], seen: set[str], key: str, label: str, row: dict | None) -> None:
    if key in seen:
        return
    events.append(
        {
            "key": key,
            "label": label,
            "date": (row or {}).get("date") or now_ist().date().isoformat(),
            "match_id": (row or {}).get("match_id"),
            "created_at": now_ist().isoformat(),
        }
    )
    seen.add(key)


def ensure_player_milestones(riot_id: str) -> list[dict]:
    community = ensure_community()
    bucket = community.setdefault("milestones", {}).setdefault(riot_id, [])
    seen = {event.get("key") for event in bucket}
    rows = _rows(riot_id)
    if not rows:
        return bucket

    _add_milestone(bucket, seen, "first_game", "First game witnessed", rows[0])
    for threshold in (50, 100, 250, 500):
        if len(rows) >= threshold:
            _add_milestone(
                bucket,
                seen,
                f"games_{threshold}",
                f"{threshold} games tracked",
                rows[threshold - 1],
            )
    wins = 0
    champ_counts: Counter = Counter()
    best_lp = None
    streak = 0
    for row in rows:
        champ = row.get("champion")
        if champ:
            champ_counts[champ] += 1
            for threshold in (10, 25, 50, 100):
                if champ_counts[champ] == threshold:
                    _add_milestone(
                        bucket,
                        seen,
                        f"champ_{champ}_{threshold}",
                        f"{threshold} {champ} games",
                        row,
                    )
        if row.get("result") == "WIN":
            wins += 1
            streak += 1
            for threshold in (50, 100, 250):
                if wins == threshold:
                    _add_milestone(
                        bucket,
                        seen,
                        f"wins_{threshold}",
                        f"{threshold} wins witnessed",
                        row,
                    )
            for threshold in (3, 5, 7, 10):
                if streak == threshold:
                    _add_milestone(
                        bucket,
                        seen,
                        f"win_streak_{threshold}_{row.get('match_id')}",
                        f"{threshold}-game win streak",
                        row,
                    )
        elif row.get("result") == "LOSS":
            streak = 0
        lp_total = row.get("lp_total")
        if isinstance(lp_total, int) and (best_lp is None or lp_total > best_lp):
            best_lp = lp_total
            _add_milestone(
                bucket,
                seen,
                f"peak_{lp_total}",
                f"New peak: {format_total_lp(lp_total)}",
                row,
            )
    save_data(data)
    return sorted(bucket, key=lambda event: event.get("date") or "")


def recap_headline(riot_id: str, row: dict) -> str:
    name = player_label(riot_id)
    result = row.get("result")
    champion = row.get("champion") or "their champion"
    lp_delta = parse_lp_delta(row)
    kda = ""
    if row.get("kills") is not None and row.get("deaths") is not None:
        kda = f" with {row.get('kills', 0)}/{row.get('deaths', 0)}/{row.get('assists', 0)}"
    if result == "WIN":
        if lp_delta > 0:
            return f"Clean win: {name} gained +{lp_delta} LP on {champion}{kda}."
        return f"{name} locked in a ranked win on {champion}{kda}."
    if result == "LOSS":
        if row.get("damage_share", 0) >= 30:
            return f"Rough loss, but {name} carried {row.get('damage_share'):.0f}% of team damage on {champion}."
        return f"{name}'s {champion} game ends in a loss{f' ({lp_delta} LP)' if lp_delta else ''}."
    return f"Short game recorded as a remake/draw for {name} on {champion}."


class MemoryNameModal(discord.ui.Modal):
    def __init__(self, riot_id: str, match_id: str, reason: str | None = None, recap_url: str | None = None):
        super().__init__(title="Remember This Match")
        self.riot_id = riot_id
        self.match_id = match_id
        self.reason = reason
        self.recap_url = recap_url
        self.name_input = discord.ui.TextInput(
            label="Memory name",
            placeholder="The 2 AM Jhin Incident",
            max_length=80,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        ok, message = save_memory(
            self.riot_id,
            self.match_id,
            str(self.name_input.value),
            interaction.user.id,
            reason=self.reason,
            recap_url=self.recap_url,
        )
        await interaction.response.send_message(
            ("✅ " if ok else "❌ ") + message,
            ephemeral=True,
        )


class PlayerProfileView(discord.ui.View):
    def __init__(self, riot_id: str):
        super().__init__(timeout=240)
        self.riot_id = riot_id
        self.message = None

    def _base(self, title: str, color: int | None = None) -> discord.Embed:
        badge_lp = _badge_lp(self.riot_id)
        e = discord.Embed(
            title=title,
            color=color or _profile_color(self.riot_id),
            timestamp=now_ist(),
        )
        e.set_author(
            name=self.riot_id,
            icon_url=tier_image_url((badge_lp or 0) // 400),
        )
        e.set_footer(text="Sisyphus-observed ranked Solo/Duo history only")
        return e

    def overview_embed(self) -> discord.Embed:
        rows = _rows(self.riot_id)
        decisive = _decisive(rows)
        wins = sum(1 for row in rows if row.get("result") == "WIN")
        losses = sum(1 for row in rows if row.get("result") == "LOSS")
        draws = sum(1 for row in rows if row.get("result") == "DRAW")
        net_lp = sum(parse_lp_delta(row) for row in rows)
        champs = Counter(row.get("champion") for row in rows if row.get("champion"))
        roles = _role_stats(rows)
        current_lp = _current_lp(self.riot_id)
        historical_lp = _historical_lp(rows)
        peak_lp = max([*historical_lp, *([current_lp] if current_lp is not None else [])], default=None)

        e = self._base(player_label(self.riot_id))
        e.description = (
            f"**Current:** `{format_total_lp(current_lp)}`\n"
            f"**Peak:** `{format_total_lp(peak_lp)}`\n\n"
            f"**{len(rows)}** games witnessed · `{wins}W / {losses}L / {draws}D`\n"
            f"Lifetime net LP: **`{net_lp:+} LP`**"
        )
        e.add_field(name="Main", value=champs.most_common(1)[0][0] if champs else "Unknown", inline=True)
        e.add_field(name="Role", value=roles.most_common(1)[0][0] if roles else "Unknown", inline=True)
        e.add_field(name="Watching Since", value=_first_date(rows), inline=True)
        if not decisive:
            e.add_field(name="Note", value="Profile will grow as Sisyphus sees more ranked games.", inline=False)
        return e

    def journey_embed(self) -> discord.Embed:
        rows = _rows(self.riot_id)
        months = _monthly_lp(rows)
        best_month = max(months.items(), key=lambda item: item[1], default=None)
        worst_month = min(months.items(), key=lambda item: item[1], default=None)
        streak_result, streak_count = current_streak(rows)
        lp_values = [int(row.get("lp_total")) for row in rows if isinstance(row.get("lp_total"), int)]
        e = self._base("Journey", GOLD)
        e.add_field(name="Current Streak", value=f"`{streak_count} {streak_result or 'games'}`", inline=True)
        e.add_field(name="Longest Win Streak", value=f"`{_longest_win_streak(rows)}`", inline=True)
        e.add_field(name="Lifetime LP", value=f"`{sum(parse_lp_delta(row) for row in rows):+} LP`", inline=True)
        if lp_values:
            e.add_field(name="Highest Point", value=f"`{format_total_lp(max(lp_values))}`", inline=True)
            e.add_field(name="Lowest Point", value=f"`{format_total_lp(min(lp_values))}`", inline=True)
        if best_month:
            e.add_field(name="Best Month", value=f"`{best_month[0]}` · `{best_month[1]:+} LP`", inline=True)
        if worst_month:
            e.add_field(name="Hardest Month", value=f"`{worst_month[0]}` · `{worst_month[1]:+} LP`", inline=True)
        milestones = ensure_player_milestones(self.riot_id)
        if milestones:
            e.add_field(
                name="Recent Milestones",
                value="\n".join(f"• {m.get('date')} — {m.get('label')}" for m in milestones[-5:])[:1024],
                inline=False,
            )
        return e

    def identity_embed(self) -> discord.Embed:
        rows = _rows(self.riot_id)
        champ_stats = _champ_stats(rows)
        most_played = sorted(champ_stats.items(), key=lambda item: item[1]["games"], reverse=True)
        best_champ = max(
            ((champ, s) for champ, s in champ_stats.items() if s["games"] >= 3),
            key=lambda item: (item[1]["wins"] / item[1]["games"], item[1]["games"]),
            default=None,
        )
        roles = _role_stats(rows)
        e = self._base("Identity", ACCENT)
        e.add_field(
            name="Most Played",
            value="\n".join(f"• **{champ}** `{s['games']} games`" for champ, s in most_played[:5]) or "Not enough champion history.",
            inline=False,
        )
        if best_champ:
            champ, stats = best_champ
            e.add_field(name="Best Champion", value=f"**{champ}** `{stats['wins']}/{stats['games']} wins`", inline=True)
        e.add_field(name="Main Role", value=roles.most_common(1)[0][0] if roles else "Unknown", inline=True)
        e.add_field(name="Champion Pool", value=f"`{len(champ_stats)} unique`", inline=True)
        averages = []
        for key, label, suffix in (
            ("kda", "KDA", ""),
            ("cs_per_min", "CS/min", ""),
            ("vision", "Vision", ""),
        ):
            value = _avg(rows, key)
            if value is not None:
                averages.append(f"`{value:.1f}` {label}{suffix}")
        if averages:
            e.add_field(name="Typical Game", value=" · ".join(averages), inline=False)
        return e

    def records_embed(self) -> discord.Embed:
        rows = _rows(self.riot_id)
        e = self._base("Records", GOLD)
        record_ids = _server_record_match_ids()
        metrics = [
            ("damage", "Most Damage", "{:,}"),
            ("kda", "Best KDA", "{:.2f}"),
            ("cs_per_min", "Highest CS/min", "{:.1f}"),
            ("vision", "Most Vision", "{}"),
            ("duration", "Longest Game", "{}s"),
        ]
        for key, label, fmt in metrics:
            candidates = [row for row in rows if isinstance(row.get(key), (int, float))]
            if not candidates:
                continue
            row = max(candidates, key=lambda item: item.get(key) or 0)
            value = fmt.format(row.get(key))
            server = f" · Boulder Archive: {record_ids[str(row.get('match_id'))]}" if str(row.get("match_id")) in record_ids else ""
            e.add_field(name=label, value=f"`{value}` on **{row.get('champion', 'Unknown')}**{server}", inline=False)
        if not e.fields:
            e.description = "Records will appear once Sisyphus has enriched match rows."
        return e

    def memories_embed(self) -> discord.Embed:
        memories = player_memories(self.riot_id)
        e = self._base("Memories", SOFT)
        if not memories:
            e.description = "No saved memories yet."
            return e
        lines = []
        for memory in memories[:10]:
            result = outcome_icon(memory.get("result"))
            recap = f" [recap]({memory['recap_url']})" if memory.get("recap_url") else ""
            lines.append(
                f"{result} **{memory.get('name')}** — {memory.get('champion', 'Unknown')} "
                f"`{memory.get('lp_change', '?')} LP` · {memory.get('date', '')}{recap}"
            )
        e.description = "\n".join(lines)
        return e

    @discord.ui.button(label="Overview", style=discord.ButtonStyle.primary, custom_id="profile_overview")
    async def btn_overview(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.overview_embed(), view=self)

    @discord.ui.button(label="Journey", style=discord.ButtonStyle.secondary, custom_id="profile_journey")
    async def btn_journey(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.journey_embed(), view=self)

    @discord.ui.button(label="Identity", style=discord.ButtonStyle.secondary, custom_id="profile_identity")
    async def btn_identity(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.identity_embed(), view=self)

    @discord.ui.button(label="Records", style=discord.ButtonStyle.secondary, custom_id="profile_records")
    async def btn_records(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.records_embed(), view=self)

    @discord.ui.button(label="Memories", style=discord.ButtonStyle.secondary, custom_id="profile_memories")
    async def btn_memories(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.memories_embed(), view=self)


def player_profile_view(riot_id: str) -> PlayerProfileView:
    return PlayerProfileView(riot_id)
