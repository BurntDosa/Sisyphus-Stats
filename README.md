# Sisyphus' Daily Data

Sisyphus is a Discord bot for a friend server that follows ranked League of
Legends Solo/Duo games, turns them into readable recaps, and adds a few rituals
around the climb.

The bot is intentionally server-first. It is not trying to be OP.GG inside
Discord, and it is not trying to scout enemies or shame players. It watches the
people the server cares about, posts useful post-game context, keeps daily and
weekly memory, and lets friends make the grind a little more social.

## What The Bot Does

When a tracked player finishes a ranked Solo/Duo game, Sisyphus posts a match
recap with the same core data people already look for:

- result, champion, role, duration, rank, LP, and LP change
- KDA, CS/min, damage, vision, gold, level, items, and team score
- kill participation, damage share, and gold share when available
- buttons for overview, blue team, red team, and the full scoreboard

Short remakes are treated as draws. Betting markets for remakes are voided and
all points are refunded.

The bot also posts a daily report at 00:05 IST for the day that just ended.
Daily reports summarize games played, wins/losses/draws, net LP, previous LP,
current LP, and the player's recent ranked history.

## Server Rituals

Sisyphus has a small community layer for ranked Solo/Duo:

- Queue Beacon announces when tracked friends enter a ranked game.
- The bot status cycles through live tracked players as
  `<player> is pushing the boulder`.
- Queue Board lets people say they are looking for ranked Solo/Duo.
- Weekly Squad Recap highlights positive server-wide moments.
- Squad Goals track weekly group goals like wins, games, unique champions, and
  positive LP days.
- Opt-in Rivalries let two linked users compare weekly ranked LP, wins, and
  games after both agree.
- Boulder Archive keeps server records such as best damage, KDA, vision, CS/min,
  fastest win, longest win, and biggest LP gain.

These features only use ranked Solo/Duo history. ARAM, Flex, normals, remakes,
and non-ranked queues are ignored for community records.

## Points Markets

The bot can also run fake-points markets for tracked ranked games when
`BETTING_ENABLED=true`.

Markets open when tracked players enter ranked Solo/Duo, lock shortly after,
and settle when the game result is known. Users can bet on win/loss with server
points, use a weekly insurance token on normal bets, or press all-in to stake
their full available balance. All-in bets cannot use insurance.

This is fake server currency only.

## Requirements

- Python 3.11+
- `uv`
- a Discord bot token with Message Content Intent enabled
- OP.GG MCP endpoint, usually `https://mcp-api.op.gg/mcp`
- Riot API key for live-game detection and small enrichments such as champion
  mastery

## Setup

Install dependencies:

```bash
uv sync
```

Create `.env`:

```bash
cp env.example_v2 .env
```

Important variables:

```dotenv
DISCORD_TOKEN=your_discord_bot_token
THREAD_ID=discord_thread_id
CHANNEL_ID=discord_channel_id
OPGG_MCP_URL=https://mcp-api.op.gg/mcp
OPGG_REGION=SEA
RIOT_KEY=RGAPI-your_key
PLATFORM=sg2
RIOT_PLATFORMS=sg2
REGION=sea
ADMIN_IDS=your_discord_user_id
DEVELOPER_DISCORD_ID=your_discord_user_id
MARKET_ROLE_ID=your_discord_role_id
BETTING_ENABLED=true
```

`THREAD_ID` wins over `CHANNEL_ID` when both are set.

Optional integrations:

```dotenv
MISTRAL_API_KEY=your_mistral_key
MISTRAL_MODEL=mistral-small-latest
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
TELEGRAM_POLLING_ENABLED=false
RIOT_KEY_DAILY_REMINDER_ENABLED=false
```

Mistral is used for startup release-note summaries. Telegram polling and the old
daily Riot key reminder are off by default.

## Run

```bash
uv run python -m sisyphus
```

The bot can also run from an existing virtualenv:

```bash
.venv/bin/python -m sisyphus
```

`data.json` lives in the project root. It stores tracked players, links, match
history, daily LP snapshots, community records, betting wallets, markets, audit
logs, and changelog state. It is git-ignored and should be preserved across
restarts.

## Common Commands

Most commands work as slash commands and as `!` prefix commands.

Tracking:

- `/track GameName#TAG`
- `/untrack GameName#TAG`
- `/list`
- `/link GameName#TAG`
- `/unlink`
- `/whoami`

Reports:

- `/recap [GameName#TAG or @user]`
- `/stats [GameName#TAG or @user]`
- `/profile [GameName#TAG or @user]`
- `/dailyreport [GameName#TAG or @user]`
- `/report`
- `/status`
- `/dashboard`
- `/help`

Community:

- `/queueup [note]`
- `/queueboard`
- `/queueclear`
- `/weeklyrecap`
- `/monthlyrecap [YYYY-MM]` (admin)
- `/halloffame`
- `/squadgoal`
- `/squadgoal set <wins|games|positive_lp_days|unique_champions|streak> [target]`
- `/rivalry challenge @user`
- `/rivalry accept @user`
- `/rivalry end @user`

Points markets:

- `/markets`
- `/bet <market_id> WIN|LOSE <stake> [insurance]`
- `/editbet <market_id> WIN|LOSE <stake> [insurance]`
- `/cancelbet <market_id>`
- `/mybets`
- `/wallet [@user]`
- `/leaderboard [metric] [range]`
- `/bprofile [@user]`
- `/insurance [@user]`

Admin-only betting tools include `/marketopen`, `/marketstatus`, `/marketbets`,
`/settlebet`, `/voidbet`, `/refund`, and `/audit`.

## v2 Discord UX

Queue Beacon now acts as a Live Game Room: one message is posted when a tracked
ranked Solo/Duo game starts, pings linked tracked players in normal message
content, and edits in place as stream status, voice-channel watchers, and
watcher betting intent change.

`/profile` is now the Sisyphus-observed player profile with Overview, Journey,
Identity, Records, and Memories pages. The old betting profile moved to
`/bprofile`. Fresh recap cards include a Remember button so the linked player
can name and save personally meaningful matches.

Monthly recaps run at 06:00 IST on the 1st of each month for the month that just
ended. They post a public server recap and DM eligible linked players who had at
least one tracked ranked Solo/Duo game that month.

## v2.1 Service Status

Sisyphus writes a private service-health snapshot to
`.automation/status-health.json`. An independent macOS LaunchAgent publishes
outbound heartbeats to Uptime Kuma for bot availability, Discord connectivity,
ranked polling, Riot live detection, OP.GG match data, and points markets.

The integration is disabled until `STATUS_PAGE_ENABLED=true` and the six
`UPTIME_KUMA_*_PUSH_URL` values are configured in `.env`. Push URLs are secrets:
never post them in Discord or commit them. `/status` and `!status` show the same
service-level health inside Discord and link to `STATUS_PAGE_URL` when enabled.

The Oracle deployment bundle and setup instructions live under
`deploy/uptime-kuma/`. Uptime Kuma should run independently from the Mac so a
missed heartbeat still records a bot or home-network outage.

## v2.1.6 Analytics Dashboard

The authenticated dashboard is served at `https://sisyphus.burntdosa.site` after
the Oracle deployment is configured. It accepts Discord OAuth sign-in with the
`identify guilds` scopes and only allows members of the configured Sisyphus
guild. `/dashboard` and `!dashboard` link to the Overview, Players, Betting,
and Community views.

The Mac remains the only authoritative host for `data.json`. Every five minutes
`scripts/automation/sisyphus-dashboard-export.py` creates an allow-listed export
and uploads it atomically over SSH. The export uses HMAC-derived member keys,
resolves wallet names locally, and excludes PUUIDs, raw Discord IDs, message and
channel IDs, reports, audit actors, recap URLs, secrets, and the source JSON.
The export and SSH upload stay disabled until `DASHBOARD_EXPORT_SECRET`,
`DASHBOARD_SSH_TARGET`, `DASHBOARD_SSH_KEY`, and
`DASHBOARD_EXPORT_ENABLED=true` are configured in `.env`.

The Oracle service bundle lives under `dashboard/deploy/`. It keeps FastAPI on
`127.0.0.1:3002`, keeps Uptime Kuma on `127.0.0.1:3001`, and exposes the
dashboard only through Nginx on HTTPS. OAuth and session placeholders belong in
the private Oracle file based on `dashboard/.env.example`; do not put OAuth
secrets in the Mac bot configuration.

## Region Notes

`OPGG_REGION` must be uppercase:

| Region | Value |
|---|---|
| Korea | `KR` |
| North America | `NA` |
| Europe West | `EUW` |
| Europe Nordic & East | `EUNE` |
| South East Asia | `SEA` |

Riot routing is separate from OP.GG routing. For this server, the normal Riot
settings are:

```dotenv
PLATFORM=sg2
RIOT_PLATFORMS=sg2
REGION=sea
```

`REGION=sea` is normalized to Riot's `asia` regional route in code.

## Automation And Backups

`scripts/automation/sisyphus-data-backup.py` validates and backs up `data.json`.
The macOS LaunchAgent `com.gagan.sisyphus.data-daily` can run it daily and keep
the latest 14 daily backups under `backups/data-json/`.

The Riot key auto-renewal scripts still exist in `scripts/automation`, but the
launchd renewer and daily Telegram reminder are not part of the default runtime.
Use them only when you intentionally want that workflow back.

## Development Notes

There is no full test suite, but there is a non-network smoke check for embed
limits and important behavior:

```bash
uv run python -m py_compile sisyphus/*.py scripts/smoke_embed_limits.py
uv run python scripts/smoke_embed_limits.py
uv run python scripts/smoke_status_health.py
uv run python scripts/smoke_process_lock.py
uv run python scripts/smoke_profile_rank.py
uv run python scripts/smoke_changelog.py
uv run python scripts/smoke_dashboard_export.py
uv run --directory dashboard python ../scripts/smoke_dashboard_auth.py
npm --prefix dashboard/web run typecheck
npm --prefix dashboard/web run build
```

Patch versions should move forward for normal commits. Major and minor versions
only move when the maintainer explicitly asks for them.
