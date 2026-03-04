# Cijene API — Deployment Guide

## Deployment Targets

| Target | Description |
|--------|-------------|
| **Local Docker** | Full stack via docker-compose |
| **Railway** | Production cloud deployment |
| **Manual** | Direct Python for development |

---

## Docker Deployment

### Prerequisites

- Docker 24+
- docker-compose v2+

### Quick Start

```bash
git clone https://github.com/frankieboxx/cijene-api.git
cd cijene-api
cp .env.docker.example .env
# Edit .env as needed
docker-compose up -d
```

API available at `http://localhost:8000`, Swagger at `http://localhost:8000/docs`.

### Services

| Service | Dockerfile | Description |
|---------|-----------|-------------|
| `api` | `Dockerfile` | FastAPI web service |
| `crawler` | `Dockerfile.crawler` | Price data crawler |
| `db` | `postgres:17` | PostgreSQL database |

### Environment Variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_PASSWORD` | — | Database password (required) |
| `DB_DSN` | auto-generated | PostgreSQL connection string |
| `BASE_URL` | `https://api-production-37dc.up.railway.app` | Public API URL |
| `DEBUG` | `false` | Enable debug mode (hot reload) |
| `TIMEZONE` | `Europe/Zagreb` | Container timezone |
| `PORT` | `8000` | API port |
| `ROOT_PATH` | `/` | ASGI root path (for reverse proxies) |
| `ARCHIVE_DIR` | `data` | Crawler output directory |
| `VERSION` | `0.1.0` | API version string |
| `MAILGUN_API_KEY` | — | Mailgun API key (for email reports) |
| `MAILGUN_DOMAIN` | — | Mailgun domain |
| `REPORT_RECIPIENTS` | — | Email recipients for price reports |
| `ATRIUM_DATABASE_URL` | — | Atrium ERP database connection string |

### Production vs Development

**Production** (no hot reload):
```bash
docker-compose -f docker-compose.yml up -d
```

**Development** (hot reload, `docker-compose.override.yml` auto-applied):
```bash
docker-compose up -d
```

### Common Operations

```bash
# Run crawler manually
docker-compose run --rm crawler

# Import crawler output into DB
docker-compose exec api uv run -m service.db.import /app/output/2025-06-01

# Compute statistics
docker-compose exec api uv run -m service.db.stats 2025-06-01

# Access PostgreSQL
docker-compose exec db psql -U cijene_user -d cijene

# View API logs
docker-compose logs -f api

# Rebuild and restart
docker-compose up -d --build

# Database backup
docker-compose exec db pg_dump -U cijene_user cijene > backup.sql

# Full teardown (keeps DB volume)
docker-compose down

# Full teardown including DB data
docker-compose down -v
```

---

## Railway Deployment

### Overview

The API service is deployed to [Railway](https://railway.app). Configuration is in `railway.toml` and `Dockerfile.railway`.

**Project**: `c9eeed53-f5d1-4c5e-9eae-68793b4691c9`
**Crawler service**: `74f04ed3-edf6-4293-a47c-82daad7dffa7`

### `railway.toml`

```toml
[build]
dockerfilePath = "Dockerfile.railway"

[deploy]
healthcheckPath = "/health"
healthcheckTimeout = 300
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 10
```

### GitHub Actions Auto-Deploy

Push to `main` triggers the deploy workflow (`.github/workflows/deploy.yml`):

1. CI runs (lint, type-check)
2. On success, deploys to Railway using `railway up`

**Required GitHub secrets / variables**:
- `RAILWAY_TOKEN` (secret) — Railway API token
- `RAILWAY_SERVICE_ID` (variable) — Service ID to deploy

### Manual Deploy

```bash
npm install -g @railway/cli
railway login
railway up --service 74f04ed3-edf6-4293-a47c-82daad7dffa7
```

---

## CI/CD (GitHub Actions)

### `ci.yml` — Lint & Type Check

Triggered on push/PR to `main`.

Steps:
1. Set up Python 3.13
2. Install `uv`
3. `uv sync --dev`
4. `uv run ruff check` — lint
5. `uv run ruff format --check` — code style
6. `uv run ty check` — type checking

### `deploy.yml` — Deploy to Railway

Triggered on push to `main`, after CI passes.

---

## Crawler Scheduling

The crawler should run daily, typically before 09:00, to have fresh prices available.

**Railway cron** (recommended): Set `0 8 * * *` schedule on the crawler service.

**Docker cron** (alternative): Use `docker-compose run --rm crawler` in a host cron job.

---

## Initial Database Setup

After first deployment:

```bash
# Schema is auto-created on startup (service.db.base.create_tables)
# Import first batch of prices
uv run -m service.db.import /path/to/2025-06-01/

# Compute initial statistics
uv run -m service.db.stats 2025-06-01

# Import enriched product data
uv run -m service.db.enrich enrichment/products.csv

# Create API user
psql -c "INSERT INTO users (name, api_key, is_active) VALUES ('admin', 'my-key', TRUE);"
```
