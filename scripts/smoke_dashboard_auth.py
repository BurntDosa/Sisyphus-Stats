"""Offline checks for dashboard auth, sessions, and response privacy."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        data_dir = Path(directory)
        export_path = data_dir / "export.json"
        export_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "generated_at": "2026-08-27T00:00:00+00:00",
                    "source_version": "2.1.6",
                    "summary": {"players": 0, "games": 0, "wins": 0, "losses": 0, "draws": 0, "net_lp": 0, "active_markets": 0, "markets": 0, "bets": 0},
                    "players": [],
                    "activity": [],
                    "betting": {"wallets": [], "markets": [], "bets": []},
                    "community": {"records": [], "milestones": [], "memories": [], "weekly_summaries": [], "monthly_summaries": [], "squad_goals": [], "historical_events": []},
                }
            ),
            encoding="utf-8",
        )
        os.environ.update(
            {
                "DASHBOARD_DATA_DIR": str(data_dir),
                "DASHBOARD_EXPORT_PATH": str(export_path),
                "DASHBOARD_SESSION_DB": str(data_dir / "sessions.sqlite"),
                "DASHBOARD_SESSION_SECRET": "s" * 48,
                "DASHBOARD_MEMBER_KEY_SECRET": "m" * 48,
                "DISCORD_CLIENT_ID": "client-id-fixture",
                "DISCORD_CLIENT_SECRET": "client-secret-fixture",
                "DASHBOARD_GUILD_ID": "999",
                "DASHBOARD_URL": "https://sisyphus.burntdosa.site",
            }
        )
        from fastapi.testclient import TestClient
        import backend.app as dashboard  # noqa: PLC0415

        client = TestClient(dashboard.app, base_url="https://testserver.local")
        health = client.get("/healthz")
        assert health.status_code == 200 and health.json() == {"status": "ok"}
        assert client.get("/api/dashboard").status_code == 401

        state = "state-fixture"
        dashboard.store.create_state(state, 4_000_000_000)
        client.cookies.set("sisyphus_oauth_state", dashboard._signed_cookie(state))

        async def member_login(_code: str):
            return "123456789012345678", "Allowed member"

        dashboard._discord_oauth = member_login
        callback = client.get("/auth/callback?code=one-time-code&state=state-fixture", follow_redirects=False)
        assert callback.status_code == 303
        session = client.get("/api/session")
        session_payload = session.json()
        assert session.status_code == 200
        assert session_payload["authenticated"] is True
        assert session_payload["display_name"] == "Allowed member"
        assert session_payload["member_key"].startswith("member_")
        assert "123456789012345678" not in session.text
        api = client.get("/api/dashboard")
        assert api.status_code == 200 and api.headers["cache-control"] == "private, no-store"
        assert "123456789012345678" not in api.text

        logout = client.post("/logout", follow_redirects=False)
        assert logout.status_code == 303
        dashboard.store.create_state("expired", 1)
        client.cookies.set("sisyphus_oauth_state", dashboard._signed_cookie("expired"))
        invalid = client.get("/auth/callback?code=one-time-code&state=expired")
        assert invalid.status_code == 400

        state = "non-member"
        dashboard.store.create_state(state, 4_000_000_000)
        client.cookies.set("sisyphus_oauth_state", dashboard._signed_cookie(state))

        async def non_member(_code: str):
            return None

        dashboard._discord_oauth = non_member
        rejected = client.get("/auth/callback?code=one-time-code&state=non-member")
        assert rejected.status_code == 403

    print("Dashboard authentication smoke checks passed.")


if __name__ == "__main__":
    main()
