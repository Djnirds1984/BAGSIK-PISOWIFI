"""Hotspot server page: WAN uplink, customer ports, enforcement wiring."""

from __future__ import annotations

from app.config import get_settings
from app.netctl import interfaces as net_ports
from app.netctl.local import LocalRouter
from app.services import catalog

CREDENTIALS = {"username": "admin", "password": "testpass123"}
ETH0_ETH1 = [
    {"name": "eth0", "state": "up", "mac": "DC:A6:32:00:00:01", "ipv4": "192.168.1.20/24"},
    {"name": "eth1", "state": "down", "mac": "DC:A6:32:00:00:02", "ipv4": ""},
]


def _login(client, csrf):
    return client.post("/admin/login", data={**CREDENTIALS, "csrf_token": csrf})


def _ports(monkeypatch, rows):
    monkeypatch.setattr(net_ports, "list_ethernet", lambda: rows)


def test_only_wired_ports_are_candidates():
    assert net_ports.is_ethernet("eth0")
    assert net_ports.is_ethernet("eth12")
    assert net_ports.is_ethernet("enp3s0")
    assert not net_ports.is_ethernet("wlan0")
    assert not net_ports.is_ethernet("lo")
    assert not net_ports.is_ethernet("docker0")
    assert not net_ports.is_ethernet("br-lan")
    # natural order, so eth2 is listed before eth10
    names = ["eth10", "eth2", "eth1"]
    assert sorted(names, key=net_ports.sort_key) == ["eth1", "eth2", "eth10"]


def test_eth0_carries_the_internet_and_the_rest_serve_customers():
    wan, hotspots = net_ports.default_assignment(["eth0", "eth1", "eth2"])

    assert wan == "eth0"
    assert hotspots == ["eth1", "eth2"]


def test_a_saved_assignment_is_not_overwritten_by_the_default():
    wan, hotspots = net_ports.default_assignment(["eth0", "eth1"], "eth1", ["eth0"])

    assert (wan, hotspots) == ("eth1", ["eth0"])


def test_page_defaults_to_eth0_wan_eth1_hotspot(client, csrf, monkeypatch):
    _login(client, csrf)
    _ports(monkeypatch, ETH0_ETH1)

    html = client.get("/admin/settings/hotspot").text

    assert 'name="role:eth0"' in html
    assert 'name="role:eth1"' in html
    assert 'value="wan"' in html and 'value="hotspot"' in html
    assert 'value="wan" selected' in html          # eth0 is the uplink
    assert "10.0.0.1" in html
    assert "LAN_IFACE=eth1" in html and "WAN_IFACE=eth0" in html


def test_saving_roles_persists_wan_and_every_hotspot_port(client, csrf, monkeypatch):
    _login(client, csrf)
    _ports(monkeypatch, [*ETH0_ETH1, {"name": "eth2", "state": "up", "mac": "", "ipv4": ""}])

    response = client.post(
        "/admin/settings/hotspot",
        data={
            "role:eth0": "wan",
            "role:eth1": "hotspot",
            "role:eth2": "hotspot",
            "address": "10.0.0.1",
            "csrf_token": csrf,
        },
    )

    assert "eth1, eth2" in response.text
    assert catalog.setting("net_wan_iface") == "eth0"
    assert catalog.setting("net_hotspot_ifaces") == "eth1,eth2"
    assert catalog.setting("net_hotspot_ip") == "10.0.0.1"


def test_a_port_can_be_added_before_it_is_plugged_in(client, csrf, monkeypatch):
    _login(client, csrf)
    _ports(monkeypatch, ETH0_ETH1)

    client.post(
        "/admin/settings/hotspot",
        data={
            "role:eth0": "wan",
            "role:eth1": "hotspot",
            "extra": "eth3",
            "address": "10.0.0.1",
            "csrf_token": csrf,
        },
    )

    assert catalog.setting("net_hotspot_ifaces") == "eth1,eth3"
    html = client.get("/admin/settings/hotspot").text
    assert 'name="role:eth3"' in html
    assert "not detected" in html


def test_a_box_without_a_second_port_is_refused(client, csrf, monkeypatch):
    _login(client, csrf)
    _ports(monkeypatch, ETH0_ETH1)

    response = client.post(
        "/admin/settings/hotspot",
        data={"role:eth0": "wan", "role:eth1": "off", "address": "10.0.0.1", "csrf_token": csrf},
    )

    assert "at least one hotspot port" in response.text
    assert catalog.setting("net_hotspot_ifaces") == ""


def test_a_bad_address_is_refused(client, csrf, monkeypatch):
    _login(client, csrf)
    _ports(monkeypatch, ETH0_ETH1)

    response = client.post(
        "/admin/settings/hotspot",
        data={
            "role:eth0": "wan",
            "role:eth1": "hotspot",
            "address": "10.0.0.300",
            "csrf_token": csrf,
        },
    )

    assert "not an IPv4 address" in response.text
    assert catalog.setting("net_hotspot_ip") == ""


def test_manual_entry_works_when_the_box_shows_no_ports(client, csrf, monkeypatch):
    _login(client, csrf)
    _ports(monkeypatch, [])

    html = client.get("/admin/settings/hotspot").text
    assert 'name="wan_name"' in html and 'name="hotspot_names"' in html

    client.post(
        "/admin/settings/hotspot",
        data={"wan_name": "eth0", "hotspot_names": "eth1, eth2", "address": "10.0.0.1", "csrf_token": csrf},
    )

    assert catalog.setting("net_wan_iface") == "eth0"
    assert catalog.setting("net_hotspot_ifaces") == "eth1,eth2"


def test_local_driver_enforces_on_every_saved_port():
    driver = LocalRouter(get_settings())

    assert driver.set_interfaces(["eth1", "eth2"], "eth0", "10.0.0.1/24") is True

    health = driver.health()
    assert health["lan_iface"] == "eth1,eth2"
    assert health["lan_ifaces"] == ["eth1", "eth2"]
    assert health["wan_iface"] == "eth0"
    assert health["lan_cidr"] == "10.0.0.1/24"

    # A later save that only renames the ports keeps the driver consistent.
    assert driver.set_interfaces(["eth3"], "eth0", "10.0.0.1/24") is True
    assert driver.health()["lan_ifaces"] == ["eth3"]
