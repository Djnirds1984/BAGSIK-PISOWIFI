"""The nftables script builder, tested without root or real interfaces.

The script is the whole enforcement policy: a default-drop forward chain plus
one accept rule (and one named counter) per paying device. Getting it wrong
either lets freeloaders through or cuts paying customers off, so it is worth
asserting on the exact syntax.
"""

from __future__ import annotations

from dataclasses import replace

from app.config import get_settings
from app.netctl.base import Grant
from app.netctl.local import LocalRouter, _Counter


def _router() -> LocalRouter:
    settings = replace(
        get_settings(),
        router_driver="local",
        lan_iface="wlan0",
        wan_iface="eth0",
        lan_cidr="10.0.0.1/24",
    )
    return LocalRouter(settings)


def test_base_policy_drops_forwarded_traffic():
    script = _router()._build_script([])

    assert "add chain inet bagsik forward { type filter hook forward priority filter; policy drop; }" in script
    assert "add rule inet bagsik forward ct state established,related accept" in script
    assert "add rule inet bagsik forward ip saddr 10.0.0.1/24 ip daddr 10.0.0.1/24 accept" in script
    # No grants means no accept rules at all.
    assert "counter name" not in script


def test_each_grant_gets_an_accept_rule_and_a_counter():
    script = _router()._build_script(
        [
            Grant(session_id=1, mac="02:00:00:00:00:01", ip="10.0.0.20", down_kbps=2000, up_kbps=512),
            Grant(session_id=2, mac="02:00:00:00:00:02", ip="10.0.0.21", down_kbps=0, up_kbps=0),
        ]
    )

    assert "add counter inet bagsik d1" in script
    assert "add rule inet bagsik forward ip saddr 10.0.0.20 counter name d1 accept" in script
    assert "add rule inet bagsik forward ip daddr 10.0.0.20 counter name d1 accept" in script
    assert "add rule inet bagsik forward ip saddr 10.0.0.21 counter name d2 accept" in script


def test_grants_without_an_ip_are_skipped():
    script = _router()._build_script(
        [Grant(session_id=1, mac="02:00:00:00:00:01", ip=None, down_kbps=1000)]
    )

    assert "counter name" not in script


def test_counters_survive_a_rebuild():
    router = _router()
    router._counters = {"d1": _Counter(name="d1", packets=12, bytes=987654)}

    script = router._build_script(
        [Grant(session_id=1, mac="02:00:00:00:00:01", ip="10.0.0.20", down_kbps=1000)]
    )

    assert "add counter inet bagsik d1 { packets 12 bytes 987654; }" in script
