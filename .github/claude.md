# Claude AI Instructions — Cijene API

## Project Overview

**Cijene API** is a Croatian grocery price tracking service that collects publicly available product pricing data from major Croatian retail chains (mandated by NN 75/2025) and exposes it through a REST API.

**Repository**: `https://github.com/frankieboxx/cijene-api`
**Live API**: `https://api.cijene.dev`
**Language**: Python 3.13+

## Two-Component Architecture

### 1. Crawler (`crawler/`)
Downloads, parses, and saves price data from retail chain websites.

- Entry: `python -m crawler.cli.crawl /output/`
- Output: CSV files + ZIP archive in a `YYYY-MM-DD/` folder
- Each chain has its own crawler class inheriting `BaseCrawler`

### 2. Web Service (`service/`)
FastAPI REST API backed by PostgreSQL.

- Entry: `python -m service.main` → `http://localhost:8000`
- Auth: Bearer token (API key in `users` table)
- DB: PostgreSQL via asyncpg

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.13+ |
| Package manager | `uv` |
| Web framework | FastAPI + Uvicorn |
| Database | PostgreSQL 17, asyncpg |
| HTTP client | httpx |
| HTML parsing | BeautifulSoup4 + lxml |
| Validation | Pydantic v2 |
| DB models | Python dataclasses |
| Fuzzy search | rapidfuzz |
| Linting | ruff |
| Type checking | ty (Astral) |

## Coding Standards

### Python style
- Python 3.13+ type hints: `list[str]`, `str | None`, `dict[str, int]`
- `async`/`await` throughout the service layer
- `snake_case` methods/variables, `PascalCase` classes, `UPPER_CASE` constants
- Docstrings on all public methods (not one-liners; include Args/Returns for complex methods)
- Never `print()` in production code — use `logger = logging.getLogger(__name__)`
- Ruff-compatible formatting (88 char line length)

### Security
- **Never interpolate untrusted values into SQL** — always use parameterized queries (`$1`, `$2`)
- API keys from `users.api_key` — never hardcode secrets
- Bearer token auth required on all `/v1` endpoints via `RequireAuth` dependency

### Database patterns
- All SQL in `service/db/psql.py` (implementation)
- All method signatures in `service/db/base.py` (interface)
- DB models are frozen dataclasses, not Pydantic models
- Use `async with self.pool.acquire() as conn:` for connections
- Use transactions for multi-statement writes

### Crawler patterns
- Inherit from `BaseCrawler` in `crawler/store/base.py`
- Implement `get_all_products(date: datetime.date) -> list[Store]`
- Use `try/except` per-store with `logger.error(...)` + `continue`
- Use `self.fetch_text()`, `self.parse_csv()`, `self.parse_price()`
- Croatian sites often use `windows-1250` encoding

## Key File Locations

```
crawler/
  crawl.py              ← CRAWLERS registry (add new crawlers here)
  store/
    base.py             ← BaseCrawler (abstract, utilities)
    models.py           ← Product, Store data models
    output.py           ← CSV and ZIP output
    *.py                ← Per-chain implementations

service/
  main.py               ← FastAPI app + startup
  config.py             ← Settings (env vars)
  routers/
    v1.py               ← Current API endpoints
    v0.py               ← Legacy endpoints
    auth.py             ← Bearer token auth
  db/
    base.py             ← Database interface (all methods here)
    psql.py             ← PostgreSQL implementation
    models.py           ← DB dataclasses
    import.py           ← CSV → DB importer
    stats.py            ← Statistics calculator
    enrich.py           ← Product enrichment importer
    psql.sql            ← Schema DDL
```

## Common Tasks

### Adding a new retail chain crawler
1. Create `crawler/store/<chain>.py` with class inheriting `BaseCrawler`
2. Implement `get_all_products(date)` using appropriate pattern (index/ZIP/API/single-file)
3. Register in `crawler/crawl.py` → `CRAWLERS` dict
4. Test: `python -m crawler.store.<chain>`

See `.github/skills/crawler-implementation.md` for full guide.

### Adding a new API endpoint
1. Add Pydantic response schema to `service/routers/v1.py`
2. Add `@router.get(...)` function with `RequireAuth`
3. Add method to `service/db/base.py` interface
4. Implement in `service/db/psql.py`

See `.github/skills/api-development.md` for full guide.

### Writing database queries
- Always use `$1, $2, ...` placeholders
- Single row: `conn.fetchrow(...)`, multiple: `conn.fetch(...)`, mutations: `conn.execute(...)`

See `.github/skills/database-queries.md` for patterns.

## Project Documentation

All documentation is in `.github/docs/`:
- `overview.md` — Project architecture and structure
- `api.md` — Complete API reference
- `database.md` — Database schema and query patterns
- `crawler.md` — Crawler architecture (detailed)
- `deployment.md` — Docker and Railway deployment
- `development.md` — Local development setup

## Deployment

- **Docker**: `docker-compose up -d`
- **Railway**: Auto-deployed via GitHub Actions (`.github/workflows/deploy.yml`) on push to `main`
- **CI**: Lint + type check on every PR (`.github/workflows/ci.yml`)

## Important Context

- Data is publicly mandated: all prices are public per NN 75/2025
- License: AGPL-3.0 (code), CC BY-NC-SA 4.0 (enrichment CSV)
- The `anchor_price` field reflects prices as of 2 May 2025 (the reference date in the law)
- `chain:` prefix is used for products without official EAN codes (chain-specific IDs)
- `atrium_database_url` connects to Atrium restaurant ERP for purchase cost comparison
