# Cijene API — Project Overview

## Purpose

Cijene API is a Croatian grocery price tracking service. It collects publicly available product pricing data from major Croatian retail chains (mandated by law under NN 75/2025) and exposes it through a REST API.

## Supported Retail Chains

| Chain | Code | Notes |
|-------|------|-------|
| Konzum | `konzum` | Index-based CSV crawler |
| Lidl | `lidl` | ZIP-archive CSV crawler |
| Plodine | `plodine` | ZIP-archive CSV crawler |
| Spar | `spar` | API-based JSON/CSV crawler |
| Tommy | `tommy` | API-based JSON/CSV crawler |
| Studenac | `studenac` | ZIP-archive XML crawler |
| Kaufland | `kaufland` | Index-based CSV crawler |
| Eurospin | `eurospin` | Index-based CSV crawler |
| dm | `dm` | Single-file Excel crawler |
| KTC | `ktc` | Index-based CSV crawler |
| Metro | `metro` | Index-based CSV crawler |
| Trgocentar | `trgocentar` | Index-based CSV crawler |
| Žabac | `zabac` | Date-agnostic CSV crawler |
| Vrutak | `vrutak` | Index-based CSV crawler |
| Ribola | `ribola` | ZIP-archive XML crawler |
| NTL | `ntl` | Index-based CSV crawler |
| Boso | `boso` | Index-based CSV crawler |
| Brodokomerc | `brodokomerc` | Index-based CSV crawler |
| Lorenco | `lorenco` | Index-based CSV crawler |
| Roto | `roto` | ZIP-archive CSV crawler |
| Trgovina Krk | `krk` | Index-based CSV crawler |

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          cijene-api                             │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │   Crawler    │───▶│  PostgreSQL  │◀───│   Web Service   │  │
│  │   Service    │    │   Database   │    │  (FastAPI/REST) │  │
│  └──────────────┘    └──────────────┘    └──────────────────┘  │
│         │                                        │              │
│         ▼                                        ▼              │
│  ┌──────────────┐                      ┌──────────────────┐    │
│  │  CSV / ZIP   │                      │   API Clients    │    │
│  │   Output     │                      │  (authenticated) │    │
│  └──────────────┘                      └──────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

## Two Main Components

### 1. Crawler (`crawler/`)

Python module that downloads, parses, and saves price data from retail chain websites.

- **Entry point**: `python -m crawler.cli.crawl`
- **Output**: Per-chain CSV files + ZIP archive in a date-named folder
- **Scheduling**: Run daily as a cron job or Railway cron service
- **Architecture**: Each store has its own crawler class inheriting from `BaseCrawler`

### 2. Web Service (`service/`)

FastAPI application serving the REST API backed by PostgreSQL.

- **Entry point**: `python -m service.main` → `http://localhost:8000`
- **Swagger docs**: `http://localhost:8000/docs`
- **Authentication**: Bearer token (API key)
- **Database**: PostgreSQL, accessed via `asyncpg`

## Repository Structure

```
cijene-api/
├── crawler/               # Price data crawler
│   ├── cli/crawl.py       # CLI entry point
│   ├── crawl.py           # Orchestration logic
│   └── store/             # Per-chain crawler implementations
│       ├── base.py        # BaseCrawler abstract class
│       ├── models.py      # Product / Store data models
│       ├── output.py      # CSV and ZIP output
│       └── *.py           # Individual store crawlers
├── service/               # FastAPI web service
│   ├── main.py            # FastAPI app + startup
│   ├── config.py          # Settings from environment variables
│   ├── routers/           # API endpoint handlers
│   │   ├── v0.py          # Legacy API (v0)
│   │   ├── v1.py          # Current API (v1)
│   │   └── auth.py        # Bearer token authentication
│   └── db/                # Database layer
│       ├── base.py        # Database abstraction class
│       ├── models.py      # DB data models (dataclasses)
│       ├── psql.py        # PostgreSQL implementation
│       ├── import.py      # CSV → DB importer
│       ├── stats.py       # Statistics calculator
│       ├── enrich.py      # Product enrichment importer
│       └── psql.sql       # Schema DDL
├── enrichment/            # Curated product data (CSV)
├── scripts/               # Utility scripts (price compare, etc.)
├── docs/                  # Technical documentation
├── .github/               # GitHub configuration, docs, skills, MCP
├── Dockerfile             # API service image
├── Dockerfile.crawler     # Crawler service image
├── Dockerfile.railway     # Railway deployment image
├── docker-compose.yml     # Production compose
├── docker-compose.override.yml  # Development overrides
└── railway.toml           # Railway deployment config
```

## Data Flow

```
Retail chain websites
        │
        ▼
  crawler.get_all_products(date)
        │  (HTTP fetch + parse)
        ▼
  List[Store] with List[Product]
        │
        ▼
  save_chain() → stores.csv + products.csv + prices.csv
        │
        ▼
  create_archive() → YYYY-MM-DD.zip
        │
        ▼
  service.db.import → PostgreSQL
        │
        ▼
  FastAPI REST API → consumers
```

## Technology Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.13+ |
| Package manager | `uv` |
| Web framework | FastAPI + Uvicorn |
| Database | PostgreSQL 17 via asyncpg |
| HTTP client | httpx |
| HTML parsing | BeautifulSoup4 + lxml |
| Data validation | Pydantic v2 |
| Data types | Python dataclasses |
| Fuzzy search | rapidfuzz |
| Excel parsing | openpyxl |
| Image processing | Pillow |
| Containerization | Docker + docker-compose |
| Cloud deployment | Railway |
| CI/CD | GitHub Actions |

## Live API

- **Base URL**: `https://api.cijene.dev`
- **Swagger UI**: `https://api.cijene.dev/docs`
- **Health check**: `https://api.cijene.dev/health`

## License

- Code: [AGPL-3.0](../../LICENSE)
- Product enrichment CSV: [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)
