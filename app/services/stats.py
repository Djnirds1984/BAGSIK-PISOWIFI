"""Revenue, usage and live operational numbers for the dashboard."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from .. import db
from ..config import get_settings
from ..utils import local_day_bounds, local_tz, parse_iso
from . import billing


def day_bounds(days_ago: int = 0) -> tuple[str, str]:
    return local_day_bounds(get_settings().timezone, days_ago)


def _voucher_revenue(start: str, end: str, conn: sqlite3.Connection | None) -> int:
    # Kiosk-sold vouchers are already counted as consumed coin credit, so only
    # admin-issued codes contribute here.
    return int(
        db.scalar(
            "SELECT COALESCE(SUM(price_centavos), 0) FROM vouchers"
            " WHERE source = 'admin' AND status IN ('active', 'used')"
            " AND redeemed_at >= ? AND redeemed_at < ?",
            (start, end),
            conn,
        )
    )


def revenue_between(start: str, end: str, conn: sqlite3.Connection | None = None) -> int:
    coins = billing.totals_between(start, end, conn)["spent_centavos"]
    return coins + _voucher_revenue(start, end, conn)


def overview(conn: sqlite3.Connection | None = None) -> dict:
    start, end = day_bounds()
    yesterday_start, yesterday_end = day_bounds(1)
    coins = billing.totals_between(start, end, conn)

    revenue = coins["spent_centavos"] + _voucher_revenue(start, end, conn)
    yesterday = revenue_between(yesterday_start, yesterday_end, conn)

    live = int(db.scalar("SELECT COUNT(*) FROM sessions WHERE status = 'active'", (), conn))
    paused = int(db.scalar("SELECT COUNT(*) FROM sessions WHERE status = 'paused'", (), conn))
    started_today = int(
        db.scalar(
            "SELECT COUNT(*) FROM sessions WHERE started_at >= ? AND started_at < ?",
            (start, end),
            conn,
        )
    )
    bytes_today = int(
        db.scalar(
            "SELECT COALESCE(SUM(bytes_used), 0) FROM sessions"
            " WHERE started_at >= ? AND started_at < ?",
            (start, end),
            conn,
        )
    )
    vouchers_unused = int(db.scalar("SELECT COUNT(*) FROM vouchers WHERE status = 'unused'", (), conn))
    vouchers_active = int(db.scalar("SELECT COUNT(*) FROM vouchers WHERE status = 'active'", (), conn))
    devices = int(db.scalar("SELECT COUNT(*) FROM devices", (), conn))
    wallet = billing.balance_centavos(conn)

    delta_pct = 0
    if yesterday > 0:
        delta_pct = round((revenue - yesterday) / yesterday * 100)

    return {
        "revenue_centavos": revenue,
        "coin_centavos": coins["spent_centavos"],
        "voucher_centavos": _voucher_revenue(start, end, conn),
        "coins_inserted_centavos": coins["inserted_centavos"],
        "yesterday_centavos": yesterday,
        "delta_pct": delta_pct,
        "live": live,
        "paused": paused,
        "started_today": started_today,
        "bytes_today": bytes_today,
        "vouchers_unused": vouchers_unused,
        "vouchers_active": vouchers_active,
        "wallet_centavos": wallet,
        "devices": devices,
    }


def hourly_revenue(days_ago: int = 0, conn: sqlite3.Connection | None = None) -> list[dict]:
    """24 buckets of revenue for the current (or previous) local day."""
    start, end = day_bounds(days_ago)
    tz = local_tz(get_settings().timezone)
    buckets = [0] * 24

    rows = db.query(
        "SELECT consumed_at AS ts, centavos FROM coin_credits"
        " WHERE status = 'consumed' AND consumed_at >= ? AND consumed_at < ?",
        (start, end),
        conn,
    )
    rows += db.query(
        "SELECT redeemed_at AS ts, price_centavos AS centavos FROM vouchers"
        " WHERE source = 'admin' AND status IN ('active', 'used')"
        " AND redeemed_at >= ? AND redeemed_at < ?",
        (start, end),
        conn,
    )
    for row in rows:
        moment = parse_iso(row["ts"])
        if moment is None:
            continue
        buckets[moment.astimezone(tz).hour] += int(row["centavos"])

    peak = max(buckets) or 1
    return [
        {"hour": hour, "centavos": value, "pct": round(value / peak * 100)}
        for hour, value in enumerate(buckets)
    ]


def plan_mix(days: int = 7, conn: sqlite3.Connection | None = None) -> list[dict]:
    start, _ = day_bounds(days - 1)
    _, end = day_bounds(0)
    rows = db.query(
        "SELECT COALESCE(plan_name, 'Unknown') AS plan, COUNT(*) AS n,"
        " COALESCE(SUM(coin_centavos), 0) AS coin"
        " FROM sessions WHERE started_at >= ? AND started_at < ?"
        " GROUP BY COALESCE(plan_name, 'Unknown') ORDER BY n DESC",
        (start, end),
        conn,
    )
    total = sum(row["n"] for row in rows) or 1
    return [
        {
            "plan": row["plan"],
            "count": row["n"],
            "coin_centavos": row["coin"],
            "pct": round(row["n"] / total * 100),
        }
        for row in rows
    ]


def daily_revenue(days: int = 7, conn: sqlite3.Connection | None = None) -> list[dict]:
    series = []
    for offset in range(days - 1, -1, -1):
        start, end = day_bounds(offset)
        tz = local_tz(get_settings().timezone)
        label = parse_iso(start)
        series.append(
            {
                "label": label.astimezone(tz).strftime("%a") if label else "?",
                "day": label.astimezone(tz).strftime("%b %d") if label else "?",
                "centavos": revenue_between(start, end, conn),
            }
        )
    peak = max((item["centavos"] for item in series), default=0) or 1
    for item in series:
        item["pct"] = round(item["centavos"] / peak * 100)
    return series


def clock_now() -> str:
    """Local time string for the dashboard header."""
    tz = local_tz(get_settings().timezone)
    return datetime.now(tz).strftime("%a %b %d \u00b7 %H:%M")
