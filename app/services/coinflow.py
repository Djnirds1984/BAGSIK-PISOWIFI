"""Single-transaction coin flow: arm, count, settle.

The acceptor owns the hardware window (one device at a time). This service is
the bookkeeping half: it waits until a pulse burst goes quiet, prices it with
the admin's per-pulse rates, then grants or extends the session of the device
that armed the slot before releasing the lock.

Nothing here runs on a request thread: `tick()` is driven by the background
loop in `app.tasks`, so a customer closing the browser cannot leave the slot
armed or their coins unspent.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .. import db
from . import billing, kiosk, sessions

log = logging.getLogger("bagsik.coinflow")

BUSY = "Another customer is currently inserting coins. Please wait."
SETTLING = "The machine is still finishing the last coin count. Try again in a moment."
RECENT_SECONDS = 90.0
SETTLE_GRACE = 30.0  # a closed window that cannot settle is released after this


class CoinFlow:
    def __init__(self, acceptor, driver=None) -> None:
        self.acceptor = acceptor
        self.driver = driver
        self._lock = threading.Lock()
        self._granted: dict[str, dict[str, Any]] = {}

    # -- customer actions --------------------------------------------------
    def arm(self, mac: str, ip: str | None) -> dict[str, Any]:
        result = self.acceptor.arm(mac or "", ip or "")
        if not result["ok"]:
            error = BUSY if result["reason"] == "busy" else SETTLING
            return {"ok": False, "err": error, "state": result["state"]}
        self._granted.pop(mac, None)
        return {"ok": True, "err": "", "state": result["state"]}

    def cancel(self, mac: str) -> bool:
        return self.acceptor.cancel(mac or "") is not None

    def reset(self) -> None:
        """Forget the last grant (tests, or a fresh shift at the counter)."""
        self._granted.clear()

    def state_for(self, mac: str | None) -> dict[str, Any]:
        txn = self.acceptor.txn_state()
        mine = bool(txn["active"] and mac and txn["mac"] == mac)
        other = bool(txn["active"] and not mine)
        granted = self.recent(mac)
        phase = self._phase(txn, mine)
        if phase == "idle" and granted is not None:
            phase = "granted"  # just settled: show what the coins bought
        return {
            "txn": txn,
            "mine": mine,
            "busy": other,
            "phase": phase,
            "busy_message": (BUSY if txn["open"] else SETTLING) if other else "",
            "quote": kiosk.quote_pulses(
                txn["pulses"], self.acceptor.pulse_centavos, txn["centavos"]
            )
            if txn["pulses"]
            else None,
            "granted": granted,
            "pulse_centavos": self.acceptor.pulse_centavos,
            "coin_ready": self.acceptor.ready,
        }

    @staticmethod
    def _phase(txn: dict[str, Any], mine: bool) -> str:
        """One word the portal can switch on: idle, listening, counting, ..."""
        if not txn["active"]:
            return "idle"
        if not mine:
            return "busy" if txn["open"] else "processing"
        if not txn["pulses"]:
            return "listening" if txn["open"] else "expired"
        if txn["open"] and (txn["idle_left"] or 0) > 0:
            return "counting"
        return "processing"

    def recent(self, mac: str | None) -> dict[str, Any] | None:
        if not mac:
            return None
        entry = self._granted.get(mac)
        if entry is None:
            return None
        age = time.time() - entry["at"]
        if age > RECENT_SECONDS:
            self._granted.pop(mac, None)
            return None
        return {**entry, "age_seconds": int(age)}

    # -- settlement loop ---------------------------------------------------
    def tick(self) -> dict[str, Any] | None:
        """Settle a finished burst, release dead windows. Called every ~0.4s."""
        if not self._lock.acquire(blocking=False):
            return None  # the previous pass is still writing
        try:
            txn = self.acceptor.txn_state()
            if not txn["active"]:
                return None
            if txn["pulses"] <= 0:
                # Max arm duration reached with no coin: release the lock.
                if not txn["open"]:
                    self.acceptor.finish(0)
                    log.info("coin window expired with no pulses (%s)", txn["mac"] or "unknown")
                return None
            if not txn["open"] and txn["overdue"] > SETTLE_GRACE:
                # Belt and braces: a window that never settles must not wedge the
                # slot. Whatever the wallet holds is still there for the customer.
                log.warning("coin window stuck for %ss, forcing release (%s)", txn["overdue"], txn["mac"])
                self.acceptor.finish(txn["pulses"])
                return None
            if txn["open"] and (txn["idle_left"] or 0) > 0:
                return None  # the burst is still running
            return self._settle(txn)
        finally:
            self._lock.release()

    def _settle(self, txn: dict[str, Any]) -> dict[str, Any] | None:
        # Freeze the count first: a pulse that lands mid-write would otherwise
        # be credited twice (once here, once in the next burst).
        self.acceptor.begin_settle()
        txn = self.acceptor.txn_state()
        quote = kiosk.quote_pulses(
            txn["pulses"], self.acceptor.pulse_centavos, txn["centavos"]
        )
        mac = txn["mac"]
        if quote["minutes"] <= 0:
            # Below the cheapest rate: coins stay in the wallet for next time.
            log.info("coin burst below the first rate: %s pulse(s)", quote["pulses"])
            self.acceptor.finish(quote["pulses"])
            return None

        try:
            with db.connect() as conn:
                session = sessions.active_for_mac(mac, conn)
                if session is not None:
                    sessions.extend(session["id"], quote["minutes"], actor="coin", conn=conn)
                    session_id = int(session["id"])
                else:
                    plan = dict(quote["tier"] or {})
                    plan["duration_minutes"] = quote["minutes"]
                    session = sessions.start_session(
                        conn,
                        plan=plan,
                        mac=mac,
                        ip=txn["ip"] or None,
                        kind="coin",
                        coin_centavos=quote["used_centavos"],
                        created_by="coin",
                    )
                    session_id = int(session["id"])
                billing.consume(
                    conn,
                    quote["used_centavos"],
                    session_id=session_id,
                    note=f"coin transaction: {quote['pulses']} pulse(s)",
                )
                db.audit(
                    conn,
                    "coin",
                    "coinflow.grant",
                    f"{quote['pulses']} pulse(s) -> {quote['minutes']}m mac={mac}",
                )
        except billing.InsufficientCredit:
            # The flush that credits the wallet has not landed yet; retry next tick.
            log.debug("settlement waiting for the coin ledger (%s)", mac)
            return None
        except sessions.SessionError:
            log.exception("could not start a session for %s", mac)
            self.acceptor.finish(quote["pulses"])
            return None

        self.acceptor.finish(quote["pulses"])
        self._granted[mac] = {
            "minutes": quote["minutes"],
            "pulses": quote["pulses"],
            "tier": (quote["tier"] or {}).get("name", ""),
            "session_id": session_id,
            "at": time.time(),
        }
        self._sync()
        log.info("coin transaction settled: %s pulse(s) -> %s min", quote["pulses"], quote["minutes"])
        return {**quote, "session_id": session_id, "mac": mac}

    def _sync(self) -> None:
        if self.driver is None:
            return
        try:
            sessions.sync_router(self.driver)
        except Exception:  # the enforcement loop will retry
            log.warning("router sync after coin settlement failed", exc_info=True)
