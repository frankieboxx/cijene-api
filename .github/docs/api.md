# Cijene API — REST API Documentation

## Base URL

- **Production**: `https://api-production-37dc.up.railway.app`
- **Local development**: `http://localhost:8000`
- **Swagger UI**: `{base_url}/docs`

## Versioning

| Version | Prefix | Status |
|---------|--------|--------|
| v0 | `/v0` | Legacy, maintained |
| v1 | `/v1` | Current, recommended |

## Authentication

All `/v1` endpoints require Bearer token authentication.

```
Authorization: Bearer <api_key>
```

API keys are stored in the `users` table:

```sql
INSERT INTO users (name, api_key, is_active) VALUES ('MyApp', 'my-secret-key', TRUE);
```

**Cache**: Successful auth results are cached for 60 minutes; invalid tokens for 60 seconds.

## Endpoints

### Service Status

#### `GET /health`
Health check endpoint. No authentication required.

**Response**:
```json
{ "status": "healthy" }
```

#### `GET /`
API info endpoint. No authentication required.

**Response**:
```json
{
  "name": "Cijene API",
  "version": "0.1.0",
  "description": "Croatian grocery price tracking service",
  "docs": "https://api-production-37dc.up.railway.app/docs",
  "health": "https://api-production-37dc.up.railway.app/health"
}
```

---

### v1 Endpoints

All v1 endpoints require authentication (`Authorization: Bearer <api_key>`).

#### `GET /v1/chains/`
List all available retail chains.

**Response**:
```json
{
  "chains": ["konzum", "lidl", "tommy", "studenac", "kaufland", "dm", "metro", "ribola", "roto"]
}
```

---

#### `GET /v1/{chain_code}/stores/`
List all stores (locations) for a specific chain.

**Path parameters**:
- `chain_code` — chain identifier (e.g., `konzum`, `metro`)

**Response**:
```json
{
  "stores": [
    {
      "chain_code": "konzum",
      "code": "001",
      "type": "supermarket",
      "address": "Ilica 123",
      "city": "Zagreb",
      "zipcode": "10000",
      "lat": 45.813,
      "lon": 15.977,
      "phone": null
    }
  ]
}
```

**Errors**:
- `404` — No chain found with the given code

---

#### `GET /v1/stores/`
Search stores with optional filters.

**Query parameters**:
| Parameter | Type | Description |
|-----------|------|-------------|
| `chains` | string | Comma-separated chain codes |
| `city` | string | Case-insensitive city substring match |
| `address` | string | Case-insensitive address substring match |
| `lat` | float | Latitude for geo search |
| `lon` | float | Longitude for geo search |
| `d` | float | Search radius in km (default: 10.0) |

**Note**: `lat` and `lon` must be provided together.

---

#### `GET /v1/products/{ean}/`
Get product data and prices by EAN barcode.

**Path parameters**:
- `ean` — EAN barcode (or `chain:<product_code>` for chain-specific codes)

**Query parameters**:
| Parameter | Type | Description |
|-----------|------|-------------|
| `date` | date | Price date `YYYY-MM-DD` (default: today) |
| `chains` | string | Comma-separated chain codes to filter |

**Response**:
```json
{
  "ean": "3800267020123",
  "brand": "Tommy",
  "name": "Mlijeko 2.8% 1L",
  "quantity": "1",
  "unit": "L",
  "chains": [
    {
      "chain": "tommy",
      "code": "TOM-001",
      "name": "Mlijeko polutrajno 2,8% m.m.",
      "brand": "Tommy",
      "category": "Mlijeko i mliječni proizvodi",
      "unit": "L",
      "quantity": "1",
      "min_price": 1.09,
      "max_price": 1.19,
      "avg_price": 1.14,
      "price_date": "2025-06-01"
    }
  ]
}
```

**Errors**:
- `404` — Product not found or no price data for given filters

---

#### `GET /v1/products/`
Search for products by name.

**Query parameters**:
| Parameter | Type | Description |
|-----------|------|-------------|
| `q` | string | **Required.** Search query |
| `date` | date | Price date `YYYY-MM-DD` (default: today) |
| `chains` | string | Comma-separated chain codes to filter |
| `fuzzy` | bool | Enable fuzzy matching (default: `false`) |
| `limit` | int | Max results 1–100 (default: 20) |

**Response**:
```json
{
  "products": [ /* same as GET /v1/products/{ean}/ */ ]
}
```

---

#### `GET /v1/prices/`
Get per-store prices for one or more products.

**Query parameters**:
| Parameter | Type | Description |
|-----------|------|-------------|
| `eans` | string | **Required.** Comma-separated EAN barcodes |
| `chains` | string | Comma-separated chain codes to filter |
| `city` | string | City name filter |
| `address` | string | Address filter |
| `lat` | float | Latitude for geo search |
| `lon` | float | Longitude |
| `d` | float | Radius in km (default: 10.0) |

**Response**:
```json
{
  "store_prices": [
    {
      "chain": "tommy",
      "ean": "3800267020123",
      "price_date": "2025-06-01",
      "regular_price": 1.09,
      "special_price": null,
      "unit_price": 1.09,
      "best_price_30": 1.05,
      "anchor_price": 1.09,
      "store": {
        "chain_id": 3,
        "code": "TOMMY-DU-01",
        "type": "supermarket",
        "address": "Ante Starčevića 5",
        "city": "Dubrovnik",
        "zipcode": "20000",
        "lat": 42.65,
        "lon": 18.09,
        "phone": null
      }
    }
  ]
}
```

---

#### `GET /v1/chain-stats/`
Return the latest data statistics per chain.

**Response**:
```json
{
  "chain_stats": [
    {
      "chain_code": "konzum",
      "price_date": "2025-06-01",
      "price_count": 125000,
      "store_count": 210,
      "created_at": "2025-06-01T09:15:00Z"
    }
  ]
}
```

---

#### `GET /v1/price-check/`
Check current prices for a product across chains in a specific city.

**Query parameters**:
| Parameter | Type | Description |
|-----------|------|-------------|
| `code` | string | Product code (Metro sifra, Konzum code, etc.) |
| `name` | string | Fuzzy product name search |
| `chain` | string | Filter by chain code |
| `city` | string | City filter (default: `Dubrovnik`) |

**Note**: Either `code` or `name` must be provided.

**Response**:
```json
{
  "query": { "code": "224155", "name": null, "chain": null, "city": "Dubrovnik" },
  "product": {
    "name": "180G ARO TEK.JOGURT 2.8%",
    "code": "224155",
    "category": "Mliječni proizvodi"
  },
  "prices": [
    {
      "chain": "metro",
      "store": "Dubrovnik",
      "price": 0.30,
      "unit_price": 1.67,
      "unit": "€/kg",
      "date": "2025-06-01",
      "quantity": "180 G"
    }
  ],
  "cheapest": {
    "chain": "metro",
    "unit_price": 1.67,
    "savings_vs_most_expensive": "33.2%"
  }
}
```

---

#### `GET /v1/product-image/{chain_product_id}`
Get product thumbnail image (JPEG 200×200).

**Path parameters**:
- `chain_product_id` — integer ID of the chain product

**Response**: `image/jpeg` binary data

**Errors**:
- `404` — No image stored for this product

---

## Error Responses

All errors follow the standard format:

```json
{ "detail": "Error message" }
```

| Code | Meaning |
|------|---------|
| 400 | Bad request (invalid parameters) |
| 403 | Authentication failed / unknown API key |
| 404 | Resource not found |

## Price Fields Reference

| Field | Description |
|-------|-------------|
| `regular_price` | Standard shelf price |
| `special_price` | Promotional/discounted price |
| `unit_price` | Price per kg or per litre |
| `best_price_30` | Lowest price in the past 30 days |
| `anchor_price` | Reference price as of 2 May 2025 (NN 75/2025) |
