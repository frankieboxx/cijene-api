# Atrium Price Compare v3 — Implementation Plan

## Context

Atrium restaurant in Dubrovnik buys from Metro Cash & Carry. We compare their purchase prices against Tommy, Studenac, Ribola, Konzum, Lidl, and Metro retail prices crawled daily into cijene-api DB.

### Two Databases

| DB | DSN env var | Purpose |
|----|-------------|---------|
| Atrium ERP | `ATRIUM_DATABASE_URL` | Purchase invoices (`troskovi` + `troskovi_detalji`) |
| cijene-api | `DB_DSN` | Crawled retail prices (`prices`, `chain_products`, `stores`) |

### Atrium Data Available

| Field | Coverage | Notes |
|-------|----------|-------|
| `jedinica_mjere` | 100% | H87=164, EA=25, KGM=12, C62=9 |
| `sifra` (Metro code) | 100% Metro items | Maps exactly to `chain_products.code` |
| `opis` | 100% | Contains weight: "180G ARO TEK.JOGURT", "1KG CHEDDAR", "500G MC EDAM" |
| `kolicina` | 100% | Purchased quantity (pieces for H87, kg for KGM) |
| `jedinicna_cijena` | 100% | Per-piece (H87/EA/C62), per-kg (KGM) |
| `eanGtinId` | 0% — all NULL | Cannot use barcode matching |

### Cijene-API Data Available

- `prices.unit_price` — retailer-declared €/kg or €/L (most reliable)
- `chain_products.quantity` — raw string ("10000 G", "0.20 kg", etc.), inconsistent across chains
- `chain_products.category` — product category from retailer
- Dubrovnik stores: IDs 11-19, 61 (9 stores across 6 chains)
- Chains: tommy(2), studenac(3), ribola(4), konzum(5), lidl(6), metro(7)

---

## Step 1 — Fix Unit Price Normalization (CRITICAL)

**Problem**: v2 extracted weight from Metro product names ("30G" → 0.03kg) and divided per-piece price, producing absurd values (€1,085/kg).

**Solution**: Use `prices.unit_price` from cijene-api DB as primary comparison metric.

### Atrium side (compute €/kg or €/L):

- **KGM items**: `jedinicna_cijena` IS already €/kg → use directly
- **H87 items with weight in `opis`**: Parse weight prefix ("180G" → 0.18kg, "1KG" → 1.0kg, "1L" → 1L), then `jedinicna_cijena / weight_kg` = €/kg
- **H87 items without weight**: Match via Metro `sifra` → get Metro's own `unit_price` from cijene-api DB as both Atrium AND retail reference
- **Special formats**: "30/1" (packs of 30) — skip or handle manually

### Cijene-API side:

- Use `prices.unit_price` directly (€/kg or €/L, declared by retailer)
- Fallback: if `unit_price == price` (base.py fallback marker), try computing from `chain_products.quantity`

### Weight parser (`parse_atrium_weight`):

```python
import re

def parse_atrium_weight(opis: str) -> tuple[float, str] | None:
    """Extract weight/volume from Metro product name prefix.
    Returns (value_in_base_unit, unit_type) or None.
    Examples: '180G ...' → (0.18, 'kg'), '1KG ...' → (1.0, 'kg'), '1L ...' → (1.0, 'L')
    """
    m = re.match(r'^(\d+[,.]?\d*)\s*(G|KG|ML|L|CL|DL)\b', opis, re.IGNORECASE)
    if not m:
        return None
    val = float(m.group(1).replace(',', '.'))
    unit = m.group(2).upper()
    conversions = {'G': (0.001, 'kg'), 'KG': (1.0, 'kg'), 'ML': (0.001, 'L'),
                   'CL': (0.01, 'L'), 'DL': (0.1, 'L'), 'L': (1.0, 'L')}
    factor, unit_type = conversions[unit]
    return (val * factor, unit_type)
```

---

## Step 2 — Fix Fuzzy Matching

**Problem**: False positives — "PAŠTETA KOKOŠJA" matching "PRŠUT GAVRILOVIĆ" at 80%.

**Changes**:
- Switch from `token_sort_ratio` → `token_set_ratio` (better for subset matching)
- Raise `FUZZY_THRESHOLD` to 80 (from 65)
- Add **sanity check**: skip matches where savings > 90% (likely wrong match)
- Add **unit_type filter**: only compare kg↔kg, L↔L; never kg↔L or kg↔piece
- Prefer matches from same `category` when available

---

## Step 3 — Updated SQL Query (cijene-api side)

```sql
SELECT
    p.price,
    p.unit_price,
    p.date,
    cp.name,
    cp.code,
    cp.quantity,
    cp.unit,
    cp.category,
    c.name AS chain_name,
    s.name AS store_name,
    s.id AS store_id
FROM prices p
JOIN chain_products cp ON p.chain_product_id = cp.id
JOIN stores s ON p.store_id = s.id
JOIN chains c ON s.chain_id = c.id
WHERE s.id = ANY($1)                          -- Dubrovnik store IDs
  AND p.date >= CURRENT_DATE - INTERVAL '3 days'
  AND p.unit_price > 0
ORDER BY cp.code, p.unit_price ASC
```

---

## Step 4 — Top 10 Najskupljih Pozicija

Query Atrium DB for highest-spend items (last 30 days):

```sql
SELECT td.opis,
       SUM(td.ukupno) AS total_spend,
       SUM(td.kolicina) AS total_qty,
       td.jedinica_mjere,
       AVG(td.jedinicna_cijena) AS avg_price,
       COUNT(*) AS purchase_count
FROM troskovi_detalji td
JOIN troskovi t ON td.trosak_id = t.id
WHERE t.datum >= NOW() - INTERVAL '30 days'
GROUP BY td.opis, td.jedinica_mjere
ORDER BY total_spend DESC
LIMIT 10
```

Display: product name, total €, avg price, times purchased, and best alternative if found.

---

## Step 5 — Weekly Price Trends

Compare this week's purchases vs last week (by product):

```sql
SELECT td.opis, td.sifra,
       CASE WHEN t.datum >= NOW() - INTERVAL '7 days' THEN 'this_week' ELSE 'last_week' END AS period,
       AVG(td.jedinicna_cijena) AS avg_price,
       SUM(td.kolicina) AS total_qty
FROM troskovi_detalji td
JOIN troskovi t ON td.trosak_id = t.id
WHERE t.datum >= NOW() - INTERVAL '14 days'
GROUP BY td.opis, td.sifra, period
ORDER BY td.opis
```

Show ↑/↓ arrows and percentage change in email.

---

## Step 6 — Category Breakdown (Keyword-Based)

NO AI — use keyword dictionary:

```python
CATEGORIES = {
    "Mliječni proizvodi": ["jogurt", "sir", "mlijeko", "mozzarella", "cheddar", "edam", "mascarpone", "vrhnje", "maslac"],
    "Meso i mesni proizvodi": ["pršut", "salama", "hrenovka", "kobasica", "debrecinka", "piletina", "svinjetina", "šunka", "losos", "tuna"],
    "Voće i povrće": ["banana", "paprika", "patlidžan", "rajčica", "tikvice", "luk", "grožđe", "jabuka", "limun"],
    "Pekarski proizvodi": ["brašno", "kruh", "pecivo", "tortilla"],
    "Jaja": ["jaja"],
    "Pića": ["coca", "fanta", "sprite", "juice", "sok", "voda", "pivo", "vino"],
    "Slastice": ["bomboni", "čokolada", "grickalice", "keks"],
    "Začini i umaci": ["ketchup", "majoneza", "senf", "sol", "papar", "origano"],
    "Kemija i potrošni": ["deterdžent", "sapun", "salveta", "folija", "vrećica"],
}
```

Aggregate: total spend per category, % of total, top item in each.

---

## Step 7 — MJML Email Template Update

### Sections (in order):
1. **Header** — "Dnevni izvještaj cijena — Atrium" + date
2. **Sažetak** — Total items matched, total potential savings €, top saving
3. **Top 10 najskupljih** — Table: rank, product, total €, alternative, potential saving
4. **Tjedni trend** — Table: product, last week avg, this week avg, ↑↓ %
5. **Kategorije** — Bar chart or table: category, total €, % share
6. **Detaljna usporedba** — Full comparison table (current v1 content, fixed)
7. **Footer** — Generated timestamp, link to dashboard (future)

### Design:
- Brand color: `#1a73e8`
- Green for savings, red for price increases
- Responsive MJML, tested in Gmail/Outlook

---

## Step 8 — Cron Configuration

### Option A — Separate Railway cron service
- Script: `scripts/price_compare.py`
- Cron: `0 9 * * *` (09:00, after 08:00 crawl)
- Env vars needed: `DB_DSN`, `ATRIUM_DATABASE_URL`, `MAILGUN_API_KEY`, `MAILGUN_DOMAIN`, `REPORT_RECIPIENTS`

### Option B — Add to existing crawl pipeline
- Append to `crawl-and-import.sh` after crawler finishes
- Pro: single service; Con: couples concerns

**Decision**: Option A (separate cron) — preferred for isolation.

---

## Step 9 — Future Supplier Support

Currently only Metro (`sifra` exact match). Future plan:
- **With sifra/EAN**: Direct code match (any supplier using UBL e-invoicing)
- **Without codes**: Fuzzy name match only (lower confidence)
- Schema: add `supplier` field to comparison logic
- The `troskovi.opis` field identifies supplier (e.g., "METRO Cash & Carry d.o.o.")

---

## Step 10 — Sanity Checks & Edge Cases

- [ ] Skip items with `kolicina = 0` or `jedinicna_cijena = 0` (exists in DB!)
- [ ] Skip items where parsed weight is implausible (> 50kg per piece)
- [ ] Skip matches with > 90% savings (likely false positive)
- [ ] Handle "30/1" pack formats (jaja) — extract pack size, compute per-unit
- [ ] Handle litre vs kg — never compare L↔kg items
- [ ] Log unmatched items for manual review

---

## Step 11 — Testing & Deployment

1. Run `--skip-email` locally, verify output makes sense
2. Spot-check top 5 matches manually (open Metro and chain websites)
3. Send test email to `franoglobal@gmail.com`
4. Commit, push to `main`
5. Railway auto-deploys — add cron schedule `0 9 * * *`
6. Monitor first 3 days of automated reports

---

## File Structure

```
scripts/
  price_compare.py    # Main script (rewrite v3)
  __init__.py          # Package marker
service/
  config.py            # ATRIUM_DATABASE_URL setting (already added)
pyproject.toml         # rapidfuzz dependency (already added)
```

## Step 12 — Price Check API Endpoint

Add a REST API endpoint to cijene-api that accepts a product code (`sifra`) and chain name, and returns current prices across Dubrovnik stores.

### Endpoint:

```
GET /api/v1/price-check?code={sifra}&chain={chain_code}
```

### Parameters:
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | string | yes | Product code (Metro `sifra`, Konzum code, etc.) |
| `chain` | string | no | Chain filter: `metro`, `konzum`, `tommy`, etc. If omitted, search all chains |
| `city` | string | no | City filter (default: `Dubrovnik`) |

### Response:

```json
{
  "query": { "code": "224155", "chain": null, "city": "Dubrovnik" },
  "product": {
    "name": "180G ARO TEK.JOGURT 2,8%",
    "code": "224155",
    "category": "Mliječni proizvodi"
  },
  "prices": [
    {
      "chain": "metro",
      "store": "Metro Dubrovnik",
      "price": 0.30,
      "unit_price": 1.67,
      "unit": "€/kg",
      "date": "2026-03-01",
      "quantity": "180 G"
    },
    {
      "chain": "konzum",
      "store": "Konzum Dubrovnik",
      "price": 0.45,
      "unit_price": 2.50,
      "unit": "€/kg",
      "date": "2026-03-01",
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

### Implementation:
- Add to `service/routers/v1.py` (or new `service/routers/price_check.py`)
- Reuse existing `db` connection from `settings.get_db()`
- SQL: lookup `chain_products.code` → join `prices` → filter by store city/chain
- Requires auth (existing `RequireAuth` dependency)
- Also support fuzzy name search: `GET /api/v1/price-check?name=jogurt&chain=metro`

### Use cases:
- Atrium ERP integration — when creating purchase order, check real-time prices
- Manual spot-check from terminal: `curl -H "Authorization: Bearer $TOKEN" "https://api.../v1/price-check?code=224155"`
- Future: Atrium UI "check alternatives" button

---

## Step 13 — GitHub Actions Deploy Workflow for Railway

Create `.github/workflows/deploy.yml` — triggered on push to `main` (after CI passes), deploys to Railway.

### Requirements:
- Railway project ID: `c9eeed53-f5d1-4c5e-9eae-68793b4691c9`
- Crawler service ID: `74f04ed3-edf6-4293-a47c-82daad7dffa7`
- Needs `RAILWAY_TOKEN` secret in GitHub repo settings
- Should only deploy when CI build succeeds (use `needs: build`)

### Workflow:

```yaml
name: Deploy to Railway

on:
  push:
    branches: ["main"]

jobs:
  build:
    uses: ./.github/workflows/ci.yml  # reuse existing CI

  deploy:
    needs: build
    runs-on: ubuntu-24.04
    if: github.ref == 'refs/heads/main'

    steps:
      - uses: actions/checkout@v4

      - name: Install Railway CLI
        run: npm install -g @railway/cli

      - name: Deploy to Railway
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
        run: railway up --service ${{ vars.RAILWAY_SERVICE_ID || '74f04ed3-edf6-4293-a47c-82daad7dffa7' }}
```

### Setup steps:
1. Generate Railway API token: `railway login` → `railway tokens create`
2. Add `RAILWAY_TOKEN` as GitHub Actions secret in repo settings
3. Optionally add `RAILWAY_SERVICE_ID` as GitHub Actions variable (for flexibility)
4. Existing CI workflow (`ci.yml`) stays as-is for lint/type checks
5. Deploy workflow triggers only on `main` push, after CI passes

### Notes:
- Railway also supports auto-deploy from GitHub (already connected) — this workflow adds explicit control + gating behind CI
- If Railway auto-deploy is sufficient, this step is optional but recommended for visibility and CI gating

---

## Step 14 — Product Image Crawler & Thumbnail Storage

Crawl product images from retailer websites for products that exist in the Atrium DB, convert to 200×200 thumbnails, and store in the cijene-api (`DB_DSN`) database with product code and EAN.

### Scope:
- **Only** crawl images for products that appear in Atrium `troskovi_detalji` (matched by `sifra` or fuzzy name)
- Sources: Metro, Konzum, Tommy, Studenac, Lidl, Ribola (each has different image URL patterns)
- Store as JPEG thumbnail (200×200, quality 85) in `product_images` table

### Image URL patterns per chain:

| Chain | Pattern | Source |
|-------|---------|--------|
| Metro | `https://metrocjenik.com.hr/images/{sifra}.jpg` or scrape from product page | CSV has SIFRA |
| Konzum | `https://www.konzum.hr/image/{product_id}` | Product page scrape |
| Tommy | Product page scrape via product code | HTML product detail |
| Studenac | Product page scrape | HTML product detail |
| Lidl | Product page scrape (`lidl.hr`) | HTML product detail |
| Ribola | Product page scrape | HTML product detail |

> **Note**: Exact image URL patterns need to be verified per chain website. Some may require headless browser (playwright) for JS-rendered pages.

### DB Model:

New table `product_images` in `DB_DSN` database:

```sql
CREATE TABLE IF NOT EXISTS product_images (
    id SERIAL PRIMARY KEY,
    chain_product_id INTEGER NOT NULL REFERENCES chain_products (id),
    ean VARCHAR(50),
    image_data BYTEA NOT NULL,
    image_format VARCHAR(10) NOT NULL DEFAULT 'jpeg',
    width INTEGER NOT NULL DEFAULT 200,
    height INTEGER NOT NULL DEFAULT 200,
    source_url TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (chain_product_id)
);

CREATE INDEX IF NOT EXISTS idx_product_images_ean ON product_images (ean);
```

### Implementation:

1. **Script**: `scripts/crawl_images.py`
2. **Flow**:
   - Query Atrium DB → get all unique `sifra` values from `troskovi_detalji`
   - For each `sifra`, find matching `chain_products` in cijene-api DB (across all chains)
   - For each matched `chain_product`, check if image already exists in `product_images`
   - If not, crawl the product page, extract `<img>` tag with product photo
   - Download image, resize to 200×200 with Pillow, convert to JPEG
   - Insert into `product_images` with `chain_product_id`, EAN (from `chain_products` → `products.ean`), and thumbnail bytes
3. **Dependencies**: `Pillow>=10.0`, `httpx` (already used), optionally `playwright` for JS-heavy sites
4. **Rate limiting**: 1 req/sec per domain, respect robots.txt
5. **Cron**: Run weekly (not daily — images don't change often): `0 10 * * 0` (Sundays 10:00)

### API endpoint (optional, extend Step 12):

```
GET /api/v1/product-image/{chain_product_id}
→ Returns image/jpeg (200×200 thumbnail)
```

### Use cases:
- Atrium ERP — display product images in purchase comparison UI
- Price compare email — embed thumbnails for top items (base64 inline)
- Future dashboard — visual product catalog

**Files**: `scripts/crawl_images.py`, `service/db/psql.sql` (migration), `service/routers/v1.py` (optional API)
**Dependencies**: `Pillow>=10.0`
**Priority**: P3

---

## Priority Order

| Priority | Steps | Issues | Description |
|----------|-------|--------|-------------|
| P0 | 1-2 | #72, #73 | Fix normalization + fuzzy matching (unblocks everything) |
| P1 | 3 | #74 | Updated SQL query |
| P2 | 4-6 | #75, #76, #77 | New analysis features (independent of each other) |
| P2 | 7 | #78 | Email template (depends on 4-6) |
| P2 | 12 | #83 | Price check API endpoint |
| P3 | 8-11 | #79, #80, #81, #82 | Deploy & harden |
| P3 | 13 | #84 | Railway deploy workflow |
| P3 | 14 | #85 | Product image crawler & thumbnail storage |
