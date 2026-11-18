"""Customer-facing portal: redeem a code, start a coin session, check status."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import db, deps
from ..services import billing, catalog, kiosk, sessions, vouchers

router = APIRouter(tags=["portal"])


def _sync(request: Request) -> None:
    """Best-effort router push; the enforcement loop retries on its own."""
    try:
        sessions.sync_router(request.app.state.router)
    except Exception:
        pass


def _target(url: str) -> str:
    """Where to return after a POST: same-site paths only."""
    return url if url.startswith("/") and not url.startswith("//") else "/"

CODE_ERRORS = {
    "missing": "That code was not found. Check the spelling and try again.",
    "void": "This code has been cancelled. Please ask the attendant.",
    "active": "This code is already in use.",
    "used": "This code has already been used.",
    "expired": "This code has expired.",
}


def _portal_context(request: Request) -> dict:
    ip, mac = deps.resolve_device(request)
    session = sessions.active_for_mac(mac)
    return {
        "ip": ip,
        "mac": mac,
        "session": sessions.session_view(session) if session else None,
        "plans": catalog.list_plans(active_only=True),
        "balance_centavos": billing.balance_centavos(),
        "coin_health": request.app.state.coin.health(),
        "coin_flow": request.app.state.coinflow.state_for(mac),
        "kiosk_voucher_enabled": catalog.setting_bool("kiosk_voucher_enabled", True),
        "show_rates": catalog.setting_bool("show_rates_on_portal", True),
    }


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    """The captive portal is one self-contained page for every device state."""
    return deps.render(request, "portal/captive.html", _portal_context(request))


@router.post("/redeem")
def redeem(
    request: Request,
    code: str = Form(""),
    csrf_token: str = Form(""),
):
    deps.verify_csrf(request, csrf_token)
    ip, mac = deps.resolve_device(request)
    if not mac:
        return deps.flash("/", err="We could not identify your device. Reconnect to BAGSIK PISOWIFI and try again.")

    voucher = vouchers.get_by_code(code)
    if voucher is None:
        return deps.flash("/", err=CODE_ERRORS["missing"])
    if voucher["status"] != "unused":
        return deps.flash("/", err=CODE_ERRORS.get(voucher["status"], "This code cannot be used."))

    plan = catalog.get_plan(voucher["plan_id"])
    if plan is None:
        return deps.flash("/", err="This code points to a plan that no longer exists.")

    try:
        with db.connect() as conn:
            sessions.start_session(
                conn,
                plan=plan,
                mac=mac,
                ip=ip,
                kind="voucher",
                voucher_id=voucher["id"],
                created_by="portal",
            )
    except sessions.SessionError as exc:
        message = {
            "device_busy": "This device already has an active session. Use up or stop it first.",
            "missing_device": "We could not identify your device.",
        }.get(str(exc), "Could not start your session. Please try again.")
        return deps.flash("/", err=message)

    _sync(request)
    return deps.flash("/", msg="Code accepted. You are online!")


@router.post("/disconnect")
def disconnect(request: Request, csrf_token: str = Form("")):
    deps.verify_csrf(request, csrf_token)
    ip, mac = deps.resolve_device(request)
    session = sessions.active_for_mac(mac)
    if session is None:
        return deps.flash("/", err="No active session on this device.")
    sessions.terminate(session["id"], "stopped by customer", actor="portal")
    _sync(request)
    return deps.flash("/", msg="Session stopped. Insert a coin or redeem a new code to go back online.")


@router.post("/pause")
def pause_time(request: Request, csrf_token: str = Form("")):
    """Customer-controlled pause: the countdown freezes and traffic is cut off."""
    deps.verify_csrf(request, csrf_token)
    ip, mac = deps.resolve_device(request)
    session = sessions.active_for_mac(mac)
    if session is None:
        return deps.flash("/", err="No active session on this device.")
    if session["status"] == "paused":
        return deps.flash("/", err="Your time is already paused.")
    if sessions.remaining_seconds(session) <= 0:
        return deps.flash("/", err="You are out of time. Insert a coin to continue.")
    sessions.pause(session["id"], actor="portal")
    _sync(request)
    return deps.flash("/", msg="Time paused. Click Resume Time when you are ready to surf again.")


@router.post("/resume")
def resume_time(request: Request, csrf_token: str = Form("")):
    deps.verify_csrf(request, csrf_token)
    ip, mac = deps.resolve_device(request)
    session = sessions.active_for_mac(mac)
    if session is None:
        return deps.flash("/", err="No active session on this device.")
    if session["status"] != "paused":
        return deps.flash("/", err="Your session is not paused.")
    sessions.resume(session["id"], actor="portal")
    _sync(request)
    return deps.flash("/", msg="Time resumed. You are online again!")


@router.get("/api/session")
def session_status(request: Request, mac: str = ""):
    """Live snapshot polled by the portal page; one shape for every state."""
    session = sessions.active_for_mac(mac or None) or sessions.active_for_mac(
        deps.resolve_device(request)[1]
    )
    balance = billing.balance_centavos()
    if session is None:
        return JSONResponse(
            {"active": False, "paused": False, "status": "idle", "session": None,
             "remaining_seconds": 0, "balance_centavos": balance}
        )
    view = sessions.session_view(session)
    return JSONResponse(
        {
            "active": session["status"] == "active",
            "paused": session["status"] == "paused",
            "status": session["status"],
            "session": view,
            "remaining_seconds": view["remaining_seconds"],
            "balance_centavos": balance,
        }
    )


# --------------------------------------------------------------------------- #
# Coin slot: one transaction at a time, armed per device, settled by the loop
# --------------------------------------------------------------------------- #
def _coin_state(request: Request, mac: str | None = None) -> dict:
    """Snapshot polled by the portal: whose turn it is and what the coins buy."""
    ip, detected = deps.resolve_device(request)
    mac = mac or detected or ""
    flow = request.app.state.coinflow.state_for(mac)
    session = sessions.active_for_mac(mac) if mac else None
    balance = billing.balance_centavos()
    return {
        "mac": mac,
        "ip": ip or "",
        "balance_centavos": balance,
        "balance_label": f"\u20b1{balance / 100:,.2f}",
        "session": sessions.session_view(session) if session else None,
        "dev_mode": request.app.state.settings.dev_mode,
        "simulate": bool(request.app.state.settings.dev_mode and request.app.state.coin.driver == "mock"),
        **flow,
    }


@router.get("/coin/state")
def coin_state(request: Request):
    """Live snapshot of the single coin transaction (idle / listening / busy)."""
    return JSONResponse(_coin_state(request))


@router.post("/coin/arm")
def coin_arm(request: Request, csrf_token: str = Form(""), to: str = Form("/")):
    """Insert Coin: bind the slot to this MAC + IP and start counting pulses."""
    deps.verify_csrf(request, csrf_token)
    to = _target(to)
    ip, mac = deps.resolve_device(request)
    if not mac:
        return deps.flash(
            to, err="We could not identify your device. Reconnect to the WiFi and try again."
        )
    if not request.app.state.coin.ready:
        return deps.flash(to, err="The coin slot is offline. Please call the attendant.")

    result = request.app.state.coinflow.arm(mac, ip)
    if not result["ok"]:
        return deps.flash(to, err=result["err"])
    return deps.flash(
        to,
        msg=f"Coin slot open for {result['state']['seconds_left']}s. "
        "Drop your coins - your time is added automatically.",
    )


@router.post("/coin/cancel")
def coin_cancel(request: Request, csrf_token: str = Form(""), to: str = Form("/")):
    """Give the slot back to the next customer without touching earned time."""
    deps.verify_csrf(request, csrf_token)
    to = _target(to)
    _ip, mac = deps.resolve_device(request)
    if not request.app.state.coinflow.cancel(mac or ""):
        return deps.flash(to, err="You do not have the coin slot open.")
    return deps.flash(to, msg="Coin slot released. Time already added stays with you.")


@router.get("/kiosk", response_class=HTMLResponse)
def kiosk_page(request: Request):
    """Counter page: coin slot, leftover credit and code sales."""
    ctx = _portal_context(request)
    ctx["coin_state"] = _coin_state(request)
    return deps.render(request, "portal/kiosk.html", ctx)


@router.post("/kiosk/buy")
def kiosk_buy(
    request: Request,
    plan_id: int = Form(0),
    quantity: int = Form(1),
    csrf_token: str = Form(""),
):
    """Sell a printable code at the counter from credit left in the wallet."""
    deps.verify_csrf(request, csrf_token)
    _ip, mac = deps.resolve_device(request)
    if not mac:
        return deps.flash("/kiosk", err="We could not identify your device. Reconnect and try again.")

    try:
        plan, codes = kiosk.create_voucher(plan_id, quantity=quantity)
    except kiosk.KioskError as exc:
        message = {
            "insufficient_credit": "Not enough credit in this device's wallet. Insert coins first.",
            "invalid_plan": "That rate is not available.",
        }.get(str(exc), "Purchase failed. Please try again.")
        return deps.flash("/kiosk", err=message)
    return deps.flash(
        "/kiosk", msg=f"{plan['name']} voucher: {' '.join(codes)} - redeem it on the portal."
    )


@router.post("/kiosk/simulate-coin")
def simulate_coin(
    request: Request,
    centavos: int = Form(100),
    csrf_token: str = Form(""),
    to: str = Form("/kiosk"),
):
    """Dev-only stand-in for the coin acceptor; it respects the arm window."""
    deps.verify_csrf(request, csrf_token)
    to = _target(to)
    if not request.app.state.settings.dev_mode or request.app.state.coin.driver != "mock":
        return deps.flash(to, err="Coin simulation is only available in development mode.")
    if centavos <= 0 or centavos > 10000:
        return deps.flash(to, err="Choose a realistic coin value.")
    if not request.app.state.coin.inject(centavos, source="mock"):
        return deps.flash(to, err="The slot is closed. Tap Insert Coin first.")
    return deps.flash(to, msg=f"Simulated {centavos / 100:.2f} peso coin inserted.")
