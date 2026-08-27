"""Local service-health snapshot used by Discord and the Kuma publisher."""
from __future__ import annotations

import json
import math
import os
import socket
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from .config import APP_VERSION, BETTING_ENABLED

HEALTH_FILE = Path(".automation/status-health.json")
_STARTED_AT = datetime.now(timezone.utc)
_STARTED_MONOTONIC = monotonic()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _service(healthy: bool, message: str) -> dict:
    return {
        "healthy": healthy,
        "message": message,
        "updated_at": _now(),
        "consecutive_failures": 0,
    }


def discord_latency_ms(latency_seconds: object) -> int | None:
    """Convert Discord's latency reading without allowing NaN or infinity through."""
    try:
        seconds = float(latency_seconds)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return round(seconds * 1000)


_snapshot = {
    "schema_version": 1,
    "generated_at": _now(),
    "process": {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "started_at": _STARTED_AT.isoformat(),
        "version": APP_VERSION,
    },
    "services": {
        "bot": _service(False, "Starting"),
        "discord": _service(False, "Connecting"),
        "polling": _service(False, "Waiting for first cycle"),
        "riot": _service(False, "Waiting for first check"),
        "opgg": _service(False, "Waiting for first check"),
        "markets": _service(
            not BETTING_ENABLED,
            "Waiting for first cycle" if BETTING_ENABLED else "Disabled by configuration",
        ),
    },
}


def _write_snapshot() -> None:
    _snapshot["generated_at"] = _now()
    try:
        HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = HEALTH_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_snapshot, indent=2), encoding="utf-8")
        tmp.replace(HEALTH_FILE)
    except OSError as exc:
        print(f"[health] could not write health snapshot: {type(exc).__name__}")


def _set_service(name: str, healthy: bool, message: str, **details) -> None:
    current = _snapshot["services"].setdefault(name, {})
    failures = 0 if healthy else int(current.get("consecutive_failures", 0)) + 1
    current.update(
        {
            "healthy": healthy,
            "message": message,
            "updated_at": _now(),
            "consecutive_failures": failures,
            **details,
        }
    )
    _write_snapshot()


def mark_bot_online() -> None:
    _set_service("bot", True, "Online")


def mark_discord(connected: bool, latency_ms: int | None = None) -> None:
    details = {"latency_ms": None}
    if latency_ms is not None:
        try:
            milliseconds = float(latency_ms)
        except (TypeError, ValueError):
            milliseconds = None
        if (
            milliseconds is not None
            and math.isfinite(milliseconds)
            and milliseconds >= 0
        ):
            details["latency_ms"] = round(milliseconds)
    _set_service(
        "discord",
        connected,
        "Connected" if connected else "Disconnected",
        **details,
    )


def mark_poll_started() -> float:
    started = monotonic()
    service = _snapshot["services"]["polling"]
    service["last_started_at"] = _now()
    service["updated_at"] = _now()
    _write_snapshot()
    return started


def mark_poll_success(started: float) -> None:
    duration_ms = max(0, round((monotonic() - started) * 1000))
    _set_service(
        "polling",
        True,
        "Polling normally",
        last_success_at=_now(),
        duration_ms=duration_ms,
    )


def mark_poll_failure(message: str = "Polling cycle failed") -> None:
    _set_service("polling", False, message)


def mark_dependency(
    name: str,
    ok: bool,
    *,
    success_message: str,
    failure_message: str,
    immediate: bool = False,
) -> None:
    current = _snapshot["services"][name]
    failures = 0 if ok else int(current.get("consecutive_failures", 0)) + 1
    healthy = ok or (not immediate and failures < 3 and bool(current.get("healthy")))
    message = success_message if ok else failure_message
    current.update(
        {
            "healthy": healthy,
            "message": message,
            "updated_at": _now(),
            "last_success_at": _now() if ok else current.get("last_success_at"),
            "consecutive_failures": failures,
        }
    )
    _write_snapshot()


def mark_markets(ok: bool, message: str | None = None) -> None:
    if not BETTING_ENABLED:
        _set_service("markets", True, "Disabled by configuration", enabled=False)
        return
    _set_service(
        "markets",
        ok,
        message or ("Housekeeping normally" if ok else "Housekeeping failed"),
        enabled=True,
        last_success_at=_now() if ok else _snapshot["services"]["markets"].get("last_success_at"),
    )


def health_snapshot() -> dict:
    snapshot = deepcopy(_snapshot)
    snapshot["process"]["uptime_seconds"] = max(0, int(monotonic() - _STARTED_MONOTONIC))
    return snapshot


_write_snapshot()
