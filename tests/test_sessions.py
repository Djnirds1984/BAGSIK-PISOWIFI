"""Session lifecycle and enforcement, driven by the mock router."""

from __future__ import annotations

from datetime import timedelta

from app import db, utils
from app.services import catalog, sessions, vouchers
from app.utils import stable_mac

MAC = stable_mac("10.0.0.55")


def _start(plan=None, voucher_id: int | None = None):
    plan = plan or catalog.list_plans()[0]
    with db.connect() as conn:
        return sessions.start_session(
            conn,
            plan=plan,
            mac=MAC,
            ip="10.0.0.55",
            kind="voucher",
            voucher_id=voucher_id,
            created_by="test",
        )


def test_time_up_ends_the_session(client, router):
    catalog.update_settings({"grace_seconds": "0"})
    row = _start()
    past = utils.to_iso(utils.now() - timedelta(seconds=5))
    with db.connect() as conn:
        conn.execute("UPDATE sessions SET ends_at = ? WHERE id = ?", (past, row["id"]))

    summary = sessions.enforce(router)

    assert summary["expired"] == 1
    closed = sessions.get_session(row["id"])
    assert closed["status"] == "expired"
    assert closed["end_reason"] == "time is up"
    assert router.active_grants() == []


def test_grace_period_delays_the_cutoff(client, router):
    catalog.update_settings({"grace_seconds": "60"})
    row = _start()
    just_over = utils.to_iso(utils.now() - timedelta(seconds=5))
    with db.connect() as conn:
        conn.execute("UPDATE sessions SET ends_at = ? WHERE id = ?", (just_over, row["id"]))

    assert sessions.enforce(router)["expired"] == 0
    assert sessions.get_session(row["id"])["status"] == "active"

    well_over = utils.to_iso(utils.now() - timedelta(seconds=120))
    with db.connect() as conn:
        conn.execute("UPDATE sessions SET ends_at = ? WHERE id = ?", (well_over, row["id"]))

    assert sessions.enforce(router)["expired"] == 1
    assert sessions.get_session(row["id"])["status"] == "expired"


def test_data_cap_ends_the_session(client, router):
    plan_id = catalog.create_plan(
        name="Capped 1 MB", price_centavos=100, duration_minutes=30, data_cap_mb=1
    )
    row = _start(catalog.get_plan(plan_id))
    router.set_usage(MAC, rx_bytes=2 * 1024 * 1024)

    summary = sessions.enforce(router)

    assert summary["capped"] == 1
    closed = sessions.get_session(row["id"])
    assert closed["status"] == "expired"
    assert closed["end_reason"] == "data cap reached"
    assert closed["bytes_used"] >= closed["data_cap_bytes"]


def test_usage_below_the_cap_keeps_the_session(client, router):
    plan_id = catalog.create_plan(
        name="Capped 10 MB", price_centavos=100, duration_minutes=30, data_cap_mb=10
    )
    row = _start(catalog.get_plan(plan_id))
    router.set_usage(MAC, rx_bytes=1024 * 1024, tx_bytes=2048)

    summary = sessions.enforce(router)

    assert summary["capped"] == 0 and summary["expired"] == 0
    live = sessions.get_session(row["id"])
    assert live["status"] == "active"
    assert live["bytes_used"] == 1024 * 1024 + 2048


def test_pause_freezes_the_clock_and_resume_gives_it_back(client):
    row = _start()
    before = sessions.remaining_seconds(row)

    assert sessions.pause(row["id"]) is True
    # Pretend the customer has been paused for a minute: the countdown must
    # not move while paused, and resume must hand that minute back.
    with db.connect() as conn:
        conn.execute(
            "UPDATE sessions SET paused_at = ? WHERE id = ?",
            (utils.to_iso(utils.now() - timedelta(seconds=60)), row["id"]),
        )

    paused = sessions.get_session(row["id"])
    assert paused["status"] == "paused"
    assert abs(sessions.remaining_seconds(paused) - (before + 60)) <= 3

    assert sessions.resume(row["id"]) is True
    resumed = sessions.get_session(row["id"])
    assert resumed["status"] == "active"
    assert resumed["paused_at"] is None
    assert abs(sessions.remaining_seconds(resumed) - sessions.remaining_seconds(paused)) <= 3
    assert utils.parse_iso(resumed["ends_at"]) > utils.parse_iso(row["ends_at"])


def test_extend_adds_time_and_raises_the_cap(client):
    row = _start()
    before = sessions.remaining_seconds(row)

    assert sessions.extend(row["id"], 5) is True

    after_row = sessions.get_session(row["id"])
    assert after_row["time_cap_seconds"] == row["time_cap_seconds"] + 300
    assert abs(sessions.remaining_seconds(after_row) - (before + 300)) <= 5


def test_terminate_releases_the_voucher(client):
    plan = catalog.list_plans()[0]
    _, codes = vouchers.create_batch(plan=plan, quantity=1)
    voucher = vouchers.get_by_code(codes[0])
    row = _start(plan, voucher_id=voucher["id"])

    assert sessions.terminate(row["id"], reason="test") is True

    assert sessions.get_session(row["id"])["status"] == "terminated"
    assert vouchers.get_voucher(voucher["id"])["status"] == "used"
    assert sessions.active_for_mac(MAC) is None


def test_router_grants_only_active_sessions(client, router):
    plan = catalog.list_plans()[0]
    first = _start(plan)

    sessions.sync_router(router)
    grants = router.active_grants()
    assert len(grants) == 1
    assert grants[0].mac == MAC
    assert grants[0].down_kbps == plan["rate_down_kbps"]

    sessions.pause(first["id"])
    sessions.sync_router(router)
    assert router.active_grants() == []
