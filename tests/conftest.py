"""Test harness.

The environment is configured for the mock router and mock coin slot *before*
``app.main`` is imported, because the FastAPI app is built at import time.
Every test gets a clean database and a clean mock router; nothing touches
nftables, tc or GPIO.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bagsik-tests-"))
os.environ.update(
    {
        "BAGSIK_DB_PATH": str(_TMP_DIR / "bagsik.db"),
        "BAGSIK_ROUTER_DRIVER": "mock",
        "BAGSIK_COIN_DRIVER": "mock",
        "BAGSIK_COIN_IDLE_SECONDS": "0.25",
        "BAGSIK_DEV_MODE": "true",
        "BAGSIK_SECRET_KEY": "test-secret-key",
        "BAGSIK_ADMIN_USER": "admin",
        "BAGSIK_ADMIN_PASSWORD": "testpass123",
        "BAGSIK_MOCK_KBPS": "0",
        "BAGSIK_ENFORCE_SECONDS": "3600",
        "BAGSIK_TIMEZONE": "Asia/Manila",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.netctl.mock import MockRouter  # noqa: E402

# The mock router derives a stable MAC from the client IP, so tests can act as
# different customers just by changing this header.
DEVICE_IP = "10.0.0.55"

# Children first, so foreign keys stay happy.
_TABLES = (
    "coin_credits",
    "sessions",
    "vouchers",
    "batches",
    "devices",
    "audit_logs",
    "plans",
    "admin_users",
    "settings",
)


def _wipe() -> None:
    settings = get_settings()
    with db.connect() as conn:
        for table in _TABLES:
            conn.execute(f"DELETE FROM {table}")
        db.seed(conn, settings)


@pytest.fixture()
def settings():
    return get_settings()


@pytest.fixture()
def router() -> MockRouter:
    driver = app.state.router
    assert isinstance(driver, MockRouter), "tests must run with the mock driver"
    return driver


@pytest.fixture()
def client(router: MockRouter):
    db.init_db(get_settings())
    _wipe()
    router.reset()
    # The coin slot is a single hardware window: never carry one into a test.
    app.state.coin.reset()
    app.state.coinflow.reset()
    while not app.state.coin.events.empty():
        app.state.coin.events.get_nowait()
    with TestClient(app) as test_client:
        test_client.headers.update({"X-Forwarded-For": DEVICE_IP})
        yield test_client


@pytest.fixture()
def csrf(client: TestClient) -> str:
    """Load a page so the CSRF cookie exists, then return its value."""
    client.get("/")
    return client.cookies.get(get_settings().csrf_cookie_name, "")


def as_device(client: TestClient, ip: str) -> None:
    """Make subsequent requests come from another customer device."""
    client.headers["X-Forwarded-For"] = ip


def mac_for(ip: str) -> str:
    from app.utils import stable_mac

    return stable_mac(ip)
