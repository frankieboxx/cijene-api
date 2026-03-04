"""
Stanic (horeca.hr) product image crawler.

For every chain_product in the 'stanic' chain that doesn't yet have an image,
fetches the WooCommerce product page, extracts the og:image URL, downloads it,
resizes to a 200×200 JPEG thumbnail and upserts into the product_images table.

Usage:
    uv run python -m scripts.crawl_images_stanic [--dry-run] [--force] [--debug]

Options:
    --dry-run   Log what would be downloaded without saving anything.
    --force     Re-download and update images that already exist.
    --debug     Enable debug logging.

Cron configuration (Railway service: crawler-stanic-images):
    Schedule: 0 21 * * 3  (21:00 UTC = 23:00 Zagreb CEST, every Wednesday)
"""

import argparse
import asyncio
import io
import logging
import re
import time
import unicodedata
from typing import Any

import asyncpg
import httpx
from bs4 import BeautifulSoup
from PIL import Image

from service.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CHAIN_CODE = "stanic"
BASE_URL = "https://horeca.hr"
PRODUCT_BASE_URL = f"{BASE_URL}/proizvod"

THUMBNAIL_SIZE = (200, 200)
THUMBNAIL_QUALITY = 85
REQUEST_DELAY_SEC = 1.5   # polite delay — WooCommerce shop, not a data API
REQUEST_TIMEOUT_SEC = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_wc_slug(name: str) -> str:
    """
    Convert a WooCommerce product name to its URL slug.

    WooCommerce slugifies product titles the same way WordPress does:
    - Strip diacritics (NFD decompose + remove combining marks)
    - Lowercase
    - Replace non-alphanumeric characters with hyphens
    - Collapse consecutive hyphens
    - Strip leading/trailing hyphens
    """
    # Strip diacritics
    nfd = unicodedata.normalize("NFD", name)
    ascii_name = "".join(c for c in nfd if unicodedata.category(c) != "Mn")

    # Lowercase
    slug = ascii_name.lower()

    # Replace non-alphanumeric (keep digits and letters) with hyphen
    slug = re.sub(r"[^a-z0-9]+", "-", slug)

    # Collapse and strip hyphens
    slug = slug.strip("-")

    return slug


def _extract_og_image(html: str) -> str | None:
    """Extract og:image URL from an HTML page."""
    soup = BeautifulSoup(html, "html.parser")

    # Try og:image meta first (most reliable for WooCommerce)
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):  # type: ignore[union-attr]
        return str(og["content"]).strip()  # type: ignore[index]

    # Fallback: WooCommerce gallery main image
    img = soup.select_one(
        "div.woocommerce-product-gallery__image img.wp-post-image"
    )
    if img and img.get("src"):
        return str(img["src"]).strip()

    # Second fallback: any .wp-post-image
    img = soup.select_one("img.wp-post-image")
    if img and img.get("src"):
        return str(img["src"]).strip()

    return None


def _resize_to_thumbnail(image_bytes: bytes) -> bytes:
    """Resize image to THUMBNAIL_SIZE, pad to exact size, return JPEG bytes."""
    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGB")
    img.thumbnail(THUMBNAIL_SIZE, Image.LANCZOS)

    if img.size != THUMBNAIL_SIZE:
        canvas = Image.new("RGB", THUMBNAIL_SIZE, (255, 255, 255))
        offset = (
            (THUMBNAIL_SIZE[0] - img.size[0]) // 2,
            (THUMBNAIL_SIZE[1] - img.size[1]) // 2,
        )
        canvas.paste(img, offset)
        img = canvas

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=THUMBNAIL_QUALITY, optimize=True)
    return out.getvalue()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def fetch_stanic_chain_products(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    """Return all chain_products for the 'stanic' chain."""
    rows = await conn.fetch(
        """
        SELECT cp.id, cp.code, cp.name
        FROM chain_products cp
        JOIN chains c ON cp.chain_id = c.id
        WHERE c.code = $1
        ORDER BY cp.id
        """,
        CHAIN_CODE,
    )
    return [dict(r) for r in rows]


async def fetch_existing_image_ids(conn: asyncpg.Connection) -> set[int]:
    """Return chain_product_ids that already have an image stored."""
    rows = await conn.fetch("SELECT chain_product_id FROM product_images")
    return {r["chain_product_id"] for r in rows}


async def upsert_image(
    conn: asyncpg.Connection,
    chain_product_id: int,
    image_data: bytes,
    source_url: str,
) -> None:
    """Insert or update a product thumbnail in product_images."""
    await conn.execute(
        """
        INSERT INTO product_images (
            chain_product_id, image_data, image_format,
            width, height, source_url, updated_at
        )
        VALUES ($1, $2, 'jpeg', 200, 200, $3, CURRENT_TIMESTAMP)
        ON CONFLICT (chain_product_id)
        DO UPDATE SET
            image_data  = EXCLUDED.image_data,
            source_url  = EXCLUDED.source_url,
            updated_at  = CURRENT_TIMESTAMP
        """,
        chain_product_id,
        image_data,
        source_url,
    )


# ---------------------------------------------------------------------------
# Core crawl
# ---------------------------------------------------------------------------

def _fetch_image_for_product(
    product: dict[str, Any],
    session: httpx.Client,
    dry_run: bool,
) -> tuple[bytes, str] | None:
    """
    Fetch and resize the product image for a single chain_product.

    Returns (jpeg_bytes, source_url) or None on failure / no image.
    """
    name: str = product["name"]
    slug = _to_wc_slug(name)
    page_url = f"{PRODUCT_BASE_URL}/{slug}/"

    logger.debug("Fetching product page: %s", page_url)

    try:
        resp = session.get(page_url, timeout=REQUEST_TIMEOUT_SEC)

        # Slug derivation sometimes misses — log and skip without error
        if resp.status_code == 404:
            logger.warning(
                "404 for '%s' (slug: %s) — skipping", name, slug
            )
            return None

        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.error("HTTP %s fetching page for '%s': %s", e.response.status_code, name, e)
        return None
    except Exception as e:
        logger.error("Error fetching page for '%s': %s", name, e)
        return None

    image_url = _extract_og_image(resp.text)
    if not image_url:
        logger.warning("No image found on page for '%s'", name)
        return None

    if dry_run:
        logger.info("[DRY RUN] Would download: %s", image_url)
        return None

    time.sleep(REQUEST_DELAY_SEC)

    try:
        img_resp = session.get(image_url, timeout=REQUEST_TIMEOUT_SEC)
        img_resp.raise_for_status()
        content_type = img_resp.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            logger.warning("Non-image content-type '%s' for %s", content_type, image_url)
            return None
        thumbnail = _resize_to_thumbnail(img_resp.content)
        return thumbnail, image_url
    except Exception as e:
        logger.error("Failed to download/resize image %s: %s", image_url, e)
        return None


async def run_crawl(dry_run: bool = False, force: bool = False) -> None:
    """Full image crawl pipeline for Stanic products."""
    dsn = settings.db_dsn
    conn: asyncpg.Connection = await asyncpg.connect(dsn)

    try:
        products = await fetch_stanic_chain_products(conn)
        if not products:
            logger.warning(
                "No chain_products found for chain '%s' — has the crawler run yet?",
                CHAIN_CODE,
            )
            return

        logger.info("Found %d stanic chain products in DB.", len(products))

        existing_ids = await fetch_existing_image_ids(conn)

        if force:
            to_crawl = products
            logger.info("--force: re-downloading all %d images.", len(to_crawl))
        else:
            to_crawl = [p for p in products if p["id"] not in existing_ids]
            logger.info(
                "%d products need images (%d already have images).",
                len(to_crawl),
                len(products) - len(to_crawl),
            )

        if not to_crawl:
            logger.info("Nothing to do.")
            return

        crawled = skipped = errors = 0

        with httpx.Client(
            headers={"User-Agent": "cijene-api-bot/1.0 (+https://cijene.dev)"},
            follow_redirects=True,
        ) as session:
            for product in to_crawl:
                result = _fetch_image_for_product(product, session, dry_run)

                if result is None:
                    skipped += 1
                else:
                    try:
                        image_bytes, source_url = result
                        await upsert_image(
                            conn,
                            chain_product_id=product["id"],
                            image_data=image_bytes,
                            source_url=source_url,
                        )
                        crawled += 1
                        logger.info(
                            "Stored image for '%s' (SKU %s) from %s",
                            product["name"],
                            product["code"],
                            source_url,
                        )
                    except Exception as e:
                        errors += 1
                        logger.error(
                            "DB error saving image for '%s': %s",
                            product["name"],
                            e,
                        )

                # Polite delay between page requests
                time.sleep(REQUEST_DELAY_SEC)

        logger.info(
            "Done. crawled=%d  skipped=%d  errors=%d",
            crawled,
            skipped,
            errors,
        )

    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download and store product images for Stanic (horeca.hr) products."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be downloaded without saving anything.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download images that already exist in the DB.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    asyncio.run(run_crawl(dry_run=args.dry_run, force=args.force))


if __name__ == "__main__":
    main()
