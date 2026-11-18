"""Coin pricing and counter sales.

The customer never picks a plan: the operator posts pulse rates (coins ->
time) and every pulse that lands while the slot is armed buys time at the
best rate it fits. This module owns that arithmetic plus the code sale at the
counter.
"""

from __future__ import annotations

import sqlite3

from .. import db
from . import billing, catalog, vouchers


class KioskError(ValueError):
    """insufficient_credit, invalid_plan."""


def quote_pulses(
    pulses: int, pulse_centavos: int = 100, credit_centavos: int | None = None
) -> dict:
    """Convert counted pulses into minutes using the admin's pulse rates.

    Rates are applied greedily from the largest down, so bulk rates bonus the
    customer while every single pulse still buys its own time. Whatever the
    rates cannot absorb stays as wallet credit.
    """
    pulses = max(0, int(pulses))
    pulse_centavos = max(1, int(pulse_centavos))
    credit = pulses * pulse_centavos if credit_centavos is None else max(0, int(credit_centavos))
    tiers = sorted(
        catalog.list_plans(active_only=True),
        key=lambda plan: (-int(plan["price_centavos"]), -int(plan["duration_minutes"])),
    )

    minutes = 0
    used = 0
    tier_used: sqlite3.Row | None = None
    for tier in tiers:
        price = int(tier["price_centavos"])
        if price <= 0:
            continue
        while credit - used >= price:
            used += price
            minutes += int(tier["duration_minutes"])
            if tier_used is None:  # the biggest rate applied sets cap and speed
                tier_used = tier

    return {
        "pulses": pulses,
        "pulse_centavos": pulse_centavos,
        "credit_centavos": credit,
        "used_centavos": used,
        "leftover_centavos": credit - used,
        "minutes": minutes,
        "seconds": minutes * 60,
        "tier": catalog.plan_summary(tier_used) if tier_used is not None else None,
        "can_grant": minutes > 0,
    }


def create_voucher(plan_id: int, quantity: int = 1) -> tuple[sqlite3.Row, list[str]]:
    """Sell codes at the counter with coin credit already in the wallet."""
    plan = catalog.get_plan(plan_id)
    if plan is None or not plan["is_active"]:
        raise KioskError("invalid_plan")
    quantity = max(1, min(5, int(quantity)))
    price = int(plan["price_centavos"]) * quantity
    if billing.balance_centavos() < price:
        raise KioskError("insufficient_credit")

    batch_id, codes = vouchers.create_batch(
        plan=plan, quantity=quantity, label="Kiosk sale", source="kiosk", actor="kiosk"
    )
    with db.connect() as conn:
        try:
            billing.consume(conn, price, voucher_id=None, note=f"kiosk vouchers batch {batch_id}")
        except billing.InsufficientCredit as exc:
            raise KioskError("insufficient_credit") from exc
        db.audit(conn, "kiosk", "kiosk.voucher", f"batch {batch_id}: {quantity} x {plan['name']}")
        return plan, codes
