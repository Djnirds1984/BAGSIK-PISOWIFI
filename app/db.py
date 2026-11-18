"""SQLite access layer.

One connection per unit of work, WAL journal, no ORM. The database lives on the
same box as the app, so this is both the simplest and the fastest option.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import Settings
from .utils import now_iso

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
_db_path: Path | None = None

DEFAULT_SETTINGS = {
    "business_name": "BAGSIK PISOWIFI",
    "portal_headline": "Welcome to BAGSIK PISOWIFI",
    "portal_subtext": "Insert a coin, buy a voucher, or redeem your code to get online.",
    "portal_contact": "Ask the tindera for a voucher code.",
    "print_footer": "BAGSIK PISOWIFI - salamat sa pag-suporta!",
    "grace_seconds": "60",
    "kiosk_voucher_enabled": "1",
    "show_rates_on_portal": "1",
}

DEFAULT_PLANS = [
    # name, price, minutes, cap_mb, down_kbps, up_kbps, sort
    ("PISO 1 - 5 mins", 100, 5, 0, 3000, 1000, 10),
    ("PISO 5 - 30 mins", 500, 30, 500, 5000, 2000, 20),
    ("PISO 10 - 75 mins", 1000, 75, 1500, 8000, 3000, 30),
    ("PISO 20 - 3 hours", 2000, 180, 3000, 10000, 5000, 40),
]


def configure(settings: Settings) -> Path:
    """Point the access layer at the configured database file."""
    global _db_path
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    _db_path = settings.db_path
    return _db_path


def db_path() -> Path:
    if _db_path is None:
        raise RuntimeError("Database is not configured - call db.configure() first")
    return _db_path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Yield a connection inside a transaction; commit or roll back on exit."""
    conn = sqlite3.connect(db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql: str, params: tuple | dict = (), conn: sqlite3.Connection | None = None):
    if conn is not None:
        return conn.execute(sql, params).fetchall()
    with connect() as own:
        return own.execute(sql, params).fetchall()


def query_one(sql: str, params: tuple | dict = (), conn: sqlite3.Connection | None = None):
    if conn is not None:
        return conn.execute(sql, params).fetchone()
    with connect() as own:
        return own.execute(sql, params).fetchone()


def scalar(sql: str, params: tuple | dict = (), conn: sqlite3.Connection | None = None, default=0):
    row = query_one(sql, params, conn)
    if row is None:
        return default
    value = row[0]
    return default if value is None else value


def init_db(settings: Settings) -> None:
    """Create the schema and seed defaults. Safe to run on every boot."""
    configure(settings)
    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    with connect() as conn:
        conn.executescript(schema)
        seed(conn, settings)


def seed(conn: sqlite3.Connection, settings: Settings) -> None:
    from .security import hash_password

    ts = now_iso()
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
            (key, value),
        )

    has_admin = conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
    if not has_admin:
        conn.execute(
            "INSERT INTO admin_users (username, password_hash, display_name, created_at)"
            " VALUES (?, ?, ?, ?)",
            (settings.admin_user, hash_password(settings.admin_password), "Administrator", ts),
        )

    has_plans = conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
    if not has_plans:
        for name, price, minutes, cap, down, up, sort in DEFAULT_PLANS:
            conn.execute(
                "INSERT INTO plans (name, price_centavos, duration_minutes, data_cap_mb,"
                " rate_down_kbps, rate_up_kbps, is_active, sort_order, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (name, price, minutes, cap, down, up, sort, ts),
            )


def audit(conn: sqlite3.Connection, actor: str, action: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_logs (created_at, actor, action, detail) VALUES (?, ?, ?, ?)",
        (now_iso(), actor, action, detail),
    )
