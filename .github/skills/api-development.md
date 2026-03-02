# Skill: Adding a New API Endpoint

## When to Use

Use this skill when adding a new REST API endpoint to the Cijene API service.

## Architecture Overview

```
service/
├── main.py              ← FastAPI app, CORS, router registration
├── config.py            ← Settings from environment variables
├── routers/
│   ├── auth.py          ← Bearer token auth (RequireAuth dependency)
│   ├── v1.py            ← Current API endpoints (add here)
│   └── v0.py            ← Legacy endpoints
└── db/
    ├── base.py          ← Database interface (abstract methods)
    └── psql.py          ← PostgreSQL implementation
```

## Step-by-Step Guide

### 1. Define response schemas (Pydantic models)

Add to `service/routers/v1.py` (or a new router file):

```python
from pydantic import BaseModel, Field

class MyResponse(BaseModel):
    """Response schema for my endpoint."""

    field_one: str = Field(..., description="Description of field_one.")
    field_two: int | None = Field(None, description="Optional integer field.")
```

### 2. Implement the endpoint

```python
from fastapi import APIRouter, HTTPException, Query
from service.routers.auth import RequireAuth

router = APIRouter(tags=["My Feature"], dependencies=[RequireAuth])

@router.get("/my-endpoint/", summary="Short summary for Swagger")
async def my_endpoint(
    param: str = Query(..., description="Required query parameter"),
    optional: int = Query(10, description="Optional parameter with default"),
) -> MyResponse:
    """
    Detailed description shown in Swagger docs.

    Explains what this endpoint does, expected inputs, and return values.
    """
    result = await db.my_query(param, optional)
    if not result:
        raise HTTPException(status_code=404, detail="Not found")
    return MyResponse(field_one=result.value, field_two=result.count)
```

### 3. Add the database method to the interface

In `service/db/base.py`, add an abstract method (or regular async method):

```python
async def my_query(self, param: str, limit: int) -> list[MyModel]:
    """
    Describe what this query does and what it returns.

    Args:
        param: The search parameter.
        limit: Maximum number of results.

    Returns:
        List of MyModel objects matching the criteria.
    """
    raise NotImplementedError
```

### 4. Implement the query in psql.py

In `service/db/psql.py`:

```python
async def my_query(self, param: str, limit: int) -> list[MyModel]:
    """
    Query implementation for PostgreSQL.
    Uses parameterized queries — never format SQL with untrusted input.
    """
    async with self.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, value
            FROM my_table
            WHERE name ILIKE '%' || $1 || '%'
            ORDER BY name
            LIMIT $2
            """,
            param,
            limit,
        )
    return [MyModel(id=row["id"], name=row["name"], value=row["value"]) for row in rows]
```

### 5. Register router (if creating a new file)

If you created a new router file, register it in `service/main.py`:

```python
from service.routers import my_new_router

app.include_router(my_new_router.router, prefix="/v1")
```

## Key Conventions

### Authentication

- Use `dependencies=[RequireAuth]` on the router (applies to all endpoints in router)
- Or `Depends(RequireAuth)` on individual endpoints

```python
# Protected router (all endpoints require auth)
router = APIRouter(tags=["..."], dependencies=[RequireAuth])

# Or per-endpoint
@router.get("/public/")
async def public():  # no auth
    ...

@router.get("/private/")
async def private(user: User = RequireAuth):  # requires auth
    ...
```

### Error Handling

```python
# 400 - Bad request
raise HTTPException(status_code=400, detail="Both lat and lon must be provided")

# 404 - Not found
raise HTTPException(status_code=404, detail=f"Product with EAN {ean} not found")

# Let FastAPI handle validation errors (422) automatically via Pydantic
```

### Query Parameters

```python
from fastapi import Query

# Required
q: str = Query(..., description="Search query")

# Optional with default
limit: int = Query(20, ge=1, le=100, description="Max results (1-100)")

# Optional without default
date: datetime.date = Query(None, description="Date in YYYY-MM-DD format")

# Comma-separated list (manual parsing)
chains: str = Query(None, description="Comma-separated chain codes")
chain_list = [c.strip().lower() for c in chains.split(",")] if chains else None
```

### Response Models

- Always define explicit Pydantic response models
- Use `Field(..., description="...")` for Swagger documentation
- Return types in function signatures (`-> MyResponse`) for type checking

### Database Access

```python
from service.config import settings

db = settings.get_db()  # Singleton, call at module level

async def my_endpoint() -> MyResponse:
    result = await db.my_query(...)
```

## Testing Your Endpoint

After starting the service (`uv run -m service.main`):

```bash
# Test with curl
curl -H "Authorization: Bearer your-api-key" \
     "http://localhost:8000/v1/my-endpoint/?param=test"

# Check Swagger UI
open http://localhost:8000/docs
```

## Checklist

- [ ] Pydantic response schema with `Field(..., description=...)` on every field
- [ ] Docstring on the endpoint function (shows in Swagger)
- [ ] Appropriate HTTP error codes (400, 404)
- [ ] Auth dependency (`RequireAuth`) unless endpoint is public
- [ ] DB method defined in `base.py` interface
- [ ] DB method implemented in `psql.py` with parameterized queries
- [ ] Query parameters validated (use `ge`, `le`, custom validation)
- [ ] Tested via Swagger UI or curl
