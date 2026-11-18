"""Coin credit ledger.

Coins are never "just a number": every pulse burst becomes a pending row, and
purchases consume those rows oldest-first. That keeps the cash report and the
session report reconcilable.
"""

from __future__ import annotations

import sqlite3
from typing import Sequence

from .. import db
from ..utils import now_iso


class InsufficientCredit(ValueError):
    def __init__(self, missing_centavos: int) -> None:
        super().__init__("insufficient_credit")
        self.missing_centavos = missing_centavos


def add_coin(centavos: int, source: str = "gpio", pulses: int = 0, note: str = "") -> int:
    centavos = int(centavos)
    if centavos <= 0:
        raise ValueError("coin value must be positive")
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO coin_credits (centavos, source, status, pulses, created_at, note)"
            " VALUES (?, ?, 'pending', ?, ?, ?)",
            (centavos, source, int(pulses), now_iso(), note),
        )
        db.audit(conn, source, "coin.in", f"{centavos} centavos")
        return int(cur.lastrowid)


def balance_centavos(conn: sqlite3.Connection | None = None) -> int:
    return int(db.scalar("SELECT COALESCE(SUM(centavos), 0) FROM coin_credits WHERE status = 'pending'", (), conn))


def pending_credits(conn: sqlite3.Connection | None = None) -> Sequence[sqlite3.Row]:
    return db.query(
        "SELECT * FROM coin_credits WHERE status = 'pending' ORDER BY id", (), conn
    )


def consume(
    conn: sqlite3.Connection,
    centavos: int,
    *,
    session_id: int | None = None,
    voucher_id: int | None = None,
    note: str = "",
) -> int:
    """Consume credit oldest-first, splitting a coin row when needed."""
    wanted = int(centavos)
    if wanted <= 0:
        return 0
    remaining = wanted
    taken = 0
    ts = now_iso()
    rows = conn.execute(
        "SELECT * FROM coin_credits WHERE status = 'pending' ORDER BY id"
    ).fetchall()
    for row in rows:
        if remaining <= 0:
            break
        available = int(row["centavos"])
        take = min(available, remaining)
        if take == available:
            conn.execute(
                "UPDATE coin_credits SET status = 'consumed', session_id = ?, voucher_id = ?,"
                " consumed_at = ?, note = ? WHERE id = ?",
                (session_id, voucher_id, ts, note, row["id"]),
            )
        else:
            conn.execute(
                "UPDATE coin_credits SET centavos = ? WHERE id = ?", (available - take, row["id"])
            )
            conn.execute(
                "INSERT INTO coin_credits (centavos, source, status, pulses, session_id,"
                " voucher_id, created_at, consumed_at, note)"
                " VALUES (?, ?, 'consumed', 0, ?, ?, ?, ?, ?)",
                (take, row["source"], session_id, voucher_id, row["created_at"], ts, note),
            )
        remaining -= take
        taken += take
    if remaining > 0:
        raise InsufficientCredit(remaining)
    return taken


def reset_wallet(actor: str = "admin", note: str = "wallet reset") -> int:
    """Void all pending credit (end of shift, or coins left by a customer)."""
    with db.connect() as conn:
        pending = int(
            db.scalar(
                "SELECT COALESCE(SUM(centavos), 0) FROM coin_credits WHERE status = 'pending'", (), conn
            )
        )
        cur = conn.execute(
            "UPDATE coin_credits SET status = 'void', consumed_at = ?, note = ?"
            " WHERE status = 'pending'",
            (now_iso(), note),
        )
        db.audit(conn, actor, "wallet.reset", f"{cur.rowcount} rows, {pending} centavos voided")
        return int(cur.rowcount)


def recent_credits(limit: int = 100, conn: sqlite3.Connection | None = None) -> Sequence[sqlite3.Row]:
    return db.query(
        "SELECT c.*, s.plan_name FROM coin_credits c"
        " LEFT JOIN sessions s ON s.id = c.session_id"
        " ORDER BY c.id DESC LIMIT ?",
        (max(1, min(500, limit)),),
        conn,
    )


def totals_between(start_iso: str, end_iso: str, conn: sqlite3.Connection | None = None) -> dict[str, int]:
    inserted = int(
        db.scalar(
            "SELECT COALESCE(SUM(centavos), 0) FROM coin_credits"
            " WHERE created_at >= ? AND created_at < ?",
            (start_iso, end_iso),
            conn,
        )
    )
    spent = int(
        db.scalar(
            "SELECT COALESCE(SUM(centavos), 0) FROM coin_credits"
            " WHERE status = 'consumed' AND consumed_at >= ? AND consumed_at < ?",
            (start_iso, end_iso),
            conn,
        )
    )
    return {"inserted_centavos": inserted, "spent_centavos": spent}
