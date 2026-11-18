"""Customer portal: codes, disconnects and captive-portal probes."""

from __future__ import annotations

from app.services import catalog, sessions, vouchers
from app.utils import stable_mac

DEVICE_IP = "10.0.0.55"
OTHER_IP = "10.0.0.56"


def _issue(quantity: int = 1):
    plan = catalog.list_plans()[0]
    _, codes = vouchers.create_batch(plan=plan, quantity=quantity)
    return plan, codes


def test_home_reaches_the_customer(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "BAGSIK PISOWIFI" in response.text
    assert "Redeem a voucher code" in response.text
    assert stable_mac(DEVICE_IP) in response.text


def test_redeem_code_starts_a_session(client, csrf):
    plan, codes = _issue()

    response = client.post("/redeem", data={"code": codes[0], "csrf_token": csrf})

    assert response.status_code == 200
    assert "You are online" in response.text

    row = sessions.active_for_mac(stable_mac(DEVICE_IP))
    assert row is not None
    assert row["status"] == "active"
    assert row["kind"] == "voucher"
    assert row["plan_name"] == plan["name"]
    assert vouchers.get_by_code(codes[0])["status"] == "active"


def test_code_cannot_be_used_twice(client, csrf):
    _, codes = _issue()
    client.post("/redeem", data={"code": codes[0], "csrf_token": csrf})

    response = client.post(
        "/redeem",
        headers={"X-Forwarded-For": OTHER_IP},
        data={"code": codes[0], "csrf_token": csrf},
    )

    assert response.status_code == 200
    assert "already in use" in response.text
    assert sessions.active_for_mac(stable_mac(OTHER_IP)) is None


def test_one_session_per_device(client, csrf):
    _, codes = _issue(quantity=2)
    client.post("/redeem", data={"code": codes[0], "csrf_token": csrf})

    response = client.post("/redeem", data={"code": codes[1], "csrf_token": csrf})

    assert "already has an active session" in response.text
    assert vouchers.get_by_code(codes[1])["status"] == "unused"


def test_unknown_code_is_reported(client, csrf):
    response = client.post("/redeem", data={"code": "AAAA-BBBB", "csrf_token": csrf})

    assert "not found" in response.text


def test_disconnect_ends_the_session(client, csrf):
    _, codes = _issue()
    client.post("/redeem", data={"code": codes[0], "csrf_token": csrf})
    row = sessions.active_for_mac(stable_mac(DEVICE_IP))
    assert row is not None

    response = client.post("/disconnect", data={"csrf_token": csrf})

    assert response.status_code == 200
    assert "Session stopped" in response.text
    assert sessions.get_session(row["id"])["status"] == "terminated"
    assert vouchers.get_by_code(codes[0])["status"] == "used"


def test_redeem_rejects_a_wrong_form_token(client, csrf):
    _, codes = _issue()

    response = client.post("/redeem", data={"code": codes[0], "csrf_token": "nope"})

    assert response.status_code == 400
    assert sessions.active_for_mac(stable_mac(DEVICE_IP)) is None


def test_captive_probes_point_at_the_portal(client, settings):
    for path in ("/generate_204", "/hotspot-detect.html", "/connecttest.txt"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == f"{settings.portal_url}/"

    info = client.get("/.well-known/captive-portal").json()
    assert info["captive"] is True
    assert info["portal"] == f"{settings.portal_url}/"
