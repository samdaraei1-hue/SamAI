"""Install Supabase Cron as the clock that dispatches the GitHub worker."""
from __future__ import annotations

import os

import psycopg
import requests

from config import config


OWNER = "samdaraei1-hue"
REPOSITORY = "SamAI"
WORKFLOW = "samai-worker.yml"
SECRET_NAME = "samai_github_dispatch_token"
JOB_NAME = "samai-github-worker"
DISPATCH_URL = (
    f"https://api.github.com/repos/{OWNER}/{REPOSITORY}"
    f"/actions/workflows/{WORKFLOW}/dispatches"
)


def main() -> None:
    token = os.getenv("SAMAI_GITHUB_DISPATCH_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Add the repository secret SAMAI_GITHUB_DISPATCH_TOKEN first")
    if not config.database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("A Supabase PostgreSQL DATABASE_URL secret is required")

    # Validate the token before changing the database. The stats operation is
    # read-only and does not publish comments.
    response = requests.post(
        DISPATCH_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"ref": "main", "inputs": {"operation": "stats"}},
        timeout=20,
    )
    if response.status_code != 204:
        raise RuntimeError(
            "GitHub rejected the read-only dispatch health check "
            f"(HTTP {response.status_code}); check Actions: read/write on SamAI"
        )

    with psycopg.connect(config.database_url) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS extensions")
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_cron WITH SCHEMA pg_catalog")
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions")
        conn.execute("CREATE SCHEMA IF NOT EXISTS vault")
        conn.execute("CREATE EXTENSION IF NOT EXISTS supabase_vault WITH SCHEMA vault")

        existing = conn.execute(
            "SELECT id FROM vault.secrets WHERE name=%s", (SECRET_NAME,)
        ).fetchone()
        description = "Fine-grained token used only to dispatch SamAI's GitHub worker."
        if existing:
            conn.execute(
                "SELECT vault.update_secret(%s,%s,%s,%s)",
                (existing[0], token, SECRET_NAME, description),
            )
        else:
            conn.execute(
                "SELECT vault.create_secret(%s,%s,%s)",
                (token, SECRET_NAME, description),
            )

        command = f"""
            SELECT net.http_post(
                url := '{DISPATCH_URL}',
                headers := jsonb_build_object(
                    'Accept', 'application/vnd.github+json',
                    'Authorization', 'Bearer ' || (
                        SELECT decrypted_secret
                        FROM vault.decrypted_secrets
                        WHERE name = '{SECRET_NAME}'
                    ),
                    'X-GitHub-Api-Version', '2022-11-28',
                    'Content-Type', 'application/json'
                ),
                body := jsonb_build_object(
                    'ref', 'main',
                    'inputs', jsonb_build_object('operation', 'scheduled')
                ),
                timeout_milliseconds := 10000
            );
        """
        job_id = conn.execute(
            "SELECT cron.schedule(%s,%s,%s)",
            (JOB_NAME, "*/20 * * * *", command),
        ).fetchone()[0]

    print(
        f"Supabase Cron job {JOB_NAME} installed (job id {job_id}), "
        "scheduled every 20 minutes. Read-only GitHub dispatch health check accepted (HTTP 204)."
    )


if __name__ == "__main__":
    main()
