# Supabase + Vercel setup

This app supports PostgreSQL through `DATABASE_URL`. Use the Supabase **Session pooler** connection string and require TLS (`sslmode=require`). Do not put the database password in this file or commit it.

## Supabase

1. Create a Supabase project and save its database password securely.
2. In **Project Settings → Database → Connect**, select **Session pooler** and copy the URI. Replace its password placeholder in the Supabase dashboard only; do not send the URI in chat or commit it.
3. Keep the database in the same broad region as the Vercel deployment when possible.

The application creates its tables automatically on first launch. It stores a persistent comment cutoff date in the database; new projects start from the current date in `Europe/Berlin`. Comments with no valid timestamp or earlier timestamps are excluded.

## Vercel environment variables

Add these variables under the Vercel project's **Settings → Environment Variables** for both Production and Preview only if Preview is intended to access the real account:

- `DATABASE_URL`: Supabase Session pooler URI, including `sslmode=require`.
- `SAMAI_DAEMON_MODE`: `true` for the API and daemon services.
- `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID`, `GEMINI_API_KEY`: add each secret directly in Vercel; never commit or paste them into chat.
- `DRY_RUN`: keep `true` until the dashboard has been checked.
- `AUTO_PUBLISH`: keep `false` until explicitly enabling live replies in the dashboard.

Deploy this folder with the Vercel CLI after linking it to the intended Vercel project. The daemon polls every five minutes by default. The dashboard's stop control writes `worker_enabled=false` to Supabase; its start control resumes it.

## Availability

Supabase Free is a persistent PostgreSQL database, but free projects can be paused after a week of inactivity. Vercel Daemons on Hobby have a monthly provisioned-memory quota, so Hobby cannot guarantee uninterrupted 24/7 operation. Check the current Vercel plan and daemon limits before relying on continuous operation.
