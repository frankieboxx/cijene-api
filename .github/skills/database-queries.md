# Skill: Database Query Patterns

## Overview

The database layer is structured as:

- `service/db/base.py` — abstract `Database` class (method signatures + docstrings)
- `service/db/psql.py` — `PostgreSQLDatabase` implementation using `asyncpg`
- `service/db/models.py` — data model dataclasses (not Pydantic)

All DB access uses parameterized queries with `asyncpg` connection pools.

## Writing a New Query

### 1. Define the method in `base.py`

```python
async def get_stores_by_city(self, city: str) -> list[StoreWithId]:
    """
    Return all stores in the given city (case-insensitive substring match).

    Args:
        city: City name or substring to search for.

    Returns:
        List of StoreWithId objects matching the city.
    """
    raise NotImplementedError
```

### 2. Implement in `psql.py`

```python
async def get_stores_by_city(self, city: str) -> list[StoreWithId]:
    """PostgreSQL implementation: city substring match (case-insensitive)."""
    async with self.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, chain_id, code, type, address, city, zipcode, lat, lon, phone
            FROM stores
            WHERE city ILIKE '%' || $1 || '%'
            ORDER BY city, address
            """,
            city,
        )
    return [
        StoreWithId(
            id=row["id"],
            chain_id=row["chain_id"],
            code=row["code"],
            type=row["type"],
            address=row["address"],
            city=row["city"],
            zipcode=row["zipcode"],
            lat=row["lat"],
            lon=row["lon"],
            phone=row["phone"],
        )
        for row in rows
    ]
```

## Common Patterns

### Connection pool usage

Always use `async with self.pool.acquire() as conn:` to borrow a connection:

```python
async with self.pool.acquire() as conn:
    row = await conn.fetchrow("SELECT * FROM chains WHERE code = $1", code)
```

### Single row vs multiple rows

```python
# Single row (returns None if not found)
row = await conn.fetchrow("SELECT * FROM users WHERE api_key = $1", key)

# Multiple rows
rows = await conn.fetch("SELECT * FROM stores WHERE chain_id = $1", chain_id)

# Execute (INSERT/UPDATE/DELETE)
await conn.execute(
    "UPDATE users SET is_active = $1 WHERE id = $2",
    True, user_id
)

# Execute many (batch insert)
await conn.executemany(
    "INSERT INTO prices (chain_product_id, store_id, price_date, regular_price) VALUES ($1, $2, $3, $4)",
    [(cp_id, store_id, date, price) for cp_id, store_id, date, price in data],
)
```

### Parameterized queries — ALWAYS use `$1`, `$2`, etc.

```python
# CORRECT — parameterized
await conn.fetch("SELECT * FROM stores WHERE city = $1", city)

# WRONG — never interpolate untrusted input into SQL
await conn.fetch(f"SELECT * FROM stores WHERE city = '{city}'")  # SQL injection!
```

### Transactions

```python
async with self.pool.acquire() as conn:
    async with conn.transaction():
        await conn.execute("INSERT INTO chains (code) VALUES ($1)", chain_code)
        await conn.execute("INSERT INTO stores (chain_id, ...) VALUES ($1, ...)", ...)
```

## Key Schema Notes

### Finding prices for a product on a date

```sql
SELECT
    p.regular_price, p.special_price, p.unit_price,
    p.best_price_30, p.anchor_price, p.price_date,
    s.city, s.address,
    c.code AS chain_code
FROM prices p
JOIN chain_products cp ON p.chain_product_id = cp.id
JOIN stores s ON p.store_id = s.id
JOIN chains c ON s.chain_id = c.id
WHERE cp.product_id = $1
  AND p.price_date = (
      SELECT MAX(price_date) FROM prices p2
      WHERE p2.chain_product_id = p.chain_product_id
        AND p2.price_date <= $2
  )
```

### Aggregated prices by chain

```sql
SELECT
    c.code AS chain,
    MIN(p.regular_price) AS min_price,
    MAX(p.regular_price) AS max_price,
    AVG(p.regular_price) AS avg_price,
    p.price_date
FROM prices p
JOIN chain_products cp ON p.chain_product_id = cp.id
JOIN chains c ON cp.chain_id = c.id
WHERE cp.product_id = ANY($1)
  AND p.price_date = (
      SELECT MAX(price_date) FROM prices p2
      WHERE p2.chain_product_id = p.chain_product_id
        AND p2.price_date <= $2
  )
GROUP BY c.code, p.price_date
```

### Geolocation (Haversine distance)

```sql
-- Stores within $3 km of ($1 lat, $2 lon)
WHERE (
    6371 * acos(
        cos(radians($1)) * cos(radians(lat)) *
        cos(radians(lon) - radians($2)) +
        sin(radians($1)) * sin(radians(lat))
    )
) <= $3
```

### Fuzzy text search (pg_trgm or rapidfuzz)

The service uses `rapidfuzz` in Python rather than PostgreSQL full-text search for simplicity. For DB-side search, `ILIKE '%' || $1 || '%'` is used:

```sql
WHERE name ILIKE '%' || $1 || '%'
```

## Upsert Pattern

The importer uses `INSERT ... ON CONFLICT DO NOTHING` or `ON CONFLICT DO UPDATE`:

```sql
INSERT INTO chain_products (chain_id, product_id, code, name, brand, category, unit, quantity)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (chain_id, code) DO UPDATE SET
    name = EXCLUDED.name,
    brand = EXCLUDED.brand,
    category = EXCLUDED.category,
    unit = EXCLUDED.unit,
    quantity = EXCLUDED.quantity
```

## DB Model Conventions

- All DB models are frozen Python `dataclasses` (not Pydantic)
- `Base` class (e.g., `Store`) has no `id`; `WithId` subclass (e.g., `StoreWithId`) adds `id: int`
- `to_dict()` is provided on models where needed for response construction

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class MyModel:
    field: str
    optional: str | None = None

@dataclass(frozen=True, slots=True, kw_only=True)
class MyModelWithId(MyModel):
    id: int
```
