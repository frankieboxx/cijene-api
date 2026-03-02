# Cijene API — Dokumentacija aplikacije

## Pregled

**Cijene API** je open-source servis za prikupljanje i objavljivanje javnih podataka o cijenama proizvoda u hrvatskim trgovačkim lancima, temeljen na Odluci o objavi cjenika (NN 75/2025).

Aplikacija se sastoji od dva glavna dijela:

| Komponenta | Modul     | Opis                                                      |
|------------|-----------|-----------------------------------------------------------|
| Crawler    | `crawler` | Preuzima podatke o cijenama s web stranica trgovačkih lanaca |
| Web servis | `service` | REST API koji omogućava pristup podacima o cijenama        |

---

## Arhitektura

```
cijene-api/
├── crawler/                # Crawler za prikupljanje podataka
│   ├── crawl.py            # Orchestracija crawlanja više lanaca
│   ├── cli/crawl.py        # CLI sučelje
│   └── store/              # Implementacije pojedinih lanaca
├── service/                # FastAPI web servis
│   ├── main.py             # Aplikacijska inicijalizacija
│   ├── config.py           # Konfiguracija iz env varijabli
│   ├── routers/            # API rute (v0, v1, auth)
│   └── db/                 # Sloj baze podataka
├── scripts/                # Skripte za pipeline, izvještaje i sl.
│   ├── pipeline.py         # Crawl → uvoz → email pipeline
│   ├── report.py           # Generiranje i slanje email izvještaja
│   └── price_compare.py    # Usporedba cijena (Atrium integracija)
├── enrichment/             # Dodatni podaci o proizvodima
│   └── products.csv        # Pročišćeni katalog ~30k proizvoda
├── railway.toml            # Konfiguracija Railway deploymenta
├── Dockerfile              # Docker slika za web servis
├── Dockerfile.crawler      # Docker slika za crawler
├── Dockerfile.railway      # Docker slika za Railway deployment
└── docker-compose.yml      # Docker Compose konfiguracija
```

---

## Podržani trgovački lanci

Sljedeći lanci su podržani (implementirani crawleri označeni su s ✅):

| Lanac           | Crawler implementiran |
|-----------------|-----------------------|
| Konzum          | ✅                    |
| Lidl            | ✅                    |
| Tommy           | ✅                    |
| Studenac        | ✅                    |
| Kaufland        | ✅                    |
| dm              | ✅                    |
| Metro           | ✅                    |
| Ribola          | ✅                    |
| Roto            | ✅                    |
| Plodine         |                       |
| Spar            |                       |
| Eurospin        |                       |
| KTC             |                       |
| Trgocentar      |                       |
| Žabac           |                       |
| Vrutak          |                       |
| NTL             |                       |
| Boso            |                       |
| Brodokomerc     |                       |
| Lorenco         |                       |
| Trgovina Krk    |                       |

---

## Tehnološki stack

| Tehnologija   | Svrha                                      |
|---------------|--------------------------------------------|
| Python 3.13   | Programski jezik                           |
| FastAPI       | REST API framework                         |
| PostgreSQL    | Relacijska baza podataka                   |
| asyncpg       | Asinkroni PostgreSQL driver                |
| httpx         | HTTP klijent za crawlanje                  |
| BeautifulSoup | HTML parsing                               |
| Pydantic      | Validacija podataka i sheme                |
| uvicorn       | ASGI server                                |
| Railway       | Cloud platforma za deployment              |
| Docker        | Containerizacija                           |
| uv            | Upravljanje Python ovisnostima             |
| Mailgun       | Slanje email izvještaja                    |

---

## Tok podataka

```
Trgovački lanci
      │
      ▼
  [Crawler]
  Preuzima CSV/XML/ZIP datoteke
      │
      ▼
  [Output]
  Generira standardizirane CSV datoteke
  i ZIP arhivu u output direktoriju
      │
      ▼
  [Uvoz u bazu] (service.db.import)
  Uvozi CSV podatke u PostgreSQL
      │
      ▼
  [Web servis] (service.main)
  REST API — pretraživanje i dohvat cijena
```

---

## Instalacija i pokretanje

### Lokalni razvoj

```bash
git clone https://github.com/frankieboxx/cijene-api.git
cd cijene-api
uv sync --dev
cp .env.example .env
# Uredite .env prema potrebi
uv run -m service.main
```

### Docker (preporučeno)

```bash
cp .env.docker.example .env
docker-compose up -d
```

Detalje pogledajte u [DOCKER.md](../../DOCKER.md).

---

## Pipeline (Crawl → Uvoz → Izvještaj)

Kompletan pipeline (crawlanje + uvoz + email) pokreće se s:

```bash
uv run -m scripts.pipeline [--date YYYY-MM-DD] [--chains chain1,chain2] [--skip-email]
```

Pipeline:
1. Crawla zadane lance (ili sve)
2. Uvozi podatke u PostgreSQL
3. Šalje email izvještaj o rezultatima

---

## Konfiguracija

Aplikacija se konfigurira putem environment varijabli (`.env` datoteka). Ključne varijable:

| Varijabla              | Opis                              | Default                          |
|------------------------|-----------------------------------|----------------------------------|
| `DB_DSN`               | PostgreSQL connection string      | `postgresql://postgres:postgres@localhost/cijene` |
| `PORT`                 | Port na kojem servis sluša        | `8000`                           |
| `DEBUG`                | Debug mod                         | `false`                          |
| `TIMEZONE`             | Vremenska zona                    | `Europe/Zagreb`                  |
| `BASE_URL`             | Javni URL servisa                 | `https://api.cijene.dev`         |
| `MAILGUN_API_KEY`      | Mailgun API ključ                 | —                                |
| `MAILGUN_DOMAIN`       | Mailgun domena                    | —                                |
| `REPORT_RECIPIENTS`    | Email adrese primatelja izvještaja | —                                |

Za potpuni popis varijabli pogledajte `.env.example`.

---

## Licenca

Aplikacija je licencirana pod [AGPL-3 licencom](../../LICENSE).

Prikupljeni podaci o cijenama su javni (NN 75/2025).  
Pročišćeni CSV katalog proizvoda (`enrichment/products.csv`) dostupan je pod [CC BY-NC-SA licencom](https://creativecommons.org/licenses/by-nc-sa/4.0/).
