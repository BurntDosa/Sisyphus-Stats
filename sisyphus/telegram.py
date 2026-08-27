import asyncio
import re
from pathlib import Path
import aiohttp

# In-memory variable to track if Telegram integration is configured
TELEGRAM_ENABLED = False
TELEGRAM_BOT_TOKEN = None
TELEGRAM_CHAT_ID = None

# Load credentials from .env
PROJECT_ROOT = Path(__file__).resolve().parents[1]
env_path = PROJECT_ROOT / ".env"

if env_path.exists():
    with open(env_path, "r") as f:
        content = f.read()
        
    token_match = re.search(r"^TELEGRAM_BOT_TOKEN\s*=\s*(.+)$", content, re.MULTILINE)
    chat_match = re.search(r"^TELEGRAM_CHAT_ID\s*=\s*(.+)$", content, re.MULTILINE)
    
    if token_match and chat_match:
        TELEGRAM_BOT_TOKEN = token_match.group(1).strip()
        TELEGRAM_CHAT_ID = chat_match.group(1).strip()
        TELEGRAM_ENABLED = True
        print(f"[telegram] Initialized with chat_id={TELEGRAM_CHAT_ID}")
    else:
        print("[telegram] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in .env")
else:
    print("[telegram] .env file not found")


async def send_telegram_notification(text: str):
    """Send a notification message to the configured Telegram Chat ID."""
    if not TELEGRAM_ENABLED or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    return True
                else:
                    body = await response.text()
                    print(f"[telegram] Failed to send notification (HTTP {response.status}): {body}")
                    return False
    except Exception as e:
        print(f"[telegram] Error sending notification: {e}")
        return False


def _update_env_key(new_key: str):
    """Write the new Riot key to the .env file."""
    try:
        if not env_path.exists():
            return False
            
        with open(env_path, "r") as f:
            lines = f.readlines()
            
        key_found = False
        for idx, line in enumerate(lines):
            if line.strip().startswith("RIOT_KEY="):
                lines[idx] = f"RIOT_KEY={new_key}\n"
                key_found = True
                break
                
        if not key_found:
            lines.append(f"\nRIOT_KEY={new_key}\n")
            
        with open(env_path, "w") as f:
            f.writelines(lines)
            
        return True
    except Exception as e:
        print(f"[telegram] Error writing new key to .env: {e}")
        return False


async def poll_telegram_updates():
    """Poll Telegram getUpdates to check for messages containing a new Riot API key."""
    if not TELEGRAM_ENABLED or not TELEGRAM_BOT_TOKEN:
        print("[telegram] Polling disabled due to missing configuration.")
        return
        
    print("[telegram] Starting polling loop...")
    offset = 0
    
    # Reuse a single ClientSession for connection pooling and resource efficiency
    async with aiohttp.ClientSession() as session:
        # Pre-fetch the latest update ID to avoid processing historical messages
        url_updates = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        try:
            async with session.get(url_updates) as r:
                if r.status == 200:
                    body = await r.json()
                    updates = body.get("result", [])
                    if updates:
                        offset = updates[-1]["update_id"] + 1
        except Exception as e:
            print(f"[telegram] Initial update fetch failed: {e}")

        # Main update checking loop
        while True:
            try:
                # Use long-polling with 30s timeout on Telegram's servers
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=30"
                async with session.get(url) as response:
                    if response.status == 200:
                        body = await response.json()
                        updates = body.get("result", [])
                        
                        for update in updates:
                            offset = update["update_id"] + 1
                            message = update.get("message") or update.get("edited_message")
                            if not message:
                                continue
                                
                            chat_id = str(message.get("chat", {}).get("id"))
                            if chat_id != TELEGRAM_CHAT_ID:
                                # Only respond to the configured developer
                                continue
                                
                            text = (message.get("text") or "").strip()
                            # Match Riot API key format (RGAPI followed by hex/uuid character blocks)
                            if re.match(r"^RGAPI-[0-9a-fA-F\-]+$", text):
                                print(f"[telegram] Received new Riot API Key from Telegram!")
                                
                                # Update configurations
                                success = _update_env_key(text)
                                if success:
                                    # Update live configuration variables in-memory
                                    import sisyphus.config as config
                                    
                                    config.RIOT_KEY = text
                                    import sisyphus.opgg as opgg
                                    if hasattr(opgg, "HEADERS"):
                                        opgg.HEADERS["X-Riot-Token"] = text
 
                                    # Update timestamp and reset alert in state database
                                    from sisyphus.state import data, save_data
                                    from sisyphus.utils import now_ist
                                    if "betting" not in data:
                                        data["betting"] = {}
                                    data["betting"]["riot_key_updated_at"] = now_ist().isoformat()
                                    data["betting"]["riot_key_expiry_alert_sent"] = False
                                    data["betting"]["riot_key_403_alert_sent"] = False
                                    save_data(data)
                                        
                                    await send_telegram_notification(
                                        "✅ *Success!* Riot API key updated inside `.env` and loaded in memory with zero-downtime!"
                                    )
                                else:
                                    await send_telegram_notification(
                                        "❌ *Error:* Failed to update `.env` file, please check bot logs."
                                    )
                            elif text.lower() == "/status":
                                from .config import RIOT_KEY as current_key
                                from .changelog import get_git_version
                                version = get_git_version()
                                masked_key = f"{current_key[:9]}...{current_key[-6:]}" if len(current_key) > 15 else "None"
                                await send_telegram_notification(
                                    f"🤖 *Sisyphus Status*\n"
                                    f"• Version: `{version}`\n"
                                    f"• Telegram: Connected\n"
                                    f"• Active Key: `{masked_key}`"
                                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[telegram] Polling error: {e}")
                # Wait 10 seconds before retrying on general errors (e.g. network disconnect)
                await asyncio.sleep(10)
                continue
                
            # Long-polling naturally pauses execution on the socket; we use a tiny 0.5s pause
            # to prevent CPU spikes in any unexpected hot-loop scenarios.
            await asyncio.sleep(0.5)
