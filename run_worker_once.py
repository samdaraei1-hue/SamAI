"""One GitHub Actions pass: discover current comments, draft, and publish by policy."""
import os
import sys
from datetime import datetime, timedelta, timezone

from config import config
from database import db
from worker import worker


def main():
    if not config.database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("A PostgreSQL DATABASE_URL secret is required")

    settings = db.get_settings()
    if not settings.get("comment_cutoff_date"):
        db.save_settings({"comment_cutoff_date": config.comment_cutoff_date})

    operation = os.getenv("WORKER_OPERATION", "run").strip().lower()
    if operation == "stats":
        since = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        with db.connect() as conn:
            rows = conn.execute("SELECT status,COUNT(*) AS count FROM comments WHERE updated_at>=? GROUP BY status", (since,)).fetchall()
        counts = {row["status"]: row["count"] for row in rows}
        print("Recent comment statuses (last 60 minutes): " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
        return
    if operation == "stop":
        db.save_settings({"worker_enabled": False})
        print("Worker paused in Supabase settings.")
        return
    if operation == "start":
        db.save_settings({"worker_enabled": True, "global_pause": False})

    settings = db.get_settings()
    config.update(settings)
    if not settings.get("worker_enabled", True):
        print("Worker is paused; no comments were processed.")
        return
    if config.global_pause or config.emergency_stop:
        print("Global pause or emergency stop is enabled; no comments were processed.")
        return

    result = worker.cycle()
    print(f"Cycle complete: discovered {result['found']} media items; processed {result['processed']} comments.")


if __name__ == "__main__":
    main()
