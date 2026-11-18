"""Coin ledger: FIFO consumption, splitting, wallet reset."""

from __future__ import annotations

import pytest

from app import db
from app.services import billing


def test_balance_is_the_sum_of_pending_coins(client):
    billing.add_coin(100, source="mock")
    billing.add_coin(500, source="mock", pulses=5)

    assert billing.balance_centavos() == 600
    assert len(billing.pending_credits()) == 2


def test_consumption_is_oldest_first_and_splits_rows(client):
    billing.add_coin(50, source="mock")
    billing.add_coin(100, source="mock")

    with db.connect() as conn:
        taken = billing.consume(conn, 120, note="unit test")

    assert taken == 120
    assert billing.balance_centavos() == 30
    pending = billing.pending_credits()
    assert len(pending) == 1
    assert pending[0]["centavos"] == 30


def test_insufficient_credit_rolls_back_cleanly(client):
    billing.add_coin(30, source="mock")

    with pytest.raises(billing.InsufficientCredit) as excinfo:
        with db.connect() as conn:
            billing.consume(conn, 40)

    assert excinfo.value.missing_centavos == 10
    assert billing.balance_centavos() == 30


def test_reset_wallet_voids_only_pending_credit(client):
    billing.add_coin(100, source="mock")
    with db.connect() as conn:
        billing.consume(conn, 100, note="spent")
    billing.add_coin(200, source="mock")

    voided = billing.reset_wallet(note="end of shift")

    assert voided == 1
    assert billing.balance_centavos() == 0
    statuses = [row["status"] for row in billing.recent_credits()]
    assert sorted(statuses) == ["consumed", "void"]


def test_zero_and_negative_coins_are_rejected(client):
    with pytest.raises(ValueError):
        billing.add_coin(0)
    with pytest.raises(ValueError):
        billing.add_coin(-100)
    assert billing.balance_centavos() == 0
