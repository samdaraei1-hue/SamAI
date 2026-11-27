# SamAI worker: Supabase clock, GitHub runner

Supabase Cron is the clock. It asks GitHub Actions to run one cycle every 20 minutes. GitHub runs the existing Python worker; Supabase stores the comment queue and settings. No Vercel worker or n8n service is involved.

## One-time setup

The four existing Actions secrets remain required: `DATABASE_URL`, `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID`, and `GEMINI_API_KEY`.

Create a fine-grained GitHub personal access token with access only to the `SamAI` repository and **Actions: Read and write** permission. Add it as the repository Actions secret `GITHUB_DISPATCH_TOKEN`. Do not paste the token into chat or commit it.

Then run **Actions → SamAI comment worker → Run workflow → install-scheduler** once. That setup run stores the token in Supabase Vault, installs a Supabase Cron job for `*/20 * * * *`, and makes a read-only GitHub dispatch health check. The health check runs `stats`; it does not publish comments.

After setup, Supabase sends the `scheduled` operation to GitHub every 20 minutes. These automatic runs are labeled `scheduled` in Actions. See the timer's execution history in the Supabase Dashboard under **Integrations → Cron**. Pause or resume processing with the workflow's `stop` and `start` operations; the Cron job can stay installed while processing is paused.

## How the scheduler is installed

`install_supabase_cron.py` creates or updates the named Cron job, so repeating the installation does not create duplicate jobs. The GitHub token is kept encrypted in Supabase Vault and is never printed in Actions logs.
