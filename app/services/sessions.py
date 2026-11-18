"""Session lifecycle: start, pause, top-up, terminate, enforce.

A session is the unit of billing. It owns the plan snapshot (so a later price
change never rewrites history), the time cap, the data cap and the byte
counters used by the enforcement loop.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Sequence

from .. import db
from ..netctl.base import Grant, RouterDriver
from ..utils import now, now_iso, parse_iso, to_iso
from . import catalog

LIVE_STATES = ("active", "paused")


class SessionError(ValueError):
    """Machine-readable failure: missing_device, device_busy, invalid_plan."""


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
def get_session(session_id: int | str | None, conn: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    if session_id in (None, "", 0, "0"):
        return None
    return db.query_one(
        "SELECT s.*, p.name AS plan_label FROM sessions s"
        " LEFT JOIN plans p ON p.id = s.plan_id WHERE s.id = ?",
        (int(session_id),),
        conn,
    )


def active_for_mac(mac: str | None, conn: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    if not mac:
        return None
    return db.query_one(
        "SELECT * FROM sessions WHERE mac = ? AND status IN ('active', 'paused')"
        " ORDER BY id DESC LIMIT 1",
        (mac,),
        conn,
    )


def live_sessions(conn: sqlite3.Connection | None = None) -> list[sqlite3.Row]:
    return db.query(
        "SELECT s.*, p.name AS plan_label FROM sessions s"
        " LEFT JOIN plans p ON p.id = s.plan_id"
        " WHERE s.status IN ('active', 'paused') ORDER BY s.id DESC",
        (),
        conn,
    )


def recent_sessions(
    limit: int = 50, status: str = "", conn: sqlite3.Connection | None = None
) -> Sequence[sqlite3.Row]:
    sql = (
        "SELECT s.*, p.name AS plan_label, v.code AS voucher_code FROM sessions s"
        " LEFT JOIN plans p ON p.id = s.plan_id"
        " LEFT JOIN vouchers v ON v.id = s.voucher_id WHERE 1 = 1"
    )
    params: list = []
    if status:
        sql += " AND s.status = ?"
        params.append(status)
    sql += " ORDER BY s.id DESC LIMIT ?"
    params.append(max(1, min(500, limit)))
    return db.query(sql, tuple(params), conn)


def remaining_seconds(row: sqlite3.Row) -> int:
    ends = parse_iso(row["ends_at"])
    if ends is None:
        return 0
    if row["status"] == "paused" and row["paused_at"]:
        paused = parse_iso(row["paused_at"])
        if paused is not None:
            return max(0, int((ends - paused).total_seconds()))
    return max(0, int((ends - now()).total_seconds()))


def session_view(row: sqlite3.Row) -> dict:
    """Serialisable snapshot shared by the portal JSON API and kiosk UI."""
    return {
        "id": row["id"],
        "mac": row["mac"],
        "ip": row["ip"],
        "plan": row["plan_name"],
        "status": row["status"],
        "kind": row["kind"],
        "started_at": row["started_at"],
        "ends_at": row["ends_at"],
        "remaining_seconds": remaining_seconds(row),
        "time_cap_seconds": row["time_cap_seconds"],
        "bytes_used": row["bytes_used"],
        "data_cap_bytes": row["data_cap_bytes"],
        "rate_down_kbps": row["rate_down_kbps"],
        "rate_up_kbps": row["rate_up_kbps"],
        "end_reason": row["end_reason"],
    }


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
def touch_device(conn: sqlite3.Connection, mac: str, ip: str | None) -> None:
    if not mac:
        return
    ts = now_iso()
    conn.execute(
        "INSERT INTO devices (mac, ip, first_seen, last_seen) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(mac) DO UPDATE SET ip = excluded.ip, last_seen = excluded.last_seen",
        (mac, ip, ts, ts),
    )


def start_session(
    conn: sqlite3.Connection,
    *,
    plan: sqlite3.Row,
    mac: str,
    ip: str | None,
    kind: str = "voucher",
    voucher_id: int | None = None,
    coin_centavos: int = 0,
    created_by: str = "portal",
) -> sqlite3.Row:
    if not mac:
        raise SessionError("missing_device")
    if plan is None:
        raise SessionError("invalid_plan")
    if active_for_mac(mac, conn) is not None:
        raise SessionError("device_busy")

    cap_seconds = max(60, int(plan["duration_minutes"]) * 60)
    data_cap = max(0, int(plan["data_cap_mb"])) * 1024 * 1024
    ts = now_iso()
    ends = to_iso(now() + timedelta(seconds=cap_seconds))

    cur = conn.execute(
        "INSERT INTO sessions (kind, voucher_id, plan_id, plan_name, mac, ip, status,"
        " started_at, ends_at, time_cap_seconds, data_cap_bytes, bytes_used,"
        " last_rx_bytes, last_tx_bytes, rate_down_kbps, rate_up_kbps, coin_centavos,"
        " created_by) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, 0, 0, 0, ?, ?, ?, ?)",
        (
            kind,
            voucher_id,
            plan["id"],
            plan["name"],
            mac,
            ip,
            ts,
            ends,
            cap_seconds,
            data_cap,
            plan["rate_down_kbps"],
            plan["rate_up_kbps"],
            coin_centavos,
            created_by,
        ),
    )
    session_id = int(cur.lastrowid)

    if voucher_id:
        conn.execute(
            "UPDATE vouchers SET status = 'active', redeemed_at = ?, session_id = ? WHERE id = ?",
            (ts, session_id, voucher_id),
        )
    touch_device(conn, mac, ip)
    db.audit(conn, created_by, "session.start", f"#{session_id} {plan['name']} mac={mac}")
    return get_session(session_id, conn)  # type: ignore[return-value]


def _close(conn: sqlite3.Connection, row: sqlite3.Row, reason: str, actor: str, status: str) -> None:
    conn.execute(
        "UPDATE sessions SET status = ?, ended_at = ?, end_reason = ?, paused_at = NULL"
        " WHERE id = ?",
        (status, now_iso(), reason, row["id"]),
    )
    if row["voucher_id"]:
        conn.execute("UPDATE vouchers SET status = 'used' WHERE id = ?", (row["voucher_id"],))
    db.audit(conn, actor, "session.end", f"#{row['id']} {reason}")


def terminate(
    session_id: int,
    reason: str = "stopped by admin",
    actor: str = "admin",
    status: str = "terminated",
    conn: sqlite3.Connection | None = None,
) -> bool:
    if conn is not None:
        row = get_session(session_id, conn)
        if row is None or row["status"] not in LIVE_STATES:
            return False
        _close(conn, row, reason, actor, status)
        return True
    with db.connect() as own:
        row = get_session(session_id, own)
        if row is None or row["status"] not in LIVE_STATES:
            return False
        _close(own, row, reason, actor, status)
    return True


def pause(session_id: int, actor: str = "admin") -> bool:
    with db.connect() as conn:
        row = get_session(session_id, conn)
        if row is None or row["status"] != "active":
            return False
        conn.execute(
            "UPDATE sessions SET status = 'paused', paused_at = ? WHERE id = ?",
            (now_iso(), session_id),
        )
        db.audit(conn, actor, "session.pause", f"#{session_id}")
    return True


def resume(session_id: int, actor: str = "admin") -> bool:
    with db.connect() as conn:
        row = get_session(session_id, conn)
        if row is None or row["status"] != "paused":
            return False
        paused_at = parse_iso(row["paused_at"])
        shift = 0
        if paused_at is not None:
            shift = int((now() - paused_at).total_seconds())
        current_end = parse_iso(row["ends_at"]) or now()
        new_end = to_iso(current_end + timedelta(seconds=max(0, shift)))
        conn.execute(
            "UPDATE sessions SET status = 'active', paused_at = NULL, ends_at = ? WHERE id = ?",
            (new_end, session_id),
        )
        db.audit(conn, actor, "session.resume", f"#{session_id} +{shift}s")
    return True


def extend(
    session_id: int,
    minutes: int,
    actor: str = "admin",
    conn: sqlite3.Connection | None = None,
) -> bool:
    if minutes <= 0:
        return False
    if conn is not None:
        return _extend(conn, session_id, minutes, actor)
    with db.connect() as own:
        return _extend(own, session_id, minutes, actor)


def _extend(conn: sqlite3.Connection, session_id: int, minutes: int, actor: str) -> bool:
    row = get_session(session_id, conn)
    if row is None or row["status"] not in LIVE_STATES:
        return False
    current_end = parse_iso(row["ends_at"]) or now()
    new_end = to_iso(current_end + timedelta(minutes=minutes))
    conn.execute(
        "UPDATE sessions SET ends_at = ?, time_cap_seconds = time_cap_seconds + ? WHERE id = ?",
        (new_end, minutes * 60, session_id),
    )
    db.audit(conn, actor, "session.extend", f"#{session_id} +{minutes}m")
    return True


# --------------------------------------------------------------------------- #
# Router synchronisation + enforcement
# --------------------------------------------------------------------------- #
def grants(conn: sqlite3.Connection | None = None) -> list[Grant]:
    rows = db.query(
        "SELECT id, mac, ip, rate_down_kbps, rate_up_kbps, plan_name FROM sessions"
        " WHERE status = 'active' ORDER BY id",
        (),
        conn,
    )
    return [
        Grant(
            session_id=row["id"],
            mac=row["mac"],
            ip=row["ip"],
            down_kbps=row["rate_down_kbps"],
            up_kbps=row["rate_up_kbps"],
            label=row["plan_name"],
        )
        for row in rows
    ]


def sync_router(driver: RouterDriver) -> int:
    """Push the current grant list to the router. Safe to call often."""
    with db.connect() as conn:
        items = grants(conn)
    driver.sync(items)
    return len(items)


def enforce(driver: RouterDriver) -> dict[str, int]:
    """Advance byte counters, retire finished sessions, then re-sync the router."""
    summary = {"checked": 0, "updated": 0, "expired": 0, "capped": 0, "grants": 0}
    grace = timedelta(seconds=max(0, catalog.setting_int("grace_seconds", 60)))
    try:
        usage = driver.read_usage()
    except Exception:  # a flaky router must not kill the loop
        usage = {}

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE status IN ('active', 'paused')"
        ).fetchall()
        for row in rows:
            summary["checked"] += 1
            if row["status"] == "paused":
                continue

            counters = usage.get(row["mac"])
            total = row["bytes_used"]
            if counters is not None:
                last_rx, last_tx = row["last_rx_bytes"], row["last_tx_bytes"]
                rx, tx = counters.rx_bytes, counters.tx_bytes
                # A counter that went backwards means the device was re-granted;
                # treat the current reading as the new delta.
                delta_rx = rx - last_rx if rx >= last_rx else rx
                delta_tx = tx - last_tx if tx >= last_tx else tx
                total += max(0, delta_rx) + max(0, delta_tx)
                conn.execute(
                    "UPDATE sessions SET bytes_used = ?, last_rx_bytes = ?, last_tx_bytes = ?"
                    " WHERE id = ?",
                    (total, rx, tx, row["id"]),
                )
                summary["updated"] += 1

            if row["data_cap_bytes"] and total >= row["data_cap_bytes"]:
                _close(conn, row, "data cap reached", "system", "expired")
                summary["capped"] += 1
                continue

            ends = parse_iso(row["ends_at"])
            if ends is not None and now() >= ends + grace:
                _close(conn, row, "time is up", "system", "expired")
                summary["expired"] += 1

        pending = grants(conn)
        summary["grants"] = len(pending)

    try:
        driver.sync(pending)
    except Exception:
        pass
    return summary


def totals(conn: sqlite3.Connection | None = None) -> dict[str, int]:
    live = db.scalar("SELECT COUNT(*) FROM sessions WHERE status = 'active'", (), conn)
    paused = db.scalar("SELECT COUNT(*) FROM sessions WHERE status = 'paused'", (), conn)
    today = db.scalar(
        "SELECT COUNT(*) FROM sessions WHERE started_at >= date('now')", (), conn
    )
    return {"live": live, "paused": paused, "started_today": today}
