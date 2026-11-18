"""Admin console: dashboard, vouchers, plans, sessions, cash, settings."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import db, deps, security, utils
from ..hardware import pins as coin_pins
from ..netctl import interfaces as net_ports
from ..services import billing, catalog, sessions, stats, vouchers

router = APIRouter(prefix="/admin", tags=["admin"])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _pesos_to_centavos(raw: str) -> int:
    cleaned = (raw or "").replace(",", "").replace("\u20b1", "").strip()
    try:
        return max(0, int(round(float(cleaned) * 100)))
    except ValueError:
        return 0


def _to_int(raw, default: int = 0) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _session_json(row) -> dict:
    return {
        "id": row["id"],
        "mac": row["mac"],
        "ip": row["ip"] or "-",
        "plan": row["plan_name"],
        "kind": row["kind"],
        "status": row["status"],
        "left": utils.fmt_seconds(sessions.remaining_seconds(row)),
        "used": utils.fmt_bytes(row["bytes_used"]),
        "cap": utils.fmt_bytes(row["data_cap_bytes"]) if row["data_cap_bytes"] else "no cap",
        "rate": (
            f"{row['rate_down_kbps']}/{row['rate_up_kbps']} kbps"
            if row["rate_down_kbps"]
            else "unshaped"
        ),
    }


def _sync(request: Request) -> None:
    try:
        sessions.sync_router(request.app.state.router)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if deps.current_admin(request):
        return deps.flash("/admin")
    return deps.render(request, "admin/login.html")


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    csrf_token: str = Form(""),
):
    deps.verify_csrf(request, csrf_token)
    row = db.query_one("SELECT * FROM admin_users WHERE username = ?", (username.strip(),))
    if row is None or not security.verify_password(password, row["password_hash"]):
        with db.connect() as conn:
            db.audit(conn, username.strip() or "unknown", "login.failed", "wrong credentials")
        return deps.flash("/admin/login", err="Wrong username or password.")

    with db.connect() as conn:
        conn.execute(
            "UPDATE admin_users SET last_login_at = ? WHERE id = ?", (utils.now_iso(), row["id"])
        )
        db.audit(conn, row["username"], "login.ok")
    response = deps.flash("/admin")
    deps.set_admin_cookie(response, row["id"], row["username"])
    return response


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    deps.verify_csrf(request, csrf_token)
    response = deps.flash("/admin/login", msg="Signed out.")
    deps.clear_admin_cookie(response)
    return response


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    admin = deps.require_admin(request)
    return deps.render(
        request,
        "admin/dashboard.html",
        {
            "admin": admin,
            "stats": stats.overview(),
            "hourly": stats.hourly_revenue(),
            "weekly": stats.daily_revenue(7),
            "mix": stats.plan_mix(7),
            "live_sessions": sessions.live_sessions(),
            "recent": sessions.recent_sessions(limit=10),
            "audit": catalog.recent_audit(10),
            "router_health": request.app.state.router.health(),
            "coin_health": request.app.state.coin.health(),
            "clock": stats.clock_now(),
            "remaining": sessions.remaining_seconds,
            "business": catalog.setting("business_name", "BAGSIK PISOWIFI"),
        },
    )


@router.get("/api/overview")
def api_overview(request: Request):
    deps.require_admin(request)
    data = stats.overview()
    live = sessions.live_sessions()
    return JSONResponse(
        {
            "clock": stats.clock_now(),
            "stats": {
                "revenue": utils.fmt_money(data["revenue_centavos"]),
                "delta": f"{data['delta_pct']:+d}%",
                "live": data["live"],
                "started_today": data["started_today"],
                "data": utils.fmt_bytes(data["bytes_today"]),
                "wallet": utils.fmt_money(data["wallet_centavos"]),
                "unused": data["vouchers_unused"],
            },
            "sessions": [_session_json(row) for row in live],
            "router": request.app.state.router.health(),
            "coin": request.app.state.coin.health(),
        }
    )


# --------------------------------------------------------------------------- #
# Vouchers
# --------------------------------------------------------------------------- #
@router.get("/vouchers", response_class=HTMLResponse)
def vouchers_page(request: Request, status: str = "", q: str = "", batch: str = ""):
    admin = deps.require_admin(request)
    return deps.render(
        request,
        "admin/vouchers.html",
        {
            "admin": admin,
            "items": vouchers.list_vouchers(status=status, search=q, batch_id=batch, limit=300),
            "counts": vouchers.counts(),
            "plans": catalog.list_plans(active_only=False),
            "batches": vouchers.list_batches(20),
            "filters": {"status": status, "q": q, "batch": batch},
        },
    )


@router.post("/vouchers/generate")
def vouchers_generate(
    request: Request,
    plan_id: str = Form("0"),
    quantity: str = Form("20"),
    label: str = Form(""),
    note: str = Form(""),
    csrf_token: str = Form(""),
):
    admin = deps.require_admin(request)
    deps.verify_csrf(request, csrf_token)
    plan = catalog.get_plan(plan_id)
    if plan is None:
        return deps.flash("/admin/vouchers", err="Pick a plan first.")
    count = max(1, min(vouchers.MAX_BATCH, _to_int(quantity, 20)))
    batch_id, codes = vouchers.create_batch(
        plan=plan,
        quantity=count,
        label=label,
        note=note,
        actor=admin["username"],
    )
    return deps.flash(
        f"/admin/vouchers/print?batch={batch_id}",
        msg=f"{len(codes)} x {plan['name']} vouchers created.",
    )


@router.get("/vouchers/print", response_class=HTMLResponse)
def vouchers_print(request: Request, batch: int = 0):
    admin = deps.require_admin(request)
    codes = vouchers.batch_codes(batch)
    batch_row = db.query_one(
        "SELECT b.*, p.name AS plan_name, p.duration_minutes, p.data_cap_mb"
        " FROM batches b LEFT JOIN plans p ON p.id = b.plan_id WHERE b.id = ?",
        (batch,),
    )
    return deps.render(
        request,
        "admin/voucher_print.html",
        {
            "admin": admin,
            "codes": codes,
            "batch": batch_row,
            "batch_id": batch,
            "business": catalog.setting("business_name", "BAGSIK PISOWIFI"),
            "footer": catalog.setting("print_footer", ""),
        },
    )


@router.post("/vouchers/{voucher_id}/void")
def voucher_void(request: Request, voucher_id: int, csrf_token: str = Form("")):
    admin = deps.require_admin(request)
    deps.verify_csrf(request, csrf_token)
    result = vouchers.void_voucher(voucher_id, actor=admin["username"])
    messages = {
        "void": ("Voucher cancelled.", ""),
        "missing": ("", "That voucher does not exist."),
        "in_use": ("", "That voucher is being used right now - stop the session first."),
        "already_void": ("Voucher was already cancelled.", ""),
    }
    msg, err = messages.get(result, ("Done.", ""))
    return deps.flash("/admin/vouchers", msg=msg, err=err)


@router.post("/vouchers/batch/{batch_id}/void")
def voucher_batch_void(request: Request, batch_id: int, csrf_token: str = Form("")):
    admin = deps.require_admin(request)
    deps.verify_csrf(request, csrf_token)
    count = vouchers.void_batch(batch_id, actor=admin["username"])
    return deps.flash("/admin/vouchers", msg=f"{count} unused vouchers in the batch were cancelled.")


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #
@router.get("/plans", response_class=HTMLResponse)
def plans_page(request: Request, inactive: int = 0):
    admin = deps.require_admin(request)
    return deps.render(
        request,
        "admin/plans.html",
        {
            "admin": admin,
            "plans": catalog.list_plans(active_only=not bool(inactive)),
            "show_inactive": bool(inactive),
        },
    )


@router.post("/plans/create")
def plan_create(
    request: Request,
    name: str = Form(""),
    price: str = Form("0"),
    minutes: str = Form("0"),
    data_cap_mb: str = Form("0"),
    down_kbps: str = Form("0"),
    up_kbps: str = Form("0"),
    sort_order: str = Form("100"),
    csrf_token: str = Form(""),
):
    admin = deps.require_admin(request)
    deps.verify_csrf(request, csrf_token)
    if not name.strip():
        return deps.flash("/admin/plans", err="Give the plan a name.")
    catalog.create_plan(
        name=name,
        price_centavos=_pesos_to_centavos(price),
        duration_minutes=max(1, _to_int(minutes, 30)),
        data_cap_mb=max(0, _to_int(data_cap_mb, 0)),
        rate_down_kbps=max(0, _to_int(down_kbps, 0)),
        rate_up_kbps=max(0, _to_int(up_kbps, 0)),
        sort_order=_to_int(sort_order, 100),
        actor=admin["username"],
    )
    return deps.flash("/admin/plans", msg=f"Plan '{name}' created.")


@router.post("/plans/{plan_id}/update")
def plan_update(
    request: Request,
    plan_id: int,
    name: str = Form(""),
    price: str = Form("0"),
    minutes: str = Form("0"),
    data_cap_mb: str = Form("0"),
    down_kbps: str = Form("0"),
    up_kbps: str = Form("0"),
    sort_order: str = Form("100"),
    csrf_token: str = Form(""),
):
    admin = deps.require_admin(request)
    deps.verify_csrf(request, csrf_token)
    catalog.update_plan(
        plan_id,
        name=name.strip() or "Untitled plan",
        price_centavos=_pesos_to_centavos(price),
        duration_minutes=max(1, _to_int(minutes, 30)),
        data_cap_mb=max(0, _to_int(data_cap_mb, 0)),
        rate_down_kbps=max(0, _to_int(down_kbps, 0)),
        rate_up_kbps=max(0, _to_int(up_kbps, 0)),
        sort_order=_to_int(sort_order, 100),
    )
    return deps.flash("/admin/plans", msg="Plan updated.")


@router.post("/plans/{plan_id}/toggle")
def plan_toggle(request: Request, plan_id: int, csrf_token: str = Form("")):
    admin = deps.require_admin(request)
    deps.verify_csrf(request, csrf_token)
    plan = catalog.get_plan(plan_id)
    if plan is not None:
        catalog.set_plan_active(plan_id, not bool(plan["is_active"]), actor=admin["username"])
    return deps.flash("/admin/plans", msg="Plan availability updated.")


@router.post("/plans/{plan_id}/delete")
def plan_delete(request: Request, plan_id: int, csrf_token: str = Form("")):
    admin = deps.require_admin(request)
    deps.verify_csrf(request, csrf_token)
    result = catalog.delete_plan(plan_id, actor=admin["username"])
    msg = "Plan deleted." if result == "deleted" else "Plan has sales history, so it was deactivated instead."
    return deps.flash("/admin/plans", msg=msg)


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
@router.get("/sessions", response_class=HTMLResponse)
def sessions_page(request: Request, status: str = ""):
    admin = deps.require_admin(request)
    return deps.render(
        request,
        "admin/sessions.html",
        {
            "admin": admin,
            "live": sessions.live_sessions(),
            "recent": sessions.recent_sessions(limit=60, status=status),
            "status_filter": status,
            "remaining": sessions.remaining_seconds,
            "router_health": request.app.state.router.health(),
        },
    )


@router.post("/sessions/{session_id}/terminate")
def session_terminate(request: Request, session_id: int, csrf_token: str = Form("")):
    admin = deps.require_admin(request)
    deps.verify_csrf(request, csrf_token)
    sessions.terminate(session_id, "stopped by admin", actor=admin["username"])
    _sync(request)
    return deps.flash("/admin/sessions", msg=f"Session #{session_id} terminated.")


@router.post("/sessions/{session_id}/pause")
def session_pause(request: Request, session_id: int, csrf_token: str = Form("")):
    admin = deps.require_admin(request)
    deps.verify_csrf(request, csrf_token)
    sessions.pause(session_id, actor=admin["username"])
    _sync(request)
    return deps.flash("/admin/sessions", msg=f"Session #{session_id} paused.")


@router.post("/sessions/{session_id}/resume")
def session_resume(request: Request, session_id: int, csrf_token: str = Form("")):
    admin = deps.require_admin(request)
    deps.verify_csrf(request, csrf_token)
    sessions.resume(session_id, actor=admin["username"])
    _sync(request)
    return deps.flash("/admin/sessions", msg=f"Session #{session_id} resumed.")


@router.post("/sessions/{session_id}/extend")
def session_extend(
    request: Request,
    session_id: int,
    minutes: str = Form("5"),
    csrf_token: str = Form(""),
):
    admin = deps.require_admin(request)
    deps.verify_csrf(request, csrf_token)
    added = max(1, min(720, _to_int(minutes, 5)))
    if sessions.extend(session_id, added, actor=admin["username"]):
        return deps.flash("/admin/sessions", msg=f"Session #{session_id} extended by {added} minutes.")
    return deps.flash("/admin/sessions", err="Only live sessions can be extended.")


# --------------------------------------------------------------------------- #
# Coins / cash
# --------------------------------------------------------------------------- #
@router.get("/coins", response_class=HTMLResponse)
def coins_page(request: Request):
    admin = deps.require_admin(request)
    start, end = stats.day_bounds()
    return deps.render(
        request,
        "admin/coins.html",
        {
            "admin": admin,
            "credits": billing.recent_credits(limit=120),
            "balance_centavos": billing.balance_centavos(),
            "today": billing.totals_between(start, end),
            "coin_health": request.app.state.coin.health(),
        },
    )


@router.post("/coins/reset")
def coins_reset(request: Request, csrf_token: str = Form("")):
    admin = deps.require_admin(request)
    deps.verify_csrf(request, csrf_token)
    count = billing.reset_wallet(actor=admin["username"])
    return deps.flash("/admin/coins", msg=f"Wallet cleared ({count} coin entries voided).")


@router.post("/coins/add")
def coins_add(request: Request, amount: str = Form("0"), csrf_token: str = Form("")):
    admin = deps.require_admin(request)
    deps.verify_csrf(request, csrf_token)
    centavos = _pesos_to_centavos(amount)
    if centavos <= 0:
        return deps.flash("/admin/coins", err="Enter an amount greater than zero.")
    billing.add_coin(centavos, source="admin", note=f"manual entry by {admin['username']}")
    return deps.flash("/admin/coins", msg=f"{utils.fmt_money(centavos)} added to the kiosk wallet.")


# --------------------------------------------------------------------------- #
# Settings + account
# --------------------------------------------------------------------------- #
@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    admin = deps.require_admin(request)
    return deps.render(
        request,
        "admin/settings.html",
        {
            "admin": admin,
            "values": catalog.all_settings(),
            "router_health": request.app.state.router.health(),
            "coin_health": request.app.state.coin.health(),
        },
    )


@router.post("/settings")
def settings_save(
    request: Request,
    business_name: str = Form("BAGSIK PISOWIFI"),
    portal_headline: str = Form(""),
    portal_subtext: str = Form(""),
    portal_contact: str = Form(""),
    print_footer: str = Form(""),
    grace_seconds: str = Form("60"),
    kiosk_voucher_enabled: str = Form("0"),
    show_rates_on_portal: str = Form("0"),
    csrf_token: str = Form(""),
):
    admin = deps.require_admin(request)
    deps.verify_csrf(request, csrf_token)
    catalog.update_settings(
        {
            "business_name": business_name.strip() or "BAGSIK PISOWIFI",
            "portal_headline": portal_headline.strip(),
            "portal_subtext": portal_subtext.strip(),
            "portal_contact": portal_contact.strip(),
            "print_footer": print_footer.strip(),
            "grace_seconds": str(max(0, min(600, _to_int(grace_seconds, 60)))),
            "kiosk_voucher_enabled": "1" if kiosk_voucher_enabled == "1" else "0",
            "show_rates_on_portal": "1" if show_rates_on_portal == "1" else "0",
        },
        actor=admin["username"],
    )
    return deps.flash("/admin/settings", msg="Settings saved.")


# --------------------------------------------------------------------------- #
# Coin hardware (board + pin)
# --------------------------------------------------------------------------- #
@router.get("/settings/hardware", response_class=HTMLResponse)
def hardware_page(request: Request, board: str = ""):
    admin = deps.require_admin(request)
    values = catalog.all_settings()
    saved = values.get("coin_board", "auto")
    choice = board if board in coin_pins.BOARD_IDS else saved
    target = coin_pins.current_target(choice, values.get("coin_pin", coin_pins.DEFAULT_PIN))
    detected = coin_pins.detect_board()
    return deps.render(
        request,
        "admin/hardware.html",
        {
            "admin": admin,
            "values": values,
            "choice": choice,
            "resolved": target,
            "pin": target["physical"] or coin_pins.DEFAULT_PIN,
            "pins": coin_pins.pin_options(target["board"]) if target["gpio"] else [],
            "boards": coin_pins.board_choices(choice),
            "detected": detected,
            "detected_label": coin_pins.board_label(detected),
            "coin_health": request.app.state.coin.health(),
            "env_driver": request.app.state.settings.coin_driver,
            "env_pin": request.app.state.settings.gpio_pin,
            "saved": "coin_board" in values,
        },
    )


@router.post("/settings/hardware")
def hardware_save(
    request: Request,
    board: str = Form("auto"),
    pin: str = Form(str(coin_pins.DEFAULT_PIN)),
    nodemcu_host: str = Form(""),
    nodemcu_port: str = Form("8080"),
    csrf_token: str = Form(""),
):
    admin = deps.require_admin(request)
    deps.verify_csrf(request, csrf_token)
    if board not in coin_pins.BOARD_IDS:
        board = "auto"

    # Resolve 'auto' to a real board first, then validate the pin the operator
    # sent instead of quietly falling back to the default.
    target = coin_pins.current_target(board, coin_pins.DEFAULT_PIN)
    if target["gpio"]:
        chosen = coin_pins.resolve(target["board"], pin)
        if not chosen["ok"]:
            return deps.flash(
                f"/admin/settings/hardware?board={board}",
                err=f"Pin {pin} is not a GPIO pin on this board.",
            )
        target = chosen

    values = {
        "coin_board": board,
        "coin_nodemcu_host": nodemcu_host.strip(),
        "coin_nodemcu_port": str(min(65535, max(1, _to_int(nodemcu_port, 8080)))),
    }
    if target["gpio"]:
        values["coin_pin"] = str(target["physical"])
    catalog.update_settings(values, actor=admin["username"])

    # Apply it now so the operator can drop a coin without restarting anything.
    driver = "mock" if request.app.state.settings.coin_driver == "mock" else target["driver"]
    request.app.state.coin.apply(driver, target["backend_pin"])

    if target["gpio"]:
        msg = f"Coin slot now listens on physical pin {target['physical']} ({target['name']})."
    else:
        msg = "Saved. The slot stays closed until the NodeMCU bridge firmware is installed."
    return deps.flash("/admin/settings/hardware", msg=msg)


# --------------------------------------------------------------------------- #
# Hotspot server (WAN uplink + customer ports)
# --------------------------------------------------------------------------- #
def _hotspot_defaults(values: dict[str, str], settings) -> tuple[str, list[str]]:
    """Saved assignment, else the wiring default from the ports this box has."""
    detected = [port["name"] for port in net_ports.list_ethernet()]
    saved_hotspots = net_ports.split_names(values.get("net_hotspot_ifaces", ""))
    env_names = [name for name in (settings.lan_iface, settings.wan_iface) if name and name != "auto"]
    return net_ports.default_assignment(
        detected or env_names or saved_hotspots,
        values.get("net_wan_iface", ""),
        saved_hotspots,
    )


@router.get("/settings/hotspot", response_class=HTMLResponse)
def hotspot_page(request: Request):
    admin = deps.require_admin(request)
    settings = request.app.state.settings
    values = catalog.all_settings()
    wan, hotspots = _hotspot_defaults(values, settings)

    address = values.get("net_hotspot_ip", "")
    if not address:
        cidr = settings.lan_cidr
        address = cidr.split("/")[0] if cidr and cidr != "auto" else "10.0.0.1"

    health = request.app.state.router.health()
    live_lans = health.get("lan_ifaces") or net_ports.split_names(str(health.get("lan_iface", "")))
    return deps.render(
        request,
        "admin/hotspot.html",
        {
            "admin": admin,
            "rows": net_ports.assign_rows(net_ports.list_ethernet(), wan, hotspots),
            "wan": wan,
            "hotspots": hotspots,
            "address": address,
            "router_health": health,
            "live_lans": live_lans or hotspots,
            "live_wan": health.get("wan_iface") or wan,
            "env_lan": settings.lan_iface,
            "env_wan": settings.wan_iface,
            "saved": bool(values.get("net_hotspot_ifaces")),
        },
    )


@router.post("/settings/hotspot")
async def hotspot_save(request: Request):
    admin = deps.require_admin(request)
    form = await request.form()
    deps.verify_csrf(request, str(form.get("csrf_token", "")))

    # One role select per detected port, so the names arrive as role:eth1=hotspot.
    roles = {
        str(key)[len("role:"):].strip(): str(value)
        for key, value in form.multi_items()
        if str(key).startswith("role:")
    }
    wan = str(form.get("wan_name", "")).strip() or next(
        (name for name, role in roles.items() if role == "wan"), ""
    )
    hotspots = [name for name, role in roles.items() if role == "hotspot"]
    for name in net_ports.split_names(str(form.get("hotspot_names", ""))):
        if name not in hotspots:
            hotspots.append(name)
    for name in net_ports.split_names(str(form.get("extra", ""))):
        if name not in hotspots:
            hotspots.append(name)
    hotspots = [name for name in hotspots if name != wan]

    if not wan:
        return deps.flash("/admin/settings/hotspot", err="Pick the port that carries the internet uplink (eth0).")
    names = [wan, *hotspots]
    if any(not net_ports.valid_name(name) for name in names):
        return deps.flash(
            "/admin/settings/hotspot",
            err="Port names may only contain letters, digits, '.', '_' or '-'.",
        )
    if not hotspots:
        return deps.flash(
            "/admin/settings/hotspot",
            err="Pick at least one hotspot port - a second NIC or the Wi-Fi interface.",
        )
    address = str(form.get("address", "")).strip()
    if not net_ports.valid_ipv4(address):
        return deps.flash("/admin/settings/hotspot", err=f"'{address}' is not an IPv4 address.")

    catalog.update_settings(
        {
            "net_wan_iface": wan,
            "net_hotspot_ifaces": ",".join(hotspots),
            "net_hotspot_ip": address,
        },
        actor=admin["username"],
    )

    # Apply now: the local driver rebuilds nftables and the tc trees in place.
    try:
        applied = bool(request.app.state.router.set_interfaces(hotspots, wan, f"{address}/24"))
    except Exception as exc:
        return deps.flash(
            "/admin/settings/hotspot",
            err=f"Saved, but the router driver rejected the change: {exc}",
        )
    if applied:
        _sync(request)

    msg = (
        f"Hotspot server enforces on {', '.join(hotspots)}; {wan} is the uplink "
        f"at {address}/24."
    )
    if not applied:
        msg += " A mock driver keeps simulating - the local driver applies this on the appliance."
    return deps.flash("/admin/settings/hotspot", msg=msg)


@router.post("/account/password")
def account_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    csrf_token: str = Form(""),
):
    admin = deps.require_admin(request)
    deps.verify_csrf(request, csrf_token)
    row = db.query_one("SELECT * FROM admin_users WHERE id = ?", (admin["id"],))
    if row is None or not security.verify_password(current_password, row["password_hash"]):
        return deps.flash("/admin/settings", err="Current password is incorrect.")
    if len(new_password) < 8:
        return deps.flash("/admin/settings", err="New password must be at least 8 characters.")
    if new_password != confirm_password:
        return deps.flash("/admin/settings", err="New passwords do not match.")
    with db.connect() as conn:
        conn.execute(
            "UPDATE admin_users SET password_hash = ? WHERE id = ?",
            (security.hash_password(new_password), admin["id"]),
        )
        db.audit(conn, admin["username"], "account.password")
    return deps.flash("/admin/settings", msg="Password updated.")
