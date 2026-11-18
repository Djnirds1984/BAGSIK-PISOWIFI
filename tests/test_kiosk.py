"""Coin slot: one device at a time, priced by the admin's pulse rates.

The slot only counts pulses after a device taps Insert Coin, the burst is
settled by the background loop (never by the request), and a second customer
is refused while the first still owns the hardware.
"""

from __future__ import annotations

import dataclasses
import time

from app.services import billing, catalog, sessions, vouchers
from app.utils import stable_mac

DEVICE_IP = "10.0.0.55"
OTHER_IP = "10.0.0.66"


def as_device(client, ip: str) -> None:
    """Make subsequent requests come from another customer device."""
    client.headers["X-Forwarded-For"] = ip


def _wait_for(predicate, timeout: float = 8.0) -> bool:
    """The coin loop runs in the app's event loop, so wait for it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _arm(client, csrf, to: str = "/"):
    return client.post("/coin/arm", data={"csrf_token": csrf, "to": to})


def _insert(client, csrf, centavos: int, to: str = "/"):
    return client.post(
        "/kiosk/simulate-coin",
        data={"centavos": centavos, "csrf_token": csrf, "to": to},
    )


def test_portal_shows_the_coin_slot_without_a_plan_picker(client):
    portal = client.get("/")
    assert "data-coin-panel" in portal.text
    assert "Insert Coin" in portal.text
    assert "Choose a plan" not in portal.text

    kiosk = client.get("/kiosk")
    assert kiosk.status_code == 200
    assert "Coin slot" in kiosk.text
    assert "Choose a plan" not in kiosk.text


def test_slot_is_shut_until_a_device_arms_it(client, csrf):
    response = _insert(client, csrf, 500)

    assert "The slot is closed" in response.text
    assert client.app.state.coin.txn_state()["active"] is False
    time.sleep(0.3)
    assert billing.balance_centavos() == 0
    assert sessions.active_for_mac(stable_mac(DEVICE_IP)) is None


def test_armed_slot_credits_time_automatically(client, csrf):
    assert "Coin slot open" in _arm(client, csrf).text
    state = client.get("/coin/state").json()
    assert state["phase"] == "listening"
    assert state["mine"] is True
    assert state["txn"]["pulses"] == 0

    _insert(client, csrf, 500)  # five ₱1 pulses

    assert _wait_for(lambda: sessions.active_for_mac(stable_mac(DEVICE_IP)) is not None)
    row = sessions.active_for_mac(stable_mac(DEVICE_IP))
    assert row["kind"] == "coin"
    assert row["coin_centavos"] == 500
    assert row["plan_name"] == "PISO 5 - 30 mins"
    assert row["time_cap_seconds"] == 30 * 60

    # The burst is consumed from the wallet and the slot reopens for the next
    # customer without anyone pressing Start.
    assert _wait_for(lambda: billing.balance_centavos() == 0)
    assert _wait_for(lambda: client.app.state.coin.txn_state()["active"] is False)
    assert _wait_for(lambda: client.get("/coin/state").json()["phase"] == "granted")


def test_second_device_is_refused_while_the_slot_is_armed(client, csrf):
    _arm(client, csrf)
    as_device(client, OTHER_IP)

    response = _arm(client, csrf)
    assert "Another customer is currently inserting coins" in response.text

    state = client.get("/coin/state").json()
    assert state["phase"] == "busy"
    assert state["mine"] is False
    assert state["txn"]["mac"] == stable_mac(DEVICE_IP)
    assert state["txn"]["seconds_left"] > 0


def test_cancel_releases_the_slot_for_the_next_device(client, csrf):
    _arm(client, csrf)
    response = client.post("/coin/cancel", data={"csrf_token": csrf, "to": "/"})
    assert "Coin slot released" in response.text
    assert client.app.state.coin.txn_state()["active"] is False

    as_device(client, OTHER_IP)
    assert "Coin slot open" in _arm(client, csrf).text
    assert client.get("/coin/state").json()["mine"] is True


def test_a_second_burst_extends_the_running_session(client, csrf):
    mac = stable_mac(DEVICE_IP)
    _arm(client, csrf)
    _insert(client, csrf, 100)
    assert _wait_for(lambda: sessions.active_for_mac(mac) is not None)
    first = sessions.active_for_mac(mac)["time_cap_seconds"]

    assert _wait_for(lambda: client.app.state.coin.txn_state()["active"] is False)
    _arm(client, csrf)
    _insert(client, csrf, 100)

    assert _wait_for(lambda: sessions.active_for_mac(mac)["time_cap_seconds"] > first)
    assert sessions.active_for_mac(mac)["time_cap_seconds"] == first + 5 * 60


def test_an_unused_window_releases_the_lock_by_itself(client, csrf):
    acceptor = client.app.state.coin
    flow = client.app.state.coinflow
    original = acceptor.arm_seconds
    acceptor.arm_seconds = 0.3
    try:
        flow.arm(stable_mac(DEVICE_IP), DEVICE_IP)
        assert client.get("/coin/state").json()["phase"] == "listening"
        time.sleep(0.4)
        flow.tick()
        assert acceptor.txn_state()["active"] is False
    finally:
        acceptor.arm_seconds = original

    as_device(client, OTHER_IP)
    assert "Coin slot open" in _arm(client, csrf).text


def test_a_coin_below_the_cheapest_rate_stays_as_credit(client, csrf):
    _arm(client, csrf)
    _insert(client, csrf, 50)  # half of the smallest bundle

    assert _wait_for(lambda: billing.balance_centavos() == 50)
    assert _wait_for(lambda: client.app.state.coin.txn_state()["active"] is False)
    assert sessions.active_for_mac(stable_mac(DEVICE_IP)) is None


def test_coin_state_reports_what_the_burst_will_buy(client, csrf):
    _arm(client, csrf)
    _insert(client, csrf, 500)
    state = client.get("/coin/state").json()

    assert state["phase"] in {"counting", "processing", "granted", "idle"}
    assert state["mac"] == stable_mac(DEVICE_IP)
    assert state["ip"] == DEVICE_IP
    if state["quote"]:
        assert state["quote"]["pulses"] >= 5
        assert state["quote"]["minutes"] >= 30


def test_voucher_sale_needs_credit(client, csrf):
    plan = catalog.list_plans()[0]
    response = client.post("/kiosk/buy", data={"plan_id": str(plan["id"]), "csrf_token": csrf})

    assert "Not enough credit" in response.text
    assert vouchers.list_vouchers() == []


def test_credit_can_still_be_turned_into_a_code(client, csrf):
    plan = catalog.list_plans()[0]
    billing.add_coin(plan["price_centavos"], source="admin", note="counter float")

    response = client.post("/kiosk/buy", data={"plan_id": str(plan["id"]), "csrf_token": csrf})

    assert "voucher:" in response.text
    assert vouchers.list_vouchers()
    assert vouchers.list_vouchers()[0]["status"] == "unused"
    assert billing.balance_centavos() == 0


def test_coin_simulation_is_off_without_dev_mode(client, csrf):
    state = client.app.state
    original = state.settings
    state.settings = dataclasses.replace(original, dev_mode=False)
    try:
        response = _insert(client, csrf, 500, to="/kiosk")
        assert "only available in development mode" in response.text
    finally:
        state.settings = original

    assert billing.balance_centavos() == 0
