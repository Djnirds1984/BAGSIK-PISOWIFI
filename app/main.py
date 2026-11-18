"""BAGSIK PISOWIFI - application entrypoint.

Run with:  python -m uvicorn app.main:app --host 0.0.0.0 --port 80
Or:        python run.py
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from . import db, tasks
from .config import get_settings
from .hardware import pins as coin_pins
from .hardware.coin import build_acceptor
from .netctl import build_driver
from .routers import admin, captive, portal
from .services import coinflow, sessions

log = logging.getLogger("bagsik")


def _apply_saved_hardware(app: FastAPI) -> None:
    """Start the acceptor on the board/pin saved in the admin console.

    BAGSIK_COIN_DRIVER=mock stays the master switch (development, tests). For a
    real slot the admin page wins; BAGSIK_GPIO_PIN is only a fallback for a
    board we cannot identify, where the operator still wants a plain line.
    """
    settings = app.state.settings
    if settings.coin_driver == "mock":
        return
    from .services import catalog

    target = coin_pins.current_target(
        catalog.setting("coin_board", "auto"),
        catalog.setting("coin_pin", coin_pins.DEFAULT_PIN),
    )
    if target["driver"] == "nodemcu" and settings.coin_driver == "gpio":
        app.state.coin.apply("gpio", settings.gpio_pin)
        return
    app.state.coin.apply(target["driver"], target["backend_pin"])


def _apply_saved_network(app: FastAPI) -> None:
    """Enforce on the hotspot ports picked in Admin > Settings > Hotspot server.

    BAGSIK_LAN_IFACE / BAGSIK_WAN_IFACE stay as the fallback for a fresh box;
    once the console has a saved assignment it wins.
    """
    from .services import catalog

    hotspots = [name.strip() for name in catalog.setting("net_hotspot_ifaces", "").split(",") if name.strip()]
    if not hotspots:
        return
    address = catalog.setting("net_hotspot_ip", "").strip()
    app.state.router.set_interfaces(
        hotspots,
        catalog.setting("net_wan_iface", "").strip(),
        f"{address}/24" if address else "",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = app.state.settings
    driver = app.state.router

    try:
        driver.ensure_ready()
        log.info("router driver ready: %s", driver.name)
    except Exception:
        log.exception("router driver failed to initialise - sessions will not be enforced")

    app.state.coin.start()
    log.info("coin acceptor: %s", app.state.coin.health())

    try:
        await asyncio.to_thread(sessions.sync_router, driver)
    except Exception:
        log.exception("initial router sync failed")

    loops = [
        asyncio.create_task(tasks.coin_loop(app), name="bagsik-coin"),
        asyncio.create_task(tasks.coin_flow_loop(app), name="bagsik-coinflow"),
        asyncio.create_task(tasks.enforcement_loop(app), name="bagsik-enforce"),
    ]

    try:
        yield
    finally:
        for task in loops:
            task.cancel()
        await asyncio.gather(*loops, return_exceptions=True)
        app.state.coin.stop()
        log.info("%s stopped", settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    db.init_db(settings)

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.router = build_driver(settings)
    app.state.coin = build_acceptor(settings)
    app.state.coinflow = coinflow.CoinFlow(app.state.coin, app.state.router)
    try:
        _apply_saved_hardware(app)
    except Exception:
        log.exception("could not apply the saved coin hardware - using the env defaults")

    try:
        _apply_saved_network(app)
    except Exception:
        log.exception("could not apply the saved hotspot ports - using the env defaults")

    app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")
    app.include_router(captive.router)
    app.include_router(portal.router)
    app.include_router(admin.router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    return app


app = create_app()
