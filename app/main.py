"""Entry point: bootstrap state, run scheduler + FastAPI in one process."""

from __future__ import annotations

import logging
import sys
import threading

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import Settings, bootstrap_settings, load_settings, runtime
from .scanner import Scanner
from .state import State
from .ta_client import TAClient
from .web import build_app


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def main() -> int:
    rt = runtime()
    configure_logging(rt["log_level"])
    log = logging.getLogger("ta-subscriber")

    state = State(rt["state_dir"])
    bootstrap_settings(state)
    log.info("state dir: %s", rt["state_dir"])

    settings_lock = threading.Lock()

    def settings_provider() -> Settings:
        with settings_lock:
            return load_settings(state)

    ta_lock = threading.Lock()
    ta_state: dict = {"client": None, "url": None, "token": None, "verify": None}

    def ta_provider() -> TAClient:
        s = settings_provider()
        with ta_lock:
            client = ta_state["client"]
            if (
                client is None
                or ta_state["url"] != s.ta_url
                or ta_state["token"] != s.ta_token
                or ta_state.get("verify") != s.verify_ssl
            ):
                if client is not None:
                    client.close()
                client = TAClient(s.ta_url, s.ta_token, verify=s.verify_ssl)
                ta_state["client"] = client
                ta_state["url"] = s.ta_url
                ta_state["token"] = s.ta_token
                ta_state["verify"] = s.verify_ssl
            return client

    scanner = Scanner(
        settings_provider=settings_provider,
        state=state,
        ta_provider=ta_provider,
    )

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.start()
    current_interval = {"minutes": 0}

    def ensure_job() -> None:
        s = settings_provider()
        minutes = s.scan_interval_minutes
        if minutes == current_interval["minutes"]:
            return
        existing = scheduler.get_job("scan")
        if existing:
            existing.reschedule(trigger=IntervalTrigger(minutes=minutes))
        else:
            scheduler.add_job(
                scanner.scan_once,
                trigger=IntervalTrigger(minutes=minutes),
                id="scan",
                max_instances=1,
                coalesce=True,
            )
        current_interval["minutes"] = minutes
        log.info("scheduler interval set to %d minutes", minutes)

    ensure_job()

    def trigger_scan() -> dict:
        return scanner.scan_once()

    def reschedule() -> None:
        ensure_job()

    def next_scan_provider() -> int | None:
        job = scheduler.get_job("scan")
        if not job or not job.next_run_time:
            return None
        return int(job.next_run_time.timestamp())

    app = build_app(
        state=state,
        settings_provider=settings_provider,
        ta_provider=ta_provider,
        trigger_scan=trigger_scan,
        reschedule=reschedule,
        next_scan_provider=next_scan_provider,
    )

    initial = settings_provider()
    if initial.is_ready():
        log.info("initial settings present, kicking first scan in background")
        threading.Thread(target=trigger_scan, daemon=True).start()
    else:
        log.warning("setup required — open the web UI to configure TA URL and token")

    host = rt["host"]
    port = int(rt["port"])
    log.info("web UI on http://%s:%d", host, port)

    config = uvicorn.Config(app, host=host, port=port, log_level=rt["log_level"].lower())
    server = uvicorn.Server(config)
    try:
        server.run()
    finally:
        scheduler.shutdown(wait=False)
        with ta_lock:
            if ta_state["client"]:
                ta_state["client"].close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
