"""Vercel Daemon entry point; state and operator controls live in PostgreSQL."""
import logging
import signal
import threading

from config import config
from database import db
from worker import worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
shutdown = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: shutdown.set())
signal.signal(signal.SIGINT, lambda *_: shutdown.set())


def main():
    if not config.database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("Set DATABASE_URL to a persistent PostgreSQL database for Vercel Daemon mode")
    settings = db.get_settings()
    if "comment_cutoff_date" not in settings:
        settings["comment_cutoff_date"] = config.comment_cutoff_date
        db.save_settings({"comment_cutoff_date": config.comment_cutoff_date})
    while not shutdown.is_set():
        try:
            settings = db.get_settings()
            config.update(settings)
            if settings.get("worker_enabled", True) and not config.global_pause and not config.emergency_stop:
                worker.cycle()
                worker.last_cycle = __import__("time").time()
        except Exception as exc:
            # Never log raw provider errors, request details, or secrets.
            logging.getLogger("samai.daemon").error("Worker iteration failed (%s)", type(exc).__name__)
        shutdown.wait(config.poll_seconds)


if __name__ == "__main__":
    main()
