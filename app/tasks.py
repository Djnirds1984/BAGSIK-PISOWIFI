"""Background loops: coin intake and session enforcement.

Both run as asyncio tasks inside the FastAPI lifespan. The blocking work
(SQLite writes, nftables calls) is pushed to a thread so the portal stays
responsive on a single-core Pi.
"""

from __future__ import annotations

import asyncio
import logging
import queue

from .services import billing, sessions

log = logging.getLogger("bagsik.tasks")


async def coin_loop(app) -> None:
    acceptor = app.state.coin
    while True:
        try:
            event = acceptor.events.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.25)
            continue
        try:
            await asyncio.to_thread(
                billing.add_coin, event.centavos, event.source, event.pulses, ""
            )
            log.info("coin credited: %s centavos (%s)", event.centavos, event.source)
        except Exception:
            log.exception("failed to record coin event")


async def coin_flow_loop(app) -> None:
    """Settle armed coin transactions even if the customer walks away."""
    flow = app.state.coinflow
    while True:
        try:
            await asyncio.to_thread(flow.tick)
        except Exception:
            log.exception("coin transaction pass failed")
        await asyncio.sleep(0.4)


async def enforcement_loop(app) -> None:
    driver = app.state.router
    interval = app.state.settings.enforce_seconds
    while True:
        try:
            summary = await asyncio.to_thread(sessions.enforce, driver)
            if summary["expired"] or summary["capped"]:
                log.info(
                    "enforcement: %s expired, %s data-capped, %s live grants",
                    summary["expired"],
                    summary["capped"],
                    summary["grants"],
                )
        except Exception:
            log.exception("enforcement pass failed")
        await asyncio.sleep(interval)
