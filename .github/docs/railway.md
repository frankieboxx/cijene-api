# Cijene API — Railway deployment

## Pregled

Aplikacija je deployana na [Railway](https://railway.app/) platformi. Railway automatski gradi i deploya aplikaciju pri svakom pushu na `main` granu (putem GitHub Actions deploja ili Railway GitHub integracije).

---

## Konfiguracija (`railway.toml`)

```toml
[build]
dockerfilePath = "Dockerfile.railway"

[deploy]
healthcheckPath = "/health"
healthcheckTimeout = 300
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 10
```

- **`Dockerfile.railway`** — Docker slika optimizirana za Railway deployment
- **`/health`** — health check endpoint koji Railway koristi za provjeru stanja
- **Restart policy** — servis se automatski restarta na grešku (max 10 puta)

---

## GitHub Actions CI/CD

### CI workflow (`.github/workflows/ci.yml`)

Pokreće se na svakom pushu i pull requestu na `main` granu:

1. Lint (`ruff check`)
2. Provjera stila koda (`ruff format --check`)
3. Provjera tipova (`ty check`)

### Deploy workflow (`.github/workflows/deploy.yml`)

Pokreće se na pushu na `main` granu, **nakon uspješnog CI**:

1. Provjerava kod (`actions/checkout`)
2. Instalira Railway CLI (`npm install -g @railway/cli`)
3. Deploya na Railway (`railway up --service $RAILWAY_SERVICE_ID`)

#### Potrebne GitHub tajne i varijable

| Vrsta       | Naziv                 | Opis                                  |
|-------------|-----------------------|---------------------------------------|
| Secret      | `RAILWAY_TOKEN`       | Railway API token                     |
| Variable    | `RAILWAY_SERVICE_ID`  | ID Railway servisa koji se deploya    |

---

## Postavljanje Railway deploja

### 1. Generiranje Railway API tokena

```bash
railway login
railway tokens create
```

Dodajte token kao GitHub Actions secret pod imenom `RAILWAY_TOKEN`.

### 2. Pronalazak Service ID-a

- Railway projekt ID: `c9eeed53-f5d1-4c5e-9eae-68793b4691c9`
- Crawler servis ID: `74f04ed3-edf6-4293-a47c-82daad7dffa7`

Dodajte service ID kao GitHub Actions varijablu pod imenom `RAILWAY_SERVICE_ID`.

### 3. Environment varijable na Railwayu

Postavite sljedeće environment varijable u Railway dashboard ili putem CLI-a:

```bash
railway variables set DB_DSN=postgresql://...
railway variables set MAILGUN_API_KEY=...
railway variables set MAILGUN_DOMAIN=...
railway variables set REPORT_RECIPIENTS=...
railway variables set ARCHIVE_DIR=/app/output
railway variables set TIMEZONE=Europe/Zagreb
```

---

## Cron job (pipeline)

Crawl → uvoz → email pipeline pokreće se kao Railway cron servis.

### Konfiguracija cron servisa

| Parametar         | Vrijednost                  |
|-------------------|-----------------------------|
| Raspored          | `0 8 * * *` (08:00 svaki dan) |
| Naredba           | `python -m scripts.pipeline` |
| Docker slika      | `Dockerfile.railway`         |

### Ručno pokretanje (Railway CLI)

```bash
# Provjera statusa servisa
railway status

# Pokretanje cron joba ručno
railway run python -m scripts.pipeline --date YYYY-MM-DD

# Pregled logova
railway logs
```

### Provjera rezultata

Pipeline po završetku šalje email izvještaj na adrese definirane u `REPORT_RECIPIENTS`.  
Izvještaj sadrži:
- Broj učitanih prodavaonica, proizvoda i cijena po lancu
- Eventualne greške pri crawlanju ili uvozu
- Ukupno trajanje izvođenja

---

## Docker slike

| Datoteka              | Namjena                                            |
|-----------------------|----------------------------------------------------|
| `Dockerfile`          | Standardna slika za lokalni razvoj i Docker Compose |
| `Dockerfile.crawler`  | Slika za standalone crawler                        |
| `Dockerfile.railway`  | Optimizirana slika za Railway deployment           |

---

## Troubleshooting

### Health check ne prolazi

Provjerite da je servis pokrenut i da `GET /health` vraća `{"status": "healthy"}`.  
Provjerite Railway logove:

```bash
railway logs --tail 100
```

### Crawler nema podataka za određeni datum

Neki lanci ne objavljuju retroaktivne podatke — crawler je date-agnostic.  
Pokrenite pipeline s eksplicitnim datumom:

```bash
railway run python -m scripts.pipeline --date YYYY-MM-DD
```

### Deploy ne uspijeva

1. Provjerite da `RAILWAY_TOKEN` secret postoji u GitHub repository settings
2. Provjerite da `RAILWAY_SERVICE_ID` varijabla postoji u GitHub Actions
3. Pregledajte GitHub Actions log za grešku

### Rollback

Railway automatski čuva povijest deploymenta. Za vraćanje na prethodnu verziju koristite Railway dashboard (Deployments → Rollback).
