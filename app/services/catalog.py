"""Plans, mutable business settings and the audit trail."""

from __future__ import annotations

import sqlite3
from typing import Sequence

from .. import db
from ..utils import now_iso


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def all_settings(conn: sqlite3.Connection | None = None) -> dict[str, str]:
    rows = db.query("SELECT key, value FROM settings", (), conn)
    return {row["key"]: row["value"] for row in rows}


def setting(key: str, default: str = "") -> str:
    row = db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def setting_int(key: str, default: int = 0) -> int:
    try:
        return int(setting(key, str(default)))
    except ValueError:
        return default


def setting_bool(key: str, default: bool = False) -> bool:
    return setting(key, "1" if default else "0") in {"1", "true", "yes", "on"}


def update_settings(values: dict[str, str], actor: str = "admin") -> None:
    with db.connect() as conn:
        for key, value in values.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
        db.audit(conn, actor, "settings.update", ", ".join(sorted(values)))


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #
def list_plans(active_only: bool = True, conn: sqlite3.Connection | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM plans"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY sort_order, price_centavos, id"
    return db.query(sql, (), conn)


def get_plan(plan_id: int | str | None, conn: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    if plan_id in (None, "", 0, "0"):
        return None
    return db.query_one("SELECT * FROM plans WHERE id = ?", (int(plan_id),), conn)


def create_plan(
    *,
    name: str,
    price_centavos: int,
    duration_minutes: int,
    data_cap_mb: int = 0,
    rate_down_kbps: int = 0,
    rate_up_kbps: int = 0,
    sort_order: int = 100,
    actor: str = "admin",
) -> int:
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO plans (name, price_centavos, duration_minutes, data_cap_mb,"
            " rate_down_kbps, rate_up_kbps, is_active, sort_order, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                name.strip() or "Untitled plan",
                max(0, price_centavos),
                max(1, duration_minutes),
                max(0, data_cap_mb),
                max(0, rate_down_kbps),
                max(0, rate_up_kbps),
                sort_order,
                now_iso(),
            ),
        )
        db.audit(conn, actor, "plan.create", name)
        return int(cur.lastrowid)


def update_plan(plan_id: int, **fields) -> None:
    allowed = {
        "name",
        "price_centavos",
        "duration_minutes",
        "data_cap_mb",
        "rate_down_kbps",
        "rate_up_kbps",
        "is_active",
        "sort_order",
    }
    clean = {k: v for k, v in fields.items() if k in allowed}
    if not clean:
        return
    assignments = ", ".join(f"{key} = ?" for key in clean)
    with db.connect() as conn:
        conn.execute(
            f"UPDATE plans SET {assignments} WHERE id = ?", (*clean.values(), plan_id)
        )
        db.audit(conn, "admin", "plan.update", f"plan {plan_id}: {sorted(clean)}")


def set_plan_active(plan_id: int, active: bool, actor: str = "admin") -> None:
    with db.connect() as conn:
        conn.execute("UPDATE plans SET is_active = ? WHERE id = ?", (1 if active else 0, plan_id))
        db.audit(conn, actor, "plan.active", f"plan {plan_id} -> {active}")


def delete_plan(plan_id: int, actor: str = "admin") -> str:
    """Remove a plan that was never sold; otherwise deactivate it for history."""
    with db.connect() as conn:
        used = db.scalar(
            "SELECT (SELECT COUNT(*) FROM vouchers WHERE plan_id = :p)"
            " + (SELECT COUNT(*) FROM sessions WHERE plan_id = :p)",
            {"p": plan_id},
            conn,
        )
        if used:
            conn.execute("UPDATE plans SET is_active = 0 WHERE id = ?", (plan_id,))
            db.audit(conn, actor, "plan.deactivate", f"plan {plan_id} has {used} records")
            return "deactivated"
        conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
        db.audit(conn, actor, "plan.delete", f"plan {plan_id}")
        return "deleted"


def plan_summary(plan: sqlite3.Row) -> dict:
    return {
        "id": plan["id"],
        "name": plan["name"],
        "price_centavos": plan["price_centavos"],
        "duration_minutes": plan["duration_minutes"],
        "data_cap_mb": plan["data_cap_mb"],
        "rate_down_kbps": plan["rate_down_kbps"],
        "rate_up_kbps": plan["rate_up_kbps"],
    }


def recent_audit(limit: int = 25, conn: sqlite3.Connection | None = None) -> Sequence[sqlite3.Row]:
    return db.query(
        "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,), conn
    )
