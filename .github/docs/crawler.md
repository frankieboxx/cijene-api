# Cijene API — Dokumentacija crawlera

## Pregled

Crawler sustav preuzima podatke o cijenama s web stranica hrvatskih trgovačkih lanaca. Svaki lanac ima svoju implementaciju koja nasljeđuje zajedničku `BaseCrawler` klasu iz `crawler/store/base.py`.

Detaljnija arhitekturna dokumentacija crawlera dostupna je u [`docs/crawler.md`](../../docs/crawler.md).

---

## Struktura

```
crawler/
├── crawl.py              # Orchestracija — crawlanje više lanaca
├── cli/
│   └── crawl.py          # CLI sučelje (argparse)
└── store/
    ├── base.py           # BaseCrawler apstraktna klasa
    ├── models.py         # Product i Store dataclass modeli
    ├── output.py         # Generiranje CSV datoteka i ZIP arhive
    ├── utils.py          # Pomoćne funkcije
    ├── konzum.py         # Konzum crawler
    ├── lidl.py           # Lidl crawler
    ├── tommy.py          # Tommy crawler
    ├── studenac.py       # Studenac crawler
    ├── kaufland.py       # Kaufland crawler
    ├── dm.py             # dm crawler
    ├── metro.py          # Metro crawler
    ├── ribola.py         # Ribola crawler
    └── roto.py           # Roto crawler
```

---

## Pokretanje crawlera

### CLI

```bash
# Crawlanje svih lanaca za danas
uv run -m crawler.cli.crawl /path/to/output/

# Crawlanje za određeni datum
uv run -m crawler.cli.crawl -d 2025-06-01 /path/to/output/

# Crawlanje samo pojedinih lanaca
uv run -m crawler.cli.crawl -c konzum,lidl /path/to/output/

# Popis podržanih lanaca
uv run -m crawler.cli.crawl -l
```

Opcije:
- `-d` / `--date` — datum u `YYYY-MM-DD` formatu (default: danas)
- `-c` / `--chains` — zarezima odvojeni popis lanaca
- `-l` / `--list` — ispis dostupnih lanaca
- `-h` / `--help` — pomoć

### Pipeline (crawl + uvoz + email)

```bash
uv run -m scripts.pipeline [--date YYYY-MM-DD] [--chains chain1,chain2] [--skip-email]
```

---

## Registrirani crawleri

Crawleri su registrirani u `crawler/crawl.py` u rječniku `CRAWLERS`:

| Ključ       | Klasa              | Datoteka              |
|-------------|--------------------|-----------------------|
| `studenac`  | `StudenacCrawler`  | `store/studenac.py`   |
| `konzum`    | `KonzumCrawler`    | `store/konzum.py`     |
| `lidl`      | `LidlCrawler`      | `store/lidl.py`       |
| `tommy`     | `TommyCrawler`     | `store/tommy.py`      |
| `kaufland`  | `KauflandCrawler`  | `store/kaufland.py`   |
| `dm`        | `DmCrawler`        | `store/dm.py`         |
| `metro`     | `MetroCrawler`     | `store/metro.py`      |
| `ribola`    | `RibolaCrawler`    | `store/ribola.py`     |
| `roto`      | `RotoCrawler`      | `store/roto.py`       |

---

## Izlazni format

Crawler generira po lancu tri CSV datoteke:

| Datoteka      | Sadržaj                                                              |
|---------------|----------------------------------------------------------------------|
| `stores.csv`  | `store_id`, `type`, `address`, `city`, `zipcode`                    |
| `products.csv`| `product_id`, `barcode`, `name`, `brand`, `category`, `unit`, `quantity` |
| `prices.csv`  | `store_id`, `product_id`, `price`, `unit_price`, `best_price_30`, `anchor_price`, `special_price` |

Sve CSV datoteke se pakuju u ZIP arhivu imenovan po datumu (`YYYY-MM-DD.zip`).

---

## Dodavanje novog crawlera

1. Kreirajte datoteku `crawler/store/naziv_lanca.py`
2. Naslijedite `BaseCrawler` i definirajte `CHAIN`, `BASE_URL`, `PRICE_MAP`, `FIELD_MAP`
3. Implementirajte metodu `get_all_products(self, date) -> list[Store]`
4. Registrirajte crawler u `crawler/crawl.py`:

```python
from crawler.store.naziv_lanca import NazivLancaCrawler
CRAWLERS = {
    ...
    NazivLancaCrawler.CHAIN: NazivLancaCrawler,
}
```

Za detaljne upute i obrasce implementacije pogledajte [`docs/crawler.md`](../../docs/crawler.md).

---

## Napomene za Windows korisnike

Postavite `PYTHONUTF8=1` ili koristite `-X utf8` flag kako biste izbjegli probleme s kodiranjem znakova.
