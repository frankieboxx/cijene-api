# Cijene API — Coding Skills

## Project Overview

This is a Croatian grocery price tracking service. It consists of a **crawler** that downloads price lists from retail chains and a **FastAPI web service** that exposes the data via REST API. The project uses Python 3.13, uv for dependency management, and PostgreSQL for storage.

## Repository Structure

```
crawler/          # Price data crawlers for each retail chain
service/          # FastAPI REST API and database layer
scripts/          # Pipeline, reporting and price comparison scripts
enrichment/       # Enriched product catalogue CSV
docs/             # Architecture documentation
.github/docs/     # GitHub-stored documentation (app, service, crawler, railway)
.github/skills/   # GitHub Copilot skills/instructions
```

## Crawler Skills

### BaseCrawler Pattern

All crawlers inherit from `crawler/store/base.py:BaseCrawler`. A crawler must:

1. Define class constants: `CHAIN`, `BASE_URL`, `PRICE_MAP`, `FIELD_MAP`
2. Implement `get_all_products(self, date: datetime.date) -> list[Store]`
3. Register itself in `crawler/crawl.py` `CRAWLERS` dict

```python
from crawler.store.base import BaseCrawler
from crawler.store.models import Product, Store

class NewChainCrawler(BaseCrawler):
    CHAIN = "newchain"
    BASE_URL = "https://www.newchain.hr"

    PRICE_MAP = {
        "price": ("MPC", True),
        "unit_price": ("Cijena po jedinici", True),
        "special_price": ("Akcijska cijena", False),
    }

    FIELD_MAP = {
        "product": ("Naziv proizvoda", True),
        "product_id": ("Šifra proizvoda", True),
    }

    def get_all_products(self, date: datetime.date) -> list[Store]:
        ...
```

### HTTP Requests

Use `self.client` (httpx instance) for all HTTP requests. The base class provides:
- `self.fetch_text(url, encodings=None)` — download text (handles encoding)
- `self.fetch_binary(url, file_pointer)` — download binary
- `self.get_zip_contents(url, extension)` — stream ZIP entries

### HTML Parsing

Use `BeautifulSoup` with `html.parser` and prefer CSS selectors:

```python
from bs4 import BeautifulSoup
soup = BeautifulSoup(content, "html.parser")
links = soup.select("a[href$='.csv']")
```

### Data Models

Use `crawler/store/models.py:Product` and `Store`. Never modify these dataclasses for chain-specific needs — use `fix_product_data()` instead.

### ZIP Date Pattern

For crawlers that match files by date in a ZIP archive, define:
```python
ZIP_DATE_PATTERN = re.compile(r".*_(\d{2})_(\d{2})_(\d{4})\.zip")
```
Then call `self.parse_index_for_zip(content)` to get a `{date: url}` mapping.

### Error Handling

- Use `try/except` per individual store; log errors and continue
- Raise exceptions only for complete crawl failures
- Log with `logger = logging.getLogger(__name__)`

### Output

`save_chain(path, stores)` in `crawler/store/output.py` writes three CSV files (`stores.csv`, `products.csv`, `prices.csv`) per chain.

## Service Skills

### FastAPI Patterns

Routers are versioned: `service/routers/v0.py` and `service/routers/v1.py`. New endpoints go into `v1.py`.

All v1 endpoints require authentication via `RequireAuth` dependency:
```python
from service.routers.auth import RequireAuth
router = APIRouter(tags=["..."], dependencies=[RequireAuth])
```

### Database Access

Always use `db = settings.get_db()` at module level to get the singleton `Database` instance. Use `await db.<method>()` for all DB operations. Never open direct DB connections.

### Response Models

Define Pydantic `BaseModel` response schemas for all endpoints:
```python
class MyResponse(BaseModel):
    field: str = Field(..., description="Description of the field.")
```

### Settings

All configuration is in `service/config.py:Settings`. Add new env vars there. Access with `from service.config import settings`.

## Code Style

- **Python version:** 3.13+
- **Type hints:** always use type hints (pyright-strict / ty check)
- **Formatter:** ruff (line length 88)
- **Linter:** ruff
- **Strings:** double quotes
- **Imports:** standard library, then third-party, then local
- **Docstrings:** required on classes and non-trivial methods; use concise multi-line format

## Naming Conventions

| Entity              | Convention        | Example                   |
|---------------------|-------------------|---------------------------|
| Crawler class       | `ChainCrawler`    | `KonzumCrawler`           |
| Crawler file        | `chain_name.py`   | `konzum.py`               |
| CHAIN constant      | lowercase         | `"konzum"`                |
| Methods             | `snake_case`      | `get_all_products`        |
| Constants           | `UPPER_CASE`      | `BASE_URL`, `PRICE_MAP`   |
| Pydantic models     | `PascalCase`      | `ProductResponse`         |

## Testing

Run lint and type checks before committing:
```bash
uv run ruff check
uv run ruff format --check
uv run ty check
```

There is no unit test suite currently — validate crawlers by running them directly:
```bash
python -m crawler.store.<module>
```

## Adding a New Crawler — Checklist

- [ ] Understand the chain's price list structure (CSV / XML / ZIP / API)
- [ ] Identify how to get the list of stores and links to price files
- [ ] Map CSV/XML columns to `PRICE_MAP` and `FIELD_MAP`
- [ ] Handle character encoding (Croatian sites often use Windows-1250)
- [ ] Implement `get_all_products()` returning `list[Store]`
- [ ] Add `if __name__ == "__main__"` test block
- [ ] Register in `crawler/crawl.py:CRAWLERS`
- [ ] Test with `python -m crawler.cli.crawl -l` to confirm registration
