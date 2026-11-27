from __future__ import annotations

import csv
import io
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from config import ROOT, config
from database import db
from worker import worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = FastAPI(title="SamAI", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

saved = db.get_settings()
if saved:
    try: config.update(saved)
    except ValueError: pass
if saved.get("comment_cutoff_date"):
    config.comment_cutoff_date = saved["comment_cutoff_date"]
else:
    db.save_settings({"comment_cutoff_date": config.comment_cutoff_date})


@app.get("/", response_class=HTMLResponse)
def home():
    return (ROOT / "templates" / "index.html").read_text(encoding="utf-8")


@app.get("/api/status")
def status():
    worker_status = worker.status()
    if config.daemon_mode:
        worker_status = {"running": bool(db.get_settings().get("worker_enabled", True)), "last_cycle": worker.last_cycle, "last_error": worker.last_error}
    return {"worker": worker_status, "settings": config.public(), "usage_today": db.usage_today(), "ready": bool(config.instagram_token and config.instagram_user_id and config.gemini_api_key)}


@app.get("/api/history")
def history(status: str | None = None, q: str = Query(default="", max_length=200)):
    return db.dashboard_data(status, q)


@app.post("/api/worker/start")
def start():
    if config.emergency_stop: raise HTTPException(409, "Emergency stop is enabled")
    if config.daemon_mode:
        db.save_settings({"worker_enabled": True, "global_pause": False})
        return {"started": True}
    return {"started": worker.start()}


@app.post("/api/worker/stop")
def stop():
    if config.daemon_mode:
        db.save_settings({"worker_enabled": False})
        return {"stopped": True}
    worker.stop(); return {"stopped": True}


@app.post("/api/worker/cycle")
def cycle():
    try: return worker.cycle()
    except Exception as exc: raise HTTPException(502, type(exc).__name__) from None


@app.put("/api/settings")
def settings(values: dict):
    try: config.update(values)
    except ValueError as exc: raise HTTPException(400, str(exc)) from None
    public = config.public(); db.save_settings(public)
    return public


@app.put("/api/personality")
def personality(data: dict):
    value = data.get("text")
    if not isinstance(value, str) or not value.strip() or len(value) > 10000: raise HTTPException(400, "متن شخصیت نامعتبر است")
    (ROOT / "prompts" / "personality.txt").write_text(value, encoding="utf-8")
    return {"saved": True}


@app.put("/api/think-tank-prompt")
def prompt(data: dict):
    value = data.get("text")
    if not isinstance(value, str) or not value.strip() or len(value) > 10000: raise HTTPException(400, "متن دستورالعمل نامعتبر است")
    (ROOT / "prompts" / "think_tank.txt").write_text(value, encoding="utf-8")
    return {"saved": True}


@app.get("/api/prompts")
def prompts():
    return {"personality": (ROOT / "prompts" / "personality.txt").read_text(encoding="utf-8"), "think_tank": (ROOT / "prompts" / "think_tank.txt").read_text(encoding="utf-8")}


@app.post("/api/corrections")
def correction(data: dict):
    comment_id, text = data.get("comment_id"), data.get("correction")
    if not isinstance(comment_id, str) or not isinstance(text, str) or not text.strip(): raise HTTPException(400, "دادهٔ اصلاح نامعتبر است")
    if not db.get_comment(comment_id): raise HTTPException(404, "کامنت پیدا نشد")
    db.add_correction(comment_id, text)
    return {"saved": True}


@app.get("/api/export.csv")
def export_csv():
    data = db.dashboard_data(limit=10000)["comments"]
    stream = io.StringIO(); writer = csv.DictWriter(stream, fieldnames=["id", "media_id", "text", "status", "reply", "published_reply_id", "decision", "quality_flags", "created_at", "updated_at"], extrasaction="ignore")
    writer.writeheader()
    for row in data: writer.writerow(row)
    return Response(stream.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=samai-history.csv"})
