"""Router driver contract.

The app never talks to nftables or tc directly: it asks a driver to make the
network match a declarative list of grants. The same session/enforcement logic
therefore runs unchanged on real hardware and in the mock driver used for
development and tests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class Grant:
    """One allowed device, with the plan's shaping limits."""

    session_id: int
    mac: str
    ip: str | None
    down_kbps: int = 0
    up_kbps: int = 0
    label: str = ""


@dataclass
class Usage:
    """Cumulative bytes counted by the driver for a device."""

    rx_bytes: int = 0
    tx_bytes: int = 0

    @property
    def total(self) -> int:
        return self.rx_bytes + self.tx_bytes


class RouterDriver(ABC):
    name = "base"

    @abstractmethod
    def ensure_ready(self) -> None:
        """Prepare the router (tables, chains, queues). Called once at startup."""

    @abstractmethod
    def sync(self, grants: Sequence[Grant]) -> None:
        """Make the router match exactly this set of grants."""

    @abstractmethod
    def read_usage(self) -> dict[str, Usage]:
        """Return cumulative byte counters keyed by MAC address."""

    def lookup_mac(self, ip: str | None) -> str | None:
        """Resolve the MAC address currently holding an IP."""
        return None

    def health(self) -> dict[str, Any]:
        return {"driver": self.name, "state": "ok"}

    def describe(self) -> dict[str, Any]:
        return {"driver": self.name}


class RouterError(RuntimeError):
    """Raised when the driver cannot apply a configuration."""
