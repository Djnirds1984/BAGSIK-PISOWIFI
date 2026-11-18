# BAGSIK PISOWIFI

A complete, self-hosted **PisoWiFi** appliance: coin-operated Wi-Fi vending with
vouchers, a captive portal, a kiosk screen and an operator dashboard.

The box **is** the system. There is no MikroTik, no vendor cloud and no external
router: one Linux SBC or mini PC serves the Wi-Fi, hands out DHCP, routes the
traffic, throttles each customer and cuts them off when their time or data runs
out. Everything runs offline in a single Python process with a SQLite file.

```
        customer phone / laptop
                  │  Wi-Fi (hostapd)
                  ▼
   ┌──────────────────────────────────────────┐
   │  SBC / mini PC  (Raspberry Pi, Orange Pi,│
   │  Banana Pi, N100 box, old laptop …)      │
   │                                          │
   │  dnsmasq   DHCP + DNS + captive advert   │
   │  nftables  default-drop forward chain    │
   │  tc        per-client HTB + ingress      │
   │  bagsik    uvicorn :80  (this app)       │
   │    /        portal (redeem, status)      │
   │    /kiosk   coin kiosk                   │
   │    /admin   operator console             │
   │    SQLite   /var/lib/bagsik/bagsik.db    │
   └──────────────────────────────────────────┘
                  │  uplink NIC
                  ▼
                 ISP
```

## What you get

- **Catch-and-release portal** – Android, iOS and Windows captive-portal probes
  are answered, plus DHCP option 114 so the portal opens by itself.
- **Coins to credit** – GPIO pulse counting for CH-926 style acceptors, an
  auditable coin ledger (oldest coin spent first) and a dev simulator.
- **Vouchers** – batches of 8-character codes (`XXXX-XXXX`, no `0/O/1/I/L`),
  printable ticket sheets, void by code or whole batch.
- **Sessions** – time cap, data cap, download/upload shaping, pause/resume,
  operator top-up, live byte counters read from nftables.
- **Enforcement** – a background loop that retires finished sessions and
  re-pushes the allow-list to the kernel, with a configurable grace period.
- **Operator console** – live dashboard, hourly/7-day revenue, plan mix, voucher
  and session history, cash reconciliation, branded settings, password change.
- **Offline front end** – server-rendered Jinja templates, hand-written CSS and
  a few KB of vanilla JS. No CDN, no Node build step, no internet needed.

## Hardware

| Part | Notes |
| --- | --- |
| SBC or mini PC | Raspberry Pi 3B+/4/5, Orange Pi, Banana Pi, Intel N100 box, any x86 with a Wi-Fi radio |
| Two network interfaces | the on-board Wi-Fi plus Ethernet is ideal; a cheap USB Wi-Fi dongle also works |
| Coin acceptor | CH-926 or similar multi-coin acceptor (12 V, pulse output) |
| 12 V PSU | for the acceptor (1–2 A is plenty) |
| Optocoupler + resistor | PC817 + 1 kΩ to protect the GPIO pin (see wiring) |
| Storage | 8 GB SD card is enough; use an SSD on x86 |

### Coin acceptor wiring (Raspberry Pi example)

| Acceptor | Connects to |
| --- | --- |
| `+12V` | 12 V PSU `+` |
| `GND` | 12 V PSU `-` **and** Pi GND (common ground) |
| `COIN` (pulse) | PC817 pin 1 through a 1 kΩ resistor |
| PC817 pin 2 | 12 V PSU `-` |
| PC817 pin 4 | Pi GPIO2 (BCM 2, physical pin 3) |
| PC817 pin 3 | Pi GND |

The optocoupler keeps the 12 V acceptor isolated from the 3.3 V GPIO. The pin
uses the internal pull-up, so each pulse pulls it low - exactly what
`gpiozero.Button` expects.

The pulse pin is chosen in the admin console (**Settings -> Coin hardware**),
by board and by the pin number printed on the 40-pin header. The default is
physical pin 3: GPIO2 on a Raspberry Pi 3B+, PA12 on an Orange Pi One/PC. A
mini PC has no header, so it selects a NodeMCU 8266 bridge instead (that
firmware is not written yet, so the slot stays closed).

Program the acceptor so **one pulse = one peso** (most acceptors do this from
the factory or via their SET routine). Then a ₱1 coin emits 1 pulse, a ₱5 coin
emits 5 pulses, and `BAGSIK_COIN_PULSE_CENTAVOS=100` turns each pulse into ₱1.00.
Pulses are batched: the line must stay quiet for `BAGSIK_COIN_IDLE_SECONDS`
before the burst is converted into time. The slot stays shut until a customer
taps **Insert Coin** on the portal, and only one device may hold it at a time.

## Quick start (no hardware needed)

Step-by-step instructions - appliance install, coin-acceptor wiring, config
reference and troubleshooting - are in **[INSTALL.md](INSTALL.md)**.

Works on Windows, macOS and Linux with the mock router and mock coin slot:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.venv\Scripts\python.exe run.py
```

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python run.py
```

Then open:

- portal – <http://localhost:8080/>
- kiosk – <http://localhost:8080/kiosk> (dev mode shows a coin simulator)
- admin – <http://localhost:8080/admin> (`admin` / `bagsik123` by default)

The mock router simulates traffic at `BAGSIK_MOCK_KBPS` per session, so data
caps, countdowns and the enforcement loop behave exactly like production.

## Install on the appliance

```bash
git clone <your-repo> ~/bagsik-src      # or copy the folder over
cd ~/bagsik-src
sudo LAN_IFACE=wlan0 WAN_IFACE=eth0 ./deploy/install.sh
```

Both interface variables are optional: the installer (and the app) detect
them - the default-route NIC becomes the uplink, the remaining NIC with an IPv4
address (Wi-Fi preferred) becomes the customer side.

`deploy/install.sh` is idempotent and does the following:

1. installs `hostapd`, `dnsmasq`, `nftables`, `iproute2`, Python and `iw`;
2. copies the app to `/opt/bagsik` and builds a virtualenv there;
3. generates `/etc/bagsik/bagsik.env` (secret key, random admin password,
   `local` router driver, `gpio` coin driver) if it does not exist yet;
4. assigns `10.0.0.1/24` to the LAN interface and enables IP forwarding;
5. writes `hostapd.conf`, `dnsmasq.conf` and the NAT table from
   `deploy/*.example`;
6. installs and starts `bagsik.service` (uvicorn on port 80 with
   `CAP_NET_ADMIN` + `CAP_NET_BIND_SERVICE`, no root shell);
7. prints the SSID, Wi-Fi password and admin credentials.

Verify with:

```bash
systemctl status bagsik hostapd dnsmasq
journalctl -u bagsik -f
nft list table inet bagsik          # the enforcement table
```

### Manual install (if you prefer)

1. `apt install python3-venv hostapd dnsmasq nftables iproute2 iw`
2. copy the repo to `/opt/bagsik`, create a venv, `pip install -r requirements.txt`
3. `cp .env.example /etc/bagsik/bagsik.env`, set `BAGSIK_ROUTER_DRIVER=local`
   and `BAGSIK_DB_PATH=/var/lib/bagsik/bagsik.db`
4. `cp deploy/bagsik.service /etc/systemd/system/`, enable and start it
5. copy the three files in `deploy/` to `/etc/hostapd/hostapd.conf`,
   `/etc/dnsmasq.d/bagsik.conf` and `/etc/nftables.d/bagsik-nat.nft`,
   replacing the `__PLACEHOLDERS__` with your interfaces and subnet.

> The `local` driver needs `nft`, `tc` and `ip` on the PATH and
> `CAP_NET_ADMIN`. It rebuilds its own `inet bagsik` table; your NAT table
> (`inet bagsik_nat`) is separate and untouched.

## First-run checklist

1. Join the `BAGSIK PISOWIFI` SSID and open <http://10.0.0.1/>.
2. Sign in at `/admin` and **change the operator password**.
3. Set the business name, portal text and print footer in *Settings*.
4. In *Settings -> Hotspot server*, confirm `eth0` is the WAN uplink and every
   other wired port is a hotspot server (eth1, eth2, ...).
5. Review the four seeded coin bundles (₱1/5 min, ₱5/30 min, ₱10/75 min,
   ₱20/3 h) and adjust the price-to-minutes rates, caps and speeds.
6. Generate a voucher batch, print the tickets, redeem one from a phone.
7. Wire the acceptor, confirm `BAGSIK_COIN_DRIVER=gpio`, then
   `systemctl restart bagsik` and insert a coin.
8. Tap *Insert Coin* on a phone, drop a coin and watch the session start on the
   dashboard once the acceptor goes quiet.

## Operating guide

**How coins are sold**

- The customer taps **Insert Coin** on the captive portal. That binds the coin
  slot to their MAC/IP for `COIN_ARM_SECONDS` and arms the GPIO listener.
- Coins drop, the pulses are counted, and when the line stays quiet the burst is
  priced with the admin's per-pulse rates (largest bundle first) and the time is
  granted or added to that device automatically - there is no plan to pick.
- The slot takes **one transaction at a time**. A second device that taps while
  someone else is inserting coins is told to wait; their tap never arms the
  slot.
- The lock is released the moment the burst settles, when the customer taps
  *Cancel*, or when the arm window expires with no coin. A stuck settle is
  force-released, and a process restart drops the window entirely.

**Two ways to sell**

- *Counter*: print a batch from *Vouchers*, hand out tickets, the customer
  redeems the code at the portal.
- *Leftover credit*: coins below the cheapest bundle stay in the wallet and can
  still be turned into a code on the kiosk page.

**Sessions** (*Sessions* page)

- *+min* extends a live session by any number of minutes.
- *Pause* frees the router slot and freezes the clock; *Resume* hands the
  frozen time back.
- *Stop* disconnects immediately and marks the voucher used.

**Cash** (*Coins & cash* page)

- The wallet is the sum of pending coin entries. It belongs to the box, not to
  a phone, so it survives the customer walking away.
- At end of shift, *Void pending credit* closes the drawer without touching
  finished sessions. The ledger keeps both cash in and credit used today.

**Backups**

The whole business is one file: `cp /var/lib/bagsik/bagsik.db backup.db`
(add `-wal`/`-shm` or use `sqlite3 .backup` while the service is running).
Settings, plans and vouchers restore by copying the file back.

## Configuration

All keys live in `.env` (development) or `/etc/bagsik/bagsik.env` (appliance)
and are prefixed with `BAGSIK_`.

| Key | Default | Meaning |
| --- | --- | --- |
| `SECRET_KEY` | dev value | HMAC key for admin cookies - **change it** |
| `DB_PATH` | `./data/bagsik.db` | SQLite file (appliance: `/var/lib/bagsik/bagsik.db`) |
| `TIMEZONE` | `Asia/Manila` | local day boundaries for reports |
| `PORTAL_URL` | `http://10.0.0.1` | address captive probes are redirected to |
| `ADMIN_USER` / `ADMIN_PASSWORD` | `admin` / `bagsik123` | seeded only when the database has no admin yet |
| `SESSION_HOURS` | `12` | admin cookie lifetime |
| `ROUTER_DRIVER` | `mock` | `local` (nftables + tc) or `mock` |
| `LAN_IFACE` / `WAN_IFACE` / `LAN_CIDR` | `auto` | interface override; `auto` = detect (the *Hotspot server* page wins) |
| `ENFORCE_SECONDS` | `10` | how often sessions are checked and re-pushed |
| `COIN_DRIVER` | `mock` | `gpio` or `mock` |
| `GPIO_PIN` | `17` | fallback BCM/line number; the *Coin hardware* page sets the pin |
| `COIN_PULSE_CENTAVOS` | `100` | value of one pulse (₱1.00) |
| `COIN_IDLE_SECONDS` | `1.2` | quiet time that ends a coin burst |
| `COIN_ARM_SECONDS` | `120` | how long an Insert Coin tap keeps the slot open |
| `MOCK_KBPS` | `240` | simulated per-session download rate (mock driver) |
| `DEV_MODE` | `false` | exposes the kiosk coin simulator; keep off in production |

Business settings (name, portal text, grace period, kiosk toggles) live in the
database and are editable from *Settings* - no restart needed. Interface roles do
too: *Settings -> Hotspot server* stores the WAN uplink and every customer port
(`eth0` uplink, `eth1`/`eth2`... hotspot servers) and applies them without a
restart.

## Tests

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest
```

73 tests cover the portal flow, CSRF, voucher lifecycle, coin ledger splitting,
the coin flow (arming, exclusive lock, settlement), the board/pin map behind the
*Coin hardware* page, the WAN/hotspot port assignment behind the *Hotspot server*
page, the kiosk checkout, session expiry/data caps/pause/extend, the nftables
script builder and every admin page. They run on the mock drivers, so no
hardware, root or Linux is required.

## Project layout

```
app/
  main.py            FastAPI app, lifespan, background loops
  config.py          dotenv + frozen Settings
  db.py              SQLite access, schema bootstrap, seeding, audit log
  schema.sql         tables: plans, batches, vouchers, sessions, coin_credits …
  security.py        PBKDF2 passwords, signed cookies, CSRF tokens
  deps.py            templates, filters, auth, CSRF, device identity
  tasks.py           coin intake loop + enforcement loop
  services/
    catalog.py       settings, plans, audit feed
    vouchers.py      code generation, batches, voiding
    sessions.py      lifecycle, grants, enforcement
    billing.py       coin ledger (FIFO with row splitting)
    kiosk.py         coin → session / voucher checkout
    stats.py         dashboard numbers
  netctl/
    base.py          driver contract (Grant / Usage)
    interfaces.py    wired NIC discovery + role defaults (admin hotspot page)
    local.py         nftables + tc on this box (the real one)
    mock.py          in-memory simulation (dev + tests)
  hardware/
    coin.py          GPIO pulse counter, mock acceptor, NodeMCU stub
    pins.py          board profiles + physical pin map (admin hardware page)
  routers/           portal, kiosk, admin, captive probes
  templates/         portal + admin pages (Jinja)
  static/            app.css, admin.js, portal.js
deploy/              install.sh, systemd unit, hostapd/dnsmasq/nftables samples
tests/               pytest suite
run.py               development entrypoint (port 8080)
```

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Portal does not pop up | DHCP option 114 unsupported → open `http://10.0.0.1/`; confirm dnsmasq is serving the `bagsik.conf` |
| Device MAC shows as `unknown` | the client is on another AP/bridge; MAC resolution reads `ip neigh` on this box |
| Everyone can browse without paying | `nft list table inet bagsik` - the forward chain must exist with `policy drop`; check the driver state in *Settings* |
| Paid customers have no internet | `sysctl net.ipv4.ip_forward`, the masquerade rule (`nft list table inet bagsik_nat`), and that `WAN_IFACE` is the uplink |
| Speeds not limited | `tc qdisc show dev <lan>` needs the `htb` root; re-save a plan to trigger a sync |
| Coins are not counted | `journalctl -u bagsik`, GPIO wiring, and `BAGSIK_COIN_DRIVER=gpio`; the *Coins* page shows acceptor health |
| A ₱5 coin credits ₱1 | the acceptor emits 1 pulse for every coin - reprogram it, or raise `COIN_PULSE_CENTAVOS` |
| Sessions never expire | the enforcement loop logs every pass; `ENFORCE_SECONDS` must be > 0 |
| dnsmasq will not start on Ubuntu | systemd-resolved owns port 53; keep `bind-interfaces` and the explicit `interface=` line, or disable the stub listener |

## Design notes and limits

- **Hardware-agnostic by construction.** The app only speaks the `RouterDriver`
  contract (`sync`, `read_usage`, `lookup_mac`, `health`). Swapping kernel
  nftables for anything else means writing one driver, not touching billing.
- **The table is rebuilt on every sync** so a stopped session can never leave a
  stale accept rule behind. Byte counters are read first and re-seeded, which is
  why data caps stay accurate across rebuilds.
- **The wallet is global to the box.** That matches one kiosk unit serving a
  queue of people; it is not a per-customer account.
- **Plain HTTP on the LAN.** The portal is deliberately a closed local network;
  do not expose port 80 of this box to the internet.
- **One admin role.** Enough for a sari-sari store; there is no multi-user
  permission model.
- Not included: SMS/e-wallet payments, multi-branch sync, thermal-printer
  drivers (use the browser's print dialog on the ticket sheet).
