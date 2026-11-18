"""Small formatting/time helpers shared by services, routes and templates."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def now() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    return to_iso(now())


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def in_seconds(seconds: float) -> str:
    return to_iso(now() + timedelta(seconds=seconds))


def local_tz(name: str) -> ZoneInfo | timezone:
    """Resolve a timezone name, degrading gracefully on hosts without tzdata."""
    try:
        return ZoneInfo(name)
    except Exception:
        try:
            return ZoneInfo("UTC")
        except Exception:  # Windows without tzdata installed
            return UTC


def fmt_money(centavos: int | None) -> str:
    value = (centavos or 0) / 100
    return f"\u20b1{value:,.2f}"


def fmt_minutes(minutes: int | float | None) -> str:
    total = int(minutes or 0)
    if total < 60:
        return f"{total} min"
    hours, mins = divmod(total, 60)
    if mins == 0:
        return f"{hours}h"
    return f"{hours}h {mins}m"


def fmt_seconds(seconds: int | float | None) -> str:
    total = max(0, int(seconds or 0))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def fmt_bytes(num: int | None) -> str:
    value = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def fmt_dt(value: str | None, tz_name: str = "Asia/Manila", fmt: str = "%b %d, %H:%M") -> str:
    dt = parse_iso(value)
    if dt is None:
        return "-"
    return dt.astimezone(local_tz(tz_name)).strftime(fmt)


def fmt_date(value: str | None, tz_name: str = "Asia/Manila") -> str:
    return fmt_dt(value, tz_name, "%b %d, %Y")


def local_day_bounds(tz_name: str = "Asia/Manila", days_ago: int = 0) -> tuple[str, str]:
    """UTC ISO bounds of a local calendar day, for revenue/report queries."""
    tz = local_tz(tz_name)
    day = datetime.now(tz).date() - timedelta(days=days_ago)
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    return to_iso(start), to_iso(start + timedelta(days=1))


def stable_mac(seed: str) -> str:
    """Deterministic locally-administered MAC, used by the mock router driver."""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    parts = [digest[i : i + 2] for i in range(0, 10, 2)]
    return "02:" + ":".join(parts)


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))
