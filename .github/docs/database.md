# Cijene API — Database Documentation

## Engine

PostgreSQL 17, accessed via `asyncpg` (async Python driver).

## Schema Overview

```
products          ← canonical products (identified by EAN barcode)
chains            ← retail chain definitions
chain_products    ← chain-specific product listings (name, code, category)
stores            ← physical store locations
prices            ← daily price records (links chain_products + stores)
users             ← API key authentication
chain_stats       ← pre-computed statistics per chain per date
product_images    ← 200×200 JPEG thumbnails for products
```

## Tables

### `products`
Canonical product registry keyed by EAN barcode.

| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `ean` | VARCHAR UNIQUE | EAN-13 or chain-specific `chain:<code>` |
| `brand` | VARCHAR | Enriched/curated brand name |
| `name` | VARCHAR | Enriched/curated product name |
| `quantity` | DECIMAL | Numeric quantity |
| `unit` | VARCHAR | Unit of measure (e.g., `kg`, `L`, `kom`) |

---

### `chains`
Retail chain registry.

| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `code` | VARCHAR UNIQUE | e.g., `konzum`, `metro` |

---

### `chain_products`
Chain-specific product listing — each chain publishes its own names, codes, and categories.

| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `chain_id` | INT FK→chains | |
| `product_id` | INT FK→products | Links to canonical product via EAN |
| `code` | VARCHAR | Chain-internal product code |
| `name` | VARCHAR | Product name as published by the chain |
| `brand` | VARCHAR | Brand as published by the chain |
| `category` | VARCHAR | Product category |
| `unit` | VARCHAR | Unit of measure |
| `quantity` | VARCHAR | Raw quantity string (e.g., `"500 G"`) |

---

### `stores`
Physical store locations.

| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `chain_id` | INT FK→chains | |
| `code` | VARCHAR | Chain-internal store code |
| `type` | VARCHAR | `supermarket`, `hipermarket`, etc. |
| `address` | VARCHAR | Street address |
| `city` | VARCHAR | City name |
| `zipcode` | VARCHAR | Postal code |
| `lat` | FLOAT | Latitude |
| `lon` | FLOAT | Longitude |
| `phone` | VARCHAR | Phone number |

---

### `prices`
Daily price records — one row per (chain_product, store, date).

| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `chain_product_id` | INT FK→chain_products | |
| `store_id` | INT FK→stores | |
| `price_date` | DATE | Date the price was published |
| `regular_price` | DECIMAL | Standard shelf price |
| `special_price` | DECIMAL | Promotional price (nullable) |
| `unit_price` | DECIMAL | Price per kg/L (nullable) |
| `best_price_30` | DECIMAL | Lowest price in last 30 days (nullable) |
| `anchor_price` | DECIMAL | Reference price since 2 May 2025 (nullable) |

---

### `users`
API key authentication table.

| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `name` | VARCHAR | Display name |
| `api_key` | VARCHAR UNIQUE | Secret API key |
| `is_active` | BOOL | Must be `TRUE` to authenticate |
| `created_at` | TIMESTAMPTZ | |

**Create user**:
```sql
INSERT INTO users (name, api_key, is_active) VALUES ('MyApp', 'secret-key', TRUE);
```

---

### `chain_stats`
Pre-computed per-chain statistics, populated by `service.db.stats`.

| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `chain_id` | INT FK→chains | |
| `price_date` | DATE | |
| `price_count` | INT | Total prices loaded |
| `store_count` | INT | Stores with data |
| `created_at` | TIMESTAMPTZ | |

---

### `product_images`
Stores 200×200 JPEG thumbnails for chain products.

| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `chain_product_id` | INT FK→chain_products UNIQUE | |
| `ean` | VARCHAR | EAN barcode for cross-reference |
| `image_data` | BYTEA | Raw JPEG bytes |
| `image_format` | VARCHAR | Default `jpeg` |
| `width` | INT | Default `200` |
| `height` | INT | Default `200` |
| `source_url` | TEXT | URL image was fetched from |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

---

## Database Layer (`service/db/`)

### `base.py` — `Database` abstract class

All DB operations are defined as `async` methods here. The PostgreSQL implementation is in `psql.py`.

Key methods:

| Method | Description |
|--------|-------------|
| `connect()` / `close()` | Connection pool lifecycle |
| `create_tables()` | Run DDL to create tables if not present |
| `list_chains()` | Return all chains |
| `list_stores(chain_code)` | Return stores for a chain |
| `filter_stores(...)` | Geo/city/address store search |
| `get_products_by_ean(eans)` | Look up products by EAN list |
| `search_products(q, limit)` | Full-text product name search |
| `fuzzy_search_products(q, limit)` | Fuzzy name search (rapidfuzz) |
| `get_chain_products_for_product(...)` | Chain listings for products |
| `get_product_prices(product_ids, date)` | Aggregated prices by chain for date |
| `get_product_store_prices(...)` | Per-store price data |
| `get_price_check(code, name, chain, city)` | Cross-chain price comparison |
| `get_user_by_api_key(key)` | Authentication lookup |
| `list_latest_chain_stats()` | Latest chain statistics |
| `get_product_image(chain_product_id)` | Retrieve thumbnail bytes |

### `psql.py` — PostgreSQL implementation

Implements `Database` using `asyncpg` connection pools. Uses parameterized queries (no string interpolation) to prevent SQL injection.

### `import.py` — CSV importer

```bash
uv run -m service.db.import /path/to/YYYY-MM-DD/
```

Reads the crawler output directory and upserts chains, stores, chain_products, and prices into the database.

### `stats.py` — Statistics calculator

```bash
uv run -m service.db.stats 2025-06-01
```

Computes and upserts `chain_stats` rows for the given dates.

### `enrich.py` — Product enrichment

```bash
uv run -m service.db.enrich enrichment/products.csv
```

Updates canonical product names, brands, quantities, and units from the curated CSV.

## Geolocation Search

Stores with `lat`/`lon` populated support distance-based filtering. The query uses the Haversine formula implemented in PostgreSQL SQL.

## Performance Notes

- Minimum pool: 5 connections, maximum: 20 (configurable via env)
- Auth results cached in-memory (60 min for hits, 60 s for misses)
- `chain_stats` avoids expensive aggregation on every API call
