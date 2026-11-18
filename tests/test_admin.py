"""Admin console: auth, dashboard, plans, vouchers, cash, settings."""

from __future__ import annotations

from app.services import billing, catalog, sessions, vouchers
from app.utils import stable_mac

CREDENTIALS = {"username": "admin", "password": "testpass123"}


def _login(client, csrf):
    return client.post("/admin/login", data={**CREDENTIALS, "csrf_token": csrf})


def test_admin_pages_require_a_login(client):
    response = client.get("/admin")

    assert response.url.path == "/admin/login"
    assert "password" in response.text.lower()


def test_wrong_password_is_refused(client, csrf):
    response = client.post(
        "/admin/login",
        data={"username": "admin", "password": "nope", "csrf_token": csrf},
    )

    assert response.url.path == "/admin/login"
    assert "Wrong username or password" in response.text


def test_login_opens_the_dashboard(client, csrf):
    response = _login(client, csrf)

    assert response.status_code == 200
    assert response.url.path == "/admin"
    assert "BAGSIK PISOWIFI" in response.text

    again = client.get("/admin")
    assert again.status_code == 200


def test_dashboard_json_feeds_the_live_tables(client, csrf):
    _login(client, csrf)

    payload = client.get("/admin/api/overview").json()

    assert payload["stats"]["live"] == 0
    assert payload["router"]["driver"] == "mock"
    assert payload["sessions"] == []


def test_every_admin_page_renders(client, csrf):
    _login(client, csrf)

    for path in ("/admin/vouchers", "/admin/plans", "/admin/sessions", "/admin/coins", "/admin/settings"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_admin_can_create_a_plan(client, csrf):
    _login(client, csrf)

    response = client.post(
        "/admin/plans/create",
        data={
            "name": "PISO 2 - 10 mins",
            "price": "2.00",
            "minutes": "10",
            "data_cap_mb": "100",
            "down_kbps": "2048",
            "up_kbps": "512",
            "sort_order": "15",
            "csrf_token": csrf,
        },
    )

    assert "created" in response.text
    names = [plan["name"] for plan in catalog.list_plans(active_only=False)]
    assert "PISO 2 - 10 mins" in names


def test_admin_generates_and_prints_vouchers(client, csrf):
    _login(client, csrf)
    plan = catalog.list_plans()[0]

    response = client.post(
        "/admin/vouchers/generate",
        data={
            "plan_id": str(plan["id"]),
            "quantity": "3",
            "label": "Morning batch",
            "note": "counter",
            "csrf_token": csrf,
        },
    )

    assert response.url.path == "/admin/vouchers/print"
    assert "Batch #" in response.text
    assert response.text.count('class="ticket"') == 3
    assert vouchers.counts()["unused"] == 3


def test_admin_adds_and_clears_the_wallet(client, csrf):
    _login(client, csrf)

    client.post("/admin/coins/add", data={"amount": "5.00", "csrf_token": csrf})
    assert billing.balance_centavos() == 500

    response = client.post("/admin/coins/reset", data={"csrf_token": csrf})
    assert "Wallet cleared" in response.text
    assert billing.balance_centavos() == 0


def test_admin_saves_business_settings(client, csrf):
    _login(client, csrf)

    client.post(
        "/admin/settings",
        data={
            "business_name": "Juan's WiFi",
            "portal_headline": "Konek na!",
            "portal_subtext": "Piso lang",
            "portal_contact": "Tanong kay Juan",
            "print_footer": "Salamat",
            "grace_seconds": "30",
            "csrf_token": csrf,
        },
    )

    assert catalog.setting("business_name") == "Juan's WiFi"
    assert catalog.setting("kiosk_voucher_enabled") == "0"
    assert catalog.setting("grace_seconds") == "30"


def test_admin_can_terminate_a_session(client, csrf):
    _login(client, csrf)
    plan = catalog.list_plans()[0]
    mac = stable_mac("10.0.0.55")
    from app import db

    with db.connect() as conn:
        row = sessions.start_session(conn, plan=plan, mac=mac, ip="10.0.0.55")

    response = client.post(
        f"/admin/sessions/{row['id']}/terminate", data={"csrf_token": csrf}
    )

    assert "terminated" in response.text
    assert sessions.get_session(row["id"])["status"] == "terminated"


def test_logout_clears_the_cookie(client, csrf):
    _login(client, csrf)

    response = client.post("/admin/logout", data={"csrf_token": csrf})

    assert response.url.path == "/admin/login"
    assert client.get("/admin").url.path == "/admin/login"
