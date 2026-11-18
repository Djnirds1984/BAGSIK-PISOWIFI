"""Captive portal detection endpoints.

Phones and laptops probe a fixed URL on connect and show a "Sign in to network"
sheet when the answer is not what they expect. Redirecting those probes to the
portal is what makes the portal appear by itself.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from ..config import get_settings

router = APIRouter(include_in_schema=False)

PROBE_PATHS = (
    "/generate_204",
    "/gen_204",
    "/hotspot-detect.html",
    "/library/test/success.html",
    "/connecttest.txt",
    "/redirect",
    "/captive-portal",
    "/portal-redirect",
)


def _target(request: Request) -> str:
    settings = get_settings()
    base = settings.portal_url.rstrip("/")
    return f"{base}/" if base else "/"


@router.get("/generate_204")
@router.get("/gen_204")
def android_probe(request: Request):
    return RedirectResponse(_target(request), status_code=302)


@router.get("/hotspot-detect.html")
@router.get("/library/test/success.html")
def apple_probe(request: Request):
    return RedirectResponse(_target(request), status_code=302)


@router.get("/connecttest.txt")
@router.get("/redirect")
@router.get("/captive-portal")
@router.get("/portal-redirect")
def windows_probe(request: Request):
    return RedirectResponse(_target(request), status_code=302)


@router.get("/ncsi.txt")
def ncsi(request: Request):
    return RedirectResponse(_target(request), status_code=302)


@router.get("/.well-known/captive-portal")
def well_known(request: Request):
    return JSONResponse({"captive": True, "portal": _target(request)})


@router.get("/favicon.ico")
def favicon():
    # A tiny transparent icon keeps portal pages free of 404 noise.
    icon = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return Response(content=icon, media_type="image/png")
