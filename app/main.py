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
from .hardware.coin import build_acceptor
from .netctl import build_driver
from .routers import admin, captive, portal
from .services import coinflow, sessions

log = logging.getLogger("bagsik")


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
