"""Voucher codes: generation, lookup, redemption, voiding."""

from __future__ import annotations

import secrets
import sqlite3
from typing import Sequence

from .. import db
from ..utils import now_iso

# Unambiguous alphabet: no 0/O, no 1/I/L. Two groups of four characters.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH = 8
MAX_BATCH = 1000


def normalize_code(raw: str) -> str:
    """Accept 'abcd efgh', 'ABCD-EFGH' or 'abcdefgh' and return 'ABCD-EFGH'."""
    cleaned = "".join(ch for ch in (raw or "").upper() if ch.isalnum())
    cleaned = cleaned[:CODE_LENGTH]
    if len(cleaned) <= 4:
        return cleaned
    return f"{cleaned[:4]}-{cleaned[4:]}"


def _random_code() -> str:
    body = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    return f"{body[:4]}-{body[4:]}"


def _unique_code(conn: sqlite3.Connection) -> str:
    for _ in range(20):
        code = _random_code()
        exists = conn.execute("SELECT 1 FROM vouchers WHERE code = ?", (code,)).fetchone()
        if not exists:
            return code
    raise RuntimeError("Could not allocate a unique voucher code")


def create_batch(
    *,
    plan: sqlite3.Row,
    quantity: int = 1,
    label: str = "",
    source: str = "admin",
    actor: str = "admin",
    note: str = "",
) -> tuple[int, list[str]]:
    """Create a batch of unused vouchers. Returns (batch_id, codes)."""
    quantity = max(1, min(MAX_BATCH, int(quantity)))
    ts = now_iso()
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO batches (label, plan_id, quantity, source, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ((label.strip() or plan["name"]), plan["id"], quantity, source, actor, ts),
        )
        batch_id = int(cur.lastrowid)
        codes: list[str] = []
        for _ in range(quantity):
            code = _unique_code(conn)
            conn.execute(
                "INSERT INTO vouchers (code, plan_id, price_centavos, status, source,"
                " batch_id, note, created_at) VALUES (?, ?, ?, 'unused', ?, ?, ?, ?)",
                (code, plan["id"], plan["price_centavos"], source, batch_id, note, ts),
            )
            codes.append(code)
        db.audit(conn, actor, "voucher.batch", f"batch {batch_id}: {quantity} x {plan['name']}")
    return batch_id, codes


def get_by_code(code: str, conn: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    return db.query_one(
        "SELECT * FROM vouchers WHERE code = ?", (normalize_code(code),), conn
    )


def list_vouchers(
    *,
    status: str = "",
    batch_id: int | str | None = None,
    search: str = "",
    limit: int = 200,
    conn: sqlite3.Connection | None = None,
) -> Sequence[sqlite3.Row]:
    sql = (
        "SELECT v.*, p.name AS plan_name, p.duration_minutes"
        " FROM vouchers v LEFT JOIN plans p ON p.id = v.plan_id WHERE 1 = 1"
    )
    params: list = []
    if status:
        sql += " AND v.status = ?"
        params.append(status)
    if batch_id not in (None, "", "0"):
        sql += " AND v.batch_id = ?"
        params.append(int(batch_id))
    if search.strip():
        sql += " AND (v.code LIKE ? OR v.note LIKE ? OR p.name LIKE ?)"
        needle = f"%{search.strip().upper()}%"
        params.extend([needle, needle, needle])
    sql += " ORDER BY v.id DESC LIMIT ?"
    params.append(max(1, min(2000, limit)))
    return db.query(sql, tuple(params), conn)


def get_voucher(voucher_id: int, conn: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    return db.query_one("SELECT * FROM vouchers WHERE id = ?", (voucher_id,), conn)


def list_batches(limit: int = 25, conn: sqlite3.Connection | None = None) -> Sequence[sqlite3.Row]:
    return db.query(
        "SELECT b.*, p.name AS plan_name,"
        " SUM(CASE WHEN v.status = 'unused' THEN 1 ELSE 0 END) AS unused_count,"
        " SUM(CASE WHEN v.status IN ('active', 'used') THEN 1 ELSE 0 END) AS sold_count"
        " FROM batches b"
        " LEFT JOIN plans p ON p.id = b.plan_id"
        " LEFT JOIN vouchers v ON v.batch_id = b.id"
        " GROUP BY b.id ORDER BY b.id DESC LIMIT ?",
        (limit,),
        conn,
    )


def batch_codes(batch_id: int, conn: sqlite3.Connection | None = None) -> Sequence[sqlite3.Row]:
    return db.query(
        "SELECT v.*, p.name AS plan_name, p.duration_minutes, p.data_cap_mb"
        " FROM vouchers v LEFT JOIN plans p ON p.id = v.plan_id"
        " WHERE v.batch_id = ? ORDER BY v.id",
        (batch_id,),
        conn,
    )


def void_voucher(voucher_id: int, actor: str = "admin") -> str:
    with db.connect() as conn:
        row = get_voucher(voucher_id, conn)
        if row is None:
            return "missing"
        if row["status"] == "active":
            return "in_use"
        if row["status"] == "void":
            return "already_void"
        conn.execute(
            "UPDATE vouchers SET status = 'void', voided_at = ? WHERE id = ?", (now_iso(), voucher_id)
        )
        db.audit(conn, actor, "voucher.void", row["code"])
        return "void"


def void_batch(batch_id: int, actor: str = "admin") -> int:
    with db.connect() as conn:
        cur = conn.execute(
            "UPDATE vouchers SET status = 'void', voided_at = ?"
            " WHERE batch_id = ? AND status = 'unused'",
            (now_iso(), batch_id),
        )
        db.audit(conn, actor, "voucher.void_batch", f"batch {batch_id}: {cur.rowcount} voided")
        return int(cur.rowcount)


def mark_session_result(conn: sqlite3.Connection, voucher_id: int | None, status: str) -> None:
    if not voucher_id:
        return
    conn.execute("UPDATE vouchers SET status = ? WHERE id = ?", (status, voucher_id))


def counts(conn: sqlite3.Connection | None = None) -> dict[str, int]:
    rows = db.query("SELECT status, COUNT(*) AS n FROM vouchers GROUP BY status", (), conn)
    result = {"unused": 0, "active": 0, "used": 0, "expired": 0, "void": 0}
    for row in rows:
        result[row["status"]] = row["n"]
    return result
