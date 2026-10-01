"""Daily rank boundaries and report views, independent of live bot state."""
from __future__ import annotations

from datetime import timedelta

import discord

from .ranks import format_total_lp, tier_for_total_lp, tier_image_url
from .utils import now_ist, today_ist


def lp_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def match_lp(row):
    value = lp_number(row.get("lp_change"))
    # Old zero-delta wins/losses were saved while the provider rank was stale.
    if value == 0 and row.get("result") in {"WIN", "LOSS"} and row.get("lp_status") != "known":
        return None
    duration = lp_number(row.get("duration"))
    if value is None and row.get("result") == "DRAW" and duration is not None and 0 < duration < 120:
        return 0
    return value


def daily_boundaries(state, riot_id, report_date):
    """Compare the prior day's close with the report day's close, never tomorrow."""
    snapshots = state.get("daily_lp", {}).get(riot_id, {})
    opening = lp_number(snapshots.get(str(report_date - timedelta(days=1))))
    closing = lp_number(snapshots.get(str(report_date)))
    return opening, closing


def record_rank_snapshot(state, riot_id, total_lp, observed_at):
    """Keep the current day's latest observed rank. Do not rewrite prior days."""
    total = lp_number(total_lp)
    if total is None:
        return
    day = str(observed_at.date())
    state.setdefault("daily_lp", {}).setdefault(riot_id, {})[day] = total
    observations = state.setdefault("daily_lp_observations", {}).setdefault(riot_id, {})
    entry = observations.setdefault(day, {"first_observed_at": observed_at.isoformat()})
    entry["last_observed_at"] = observed_at.isoformat()


def merge_daily_history(stored, recent, report_date):
    """Retain stored games when the provider's latest feed is delayed or truncated."""
    rows = {str(h.get("match_id") or f"stored-{i}"): dict(h)
            for i, h in enumerate(stored) if h.get("date") == str(report_date)}
    for i, row in enumerate(recent):
        if row.get("date") == str(report_date):
            key = str(row.get("match_id") or f"recent-{i}")
            rows[key] = {**row, **rows.get(key, {})}
    return sorted(rows.values(), key=lambda r: str(r.get("match_created_at") or r.get("recorded_at") or r.get("date") or ""))


def daily_lp_summary(opening, closing, history):
    values = [match_lp(row) for row in history]
    unknown = sum(value is None for value in values)
    known_sum = sum(value for value in values if value is not None)
    if opening is not None and closing is not None:
        return closing - opening, "snapshots", unknown, known_sum
    if history and not unknown:
        return known_sum, "matches", 0, known_sum
    return None, "partial" if any(value is not None for value in values) else "unavailable", unknown, known_sum


def icon(row):
    return {"WIN": "✅", "LOSS": "❌", "DRAW": "➖"}.get(row.get("result"), "❔")


def lp_label(value):
    return "Unavailable" if value is None else f"`{format_total_lp(value)}`"


class DailyReportView(discord.ui.View):
    """Daily summary and recent history, shared by previews and production."""

    def __init__(self, riot_id, today_lp, yesterday_lp, history_today, history_all,
                 report_date=None, *, namespace="daily", timeout=180):
        super().__init__(timeout=timeout)
        self.riot_id = riot_id
        self.today_lp = lp_number(today_lp)
        self.yesterday_lp = lp_number(yesterday_lp)
        self.history_today = history_today
        self.history_all = history_all
        self.report_date = report_date or today_ist()
        self.message = None
        for child in self.children:
            child.custom_id = f"{namespace}:{child.custom_id}"

    def _summary_embed(self):
        net, source, unknown, known_sum = daily_lp_summary(self.yesterday_lp, self.today_lp, self.history_today)
        color = 0x5865F2 if net is None else 0x57F287 if net >= 0 else 0xED4245
        embed = discord.Embed(title="Daily Report",
                              description=f"**{self.report_date.strftime('%A, %B %d %Y')}**",
                              color=color, timestamp=now_ist())
        author = {"name": self.riot_id}
        if self.today_lp is not None and self.today_lp > 0:
            author["icon_url"] = tier_image_url(tier_for_total_lp(self.today_lp))
        embed.set_author(**author)
        counts = [sum(h.get("result") == result for h in self.history_today) for result in ("WIN", "LOSS", "DRAW")]
        embed.add_field(name="Games", value=f"**{len(self.history_today)}**", inline=True)
        embed.add_field(name="W / L / D", value=f"`✅ {counts[0]}`  `❌ {counts[1]}`  `➖ {counts[2]}`", inline=True)
        if net is not None:
            net_text = f"**`{net:+d} LP`**" + ("\nRecorded matches" if source == "matches" else "")
        elif source == "partial":
            net_text = f"**Unavailable**\nKnown matches: `{known_sum:+d} LP`"
        else:
            net_text = "**Unavailable**"
        embed.add_field(name="Net LP", value=net_text, inline=True)
        embed.add_field(name="Start LP", value=lp_label(self.yesterday_lp), inline=True)
        embed.add_field(name="End LP" if self.report_date < today_ist() else "Current LP", value=lp_label(self.today_lp), inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        lines = []
        for i, row in enumerate(self.history_today, 1):
            delta = match_lp(row)
            change = "LP unavailable" if delta is None else f"{delta:+d} LP"
            context = ""
            if row.get("kills") is not None and row.get("deaths") is not None:
                context = f" · `{row.get('kills', 0)}/{row.get('deaths', 0)}/{row.get('assists', 0)}`"
            lines.append(f"`{i}.` {icon(row)} **{row.get('champion', 'Unknown')}** `{change}`{context}")
        # Reserve space for LP details, progress and Discord's aggregate limit.
        chunks = []
        chunk = ""
        for line in lines:
            line = line[:1000]
            if len(chunk) + len(line) + 1 > 1024:
                chunks.append(chunk)
                chunk = ""
            chunk += ("\n" if chunk else "") + line
        if chunk:
            chunks.append(chunk)
        displayed = 0
        for page, chunk in enumerate(chunks):
            name = "Match History" if page == 0 else "Match History (continued)"
            if len(embed.fields) >= 21 or len(embed) + len(name) + len(chunk) > 4900:
                break
            embed.add_field(name=name, value=chunk, inline=False)
            displayed += len(chunk.splitlines())
        if displayed < len(lines):
            embed.add_field(name="More Matches", value=f"{len(lines) - displayed} more recorded games are included in the totals.", inline=False)
        if unknown or source != "snapshots":
            details = []
            if source == "snapshots":
                details.append("Daily Net LP uses the saved start and end rank snapshots.")
            elif source == "matches":
                details.append("Net LP uses the recorded matches. A rank boundary is unavailable.")
            else:
                details.append("The full daily change is unavailable because LP records are incomplete.")
            if unknown:
                details.append(f"Individual LP is unavailable for {unknown} {'match' if unknown == 1 else 'matches'}.")
            embed.add_field(name="LP Details", value="\n".join(details), inline=False)
        blocks = min(abs(net) // 5, 20) if net is not None else 0
        embed.add_field(name="Progress", value=(("■" if net >= 0 else "□") * blocks or "▪") if net is not None else "Unavailable", inline=False)
        embed.set_footer(text="Ranked Solo/Duo only · LP uses the report day's boundaries")
        return embed

    def _history_embed(self):
        embed = discord.Embed(title="Recent History", color=0x5865F2, timestamp=now_ist())
        author = {"name": self.riot_id}
        if self.today_lp is not None and self.today_lp > 0:
            author["icon_url"] = tier_image_url(tier_for_total_lp(self.today_lp))
        embed.set_author(**author)
        lines = []
        for row in self.history_all[::-1][:10]:
            delta = match_lp(row)
            change = "LP unavailable" if delta is None else f"{delta:+d} LP"
            details = ""
            cs, kp = row.get("cs_per_min"), row.get("kill_participation")
            if isinstance(cs, (int, float)):
                details += f" · `{cs:.1f} CS/min`"
            if isinstance(kp, (int, float)):
                details += f" · `{kp:.0f}% KP`"
            lines.append(f"{icon(row)} **{row.get('champion', 'Unknown')}** `{change}`{details}  _{row.get('date', '')}_")
        embed.description = "\n".join(lines) or "No games recorded yet."
        return embed

    @discord.ui.button(label="📊 Summary", style=discord.ButtonStyle.primary, custom_id="summary", row=0)
    async def btn_summary(self, interaction, _button):
        await interaction.response.edit_message(embed=self._summary_embed(), view=self)

    @discord.ui.button(label="📜 Recent History", style=discord.ButtonStyle.secondary, custom_id="history", row=0)
    async def btn_history(self, interaction, _button):
        await interaction.response.edit_message(embed=self._history_embed(), view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
