"""Local router driver: this box *is* the system.

Runs on any Linux SBC or mini PC (Raspberry Pi, Orange Pi, Banana Pi, Intel
N100 boxes, old laptops...). The box serves the Wi-Fi, hands out DHCP, routes
and enforces - no external router vendor involved.

Enforcement model
-----------------
  * nftables table ``inet bagsik`` owns the forward chain with policy DROP, so
    an unpaid device can reach nothing but the box itself (which is where the
    captive portal lives - it is answered by the input chain, not forwarded).
  * Every live session gets an explicit accept rule plus a named counter, so
    the dashboard reads real byte totals per device.
  * ``tc`` HTB classes shape download per client IP on the LAN interface, and
    an ingress policer shapes upload.

Interfaces are auto-detected: the default-route NIC is the uplink, the
remaining NIC with an IPv4 address (Wi-Fi preferred) is the customer side.
Set BAGSIK_LAN_IFACE / BAGSIK_WAN_IFACE / BAGSIK_LAN_CIDR to override.

Everything is rebuilt from the grant list on each sync, and counter values are
carried across rebuilds so data caps stay accurate.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Any, Sequence

from ..config import Settings
from .base import Grant, RouterDriver, RouterError, Usage

log = logging.getLogger("bagsik.netctl")

TABLE = "bagsik"
ROOT_QDISC = "2:"
DEFAULT_CLASS = 999
INGRESS_QDISC = "ffff:"
SYS_NET = "/sys/class/net"


@dataclass
class _Counter:
    name: str
    packets: int = 0
    bytes: int = 0


class LocalRouter(RouterDriver):
    name = "local"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lan = settings.lan_iface
        self.wan = settings.wan_iface
        self.lan_cidr = settings.lan_cidr
        self._lock = threading.Lock()
        self._ready = False
        self._last_error = ""
        self._grant_map: dict[str, str] = {}  # mac -> counter name
        self._counters: dict[str, _Counter] = {}

    # -- public API --------------------------------------------------------
    def ensure_ready(self) -> None:
        for binary in ("nft", "tc", "ip"):
            if shutil.which(binary) is None:
                raise RouterError(f"'{binary}' not found - install nftables/iproute2")
        self._resolve_interfaces()
        self._run(["nft", "delete", "table", "inet", TABLE], check=False)
        self._nft(self._base_rules())
        self._ready = True
        log.info(
            "local router ready: lan=%s (%s) wan=%s", self.lan, self.lan_cidr, self.wan or "?"
        )

    def sync(self, grants: Sequence[Grant]) -> None:
        if not self._ready:
            self.ensure_ready()
        with self._lock:
            self._counters = self._read_counters()
            # The table is rebuilt from scratch so removed sessions can never
            # leave a stale accept rule behind. Counters are re-seeded.
            self._run(["nft", "delete", "table", "inet", TABLE], check=False)
            self._nft(self._build_script(grants))
            self._sync_tc(grants)
            self._grant_map = {
                grant.mac: self._counter_name(index)
                for index, grant in enumerate(grants, start=1)
                if grant.ip
            }
            self._ready = True

    def read_usage(self) -> dict[str, Usage]:
        counters = self._read_counters()
        usage: dict[str, Usage] = {}
        for mac, counter_name in list(self._grant_map.items()):
            counter = counters.get(counter_name)
            if counter is None:
                continue
            usage[mac] = Usage(rx_bytes=counter.bytes, tx_bytes=0)
        return usage

    def lookup_mac(self, ip: str | None) -> str | None:
        if not ip:
            return None
        try:
            out = self._run(["ip", "neigh", "show", ip])
        except RouterError:
            return None
        match = re.search(r"lladdr\s+([0-9a-fA-F:]{17})", out)
        return match.group(1).lower() if match else None

    def health(self) -> dict[str, Any]:
        return {
            "driver": self.name,
            "state": "ok" if self._ready else ("error" if self._last_error else "init"),
            "lan_iface": self.lan,
            "wan_iface": self.wan,
            "lan_cidr": self.lan_cidr,
            "grants": len(self._grant_map),
            "error": self._last_error,
        }

    def describe(self) -> dict[str, Any]:
        return {"driver": self.name, "lan": self.lan, "wan": self.wan, "cidr": self.lan_cidr}

    # -- interface discovery ----------------------------------------------
    def _resolve_interfaces(self) -> None:
        """Fill in LAN/WAN/CIDR when they are configured as 'auto'."""
        if self.lan not in ("auto", "") and self.lan_cidr not in ("auto", "") and self.wan not in ("auto", ""):
            return

        default_ifaces = [
            route.get("dev")
            for route in self._json(["ip", "-j", "route", "show", "default"])
            if route.get("dev")
        ]
        if self.wan in ("auto", "") and default_ifaces:
            self.wan = default_ifaces[0]

        candidates: list[tuple[bool, str, str]] = []
        try:
            names = sorted(os.listdir(SYS_NET))
        except OSError:
            names = []
        for name in names:
            if name == "lo" or name == self.wan:
                continue
            if not os.path.isdir(os.path.join(SYS_NET, name)):
                continue
            cidr = self._ipv4_cidr(name)
            if not cidr:
                continue
            wireless = os.path.isdir(os.path.join(SYS_NET, name, "wireless"))
            candidates.append((wireless, name, cidr))

        if self.lan in ("auto", ""):
            if not candidates:
                raise RouterError(
                    "no LAN interface with an IPv4 address - configure the access point "
                    "interface first (BAGSIK_LAN_IFACE)"
                )
            candidates.sort(key=lambda item: (not item[0], item[1]))  # Wi-Fi wins
            self.lan = candidates[0][1]

        if self.lan_cidr in ("auto", ""):
            self.lan_cidr = self._ipv4_cidr(self.lan) or ""
            if not self.lan_cidr:
                raise RouterError(f"interface {self.lan} has no IPv4 address - check dnsmasq/static IP")

    def _ipv4_cidr(self, iface: str) -> str | None:
        try:
            data = self._json(["ip", "-j", "-4", "addr", "show", "dev", iface])
        except RouterError:
            return None
        for item in data:
            for info in item.get("addr_info", []):
                if info.get("family") == "inet" and info.get("local"):
                    return f"{info['local']}/{info['prefixlen']}"
        return None

    def _json(self, args: list[str]) -> list[dict]:
        try:
            raw = self._run(args)
        except RouterError:
            return []
        try:
            payload = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return []
        return payload if isinstance(payload, list) else [payload]

    # -- nftables ----------------------------------------------------------
    def _base_rules(self) -> list[str]:
        return [
            f"add table inet {TABLE}",
            f"add chain inet {TABLE} forward {{ type filter hook forward priority filter; policy drop; }}",
            f"add rule inet {TABLE} forward ct state established,related accept",
            f"add rule inet {TABLE} forward ip saddr {self.lan_cidr} ip daddr {self.lan_cidr} accept",
        ]

    def _counter_name(self, index: int) -> str:
        return f"d{index}"

    def _build_script(self, grants: Sequence[Grant]) -> str:
        lines = self._base_rules()

        seen: set[str] = set()
        for index, grant in enumerate(grants, start=1):
            if not grant.ip or grant.ip in seen:
                continue
            seen.add(grant.ip)
            counter = self._counter_name(index)
            previous = self._counters.get(counter)
            if previous is not None:
                lines.append(
                    f"add counter inet {TABLE} {counter} "
                    f"{{ packets {previous.packets} bytes {previous.bytes}; }}"
                )
            else:
                lines.append(f"add counter inet {TABLE} {counter}")
            lines.append(
                f"add rule inet {TABLE} forward ip saddr {grant.ip} "
                f"counter name {counter} accept"
            )
            lines.append(
                f"add rule inet {TABLE} forward ip daddr {grant.ip} "
                f"counter name {counter} accept"
            )
        return "\n".join(lines)

    def _nft(self, script: str | list[str], check: bool = True) -> str:
        body = script if isinstance(script, str) else "\n".join(script)
        return self._run(["nft", "-f", "-"], stdin=body + "\n", check=check)

    def _read_counters(self) -> dict[str, _Counter]:
        try:
            raw = self._run(["nft", "-j", "list", "table", "inet", TABLE])
        except RouterError:
            return {}
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        counters: dict[str, _Counter] = {}
        for item in payload.get("nftables", []):
            counter = item.get("counter")
            if not counter:
                continue
            name = counter.get("name")
            if not name:
                continue
            counters[name] = _Counter(
                name=name, packets=int(counter.get("packets", 0)), bytes=int(counter.get("bytes", 0))
            )
        return counters

    # -- tc shaping --------------------------------------------------------
    def _sync_tc(self, grants: Sequence[Grant]) -> None:
        shaped = [grant for grant in grants if grant.ip and grant.down_kbps > 0]
        try:
            self._run(["tc", "qdisc", "replace", "dev", self.lan, "root", "handle", ROOT_QDISC, "htb", "default", str(DEFAULT_CLASS)])
            self._run(
                ["tc", "class", "replace", "dev", self.lan, "parent", ROOT_QDISC,
                 "classid", f"{ROOT_QDISC}{DEFAULT_CLASS}", "htb", "rate", "1000mbit"]
            )
        except RouterError as exc:
            self._last_error = f"tc root: {exc}"
            return

        for index, grant in enumerate(grants, start=1):
            if not grant.ip or grant.down_kbps <= 0:
                continue
            classid = f"{ROOT_QDISC}{index}"
            try:
                self._run(
                    ["tc", "class", "replace", "dev", self.lan, "parent", ROOT_QDISC,
                     "classid", classid, "htb", "rate", f"{grant.down_kbps}kbit",
                     "ceil", f"{grant.down_kbps}kbit", "burst", "32k"]
                )
                self._run(
                    ["tc", "filter", "replace", "dev", self.lan, "parent", ROOT_QDISC,
                     "protocol", "ip", "prio", "1", "u32", "match", "ip", "dst",
                     f"{grant.ip}/32", "flowid", classid]
                )
            except RouterError as exc:
                self._last_error = f"tc class {grant.ip}: {exc}"

        if shaped:
            self._sync_ingress([grant for grant in grants if grant.ip and grant.up_kbps > 0])

    def _sync_ingress(self, grants: Sequence[Grant]) -> None:
        try:
            self._run(["tc", "qdisc", "replace", "dev", self.lan, "handle", INGRESS_QDISC, "ingress"])
        except RouterError as exc:
            self._last_error = f"tc ingress: {exc}"
            return
        for grant in grants:
            try:
                self._run(
                    ["tc", "filter", "replace", "dev", self.lan, "parent", INGRESS_QDISC,
                     "protocol", "ip", "prio", "1", "u32", "match", "ip", "src",
                     f"{grant.ip}/32", "police", "rate", f"{grant.up_kbps}kbit",
                     "burst", "32k", "drop", "flowid", ":1"]
                )
            except RouterError as exc:
                self._last_error = f"tc police {grant.ip}: {exc}"

    # -- process helpers ---------------------------------------------------
    def _run(self, args: list[str], stdin: str | None = None, check: bool = True) -> str:
        try:
            result = subprocess.run(
                args,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RouterError(f"{' '.join(args[:3])}: {exc}") from exc
        if check and result.returncode != 0:
            raise RouterError(
                f"{' '.join(args[:3])} failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        return result.stdout
