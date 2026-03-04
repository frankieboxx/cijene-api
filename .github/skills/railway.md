# Cijene API — Railway & Deployment Skills

## Railway Platform

The application is deployed on [Railway](https://railway.app/). Configuration lives in `railway.toml` at the repository root.

```toml
[build]
dockerfilePath = "Dockerfile.railway"

[deploy]
healthcheckPath = "/health"
healthcheckTimeout = 300
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 10
```

## Services on Railway

| Service   | Description                                | Cron Schedule    |
|-----------|--------------------------------------------|------------------|
| API       | FastAPI web service (`Dockerfile.railway`) | always-on        |
| Crawler   | Pipeline: crawl → import → email           | `0 8 * * *`      |

- **Crawler service ID:** `74f04ed3-edf6-4293-a47c-82daad7dffa7`
- **Railway project ID:** `c9eeed53-f5d1-4c5e-9eae-68793b4691c9`

## GitHub Actions Workflows

### CI (`.github/workflows/ci.yml`)
- Triggered: push / PR to `main`
- Runs on: `ubuntu-24.04`, `macos-latest`, `windows-latest`
- Steps: install `uv`, sync deps, `ruff check`, `ruff format --check`, `ty check`

### Deploy (`.github/workflows/deploy.yml`)
- Triggered: push to `main` (after CI passes via `needs: build`)
- Installs Railway CLI, runs `railway up --service $RAILWAY_SERVICE_ID`
- Requires: `RAILWAY_TOKEN` secret, `RAILWAY_SERVICE_ID` variable

## Required GitHub Secrets and Variables

| Type     | Name                  | Description                   |
|----------|-----------------------|-------------------------------|
| Secret   | `RAILWAY_TOKEN`       | Railway API token             |
| Variable | `RAILWAY_SERVICE_ID`  | Railway service ID to deploy  |

## Environment Variables (Railway)

Set these in Railway dashboard or via `railway variables set`:

```
DB_DSN=postgresql://...
ARCHIVE_DIR=/app/output
TIMEZONE=Europe/Zagreb
BASE_URL=https://api-production-37dc.up.railway.app
MAILGUN_API_KEY=...
MAILGUN_DOMAIN=...
REPORT_RECIPIENTS=...
```

## Health Check

`GET /health` must return `{"status": "healthy"}`.  
Railway uses this to verify deployment health before routing traffic.

## CLI Commands

```bash
# Login to Railway
railway login

# Check service status
railway status

# View live logs
railway logs --tail 100

# Run a one-off command in the Railway environment
railway run python -m scripts.pipeline --date YYYY-MM-DD

# Create API token
railway tokens create
```

## Rollback

Use Railway dashboard → Deployments → select previous deployment → Redeploy.

## Docker Images

| File                    | Used for                          |
|-------------------------|-----------------------------------|
| `Dockerfile`            | Local dev + Docker Compose         |
| `Dockerfile.crawler`    | Standalone crawler container      |
| `Dockerfile.railway`    | Railway deployment (API + pipeline)|
