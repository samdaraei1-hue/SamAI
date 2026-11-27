"""CLI entry point; the dashboard and background worker are served by Uvicorn."""
import uvicorn

from config import config
from dashboard import app


if __name__ == "__main__":
    uvicorn.run("dashboard:app", host=config.host, port=config.port, reload=False)
