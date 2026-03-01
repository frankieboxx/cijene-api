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

## Priority Order

1. **Steps 1-2**: Fix normalization + fuzzy matching (unblocks everything)
2. **Step 3**: Updated SQL query
3. **Steps 4-6**: New analysis features (independent of each other)
4. **Step 7**: Email template (depends on 4-6)
5. **Steps 8-11**: Deploy & harden
