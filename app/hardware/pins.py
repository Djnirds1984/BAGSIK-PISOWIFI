"""Coin-acceptor pin map for the supported boards.

The operator picks a board and a *physical* header pin - the number printed on
the 40-pin connector - because that is the only numbering a Raspberry Pi and an
Orange Pi share. Each family then maps it to the number its driver expects:

    physical 3   Raspberry Pi 3B+   ->  GPIO2   (gpiozero pin 2)
    physical 3   Orange Pi One/PC   ->  PA12    (gpiochip line 12, bank A)

A mini PC has no header at all: its coin pulses arrive from a NodeMCU 8266
bridge over Wi-Fi, so the board carries a host/port instead of a pin. That
bridge firmware does not exist yet, so the slot stays closed until it does.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_PIN = 3

BOARDS: tuple[dict, ...] = (
    {
        "id": "rpi",
        "name": "Raspberry Pi 3B+ / 4 / 5",
        "family": "40-pin header \u00b7 BCM GPIO",
        "gpio": True,
    },
    {
        "id": "orangepi",
        "name": "Orange Pi One / PC",
        "family": "40-pin header \u00b7 Allwinner PA/PB\u2026 pins",
        "gpio": True,
    },
    {
        "id": "x64",
        "name": "Mini PC x86_64 + NodeMCU 8266",
        "family": "no GPIO header \u00b7 pulses over Wi-Fi",
        "gpio": False,
    },
)

BOARD_IDS = {"auto"} | {board["id"] for board in BOARDS}

# physical pin -> (BCM on Raspberry Pi, sunxi bank, bit inside the bank)
# Only pins that are safe to drive: power, ground and the HAT ID EEPROM pins
# (27/28) are omitted on purpose.
HEADER: dict[int, tuple[int, str, int]] = {
    3: (2, "A", 12),    # RPi SDA1  /  OPi TWI0_SDA
    5: (3, "A", 11),    # RPi SCL1  /  OPi TWI0_SCK
    7: (4, "A", 6),
    8: (14, "A", 13),
    10: (15, "A", 14),
    11: (17, "A", 1),
    12: (18, "D", 14),
    13: (27, "A", 0),
    15: (22, "A", 3),
    16: (23, "C", 4),
    18: (24, "C", 7),
    19: (10, "C", 0),
    21: (9, "C", 1),
    22: (25, "A", 2),
    23: (11, "C", 2),
    24: (8, "C", 3),
    26: (7, "A", 21),
    29: (5, "A", 7),
    31: (6, "A", 8),
    32: (12, "G", 8),
    33: (13, "A", 9),
    35: (19, "A", 10),
    36: (16, "G", 9),
    37: (26, "A", 20),
    38: (20, "G", 6),
    40: (21, "G", 7),
}


def as_pin(value) -> int | None:
    """Best-effort integer physical pin (None when it is not a number)."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def board_label(board: str) -> str:
    for entry in BOARDS:
        if entry["id"] == board:
            return entry["name"]
    return "Unknown board"


def _model() -> str:
    for path in (
        "/proc/device-tree/model",
        "/sys/firmware/devicetree/base/model",
    ):
        try:
            return Path(path).read_bytes().replace(b"\x00", b"").decode("utf-8", "ignore").strip()
        except OSError:
            continue
    return ""


def detect_board(model: str | None = None) -> str:
    """Which of the three families this machine looks like."""
    text = (_model() if model is None else model).lower()
    if "raspberry" in text:
        return "rpi"
    if "orangepi" in text or "orange pi" in text or "allwinner" in text or "sunxi" in text:
        return "orangepi"
    return "x64"


def resolve(board: str, physical) -> dict:
    """Turn a board + physical pin into what the driver actually uses."""
    pin = as_pin(physical)
    if board == "x64":
        return {
            "board": "x64",
            "board_label": board_label("x64"),
            "gpio": False,
            "driver": "nodemcu",
            "physical": None,
            "name": "",
            "backend_pin": None,
            "ok": True,
        }
    row = HEADER.get(pin or 0)
    if row is None:
        return {
            "board": board,
            "board_label": board_label(board),
            "gpio": True,
            "driver": "gpio",
            "physical": pin,
            "name": "",
            "backend_pin": None,
            "ok": False,
        }
    bcm, bank, bit = row
    if board == "orangepi":
        name = f"P{bank}{bit}"
        backend = (ord(bank) - ord("A")) * 32 + bit
    else:
        name = f"GPIO{bcm}"
        backend = bcm
    return {
        "board": board,
        "board_label": board_label(board),
        "gpio": True,
        "driver": "gpio",
        "physical": pin,
        "name": name,
        "backend_pin": backend,
        "ok": True,
    }


def current_target(board: str, physical) -> dict:
    """Resolve the stored choice, expanding 'auto' and falling back to pin 3."""
    if board not in BOARD_IDS:
        board = "auto"
    if board == "auto":
        board = detect_board()
    target = resolve(board, physical)
    if not target["ok"]:
        target = resolve(board, DEFAULT_PIN)
    return target


def pin_options(board: str) -> list[dict]:
    return [
        {"value": pin, "label": f"Pin {pin} \u00b7 {resolve(board, pin)['name']}"}
        for pin in sorted(HEADER)
    ]


def physical_for_backend(board: str, backend: int | None) -> int | None:
    """Reverse lookup, used to pre-select the pin the box is already using."""
    if backend is None or board == "x64":
        return None
    for pin in sorted(HEADER):
        if resolve(board, pin)["backend_pin"] == backend:
            return pin
    return None


def board_choices(active: str) -> list[dict]:
    """Board cards for the admin page, with 'auto' first."""
    options = [
        {
            "id": "auto",
            "name": "Auto-detect",
            "family": "picked from the device tree at boot",
            "gpio": True,
            "active": active == "auto",
        }
    ]
    for board in BOARDS:
        options.append({**board, "active": active == board["id"]})
    return options
