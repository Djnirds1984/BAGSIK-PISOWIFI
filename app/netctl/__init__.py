"""Router driver selection.

BAGSIK PISOWIFI is a standalone appliance: the same box serves the Wi-Fi,
hands out DHCP, routes the traffic and enforces who may pass. There is no
external router vendor in the loop, so there are exactly two drivers:

  * ``local`` - the real one: nftables + tc on this machine.
  * ``mock``  - in-memory simulation for development and tests.
"""

from __future__ import annotations

from ..config import Settings
from .base import Grant, RouterDriver, Usage

__all__ = ["Grant", "RouterDriver", "Usage", "build_driver"]


def build_driver(settings: Settings) -> RouterDriver:
    if settings.router_driver == "local":
        from .local import LocalRouter

        return LocalRouter(settings)
    from .mock import MockRouter

    return MockRouter(kbps=settings.mock_kbps)
