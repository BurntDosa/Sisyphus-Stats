"""Discord UI views — match scoreboard, daily report, stats tabs."""
from __future__ import annotations

import io

import aiohttp
import discord

from .config import BETTING_ENABLED, DASHBOARD_URL, DEVELOPER_DISCORD_ID
from .ddragon import build_composite_items_image, champion_icon_url
from .community import praise_lines
from .outcome import compute_net_lp, match_outcome, outcome_icon
from .profiles import MemoryNameModal, recap_headline, user_owns_player
from .ranks import (
    DIVISION_BY_INDEX,
    TIER_BY_INDEX,
    TIER_COLOR,
    format_total_lp,
    tier_emoji,
    tier_image_url,
)
from .utils import as_list, now_ist, today_ist


def sort_key(p):
    return (-p["kills"], -p["assists"])


def duration_str(secs):
    return f"{secs // 60}m {secs % 60:02d}s"


def lp_delta_str(old, new):
    if old is None:
        return ""
    d = new - old
    return f"`{'+' if d >= 0 else ''}{d} LP`"


def percent(num, den):
    return (num / den * 100) if den else 0.0


def compact_int(value):
    value = int(value or 0)
    if abs(value) >= 1000:
        return f"{value / 1000:.1f}k"
    return str(value)


async def _hide_timed_out_view(view, label):
    for c in view.children:
        if isinstance(c, discord.ui.Button):
            c.disabled = True

    msg = getattr(view, "message", None)
    if msg is None:
        return

    try:
        await msg.edit(view=None)
    except discord.HTTPException as exc:
        print(f"[view.timeout] {label}: {exc}")
        try:
            await msg.edit(view=view)
        except discord.HTTPException as exc2:
            print(f"[view.timeout] {label} fallback failed: {exc2}")


def build_team_table(participants, highlight_puuid=None):
    header = (
        f"{'Champion':<14} {'K':>3} {'D':>3} {'A':>3} {'CS':>5} {'DMG':>7} {'Gold':>6}"
    )
    sep = "─" * len(header)
    rows = [header, sep]
    for p in sorted(participants, key=sort_key):
        champ = p["championName"][:13]
        k, d, a = p["kills"], p["deaths"], p["assists"]
        cs = p["totalMinionsKilled"] + p.get("neutralMinionsKilled", 0)
        dmg = p["totalDamageDealtToChampions"]
        gold = p["goldEarned"]
        marker = "▶ " if p["puuid"] == highlight_puuid else "  "
        rows.append(
            f"{marker}{champ:<12} {k:>3} {d:>3} {a:>3} {cs:>5} {dmg:>7,} {gold:>6,}"
        )
    return "```\n" + "\n".join(rows) + "\n```"


class ScoreboardView(discord.ui.View):
    """Buttons: [📊 Overview] [🔵 Blue] [🔴 Red] [🏅 Full]"""

    def __init__(
        self, match_data, tracked_puuid, riot_id, tier, rank, lp, old_lp, new_lp
    ):
        super().__init__(timeout=300)
        self.match = match_data
        self.tracked_puuid = tracked_puuid
        self.riot_id = riot_id
        self.tier = tier
        self.rank = rank
        self.lp = lp
        self.old_lp = old_lp
        self.new_lp = new_lp
        self.message = None

        info = match_data["info"]
        parts = info["participants"]
        self.blue_team = [p for p in parts if p["teamId"] == 100]
        self.red_team = [p for p in parts if p["teamId"] == 200]
        self.participant = next(
            (p for p in parts if p["puuid"] == tracked_puuid), parts[0]
        )
        self.duration = info["gameDuration"]
        self.blue_kills = sum(p["kills"] for p in self.blue_team)
        self.red_kills = sum(p["kills"] for p in self.red_team)
        self.tracked_team_id = self.participant.get("teamId")

        blue_win = any(p["win"] for p in self.blue_team)
        self.blue_result = "Victory" if blue_win else "Defeat"
        self.red_result = "Defeat" if blue_win else "Victory"
        self.items_file_bytes: bytes | None = None

    async def prepare(self, session: aiohttp.ClientSession):
        item_ids = [self.participant.get(f"item{i}", 0) for i in range(7)]
        file = await build_composite_items_image(session, item_ids)
        if file:
            self.items_file_bytes = file.fp.read()
        else:
            self.items_file_bytes = None

    def _overview_embed(self):
        p = self.participant
        lp_diff = (self.new_lp - self.old_lp) if self.old_lp is not None else None
        outcome = match_outcome(
            p.get("result_code"),
            lp_diff if lp_diff is not None else 0,
            self.duration,
        )

        if outcome == "WIN":
            color = TIER_COLOR.get(self.tier, 0x5865F2)
            result_line = "✅ Victory"
        elif outcome == "LOSS":
            color = 0xED4245
            result_line = "❌ Defeat"
        else:
            color = 0x99AAB5
            result_line = f"➖ Remake · {duration_str(self.duration)}"

        rank_str = f"{self.tier} {self.rank}".strip() if self.rank else self.tier

        if self.old_lp is not None and (self.new_lp // 100) > (self.old_lp // 100):
            old_tier_idx = self.old_lp // 400
            old_tier = TIER_BY_INDEX.get(old_tier_idx, "UNRANKED")
            old_div_idx = min(3, (self.old_lp % 400) // 100)
            old_div = DIVISION_BY_INDEX.get(old_div_idx, "4")
            old_lp_val = (self.old_lp % 400) % 100
            old_rank_str = f"{old_tier} {old_div}".strip() if old_div else old_tier
            lp_line = (
                f"{tier_emoji(old_tier)} **{old_rank_str}** — {old_lp_val} LP  "
                f"{lp_delta_str(self.old_lp, self.new_lp)}  ⬆️ "
                f"{tier_emoji(self.tier)} **{rank_str}**"
            )
        elif self.old_lp is not None and (self.new_lp // 100) < (self.old_lp // 100):
            old_tier_idx = self.old_lp // 400
            old_tier = TIER_BY_INDEX.get(old_tier_idx, "UNRANKED")
            old_div_idx = min(3, (self.old_lp % 400) // 100)
            old_div = DIVISION_BY_INDEX.get(old_div_idx, "4")
            old_lp_val = (self.old_lp % 400) % 100
            old_rank_str = f"{old_tier} {old_div}".strip() if old_div else old_tier
            lp_line = (
                f"{tier_emoji(old_tier)} **{old_rank_str}** — {old_lp_val} LP  "
                f"{lp_delta_str(self.old_lp, self.new_lp)}  ⬇️ "
                f"{tier_emoji(self.tier)} **{rank_str}**"
            )
        else:
            lp_line = (
                f"{tier_emoji(self.tier)} **{rank_str}** — {self.lp} LP  "
                f"{lp_delta_str(self.old_lp, self.new_lp)}"
            )

        e = discord.Embed(color=color, timestamp=now_ist())
        e.set_author(name=self.riot_id, icon_url=tier_image_url(self.tier))
        position = p.get("position")
        title_champ = p["championName"]
        if position:
            title_champ = f"{title_champ} ({position})"
        e.description = f"**Ranked Solo/Duo** · `{duration_str(self.duration)}`\n{lp_line}"
        champ_icon = champion_icon_url(p.get("championId"))
        if champ_icon:
            e.set_thumbnail(url=champ_icon)

        kda = (p["kills"] + p["assists"]) / max(p["deaths"], 1)
        cs = p["totalMinionsKilled"] + p.get("neutralMinionsKilled", 0)
        cpm = cs / (self.duration / 60) if self.duration else 0.0
        dmg = p["totalDamageDealtToChampions"]
        vision = p["visionScore"]
        gold = p["goldEarned"]
        team = self.blue_team if self.tracked_team_id == 100 else self.red_team
        enemy = self.red_team if self.tracked_team_id == 100 else self.blue_team
        team_kills = sum(int(tp.get("kills") or 0) for tp in team)
        team_damage = sum(int(tp.get("totalDamageDealtToChampions") or 0) for tp in team)
        team_gold = sum(int(tp.get("goldEarned") or 0) for tp in team)
        kp = percent(p["kills"] + p["assists"], team_kills)
        dmg_share = percent(dmg, team_damage)
        gold_share = percent(gold, team_gold)

        e.add_field(
            name="Core Line",
            value=(
                f"**{p['kills']}/{p['deaths']}/{p['assists']}** KDA · `{kda:.2f}` ratio\n"
                f"**{cs}** CS · `{cpm:.1f}/min`\n"
                f"Level **{p['champLevel']}**"
            ),
            inline=True,
        )
        e.add_field(
            name="Output",
            value=(
                f"Damage **{dmg:,}** · `{dmg_share:.0f}% share`\n"
                f"Gold **{gold:,}** · `{gold_share:.0f}% share`\n"
                f"Kill participation `{kp:.0f}%`"
            ),
            inline=True,
        )
        vision_bits = [f"Vision **{vision}**"]
        if p.get("wardsPlaced"):
            vision_bits.append(f"Wards `{p['wardsPlaced']}`")
        if p.get("wardsKilled"):
            vision_bits.append(f"Cleared `{p['wardsKilled']}`")
        if p.get("controlWardsBought"):
            vision_bits.append(f"Control `{p['controlWardsBought']}`")
        e.add_field(name="Map Work", value="\n".join(vision_bits), inline=True)

        row = {
            "result": outcome,
            "lp_change": "" if lp_diff is None else f"{'+' if lp_diff >= 0 else ''}{lp_diff}",
            "champion": p.get("championName"),
            "kills": p.get("kills"),
            "deaths": p.get("deaths"),
            "assists": p.get("assists"),
            "kill_participation": round(kp, 1),
            "cs_per_min": round(cpm, 1),
            "vision": vision,
            "damage_share": round(dmg_share, 1),
        }
        e.add_field(name="Story", value=recap_headline(self.riot_id, row), inline=False)
        spotlights = praise_lines(self.riot_id, row)
        if spotlights:
            e.add_field(name="Spotlight", value="\n".join(f"• {line}" for line in spotlights), inline=False)
        e.add_field(
            name="Context",
            value=(
                f"Team kills `{team_kills}` · Enemy kills `{sum(int(ep.get('kills') or 0) for ep in enemy)}`\n"
                f"Damage share `{dmg_share:.0f}%` · Gold share `{gold_share:.0f}%`"
            ),
            inline=False,
        )

        item_names = [name for name in as_list(p.get("itemNames")) if name]
        if item_names:
            e.add_field(name="Items", value=" · ".join(item_names), inline=False)
        else:
            items = [
                str(p.get(f"item{i}", 0))
                for i in range(7)
                if p.get(f"item{i}", 0) != 0
            ]
            if items:
                e.add_field(name="Items (IDs)", value=" · ".join(items), inline=False)

        # Match Objectives Summary
        teams_data = self.match.get("info", {}).get("teams", [])
        if teams_data:
            first_blood = "None"
            first_tower = "None"
            blue_dragons = 0
            red_dragons = 0
            blue_barons = 0
            red_barons = 0
            
            for t in teams_data:
                team_label = "🔵 Blue" if t.get("teamId") == 100 else "🔴 Red"
                objs = t.get("objectives", {})
                if objs.get("champion", {}).get("first"):
                    first_blood = team_label
                if objs.get("tower", {}).get("first"):
                    first_tower = team_label
                if t.get("teamId") == 100:
                    blue_dragons = objs.get("dragon", {}).get("kills", 0)
                    blue_barons = objs.get("baron", {}).get("kills", 0)
                else:
                    red_dragons = objs.get("dragon", {}).get("kills", 0)
                    red_barons = objs.get("baron", {}).get("kills", 0)
            
            has_objectives = any(
                t.get("objectives") for t in teams_data if isinstance(t, dict)
            )
            if has_objectives:
                objectives_summary = (
                    f"First Blood: **{first_blood}** · First Tower: **{first_tower}**\n"
                    f"Dragons: 🔵 `{blue_dragons}` vs `{red_dragons}` 🔴 · "
                    f"Barons: 🔵 `{blue_barons}` vs `{red_barons}` 🔴"
                )
                e.add_field(name="Objectives", value=objectives_summary, inline=False)

        if getattr(self, "items_file_bytes", None):
            e.set_image(url="attachment://items.png")

        if self.tracked_team_id == 100:
            match_score = f"{self.blue_kills}-{self.red_kills}"
        else:
            match_score = f"{self.red_kills}-{self.blue_kills}"

        e.title = f"{result_line} · {title_champ} · {match_score}"
        e.set_footer(text="Team and full scoreboard views are available below")
        return e

    def get_overview_kwargs(self):
        kwargs = {"embed": self._overview_embed(), "view": self}
        if getattr(self, "items_file_bytes", None):
            kwargs["file"] = discord.File(
                io.BytesIO(self.items_file_bytes), filename="items.png"
            )
        return kwargs

    def _team_embed(self, team, team_name, result, color):
        e = discord.Embed(
            title=f"{'🔵' if team_name == 'Blue' else '🔴'} {team_name} Team  ·  **{result}**",
            color=color,
            timestamp=now_ist(),
        )
        e.set_author(name=self.riot_id, icon_url=tier_image_url(self.tier))
        champ_icon = champion_icon_url(self.participant.get("championId"))
        if champ_icon:
            e.set_thumbnail(url=champ_icon)
        e.description = (
            f"⏱ `{duration_str(self.duration)}`\n▶ = tracked player\n\n"
            + build_team_table(team, self.tracked_puuid)
        )

        for p in sorted(team, key=sort_key):
            kda = (p["kills"] + p["assists"]) / max(p["deaths"], 1)
            cs = p["totalMinionsKilled"] + p.get("neutralMinionsKilled", 0)
            dmg = p["totalDamageDealtToChampions"]
            marker = "▶ " if p["puuid"] == self.tracked_puuid else ""
            e.add_field(
                name=f"{marker}{p['championName']}",
                value=(
                    f"`{p['kills']}/{p['deaths']}/{p['assists']}` · {kda:.1f} KDA\n"
                    f"`{cs}` CS · `{dmg:,}` dmg"
                ),
                inline=True,
            )
        return e

    @discord.ui.button(
        label="📊 Overview", style=discord.ButtonStyle.primary, custom_id="overview", row=0
    )
    async def btn_overview(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        kwargs = self.get_overview_kwargs()
        kwargs["attachments"] = [kwargs.pop("file")] if "file" in kwargs else []
        await interaction.response.edit_message(**kwargs)

    @discord.ui.button(
        label="🔵 Blue Team", style=discord.ButtonStyle.secondary, custom_id="blue", row=0
    )
    async def btn_blue(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        e = self._team_embed(self.blue_team, "Blue", self.blue_result, 0x5865F2)
        await interaction.response.edit_message(embed=e, view=self)

    @discord.ui.button(
        label="🔴 Red Team", style=discord.ButtonStyle.secondary, custom_id="red", row=0
    )
    async def btn_red(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        e = self._team_embed(self.red_team, "Red", self.red_result, 0xED4245)
        await interaction.response.edit_message(embed=e, view=self)

    @discord.ui.button(
        label="🏅 Full Leaderboard",
        style=discord.ButtonStyle.secondary,
        custom_id="full",
        row=0,
    )
    async def btn_full(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        info = self.match["info"]
        parts = info["participants"]
        win_t = 100 if any(p["win"] for p in self.blue_team) else 200
        sorted_parts = sorted(
            parts, key=lambda p: (0 if p["teamId"] == win_t else 1, sort_key(p))
        )

        e = discord.Embed(
            title=f"🏅 Full Scoreboard  ·  {duration_str(self.duration)}",
            color=0xFEE75C,
            timestamp=now_ist(),
        )
        e.set_author(name=self.riot_id, icon_url=tier_image_url(self.tier))
        champ_icon = champion_icon_url(self.participant.get("championId"))
        if champ_icon:
            e.set_thumbnail(url=champ_icon)

        for team_id, label in [(100, "🔵 Blue Team"), (200, "🔴 Red Team")]:
            team = [p for p in sorted_parts if p["teamId"] == team_id]
            is_win = any(p["win"] for p in team)
            lines = []
            for p in sorted(team, key=sort_key):
                kda = f"{p['kills']}/{p['deaths']}/{p['assists']}"
                cs = p["totalMinionsKilled"] + p.get("neutralMinionsKilled", 0)
                marker = "**▶** " if p["puuid"] == self.tracked_puuid else ""
                lines.append(f"{marker}`{p['championName'][:10]:<10}` {kda}  CS:{cs}")
            e.add_field(
                name=f"{label}  {'✅ Victory' if is_win else '❌ Defeat'}",
                value="\n".join(lines),
                inline=False,
            )
        await interaction.response.edit_message(embed=e, view=self)

    @discord.ui.button(
        label="Remember",
        style=discord.ButtonStyle.secondary,
        custom_id="remember_match",
        row=1,
    )
    async def btn_remember(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not user_owns_player(interaction.user.id, self.riot_id):
            await interaction.response.send_message(
                "❌ Only the linked player can save this match as a memory.",
                ephemeral=True,
            )
            return
        match_id = self.match.get("metadata", {}).get("matchId")
        if not match_id:
            await interaction.response.send_message(
                "❌ This recap does not have a match id to remember.",
                ephemeral=True,
            )
            return
        recap_url = getattr(getattr(self, "message", None), "jump_url", None)
        await interaction.response.send_modal(
            MemoryNameModal(self.riot_id, str(match_id), recap_url=recap_url)
        )

    async def on_timeout(self):
        await _hide_timed_out_view(self, "ScoreboardView")


class DailyReportView(discord.ui.View):
    """Daily summary with a 'Recent History' tab."""

    def __init__(
        self, riot_id, today_lp, yesterday_lp, history_today, history_all, report_date=None
    ):
        super().__init__(timeout=180)
        self.riot_id = riot_id
        self.today_lp = today_lp
        self.yesterday_lp = yesterday_lp
        self.history_today = history_today
        self.history_all = history_all
        self.report_date = report_date or today_ist()
        self.message = None

    def _summary_embed(self):
        fallback_diff = self.today_lp - (
            self.yesterday_lp if self.yesterday_lp is not None else self.today_lp
        )
        diff = compute_net_lp(self.history_today, fallback_diff)
        color = 0x57F287 if diff >= 0 else 0xED4245
        sign = "+" if diff >= 0 else ""

        wins = sum(1 for h in self.history_today if h["result"] == "WIN")
        losses = sum(1 for h in self.history_today if h["result"] == "LOSS")
        draws = sum(1 for h in self.history_today if h["result"] == "DRAW")

        e = discord.Embed(
            title="Daily Report",
            description=f"**{self.report_date.strftime('%A, %B %d %Y')}**",
            color=color,
            timestamp=now_ist(),
        )
        e.set_author(name=self.riot_id, icon_url=tier_image_url(self.today_lp // 400))

        e.add_field(name="Games", value=f"**{wins + losses + draws}**", inline=True)
        e.add_field(
            name="W / L / D",
            value=f"`✅ {wins}`  `❌ {losses}`  `➖ {draws}`",
            inline=True,
        )
        e.add_field(name="Net LP", value=f"**`{sign}{diff} LP`**", inline=True)

        if self.yesterday_lp is not None:
            e.add_field(
                name="Previous LP",
                value=f"`{format_total_lp(self.yesterday_lp)}`",
                inline=True,
            )
        e.add_field(
            name="Current LP",
            value=f"`{format_total_lp(self.today_lp)}`",
            inline=True,
        )
        e.add_field(name="​", value="​", inline=True)

        decisive_history_today = [
            h for h in self.history_today if h.get("result") != "DRAW"
        ]
        if decisive_history_today:
            lines = []
            for i, h in enumerate(decisive_history_today, 1):
                icon = outcome_icon(h.get("result"))
                lpc = h.get("lp_change")
                lpc_str = "?" if lpc is None else lpc
                context = ""
                if h.get("kills") is not None and h.get("deaths") is not None:
                    context = f" · `{h.get('kills', 0)}/{h.get('deaths', 0)}/{h.get('assists', 0)}`"
                lines.append(f"`{i}.` {icon} **{h['champion']}** `{lpc_str} LP`{context}")
            if lines:
                e.add_field(name="Match History", value="\n".join(lines), inline=False)

        blocks = min(abs(diff) // 5, 20)
        bar = ("■" if diff >= 0 else "□") * blocks or "▪"
        e.add_field(name="Progress", value=bar, inline=False)
        e.set_footer(text="Ranked Solo/Duo only")
        return e

    def _history_embed(self):
        e = discord.Embed(
            title="Recent History", color=0x5865F2, timestamp=now_ist()
        )
        e.set_author(name=self.riot_id, icon_url=tier_image_url(self.today_lp // 400))
        recent = [h for h in self.history_all[::-1] if h.get("result") != "DRAW"]
        if not recent:
            e.description = "No games recorded yet."
        else:
            lines = []
            for h in recent[:10]:
                icon = outcome_icon(h.get("result"))
                lpc = h.get("lp_change")
                lpc_str = "?" if lpc is None else lpc
                details = ""
                if h.get("cs_per_min") or h.get("kill_participation"):
                    details = f" · `{h.get('cs_per_min', 0):.1f} CS/min` · `{h.get('kill_participation', 0):.0f}% KP`"
                lines.append(
                    f"{icon} **{h['champion']}** `{lpc_str} LP`{details}  _{h.get('date', '')}_"
                )
            e.description = "\n".join(lines)
        return e

    @discord.ui.button(
        label="📊 Summary", style=discord.ButtonStyle.primary, custom_id="summary", row=0
    )
    async def btn_summary(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.edit_message(embed=self._summary_embed(), view=self)

    @discord.ui.button(
        label="📜 Recent History",
        style=discord.ButtonStyle.secondary,
        custom_id="history",
        row=0,
    )
    async def btn_history(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.edit_message(embed=self._history_embed(), view=self)

    async def on_timeout(self):
        await _hide_timed_out_view(self, "DailyReportView")


class StatsTabsView(discord.ui.View):
    """Tabs: Today's stats / All-time history."""

    def __init__(
        self,
        riot_id,
        tier,
        rank,
        lp,
        today_diff,
        today_wins,
        today_losses,
        today_draws,
        all_wins,
        all_losses,
        all_draws,
        all_net_lp,
        peak_total_lp,
    ):
        super().__init__(timeout=180)
        self.riot_id = riot_id
        self.tier = tier
        self.rank = rank
        self.lp = lp
        self.today_diff = today_diff
        self.today_wins = today_wins
        self.today_losses = today_losses
        self.today_draws = today_draws
        self.all_wins = all_wins
        self.all_losses = all_losses
        self.all_draws = all_draws
        self.all_net_lp = all_net_lp
        self.peak_total_lp = peak_total_lp
        self.message = None

    def _today_embed(self):
        rank_str = f"{self.tier} {self.rank}".strip() if self.rank else self.tier
        sign = "+" if self.today_diff >= 0 else ""
        e = discord.Embed(
            title="Today's Stats",
            color=TIER_COLOR.get(self.tier, 0x5865F2),
            timestamp=now_ist(),
        )
        e.set_author(name=self.riot_id, icon_url=tier_image_url(self.tier))
        e.add_field(
            name="Rank",
            value=f"{tier_emoji(self.tier)} **{rank_str}** — {self.lp} LP",
            inline=False,
        )
        e.add_field(
            name="Today",
            value=(
                f"`✅ {self.today_wins}`  `❌ {self.today_losses}`  `➖ {self.today_draws}`\n"
                f"**`{sign}{self.today_diff} LP`**"
            ),
            inline=False,
        )
        e.set_footer(text="Ranked Solo/Duo only")
        return e

    def _all_time_embed(self):
        sign = "+" if self.all_net_lp >= 0 else ""
        e = discord.Embed(
            title="All-time History", color=0xFEE75C, timestamp=now_ist()
        )
        e.set_author(name=self.riot_id, icon_url=tier_image_url(self.tier))
        e.add_field(
            name="W / L / D",
            value=f"`✅ {self.all_wins}`  `❌ {self.all_losses}`  `➖ {self.all_draws}`",
            inline=True,
        )
        e.add_field(
            name="Net LP (tracked)",
            value=f"**`{sign}{self.all_net_lp} LP`**",
            inline=True,
        )
        e.add_field(
            name="Peak Rank",
            value=f"`{format_total_lp(self.peak_total_lp)}`",
            inline=True,
        )
        e.set_footer(text="Tracked history only")
        return e

    @discord.ui.button(
        label="📊 Today",
        style=discord.ButtonStyle.primary,
        custom_id="stats_today",
        row=0,
    )
    async def btn_today(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.edit_message(embed=self._today_embed(), view=self)

    @discord.ui.button(
        label="🏆 All-time",
        style=discord.ButtonStyle.secondary,
        custom_id="stats_alltime",
        row=0,
    )
    async def btn_all_time(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.edit_message(embed=self._all_time_embed(), view=self)

    async def on_timeout(self):
        await _hide_timed_out_view(self, "StatsTabsView")


class HelpView(discord.ui.View):
    """Interactive help menu view with category buttons."""

    def __init__(self):
        super().__init__(timeout=180)
        self.message = None
        if not BETTING_ENABLED:
            self.remove_item(self.btn_betting)

    def _update_button_colors(self, active_button: discord.ui.Button):
        buttons = [
            self.btn_overview,
            self.btn_tracking,
            self.btn_community,
            self.btn_reporting,
        ]
        if BETTING_ENABLED:
            buttons.append(self.btn_betting)
        for btn in buttons:
            if btn == active_button:
                btn.style = discord.ButtonStyle.primary
            else:
                btn.style = discord.ButtonStyle.secondary

    def _overview_embed(self):
        e = discord.Embed(
            title="Sisyphus Help",
            description=(
                "**Ranked Solo/Duo tracking, post-game recaps, squad rituals, "
                "and fake-points markets for your Discord server.**\n\n"
                "Most commands work as slash commands and with the `!` prefix."
            ),
            color=0x5865F2,
            timestamp=now_ist(),
        )
        e.add_field(
            name="Core Loop",
            value=(
                "`/track` players, link Discord accounts, and get automatic "
                "match recaps after ranked Solo/Duo games. Recaps can be saved "
                "as player-owned memories. Use `/status` for current service health "
                "and `/dashboard` for analytics."
            ),
            inline=False,
        )
        e.add_field(
            name="Community",
            value=(
                "Live Game Room, Queue Board, Weekly and Monthly Recaps, Squad Goals, "
                "Rivalries, player profiles, and the Boulder Archive."
            ),
            inline=False,
        )
        if BETTING_ENABLED:
            e.add_field(
                name="Points Mode",
                value=(
                    "Fake-points markets are active. Use the Betting tab for wallets, bets, "
                    "markets, all-in buttons, insurance, and leaderboards."
                ),
                inline=False,
            )
        if DASHBOARD_URL:
            e.add_field(
                name="Analytics Dashboard",
                value=f"[Open Sisyphus Analytics]({DASHBOARD_URL}) or use `/dashboard`.",
                inline=False,
            )
        e.set_footer(text="Use the category buttons below to browse commands.")
        return e

    def _tracking_embed(self):
        e = discord.Embed(
            title="Tracking & Recaps",
            description="Manage tracked Riot IDs and pull ranked Solo/Duo summaries.",
            color=0x3498DB,
            timestamp=now_ist(),
        )
        e.add_field(
            name="Player Tracking",
            value=(
                "• `/track <riot_id>` — Register a player to be tracked (e.g. `/track GameName#TAG`).\n"
                "• `/untrack <riot_id>` — Stop tracking a player.\n"
                "• `/list` — View all currently tracked players."
            ),
            inline=False,
        )
        e.add_field(
            name="Discord Linkage",
            value=(
                "• `/link <riot_id>` — Link your Discord account to your tracked League profile.\n"
                "• `/unlink` — Remove your account link.\n"
                "• `/whoami` — Check which Riot ID is linked to your Discord account."
            ),
            inline=False,
        )
        e.add_field(
            name="Stats & Recaps",
            value=(
                "• `/recap [target]` — Post the latest ranked Solo/Duo recap.\n"
                "• `/stats [target]` — View rank, daily LP, peak, and all-time tracked stats.\n"
                "• `/profile [target]` — View a Sisyphus-observed player profile.\n"
                "• `/dailyreport [target]` — Force a daily report card."
            ),
            inline=False,
        )
        e.set_footer(text="Targets can be GameName#TAG, @user, or omitted if linked.")
        return e

    def _community_embed(self):
        e = discord.Embed(
            title="Community",
            description="Friend-group rituals for ranked Solo/Duo only. No shaming, just useful chaos.",
            color=0x57F287,
            timestamp=now_ist(),
        )
        e.add_field(
            name="Queue Board",
            value=(
                "• `/queueup [note]` — Let friends know you are looking for ranked Solo/Duo.\n"
                "• `/queueboard` — See who is currently looking to play.\n"
                "• `/queueclear` — Remove yourself from the queue board."
            ),
            inline=False,
        )
        e.add_field(
            name="Squad Rituals",
            value=(
                "• `/weeklyrecap` — Post this week's squad recap.\n"
                "• `/monthlyrecap [YYYY-MM]` — Admin: post a monthly squad recap.\n"
                "• `/halloffame` — Show the Boulder Archive records.\n"
                "• `/squadgoal` — View this week's squad goals.\n"
                "• `/squadgoal set <type> [target]` — Set a weekly goal."
            ),
            inline=False,
        )
        e.add_field(
            name="Goal Types",
            value="`wins` · `games` · `positive_lp_days` · `unique_champions` · `streak`",
            inline=False,
        )
        e.add_field(
            name="Friendly Rivalries",
            value=(
                "• `/rivalry` — Show active opt-in rivalries.\n"
                "• `/rivalry challenge @friend` — Send a challenge.\n"
                "• `/rivalry accept @friend` — Accept a challenge.\n"
                "• `/rivalry end @friend` — End a rivalry."
            ),
            inline=False,
        )
        e.set_footer(text="Live Game Room, Queue Beacon, and praise highlights are automatic.")
        return e

    def _betting_embed(self):
        from .betting import DAILY_FLOOR, INITIAL_BALANCE, MAX_STAKE, MIN_STAKE

        e = discord.Embed(
            title="Betting & Points",
            description=(
                "Fake-points markets for tracked ranked Solo/Duo games. "
                f"Stake range: `{MIN_STAKE}-{MAX_STAKE}` pts."
            ),
            color=0x2ECC71,
            timestamp=now_ist(),
        )
        e.add_field(
            name="Wallet & Balances",
            value=(
                "• `/wallet [target]` — View your current points, wagered amount, and stats (alias: `/balance`).\n"
                "• `/bprofile [target]` — View a detailed betting profile summary card.\n"
                "• `/insurance [target]` — Check weekly insurance tokens.\n"
                f"• New wallets start at `{INITIAL_BALANCE}` pts; daily floor is `{DAILY_FLOOR}` pts."
            ),
            inline=False,
        )
        e.add_field(
            name="Markets & Bets",
            value=(
                "• `/markets` — List currently active open markets.\n"
                "• `/bet <market_id> <side> <stake> [insurance]` — Place a bet, e.g. `/bet m6 WIN 100 y`.\n"
                "• `/editbet <market_id> <side> <stake> [insurance]` — Modify your active bet before the market locks.\n"
                "• `/cancelbet <market_id>` — Cancel your bet and get a full refund (only before lock).\n"
                "• `/mybets` — Show your active bets.\n"
                "• Market cards include **All-In WIN** and **All-In LOSE** buttons."
            ),
            inline=False,
        )
        e.add_field(
            name="Leaderboards & Admin",
            value=(
                "• `/leaderboard [metric] [range_name]` — Compare points, profit, or streak metrics.\n"
                "• `/marketopen <riot_id | prob | title | rationale>` — Open a custom market.\n"
                "• `/marketstatus`, `/marketbets`, `/settlebet`, `/voidbet`, `/refund`, `/audit` — Admin tools."
            ),
            inline=False,
        )
        return e

    def _reporting_embed(self):
        e = discord.Embed(
            title="Support & Reporting",
            description="File reports or request adjustments from the developer directly through Discord.",
            color=0x9B59B6,
            timestamp=now_ist(),
        )
        e.add_field(
            name="User Reporting System",
            value=(
                "• `/report` — Submit a report through an interactive form:\n"
                "  * **Bug / Bot Issue** — Report technical bugs.\n"
                "  * **Wrong Match Result** — Report incorrect match recap outcomes."
                + (
                    "\n  * **Refund Request** — Claim point refunds for wagers."
                    if BETTING_ENABLED
                    else ""
                )
            ),
            inline=False,
        )
        e.add_field(
            name="Service Health",
            value=(
                "• `/status` — Show bot uptime, Discord connectivity, ranked polling, "
                "Riot, OP.GG, and points-market health.\n"
                "• `/dashboard` — Open the authenticated analytics dashboard."
            ),
            inline=False,
        )
        e.set_footer(text="Reports are sent privately when possible.")
        return e

    @discord.ui.button(
        label="🏠 Overview", style=discord.ButtonStyle.primary, custom_id="help_overview", row=0
    )
    async def btn_overview(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self._update_button_colors(button)
        await interaction.response.edit_message(embed=self._overview_embed(), view=self)

    @discord.ui.button(
        label="👤 Tracking", style=discord.ButtonStyle.secondary, custom_id="help_tracking", row=0
    )
    async def btn_tracking(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self._update_button_colors(button)
        await interaction.response.edit_message(embed=self._tracking_embed(), view=self)

    @discord.ui.button(
        label="🤝 Community", style=discord.ButtonStyle.secondary, custom_id="help_community", row=0
    )
    async def btn_community(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self._update_button_colors(button)
        await interaction.response.edit_message(embed=self._community_embed(), view=self)

    @discord.ui.button(
        label="💰 Betting", style=discord.ButtonStyle.secondary, custom_id="help_betting", row=0
    )
    async def btn_betting(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self._update_button_colors(button)
        await interaction.response.edit_message(embed=self._betting_embed(), view=self)

    @discord.ui.button(
        label="📋 Reporting", style=discord.ButtonStyle.secondary, custom_id="help_reporting", row=0
    )
    async def btn_reporting(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self._update_button_colors(button)
        await interaction.response.edit_message(embed=self._reporting_embed(), view=self)

    async def on_timeout(self):
        await _hide_timed_out_view(self, "HelpView")


class BugReportModal(discord.ui.Modal, title="Report Bot Issue / Bug"):
    description_input = discord.ui.TextInput(
        label="Issue Description",
        style=discord.TextStyle.paragraph,
        placeholder="Describe what happened...",
        required=True,
        max_length=1000
    )
    reproduce_input = discord.ui.TextInput(
        label="Steps to Reproduce (Optional)",
        style=discord.TextStyle.paragraph,
        placeholder="1. Type /stats\n2. See blank embed...",
        required=False,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await handle_submitted_report(
            interaction,
            "Bot Issue / Bug",
            {
                "Description": self.description_input.value,
                "Steps to Reproduce": self.reproduce_input.value or "None"
            }
        )


class ResultCorrectionModal(discord.ui.Modal, title="Report Wrong Match Result"):
    match_input = discord.ui.TextInput(
        label="Match Details",
        style=discord.TextStyle.short,
        placeholder="Match ID or Player GameName#TAG",
        required=True,
        max_length=150
    )
    correction_input = discord.ui.TextInput(
        label="What was wrong?",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Match resolved as WIN but was actually a remake/LOSS...",
        required=True,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await handle_submitted_report(
            interaction,
            "Wrong Match Result",
            {
                "Match Details": self.match_input.value,
                "Details": self.correction_input.value
            }
        )


class RefundRequestModal(discord.ui.Modal, title="Request Refund"):
    amount_input = discord.ui.TextInput(
        label="Refund Amount (number only)",
        style=discord.TextStyle.short,
        placeholder="e.g. 500",
        required=True,
        max_length=15
    )
    reason_input = discord.ui.TextInput(
        label="Reason for Refund",
        style=discord.TextStyle.paragraph,
        placeholder="Why do you need a refund? (e.g. remake, bot error, etc.)",
        required=True,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Validate amount is numeric
        val_str = self.amount_input.value.strip()
        if not val_str.isdigit():
            await interaction.response.send_message(
                content="❌ **Error:** Refund amount must be a number only (e.g. 500). Please try again.",
                ephemeral=True
            )
            return
            
        await interaction.response.defer(ephemeral=True)
        await handle_submitted_report(
            interaction,
            "Refund Request",
            {
                "Refund Amount": val_str,
                "Reason": self.reason_input.value
            }
        )


class DeveloperRefundDecisionView(discord.ui.View):
    """View sent to the developer's DM to approve/deny a refund request."""

    def __init__(self, bot: discord.Client, report_id: str, reporter_id: int, amount: int, reason: str):
        super().__init__(timeout=86400) # 24-hour timeout
        self.bot = bot
        self.report_id = report_id
        self.reporter_id = reporter_id
        self.amount = amount
        self.reason = reason

    async def _disable_all(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success, custom_id="refund_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self._disable_all()

        from .betting import admin_refund
        from .state import data, save_data
        
        # Apply the refund
        wallet, err = await admin_refund(
            self.reporter_id, 
            self.amount, 
            reason=f"Approved Refund request {self.report_id}: {self.reason}"
        )
        
        # Update status in database
        for r in data.get("reports", []):
            if r.get("report_id") == self.report_id:
                r["status"] = "Approved"
                break
        save_data(data)

        # Edit developer DM embed
        embed = interaction.message.embeds[0]
        embed.title = f"✅ Refund Approved — {self.report_id}"
        embed.color = 0x2ECC71 # Green
        embed.add_field(name="Resolution", value=f"Approved by Developer. `{self.amount}` points refunded.", inline=False)
        await interaction.message.edit(embed=embed, view=self)

        # Notify reporter
        try:
            reporter = self.bot.get_user(self.reporter_id) or await self.bot.fetch_user(self.reporter_id)
            if reporter:
                await reporter.send(
                    f"✅ **Refund Approved!** Your refund request (`{self.report_id}`) for **{self.amount}** points was approved by the developer."
                )
        except Exception as e:
            print(f"[report] Failed to notify reporter {self.reporter_id} of approval: {e}")

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.danger, custom_id="refund_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self._disable_all()

        from .state import data, save_data
        
        # Update status in database
        for r in data.get("reports", []):
            if r.get("report_id") == self.report_id:
                r["status"] = "Denied"
                break
        save_data(data)

        # Edit developer DM embed
        embed = interaction.message.embeds[0]
        embed.title = f"❌ Refund Denied — {self.report_id}"
        embed.color = 0xED4245 # Red
        embed.add_field(name="Resolution", value="Denied by Developer.", inline=False)
        await interaction.message.edit(embed=embed, view=self)

        # Notify reporter
        try:
            reporter = self.bot.get_user(self.reporter_id) or await self.bot.fetch_user(self.reporter_id)
            if reporter:
                await reporter.send(
                    f"❌ **Refund Denied.** Your refund request (`{self.report_id}`) was reviewed and denied by the developer."
                )
        except Exception as e:
            print(f"[report] Failed to notify reporter {self.reporter_id} of denial: {e}")


class ReportSelectView(discord.ui.View):
    """View to select report type before launching corresponding Modal."""

    def __init__(self):
        super().__init__(timeout=180)
        self.message = None
        if not BETTING_ENABLED:
            self.remove_item(self.btn_refund)

    @discord.ui.button(label="🐛 Bug / Bot Issue", style=discord.ButtonStyle.danger, custom_id="report_bug")
    async def btn_bug(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BugReportModal())

    @discord.ui.button(label="❌ Wrong Match Result", style=discord.ButtonStyle.primary, custom_id="report_result")
    async def btn_result(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ResultCorrectionModal())

    @discord.ui.button(label="💰 Refund Request", style=discord.ButtonStyle.success, custom_id="report_refund")
    async def btn_refund(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RefundRequestModal())

    async def on_timeout(self):
        await _hide_timed_out_view(self, "ReportSelectView")


async def handle_submitted_report(interaction: discord.Interaction, report_type: str, fields: dict[str, str]):
    from .state import data, save_data
    from .bot import bot
    from .utils import now_ist

    # 1. Update counter and generate report ID
    if "report_counter" not in data:
        data["report_counter"] = 1000
    data["report_counter"] += 1
    report_id = f"R-{data['report_counter']}"

    # 2. Build the report entry
    report_entry = {
        "report_id": report_id,
        "reporter_id": interaction.user.id,
        "reporter_name": str(interaction.user),
        "report_type": report_type,
        "fields": fields,
        "status": "Pending",
        "timestamp": now_ist().isoformat()
    }
    
    # 3. Save to database
    if "reports" not in data:
        data["reports"] = []
    data["reports"].append(report_entry)
    save_data(data)

    # 4. Determine embed color based on type
    color = 0xED4245 # Red for Bug
    if report_type == "Wrong Match Result":
        color = 0xE67E22 # Orange
    elif report_type == "Refund Request":
        color = 0xF1C40F # Yellow

    # 5. Format embed for Developer DM
    embed = discord.Embed(
        title=f"📋 New Report Submitted — {report_id}",
        description=f"Type: **{report_type}**",
        color=color,
        timestamp=now_ist()
    )
    embed.add_field(name="Reporter", value=f"<@{interaction.user.id}> (`{interaction.user}`)", inline=False)
    for field_name, field_val in fields.items():
        embed.add_field(name=field_name, value=field_val, inline=False)
    embed.set_footer(text="Sisyphus Bot Reporting System")

    # 6. Send the developer review notification when it is privately configured.
    developer_id = DEVELOPER_DISCORD_ID
    if not developer_id:
        print("[report] Developer notification is disabled: DEVELOPER_DISCORD_ID is not configured.")
        await interaction.followup.send(
            content=f"✅ **Thank you!** Your report (**`{report_id}`**) has been logged for review.",
            ephemeral=True,
        )
        return
    try:
        developer = bot.get_user(developer_id) or await bot.fetch_user(developer_id)
        if developer:
            view = None
            if report_type == "Refund Request":
                try:
                    amount = int(fields["Refund Amount"])
                    reason = fields["Reason"]
                    view = DeveloperRefundDecisionView(bot, report_id, interaction.user.id, amount, reason)
                except Exception as e:
                    print(f"[report] Error preparing decision view: {e}")

            msg = await developer.send(
                content=f"🔔 **Sisyphus Report System Alert**\nA new user report has been logged.",
                embed=embed,
                view=view
            )
            if view:
                view.message = msg
            print(f"[report] Sent report {report_id} to developer DM.")
        else:
            print(f"[report] Failed to locate developer user ID {developer_id} in cache or fetch.")
    except Exception as e:
        print(f"[report] Error sending developer DM: {e}")

    # 7. Reply to the reporter
    await interaction.followup.send(
        content=f"✅ **Thank you!** Your report (**`{report_id}`**) has been logged and sent to the developer for review.",
        ephemeral=True
    )
