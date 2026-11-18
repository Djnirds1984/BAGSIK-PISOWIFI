"""Wired ports available to the hotspot server.

The box has exactly one uplink and one or more customer-side ports: ``eth0``
carries the internet, every other wired NIC feeds a hotspot (a switch, an
access point in bridge mode, or a direct cable). This module lists the ports the
kernel can see, plus the small amount of parsing the admin console needs.

It is deliberately read-only: addresses, DHCP and NAT are still set up by
``deploy/install.sh`` or by the operator's own network configuration. What is
saved here is the role of each port, which the enforcement path then uses.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

SYS_NET = "/sys/class/net"

# eth0/eth1 (kernel names) and enp3s0/eno1/ens3/enx0011223344 (predictable
# names). Bridges, bonds, docker/veth, tunnels, VPN links and radios are not
# wired customer ports.
_WIRED = re.compile(r"^(eth\d+|en[a-z]{0,2}\d+[a-z0-9]*)$")
_INTERFACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}$")
_IPV4 = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


def is_ethernet(name: str) -> bool:
    return bool(_WIRED.match((name or "").strip()))


def sort_key(name: str) -> tuple:
    """Natural order, so eth2 comes before eth10."""
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part)
        for part in re.split(r"(\d+)", (name or "").strip())
        if part
    )


def split_names(raw: str) -> list[str]:
    """'eth1, eth2 eth3' -> ['eth1', 'eth2', 'eth3']."""
    names: list[str] = []
    for chunk in re.split(r"[,\s]+", raw or ""):
        chunk = chunk.strip()
        if chunk and chunk not in names:
            names.append(chunk)
    return names


def valid_name(name: str) -> bool:
    return bool(_INTERFACE.match((name or "").strip()))


def valid_ipv4(address: str) -> bool:
    match = _IPV4.match((address or "").strip())
    return bool(match) and all(0 <= int(part) <= 255 for part in match.groups())


def default_assignment(
    names: list[str], wan: str = "", hotspots: list[str] | None = None
) -> tuple[str, list[str]]:
    """Fill in the wiring default: eth0 uplink, every other wired port hotspot."""
    known = list(names)
    wan = wan or ("eth0" if "eth0" in known else (known[0] if known else ""))
    servers = [name for name in (hotspots or []) if name and name != wan]
    if not servers:
        servers = [name for name in known if name != wan]
    return wan, servers


def assign_rows(rows: list[dict], wan: str = "", hotspots: list[str] | None = None) -> list[dict]:
    """Per-port table for the admin page, including saved ports not plugged in."""
    hotspots = list(hotspots or [])
    seen = {row["name"] for row in rows}
    extra = [name for name in [wan, *hotspots] if name and name not in seen]
    table = [dict(row) for row in rows]
    table += [
        {"name": name, "state": "not detected", "mac": "", "ipv4": ""} for name in extra
    ]
    table.sort(key=lambda row: sort_key(row["name"]))
    for row in table:
        row["role"] = "wan" if row["name"] == wan else ("hotspot" if row["name"] in hotspots else "off")
    return table


def list_ethernet(sys_net: str = SYS_NET) -> list[dict]:
    """Every wired NIC on this host, with state, MAC and IPv4 (if any)."""
    try:
        names = sorted((name for name in os.listdir(sys_net) if is_ethernet(name)), key=sort_key)
    except OSError:
        return []
    addresses = _ipv4_map()
    ports: list[dict] = []
    for name in names:
        base = os.path.join(sys_net, name)
        if not os.path.isdir(base):
            continue
        ports.append(
            {
                "name": name,
                "state": _read(os.path.join(base, "operstate"), "unknown"),
                "mac": _read(os.path.join(base, "address")).upper(),
                "ipv4": addresses.get(name, ""),
            }
        )
    return ports


def _read(path: str, default: str = "") -> str:
    try:
        return open(path, encoding="ascii", errors="ignore").read().strip() or default
    except OSError:
        return default


def _ipv4_map() -> dict[str, str]:
    """One ``ip`` call for every port, instead of one per port."""
    if shutil.which("ip") is None:
        return {}
    try:
        result = subprocess.run(
            ["ip", "-j", "-4", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return {}
    found: dict[str, str] = {}
    for item in payload if isinstance(payload, list) else [payload]:
        name = item.get("ifname")
        for info in item.get("addr_info", []):
            if info.get("family") == "inet" and info.get("local"):
                found[name] = f"{info['local']}/{info['prefixlen']}"
                break
    return found
