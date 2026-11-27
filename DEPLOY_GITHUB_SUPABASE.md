# GitHub Actions + Supabase worker

GitHub Actions runs one worker cycle every 15 minutes. Supabase stores the queue and settings between independent GitHub-hosted runs. No Vercel daemon is used for comment processing.

## GitHub repository settings

Add these Actions secrets in **Settings → Secrets and variables → Actions → Secrets**:

- `DATABASE_URL`: Supabase PostgreSQL Session Pooler URI with `sslmode=require`.
- `INSTAGRAM_ACCESS_TOKEN`
- `INSTAGRAM_USER_ID`
- `GEMINI_API_KEY`

Add this Actions variable in **Settings → Secrets and variables → Actions → Variables**:

- `SAMAI_ENABLED` = `true`

The workflow file is `.github/workflows/samai-worker.yml`. Scheduled runs are best-effort and can be delayed by GitHub; check the Actions tab to confirm each `schedule` run.

## Controls

Use **Actions → SamAI comment worker → Run workflow** and select:

- `run`: process a cycle now.
- `stats`: print recent processing counts and usage.
- `stop`: pause processing in Supabase.
- `start`: resume processing and process the stored queue.

Starting through this workflow leaves the legacy Vercel daemon disabled. Supabase remains the shared persistent store.
