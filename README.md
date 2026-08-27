<p align="center">
  <img src="dashboard/web/public/assets/SisyphusStats.png" alt="Sisyphus Stats artwork" width="100%" />
</p>

<p align="center">
  <img src="dashboard/web/public/assets/Sisyphus-Favicon.png" alt="Sisyphus emblem" width="88" />
</p>

<h1 align="center">Sisyphus</h1>

<p align="center">
  A Discord bot for a League of Legends squad. It tracks ranked Solo/Duo games and records the climb.
</p>

<p align="center">
  <a href="https://github.com/BurntDosa/Sisyphus-Stats"><img src="https://img.shields.io/badge/source-public%20mirror-A0283B?style=for-the-badge" alt="Public source mirror" /></a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-221A17?style=for-the-badge" alt="Python 3.11 or later" />
  <img src="https://img.shields.io/badge/Discord-guild%20bot-5865F2?style=for-the-badge" alt="Discord guild bot" />
  <a href="https://sisyphus.burntdosa.site"><img src="https://img.shields.io/badge/dashboard-private-496B55?style=for-the-badge" alt="Private dashboard" /></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> |
  <a href="#what-sisyphus-records">Features</a> |
  <a href="#commands">Commands</a> |
  <a href="#private-dashboard">Dashboard</a> |
  <a href="#development">Development</a>
</p>

<p align="center"><strong>Track matches. Read recaps. Keep the record.</strong></p>

---

<table>
  <tr>
    <td width="50%" valign="top">
      <h3>Match record</h3>
      <p>Read ranked Solo/Duo recaps with LP, KDA, CS per minute, vision, items, and team scores.</p>
    </td>
    <td width="50%" valign="top">
      <h3>Squad record</h3>
      <p>Use Live Game Room, Queue Board, recaps, rivalries, goals, and the Boulder Archive.</p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h3>Player record</h3>
      <p>Follow rank changes, role and champion history, saved memories, and recent match form.</p>
    </td>
    <td width="50%" valign="top">
      <h3>Private analytics</h3>
      <p>Sign in with Discord to view player, points-market, and community data in the dashboard.</p>
    </td>
  </tr>
</table>

<p align="center">
  <img src="dashboard/web/public/assets/ranks/emblem-bronze.png" alt="Bronze rank emblem" width="72" />
  <img src="dashboard/web/public/assets/ranks/emblem-silver.png" alt="Silver rank emblem" width="72" />
  <img src="dashboard/web/public/assets/ranks/emblem-gold.png" alt="Gold rank emblem" width="72" />
  <img src="dashboard/web/public/assets/ranks/emblem-platinum.png" alt="Platinum rank emblem" width="72" />
  <img src="dashboard/web/public/assets/ranks/emblem-emerald.png" alt="Emerald rank emblem" width="72" />
  <img src="dashboard/web/public/assets/ranks/emblem-diamond.png" alt="Diamond rank emblem" width="72" />
</p>

---

## What Sisyphus Does

Sisyphus watches tracked Riot accounts for ranked Solo/Duo matches. It posts a recap after each new game.

Each recap can show the result, champion, role, duration, rank, and LP change.

It can also show KDA, CS per minute, damage, vision, gold, items, and team score.

The bot keeps daily LP history and shared server records. It ignores ARAM, Flex, normal games, and other queues.

It also gives the squad a private dashboard. The dashboard shows player journeys, recent form, points markets, and community records.

## What Sisyphus Records

| Area | Sisyphus records |
| --- | --- |
| Match recaps | Result, champion, role, duration, rank, LP, KDA, CS per minute, vision, damage, gold, items, and scores. |
| Player history | Ranked Solo/Duo matches, daily LP points, current rank, peak rank, role, champion pool, and saved memories. |
| Squad history | Daily reports, weekly recaps, monthly recaps, rivalries, squad goals, and Boulder Archive records. |
| Live game room | Queue Beacon updates, linked-player notices, stream state, and watch intent. |
| Points markets | Wallets, open markets, bets, settlements, refunds, insurance, and leaderboards. This uses fake server points only. |
| Service health | Bot availability, Discord gateway state, polling freshness, Riot live-game checks, OP.GG match data, and points-market health. |

## Highlights

### Match Recaps

Sisyphus posts one recap for each new ranked Solo/Duo match. Use the recap buttons to read the match overview, blue team, red team, or full scoreboard.

Games shorter than two minutes count as remakes. Sisyphus records them as draws and voids linked points markets.

### Player Journeys

`/profile` shows Sisyphus-observed ranked history. It includes Overview, Journey, Identity, Records, and Memories pages.

Linked players can save a personal note on a recap. The note becomes a match memory.

### Squad Rituals

Queue Beacon acts as a Live Game Room. It updates one message as a tracked player enters and leaves a ranked game.

The Queue Board helps members find ranked Solo/Duo partners. Weekly and monthly recaps give the server a clear record of its progress.

### Points Markets

Sisyphus can open fake-points markets for tracked ranked games. Members can place win or loss bets before a market locks.

Members can edit or cancel a bet before lock. They can also use a weekly insurance token on a normal bet.

All-in bets use the full available wallet balance. All-in bets cannot use insurance.

## Quick Start

### 1. Get the source

```bash
git clone https://github.com/BurntDosa/Sisyphus-Stats.git
cd Sisyphus-Stats
```

### 2. Install the Python environment

```bash
uv sync
```

### 3. Create the private configuration file

```bash
cp env.example_v2 .env
```

Set the required values in `.env`.

| Variable | Purpose |
| --- | --- |
| `DISCORD_TOKEN` | The Discord bot token. Enable Message Content Intent for this bot. |
| `THREAD_ID` or `CHANNEL_ID` | The Discord thread or channel that receives posts. `THREAD_ID` has priority. |
| `GUILD_ID` | The Discord server ID for guild-local commands. |
| `ADMIN_IDS` | A comma-separated list of Discord user IDs that can use admin commands. |
| `RIOT_KEY` | The Riot API key for live-game checks. |
| `OPGG_MCP_URL` and `OPGG_REGION` | The OP.GG MCP endpoint and the region for match data. |

Use `env.example_v2` for the complete configuration list. Never commit `.env`, `data.json`, push URLs, or SSH keys.

### 4. Start the bot

```bash
uv run python -m sisyphus
```

The bot creates or updates `data.json` in the project root. This file is the private runtime state.

Before you change or restore this file, make a timestamped backup. Do not commit it.

## Commands

Most commands work as Discord slash commands and with the `!` prefix.

| Group | Commands | Use |
| --- | --- | --- |
| Start here | `/help`, `/status`, `/dashboard` | Show the command guide, service health, or the private dashboard link. |
| Track players | `/track`, `/untrack`, `/list`, `/link`, `/unlink`, `/whoami` | Track Riot accounts and link Discord members to tracked accounts. |
| Read matches | `/recap`, `/stats`, `/profile`, `/dailyreport`, `/report` | Read recaps, rank data, player history, daily reports, or submit a report. |
| Squad tools | `/queueup`, `/queueboard`, `/queueclear`, `/weeklyrecap`, `/monthlyrecap`, `/halloffame`, `/squadgoal`, `/rivalry` | Run the shared ranked queue and record squad progress. |
| Points markets | `/markets`, `/bet`, `/editbet`, `/cancelbet`, `/mybets`, `/wallet`, `/leaderboard`, `/bprofile`, `/insurance` | Use fake-points markets and wallet tools. |
| Admin tools | `/marketopen`, `/marketstatus`, `/marketbets`, `/settlebet`, `/voidbet`, `/refund`, `/audit` | Manage points markets and audit their changes. |

Use `/help` in Discord for command arguments and current access rules.

## Private Dashboard

The dashboard runs at [sisyphus.burntdosa.site](https://sisyphus.burntdosa.site).

Members sign in through Discord. The dashboard checks that each user belongs to the configured Discord server.

The dashboard has four views:

| View | Shows |
| --- | --- |
| Overview | Squad LP lines, recent form, activity, and headline records. |
| Players | Player rank, LP history, match form, champions, roles, and match records. |
| Betting | Wallet leaderboards, market volume, outcomes, markets, and bet results. |
| Community | Records, milestones, memories, recap notes, and squad-goal progress. |

The dashboard loads a new export every five minutes. Members can also refresh it manually.

The Mac keeps the source `data.json`. A local export task sends a selected, sanitized data set to the dashboard server.

The export excludes PUUIDs, Discord IDs, channel and message IDs, reports, audit actors, recap links, secrets, and raw `data.json`.

## Service Status

`/status` and `!status` show the current service state in Discord.

The bot writes a private health snapshot. A separate macOS task sends outbound heartbeats to Uptime Kuma.

The status checks cover bot availability, Discord, ranked polling, Riot live-game detection, OP.GG match data, and points markets.

Set `STATUS_PAGE_ENABLED=true` only after you configure all required Uptime Kuma push URLs. Treat every push URL as a secret.

## Region Settings

Set `OPGG_REGION` to one of these values.

| Region | Value |
| --- | --- |
| Korea | `KR` |
| North America | `NA` |
| Europe West | `EUW` |
| Europe Nordic and East | `EUNE` |
| South East Asia | `SEA` |

Riot routing is separate from OP.GG routing. The normal settings for this server are shown in `env.example_v2`.

## Data, Backups, and Privacy

`data.json` contains private server state. It can include tracked players, linked accounts, match history, LP points, and community records.

It can also include points-market data, audit entries, and changelog state.

Keep this file outside Git. Keep it on the Mac that runs the bot.

The private operator project includes `scripts/automation/sisyphus-data-backup.py` for backup validation.

Its macOS LaunchAgent can keep daily backups under `backups/data-json/`.

## Development

Run these checks before you commit a change.

```bash
uv run python -m py_compile sisyphus/*.py scripts/smoke_embed_limits.py
uv run python scripts/smoke_embed_limits.py
uv run python scripts/smoke_status_health.py
uv run python scripts/smoke_process_lock.py
uv run python scripts/smoke_profile_commands.py
uv run python scripts/smoke_profile_rank.py
uv run python scripts/smoke_duplicate_events.py
uv run python scripts/smoke_changelog.py
uv run python scripts/smoke_dashboard_export.py
uv run --directory dashboard python ../scripts/smoke_dashboard_auth.py
npm --prefix dashboard/web run typecheck
npm --prefix dashboard/web run build
```

Use `uv` for Python dependency changes. Keep the bot state files out of Git.

Do not start a second bot process when the supervisor is active. Ask the supervisor to restart the managed process.

## Public Source Mirror

This repository is the public source mirror for the private Sisyphus project. A GitHub Actions workflow updates it after a private `main` branch push.

The workflow copies source code, templates, checks, and dashboard code.

It does not copy `.env`, `data.json`, backups, runtime logs, deployment files, private IDs, or secrets.

Do not use this repository as a source of live server state. Make operational changes in the private project.

## Visual Assets That Would Help

The existing hero artwork is in use. These assets would make the README easier to scan:

1. A Discord recap screenshot at `1600 x 1000` pixels. Show one completed ranked Solo/Duo recap with the overview buttons. Blur or replace member names and IDs.
2. A dashboard Overview screenshot at `1600 x 1000` pixels. Show the squad LP chart, the record list, and the left navigation rail.
3. A dashboard Players screenshot at `1600 x 1000` pixels. Show a rank emblem, LP journey, and the collapsed recent-match section.
4. An optional 8 to 12 second GIF at `1440 x 900` pixels. Show `/track`, a new recap, and the dashboard refresh. Keep the file below `10 MB`.

Put still images in `docs/assets/`. Put the GIF in `docs/assets/sisyphus-flow.gif`.
