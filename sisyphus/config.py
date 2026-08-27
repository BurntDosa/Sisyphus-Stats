"""Environment configuration and shared constants."""
from __future__ import annotations

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
THREAD_ID = int(os.getenv("THREAD_ID", "0"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
DESTINATION_ID = THREAD_ID or CHANNEL_ID

OPGG_MCP_URL = os.getenv("OPGG_MCP_URL", "https://mcp-api.op.gg/mcp")
OPGG_REGION = os.getenv("OPGG_REGION", "SEA").upper()
BACKFILL_DAYS = int(os.getenv("BACKFILL_DAYS", "3"))
LP_RECONCILE_DELAY_MINUTES = int(os.getenv("LP_RECONCILE_DELAY_MINUTES", "10"))
# Don't post recap embeds for matches older than this. After downtime, the
# poll loop advances last_match_id past stale matches silently rather than
# flooding the channel with a backlog.
MAX_RECAP_AGE_HOURS = int(os.getenv("MAX_RECAP_AGE_HOURS", "6"))
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
BETTING_ENABLED = os.getenv("BETTING_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
TELEGRAM_POLLING_ENABLED = os.getenv("TELEGRAM_POLLING_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
RIOT_KEY_DAILY_REMINDER_ENABLED = os.getenv(
    "RIOT_KEY_DAILY_REMINDER_ENABLED", "false"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

POLL_INTERVAL = 30  # seconds — keep ≥ 20 to respect rate limits

IST = ZoneInfo("Asia/Kolkata")
DATA_FILE = "data.json"

def _env_discord_id(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    return int(value) if value.isdigit() else None


# Keep server-specific identifiers in the private environment rather than in
# source control. An empty value disables the matching optional integration.
ADMIN_IDS: set[int] = set()
_admin_ids_env = os.getenv("ADMIN_IDS")
if _admin_ids_env:
    for _val in _admin_ids_env.split(","):
        _val = _val.strip()
        if _val.isdigit():
            ADMIN_IDS.add(int(_val))
DEVELOPER_DISCORD_ID = _env_discord_id("DEVELOPER_DISCORD_ID")
MARKET_ROLE_ID = _env_discord_id("MARKET_ROLE_ID")

# Riot Games API Config
RIOT_KEY = os.getenv("RIOT_KEY")
PLATFORM = os.getenv("PLATFORM", "sg2").lower()
REGION = os.getenv("REGION", "asia").lower()
if REGION == "sea":
    REGION = "asia" # Fallback regional route to 'asia' as dev keys are restricted to 'asia' for regional calls
_platforms_env = os.getenv("RIOT_PLATFORMS")
if _platforms_env:
    RIOT_PLATFORMS = [
        platform.strip().lower()
        for platform in _platforms_env.split(",")
        if platform.strip()
    ]
elif OPGG_REGION == "SEA":
    RIOT_PLATFORMS = ["sg2"]
else:
    RIOT_PLATFORMS = [PLATFORM]
if PLATFORM not in RIOT_PLATFORMS:
    RIOT_PLATFORMS.insert(0, PLATFORM)

# Mistral AI Config
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
MISTRAL_TIMEOUT_SECONDS = int(os.getenv("MISTRAL_TIMEOUT_SECONDS", "5"))
APP_VERSION = os.getenv("APP_VERSION", "2.1.9")
STATUS_PAGE_ENABLED = os.getenv("STATUS_PAGE_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
STATUS_PAGE_URL = os.getenv("STATUS_PAGE_URL", "").strip()
STATUS_HEALTH_MAX_AGE_SECONDS = int(os.getenv("STATUS_HEALTH_MAX_AGE_SECONDS", "180"))
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "").strip()
