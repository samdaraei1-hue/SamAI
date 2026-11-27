from __future__ import annotations

import csv
import io
import json
import logging
import threading
import time
from pathlib import Path

from config import config
from database import db, normalize_timestamp
from instagram_api import InstagramAPI, InstagramError
from think_tank import QuotaReached, think_tank

log = logging.getLogger("samai")
try:
    HISTORICAL_TEST_IDS = {str(x) for x in json.loads((Path(__file__).parent / "historical_test_ids.json").read_text(encoding="utf-8"))}
except (OSError, ValueError, KeyError, TypeError):
    HISTORICAL_TEST_IDS = set()


class Worker:
    def __init__(self, api=None, tank=None, database=None):
        self.api, self.tank, self.db = api or InstagramAPI(), tank or think_tank, database or db
        self.thread = None
        self.stop_event = threading.Event()
        self.cycle_lock = threading.Lock()
        self.last_cycle = None
        self.last_error = None
        self.enabled = False

    def start(self):
        if self.thread and self.thread.is_alive():
            return False
        self.stop_event.clear(); self.enabled = True
        self.thread = threading.Thread(target=self.run, name="samai-worker", daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.enabled = False; self.stop_event.set()
        return True

    def run(self):
        while not self.stop_event.is_set():
            try:
                if not config.emergency_stop and not config.global_pause:
                    self.cycle()
            except Exception as exc:
                # Never persist/log credentials, request URLs, or raw provider errors.
                self.last_error = type(exc).__name__
                log.error("Worker cycle failed (%s)", type(exc).__name__)
            self.last_cycle = time.time()
            self.stop_event.wait(config.poll_seconds)

    def cycle(self, discover=True):
        settings = self._refresh_persistent_controls()
        if config.emergency_stop or config.global_pause:
            return {"found": 0, "processed": 0}
        if config.daemon_mode and not settings.get("worker_enabled", True):
            return {"found": 0, "processed": 0}
        if not self.cycle_lock.acquire(blocking=False):
            return {"found": 0, "processed": 0}
        try:
            self._recover_uncertain()
            found = 0
            if discover:
                for media in self.api.media():
                    settings = self._refresh_persistent_controls()
                    if config.emergency_stop or config.global_pause or self._background_stopping() or (config.daemon_mode and not settings.get("worker_enabled", True)):
                        break
                    self.db.save_media(media); found += 1
                    print(f"Scanning media item {found}...", flush=True)
                    comments_seen = 0
                    recent_comments = []
                    for comment in self.api.comments(str(media["id"])):
                        comments_seen += 1
                        # Refresh the remote stop/pause control periodically,
                        # rather than opening a new database connection for
                        # every comment during discovery.
                        if comments_seen == 1 or comments_seen % 25 == 0:
                            settings = self._refresh_persistent_controls()
                        if config.emergency_stop or config.global_pause or self._background_stopping() or (config.daemon_mode and not settings.get("worker_enabled", True)):
                            break
                        # Creator posts are never replied to; own comments are excluded.
                        if self.api.is_own(comment) or comment.get("hidden"):
                            continue
                        source_timestamp = normalize_timestamp(comment.get("timestamp"))
                        if not source_timestamp or source_timestamp < config.comment_cutoff_utc:
                            # Do not backfill old or undated comments into the generation queue.
                            existing = self.db.get_comment(str(comment["id"]))
                            if existing and existing["status"] in {"pending", "retry", "drafted", "processing"}:
                                self.db.save_comment(comment, media["id"])
                                self.db.update_comment(comment["id"], "skipped", decision="Comment is older than the configured start date or has no timestamp.", quality_flags=["before_cutoff_or_undated"])
                            continue
                        recent_comments.append(comment)
                    self.db.save_comments(recent_comments, media["id"])
                    for comment in recent_comments:
                        if str(comment["id"]) in HISTORICAL_TEST_IDS:
                            self.db.update_comment(comment["id"], "skipped", decision="Historical regression comment; excluded from automatic publishing.", quality_flags=["historical_test_data"])
                            continue
                        # Only top-level comments can be responded to through this endpoint.
                        if comment.get("parent_id"):
                            self.db.update_comment(comment["id"], "skipped", decision="Nested comment; reply endpoint supports top-level comments only.", quality_flags=["nested_comment"])
                            continue
                        creator_replies = self.api.creator_replies(comment["id"], existing=comment.get("replies"))
                        if creator_replies:
                            reply = creator_replies[0]
                            self.db.update_comment(comment["id"], "replied_elsewhere", reply=reply.get("text") or "", published_reply_id=reply.get("id"), decision="An existing creator reply was found.")
            pending = self.db.pending(config.max_replies_cycle)
            pending = [x for x in pending if x["attempts"] <= config.max_retries]
            batch_total = 0
            for batch in self._chunks(pending):
                settings = self._refresh_persistent_controls()
                if config.emergency_stop or config.global_pause or self._background_stopping():
                    break
                if config.daemon_mode and not settings.get("worker_enabled", True): break
                batch_total += len(batch)
                for item in batch:
                    self.db.update_comment(item["id"], "processing", attempts=item["attempts"] + 1, error=None)
                try:
                    results = self.tank.process(batch)
                except QuotaReached:
                    # Quota exhaustion is a wait condition, not a failed content attempt.
                    for item in batch: self.db.update_comment(item["id"], "retry", attempts=item["attempts"], error="Daily Gemini quota reached")
                    break
                except Exception as exc:
                    for item in batch:
                        status = "retry" if item["attempts"] < config.max_retries else "error"
                        self.db.update_comment(item["id"], status, error=type(exc).__name__)
                    continue
                by_id = {str(r["id"]): r for r in results}
                for item in batch:
                    settings = self._refresh_persistent_controls()
                    if config.daemon_mode and not settings.get("worker_enabled", True):
                        for remaining in batch[batch.index(item):]:
                            self.db.update_comment(remaining["id"], "retry", attempts=remaining["attempts"], error="Worker stopped before publication")
                        break
                    result = by_id[str(item["id"])]
                    decision = (result.get("summary") or "")[:500]
                    flags = result.get("flags") or []
                    reply = (result.get("reply") or "").strip()
                    action = result.get("action")
                    if action == "skip" or not reply:
                        self.db.update_comment(item["id"], "skipped", reply=reply, decision=decision, quality_flags=flags)
                    elif action == "retry":
                        self.db.update_comment(item["id"], "retry" if item["attempts"] < config.max_retries else "skipped", reply=reply, decision=decision, quality_flags=flags)
                    elif action == "publish":
                        self.db.update_comment(item["id"], "drafted", reply=reply, decision=decision, quality_flags=flags)
                        if config.auto_publish and not config.dry_run and not config.global_pause and not config.emergency_stop and not self._background_stopping():
                            self._publish(item, reply, decision, flags)
            self.db.cleanup()
            return {"found": found, "processed": batch_total}
        finally:
            self.cycle_lock.release()

    @staticmethod
    def _chunks(items):
        """Cap both comment count and estimated prompt size before tokenization."""
        chunks, current, chars = [], [], 0
        for item in items:
            size = min(1600, len(item.get("text") or "")) + min(1200, len(item.get("caption") or "")) + 160
            if current and (len(current) >= config.batch_size or chars + size > config.max_batch_chars):
                chunks.append(current); current, chars = [], 0
            current.append(item); chars += size
        if current: chunks.append(current)
        return chunks

    def _background_stopping(self):
        return threading.current_thread() is self.thread and self.stop_event.is_set()

    def _refresh_persistent_controls(self):
        if not config.daemon_mode: return {}
        values = self.db.get_settings()
        config.update(values)
        return values

    def _recover_uncertain(self):
        if not config.auto_publish or config.dry_run:
            return
        for item in self.db.uncertain():
            try:
                # The GET is the required idempotency check after a lost POST result.
                existing = self.api.creator_replies(item["id"])
                if existing:
                    self.db.update_comment(item["id"], "replied_elsewhere", reply=existing[0].get("text") or "", published_reply_id=existing[0].get("id"), decision="A creator reply was found while recovering uncertain publication.")
                elif item.get("reply"):
                    self.db.update_comment(item["id"], "drafted", decision="Instagram confirmed no creator reply; publication can safely be attempted again.")
                    self._publish(item, item["reply"], item.get("decision") or "", json.loads(item.get("quality_flags") or "[]"))
            except InstagramError:
                # Keep uncertain for the next cycle; never publish without a successful GET.
                continue

    def _publish(self, item, reply, decision, flags):
        if self._background_stopping() or config.global_pause or config.emergency_stop:
            self.db.update_comment(item["id"], "drafted", reply=reply, decision="Publication paused by operator control.", quality_flags=flags)
            return
        usage = self.db.usage_today()
        if usage["publications"] >= config.max_publications_day:
            self.db.update_comment(item["id"], "drafted", decision="Daily publication budget reached; held as draft.", quality_flags=flags)
            return
        existing = self.api.creator_replies(item["id"])
        if existing:
            self.db.update_comment(item["id"], "replied_elsewhere", reply=existing[0].get("text") or reply, published_reply_id=existing[0].get("id"), decision="A creator reply already exists; duplicate publication prevented.", quality_flags=flags)
            return
        # Persist intent before the irreversible call; restart recovery checks Instagram first.
        self.db.update_comment(item["id"], "publishing", reply=reply, decision=decision, quality_flags=flags)
        try:
            response = self.api.publish_reply(item["id"], reply)
            self.db.update_comment(item["id"], "published", published_reply_id=str(response.get("id", "")), reply=reply, decision=decision, quality_flags=flags, error=None)
        except InstagramError as exc:
            if exc.status is None or exc.transient:
                try:
                    if self.api.already_replied(item["id"], reply):
                        self.db.update_comment(item["id"], "published", published_reply_id="verified_after_uncertain_result", reply=reply, decision=decision, quality_flags=flags)
                        return
                except InstagramError:
                    self.db.update_comment(item["id"], "uncertain", reply=reply, decision="Could not verify an uncertain publication result; automatic retry disabled.", quality_flags=flags, error="uncertain_publication")
                    return
            self.db.update_comment(item["id"], "retry" if exc.transient else "error", reply=reply, decision=decision, quality_flags=flags, error=str(exc))

    def status(self):
        return {"running": bool(self.thread and self.thread.is_alive() and self.enabled), "last_cycle": self.last_cycle, "last_error": self.last_error}


worker = Worker()
