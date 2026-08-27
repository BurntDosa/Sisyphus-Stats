"""Authenticated FastAPI service for the Sisyphus analytics dashboard."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner


APP_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = APP_ROOT / "web" / "dist"
DEFAULT_ENV_FILE = Path(os.getenv("DASHBOARD_ENV_FILE", "/opt/sisyphus-dashboard/.env"))


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(DEFAULT_ENV_FILE)


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    base_url: str
    client_id: str
    client_secret: str
    guild_id: str
    session_secret: str
    member_key_secret: str
    export_path: Path
    session_db: Path
    session_ttl: int
    oauth_state_ttl: int

    @classmethod
    def from_environment(cls) -> "Settings":
        configured_root = os.getenv("DASHBOARD_DATA_DIR")
        if configured_root:
            data_root = Path(configured_root)
        else:
            data_root = APP_ROOT / ".automation" / "dashboard"
        return cls(
            base_url=os.getenv("DASHBOARD_URL", "https://sisyphus.burntdosa.site").rstrip("/"),
            client_id=os.getenv("DISCORD_CLIENT_ID", "").strip(),
            client_secret=os.getenv("DISCORD_CLIENT_SECRET", "").strip(),
            guild_id=(os.getenv("DASHBOARD_GUILD_ID") or os.getenv("GUILD_ID") or "").strip(),
            session_secret=os.getenv("DASHBOARD_SESSION_SECRET", "").strip(),
            member_key_secret=os.getenv("DASHBOARD_MEMBER_KEY_SECRET", "").strip(),
            export_path=Path(os.getenv("DASHBOARD_EXPORT_PATH", str(data_root / "export.json"))),
            session_db=Path(os.getenv("DASHBOARD_SESSION_DB", str(data_root / "sessions.sqlite"))),
            session_ttl=max(300, int(os.getenv("DASHBOARD_SESSION_TTL_SECONDS", "28800"))),
            oauth_state_ttl=max(60, int(os.getenv("DASHBOARD_OAUTH_STATE_TTL_SECONDS", "600"))),
        )

    @property
    def oauth_ready(self) -> bool:
        return bool(self.client_id and self.client_secret and self.guild_id and self.session_secret)


settings = Settings.from_environment()
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
FORBIDDEN_EXPORT_KEYS = {
    "puuid",
    "match_id",
    "recap_url",
    "recap_channel_id",
    "recap_message_id",
    "recap_jump_url",
    "owner_id",
    "creator_id",
    "channel_id",
    "message_id",
    "reporter_id",
    "actor",
    "user_id",
    "discord_id",
    "access_token",
    "client_secret",
    "session_secret",
    "token",
    "password",
    "secret",
    "private_key",
    "reports",
    "audit",
}


def _forbidden_export_keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_EXPORT_KEYS:
                found.add(str(key))
            found.update(_forbidden_export_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_forbidden_export_keys(item))
    return found


class SessionStore:
    """Small SQLite store so cookies contain no Discord identifiers."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS oauth_states (
                    state_hash TEXT PRIMARY KEY,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
                """
            )

    def purge(self, now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        with self._connect() as connection:
            connection.execute("DELETE FROM oauth_states WHERE expires_at <= ?", (now,))
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))

    def create_state(self, state: str, expires_at: int) -> None:
        digest = hashlib.sha256(state.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO oauth_states(state_hash, expires_at) VALUES (?, ?)",
                (digest, expires_at),
            )

    def consume_state(self, state: str, now: int | None = None) -> bool:
        now = int(time.time()) if now is None else now
        digest = hashlib.sha256(state.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT expires_at FROM oauth_states WHERE state_hash = ?", (digest,)
            ).fetchone()
            connection.execute("DELETE FROM oauth_states WHERE state_hash = ?", (digest,))
        return bool(row and int(row["expires_at"]) > now)

    def create_session(self, user_id: str, display_name: str, expires_at: int) -> str:
        session_id = secrets.token_urlsafe(32)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO sessions(session_id, user_id, display_name, expires_at) VALUES (?, ?, ?, ?)",
                (session_id, user_id, display_name[:80], expires_at),
            )
        return session_id

    def get_session(self, session_id: str, now: int | None = None) -> dict[str, str] | None:
        now = int(time.time()) if now is None else now
        with self._connect() as connection:
            row = connection.execute(
                "SELECT user_id, display_name, expires_at FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row or int(row["expires_at"]) <= now:
            if row:
                self.delete_session(session_id)
            return None
        return {"user_id": str(row["user_id"]), "display_name": str(row["display_name"])}

    def delete_session(self, session_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))


store = SessionStore(settings.session_db)
signer = TimestampSigner(settings.session_secret or secrets.token_urlsafe(32), salt="sisyphus-dashboard")
rate_buckets: dict[str, list[float]] = {}


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-real-ip", "").strip()
    if forwarded:
        return forwarded[:80]
    return (request.client.host if request.client else "unknown")[:80]


def _allow_auth_attempt(request: Request) -> bool:
    now = time.monotonic()
    key = _client_key(request)
    attempts = [value for value in rate_buckets.get(key, []) if now - value < 300]
    if len(attempts) >= 12:
        rate_buckets[key] = attempts
        return False
    attempts.append(now)
    rate_buckets[key] = attempts
    if len(rate_buckets) > 512:
        for old_key, values in list(rate_buckets.items()):
            if not values or now - values[-1] >= 300:
                rate_buckets.pop(old_key, None)
    return True


def _signed_cookie(value: str) -> str:
    return signer.sign(value).decode("utf-8")


def _unsign_cookie(value: str | None, max_age: int) -> str | None:
    if not value:
        return None
    try:
        return signer.unsign(value, max_age=max_age).decode("utf-8")
    except (BadSignature, SignatureExpired, UnicodeDecodeError):
        return None


def _member_key(user_id: str) -> str | None:
    """Map the private OAuth identity to the opaque key used by the export."""
    if not settings.member_key_secret:
        return None
    digest = hmac.new(
        settings.member_key_secret.encode("utf-8"),
        str(user_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:20]
    return f"member_{digest}"


def _session(request: Request) -> dict[str, str] | None:
    session_id = _unsign_cookie(request.cookies.get("sisyphus_session"), settings.session_ttl)
    if not session_id:
        return None
    store.purge()
    return store.get_session(session_id)


def _cookie_options(max_age: int) -> dict[str, Any]:
    return {
        "max_age": max_age,
        "httponly": True,
        "secure": True,
        "samesite": "lax",
        "path": "/",
    }


def _login_page(message: str | None = None) -> HTMLResponse:
    detail = f"<p class=\"notice\">{message}</p>" if message else ""
    html = f"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<link rel=\"icon\" type=\"image/png\" href=\"{settings.base_url}/assets/Sisyphus-Favicon.png\">
<meta name=\"description\" content=\"Private Sisyphus server analytics for ranked progress, points markets, and community history.\">
<meta property=\"og:title\" content=\"The Boulder Chronicle | Sisyphus\">
<meta property=\"og:description\" content=\"Private Sisyphus server analytics for ranked progress, points markets, and community history.\">
<meta property=\"og:type\" content=\"website\">
<meta property=\"og:url\" content=\"{settings.base_url}/\">
<meta property=\"og:image\" content=\"{settings.base_url}/assets/SisyphusStats.png\">
<meta property=\"og:image:type\" content=\"image/png\">
<meta property=\"og:image:width\" content=\"2048\">
<meta property=\"og:image:height\" content=\"1536\">
<meta name=\"twitter:card\" content=\"summary_large_image\">
<meta name=\"twitter:title\" content=\"The Boulder Chronicle | Sisyphus\">
<meta name=\"twitter:description\" content=\"Private Sisyphus server analytics.\">
<meta name=\"twitter:image\" content=\"{settings.base_url}/assets/SisyphusStats.png\">
<title>The Boulder Chronicle | Sisyphus</title><style>body{{margin:0;background:#FFF3E7;color:#221A17;font:16px 'DM Mono',monospace;display:grid;min-height:100vh;place-items:center}}main{{width:min(92vw,520px);padding:32px;border:1px solid #D8C5B8;background:#FFF9F2;border-radius:2px;box-shadow:4px 4px 0 rgba(34,26,23,.12)}}h1{{margin:0 0 8px;font:800 30px Geomini,sans-serif}}p{{color:#765F58;line-height:1.5}}a{{display:inline-flex;padding:12px 16px;background:#A0283B;color:#FFF3E7;text-decoration:none;border-radius:2px;font-weight:700}}.notice{{color:#701B2B}}</style></head>
<body><main><h1>The Boulder Chronicle</h1><p>Sign in with Discord to continue.</p>{detail}<a href=\"/login\">Continue with Discord</a></main></body></html>"""
    response = HTMLResponse(html)
    response.headers["Cache-Control"] = "no-store"
    return response


def _oauth_error(message: str, status_code: int = 400) -> HTMLResponse:
    response = _login_page(message)
    response.status_code = status_code
    response.delete_cookie("sisyphus_oauth_state", path="/")
    return response


def _redirect_uri() -> str:
    return f"{settings.base_url}/auth/callback"


async def _discord_oauth(code: str) -> tuple[str, str] | None:
    payload = {
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(),
    }
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        token_response = await client.post(
            "https://discord.com/api/oauth2/token",
            data=payload,
            headers={"Accept": "application/json"},
        )
        if token_response.status_code != 200:
            return None
        token_data = token_response.json()
        access_token = token_data.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            return None
        headers = {"Authorization": f"Bearer {access_token}"}
        user_response = await client.get("https://discord.com/api/users/@me", headers=headers)
        guild_response = await client.get("https://discord.com/api/users/@me/guilds", headers=headers)
        if user_response.status_code != 200 or guild_response.status_code != 200:
            return None
        user = user_response.json()
        guilds = guild_response.json()
        if not isinstance(user, dict) or not isinstance(guilds, list):
            return None
        if not any(str(guild.get("id")) == settings.guild_id for guild in guilds if isinstance(guild, dict)):
            return None
        user_id = str(user.get("id") or "")
        if not user_id.isdigit():
            return None
        display_name = str(user.get("global_name") or user.get("username") or "Discord member")
        return user_id, " ".join(display_name.split())[:80]


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-store" if request.url.path.startswith(("/api/", "/auth/", "/login", "/logout", "/healthz")) else "private, max-age=60")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    )
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})


@app.get("/login")
async def login(request: Request):
    if not _allow_auth_attempt(request):
        return _oauth_error("Too many sign-in attempts. Try again later.", 429)
    if _session(request):
        return RedirectResponse("/", status_code=303)
    if not settings.oauth_ready:
        return _oauth_error("Discord sign-in is not configured yet.", 503)
    state = secrets.token_urlsafe(32)
    store.purge()
    store.create_state(state, int(time.time()) + settings.oauth_state_ttl)
    query = urlencode(
        {
            "client_id": settings.client_id,
            "response_type": "code",
            "redirect_uri": _redirect_uri(),
            "scope": "identify guilds",
            "state": state,
        }
    )
    response = RedirectResponse(f"https://discord.com/oauth2/authorize?{query}", status_code=303)
    response.set_cookie("sisyphus_oauth_state", _signed_cookie(state), **_cookie_options(settings.oauth_state_ttl))
    return response


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str | None = None, state: str | None = None):
    if not _allow_auth_attempt(request):
        return _oauth_error("Too many sign-in attempts. Try again later.", 429)
    cookie_state = _unsign_cookie(request.cookies.get("sisyphus_oauth_state"), settings.oauth_state_ttl)
    if not state or not cookie_state or not hmac.compare_digest(state, cookie_state) or not store.consume_state(state):
        return _oauth_error("That sign-in session expired or was invalid. Start again.", 400)
    if not code or not settings.oauth_ready:
        return _oauth_error("Discord sign-in could not be completed.", 400)
    try:
        identity = await _discord_oauth(code)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        identity = None
    if identity is None:
        return _oauth_error("This Discord account is not a member of the Sisyphus server.", 403)
    user_id, display_name = identity
    session_id = store.create_session(user_id, display_name, int(time.time()) + settings.session_ttl)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("sisyphus_session", _signed_cookie(session_id), **_cookie_options(settings.session_ttl))
    response.delete_cookie("sisyphus_oauth_state", path="/")
    return response


@app.post("/logout")
async def logout(request: Request):
    session_id = _unsign_cookie(request.cookies.get("sisyphus_session"), settings.session_ttl)
    if session_id:
        store.delete_session(session_id)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("sisyphus_session", path="/")
    return response


def _authenticated(request: Request) -> dict[str, str]:
    session = _session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required")
    return session


@app.get("/api/session")
async def api_session(request: Request) -> JSONResponse:
    session = _authenticated(request)
    return JSONResponse(
        {
            "authenticated": True,
            "display_name": session["display_name"],
            "member_key": _member_key(session["user_id"]),
        }
    )


@app.get("/api/dashboard")
async def api_dashboard(request: Request) -> JSONResponse:
    _authenticated(request)
    try:
        payload = json.loads(settings.export_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(status_code=503, detail="Dashboard data is temporarily unavailable") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=503, detail="Dashboard data is temporarily unavailable")
    if _forbidden_export_keys(payload):
        raise HTTPException(status_code=503, detail="Dashboard data is temporarily unavailable")
    return JSONResponse(payload, headers={"Cache-Control": "private, no-store", "Pragma": "no-cache"})


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if not _session(request):
        return _login_page()
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("Dashboard assets are not installed yet.", status_code=503)
    return FileResponse(index, headers={"Cache-Control": "private, no-store"})


if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
