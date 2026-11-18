"""In-memory router driver.

Purpose: let the full product (portal, kiosk, caps, throttling decisions) run
and be tested on a laptop with no radios and no root. Traffic is simulated at
the configured kbps so data caps are observable in a demo.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Sequence

from ..utils import stable_mac
from .base import Grant, RouterDriver, Usage


class MockRouter(RouterDriver):
    name = "mock"

    def __init__(self, kbps: int = 240) -> None:
        self.kbps = max(0, kbps)
        self._lock = threading.Lock()
        self._grants: dict[str, Grant] = {}
        self._usage: dict[str, Usage] = {}
        self._since: dict[str, float] = {}
        self._last_tick = time.monotonic()
        self._ip_index: dict[str, str] = {}
        self._syncs = 0

    # -- lifecycle ---------------------------------------------------------
    def ensure_ready(self) -> None:
        with self._lock:
            self._last_tick = time.monotonic()

    # -- core --------------------------------------------------------------
    def sync(self, grants: Sequence[Grant]) -> None:
        with self._lock:
            self._account_locked()
            wanted = {g.mac: g for g in grants if g.mac}
            for mac in list(self._grants):
                if mac not in wanted:
                    self._grants.pop(mac, None)
                    self._since.pop(mac, None)
                    self._usage.pop(mac, None)
            for mac, grant in wanted.items():
                if mac not in self._grants:
                    self._since[mac] = time.monotonic()
                    self._usage.setdefault(mac, Usage())
                self._grants[mac] = grant
                if grant.ip:
                    self._ip_index[grant.ip] = mac
            self._syncs += 1

    def read_usage(self) -> dict[str, Usage]:
        with self._lock:
            self._account_locked()
            return {mac: Usage(u.rx_bytes, u.tx_bytes) for mac, u in self._usage.items()}

    def lookup_mac(self, ip: str | None) -> str | None:
        if not ip:
            return None
        with self._lock:
            known = self._ip_index.get(ip)
        return known or stable_mac(ip)

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "driver": self.name,
                "state": "ok",
                "grants": len(self._grants),
                "syncs": self._syncs,
                "simulated_kbps": self.kbps,
                "devices": sorted(self._ip_index.items())[:20],
            }

    def describe(self) -> dict[str, Any]:
        return {"driver": self.name, "kbps": self.kbps}

    # -- test/demo helpers -------------------------------------------------
    def set_usage(self, mac: str, rx_bytes: int, tx_bytes: int = 0) -> None:
        """Force counters, so cap enforcement can be tested deterministically."""
        with self._lock:
            self._usage[mac] = Usage(rx_bytes, tx_bytes)
            self._last_tick = time.monotonic()

    def active_grants(self) -> list[Grant]:
        with self._lock:
            return list(self._grants.values())

    def reset(self) -> None:
        """Forget every grant and counter (tests, demo restarts)."""
        with self._lock:
            self._grants.clear()
            self._usage.clear()
            self._since.clear()
            self._ip_index.clear()
            self._last_tick = time.monotonic()

    # -- internals ---------------------------------------------------------
    def _account_locked(self) -> None:
        now = time.monotonic()
        elapsed = max(0.0, now - self._last_tick)
        self._last_tick = now
        if elapsed <= 0 or not self.kbps:
            return
        rate_bytes = (self.kbps * 1000 / 8) * elapsed
        for mac in self._grants:
            usage = self._usage.setdefault(mac, Usage())
            usage.rx_bytes += int(rate_bytes)
            usage.tx_bytes += int(rate_bytes / 6)
