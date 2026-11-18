# BAGSIK PISOWIFI - Installation Guide

This guide turns one Linux box into a coin-operated Wi-Fi vending machine: it
broadcasts the Wi-Fi, hands out addresses, runs the captive portal, counts coin
pulses, and cuts customers off when their time runs out.

Two ways to install it:

| Goal | Section | Time |
| --- | --- | --- |
| Look at the portal and admin console on your own PC (no hardware) | [2. Quick look on a PC](#2-quick-look-on-a-pc-no-hardware) | 5 min |
| Put it on the real appliance (Raspberry Pi / mini PC) | [3. Appliance install](#3-appliance-install) | 30 min |
| Same as 3, but doing every step by hand | [5. Manual install](#5-manual-install-without-the-script) | 45 min |

---

## 1. Before you start

### 1.1 The hardware

| Part | Notes |
| --- | --- |
| SBC or mini PC | Raspberry Pi 3B+/4/5, Orange Pi, Banana Pi, Intel N100 box, an old laptop |
| Two network interfaces | on-board Wi-Fi + Ethernet is ideal; a USB Wi-Fi dongle also works |
| Coin acceptor | CH-926 or similar (12 V, pulse output) |
| 12 V PSU | powers the acceptor, 1-2 A is enough |
| Optocoupler + 1 kOhm resistor | PC817 protects the 3.3 V GPIO pin |
| Storage | 8 GB SD card is enough; use an SSD on x86 |

The box needs **Python 3.10 or newer** (3.11/3.12 recommended) and an internet
connection *while installing*. After that it runs fully offline.

### 1.2 The network

```
   phone / laptop  --Wi-Fi-->  [ this box ]  --Ethernet-->  ISP modem
                               wlan0 10.0.0.1              (DHCP or PPPoE)
                               hostapd, dnsmasq, nftables, tc, uvicorn :80
```

- The customer side is a private subnet, `10.0.0.1/24` by default, served by the
  box. Customers get addresses from `10.0.0.10` to `10.0.0.250`.
- The uplink is whatever interface has the default route. If your ISP needs a
  PPPoE login, configure that on the uplink interface with your distro's tools
  first - BAGSIK only routes and NATs the traffic, it does not dial.

### 1.3 Note these down before installing

| Item | How to find it | Default |
| --- | --- | --- |
| Wi-Fi interface (customer side) | `ip -br link` | `wlan0` |
| Uplink interface | `ip route show default` | `eth0` |
| Box address on the customer side | your choice | `10.0.0.1` |
| Wi-Fi name / password | your choice | `BAGSIK PISOWIFI` / `bagsikwifi` |
| Coin rates | admin console, later | P1=5 min, P5=30, P10=75, P20=180 |

Keep SSH on the **uplink** interface while installing. If you run the installer
over the Wi-Fi interface you are about to reconfigure, you will lose the shell.

---

## 2. Quick look on a PC (no hardware)

Runs on Windows, macOS and Linux with simulated router and coin slot. Nothing
is installed system-wide.

### Windows (PowerShell)

```powershell
cd C:\path\to\BAGSIK-PISOWIFI
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.venv\Scripts\python.exe run.py
```

### Linux / macOS

```bash
cd /path/to/BAGSIK-PISOWIFI
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python run.py
```

Open:

| Page | Address | Login |
| --- | --- | --- |
| Captive portal | <http://localhost:8080/> | none |
| Coin kiosk | <http://localhost:8080/kiosk> | none |
| Admin console | <http://localhost:8080/admin> | `admin` / `bagsik123` |

### Try the coin flow without a coin slot

`.env.example` already sets `BAGSIK_DEV_MODE=true` and
`BAGSIK_COIN_DRIVER=mock`, so the kiosk page shows a simulator.

1. Open `/kiosk` and press **Insert Coin** (this arms the slot for this device).
2. Press **P5** in the *Coin simulator (dev)* card - it counts 5 pulses.
3. Wait about a second for the line to go quiet: the burst is priced, the
   session starts, and the portal shows a running countdown.
4. Open `/` in another tab while the slot is still armed: it shows the live
   state ("Counting coins"). A second browser profile acts as a second device
   and is told to wait.
5. Press **Cancel** or just wait 120 s; the slot closes by itself.

The database is created on first start at `./data/bagsik.db`. Delete that file
(or the whole `data` folder) to start from a clean box.

This mode simulates traffic at `BAGSIK_MOCK_KBPS` per session, so data caps,
countdowns and the enforcement loop behave like production - but **nothing is
actually restricted on your PC**, because the mock driver never touches the
kernel.

---

## 3. Appliance install

### 3.1 Prepare the OS

1. Write Raspberry Pi OS Lite (Bookworm), Debian 12, Ubuntu Server 22.04+ or
   Armbian to the card/SSD. In Raspberry Pi Imager's advanced options set the
   hostname, user, SSH key and Wi-Fi country.
2. Boot, log in over SSH, and update:

   ```bash
   sudo apt update && sudo apt -y full-upgrade
   sudo reboot
   ```

3. Check what the kernel sees:

   ```bash
   ip -br link
   ip -4 addr
   ip route show default
   ```

4. If NetworkManager is installed (Raspberry Pi OS Bookworm, Ubuntu), release
   the Wi-Fi radio so it does not fight `hostapd`:

   ```bash
   sudo nmcli device set wlan0 managed no
   sudo rfkill unblock wifi
   ```

### 3.2 Put the code on the box

Either clone it:

```bash
git clone <your-repo-url> ~/bagsik-src
```

or copy the folder from your PC (PowerShell, from the project folder):

```powershell
scp -r app deploy tests requirements.txt requirements-dev.txt pytest.ini run.py `
    README.md INSTALL.md .env.example pi@bagsik.local:~/bagsik-src
```

Do not copy `.venv` or `data` - the installer builds its own environment.

### 3.3 Run the installer

```bash
cd ~/bagsik-src
sudo LAN_IFACE=wlan0 WAN_IFACE=eth0 ./deploy/install.sh
```

Both interface variables are optional. Without them the installer detects them:
the default-route NIC becomes the uplink and the other NIC with an IPv4 address
(Wi-Fi preferred) becomes the customer side.

| Variable | Default | Meaning |
| --- | --- | --- |
| `LAN_IFACE` / `WAN_IFACE` | auto | customer / uplink interface |
| `LAN_IP` | `10.0.0.1` | box address on the customer side (always `/24`) |
| `WLAN_PASSWORD` | `bagsikwifi` | WPA2 password of the hotspot |
| `APP_DIR` | `/opt/bagsik` | where the application is installed |
| `ETC_DIR` | `/etc/bagsik` | where the config file is installed |
| `DATA_DIR` | `/var/lib/bagsik` | where the SQLite database lives |
| `SVC_USER` | `bagsik` | service account that runs the app |

The installer is idempotent - running it again upgrades in place and **keeps**
an existing `/etc/bagsik/bagsik.env`.

### 3.4 What the installer changes

| Path | Contents |
| --- | --- |
| `/opt/bagsik` | the application + its own `.venv` |
| `/etc/bagsik/bagsik.env` | all `BAGSIK_*` settings, mode 0640 (root can read, service can read) |
| `/var/lib/bagsik/bagsik.db` | the database (created on first start) |
| `/etc/hostapd/hostapd.conf` | the access point, SSID `BAGSIK PISOWIFI`, WPA2 |
| `/etc/dnsmasq.d/bagsik.conf` | DHCP range, DNS, DHCP option 114 (portal advertisement) |
| `/etc/nftables.d/bagsik-nat.nft` | masquerade + forced DNS, table `inet bagsik_nat` |
| `/etc/sysctl.d/90-bagsik.conf` | `net.ipv4.ip_forward=1` |
| `/etc/systemd/system/bagsik.service` | runs uvicorn on port 80 as `bagsik` with only `CAP_NET_ADMIN` + `CAP_NET_BIND_SERVICE` |

The generated env file already contains a random `BAGSIK_SECRET_KEY` and a
random admin password, and selects `ROUTER_DRIVER=local`, `COIN_DRIVER=gpio`,
`DEV_MODE=false`.

### 3.5 Read the admin credentials

```bash
sudo grep -E 'ADMIN_(USER|PASSWORD)' /etc/bagsik/bagsik.env
```

Change the password from **Settings -> Change password** on your first visit.
Editing the env file later has no effect on an existing account - it is only
used to seed the first admin.

### 3.6 Verify

```bash
systemctl status bagsik hostapd dnsmasq --no-pager
journalctl -u bagsik -n 30 --no-pager
nft list tables                 # expect: table inet bagsik and table inet bagsik_nat
ip -4 addr show wlan0           # expect: 10.0.0.1/24
curl -sI http://10.0.0.1/ | head -n 1
```

| Check | Expected |
| --- | --- |
| `systemctl is-active bagsik hostapd dnsmasq` | three times `active` |
| `journalctl -u bagsik` | `router driver ready: local` and `coin acceptor: {...}` |
| `nft list table inet bagsik` | a `forward` chain with `policy drop` |
| `nft list table inet bagsik_nat` | `masquerade` on `wlan0` -> `eth0` |
| joining the SSID | a phone gets `10.0.0.x` and the portal opens |

If `hostapd` is not active, see [8. Troubleshooting](#8-troubleshooting).

### 3.7 First-run checklist in the admin console

1. **Settings** - business name, portal text, print footer, grace period, then
   change the operator password.
2. **Coin rates** - confirm the four seeded bundles (P1/5 min, P5/30 min,
   P10/75 min, P20/3 h) and their caps and speeds. Every rate is price ->
   minutes; the portal never asks the customer to choose one.
3. **Vouchers** - generate a small batch, print the sheet, redeem one code on a
   phone to prove the portal grants time.
4. **Coins & cash** - the acceptor health line should read `gpio`, `ready: true`
   and the pin number.
5. Wire the coin acceptor (next section).

---

## 4. Wire and test the coin acceptor

### 4.1 Wiring (Raspberry Pi example)

| Acceptor | Connects to |
| --- | --- |
| `+12V` | 12 V PSU `+` |
| `GND` | 12 V PSU `-` **and** Pi GND (common ground) |
| `COIN` (pulse) | PC817 pin 1 through a 1 kOhm resistor |
| PC817 pin 2 | 12 V PSU `-` |
| PC817 pin 4 | Pi GPIO17 (BCM 17 = physical pin 11) |
| PC817 pin 3 | Pi GND |

The optocoupler keeps the 12 V acceptor isolated from the 3.3 V pin, which uses
its internal pull-up: every pulse pulls it low.

### 4.2 Programming the acceptor

Program it so **one pulse = one peso** (many units do this from the factory or
through their SET routine). Then a P1 coin emits 1 pulse, a P5 coin 5 pulses,
and `BAGSIK_COIN_PULSE_CENTAVOS=100` turns each pulse into P1.00.

If your acceptor cannot be reprogrammed, raise `BAGSIK_COIN_PULSE_CENTAVOS` to
the value of one pulse instead (for example `500` if it emits a single pulse for
a P5 coin).

### 4.3 Relevant settings

| Key | Default | Meaning |
| --- | --- | --- |
| `BAGSIK_COIN_DRIVER` | `gpio` (appliance) | `gpio` for real pulses, `mock` for the simulator |
| `BAGSIK_GPIO_PIN` | `17` | BCM pin the pulse line is wired to |
| `BAGSIK_COIN_PULSE_CENTAVOS` | `100` | value of one pulse, in centavos |
| `BAGSIK_COIN_IDLE_SECONDS` | `1.2` | quiet time that ends a coin burst |
| `BAGSIK_COIN_ARM_SECONDS` | `120` | how long an **Insert Coin** tap keeps the slot open |

Edit `/etc/bagsik/bagsik.env`, then `sudo systemctl restart bagsik`.

### 4.4 Test it

```bash
sudo journalctl -u bagsik -f
```

1. Join the Wi-Fi, open the portal, press **Insert Coin** - the page switches to
   "Listening for coins..." with a countdown.
2. Drop a P1 coin. The log shows `coin accepted: 1 pulse(s) = 100 centavos` and
   the portal turns into a running session after the burst goes quiet.
3. The **Coins & cash** page shows the credit and the ledger entry.
4. Drop P1 + P5 without leaving the window: the pulses are batched and priced
   together with the largest bundle first.

If nothing arrives:

```bash
ls -l /dev/gpiochip*                                    # the pin hardware exists
/opt/bagsik/.venv/bin/python -c "import gpiozero; print(gpiozero.__version__)"
sudo grep COIN /etc/bagsik/bagsik.env                   # driver=gpio, correct pin
```

`gpiozero unavailable` in the log means the venv has no GPIO library - reinstall
it into the venv with `sudo /opt/bagsik/.venv/bin/pip install gpiozero lgpio`
(on Raspberry Pi OS Bookworm `lgpio` is the backend) and restart the service.

---

## 5. Manual install (without the script)

Use this if you do not want the script touching `hostapd`, `dnsmasq` or
`nftables`, or if you already have them configured.

```bash
# 1. packages
sudo apt update
sudo apt install -y python3 python3-venv python3-pip \
     hostapd dnsmasq nftables iproute2 iw rfkill ca-certificates sqlite3

# 2. service account, code, virtualenv
sudo useradd --system --home /opt/bagsik --shell /usr/sbin/nologin bagsik
sudo install -d -m 0755 /opt/bagsik
sudo rsync -a --exclude .venv --exclude data --exclude .git --exclude .env ./ /opt/bagsik/
sudo python3 -m venv /opt/bagsik/.venv
sudo /opt/bagsik/.venv/bin/pip install -r /opt/bagsik/requirements.txt
sudo /opt/bagsik/.venv/bin/pip install gpiozero lgpio        # coin slot only
sudo chown -R bagsik:bagsik /opt/bagsik

# 3. config
sudo install -d -m 0750 /etc/bagsik
sudo install -d -m 0750 -o bagsik -g bagsik /var/lib/bagsik
sudo cp /opt/bagsik/.env.example /etc/bagsik/bagsik.env
sudo chmod 0640 /etc/bagsik/bagsik.env
sudo chown root:bagsik /etc/bagsik/bagsik.env
sudo nano /etc/bagsik/bagsik.env
```

Set at least these values in `/etc/bagsik/bagsik.env`:

```ini
BAGSIK_SECRET_KEY=<python -c "import secrets;print(secrets.token_urlsafe(48))">
BAGSIK_DB_PATH=/var/lib/bagsik/bagsik.db
BAGSIK_PORTAL_URL=http://10.0.0.1
BAGSIK_ROUTER_DRIVER=local
BAGSIK_LAN_IFACE=wlan0
BAGSIK_WAN_IFACE=eth0
BAGSIK_LAN_CIDR=10.0.0.1/24
BAGSIK_COIN_DRIVER=gpio
BAGSIK_DEV_MODE=false
```

Then the access point, DHCP and NAT (replace the interfaces and subnet):

```bash
sudo sed -e "s/__LAN__/wlan0/g" -e "s/__WLAN_PASSWORD__/your-wifi-password/g" \
  /opt/bagsik/deploy/hostapd.conf.example | sudo tee /etc/hostapd/hostapd.conf
sudo chmod 0600 /etc/hostapd/hostapd.conf
sudo sed -i 's|^#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd

sudo sed -e "s/__LAN_IP__/10.0.0.1/g" -e "s/__LAN_START__/10.0.0.10/g" \
  -e "s/__LAN_END__/10.0.0.250/g" -e "s/__LAN__/wlan0/g" \
  /opt/bagsik/deploy/dnsmasq.conf.example | sudo tee /etc/dnsmasq.d/bagsik.conf
sudo sed -i 's/^#\?bind-interfaces/bind-interfaces/' /etc/dnsmasq.conf

sudo install -d /etc/nftables.d
sudo sed -e "s/__LAN__/wlan0/g" -e "s/__WAN__/eth0/g" -e "s/__LAN_CIDR__/10.0.0.1\/24/g" \
  /opt/bagsik/deploy/nftables-bagsik-nat.nft | sudo tee /etc/nftables.d/bagsik-nat.nft
grep -q 'bagsik-nat.nft' /etc/nftables.conf || \
  echo 'include "/etc/nftables.d/bagsik-nat.nft"' | sudo tee -a /etc/nftables.conf
```

Address, forwarding and the service:

```bash
sudo ip addr add 10.0.0.1/24 dev wlan0          # make it permanent with nmcli,
                                                # /etc/network/interfaces or
                                                # systemd-networkd
echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/90-bagsik.conf
sudo sysctl -q --system

sudo install -m 0644 /opt/bagsik/deploy/bagsik.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl unmask hostapd
sudo systemctl enable --now nftables hostapd dnsmasq bagsik
```

Continue with [3.6 Verify](#36-verify).

---

## 6. Configuration reference

Everything lives in `.env` (PC) or `/etc/bagsik/bagsik.env` (appliance) and is
prefixed with `BAGSIK_`. Restart the service after editing.

| Key | Default | Meaning |
| --- | --- | --- |
| `SECRET_KEY` | dev value | HMAC key for admin cookies - **change it** |
| `DB_PATH` | `./data/bagsik.db` | SQLite file; appliance default `/var/lib/bagsik/bagsik.db` |
| `TIMEZONE` | `Asia/Manila` | local day boundaries in reports |
| `PORTAL_URL` | `http://10.0.0.1` | address captive probes are redirected to |
| `ADMIN_USER` / `ADMIN_PASSWORD` | `admin` / `bagsik123` | used only to seed the first admin |
| `SESSION_HOURS` | `12` | admin cookie lifetime |
| `ROUTER_DRIVER` | `mock` | `local` (nftables + tc on this box) or `mock` |
| `LAN_IFACE` / `WAN_IFACE` / `LAN_CIDR` | `auto` | interface override; `auto` = detect |
| `ENFORCE_SECONDS` | `10` | how often sessions are checked and re-pushed (min 2) |
| `COIN_DRIVER` | `mock` | `gpio` or `mock` |
| `GPIO_PIN` | `17` | BCM pin the acceptor pulses |
| `COIN_PULSE_CENTAVOS` | `100` | centavos per pulse (min 1) |
| `COIN_IDLE_SECONDS` | `1.2` | quiet time that ends a burst (min 0.2) |
| `COIN_ARM_SECONDS` | `120` | lifetime of an Insert Coin window (min 15) |
| `PORT` | `8080` | only used by `run.py`; the systemd unit binds port 80 |
| `MOCK_KBPS` | `240` | simulated per-session rate for the mock driver |
| `DEV_MODE` | `false` | shows the kiosk coin simulator (needs the mock coin driver) |

Business settings - business name, portal text, print footer, grace period,
kiosk toggles - live in the database and are edited in **Settings**. No restart.

Keep `DB_PATH` inside `/var/lib/bagsik`: the systemd unit sandboxes the process
with `ProtectSystem=full`, so other locations are read-only to the service.

---

## 7. Day-2 operations

```bash
sudo systemctl restart bagsik            # apply env changes
sudo systemctl status bagsik --no-pager
sudo journalctl -u bagsik -f             # live log
sudo journalctl -u bagsik --since '1 hour ago' --no-pager
```

**Update to a new version**

```bash
cd ~/bagsik-src
git pull
sudo ./deploy/install.sh                 # keeps /etc/bagsik/bagsik.env
```

The installer rebuilds the venv, reinstalls the unit and restarts the service.
The database is never touched.

**Backup** (safe while running)

```bash
sudo sqlite3 /var/lib/bagsik/bagsik.db ".backup '/root/bagsik-$(date +%F).db'"
```

Or simply `sudo systemctl stop bagsik && sudo cp /var/lib/bagsik/bagsik.db /root/`.

**Restore** - stop the service, copy the file back to
`/var/lib/bagsik/bagsik.db`, `sudo chown bagsik:bagsik` it, start the service.

**Locked out of the admin console** - the first admin is seeded only when there
is none, so:

```bash
sudo systemctl stop bagsik
sudo sqlite3 /var/lib/bagsik/bagsik.db "DELETE FROM admin_users;"
sudo systemctl start bagsik              # re-seeds from BAGSIK_ADMIN_USER/PASSWORD
```

Change the password again from **Settings** afterwards.

**Uninstall** (stop first, back up the database):

```bash
sudo systemctl disable --now bagsik hostapd dnsmasq
sudo rm -f /etc/systemd/system/bagsik.service /etc/hostapd/hostapd.conf \
           /etc/dnsmasq.d/bagsik.conf /etc/nftables.d/bagsik-nat.nft \
           /etc/sysctl.d/90-bagsik.conf
sudo rm -rf /opt/bagsik /etc/bagsik /var/lib/bagsik
sudo userdel bagsik
sudo systemctl daemon-reload
```

---

## 8. Troubleshooting

| Symptom | Check | Fix |
| --- | --- | --- |
| `bagsik` will not start | `journalctl -u bagsik -n 50` | most often `DB_PATH` outside `/var/lib/bagsik`, or port 80 taken (`ss -lntp \| grep :80`) |
| Portal does not pop up | `curl -s http://10.0.0.1/generate_204` | DHCP option 114 unsupported by the client: open `http://10.0.0.1/` manually; confirm dnsmasq loads `bagsik.conf` |
| `hostapd` keeps restarting | `journalctl -u hostapd -n 30` | Wi-Fi held by NetworkManager (`nmcli device set wlan0 managed no`), or the adapter does not support AP mode |
| `dnsmasq` fails on port 53 | `journalctl -u dnsmasq -n 30` | `systemd-resolved` owns port 53: keep `bind-interfaces`, or `sudo systemctl disable --now systemd-resolved` |
| Everyone can browse without paying | `nft list table inet bagsik` | the `forward` chain must exist with `policy drop`; check the driver line in the log and in **Settings** |
| Paid customers have no internet | `sysctl net.ipv4.ip_forward`, `nft list table inet bagsik_nat` | forwarding off, masquerade on the wrong uplink, or `WAN_IFACE` is not the default route |
| Speeds are not limited | `tc qdisc show dev wlan0` | no `htb` root: re-save a rate in **Coin rates** to force a sync |
| Coins are not counted | `journalctl -u bagsik -f`, `sudo grep COIN /etc/bagsik/bagsik.env` | wrong pin, missing `gpiozero` in the venv, `COIN_DRIVER=mock`, or the slot was never armed from the portal |
| A P5 coin credits P1 | the `P5` coin is priced as 5 pulses | the acceptor emits 1 pulse per coin: reprogram it or raise `COIN_PULSE_CENTAVOS` |
| A second phone says "Another customer..." and stays there | this is by design | the slot takes one transaction at a time; it frees itself after `COIN_ARM_SECONDS` or when the current burst settles |
| Sessions never expire | `journalctl -u bagsik \| grep enforce` | `ENFORCE_SECONDS` must be >= 2, and the `local` driver must be ready |
| MAC shows as `unknown` | `ip neigh show <client ip>` | the client is behind another AP or bridge; the box resolves MACs from its own neighbour table |
| Vouchers print but codes are rejected | **Vouchers** page | the code was voided, the batch was voided, or it was redeemed already |

---

## 9. Security notes

- Keep port 80 of this box on the customer LAN only. It is plain HTTP by design
  and must never be reachable from the internet.
- Change `SECRET_KEY` (the installer generates one) and the admin password.
- Leave `DEV_MODE=false` in production: it exposes the kiosk coin simulator,
  which lets anyone arm the slot and inject simulated coins.
- There is one admin role. Every action is written to the audit log, visible on
  the dashboard.

Further reading: [README.md](README.md) covers the operating side - coin flow,
sessions, vouchers, cash reconciliation, backups and the project layout.
