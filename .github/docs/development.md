# Cijene API — Development Guide

## Prerequisites

- Python 3.13+
- [`uv`](https://docs.astral.sh/uv/) (recommended package manager)
- PostgreSQL 17 (or Docker)
- Git

## Project Setup

```bash
git clone https://github.com/frankieboxx/cijene-api.git
cd cijene-api
uv sync --dev
```

This installs all dependencies including dev tools (`ruff`, `pyright`, `pre-commit`, `ty`).

## Environment Configuration

### For local development (no Docker)

```bash
cp .env.example .env
# Edit .env:
#   DB_DSN=postgresql://user:password@localhost/cijene
#   DEBUG=true
```

### For Docker development

```bash
cp .env.docker.example .env
# Edit .env as needed
docker-compose up -d
```

## Running the API Service

```bash
uv run -m service.main
```

- API: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- Auto-reload on change when `DEBUG=true`

## Running the Crawler

```bash
# Crawl all chains for today
uv run -m crawler.cli.crawl /tmp/output/

# Crawl specific chains
uv run -m crawler.cli.crawl -c konzum,metro /tmp/output/

# Crawl a specific date
uv run -m crawler.cli.crawl -d 2025-06-01 /tmp/output/

# List available chains
uv run -m crawler.cli.crawl -l
```

### Windows

Set `PYTHONUTF8=1` or use `-X utf8` to avoid encoding issues:
```powershell
$env:PYTHONUTF8 = "1"
uv run -m crawler.cli.crawl /tmp/output/
```

## Importing Crawler Output

After running the crawler:

```bash
# Import prices to DB
uv run -m service.db.import /tmp/output/2025-06-01/

# Compute statistics
uv run -m service.db.stats 2025-06-01

# Import enriched product data (one-time or periodically)
uv run -m service.db.enrich enrichment/products.csv
```

## Linting & Formatting

```bash
# Check for lint errors
uv run ruff check

# Auto-fix lint errors
uv run ruff check --fix

# Check code formatting
uv run ruff format --check

# Auto-format code
uv run ruff format

# Type checking
uv run ty check
```

## Pre-commit Hooks

```bash
# Install pre-commit hooks (run once)
uv run pre-commit install

# Run manually on all files
uv run pre-commit run --all-files
```

## Testing Individual Crawlers

Each crawler has a `__main__` block:

```bash
# Test a specific crawler
python -m crawler.store.konzum
python -m crawler.store.metro
python -m crawler.store.tommy
```

## Adding a New Crawler

See [`.github/skills/crawler-implementation.md`](../skills/crawler-implementation.md) for a step-by-step guide.

Quick summary:
1. Create `crawler/store/<name>.py` with a class inheriting `BaseCrawler`
2. Implement `get_all_products(date)` method
3. Register in `crawler/crawl.py` → `CRAWLERS` dict

## Adding a New API Endpoint

See [`.github/skills/api-development.md`](../skills/api-development.md) for a step-by-step guide.

Quick summary:
1. Add endpoint to `service/routers/v1.py` (or new router file)
2. Add required DB queries to `service/db/base.py` (interface) and `service/db/psql.py` (implementation)
3. Use `RequireAuth` dependency for protected endpoints

## Code Style Conventions

- Python 3.13+ type hints everywhere
- Pydantic v2 for request/response schemas
- Python `dataclasses` for DB model objects
- `async`/`await` throughout the service layer
- `snake_case` for methods and variables
- `UPPER_CASE` for constants
- `PascalCase` for classes
- Docstrings on all public methods (concise but not one-liner)
- Ruff for linting and formatting (configured in `pyproject.toml`)

## Project Structure Tips

- `service/db/base.py` is the single source of truth for DB method signatures
- `service/db/psql.py` is the PostgreSQL implementation
- Settings come from environment variables via `service/config.py`
- `settings.get_db()` returns a singleton DB instance
- All API endpoints are in `service/routers/v1.py` (or `v0.py` for legacy)

## Debugging

Enable debug logging in a crawler's `__main__` block:

```python
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.DEBUG)
    from crawler.store.konzum import KonzumCrawler
    import datetime
    crawler = KonzumCrawler()
    stores = crawler.get_all_products(datetime.date.today())
    print(stores[0])
    print(stores[0].items[0])
```

For the service, set `DEBUG=true` in `.env` to enable debug logging and auto-reload.
