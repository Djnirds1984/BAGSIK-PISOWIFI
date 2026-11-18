"""Request plumbing shared by the portal and admin routers."""

from __future__ import annotations

import functools
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import db, security, utils
from .config import get_settings
from .services import catalog


@functools.lru_cache(maxsize=1)
def templates() -> Jinja2Templates:
    settings = get_settings()
    engine = Jinja2Templates(directory=str(settings.template_dir))
    env = engine.env
    env.filters.update(
        {
            "money": utils.fmt_money,
            "minutes": utils.fmt_minutes,
            "seconds": utils.fmt_seconds,
            "bytes": utils.fmt_bytes,
            "dt": lambda value: utils.fmt_dt(value, settings.timezone),
            "dt_short": lambda value: utils.fmt_dt(value, settings.timezone, "%b %d, %H:%M"),
            "dt_time": lambda value: utils.fmt_dt(value, settings.timezone, "%H:%M"),
        }
    )
    env.globals.update(
        {
            "app_version": settings.version,
            "money": utils.fmt_money,
            "minutes": utils.fmt_minutes,
            "seconds": utils.fmt_seconds,
            "bytes": utils.fmt_bytes,
            "now_clock": lambda: utils.fmt_dt(utils.now_iso(), settings.timezone, "%b %d \u00b7 %H:%M"),
        }
    )
    return engine


def csrf_token(request: Request) -> str:
    settings = get_settings()
    token = request.cookies.get(settings.csrf_cookie_name)
    if token:
        return token
    token = security.new_csrf_token()
    request.state.new_csrf = token
    return token


def render(request: Request, name: str, context: dict[str, Any] | None = None, status_code: int = 200):
    settings = get_settings()
    token = csrf_token(request)
    ctx: dict[str, Any] = {
        "request": request,
        "settings": settings,
        "biz": catalog.all_settings(),
        "csrf_token": token,
        "current_path": request.url.path,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
    }
    ctx.update(context or {})
    response = templates().TemplateResponse(
        request=request, name=name, context=ctx, status_code=status_code
    )
    if getattr(request.state, "new_csrf", None):
        response.set_cookie(
            settings.csrf_cookie_name,
            token,
            max_age=60 * 60 * 12,
            httponly=True,
            samesite="lax",
            path="/",
        )
    response.headers.setdefault("Cache-Control", "no-store")
    return response


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def current_admin(request: Request) -> dict | None:
    settings = get_settings()
    payload = security.unsign(request.cookies.get(settings.cookie_name), settings.secret_key)
    if not payload:
        return None
    row = db.query_one(
        "SELECT id, username, display_name FROM admin_users WHERE id = ?", (payload.get("uid"),)
    )
    if row is None:
        return None
    return {"id": row["id"], "username": row["username"], "display_name": row["display_name"]}


def require_admin(request: Request) -> dict:
    admin = current_admin(request)
    if admin is None:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    return admin


def verify_csrf(request: Request, token: str) -> None:
    settings = get_settings()
    expected = request.cookies.get(settings.csrf_cookie_name, "")
    if not expected or not security.constant_time_equal(expected, token or ""):
        raise HTTPException(status_code=400, detail="Invalid form token - reload the page and try again.")


def set_admin_cookie(response, admin_id: int, username: str) -> None:
    settings = get_settings()
    token = security.sign(
        {"uid": admin_id, "u": username}, settings.secret_key, settings.session_hours * 3600
    )
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_admin_cookie(response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.cookie_name, path="/")


# --------------------------------------------------------------------------- #
# Device identity
# --------------------------------------------------------------------------- #
def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def resolve_device(request: Request) -> tuple[str | None, str | None]:
    """Return (ip, mac) for the device making this request."""
    ip = client_ip(request)
    mac = None
    driver = request.app.state.router
    if ip:
        try:
            mac = driver.lookup_mac(ip)
        except Exception:
            mac = None
    return ip, mac


# --------------------------------------------------------------------------- #
# Redirect helpers carrying a message
# --------------------------------------------------------------------------- #
def flash(url: str, msg: str = "", err: str = "") -> RedirectResponse:
    params = []
    if msg:
        params.append(f"msg={quote(msg)}")
    if err:
        params.append(f"err={quote(err)}")
    if params:
        url = f"{url}{'&' if '?' in url else '?'}{'&'.join(params)}"
    return RedirectResponse(url, status_code=303)
