"""Offline smoke checks for status health classification and secret handling."""
from __future__ import annotations

import importlib.util
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTOMATION = ROOT / "scripts" / "automation"
sys.path.insert(0, str(AUTOMATION))

from status_health import (  # noqa: E402
    COMPONENTS,
    classify_snapshot,
    default_process_alive,
)


def snapshot(now: datetime) -> dict:
    stamp = now.isoformat()
    services = {
        name: {
            "healthy": True,
            "message": "Operational",
            "updated_at": stamp,
            "last_success_at": stamp,
            "enabled": True,
        }
        for name in COMPONENTS
    }
    services["discord"]["latency_ms"] = 42
    services["polling"]["duration_ms"] = 125
    return {
        "generated_at": stamp,
        "process": {"pid": 123, "started_at": stamp, "version": "2.1.6"},
        "services": services,
    }


def main() -> None:
    assert default_process_alive(os.getpid())
    assert not default_process_alive(-1)

    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    healthy = classify_snapshot(snapshot(now), now=now, process_alive=lambda _: True)
    assert all(result["healthy"] for result in healthy.values())
    assert healthy["discord"]["ping_ms"] == 42
    assert healthy["polling"]["ping_ms"] == 125

    invalid_latency_data = snapshot(now)
    invalid_latency_data["services"]["discord"]["latency_ms"] = float("inf")
    invalid_latency = classify_snapshot(
        invalid_latency_data, now=now, process_alive=lambda _: True
    )
    assert invalid_latency["discord"]["healthy"]
    assert invalid_latency["discord"]["ping_ms"] is None

    stale_data = snapshot(now - timedelta(seconds=181))
    stale = classify_snapshot(stale_data, now=now, process_alive=lambda _: True)
    assert all(not result["healthy"] for result in stale.values())

    dead = classify_snapshot(snapshot(now), now=now, process_alive=lambda _: False)
    assert all(not result["healthy"] for result in dead.values())

    disconnected_data = snapshot(now)
    disconnected_data["services"]["discord"]["healthy"] = False
    disconnected = classify_snapshot(disconnected_data, now=now, process_alive=lambda _: True)
    assert not disconnected["discord"]["healthy"]
    assert disconnected["polling"]["healthy"]

    dependency_data = snapshot(now)
    dependency_data["services"]["opgg"].update(
        {"healthy": False, "message": "OP.GG request failed"}
    )
    dependency_failure = classify_snapshot(
        dependency_data, now=now, process_alive=lambda _: True
    )
    assert not dependency_failure["opgg"]["healthy"]
    assert dependency_failure["riot"]["healthy"]
    recovered = classify_snapshot(snapshot(now), now=now, process_alive=lambda _: True)
    assert all(result["healthy"] for result in recovered.values())

    disabled_data = snapshot(now)
    disabled_data["services"]["markets"].update(
        {"healthy": True, "enabled": False, "message": "Disabled by configuration"}
    )
    disabled = classify_snapshot(disabled_data, now=now, process_alive=lambda _: True)
    assert disabled["markets"]["healthy"]

    spec = importlib.util.spec_from_file_location(
        "status_publisher", AUTOMATION / "sisyphus-status-publisher.py"
    )
    assert spec and spec.loader
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    secret = "private-monitor-token"
    url = publisher._push_url(
        f"https://status.example/api/push/{secret}?status=up&msg=OK&ping=&existing=1",
        {"healthy": False, "message": "Polling failed", "ping_ms": 500},
    )
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert secret in url and query["status"] == ["down"] and query["ping"] == ["500"]
    assert query["msg"] == ["Polling failed"] and query["existing"] == ["1"]
    assert secret not in "polling publish failed: HTTP 500"
    print("Status health smoke checks passed.")


if __name__ == "__main__":
    main()
