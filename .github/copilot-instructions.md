# GitHub Copilot Instructions — Cijene API

## Project Description

Cijene API is a Croatian grocery price tracking service. It consists of two components:
1. **Crawler** (`crawler/`) — downloads and parses daily price lists from Croatian retail chains
2. **Web Service** (`service/`) — FastAPI REST API serving the price data from PostgreSQL

See `.github/docs/overview.md` for the full project overview.

## Technology Stack

- Python 3.13+ with strict type hints
- FastAPI + Uvicorn (web service)
- asyncpg (PostgreSQL async driver)
- Pydantic v2 (request/response schemas)
- Python dataclasses (DB model objects)
- httpx (HTTP client in crawlers)
- BeautifulSoup4 + lxml (HTML parsing)
- openpyxl (Excel parsing)
- rapidfuzz (fuzzy search)
- Pillow (image processing)
- uv (package manager)
- Ruff (linting + formatting)

## Code Style

- Always use Python type hints (3.13+ syntax: `list[str]` not `List[str]`, `str | None` not `Optional[str]`)
- Async/await throughout the service layer
- `snake_case` for functions, variables, module names
- `UPPER_CASE` for constants
- `PascalCase` for classes
- Docstrings on all public methods — concise but not one-liners; include Args/Returns for complex methods
- No unnecessary comments — the code should be self-explanatory
- Use `logger = logging.getLogger(__name__)` for logging; never `print()` in production code

## Architecture Patterns

### Adding a crawler

- Inherit from `BaseCrawler` in `crawler/store/base.py`
- Implement `get_all_products(date: datetime.date) -> list[Store]`
- Use `CHAIN`, `BASE_URL`, `PRICE_MAP`, `FIELD_MAP` class attributes
- Use `self.fetch_text()`, `self.parse_csv()`, `self.parse_price()`, `self.get_zip_contents()`
- Register in `crawler/crawl.py` → `CRAWLERS` dict
- See `.github/skills/crawler-implementation.md` for patterns

### Adding an API endpoint

- Add to `service/routers/v1.py`
- Define Pydantic response schema with `Field(..., description="...")`
- Use `RequireAuth` dependency for protected endpoints
- Add DB method to `service/db/base.py` interface + `service/db/psql.py` implementation
- Use parameterized queries (`$1`, `$2`) — never string-format SQL
- See `.github/skills/api-development.md` for patterns

### Database queries

- All DB access through `service/db/base.py` methods
- Parameterized queries only (no string interpolation)
- Use `async with self.pool.acquire() as conn:` for connection borrowing
- DB models are frozen dataclasses, not Pydantic models
- See `.github/skills/database-queries.md` for patterns

## Important Conventions

1. **DB layer separation**: Endpoint handlers call `db.*()` methods; SQL lives only in `psql.py`
2. **Settings**: All configuration via environment variables in `service/config.py`; use `settings.get_db()` for DB singleton
3. **Error handling in crawlers**: Use `try/except` per-store; `logger.error(...)` and `continue` on failure
4. **Price parsing**: Always use `self.parse_price(value, required=True/False)` — never parse prices manually
5. **Encoding**: Croatian sites often use `windows-1250`; use `fetch_text(url, encodings=["windows-1250", "utf-8"])`
6. **Authentication**: Bearer token auth; keys in `users.api_key` column

## File Locations

| What | Where |
|------|-------|
| Crawler base class | `crawler/store/base.py` |
| Crawler models | `crawler/store/models.py` |
| Crawler registry | `crawler/crawl.py` → `CRAWLERS` |
| API endpoints | `service/routers/v1.py` |
| Auth middleware | `service/routers/auth.py` |
| DB interface | `service/db/base.py` |
| DB implementation | `service/db/psql.py` |
| DB models (dataclasses) | `service/db/models.py` |
| App settings | `service/config.py` |
| DB schema SQL | `service/db/psql.sql` |

## Documentation

Full project documentation is in `.github/docs/`:
- `overview.md` — Architecture and project structure
- `api.md` — All API endpoints
- `database.md` — Database schema and query patterns
- `crawler.md` — Crawler architecture and implementation guide
- `deployment.md` — Docker and Railway deployment
- `development.md` — Local development setup

Skills files in `.github/skills/`:
- `crawler-implementation.md` — Step-by-step crawler guide
- `api-development.md` — Step-by-step API endpoint guide
- `database-queries.md` — DB query patterns and conventions
