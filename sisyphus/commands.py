"""All !commands — !track, !untrack, !list, !link, !unlink, !whoami, !recap, !stats, !report."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from .bot import bot, get_post_destination
from .betting import (
    active_bets_embed,
    audit_embed,
    admin_refund,
    build_market_view,
    cancel_bet,
    create_market,
    get_market,
    get_wallet,
    insurance_embed,
    leaderboard_embed,
    list_open_markets,
    market_public_message,
    market_bets_embed,
    market_status_embed,
    edit_bet,
    place_bet,
    register_persistent_market_views,
    seed_market_for_tracked_key,
    settle_market,
    settle_markets_for_match,
    settled_market_message,
    timeout_stale_markets,
    betting_profile_embed,
    wallet_embed,
)
from .config import (
    ADMIN_IDS,
    APP_VERSION,
    BACKFILL_DAYS,
    BETTING_ENABLED,
    DASHBOARD_URL,
    OPGG_REGION,
    STATUS_PAGE_ENABLED,
    STATUS_PAGE_URL,
)
from .community import (
    GOAL_PRESETS,
    accept_rivalry,
    create_rivalry_invite,
    create_squad_goal,
    end_rivalry,
    halloffame_embed,
    queueboard_embed,
    queueclear,
    queueup,
    rivalry_embed,
    squad_goals_embed,
    weekly_recap_embed,
)
from .opgg import (
    build_history_entry,
    find_history_participant,
    get_lp_info,
    get_ranked_stats,
    get_recent_matches,
    get_summoner_profile,
    ranked_entries_from_profile,
    recent_today_history,
)
from .outcome import compute_all_time_stats, compute_net_lp
from .profiles import player_profile_view
from .ranks import TIER_COLOR, tier_emoji, tier_image_url
from .recap import build_latest_recap
from .state import data, posted_matches, save_data
from .utils import as_list, match_day_ist, today_ist, now_ist
from .views import DailyReportView, StatsTabsView, HelpView, ReportSelectView
from .health import health_snapshot

MENTION_RE = re.compile(r"^<@!?(\d+)>$")


def parse_mention_user_id(raw_target: str | None):
    if not raw_target:
        return None
    m = MENTION_RE.match(raw_target.strip())
    return m.group(1) if m else None


def linked_riot_for_user(user_id: int):
    key = data.get("links", {}).get(str(user_id))
    if not key:
        return None
    if key in data.get("tracked", {}):
        return key
    del data["links"][str(user_id)]
    save_data(data)
    return None


def resolve_target_key(ctx: commands.Context, target: str | None, command_name: str):
    tracked = data.get("tracked", {})
    if not tracked:
        return None, "No players tracked. Use `!track GameName#TAG` first."

    if target:
        raw = target.strip()
        mention_id = parse_mention_user_id(raw)
        if mention_id:
            linked = data.get("links", {}).get(mention_id)
            if linked and linked in tracked:
                return linked, None
            return (
                None,
                f"❌ <@{mention_id}> is not linked. Ask them to run `!link GameName#TAG`.",
            )
        if raw in tracked:
            return raw, None
        return None, f"❌ Not tracking **{raw}**."

    linked_self = linked_riot_for_user(ctx.author.id)
    if linked_self:
        return linked_self, None

    if len(tracked) == 1:
        return next(iter(tracked)), None
    return (
        None,
        f"❌ Multiple players tracked. Use `!{command_name} GameName#TAG`, "
        f"`!{command_name} @user`, or run `!link GameName#TAG`.",
    )


@bot.hybrid_command(name="help", help="Show Sisyphus-Bot commands and usage guide")
async def cmd_help(ctx: commands.Context):
    view = HelpView()
    embed = view._overview_embed()
    msg = await ctx.send(embed=embed, view=view)
    view.message = msg


def _status_duration(seconds: int) -> str:
    days, remainder = divmod(max(0, seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


@bot.hybrid_command(name="status", help="Show current bot and service health")
async def cmd_status(ctx: commands.Context):
    snapshot = health_snapshot()
    services = snapshot.get("services", {})
    labels = (
        ("bot", "Bot Availability"),
        ("discord", "Discord Gateway"),
        ("polling", "Ranked Match Polling"),
        ("riot", "Riot Live Detection"),
        ("opgg", "OP.GG Match Data"),
        ("markets", "Points Markets"),
    )
    healthy = all(bool(services.get(key, {}).get("healthy")) for key, _ in labels)
    lines = []
    for key, label in labels:
        service = services.get(key, {})
        state = "Operational" if service.get("healthy") else "Unavailable"
        lines.append(f"**{label}:** {state}")

    embed = discord.Embed(
        title="Sisyphus System Status",
        description=(
            "All monitored systems are operational."
            if healthy
            else "One or more monitored systems need attention."
        ),
        color=0x57F287 if healthy else 0xED4245,
        timestamp=now_ist(),
    )
    embed.add_field(name="Services", value="\n".join(lines), inline=False)
    process = snapshot.get("process", {})
    embed.add_field(name="Version", value=f"`v{APP_VERSION}`", inline=True)
    embed.add_field(
        name="Process Uptime",
        value=_status_duration(int(process.get("uptime_seconds", 0))),
        inline=True,
    )
    last_poll = services.get("polling", {}).get("last_success_at")
    if last_poll:
        try:
            poll_dt = datetime.fromisoformat(str(last_poll).replace("Z", "+00:00"))
            poll_value = discord.utils.format_dt(poll_dt, style="R")
        except ValueError:
            poll_value = "Not available"
    else:
        poll_value = "Waiting for first cycle"
    embed.add_field(name="Last Ranked Poll", value=poll_value, inline=True)
    if STATUS_PAGE_ENABLED and STATUS_PAGE_URL:
        embed.add_field(
            name="Public Status Page",
            value=f"[Open uptime and incident history]({STATUS_PAGE_URL})",
            inline=False,
        )
    if DASHBOARD_URL:
        embed.add_field(
            name="Analytics Dashboard",
            value=f"[Open Sisyphus Analytics]({DASHBOARD_URL})",
            inline=False,
        )
    embed.set_footer(text="Service-level health only")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="dashboard", help="Open the Sisyphus analytics dashboard")
async def cmd_dashboard(ctx: commands.Context):
    if not DASHBOARD_URL:
        await ctx.send("The analytics dashboard is not configured yet.")
        return
    await ctx.send(f"📊 [Open Sisyphus Analytics]({DASHBOARD_URL})")


@bot.hybrid_command(name="track", help="Track a player: !track GameName#TAG")
async def cmd_track(ctx, *, riot_id: str):
    await ctx.defer()
    if "#" not in riot_id:
        await ctx.send("❌ Format: `!track GameName#TAG`  (e.g. `!track Faker#KR1`)")
        return

    game_name, tag = riot_id.split("#", 1)
    async with aiohttp.ClientSession() as session:
        summoner, err = await get_summoner_profile(session, game_name, tag)
        if not summoner:
            if err and "not found" in err.lower():
                await ctx.send(
                    f"❌ Summoner `{riot_id}` not found on OP.GG region `{OPGG_REGION}`."
                )
            else:
                await ctx.send(f"❌ OP.GG MCP request failed: {err or 'unknown error'}")
            return
        ranked = ranked_entries_from_profile(summoner)
        tier, rank, lp, total_lp = get_lp_info(ranked)
        recent = await get_recent_matches(session, game_name, tag, count=20)
        recent_ranked = [m for m in recent if m.get("game_type") == "SOLORANKED"]
        last_id = recent_ranked[0].get("id") if recent_ranked else None

        puuid = summoner.get("puuid")
        if not puuid and recent_ranked:
            for participant in as_list(recent_ranked[0].get("participants")):
                s = participant.get("summoner") or {}
                if (s.get("game_name") or "").lower() == game_name.lower() and (
                    s.get("tagline") or ""
                ).lower() == tag.lower():
                    puuid = s.get("puuid")
                    break

        # Resolve the official key-specific PUUID using the Riot API
        try:
            from .config import REGION, RIOT_KEY
            if RIOT_KEY:
                account_url = f"https://{REGION}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag}"
                headers = {"X-Riot-Token": RIOT_KEY}
                async with session.get(account_url, headers=headers) as r:
                    if r.status == 200:
                        acc_data = await r.json()
                        if acc_data.get("puuid"):
                            puuid = acc_data["puuid"]
        except Exception as e:
            print(f"[commands] Failed to resolve PUUID on track for {game_name}#{tag}: {e}")

    key = f"{game_name}#{tag}"
    data["tracked"][key] = {
        "puuid": puuid,
        "game_name": game_name,
        "tag_line": tag,
        "last_match_id": last_id,
        "last_known_lp": total_lp,
        "history_backfilled": True,
    }
    if last_id:
        posted_matches.add(last_id)
    today_str = str(today_ist())
    data.setdefault("daily_lp", {}).setdefault(key, {})[today_str] = total_lp
    history_rows = data.setdefault("history", {}).setdefault(key, [])
    existing_ids = {h.get("match_id") for h in history_rows}
    cutoff = today_ist() - timedelta(days=BACKFILL_DAYS)
    for entry in reversed(recent_ranked):
        entry_id = entry.get("id")
        entry_day = match_day_ist(entry.get("created_at"))
        if (
            not entry_id
            or not entry_day
            or entry_day < cutoff
            or entry_id in existing_ids
        ):
            continue
        participant = find_history_participant(entry, game_name, tag, puuid)
        if not participant:
            continue
        history_rows.append(build_history_entry(entry, participant, total_lp))
        existing_ids.add(entry_id)
    save_data(data)
    destination = await get_post_destination()
    if BETTING_ENABLED and destination:
        await seed_market_for_tracked_key(key, destination, creator_id=ctx.author.id)

    rank_str = f"{tier} {rank}".strip() if rank else tier
    e = discord.Embed(
        title=f"Now tracking {key}",
        description=(
            f"{tier_emoji(tier)} **{rank_str}** — {lp} LP\n\n"
            "I'll post a recap after every ranked game."
        ),
        color=TIER_COLOR.get(tier, 0x5865F2),
    )
    e.set_author(name=key, icon_url=tier_image_url(tier))
    await ctx.send(embed=e)


@bot.hybrid_command(name="untrack", help="Stop tracking a player: !untrack GameName#TAG")
async def cmd_untrack(ctx, *, riot_id: str):
    key = riot_id.strip()
    if key in data["tracked"]:
        del data["tracked"][key]
        stale_links = [
            user_id for user_id, linked in data.get("links", {}).items() if linked == key
        ]
        for user_id in stale_links:
            del data["links"][user_id]
        save_data(data)
        await ctx.send(f"✅ Stopped tracking **{key}**.")
    else:
        await ctx.send(f"❌ **{key}** isn't being tracked.")


@bot.hybrid_command(name="list", help="List all tracked players")
async def cmd_list(ctx):
    if not data["tracked"]:
        await ctx.send("No players tracked. Use `!track GameName#TAG` to add one.")
        return
    e = discord.Embed(title="Tracked Players", color=0x5865F2)
    e.description = "\n".join(f"• `{k}`" for k in data["tracked"])
    await ctx.send(embed=e)


@bot.hybrid_command(name="link", help="Link yourself to a tracked Riot ID: !link GameName#TAG")
async def cmd_link(ctx, *, riot_id: str):
    key = riot_id.strip()
    if key not in data.get("tracked", {}):
        await ctx.send(f"❌ Not tracking **{key}**. Use `!track {key}` first.")
        return
    data.setdefault("links", {})[str(ctx.author.id)] = key
    save_data(data)
    await ctx.send(
        f"✅ Linked {ctx.author.mention} to **{key}**. "
        "You can now run `!stats`, `!report`, and `!recap` without arguments."
    )


@bot.hybrid_command(name="unlink", help="Remove your Discord↔Riot link")
async def cmd_unlink(ctx):
    links = data.setdefault("links", {})
    removed = links.pop(str(ctx.author.id), None)
    if removed:
        save_data(data)
        await ctx.send(f"✅ Removed your link to **{removed}**.")
        return
    await ctx.send("❌ You are not linked yet. Use `!link GameName#TAG`.")


@bot.hybrid_command(name="whoami", help="Show your linked Riot ID")
async def cmd_whoami(ctx):
    linked = linked_riot_for_user(ctx.author.id)
    if not linked:
        await ctx.send("❌ No Riot ID linked. Use `!link GameName#TAG`.")
        return
    await ctx.send(f"{ctx.author.mention} is linked to **{linked}**.")


@bot.hybrid_command(name="recap", help="Post latest match recap: !recap [GameName#TAG|@user]")
async def cmd_recap(ctx, *, target: str | None = None):
    await ctx.defer()
    key, err = resolve_target_key(ctx, target, "recap")
    if err:
        await ctx.send(err)
        return
    info = data["tracked"][key]
    async with aiohttp.ClientSession() as session:
        view, kwargs, recap_err = await build_latest_recap(session, key, info)
    if recap_err:
        await ctx.send(recap_err)
        return
    msg = await ctx.send(**kwargs)
    view.message = msg


@bot.hybrid_command(name="stats", help="Live rank stats: !stats [GameName#TAG|@user]")
async def cmd_stats(ctx, *, target: str | None = None):
    await ctx.defer()
    key, err = resolve_target_key(ctx, target, "stats")
    if err:
        await ctx.send(err)
        return
    info = data["tracked"][key]
    async with aiohttp.ClientSession() as session:
        ranked = await get_ranked_stats(
            session, info.get("game_name"), info.get("tag_line")
        )
        recent = await get_recent_matches(
            session, info.get("game_name"), info.get("tag_line"), count=20
        )
    if ranked is None:
        await ctx.send("❌ OP.GG ranked stats fetch failed — try again shortly.")
        return
    tier, rank, lp, total_lp = get_lp_info(ranked)
    today_str = str(today_ist())
    yesterday_str = str(today_ist() - timedelta(days=1))
    today_lp = data.get("daily_lp", {}).get(key, {}).get(today_str, total_lp)
    yesterday_lp = data.get("daily_lp", {}).get(key, {}).get(yesterday_str)
    fallback_diff = today_lp - (yesterday_lp if yesterday_lp is not None else today_lp)
    recent_ranked = [m for m in recent if m.get("game_type") == "SOLORANKED"]
    h_all = data.get("history", {}).get(key, [])
    h_today = recent_today_history(
        recent_ranked,
        info.get("game_name"),
        info.get("tag_line"),
        info.get("puuid"),
        h_all,
    )
    diff = compute_net_lp(h_today, fallback_diff)
    wins = sum(1 for h in h_today if h["result"] == "WIN")
    losses = sum(1 for h in h_today if h["result"] == "LOSS")
    draws = sum(1 for h in h_today if h["result"] == "DRAW")
    all_wins, all_losses, all_draws, all_net_lp, peak_total_lp = compute_all_time_stats(
        h_all
    )

    view = StatsTabsView(
        key,
        tier,
        rank,
        lp,
        diff,
        wins,
        losses,
        draws,
        all_wins,
        all_losses,
        all_draws,
        all_net_lp,
        peak_total_lp,
    )
    await ctx.send(embed=view._today_embed(), view=view)


@bot.hybrid_command(
    name="dailyreport", help="Force the daily report: !dailyreport [GameName#TAG|@user]"
)
async def cmd_dailyreport(ctx, *, target: str | None = None):
    await ctx.defer()
    key, err = resolve_target_key(ctx, target, "dailyreport")
    if err:
        await ctx.send(err)
        return
    today_str = str(today_ist())
    yesterday_str = str(today_ist() - timedelta(days=1))
    today_lp = data.get("daily_lp", {}).get(key, {}).get(today_str, 0)
    yesterday_lp = data.get("daily_lp", {}).get(key, {}).get(yesterday_str)
    info = data["tracked"][key]
    async with aiohttp.ClientSession() as session:
        recent = await get_recent_matches(
            session, info.get("game_name"), info.get("tag_line"), count=20
        )
    recent_ranked = [m for m in recent if m.get("game_type") == "SOLORANKED"]
    h_all = data.get("history", {}).get(key, [])
    h_today = recent_today_history(
        recent_ranked,
        info.get("game_name"),
        info.get("tag_line"),
        info.get("puuid"),
        h_all,
    )
    view = DailyReportView(key, today_lp, yesterday_lp, h_today, h_all)
    await ctx.send(embed=view._summary_embed(), view=view)


@bot.hybrid_command(name="report", help="Report a bot issue or wrong match outcome")
async def cmd_report(ctx):
    report_options = (
        "🐛 **Bug / Bot Issue** — Report technical bugs or errors.\n"
        "❌ **Wrong Match Result** — Report a match recap that resolved with the wrong outcome."
    )
    if BETTING_ENABLED:
        report_options += "\n💰 **Refund Request** — Request a point adjustment/refund for stakes."

    embed = discord.Embed(
        title="📋 Sisyphus Bot Reporting Center",
        description=(
            "Please select the type of report you want to file using the buttons below:\n\n"
            f"{report_options}"
        ),
        color=0x5865F2,
        timestamp=now_ist()
    )
    view = ReportSelectView()
    
    if ctx.interaction:
        msg = await ctx.send(embed=embed, view=view, ephemeral=True)
        view.message = msg
    else:
        try:
            msg = await ctx.author.send(embed=embed, view=view)
            view.message = msg
            try:
                await ctx.message.delete()
            except Exception:
                pass
            await ctx.send("📥 **Sisyphus Report System** — I have sent you a DM with the reporting form to keep your report private.", delete_after=10)
        except Exception:
            await ctx.send("❌ **Error:** I was unable to send you a DM. Please enable Direct Messages from server members or use the `/report` slash command.", delete_after=15)


@bot.hybrid_command(name="queueup", help="Join the ranked Solo/Duo queue board")
async def cmd_queueup(ctx, *, note: str | None = None):
    display_name = getattr(ctx.author, "display_name", str(ctx.author))
    embed = queueup(ctx.author.id, display_name, note)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="queueboard", help="Show friends looking for ranked Solo/Duo")
async def cmd_queueboard(ctx):
    await ctx.send(embed=queueboard_embed())


@bot.hybrid_command(name="queueclear", help="Remove yourself from the ranked queue board")
async def cmd_queueclear(ctx):
    if queueclear(ctx.author.id):
        await ctx.send("✅ Removed you from the ranked Solo/Duo queue board.")
    else:
        await ctx.send("You were not on the queue board.")


@bot.hybrid_group(name="rivalry", fallback="list", help="Opt-in friendly ranked rivalries")
async def cmd_rivalry(ctx):
    await ctx.send(embed=rivalry_embed())


@cmd_rivalry.command(name="challenge", help="Challenge a friend to an opt-in ranked rivalry")
async def cmd_rivalry_challenge(ctx, member: discord.Member):
    if member.bot or member.id == ctx.author.id:
        await ctx.send("❌ Choose another human in this server.")
        return
    await ctx.send(embed=create_rivalry_invite(ctx.author.id, member.id, ctx.author.display_name))


@cmd_rivalry.command(name="accept", help="Accept a pending rivalry challenge")
async def cmd_rivalry_accept(ctx, member: discord.Member):
    ok, embed = accept_rivalry(ctx.author.id, member.id)
    await ctx.send(embed=embed)


@cmd_rivalry.command(name="end", help="End an active rivalry")
async def cmd_rivalry_end(ctx, member: discord.Member):
    if end_rivalry(ctx.author.id, member.id):
        await ctx.send(f"✅ Ended the rivalry with {member.mention}.")
    else:
        await ctx.send("No active rivalry found.")


@bot.hybrid_group(name="squadgoal", fallback="list", help="Set and view ranked squad goals")
async def cmd_squadgoal(ctx):
    await ctx.send(embed=squad_goals_embed())


@cmd_squadgoal.command(name="set", help="Set a weekly squad goal")
@app_commands.choices(
    goal_type=[
        app_commands.Choice(name=name, value=goal_type)
        for goal_type, (name, _metric, _target) in GOAL_PRESETS.items()
    ]
)
async def cmd_squadgoal_set(ctx, goal_type: str, target: int | None = None):
    ok, embed = create_squad_goal(goal_type, target)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="weeklyrecap", help="Post the current weekly squad recap")
async def cmd_weeklyrecap(ctx):
    await ctx.send(embed=weekly_recap_embed(days=7))


@bot.hybrid_command(name="monthlyrecap", help="Admin: post the previous monthly recap")
async def cmd_monthlyrecap(ctx, month: str | None = None):
    if not _is_admin(ctx):
        await ctx.send("❌ Admin only.")
        return
    destination = await get_post_destination()
    if not destination:
        await ctx.send("❌ Could not resolve the configured post destination.")
        return
    year = month_num = None
    if month:
        try:
            year_text, month_text = month.split("-", 1)
            year = int(year_text)
            month_num = int(month_text)
            if month_num < 1 or month_num > 12:
                raise ValueError
        except ValueError:
            await ctx.send("❌ Month must be `YYYY-MM`, for example `2026-07`.")
            return
    from .monthly import run_monthly_recap

    posted = await run_monthly_recap(destination, ctx.bot, year, month_num)
    await ctx.send("✅ Monthly recap posted." if posted else "No monthly recap posted.")


@bot.hybrid_command(name="halloffame", help="Show the ranked Solo/Duo Boulder Archive")
async def cmd_halloffame(ctx):
    await ctx.send(embed=halloffame_embed())


def _is_admin(ctx: commands.Context) -> bool:
    if ctx.author.id in ADMIN_IDS:
        return True
    perms = ctx.author.guild_permissions
    return bool(perms.administrator or perms.manage_guild)


def _parse_market_spec(spec: str):
    parts = [p.strip() for p in spec.split("|")]
    tracked_key = parts[0] if parts else ""
    win_prob = 0.5
    title = f"Market for {tracked_key}" if tracked_key else "Market"
    rationale = ""
    if len(parts) > 1 and parts[1]:
        try:
            win_prob = float(parts[1])
        except ValueError:
            pass
    if len(parts) > 2 and parts[2]:
        title = parts[2]
    if len(parts) > 3:
        rationale = parts[3]
    return tracked_key, win_prob, title, rationale


@bot.hybrid_command(name="wallet", help="Show your betting wallet")
async def cmd_wallet(ctx, *, target: str | None = None):
    user_id = ctx.author.id
    if target:
        mention_id = parse_mention_user_id(target)
        if mention_id:
            user_id = int(mention_id)
    member = ctx.guild.get_member(user_id) if ctx.guild else None
    await get_wallet(user_id)
    await ctx.send(embed=wallet_embed(user_id, member.display_name if member else None))


@bot.hybrid_command(name="balance", help="Alias for !wallet")
async def cmd_balance(ctx, *, target: str | None = None):
    await cmd_wallet(ctx, target=target)


@bot.hybrid_command(name="marketopen", help="Open a betting market: !marketopen RiotID | 0.55 | Title | Rationale")
async def cmd_marketopen(ctx, *, spec: str):
    tracked_key, win_prob, title, rationale = _parse_market_spec(spec)
    if not tracked_key:
        await ctx.send("❌ Usage: `!marketopen RiotID | 0.55 | Title | Rationale`")
        return
    if tracked_key not in data.get("tracked", {}):
        await ctx.send(f"❌ Not tracking **{tracked_key}**.")
        return
    linked = None
    if ctx.guild:
        linked = data.get("links", {}).get(str(ctx.author.id))
    if not _is_admin(ctx) and linked != tracked_key:
        await ctx.send("❌ Only the linked owner or an admin can open markets.")
        return

    market, err = await create_market(
        tracked_key=tracked_key,
        title=title,
        creator_id=ctx.author.id,
        win_prob=win_prob,
        rationale=rationale,
        channel_id=ctx.channel.id,
    )
    if err:
        await ctx.send(f"❌ {err}")
        return

    msg = await ctx.send(**market_public_message(market))
    market["channel_id"] = msg.channel.id
    market["message_id"] = msg.id
    data["betting"]["markets"][market["market_id"]]["channel_id"] = msg.channel.id
    data["betting"]["markets"][market["market_id"]]["message_id"] = msg.id
    save_data(data)
    if ctx.bot:
        ctx.bot.add_view(build_market_view(market["market_id"]), message_id=msg.id)


@bot.hybrid_command(name="markets", help="List active betting markets")
async def cmd_markets(ctx):
    markets = list_open_markets()
    if not markets:
        await ctx.send("No active markets.")
        return
    e = discord.Embed(title="🎲 Active Markets", color=0x5865F2)
    lines = []
    for market in markets[:10]:
        lines.append(
            f"`{market['market_id']}` **{market['title']}** — `{market['tracked_key']}` — {market['status'].upper()}"
        )
    e.description = "\n".join(lines)
    await ctx.send(embed=e)


@bot.hybrid_command(name="bet", help="Place a bet: !bet market_id WIN|LOSE stake [y/n]")
async def cmd_bet(ctx, market_id: str, side: str, stake: str, insurance: str | None = None):
    use_insurance = str(insurance or "").strip().lower() in {"y", "yes", "1", "true"}
    bet, err = await place_bet(ctx.author.id, market_id, side, stake, use_insurance)
    if err:
        await ctx.send(f"❌ {err}")
        return
    await ctx.send(
        f"✅ Bet placed on `{market_id}`: **{bet['side']}** {bet['stake']} pts at `{bet['odds']:.2f}`"
    )


@bot.hybrid_command(name="cancelbet", help="Cancel your bet before lock: !cancelbet market_id")
async def cmd_cancelbet(ctx, market_id: str):
    bet, err = await cancel_bet(ctx.author.id, market_id)
    if err:
        await ctx.send(f"❌ {err}")
        return
    await ctx.send(f"✅ Cancelled bet on `{market_id}` and refunded `{bet['stake']}` pts.")


@bot.hybrid_command(name="mybets", help="Show your active bets")
async def cmd_mybets(ctx):
    await ctx.send(embed=active_bets_embed(ctx.author.id))


@bot.hybrid_command(name="leaderboard", help="Show the betting leaderboard")
async def cmd_leaderboard(ctx, metric: str | None = None, range_name: str | None = None):
    await ctx.send(embed=leaderboard_embed(metric or "balance", range_name or "all"))


@bot.hybrid_command(name="profile", help="Show a Sisyphus-observed player profile")
async def cmd_profile(ctx, *, target: str | None = None):
    await ctx.defer()
    key, err = resolve_target_key(ctx, target, "profile")
    if err:
        await ctx.send(err)
        return
    view = player_profile_view(key)
    msg = await ctx.send(embed=view.overview_embed(), view=view)
    view.message = msg


@bot.hybrid_command(name="bprofile", help="Show your betting profile")
async def cmd_bprofile(ctx, *, target: str | None = None):
    user_id = ctx.author.id
    if target:
        mention_id = parse_mention_user_id(target)
        if mention_id:
            user_id = int(mention_id)
    await ctx.send(embed=betting_profile_embed(user_id))


@bot.hybrid_command(name="insurance", help="Show your insurance status")
async def cmd_insurance(ctx, *, target: str | None = None):
    user_id = ctx.author.id
    if target:
        mention_id = parse_mention_user_id(target)
        if mention_id:
            user_id = int(mention_id)
    await ctx.send(embed=insurance_embed(user_id))


@bot.hybrid_command(name="marketbets", help="Show who bet on a market")
async def cmd_marketbets(ctx, market_id: str):
    if not _is_admin(ctx):
        await ctx.send("❌ Admin only.")
        return
    await ctx.send(embed=market_bets_embed(market_id))


@bot.hybrid_command(name="marketstatus", help="Show market status")
async def cmd_marketstatus(ctx):
    if not _is_admin(ctx):
        await ctx.send("❌ Admin only.")
        return
    await ctx.send(embed=market_status_embed())


@bot.hybrid_command(name="audit", help="Show audit log for a user")
async def cmd_audit(ctx, *, target: str | None = None):
    if not _is_admin(ctx):
        await ctx.send("❌ Admin only.")
        return
    user_id = ctx.author.id
    if target:
        mention_id = parse_mention_user_id(target)
        if mention_id:
            user_id = int(mention_id)
    await ctx.send(embed=audit_embed(user_id))


@bot.hybrid_command(name="refund", help="Admin refund points: !refund @user amount [reason]")
@app_commands.default_permissions(manage_guild=True)
async def cmd_refund(ctx, target: str, amount: int, *, reason: str = "ADMIN_ADJUSTMENT"):
    if not _is_admin(ctx):
        await ctx.send("❌ Admin only.")
        return
    mention_id = parse_mention_user_id(target)
    if not mention_id:
        await ctx.send("❌ Target must be a user mention.")
        return
    wallet, err = await admin_refund(int(mention_id), amount, reason=reason)
    if err:
        await ctx.send(f"❌ {err}")
        return
    await ctx.send(f"✅ Refunded `{amount}` pts to <@{mention_id}>.")


@bot.hybrid_command(name="editbet", help="Edit your bet before lock: !editbet market_id WIN|LOSE stake [y/n]")
async def cmd_editbet(ctx, market_id: str, side: str, stake: str, insurance: str | None = None):
    use_insurance = str(insurance or "").strip().lower() in {"y", "yes", "1", "true"}
    bet, err = await edit_bet(ctx.author.id, market_id, side, stake, use_insurance)
    if err:
        await ctx.send(f"❌ {err}")
        return
    await ctx.send(
        f"✅ Bet updated on `{market_id}`: **{bet['side']}** {bet['stake']} pts at `{bet['odds']:.2f}`"
    )


@bot.hybrid_command(name="settlebet", help="Admin settle a market: !settlebet market_id WIN|LOSE")
@app_commands.default_permissions(manage_guild=True)
async def cmd_settlebet(ctx, market_id: str, result: str):
    if not _is_admin(ctx):
        await ctx.send("❌ Admin only.")
        return
    market, err = await settle_market(market_id, result)
    if err:
        await ctx.send(f"❌ {err}")
        return

    # Gather bettors and pings
    bets_dict = data.get("betting", {}).get("bets", {}).get(market_id, {})
    bettors = list(bets_dict.keys())
    pings = " ".join([f"<@{uid}>" for uid in bettors]) if bettors else ""
    content_str = f"🔔 Market Resolution Notification! {pings}" if pings else "🔔 Market Resolution Notification!"

    if market.get("channel_id") and market.get("message_id"):
        channel = ctx.bot.get_channel(market["channel_id"]) or await ctx.bot.fetch_channel(
            market["channel_id"]
        )
        if channel:
            try:
                msg = await channel.fetch_message(market["message_id"])
                await msg.edit(**settled_market_message(market))
                await channel.send(content=content_str, embed=settled_market_message(market)["embed"])
            except Exception:
                pass
    else:
        await ctx.send(content=content_str, embed=settled_market_message(market)["embed"])


@bot.hybrid_command(name="voidbet", help="Admin void a market: !voidbet market_id [reason]")
@app_commands.default_permissions(manage_guild=True)
async def cmd_voidbet(ctx, market_id: str, *, reason: str = "ADMIN_OVERRIDE"):
    if not _is_admin(ctx):
        await ctx.send("❌ Admin only.")
        return
    market, err = await settle_market(market_id, "VOID", reason=reason)
    if err:
        await ctx.send(f"❌ {err}")
        return

    # Gather bettors and pings
    bets_dict = data.get("betting", {}).get("bets", {}).get(market_id, {})
    bettors = list(bets_dict.keys())
    pings = " ".join([f"<@{uid}>" for uid in bettors]) if bettors else ""
    content_str = f"🔔 Market Resolution Notification! {pings}" if pings else "🔔 Market Resolution Notification!"

    if market.get("channel_id") and market.get("message_id"):
        channel = ctx.bot.get_channel(market["channel_id"]) or await ctx.bot.fetch_channel(
            market["channel_id"]
        )
        if channel:
            try:
                msg = await channel.fetch_message(market["message_id"])
                await msg.edit(**settled_market_message(market))
                await channel.send(content=content_str, embed=settled_market_message(market)["embed"])
            except Exception:
                pass
    else:
        await ctx.send(content=content_str, embed=settled_market_message(market)["embed"])


BETTING_COMMAND_NAMES = {
    "wallet",
    "balance",
    "marketopen",
    "markets",
    "bet",
    "cancelbet",
    "mybets",
    "leaderboard",
    "bprofile",
    "insurance",
    "marketbets",
    "marketstatus",
    "audit",
    "refund",
    "editbet",
    "settlebet",
    "voidbet",
}


def unregister_disabled_betting_commands() -> None:
    if BETTING_ENABLED:
        return

    for name in BETTING_COMMAND_NAMES:
        bot.remove_command(name)
        try:
            bot.tree.remove_command(name, type=discord.AppCommandType.chat_input)
        except Exception:
            pass


unregister_disabled_betting_commands()
