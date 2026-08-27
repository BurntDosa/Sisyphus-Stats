"""Small pure helpers — timezone, list coercion, ISO parsing."""
from __future__ import annotations

from datetime import datetime, timezone

from .config import IST


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist():
    return now_ist().date()


def parse_iso_datetime(raw: str | None):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def match_day_ist(created_at: str | None):
    """Convert an OP.GG `created_at` (ISO) to an IST calendar date.

    OP.GG returns naive datetimes in UTC for some endpoints. Force UTC interpretation
    when no tz info is present so the result is independent of the host server's TZ.
    """
    if not created_at:
        return None
    try:
        dt = datetime.fromisoformat(created_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).date()
    except Exception as exc:
        print(f"[match_day_ist] parse failed for {created_at!r}: {exc}")
        return None
