"""Configuration for BAGSIK PISOWIFI.

Deliberately dependency-free: a dotenv reader plus one frozen dataclass. The
whole config fits on one screen so it is obvious what a deployment needs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APP_VERSION = "1.0.0"


def load_dotenv(path: Path | None = None) -> None:
    """Populate os.environ from a .env file without overriding real env vars."""
    path = path or BASE_DIR / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _str(name: str, default: str = "") -> str:
    return os.environ.get(f"BAGSIK_{name}", default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_str(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_str(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    value = _str(name, "1" if default else "0").lower()
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str
    version: str
    secret_key: str
    db_path: Path
    timezone: str

    admin_user: str
    admin_password: str
    session_hours: int

    router_driver: str
    lan_iface: str
    wan_iface: str
    lan_cidr: str
    enforce_seconds: int

    coin_driver: str
    gpio_pin: int
    coin_pulse_centavos: int
    coin_idle_seconds: float
    coin_arm_seconds: int

    portal_url: str
    mock_kbps: int
    dev_mode: bool

    @property
    def cookie_name(self) -> str:
        return "bagsik_admin"

    @property
    def csrf_cookie_name(self) -> str:
        return "bagsik_csrf"

    @property
    def static_dir(self) -> Path:
        return Path(__file__).resolve().parent / "static"

    @property
    def template_dir(self) -> Path:
        return Path(__file__).resolve().parent / "templates"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv()
    db_path = Path(_str("DB_PATH", "./data/bagsik.db"))
    if not db_path.is_absolute():
        db_path = (BASE_DIR / db_path).resolve()
    return Settings(
        app_name="BAGSIK PISOWIFI",
        version=APP_VERSION,
        secret_key=_str("SECRET_KEY", "bagsik-dev-secret-change-me"),
        db_path=db_path,
        timezone=_str("TIMEZONE", "Asia/Manila"),
        admin_user=_str("ADMIN_USER", "admin"),
        admin_password=_str("ADMIN_PASSWORD", "bagsik123"),
        session_hours=_int("SESSION_HOURS", 12),
        router_driver=_str("ROUTER_DRIVER", "mock").lower(),
        lan_iface=_str("LAN_IFACE", "auto"),
        wan_iface=_str("WAN_IFACE", "auto"),
        lan_cidr=_str("LAN_CIDR", "auto"),
        enforce_seconds=max(2, _int("ENFORCE_SECONDS", 10)),
        coin_driver=_str("COIN_DRIVER", "mock").lower(),
        gpio_pin=_int("GPIO_PIN", 17),
        coin_pulse_centavos=max(1, _int("COIN_PULSE_CENTAVOS", 100)),
        coin_idle_seconds=max(0.2, _float("COIN_IDLE_SECONDS", 1.2)),
        coin_arm_seconds=max(15, _int("COIN_ARM_SECONDS", 120)),
        portal_url=_str("PORTAL_URL", "http://10.0.0.1").rstrip("/"),
        mock_kbps=max(0, _int("MOCK_KBPS", 240)),
        dev_mode=_bool("DEV_MODE", False),
    )
