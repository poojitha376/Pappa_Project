"""Entry point: start the Sensibull feed + sampling scheduler, then serve the dashboard.

    python run.py              # normal: samples at the 27 IST times
    python run.py --simulate   # compressed run for testing (all 27 rows, ~2s apart)

Local-first. To move to an always-on server later, run the same command there with the
timezone left as Asia/Kolkata.
"""
from __future__ import annotations

import sys

import uvicorn

from core import service


def main() -> None:
    simulate = "--simulate" in sys.argv
    svc = service.Service(simulate=simulate)
    service.SERVICE = svc
    svc.start()

    import app as app_module

    cfg = svc.cfg
    print(f"dashboard: http://127.0.0.1:{cfg.get('port', 8000)}", flush=True)
    try:
        uvicorn.run(app_module.app, host="0.0.0.0",
                    port=int(cfg.get("port", 8000)), log_level="warning")
    finally:
        svc.stop()


if __name__ == "__main__":
    main()
