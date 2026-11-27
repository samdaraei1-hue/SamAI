from __future__ import annotations

import json
import os
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    def __init__(self) -> None:
        self.instagram_token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
        self.instagram_user_id = os.getenv("INSTAGRAM_USER_ID", "").strip()
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.database_url = os.getenv("DATABASE_URL", "").strip()
        self.graph_base = os.getenv("INSTAGRAM_GRAPH_BASE", "https://graph.facebook.com/v27.0").rstrip("/")
        self.model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self.db_path = ROOT / os.getenv("SAMAI_DB", "samai.sqlite3")
        self.poll_seconds = _int("POLL_SECONDS", 300, 30)
        self.batch_size = _int("BATCH_SIZE", 20, 1)
        self.max_batch_chars = _int("MAX_BATCH_CHARS", 24000, 1000)
        self.max_replies_cycle = _int("MAX_REPLIES_PER_CYCLE", 10, 1)
        self.max_requests_day = _int("MAX_GEMINI_REQUESTS_DAY", 100, 0)
        self.max_tokens_day = _int("MAX_GEMINI_TOKENS_DAY", 100000, 0)
        self.max_publications_day = _int("MAX_PUBLICATIONS_DAY", 20, 0)
        self.max_retries = _int("MAX_RETRIES", 3, 0)
        self.max_media = _int("MAX_MEDIA_PER_CYCLE", 50, 1)
        self.max_pages = _int("MAX_PAGES_PER_EDGE", 20, 1)
        self.retention_days = _int("RETENTION_DAYS", 180, 1)
        self.comment_cutoff_date = os.getenv("COMMENT_CUTOFF_DATE", "").strip() or datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
        self.dry_run = _bool("DRY_RUN", True)
        # Both switches must be deliberately enabled before anything is published.
        self.auto_publish = _bool("AUTO_PUBLISH", False)
        self.global_pause = _bool("GLOBAL_PAUSE", False)
        self.emergency_stop = _bool("EMERGENCY_STOP", False)
        self.host = "127.0.0.1"
        self.port = _int("DASHBOARD_PORT", 8000, 1)
        self.daemon_mode = _bool("SAMAI_DAEMON_MODE", False)

    def public(self) -> dict:
        return {k: v for k, v in {
            "poll_seconds": self.poll_seconds, "batch_size": self.batch_size,
            "max_replies_cycle": self.max_replies_cycle, "max_requests_day": self.max_requests_day,
            "max_tokens_day": self.max_tokens_day, "max_publications_day": self.max_publications_day,
            "retention_days": self.retention_days, "dry_run": self.dry_run,
            "auto_publish": self.auto_publish, "global_pause": self.global_pause,
            "emergency_stop": self.emergency_stop, "model": self.model,
            "comment_cutoff_date": self.comment_cutoff_date,
        }.items()}

    @property
    def comment_cutoff_utc(self) -> str:
        local_midnight = datetime.combine(datetime.fromisoformat(self.comment_cutoff_date).date(), time.min, ZoneInfo("Europe/Berlin"))
        return local_midnight.astimezone(timezone.utc).isoformat()

    def update(self, values: dict) -> None:
        allowed = {"poll_seconds", "batch_size", "max_replies_cycle", "max_requests_day",
                   "max_tokens_day", "max_publications_day", "retention_days", "dry_run",
                   "auto_publish", "global_pause", "emergency_stop"}
        for key, value in values.items():
            if key == "comment_cutoff_date":
                if not isinstance(value, str):
                    raise ValueError("comment_cutoff_date must be YYYY-MM-DD")
                try:
                    datetime.fromisoformat(value).date()
                except ValueError:
                    raise ValueError("comment_cutoff_date must be YYYY-MM-DD") from None
                self.comment_cutoff_date = value
                continue
            if key not in allowed:
                continue
            if isinstance(getattr(self, key), bool):
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be boolean")
            elif not isinstance(value, int) or value < (30 if key == "poll_seconds" else 1):
                raise ValueError(f"invalid {key}")
            setattr(self, key, value)


config = Config()
