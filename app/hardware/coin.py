"""Coin acceptor.

Real acceptors (CH-926 and friends) emit one electrical pulse per programmed
unit. We count pulses on a GPIO pin and treat a quiet line as "coin finished",
which converts a burst of pulses into a peso value.

The slot is closed until a customer arms it from the portal: pulses that arrive
while disarmed are counted as ignored and never credited. That is what the
"Insert Coin" button is for - it opens a short window for one device.

The mock backend keeps the whole product testable without a coin slot: the
kiosk page calls `inject()`, which honours the same arm window.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("bagsik.coin")


@dataclass(frozen=True)
class CoinEvent:
    centavos: int
    pulses: int
    source: str


class CoinAcceptor:
    def __init__(
        self,
        driver: str = "mock",
        pin: int = 17,
        pulse_centavos: int = 100,
        idle_seconds: float = 1.2,
        arm_seconds: float = 120.0,
    ) -> None:
        self.driver = driver if driver in {"mock", "gpio"} else "mock"
        self.pin = pin
        self.pulse_centavos = max(1, pulse_centavos)
        self.idle_seconds = idle_seconds
        self.arm_seconds = max(5.0, float(arm_seconds))
        self.events: "queue.Queue[CoinEvent]" = queue.Queue()
        self.ready = False
        self.last_error = ""
        self.total_events = 0
        self.ignored_pulses = 0
        self._pulses = 0
        self._txn: dict[str, Any] | None = None
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._button = None
        self._stopped = threading.Event()

    # -- public API --------------------------------------------------------
    def start(self) -> None:
        if self.driver == "gpio":
            self._start_gpio()
        else:
            self.ready = True
            log.info("coin acceptor running in mock mode")

    def stop(self) -> None:
        self._stopped.set()
        if self._timer is not None:
            self._timer.cancel()
        if self._button is not None:
            try:
                self._button.close()
            except Exception:  # pragma: no cover - hardware teardown
                pass
            self._button = None
        self.ready = False

    def inject(self, centavos: int, pulses: int = 0, source: str = "mock") -> bool:
        """Simulate a coin drop (mock driver and tests). False if the slot is shut."""
        centavos = int(centavos)
        if centavos <= 0:
            return False
        pulses = int(pulses or max(1, centavos // self.pulse_centavos))
        with self._lock:
            txn = self._open_txn()
            if txn is None:
                self.ignored_pulses += 1
                return False
            txn["pulses"] += pulses
            txn["centavos"] += centavos
            txn["last_pulse"] = time.monotonic()
        self.total_events += 1
        self.events.put(CoinEvent(centavos=centavos, pulses=pulses, source=source))
        return True

    # -- transaction window ------------------------------------------------
    # One acceptor, one transaction. The window belongs to a single device:
    # another device is refused until this one settles, cancels or times out.
    def arm(self, mac: str, ip: str = "") -> dict[str, Any]:
        mac = mac or ""
        with self._lock:
            now = time.monotonic()
            txn = self._txn
            if txn is not None and txn.get("settling"):
                # The counter is being written; do not steal the transaction.
                return {"ok": False, "reason": "settling", "state": self._state_locked(now)}
            if txn is not None and txn["deadline"] <= now:
                # Pulses are in, the count is being settled - do not steal it.
                return {"ok": False, "reason": "settling", "state": self._state_locked(now)}
            if txn is not None and txn["mac"] != mac:
                return {"ok": False, "reason": "busy", "state": self._state_locked(now)}
            if txn is None:
                self._txn = {
                    "mac": mac,
                    "ip": ip or "",
                    "opened": now,
                    "deadline": now + self.arm_seconds,
                    "pulses": 0,
                    "centavos": 0,
                    "last_pulse": 0.0,
                    "settling": False,
                }
            else:  # same device taps again: keep the coins, extend the window
                txn["deadline"] = now + self.arm_seconds
                txn["ip"] = ip or txn["ip"]
            log.info("coin slot armed for %ss (%s)", int(self.arm_seconds), mac or "unknown device")
            return {"ok": True, "reason": "", "state": self._state_locked(now)}

    def begin_settle(self) -> bool:
        """Freeze the count so a late pulse cannot join a burst being priced."""
        with self._lock:
            if self._txn is None:
                return False
            self._txn["settling"] = True
            return True

    def cancel(self, mac: str) -> dict[str, Any] | None:
        """Release the window if `mac` owns it; credited coins stay in the wallet."""
        with self._lock:
            txn = self._txn
            if txn is None or txn["mac"] != mac:
                return None
            state = self._state_locked(time.monotonic())
            self._txn = None
            return state

    def finish(self, pulses: int) -> None:
        """Release the window once its pulses have been settled."""
        with self._lock:
            txn = self._txn
            if txn is not None and txn["pulses"] == pulses:
                self._txn = None

    def txn_state(self) -> dict[str, Any]:
        with self._lock:
            return self._state_locked(time.monotonic())

    def reset(self) -> None:
        """Drop any open window (used by tests and by an operator restart)."""
        with self._lock:
            timer = self._timer
            self._txn = None
            self._pulses = 0
            self._timer = None
        if timer is not None:
            timer.cancel()
        self._stopped.clear()

    def _open_txn(self) -> dict[str, Any] | None:
        """Unlocked: the live window, or None when the slot is shut or settling."""
        txn = self._txn
        if txn is None or txn.get("settling") or txn["deadline"] <= time.monotonic():
            return None
        return txn

    def _state_locked(self, now: float) -> dict[str, Any]:
        txn = self._txn
        if txn is None:
            return {
                "active": False,
                "open": False,
                "mac": "",
                "ip": "",
                "seconds_left": 0,
                "arm_seconds": int(self.arm_seconds),
                "pulses": 0,
                "centavos": 0,
                "idle_left": None,
                "overdue": 0.0,
            }
        idle_left = None
        if txn["pulses"] and txn["last_pulse"]:
            idle_left = max(0.0, round(self.idle_seconds - (now - txn["last_pulse"]), 2))
        return {
            "active": True,
            "open": txn["deadline"] > now,
            "mac": txn["mac"],
            "ip": txn["ip"],
            "seconds_left": int(round(max(0.0, txn["deadline"] - now))),
            "arm_seconds": int(self.arm_seconds),
            "pulses": int(txn["pulses"]),
            "centavos": int(txn["centavos"]),
            "idle_left": idle_left,
            "overdue": round(max(0.0, now - txn["deadline"]), 2),
        }

    def health(self) -> dict[str, Any]:
        return {
            "driver": self.driver,
            "ready": self.ready,
            "pin": self.pin if self.driver == "gpio" else None,
            "pulse_centavos": self.pulse_centavos,
            "events": self.total_events,
            "ignored_pulses": self.ignored_pulses,
            "txn": self.txn_state(),
            "error": self.last_error,
        }

    # -- GPIO implementation ----------------------------------------------
    def _start_gpio(self) -> None:
        try:
            from gpiozero import Button  # hardware only
        except Exception as exc:  # not a Pi, or gpiozero missing
            self.last_error = f"gpiozero unavailable: {exc}"
            self.ready = False
            log.warning("GPIO coin driver disabled: %s", self.last_error)
            return
        try:
            self._button = Button(self.pin, pull_up=True, bounce_time=0.02)
            self._button.when_pressed = self._on_pulse
            self.ready = True
            log.info("coin acceptor listening on GPIO%s", self.pin)
        except Exception as exc:  # pragma: no cover - hardware failure
            self.last_error = f"GPIO{self.pin} init failed: {exc}"
            self.ready = False
            log.warning("GPIO coin driver disabled: %s", self.last_error)

    def _on_pulse(self) -> None:
        if self._stopped.is_set():
            return
        with self._lock:
            txn = self._open_txn()
            if txn is None:
                self.ignored_pulses += 1
                return
            txn["pulses"] += 1
            txn["centavos"] += self.pulse_centavos
            txn["last_pulse"] = time.monotonic()
            self._pulses += 1
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.idle_seconds, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        """A quiet line ends a pulse burst: hand it to the ledger once."""
        with self._lock:
            pulses = self._pulses
            self._pulses = 0
        if not pulses:
            return
        centavos = pulses * self.pulse_centavos
        self.total_events += 1
        self.events.put(CoinEvent(centavos=centavos, pulses=pulses, source="gpio"))
        log.info("coin accepted: %d pulse(s) = %d centavos", pulses, centavos)


def build_acceptor(settings) -> CoinAcceptor:
    return CoinAcceptor(
        driver=settings.coin_driver,
        pin=settings.gpio_pin,
        pulse_centavos=settings.coin_pulse_centavos,
        idle_seconds=settings.coin_idle_seconds,
        arm_seconds=settings.coin_arm_seconds,
    )
