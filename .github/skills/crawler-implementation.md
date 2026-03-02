# Skill: Implementing a New Crawler

## When to Use

Use this skill when adding support for a new Croatian retail chain that publishes price lists online.

## Step-by-Step Guide

### 1. Create the crawler file

Create `crawler/store/<chain_name>.py` (lowercase, underscores).

```python
import datetime
import logging
import re

from crawler.store.base import BaseCrawler
from crawler.store.models import Product, Store

logger = logging.getLogger(__name__)


class ChainNameCrawler(BaseCrawler):
    """
    Crawler for ChainName store prices.

    Brief description of the data source and approach used.
    (e.g., "Downloads daily CSV files from a ZIP archive published at INDEX_URL.")
    """

    CHAIN = "chain_name"         # Lowercase, used as chain code and folder name
    BASE_URL = "https://www.chain.hr"
    INDEX_URL = "https://www.chain.hr/price-list/"

    # Map our field names to CSV/XML column names and whether they are required
    PRICE_MAP = {
        "price": ("MPC", True),
        "unit_price": ("Cijena po jedinici", True),
        "special_price": ("Akcijska cijena", False),
        "best_price_30": ("Najniža cijena 30 dana", False),
        "anchor_price": ("Sidrena cijena", False),
    }

    FIELD_MAP = {
        "product": ("Naziv proizvoda", True),
        "product_id": ("Šifra proizvoda", True),
        "brand": ("Marka", False),
        "quantity": ("Količina", False),
        "unit": ("Jedinica mjere", False),
        "barcode": ("Barkod", False),
        "category": ("Kategorija", False),
    }

    def get_all_products(self, date: datetime.date) -> list[Store]:
        """
        Download and parse all stores and products for the given date.

        Args:
            date: The date for which to fetch price data.

        Returns:
            List of Store objects, each with a populated .items list.
        """
        # TODO: Implement crawling logic based on the chain's data structure.
        # See patterns below.
        pass

    if __name__ == "__main__":
        import logging
        logging.basicConfig(level=logging.DEBUG)
        crawler = ChainNameCrawler()
        stores = crawler.get_all_products(datetime.date.today())
        if stores:
            print(stores[0])
            if stores[0].items:
                print(stores[0].items[0])
```

### 2. Choose the right pattern

#### Index-based (most common)

The chain publishes an HTML page with links to individual store CSV files.

```python
def get_all_products(self, date: datetime.date) -> list[Store]:
    index_content = self.fetch_text(self.INDEX_URL)
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(index_content, "html.parser")
    links = [a["href"] for a in soup.select("a[href$='.csv']")]

    stores = []
    for url in links:
        try:
            full_url = f"{self.BASE_URL}{url}"
            store = self.parse_store_info(url)
            content = self.fetch_text(full_url, encodings=["windows-1250", "utf-8"])
            store.items = self.parse_csv(content)
            stores.append(store)
        except Exception as e:
            logger.error(f"Error processing {url}: {e}")
    return stores
```

#### ZIP archive

The chain publishes a ZIP file containing CSV/XML files for all stores.

```python
ZIP_DATE_PATTERN = re.compile(r".*_(\d{2})_(\d{2})_(\d{4})\.zip")

def get_all_products(self, date: datetime.date) -> list[Store]:
    zip_url = self.get_index(date)  # implement to find ZIP for date
    stores = []
    for filename, content in self.get_zip_contents(zip_url, ".csv"):
        try:
            store = self.parse_store_from_filename(filename)
            store.items = self.parse_csv(content.decode("utf-8"))
            stores.append(store)
        except Exception as e:
            logger.error(f"Error processing {filename}: {e}")
    return stores
```

#### API-based (JSON)

The chain provides a JSON API returning CSV download URLs.

```python
def get_all_products(self, date: datetime.date) -> list[Store]:
    response = self.client.get(self.API_URL, params={"date": str(date)})
    response.raise_for_status()
    store_map = response.json()

    stores = []
    for store_info in store_map:
        try:
            store = self.parse_store_from_api_data(store_info)
            content = self.fetch_text(store_info["csv_url"])
            store.items = self.parse_csv(content)
            stores.append(store)
        except Exception as e:
            logger.error(f"Error processing store: {e}")
    return stores
```

#### Single file (global pricing)

The chain publishes one price file for all stores.

```python
def get_all_products(self, date: datetime.date) -> list[Store]:
    content = self.fetch_text(self.PRICE_URL)
    products = self.parse_csv(content)
    return [Store(
        chain=self.CHAIN,
        store_id="all",
        name=self.CHAIN.capitalize(),
        store_type="store",
        city="",
        street_address="",
        items=products,
    )]
```

### 3. Implement store info parsing

```python
def parse_store_info(self, filename: str) -> Store:
    """
    Parse store information from a CSV filename.

    Expected format: STORE_TYPE_STREET_ADDRESS_ZIPCODE_CITY_STORE_ID_DATE.csv
    """
    pattern = r"([^_]+)_(.+)_(\d{5})_([^_]+)_(\d+)_.*\.csv"
    m = re.search(pattern, filename)
    if not m:
        raise ValueError(f"Cannot parse store info from: {filename}")
    store_type, address, zipcode, city, store_id = m.groups()
    return Store(
        chain=self.CHAIN,
        store_id=store_id,
        name=f"{self.CHAIN.capitalize()} {city.title()}",
        store_type=store_type.lower(),
        street_address=address.replace("_", " ").title(),
        zipcode=zipcode,
        city=city.title(),
    )
```

### 4. Register the crawler

Add to `crawler/crawl.py`:

```python
from crawler.store.chain_name import ChainNameCrawler

CRAWLERS = {
    # ... existing crawlers ...
    ChainNameCrawler.CHAIN: ChainNameCrawler,
}
```

### 5. Test

```bash
# Run standalone test
python -m crawler.store.chain_name

# Verify it's registered
python -m crawler.cli.crawl -l

# Run a full crawl test
python -m crawler.cli.crawl -c chain_name /tmp/test-output/
```

## Pre-Implementation Checklist

- [ ] Identify the data format: CSV / XML / JSON / Excel / ZIP
- [ ] Check if historical data is available (by date)
- [ ] Identify how stores are listed (index page, API, filenames)
- [ ] Determine file encoding (usually `utf-8` or `windows-1250`)
- [ ] Map CSV/XML columns to `PRICE_MAP` and `FIELD_MAP`
- [ ] Check if ZIP files need special handling (see `StudenacCrawler`)
- [ ] Determine if the site is date-agnostic (no historical data)
- [ ] Test with today's date and an older date

## Common Issues

| Problem | Solution |
|---------|----------|
| Encoding errors | Try `encodings=["windows-1250", "utf-8"]` in `fetch_text` |
| TLS errors | Set `VERIFY_TLS_CERT = False` (use sparingly) |
| Timeout errors | Increase `TIMEOUT = 60.0` |
| Date not found | Log available dates; check `ZIP_DATE_PATTERN` regex |
| Empty result | Verify the site provides data for the requested date |

## BaseCrawler Utilities

| Method | Usage |
|--------|-------|
| `fetch_text(url, encodings, prefix)` | Download text (HTML, CSV) |
| `fetch_binary(url, fp)` | Download binary to file pointer |
| `parse_price(value, required)` | Parse price string to Decimal |
| `parse_csv(content, delimiter)` | Parse CSV string to Product list |
| `parse_xml_product(element)` | Parse XML element to Product |
| `get_zip_contents(url, ext)` | Iterate files in remote ZIP |
| `parse_index_for_zip(content)` | Find ZIP URLs by date pattern |
| `self.client` | Raw httpx client for custom requests |
