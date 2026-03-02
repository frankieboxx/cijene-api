# Cijene API — Dokumentacija web servisa

## Pregled

Web servis je REST API izgrađen s [FastAPI](https://fastapi.tiangolo.com/) frameworkom koji pruža pristup podacima o cijenama proizvoda u hrvatskim trgovačkim lancima.

- **Swagger UI (interaktivna dokumentacija):** `https://api.cijene.dev/docs`
- **OpenAPI schema:** `https://api.cijene.dev/openapi.json`
- **Health check:** `GET /health`

---

## Arhitektura servisa

```
service/
├── main.py          # FastAPI aplikacija, lifespan, CORS, rute
├── config.py        # Settings klasa — konfiguracija iz env varijabli
├── routers/
│   ├── v0.py        # API v0 (starija verzija, kompatibilnost)
│   ├── v1.py        # API v1 — trenutna verzija
│   └── auth.py      # Autentifikacija (Bearer token)
└── db/
    ├── base.py      # Apstraktna Database klasa
    ├── psql.py      # PostgreSQL implementacija (asyncpg)
    ├── models.py    # Dataclass modeli (Chain, Store, Product, Price…)
    ├── import.py    # CLI za uvoz CSV podataka u bazu
    ├── stats.py     # CLI za izračun statistika
    └── enrich.py    # CLI za uvoz pročišćenog kataloga proizvoda
```

---

## Pokretanje servisa

### Lokalno

```bash
uv run -m service.main
```

Servis je dostupan na `http://localhost:8000`.

### Docker

```bash
docker-compose up -d
```

---

## API endpointovi (v1)

Svi endpointovi pod `/v1` zahtijevaju autentifikaciju putem Bearer tokena.

### Lanci

| Metoda | Putanja          | Opis                          |
|--------|------------------|-------------------------------|
| GET    | `/v1/chains/`    | Popis svih dostupnih lanaca   |

### Prodavaonice

| Metoda | Putanja                   | Opis                                              |
|--------|---------------------------|---------------------------------------------------|
| GET    | `/v1/{chain_code}/stores/` | Popis prodavaonica određenog lanca                |
| GET    | `/v1/stores/`             | Pretraživanje prodavaonica (grad, adresa, geo)    |

**Pretraživanje prodavaonica** podržava filtriranje po:
- `chains` — popis kodova lanaca (zarezima odvojeni)
- `city` — naziv grada (case-insensitive)
- `address` — adresa (case-insensitive)
- `lat`, `lon`, `d` — geolokacijska pretraga (u kilometrima, default: 10 km)

### Proizvodi i cijene

| Metoda | Putanja                    | Opis                                                       |
|--------|----------------------------|------------------------------------------------------------|
| GET    | `/v1/products/`            | Pretraživanje proizvoda po nazivu                          |
| GET    | `/v1/products/{ean}/`      | Dohvat podataka i cijena proizvoda po EAN barkodu          |
| GET    | `/v1/prices/`              | Cijene proizvoda po prodavaonicama s filtriranjem          |
| GET    | `/v1/price-check/`         | Provjera cijena po svim lancima (za dani grad/šifru/naziv) |

### Statistike

| Metoda | Putanja              | Opis                                             |
|--------|----------------------|--------------------------------------------------|
| GET    | `/v1/chain-stats/`   | Statistike učitanih podataka po lancu            |

### Slike proizvoda

| Metoda | Putanja                              | Opis                                         |
|--------|--------------------------------------|----------------------------------------------|
| GET    | `/v1/product-image/{chain_product_id}` | JPEG thumbnail slike proizvoda (200×200)   |

---

## Autentifikacija

Endpointovi pod `/v1` koriste HTTP Bearer autentifikaciju. Korisnici se kreiraju direktno u bazi podataka:

```sql
INSERT INTO users (name, api_key, is_active) VALUES ('Korisnik', 'secret-key', TRUE);
```

U zahtjevima koristite:

```
Authorization: Bearer <api_key>
```

---

## Baza podataka

### Uvoz podataka

Nakon što crawler generira ZIP arhivu, podaci se uvoze u bazu:

```bash
uv run -m service.db.import /path/to/YYYY-MM-DD.zip
# ili iz direktorija:
uv run -m service.db.import /path/to/YYYY-MM-DD/
```

Opcije:
- `-s` / `--skip-stats` — preskoči izračun statistika (brži uvoz)
- `-d` / `--debug` — ispis debug informacija

### Izračun statistika

```bash
uv run -m service.db.stats 2024-01-15
uv run -m service.db.stats 2024-01-15 2024-01-16 2024-01-17
```

### Uvoz pročišćenih podataka o proizvodima

```bash
uv run -m service.db.enrich enrichment/products.csv
```

---

## Konfiguracija

Settings se učitavaju iz environment varijabli (putem `python-dotenv`):

| Varijabla              | Opis                              | Default                          |
|------------------------|-----------------------------------|----------------------------------|
| `DB_DSN`               | PostgreSQL connection string      | `postgresql://postgres:postgres@localhost/cijene` |
| `DB_MIN_CONNECTIONS`   | Min. veze u connection poolu      | `5`                              |
| `DB_MAX_CONNECTIONS`   | Max. veze u connection poolu      | `20`                             |
| `PORT`                 | Port servisa                      | `8000`                           |
| `HOST`                 | Bind adresa                       | `0.0.0.0`                        |
| `DEBUG`                | Debug mod (hot reload)            | `false`                          |
| `ROOT_PATH`            | Root path (za reverse proxy)      | `/`                              |
| `BASE_URL`             | Javni URL servisa                 | `https://api.cijene.dev`         |
| `TIMEZONE`             | Vremenska zona                    | `Europe/Zagreb`                  |
| `MAILGUN_API_KEY`      | Mailgun API ključ                 | —                                |
| `MAILGUN_DOMAIN`       | Mailgun domena                    | —                                |
| `REPORT_RECIPIENTS`    | Primatelji email izvještaja       | —                                |

---

## Modeli podataka

Ključni dataclass modeli definirani u `service/db/models.py`:

| Model              | Opis                                          |
|--------------------|-----------------------------------------------|
| `Chain`            | Trgovački lanac (`code`)                      |
| `Store`            | Prodavaonica (lanac, adresa, grad, geolokacija) |
| `Product`          | Proizvod (EAN, naziv, marka, količina)        |
| `ChainProduct`     | Proizvod specifičan za lanac (šifra, naziv)  |
| `Price`            | Cijena (redovna, akcijska, cijena po jed.)   |
| `StorePrice`       | Cijena u prodavaonici (s informacijom o lancu) |
| `ChainStats`       | Statistike po lancu i datumu                 |

---

## Zdravlje sustava

```
GET /health
→ {"status": "healthy"}
```

```
GET /
→ {"name": "Cijene API", "version": "...", "docs": "...", "health": "..."}
```
