"""Live SG apex-rank cutoff snapshots from Replays.lol."""
from __future__ import annotations

import re

import aiohttp

from .ranks import set_apex_cutoffs
from .state import data, save_data
from .utils import now_ist

APEX_CUTOFF_REGION = "SG"
APEX_CUTOFF_SOURCE = "https://www.replays.lol/cutoff/SG"
_CUTOFF_PATTERN = re.compile(r"You need\s*([\d,]+)\s*LP", re.IGNORECASE)


def parse_cutoff_lp(page: str) -> int | None:
    """Extract the visible current cutoff from a Replays.lol cutoff page."""
    text = re.sub(r"<[^>]+>", " ", page or "")
    text = re.sub(r"\s+", " ", text)
    match = _CUTOFF_PATTERN.search(text)
    if not match:
        return None
    try:
        value = int(match.group(1).replace(",", ""))
    except ValueError:
        return None
    return value if value > 0 else None


def load_saved_apex_cutoffs() -> dict | None:
    snapshot = data.get("apex_cutoffs")
    if not isinstance(snapshot, dict):
        return None
    challenger = snapshot.get("challenger")
    grandmaster = snapshot.get("grandmaster")
    if not isinstance(challenger, int) or not isinstance(grandmaster, int):
        return None
    if challenger <= grandmaster or grandmaster <= 0:
        return None
    set_apex_cutoffs(challenger=challenger, grandmaster=grandmaster)
    return snapshot


def tier_for_apex_lp(lp: int) -> str | None:
    """Classify current raw LP with the newest retained SG cutoff snapshot."""
    snapshot = data.get("apex_cutoffs")
    if not isinstance(snapshot, dict):
        return None
    challenger = snapshot.get("challenger")
    grandmaster = snapshot.get("grandmaster")
    if not isinstance(challenger, int) or not isinstance(grandmaster, int):
        return None
    if challenger <= grandmaster or grandmaster <= 0:
        return None
    if lp >= challenger:
        return "CHALLENGER"
    if lp >= grandmaster:
        return "GRANDMASTER"
    return "MASTER"


async def refresh_sg_apex_cutoffs(session: aiohttp.ClientSession) -> bool:
    """Fetch and atomically retain the latest valid SG Grandmaster/Challenger cutoffs."""
    urls = {
        "challenger": f"{APEX_CUTOFF_SOURCE}/challenger",
        "grandmaster": f"{APEX_CUTOFF_SOURCE}/grandmaster",
    }
    values: dict[str, int] = {}
    try:
        for tier, url in urls.items():
            async with session.get(url, headers={"User-Agent": "Sisyphus-Bot apex-cutoff monitor"}, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    print(f"[apex] {tier} cutoff fetch returned HTTP {response.status}")
                    return False
                cutoff = parse_cutoff_lp(await response.text())
                if cutoff is None:
                    print(f"[apex] {tier} cutoff page did not contain a valid LP value")
                    return False
                values[tier] = cutoff
    except (aiohttp.ClientError, TimeoutError) as exc:
        print(f"[apex] cutoff fetch failed: {type(exc).__name__}")
        return False

    challenger = values["challenger"]
    grandmaster = values["grandmaster"]
    if challenger <= grandmaster:
        print("[apex] rejected invalid cutoff snapshot: Challenger must exceed Grandmaster")
        return False

    snapshot = {
        "region": APEX_CUTOFF_REGION,
        "challenger": challenger,
        "grandmaster": grandmaster,
        "source": APEX_CUTOFF_SOURCE,
        "fetched_at": now_ist().isoformat(),
    }
    data["apex_cutoffs"] = snapshot
    set_apex_cutoffs(challenger=challenger, grandmaster=grandmaster)
    save_data(data)
    print(f"[apex] SG cutoffs refreshed: Grandmaster {grandmaster} LP, Challenger {challenger} LP")
    return True
