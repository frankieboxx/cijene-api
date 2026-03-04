from __future__ import annotations

import datetime
import logging
import re
import time
from decimal import Decimal

import httpx
from bs4 import BeautifulSoup

from crawler.store.base import BaseCrawler
from crawler.store.models import Product, Store

logger = logging.getLogger(__name__)

# Regex to extract quantity embedded in product names like "1650 mL/900 g" or "500 g"
_QTY_RE = re.compile(
    r"(\d+[\d,.]*\s*(?:mL|ml|L|l|cl|kg|g|kom|kos|pack|pcs))",
    re.IGNORECASE,
)

# Regex to extract unit from unit-price string e.g. "3.02 €/L" → "L"
_UNIT_RE = re.compile(r"/\s*(\w+)\s*$")


def _extract_quantity(name: str) -> str:
    """Return first quantity token found in the product name, or empty string."""
    m = _QTY_RE.search(name)
    return m.group(1).strip() if m else ""


def _extract_unit(unit_price_text: str) -> str:
    """Return unit of measure from unit-price string like '3.02 €/L'."""
    m = _UNIT_RE.search(unit_price_text)
    return m.group(1).strip() if m else "kom"


class StanicCrawler(BaseCrawler):
    """
    Crawler for Stanić wholesale web shop (horeca.hr).

    Scrapes WooCommerce product listing pages and individual product pages.
    No EAN barcodes are available; internal SKUs are used as product IDs.
    Barcodes are auto-set to 'stanic:<sku>' by fix_product_data().

    https://horeca.hr/trgovina/
    """

    CHAIN = "stanic"
    BASE_URL = "https://horeca.hr"
    SHOP_URL = f"{BASE_URL}/trgovina/"

    # Delay between HTTP requests to be polite to the server
    REQUEST_DELAY = 1.0

    # Single (virtual) store — central warehouse + pickup
    STORE_ID = "sveta-nedelja"
    STORE_NAME = "Stanić veleprodaja"
    STORE_TYPE = "veleprodaja"
    STORE_CITY = "Sveta Nedelja"
    STORE_ADDRESS = "Kerestinečka cesta 57/A"
    STORE_ZIPCODE = "10431"

    # WooCommerce product card selectors
    _CARD_SEL = "li.product"
    _CARD_LINK_SEL = "a.woocommerce-LoopProduct-link"
    _CARD_TITLE_SEL = "h2.woocommerce-loop-product__title"
    _CARD_PRICE_SEL = "span.woocommerce-Price-amount bdi"

    # WooCommerce product detail page selectors
    _DETAIL_PRICE_SEL = "p.price span.woocommerce-Price-amount bdi"
    _DETAIL_SKU_SEL = "span.sku"
    _DETAIL_CATS_SEL = "span.posted_in a"
    _DETAIL_TAGS_SEL = "span.tagged_as a"
    _DETAIL_UNIT_SEL = "p.price"

    def _page_url(self, page: int) -> str:
        if page <= 1:
            return self.SHOP_URL
        return f"{self.SHOP_URL}page/{page}/"

    def _fetch_page(self, url: str) -> str | None:
        """Fetch a page; return None on 404, raise on other errors."""
        try:
            return self.fetch_text(url)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            logger.error(f"HTTP {e.response.status_code} fetching {url}")
            return None
        except Exception as e:
            logger.error(f"Failed to fetch {url}: {e}")
            return None

    def _parse_listing_cards(self, html: str) -> list[dict]:
        """
        Parse product cards from a WooCommerce listing page.

        Returns list of dicts with keys: name, price_text, url.
        """
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(self._CARD_SEL)
        results = []

        for card in cards:
            try:
                link_el = card.select_one(self._CARD_LINK_SEL)
                title_el = card.select_one(self._CARD_TITLE_SEL)
                price_el = card.select_one(self._CARD_PRICE_SEL)

                if not link_el or not title_el:
                    continue

                url = str(link_el.get("href", "")).strip()
                name = title_el.get_text(strip=True)
                price_text = price_el.get_text(strip=True) if price_el else ""

                if url and name:
                    results.append(
                        {"name": name, "price_text": price_text, "url": url}
                    )
            except Exception as e:
                logger.debug(f"Skipping card due to parse error: {e}")

        return results

    def _fetch_product_detail(self, url: str) -> dict:
        """
        Fetch individual product page and extract SKU, unit price, category, brand.

        Returns dict with keys: sku, unit_price_text, unit, category, brand.
        Falls back to safe defaults on any failure.
        """
        defaults: dict = {
            "sku": "",
            "unit_price_text": "",
            "unit": "kom",
            "category": "",
            "brand": "",
        }

        try:
            html = self.fetch_text(url)
        except Exception as e:
            logger.warning(f"Could not fetch product detail {url}: {e}")
            return defaults

        soup = BeautifulSoup(html, "html.parser")

        # SKU
        sku_el = soup.select_one(self._DETAIL_SKU_SEL)
        sku = sku_el.get_text(strip=True) if sku_el else ""

        # Unit price & unit from the price block text, e.g. "Jedinična cijena: 3.02 €/L"
        price_block = soup.select_one(self._DETAIL_UNIT_SEL)
        unit_price_text = ""
        unit = "kom"
        if price_block:
            block_text = price_block.get_text(" ", strip=True)
            m = re.search(r"Jedinična cijena:\s*([\d,.]+\s*€/\w+)", block_text)
            if m:
                unit_price_text = m.group(1).strip()
                unit = _extract_unit(unit_price_text)

        # Category (deepest / most specific)
        cat_els = soup.select(self._DETAIL_CATS_SEL)
        category = cat_els[-1].get_text(strip=True) if cat_els else ""

        # Brand from product tag ("Oznaka:")
        tag_els = soup.select(self._DETAIL_TAGS_SEL)
        brand = tag_els[0].get_text(strip=True) if tag_els else ""

        return {
            "sku": sku,
            "unit_price_text": unit_price_text,
            "unit": unit,
            "category": category,
            "brand": brand,
        }

    def _build_product(self, listing: dict, detail: dict) -> Product | None:
        """Assemble a Product from listing + detail dicts."""
        name = listing["name"]
        sku = detail["sku"] or re.sub(r"[^a-z0-9-]", "", listing["url"].rstrip("/").split("/")[-1])

        try:
            price = self.parse_price(listing["price_text"], required=True)
        except ValueError:
            logger.warning(f"Skipping '{name}' — cannot parse price '{listing['price_text']}'")
            return None

        unit_price: Decimal | None = self.parse_price(detail["unit_price_text"], required=False)

        data = {
            "product": name,
            "product_id": sku,
            "brand": detail["brand"],
            "quantity": _extract_quantity(name),
            "unit": detail["unit"],
            "price": price,
            "unit_price": unit_price,
            "barcode": "",  # fix_product_data() will set to "stanic:<sku>"
            "category": detail["category"],
            "special_price": None,
        }

        try:
            data = self.fix_product_data(data)
            return Product(**data)  # type: ignore[arg-type]
        except Exception as e:
            logger.warning(f"Skipping '{name}': {e}")
            return None

    def get_all_products(self, date: datetime.date) -> list[Store]:
        """
        Crawl all listing pages and fetch per-product details.

        Sleeps REQUEST_DELAY seconds between requests.
        On per-product failure, logs and continues to the next product.
        """
        all_products: list[Product] = []
        page = 1

        while True:
            url = self._page_url(page)
            logger.info(f"Fetching listing page {page}: {url}")

            html = self._fetch_page(url)
            if html is None:
                logger.info(f"Page {page} returned 404 — end of listing.")
                break

            cards = self._parse_listing_cards(html)
            if not cards:
                logger.info(f"Page {page} has no products — end of listing.")
                break

            logger.info(f"Page {page}: {len(cards)} products found.")

            for card in cards:
                time.sleep(self.REQUEST_DELAY)
                detail = self._fetch_product_detail(card["url"])
                product = self._build_product(card, detail)
                if product:
                    all_products.append(product)

            page += 1
            time.sleep(self.REQUEST_DELAY)

        if not all_products:
            logger.error("No products scraped from horeca.hr (stanic)")
            return []

        logger.info(f"Scraped {len(all_products)} products from horeca.hr (stanic)")

        store = Store(
            chain=self.CHAIN,
            store_id=self.STORE_ID,
            name=self.STORE_NAME,
            store_type=self.STORE_TYPE,
            city=self.STORE_CITY,
            street_address=self.STORE_ADDRESS,
            zipcode=self.STORE_ZIPCODE,
            items=all_products,
        )
        return [store]
