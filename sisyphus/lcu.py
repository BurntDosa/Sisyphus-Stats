"""Read the local League client (LCU) to build a /teams roster. No Riot API key.

Only works when the bot runs on the same PC as the League client, with you
sitting in a custom lobby. The LCU is local and unofficial, so field names can
change between client patches.
"""
from __future__ import annotations

import base64
import json
import os
import re
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

UNRANKED_DEFAULT = "s4"  # rating used for unranked players (Silver IV)

_CODES = {
    "IRON": "i", "BRONZE": "b", "SILVER": "s", "GOLD": "g", "PLATINUM": "p",
    "EMERALD": "e", "DIAMOND": "d", "MASTER": "m", "GRANDMASTER": "gm", "CHALLENGER": "c",
}
_APEX = {"MASTER", "GRANDMASTER", "CHALLENGER"}
_DIV = {"I": "1", "II": "2", "III": "3", "IV": "4"}
_DEFAULT_DIRS = (r"C:\Riot Games\League of Legends", r"D:\Riot Games\League of Legends")

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE  # the client uses a self-signed local certificate


class LcuError(ValueError):
    """Raised with a user-readable message; ValueError so /teams-style handlers catch it."""


def _find_lockfile() -> Path:
    env = os.environ.get("LOL_LOCKFILE")
    if env and Path(env).is_file():
        return Path(env)
    candidates: list[Path] = []
    if os.environ.get("LOL_DIR"):
        candidates.append(Path(os.environ["LOL_DIR"]) / "lockfile")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"name='LeagueClientUx.exe'\" "
             "| Select-Object -First 1).ExecutablePath"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
        if out:
            candidates.append(Path(out).parent / "lockfile")
    except (OSError, subprocess.SubprocessError):
        pass
    candidates += [Path(d) / "lockfile" for d in _DEFAULT_DIRS]
    for path in candidates:
        if path.is_file():
            return path
    raise LcuError(
        "Couldn't find the League client. Open the client, or set `LOL_DIR` to its install folder."
    )


class _Client:
    def __init__(self) -> None:
        try:
            parts = _find_lockfile().read_text().strip().split(":")
            self.port, token = parts[2], parts[3]
        except (OSError, IndexError) as exc:
            raise LcuError(f"Couldn't read the League client lockfile ({exc}).") from exc
        auth = base64.b64encode(f"riot:{token}".encode()).decode()
        self._headers = {"Authorization": f"Basic {auth}"}

    def get(self, path: str):
        req = urllib.request.Request(f"https://127.0.0.1:{self.port}{path}", headers=self._headers)
        try:
            with urllib.request.urlopen(req, context=_CTX, timeout=5) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            raise LcuError(f"League client returned {exc.code} for {path}.") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise LcuError("League client isn't reachable. Is it open?") from exc


def _rank_code(client: _Client, puuid: str | None) -> str:
    if not puuid:
        return UNRANKED_DEFAULT
    try:
        stats = client.get(f"/lol-ranked/v1/ranked-stats/{puuid}")
        solo = (stats.get("queueMap") or {}).get("RANKED_SOLO_5x5") or {}
    except LcuError:
        return UNRANKED_DEFAULT
    tier = str(solo.get("tier") or "").upper()
    if tier in _APEX:
        return f"{_CODES[tier]}{int(solo.get('leaguePoints') or 0)}"
    if tier in _CODES and solo.get("division") in _DIV:
        return f"{_CODES[tier]}{_DIV[solo['division']]}"
    return UNRANKED_DEFAULT


def _member_name(client: _Client, member: dict) -> str:
    name = member.get("gameName") or member.get("summonerName")
    puuid = member.get("puuid")
    if not name and puuid:
        try:
            name = client.get(f"/lol-summoner/v2/summoners/puuid/{puuid}").get("gameName")
        except LcuError:
            name = None
    return re.sub(r"[,;:=\r\n]", "", name or "").strip()


def lobby_roster_text() -> str:
    """Blocking. Returns 'Name=g2, Name2=m150, ...' ready for teams.make_teams_message."""
    client = _Client()
    try:
        lobby = client.get("/lol-lobby/v2/lobby")
    except LcuError as exc:
        raise LcuError("You're not in a lobby. Open the custom lobby and try again.") from exc
    config = lobby.get("gameConfig") or {}
    members = [*(config.get("customTeam100") or []), *(config.get("customTeam200") or [])]
    members = members or list(lobby.get("members") or [])
    if not members:
        raise LcuError("No players found in the lobby.")
    entries = []
    for member in members:
        name = _member_name(client, member)
        if not name:
            raise LcuError("Couldn't read a player's name from the lobby.")
        entries.append(f"{name}={_rank_code(client, member.get('puuid'))}")
    return ", ".join(entries)


if __name__ == "__main__":  # quick check: python -m sisyphus.lcu
    print(lobby_roster_text())
