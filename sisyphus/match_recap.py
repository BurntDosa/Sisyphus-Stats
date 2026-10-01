"""State-independent shared match embeds and navigation.

Snapshots are ordinary JSON dictionaries. This module deliberately never imports
the bot, runtime state, betting, or profile services; the caller supplies Remember.
"""
from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone

import discord

from .ddragon import build_champion_roster_thumbnail, build_composite_items_image, champion_icon_url
from .ranks import format_rank, tier_emoji


def recap_digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def total(parts, field):
    values = [number(p.get(field)) for p in parts]
    return sum(values) if values and all(v is not None for v in values) else None


def ratio(numerator, denominator):
    if numerator is None or denominator is None:
        return None
    return numerator / denominator * 100 if denominator else 0.0


def shown(value, precision=None):
    if value is None:
        return "Unavailable"
    return f"{value:,.{precision}f}" if precision is not None else f"{value:,}"


def name(member):
    return discord.utils.escape_markdown(str(member["riot_id"]).split("#", 1)[0])[:80]


def members(snapshot):
    return sorted(snapshot["members"], key=lambda m: m["riot_id"].casefold())


def stamp(snapshot):
    try:
        dt = datetime.fromisoformat(snapshot["match"]["info"]["gameCreation"].replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def duration(snapshot):
    seconds = int(snapshot["match"]["info"].get("gameDuration") or 0)
    return f"{seconds // 60}m {seconds % 60:02d}s"


def team_parts(snapshot):
    return [p for p in snapshot["match"]["info"]["participants"] if p.get("teamId") == snapshot["team_id"]]


def metrics(snapshot):
    pair = [m["participant"] for m in members(snapshot)]
    team = team_parts(snapshot)
    kills, deaths, assists = (total(pair, k) for k in ("kills", "deaths", "assists"))
    damage = total(pair, "totalDamageDealtToChampions")
    gold = total(pair, "goldEarned")
    cs = [number(p.get("totalMinionsKilled")) for p in pair]
    jungle = [number(p.get("neutralMinionsKilled")) for p in pair]
    return {
        "kills": kills, "deaths": deaths, "assists": assists,
        "kda": (kills + assists) / max(deaths, 1) if all(v is not None for v in (kills, deaths, assists)) else None,
        "damage": damage, "damage_share": ratio(damage, total(team, "totalDamageDealtToChampions")),
        "gold": gold, "gold_share": ratio(gold, total(team, "goldEarned")),
        "cs": sum(cs) + sum(jungle) if all(v is not None for v in cs + jungle) else None,
        "vision": total(pair, "visionScore"),
    }


def lp_line(member):
    tier = member.get("tier")
    rank = format_rank(tier, member.get("rank")) if tier else "Rank unavailable"
    raw = number(member.get("lp"))
    current = f"{tier_emoji(tier)} **{rank}**" + (f" · {raw} LP" if raw is not None else "")
    delta = number(member.get("lp_delta"))
    status = "Pending" if member.get("lp_status") == "pending" else "Unavailable"
    return current + (f" · **{delta:+d} LP**" if isinstance(delta, int) else f" · LP change **{status}**")


def player_lines(snapshot, member):
    p = member["participant"]
    team = team_parts(snapshot)
    k, d, a = (number(p.get(f)) for f in ("kills", "deaths", "assists"))
    kda = (k + a) / max(d, 1) if all(v is not None for v in (k, d, a)) else None
    cs_a, cs_b = number(p.get("totalMinionsKilled")), number(p.get("neutralMinionsKilled"))
    cs = cs_a + cs_b if cs_a is not None and cs_b is not None else None
    seconds = number(snapshot["match"]["info"].get("gameDuration"))
    cpm = cs / (seconds / 60) if cs is not None and seconds else None
    damage, gold = number(p.get("totalDamageDealtToChampions")), number(p.get("goldEarned"))
    kp = ratio(k + a if k is not None and a is not None else None, total(team, "kills"))
    return [
        lp_line(member),
        f"**{shown(k)}/{shown(d)}/{shown(a)}** · `{shown(kda, 2)}` KDA",
        f"CS **{shown(cs)}** · `{shown(cpm, 1)}/min`",
        f"Damage **{shown(damage)}** · `{shown(ratio(damage, total(team, 'totalDamageDealtToChampions')), 0)}%` of team",
        f"Gold **{shown(gold)}** · Kill participation `{shown(kp, 0)}%`",
        f"Vision score **{shown(number(p.get('visionScore')))}**",
    ]


def team_context(snapshot):
    info = snapshot["match"]["info"]
    team, enemy = team_parts(snapshot), [p for p in info["participants"] if p.get("teamId") != snapshot["team_id"]]
    summary = [f"Team kills **{shown(total(team, 'kills'))}–{shown(total(enemy, 'kills'))}**"]
    first_objectives = []
    teams = {t.get("teamId"): t.get("objectives", {}) for t in info.get("teams", [])}
    ours, theirs = teams.get(snapshot["team_id"], {}), teams.get(200 if snapshot["team_id"] == 100 else 100, {})
    for field, label in (("dragon", "Dragons"), ("baron", "Barons")):
        if field in ours and field in theirs:
            summary.append(f"{label} **{ours[field].get('kills', 0)}–{theirs[field].get('kills', 0)}**")
    for field, label in (("champion", "First blood"), ("tower", "First tower"), ("dragon", "First dragon"), ("baron", "First baron")):
        if ours.get(field, {}).get("first") or theirs.get(field, {}).get("first"):
            first_objectives.append(f"{label}: **{'Our team' if ours.get(field, {}).get('first') else 'Enemy team'}**")
    return "\n".join([" · ".join(summary)] + ([" · ".join(first_objectives)] if first_objectives else []))


def base_embed(snapshot, title):
    color = {"WIN": 0x57F287, "LOSS": 0xED4245, "DRAW": 0x99AAB5}.get(snapshot["outcome"], 0x5865F2)
    return discord.Embed(title=title[:256], color=color, timestamp=stamp(snapshot))


def overview_embed(snapshot):
    roster = members(snapshot)
    result = {"WIN": "✅ Victory", "LOSS": "❌ Defeat", "DRAW": "➖ Remake"}[snapshot["outcome"]]
    label = "Duo" if len(roster) == 2 else "Squad"
    e = base_embed(snapshot, f"{result} · {label} Recap")
    e.set_author(name=" & ".join(str(m["riot_id"]).split("#", 1)[0] for m in roster)[:256])
    e.description = f"**Ranked Solo/Duo** · `{duration(snapshot)}` · Team score **{shown(total(team_parts(snapshot), 'kills'))}–{shown(total([p for p in snapshot['match']['info']['participants'] if p.get('teamId') != snapshot['team_id']], 'kills'))}**"
    for i, m in enumerate(roster):
        p = m["participant"]
        champ = discord.utils.escape_markdown(str(p.get("championName") or "Unknown champion"))
        role = f" ({p['position']})" if p.get("position") else ""
        e.add_field(name=f"{name(m)} · {champ}{role}"[:256], value="\n".join(player_lines(snapshot, m))[:1024], inline=len(roster) == 2)
        if len(roster) == 2 and i == 0:
            # Reserve the middle inline column as a gutter between teammates.
            e.add_field(name="\u200b", value="\u200b", inline=True)
    stats = metrics(snapshot)
    e.add_field(name=f"{label} Contribution", value=(
        f"**{shown(stats['kills'])}/{shown(stats['deaths'])}/{shown(stats['assists'])}** combined K/D/A · `{shown(stats['kda'], 2)}` KDA\n"
        f"Damage **{shown(stats['damage'])}** · `{shown(stats['damage_share'], 0)}%` of team\n"
        f"Gold **{shown(stats['gold'])}** · `{shown(stats['gold_share'], 0)}%` of team\n"
        f"CS **{shown(stats['cs'])}** · Combined vision score **{shown(stats['vision'])}**"
    ), inline=False)
    e.add_field(name="Team Context", value=team_context(snapshot), inline=False)
    e.set_footer(text="Player details and team scoreboards are available below")
    return e


def player_embed(snapshot, member):
    p = member["participant"]
    e = base_embed(snapshot, f"{name(member)} · {p.get('championName') or 'Unknown champion'}")
    e.description = f"**Ranked Solo/Duo** · `{duration(snapshot)}`\n{lp_line(member)}"
    icon = champion_icon_url(p.get("championId"))
    if icon:
        e.set_thumbnail(url=icon)
    lines = player_lines(snapshot, member)
    e.add_field(name="Core Line", value="\n".join(lines[1:3]), inline=True)
    e.add_field(name="Output", value="\n".join(lines[3:5]), inline=True)
    vision = [lines[5]]
    for field, label in (("wardsPlaced", "Wards placed"), ("wardsKilled", "Wards cleared"), ("controlWardsBought", "Control wards")):
        if number(p.get(field)) is not None:
            vision.append(f"{label} `{p[field]}`")
    e.add_field(name="Map Work", value="\n".join(vision), inline=True)
    if member.get("story_headline"):
        e.add_field(name="Story", value=str(member["story_headline"])[:1024], inline=False)
    if member.get("spotlights"):
        e.add_field(name="Spotlight", value="\n".join(f"• {line}" for line in member["spotlights"])[:1024], inline=False)
    items = [str(x) for x in p.get("itemNames", []) if x]
    if items:
        e.add_field(name="Items", value=" · ".join(items)[:1024], inline=False)
    e.add_field(name="Team Context", value=team_context(snapshot), inline=False)
    return e


def scoreboard_embed(snapshot, team_id=None):
    e = base_embed(snapshot, ("Full Scoreboard" if team_id is None else f"{'🔵 Blue' if team_id == 100 else '🔴 Red'} Team") + f" · {duration(snapshot)}")
    highlighted = {m["participant"].get("puuid") for m in members(snapshot) if m["participant"].get("puuid")}
    identities = {(m["participant"].get("gameName", "").casefold(), m["participant"].get("tagLine", "").casefold()) for m in members(snapshot)}
    e.description = "▶ = tracked teammate"
    for tid in ([team_id] if team_id else [100, 200]):
        parts = [p for p in snapshot["match"]["info"]["participants"] if p.get("teamId") == tid]
        lines = []
        for p in sorted(parts, key=lambda p: (-int(p.get("kills") or 0), -int(p.get("assists") or 0))):
            tracked = p.get("puuid") in highlighted or (str(p.get("gameName") or "").casefold(), str(p.get("tagLine") or "").casefold()) in identities
            lines.append(f"{'▶ ' if tracked else ''}**{p.get('championName') or 'Unknown'}** · {shown(p.get('kills'))}/{shown(p.get('deaths'))}/{shown(p.get('assists'))} · Damage {shown(p.get('totalDamageDealtToChampions'))}")
        e.add_field(name="🔵 Blue Team" if tid == 100 else "🔴 Red Team", value="\n".join(lines)[:1024] or "Unavailable", inline=False)
    return e


class SharedRecapView(discord.ui.View):
    def __init__(self, snapshot, *, remember=None, namespace="recap", timeout=300):
        super().__init__(timeout=timeout)
        self.snapshot = snapshot
        self.remember = remember
        self.message = None
        self.images = {}
        self.thumbnail_bytes = None
        self.marker = f"{namespace}:{recap_digest(snapshot['key'])}"
        self.current_page = "overview"
        roster = members(snapshot)
        self._button("Overview", "overview", 0, discord.ButtonStyle.primary)
        if len(roster) <= 2:
            for i, m in enumerate(roster):
                self._button(str(m["riot_id"]).split("#", 1)[0][:80], f"player:{i}", 0)
            self._button("🔵 Blue Team", "blue", 0)
            self._button("🔴 Red Team", "red", 0)
            self._button("Full Scoreboard", "full", 1)
        else:
            self._button("🔵 Blue Team", "blue", 0)
            self._button("🔴 Red Team", "red", 0)
            self._button("Full Scoreboard", "full", 0)
            select = discord.ui.Select(placeholder="Player details", custom_id=f"{self.marker}:player", row=1,
                options=[discord.SelectOption(label=str(m["riot_id"])[:100], value=str(i)) for i, m in enumerate(roster)])
            async def choose(interaction):
                await self.change_page(interaction, f"player:{select.values[0]}")
            select.callback = choose
            self.add_item(select)
        remember_button = discord.ui.Button(label="Remember", custom_id=f"{self.marker}:remember", row=2 if len(roster) > 2 else 1)
        remember_button.callback = self._remember
        self.add_item(remember_button)

    def _button(self, label, page, row, style=discord.ButtonStyle.secondary):
        button = discord.ui.Button(label=label, style=style, row=row, custom_id=f"{self.marker}:{page}")
        async def navigate(interaction):
            await self.change_page(interaction, page)
        button.callback = navigate
        self.add_item(button)

    async def prepare(self, session):
        self.thumbnail_bytes = await build_champion_roster_thumbnail(session,
            [m["participant"].get("championId") for m in members(self.snapshot)])
        for i, member in enumerate(members(self.snapshot)):
            ids = [member["participant"].get(f"item{n}", 0) for n in range(7)]
            file = await build_composite_items_image(session, ids)
            if file:
                self.images[i] = file.fp.read()
                file.close()

    def payload(self, page):
        if page == "overview":
            embed = overview_embed(self.snapshot)
            if self.thumbnail_bytes:
                embed.set_thumbnail(url="attachment://champions.png")
                return {"embed": embed, "view": self,
                    "file": discord.File(io.BytesIO(self.thumbnail_bytes), filename="champions.png")}
        elif page.startswith("player:"):
            i = int(page.split(":")[1])
            embed = player_embed(self.snapshot, members(self.snapshot)[i])
            if i in self.images:
                filename = f"items-{i}.png"
                embed.set_image(url=f"attachment://{filename}")
                return {"embed": embed, "view": self, "file": discord.File(io.BytesIO(self.images[i]), filename=filename)}
        else:
            embed = scoreboard_embed(self.snapshot, {"blue": 100, "red": 200}.get(page))
        return {"embed": embed, "view": self}

    def get_overview_kwargs(self):
        return self.payload("overview")

    async def change_page(self, interaction, page):
        self.current_page = page
        payload = self.payload(page)
        payload["attachments"] = [payload.pop("file")] if "file" in payload else []
        await interaction.response.edit_message(**payload)

    async def _remember(self, interaction):
        if self.remember:
            await self.remember(interaction, self.snapshot, self.message)
        else:
            await interaction.response.send_message("Remember is unavailable for this recap.", ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
