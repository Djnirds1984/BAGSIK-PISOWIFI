"""Development entrypoint.

    python run.py

Reads .env, starts uvicorn on 0.0.0.0:8080 by default so it runs without root
on Windows/macOS for a quick look at the portal and admin console. On the
appliance the systemd unit runs uvicorn directly on port 80 instead.
"""

from __future__ import annotations

import os

import uvicorn

from app.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    host = os.environ.get("BAGSIK_BIND", "0.0.0.0")
    port = int(os.environ.get("BAGSIK_PORT", "8080"))
    print(f"{settings.app_name} v{settings.version}")
    print(f"  portal : http://localhost:{port}/")
    print(f"  admin  : http://localhost:{port}/admin")
    print(f"  driver : router={settings.router_driver} coin={settings.coin_driver}")
    if settings.router_driver != "mock":
        print("  note   : the 'local' driver needs Linux, nftables and root")
    uvicorn.run("app.main:app", host=host, port=port, log_level="info")
