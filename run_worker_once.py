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
    config.update(settings)
    if not settings.get("comment_cutoff_date"):
        db.save_settings({"comment_cutoff_date": config.comment_cutoff_date})

    operation = os.getenv("WORKER_OPERATION", "run").strip().lower()
    if operation == "stats":
        since = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        with db.connect() as conn:
            rows = conn.execute("SELECT status,COUNT(*) AS count FROM comments WHERE updated_at>=? GROUP BY status", (since,)).fetchall()
            failures = conn.execute("SELECT error,COUNT(*) AS count FROM comments WHERE updated_at>=? AND error IS NOT NULL GROUP BY error", (since,)).fetchall()
            today = conn.execute("SELECT status,COUNT(*) AS count FROM comments WHERE source_timestamp>=? GROUP BY status", (config.comment_cutoff_utc,)).fetchall()
        counts = {row["status"]: row["count"] for row in rows}
        today_counts = {row["status"]: row["count"] for row in today}
        usage = db.usage_today()
        print("Recent comment statuses (last 60 minutes): " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
        print("Today's comments by status: " + ", ".join(f"{key}={value}" for key, value in sorted(today_counts.items())))
        if failures:
            print("Recent failure reasons: " + ", ".join(f"{row['error']}={row['count']}" for row in failures))
        print("Publishing controls: " + ", ".join(f"{key}={settings.get(key, default)}" for key, default in (("auto_publish", config.auto_publish), ("dry_run", config.dry_run), ("max_publications_day", config.max_publications_day))))
        print(f"Gemini usage today: requests={usage['requests']}/{settings.get('max_requests_day', config.max_requests_day)}, tokens={usage['tokens']}/{settings.get('max_tokens_day', config.max_tokens_day)}")
        print(f"Publications today: {usage['publications']}")
        return
    if operation == "stop":
        # Also stop any legacy Vercel daemon still connected to this database.
        db.save_settings({"worker_enabled": False, "global_pause": True})
        print("Worker paused in Supabase settings.")
        return
    if operation == "start":
        db.save_settings({
            # GitHub Actions is the sole worker; leave the legacy daemon off.
            "worker_enabled": False, "global_pause": False,
            "auto_publish": True, "dry_run": False,
            "max_replies_cycle": 100, "max_publications_day": 200,
            "max_tokens_day": 300000,
        })

    settings = db.get_settings()
    config.update(settings)
    if config.global_pause or config.emergency_stop:
        print("Global pause or emergency stop is enabled; no comments were processed.")
        return

    # Start processes the queue already stored in Supabase immediately; the
    # scheduled run will discover any newer comments on its next pass.
    result = worker.cycle(discover=operation != "start")
    print(f"Cycle complete: discovered {result['found']} media items; processed {result['processed']} comments.")


if __name__ == "__main__":
    main()
