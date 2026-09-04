# Cloudflare Worker: RepeaterMock Scraper Trigger

Receives `POST /trigger` from the GitHub Actions **merge job** and triggers the next matrix run via GitHub API (`workflow_dispatch`).

## Deploy (one-time)

### Option A — Interactive (recommended)

Run from this folder on your own machine (where you can log in to Cloudflare via browser):

```bash
cd cloudflare-trigger
npm install -g wrangler     # if not already installed
wrangler login              # opens browser, log in with any of your 5 Cloudflare accounts
wrangler deploy             # deploys the Worker
wrangler secret put GH_PAT  # paste your GitHub Classic PAT (repo+workflow scope)
wrangler secret put TRIGGER_TOKEN  # paste the value of CF_TRIGGER_TOKEN secret
```

After deploy, note the Worker URL printed by `wrangler deploy` (e.g. `https://rm-scraper-trigger.<your-account>.workers.dev`).

If the URL is different from what's in your GitHub secret `CF_TRIGGER_URL`, update the secret:

```bash
# Use the GitHub API or Settings → Secrets and variables → Actions → Update
# CF_TRIGGER_URL = https://rm-scraper-trigger.<your-account>.workers.dev/trigger
```

### Option B — With API token (headless)

If you have a Cloudflare API token (from Cloudflare dashboard → My Profile → API Tokens → Create Token → "Edit Cloudflare Workers" template):

```bash
cd cloudflare-trigger
CLOUDFLARE_API_TOKEN=YOUR_CF_TOKEN CLOUDFLARE_ACCOUNT_ID=YOUR_ACCOUNT_ID wrangler deploy
# Set secrets via Cloudflare API or dashboard:
#   GH_PAT          = <same classic PAT you used for GitHub secret GH_PAT>
#   TRIGGER_TOKEN   = <same value as CF_TRIGGER_TOKEN GitHub secret>
```

## How it works

```
GitHub Actions merge job completes
        ↓
POST https://<your-worker>.workers.dev/trigger
   Authorization: Bearer <TRIGGER_TOKEN>
   Body: {"max_tests_per_job": ""}
        ↓
Worker verifies bearer token
        ↓
Worker calls GitHub API:
   POST /repos/sujitbhai7710/repeatermock-mass-scraper/actions/workflows/scrape-matrix.yml/dispatches
   Authorization: Bearer <GH_PAT>
   Body: {"ref": "main", "inputs": {"max_tests_per_job": ""}}
        ↓
Next matrix run starts (within ~30s)
```

## Security

- All secrets are stored as Cloudflare Worker secrets (`wrangler secret put`), **NOT** in plain text in the source code
- The Worker reads them from `env.GH_PAT` and `env.TRIGGER_TOKEN` at runtime
- Bearer token authentication prevents unauthorized triggers
- The GitHub PAT has only `repo` + `workflow` scope (classic PAT, scoped minimally)

## Testing

After deploy:

```bash
curl -X POST https://<your-worker>.workers.dev/trigger \
  -H "Authorization: Bearer <CF_TRIGGER_TOKEN value>" \
  -H "Content-Type: application/json" \
  -d '{"max_tests_per_job": ""}'
```

Expected response:

```json
{"ok": true, "triggered": true, "message": "Next matrix run triggered on GitHub"}
```

## Without the Worker deployed

If the Worker is not deployed, the merge job's auto-trigger step will print:

```
⚠️ CF_TRIGGER_URL or CF_TRIGGER_TOKEN not set in secrets — skipping auto-trigger
```

This is **non-fatal** — the matrix run still completes, but you'll need to manually trigger the next run via GitHub Actions UI (Actions tab → "Scrape RepeaterMock (Full Matrix)" → Run workflow).
