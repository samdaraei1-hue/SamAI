from __future__ import annotations

import json
import ipaddress
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from config import config

_DNS_CACHE = {}
_DNS_LOCK = threading.Lock()


def _resolve_ipv4_doh(host):
    """Resolve a public DB hostname over HTTPS for Vercel's isolated Python runtime.

    Only the hostname is sent to Cloudflare's public DNS endpoint. Database
    credentials remain in the PostgreSQL TLS connection and are never sent to DNS.
    """
    now_ts = time.monotonic()
    with _DNS_LOCK:
        cached = _DNS_CACHE.get(host)
        if cached and cached[0] > now_ts:
            return cached[1]
    import requests
    addresses = []
    ttl = 60
    names, queried = [host], set()
    while names and len(queried) < 5 and not addresses:
        name = names.pop(0).rstrip(".")
        if name in queried:
            continue
        queried.add(name)
        resolvers = (
            ("https://cloudflare-dns.com/dns-query", {"accept": "application/dns-json"}),
            ("https://dns.google/resolve", {"accept": "application/dns-json"}),
        )
        for endpoint, headers in resolvers:
            try:
                response = requests.get(endpoint, params={"name": name, "type": "A"}, headers=headers, timeout=5)
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError):
                continue
            for answer in payload.get("Answer", []):
                try:
                    answer_type = int(answer.get("type", 0))
                except (TypeError, ValueError):
                    continue
                ttl = min(ttl, int(answer.get("TTL", ttl)))
                if answer_type == 1:
                    try:
                        address = str(ipaddress.IPv4Address(answer.get("data", "")))
                    except ipaddress.AddressValueError:
                        continue
                    if address not in addresses:
                        addresses.append(address)
                elif answer_type == 5:
                    target = str(answer.get("data", "")).rstrip(".")
                    if target and target not in queried:
                        names.append(target)
            if addresses:
                break
    if not addresses:
        raise RuntimeError("Could not resolve the database host")
    with _DNS_LOCK:
        _DNS_CACHE[host] = (time.monotonic() + max(10, min(ttl, 60)), addresses)
    return addresses


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_timestamp(value):
    if not value: return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


class Database:
    def __init__(self, path=None):
        self.path = str(path or config.database_url or config.db_path)
        self.is_postgres = self.path.startswith(("postgres://", "postgresql://"))
        if config.daemon_mode and not self.is_postgres:
            raise RuntimeError("Vercel Daemon mode requires a persistent PostgreSQL DATABASE_URL")
        self.lock = threading.RLock()
        self.init()

    class PostgresConnection:
        def __init__(self, url):
            import psycopg
            from psycopg.rows import dict_row
            from psycopg.conninfo import conninfo_to_dict
            options = conninfo_to_dict(url)
            if config.daemon_mode and os.getenv("VERCEL") and options.get("host"):
                last_error = None
                options.setdefault("sslmode", "require")
                for address in _resolve_ipv4_doh(options["host"]):
                    try:
                        self.raw = psycopg.connect(**{**options, "hostaddr": address}, row_factory=dict_row, connect_timeout=15, prepare_threshold=None)
                        break
                    except psycopg.OperationalError as exc:
                        last_error = exc
                else:
                    raise last_error or RuntimeError("Could not connect to the database")
            else:
                # Supabase's transaction pooler may reuse server connections;
                # disabling named prepared statements avoids name collisions.
                self.raw = psycopg.connect(url, row_factory=dict_row, connect_timeout=15, prepare_threshold=None)
        @staticmethod
        def _sql(query): return query.replace("?", "%s")
        def execute(self, query, params=()): return self.raw.execute(self._sql(query), params)
        def executemany(self, query, params): return self.raw.cursor().executemany(self._sql(query), params)
        def executescript(self, script):
            for statement in script.split(";"):
                if statement.strip(): self.raw.execute(statement)
        def commit(self): self.raw.commit()
        def rollback(self): self.raw.rollback()
        def close(self): self.raw.close()

    @contextmanager
    def connect(self):
        if self.is_postgres:
            db = self.PostgresConnection(self.path)
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
            return
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def init(self):
        with self.connect() as db:
            if not self.is_postgres:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA foreign_keys=ON")
            schema = """
            CREATE TABLE IF NOT EXISTS media(id TEXT PRIMARY KEY, caption TEXT NOT NULL DEFAULT '', media_type TEXT, timestamp TEXT, discovered_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS comments(id TEXT PRIMARY KEY, media_id TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', reply TEXT, published_reply_id TEXT, decision TEXT, quality_flags TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT, attempts INTEGER NOT NULL DEFAULT 0, parent_comment_id TEXT, thread_context TEXT NOT NULL DEFAULT '', FOREIGN KEY(media_id) REFERENCES media(id));
            CREATE INDEX IF NOT EXISTS comments_status_idx ON comments(status, updated_at);
            CREATE TABLE IF NOT EXISTS usage(id {identity}, stage TEXT NOT NULL, requests INTEGER NOT NULL DEFAULT 1, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, error TEXT);
            CREATE TABLE IF NOT EXISTS corrections(id {identity}, comment_id TEXT NOT NULL, correction TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """.format(identity="BIGSERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY")
            db.executescript(schema)
            if self.is_postgres:
                db.execute("ALTER TABLE comments ADD COLUMN IF NOT EXISTS source_timestamp TEXT")
                db.execute("ALTER TABLE comments ADD COLUMN IF NOT EXISTS parent_comment_id TEXT")
                db.execute("ALTER TABLE comments ADD COLUMN IF NOT EXISTS thread_context TEXT NOT NULL DEFAULT ''")
            else:
                columns = {r["name"] for r in db.execute("PRAGMA table_info(comments)")}
                if "source_timestamp" not in columns:
                    db.execute("ALTER TABLE comments ADD COLUMN source_timestamp TEXT")
                if "parent_comment_id" not in columns:
                    db.execute("ALTER TABLE comments ADD COLUMN parent_comment_id TEXT")
                if "thread_context" not in columns:
                    db.execute("ALTER TABLE comments ADD COLUMN thread_context TEXT NOT NULL DEFAULT ''")
            # Recover interrupted work conservatively: drafts can be regenerated; an
            # in-flight publish must be checked on Instagram before any retry.
            db.execute("UPDATE comments SET status='retry',error='recovered_after_restart' WHERE status='processing'")
            db.execute("UPDATE comments SET status='uncertain',error='recovered_after_restart' WHERE status='publishing'")

    def save_media(self, item):
        with self.connect() as db:
            db.execute("INSERT INTO media VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET caption=excluded.caption,media_type=excluded.media_type,timestamp=excluded.timestamp", (str(item["id"]), item.get("caption") or "", item.get("media_type"), item.get("timestamp"), now()))

    def save_comment(self, item, media_id):
        # Usernames and profile identifiers are intentionally not persisted.
        self.save_comments([item], media_id)

    def save_comments(self, items, media_id):
        # Keep one PostgreSQL connection/transaction per media batch rather
        # than opening a new connection for every Instagram comment.
        if not items:
            return
        created = now()
        with self.connect() as db:
            db.executemany(
                "INSERT INTO comments(id,media_id,text,status,created_at,updated_at,source_timestamp,parent_comment_id,thread_context) VALUES(?,?,?,'pending',?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET source_timestamp=COALESCE(excluded.source_timestamp,comments.source_timestamp),parent_comment_id=COALESCE(excluded.parent_comment_id,comments.parent_comment_id),thread_context=CASE WHEN excluded.thread_context<>'' THEN excluded.thread_context ELSE comments.thread_context END,status=CASE WHEN comments.status='skipped' AND comments.quality_flags LIKE '%%nested_comment%%' AND excluded.parent_comment_id IS NOT NULL THEN 'pending' ELSE comments.status END,decision=CASE WHEN comments.status='skipped' AND comments.quality_flags LIKE '%%nested_comment%%' AND excluded.parent_comment_id IS NOT NULL THEN NULL ELSE comments.decision END,quality_flags=CASE WHEN comments.status='skipped' AND comments.quality_flags LIKE '%%nested_comment%%' AND excluded.parent_comment_id IS NOT NULL THEN '[]' ELSE comments.quality_flags END,reply=CASE WHEN comments.status='skipped' AND comments.quality_flags LIKE '%%nested_comment%%' AND excluded.parent_comment_id IS NOT NULL THEN NULL ELSE comments.reply END,error=CASE WHEN comments.status='skipped' AND comments.quality_flags LIKE '%%nested_comment%%' AND excluded.parent_comment_id IS NOT NULL THEN NULL ELSE comments.error END,updated_at=CASE WHEN comments.status='skipped' AND comments.quality_flags LIKE '%%nested_comment%%' AND excluded.parent_comment_id IS NOT NULL THEN excluded.updated_at ELSE comments.updated_at END",
                [(str(item["id"]), str(media_id), item.get("text") or "", created, created, normalize_timestamp(item.get("timestamp")), str(item.get("parent_id")) if item.get("parent_id") else None, (item.get("thread_context") or "")[:4000]) for item in items],
            )

    def get_comment(self, comment_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM comments WHERE id=?", (str(comment_id),)).fetchone()
            return dict(row) if row else None

    def pending(self, limit=100):
        with self.connect() as db:
            from config import config
            return [dict(r) for r in db.execute("SELECT c.*,m.caption FROM comments c JOIN media m ON m.id=c.media_id WHERE c.status IN ('pending','retry') AND c.source_timestamp>=? ORDER BY c.source_timestamp,c.created_at LIMIT ?", (config.comment_cutoff_utc, limit))]

    def drafted(self, limit=100):
        with self.connect() as db:
            from config import config
            return [dict(r) for r in db.execute("SELECT c.*,m.caption FROM comments c JOIN media m ON m.id=c.media_id WHERE c.status='drafted' AND c.reply IS NOT NULL AND c.source_timestamp>=? ORDER BY c.source_timestamp,c.created_at LIMIT ?", (config.comment_cutoff_utc, limit))]

    def uncertain(self, limit=50):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT c.*,m.caption FROM comments c JOIN media m ON m.id=c.media_id WHERE c.status='uncertain' ORDER BY c.updated_at LIMIT ?", (limit,))]

    def update_comment(self, comment_id, status, **fields):
        allowed = {"reply", "published_reply_id", "decision", "quality_flags", "error", "attempts"}
        fields = {k: (json.dumps(v, ensure_ascii=False) if k == "quality_flags" and not isinstance(v, str) else v) for k, v in fields.items() if k in allowed}
        fields.update(status=status, updated_at=now())
        sql = ",".join(f"{k}=?" for k in fields)
        with self.connect() as db:
            db.execute(f"UPDATE comments SET {sql} WHERE id=?", (*fields.values(), str(comment_id)))

    def log_usage(self, stage, input_tokens=0, output_tokens=0, error=None, requests=1):
        with self.connect() as db:
            db.execute("INSERT INTO usage(stage,requests,input_tokens,output_tokens,created_at,error) VALUES(?,?,?,?,?,?)", (stage, requests, input_tokens or 0, output_tokens or 0, now(), str(error)[:500] if error else None))

    def usage_today(self):
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with self.connect() as db:
            row = db.execute("SELECT COALESCE(SUM(requests),0) requests,COALESCE(SUM(input_tokens+output_tokens),0) tokens FROM usage WHERE created_at>=?", (start,)).fetchone()
            pubs = db.execute("SELECT COUNT(*) n FROM comments WHERE status='published' AND updated_at>=?", (start,)).fetchone()["n"]
        return {"requests": row["requests"], "tokens": row["tokens"], "publications": pubs}

    def dashboard_data(self, status=None, query="", limit=300):
        from config import config
        where, params = ["c.source_timestamp>=?"], [config.comment_cutoff_utc]
        if status:
            where.append("c.status=?"); params.append(status)
        if query:
            where.append("(c.text LIKE ? OR c.reply LIKE ? OR m.caption LIKE ?)"); params.extend([f"%{query}%"] * 3)
        clause = " WHERE " + " AND ".join(where) if where else ""
        with self.connect() as db:
            comments = [dict(r) for r in db.execute(f"SELECT c.*,m.caption FROM comments c JOIN media m ON m.id=c.media_id{clause} ORDER BY c.updated_at DESC LIMIT ?", (*params, limit))]
            media = [dict(r) for r in db.execute("SELECT * FROM media ORDER BY discovered_at DESC LIMIT 100")]
            errors = [dict(r) for r in db.execute("SELECT * FROM usage WHERE error IS NOT NULL ORDER BY created_at DESC LIMIT 50")]
            usage = db.execute("SELECT COALESCE(SUM(requests),0) requests,COALESCE(SUM(input_tokens),0) input_tokens,COALESCE(SUM(output_tokens),0) output_tokens FROM usage").fetchone()
        return {"comments": comments, "media": media, "errors": [dict(x, error="Request failed; details redacted") for x in errors], "usage": dict(usage)}

    def add_correction(self, comment_id, correction):
        with self.connect() as db:
            db.execute("INSERT INTO corrections(comment_id,correction,created_at) VALUES(?,?,?)", (comment_id, correction[:2000], now()))

    def examples(self, limit=5):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT c.text,c.reply,x.correction FROM comments c JOIN corrections x ON x.comment_id=c.id ORDER BY x.created_at DESC LIMIT ?", (limit,))]

    def get_settings(self):
        with self.connect() as db:
            return {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT key,value FROM settings")}

    def save_settings(self, values):
        with self.connect() as db:
            db.executemany("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [(k, json.dumps(v)) for k, v in values.items()])

    def cleanup(self, days=None):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days or config.retention_days)).isoformat()
        with self.connect() as db:
            db.execute("DELETE FROM usage WHERE created_at<?", (cutoff,))
            db.execute("DELETE FROM corrections WHERE created_at<?", (cutoff,))
            db.execute("DELETE FROM comments WHERE updated_at<?", (cutoff,))
            db.execute("DELETE FROM media WHERE discovered_at<? AND id NOT IN (SELECT media_id FROM comments)", (cutoff,))


db = Database()
