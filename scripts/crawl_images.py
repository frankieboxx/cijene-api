"""
Product image crawler — Atrium Price Compare v3.

Crawls product images from retailer websites for products that appear in
the Atrium DB (matched by sifra or fuzzy name), resizes them to 200×200
JPEG thumbnails, and stores them in the cijene-api `product_images` table.

Two databases:
- ATRIUM_DATABASE_URL — ERP with table troskovi_detalji (sifra codes)
- DB_DSN — cijene-api with tables chain_products, product_images

Usage:
    uv run python -m scripts.crawl_images [--dry-run] [--debug]

Cron configuration (Railway):
    Script:   scripts/crawl_images.py
    Schedule: 0 10 * * 0 (10:00 every Sunday — images don't change often)
    Env vars: DB_DSN, ATRIUM_DATABASE_URL

Supplier support:
    Currently crawls images only for Metro products matched via sifra.
    TODO: Extend to other chains when image URL patterns are identified.
"""

import argparse
import asyncio
import io
import logging
import time
import urllib.robotparser
from urllib.parse import urlparse

import asyncpg
import httpx
from PIL import Image

from service.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

THUMBNAIL_SIZE = (200, 200)
THUMBNAIL_QUALITY = 85
REQUEST_DELAY_SEC = 1.0  # 1 request/second per domain (rate limiting)
REQUEST_TIMEOUT_SEC = 15

# Known image URL patterns per chain
# Maps chain code → function that builds image URL from chain_product_id/code
CHAIN_IMAGE_URL_PATTERNS: dict[str, str] = {
    # TODO: Add retailer-specific image URL patterns here when known.
    # Example pattern (not real):
    # "metro": "https://produkte.metro.de/catalog/product/image/{code}.jpg",
}


def _get_robots_parser(domain: str, session: httpx.Client) -> urllib.robotparser.RobotFileParser:
    """Fetch and parse robots.txt for a domain."""
    parser = urllib.robotparser.RobotFileParser()
    robots_url = f"https://{domain}/robots.txt"
    try:
        resp = session.get(robots_url, timeout=10)
        parser.parse(resp.text.splitlines())
    except Exception:
        pass  # If robots.txt is not accessible, assume allowed
    return parser


def resize_to_thumbnail(image_bytes: bytes) -> bytes:
    """Resize image to THUMBNAIL_SIZE and return JPEG bytes."""
    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGB")
    img.thumbnail(THUMBNAIL_SIZE, Image.LANCZOS)
    # Pad to exact 200×200 if needed
    if img.size != THUMBNAIL_SIZE:
        canvas = Image.new("RGB", THUMBNAIL_SIZE, (255, 255, 255))
        offset = ((THUMBNAIL_SIZE[0] - img.size[0]) // 2, (THUMBNAIL_SIZE[1] - img.size[1]) // 2)
        canvas.paste(img, offset)
        img = canvas
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=THUMBNAIL_QUALITY, optimize=True)
    return out.getvalue()


async def fetch_atrium_sifras(conn: asyncpg.Connection) -> list[str]:
    """Get all unique Metro product codes (sifra) from Atrium DB."""
    rows = await conn.fetch(
        """
        SELECT DISTINCT sifra
        FROM troskovi_detalji
        WHERE sifra IS NOT NULL AND sifra != ''
        ORDER BY sifra
        """
    )
    return [r["sifra"] for r in rows]


async def fetch_chain_products_by_codes(
    conn: asyncpg.Connection, codes: list[str]
) -> list[dict]:
    """Find chain_products in cijene-api matching given codes."""
    rows = await conn.fetch(
        """
        SELECT cp.id, cp.code, cp.name, cp.category, c.code AS chain
        FROM chain_products cp
        JOIN chains c ON cp.chain_id = c.id
        WHERE cp.code = ANY($1)
        """,
        codes,
    )
    return [dict(r) for r in rows]


async def fetch_existing_image_ids(conn: asyncpg.Connection) -> set[int]:
    """Get chain_product_ids that already have images stored."""
    rows = await conn.fetch("SELECT chain_product_id FROM product_images")
    return {r["chain_product_id"] for r in rows}


async def upsert_product_image(
    conn: asyncpg.Connection,
    chain_product_id: int,
    image_data: bytes,
    source_url: str,
    ean: str | None = None,
) -> None:
    """Insert or update a product image in the product_images table."""
    await conn.execute(
        """
        INSERT INTO product_images (
            chain_product_id, ean, image_data, image_format,
            width, height, source_url, updated_at
        )
        VALUES ($1, $2, $3, 'jpeg', 200, 200, $4, CURRENT_TIMESTAMP)
        ON CONFLICT (chain_product_id)
        DO UPDATE SET
            image_data = EXCLUDED.image_data,
            ean = COALESCE(EXCLUDED.ean, product_images.ean),
            source_url = EXCLUDED.source_url,
            updated_at = CURRENT_TIMESTAMP
        """,
        chain_product_id,
        ean,
        image_data,
        source_url,
    )


def crawl_image_for_product(
    product: dict,
    session: httpx.Client,
    robots_cache: dict[str, urllib.robotparser.RobotFileParser],
    last_request_time: dict[str, float],
    dry_run: bool = False,
) -> tuple[bytes, str] | None:
    """
    Attempt to crawl a product image for a single chain product.

    Returns (image_bytes, source_url) or None if no image available.
    """
    chain = product.get("chain", "")
    code = product.get("code", "")

    url_pattern = CHAIN_IMAGE_URL_PATTERNS.get(chain)
    if not url_pattern:
        logger.debug("No image URL pattern configured for chain: %s", chain)
        return None

    image_url = url_pattern.format(code=code, id=product.get("id"))
    domain = urlparse(image_url).netloc

    # Check robots.txt
    if domain not in robots_cache:
        robots_cache[domain] = _get_robots_parser(domain, session)
    if not robots_cache[domain].can_fetch("*", image_url):
        logger.info("robots.txt disallows crawling: %s", image_url)
        return None

    # Rate limiting: 1 req/s per domain
    now = time.monotonic()
    last = last_request_time.get(domain, 0)
    wait = REQUEST_DELAY_SEC - (now - last)
    if wait > 0:
        time.sleep(wait)
    last_request_time[domain] = time.monotonic()

    if dry_run:
        logger.info("[DRY RUN] Would fetch: %s", image_url)
        return None

    try:
        resp = session.get(image_url, timeout=REQUEST_TIMEOUT_SEC)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            logger.warning("Non-image response for %s: %s", image_url, content_type)
            return None
        thumbnail = resize_to_thumbnail(resp.content)
        return thumbnail, image_url
    except Exception as e:
        logger.warning("Failed to fetch image %s: %s", image_url, e)
        return None


async def run_crawl(dry_run: bool = False) -> None:
    """Run the full image crawl pipeline."""
    atrium_dsn = settings.atrium_database_url
    cijene_dsn = settings.db_dsn

    if not atrium_dsn:
        raise ValueError("ATRIUM_DATABASE_URL not configured")

    logger.info("Connecting to Atrium DB...")
    atrium_conn = await asyncpg.connect(atrium_dsn)

    logger.info("Connecting to Cijene-API DB...")
    cijene_conn = await asyncpg.connect(cijene_dsn)

    try:
        # 1. Get all unique Metro sifra codes from Atrium
        logger.info("Fetching Atrium sifra codes...")
        sifras = await fetch_atrium_sifras(atrium_conn)
        logger.info("Found %d unique sifra codes in Atrium", len(sifras))

        # 2. Find matching chain_products in cijene-api
        logger.info("Looking up chain products in cijene-api...")
        products = await fetch_chain_products_by_codes(cijene_conn, sifras)
        logger.info("Found %d matching chain products", len(products))

        # 3. Skip products that already have images
        existing_ids = await fetch_existing_image_ids(cijene_conn)
        to_crawl = [p for p in products if p["id"] not in existing_ids]
        logger.info(
            "Need to crawl images for %d products (%d already have images)",
            len(to_crawl),
            len(products) - len(to_crawl),
        )

        if not to_crawl:
            logger.info("All products already have images. Nothing to do.")
            return

        # 4. Crawl images
        robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}
        last_request_time: dict[str, float] = {}
        crawled = 0
        skipped = 0

        with httpx.Client(
            headers={"User-Agent": "cijene-api-bot/1.0 (+https://cijene.dev)"},
            follow_redirects=True,
        ) as session:
            for product in to_crawl:
                result = crawl_image_for_product(
                    product=product,
                    session=session,
                    robots_cache=robots_cache,
                    last_request_time=last_request_time,
                    dry_run=dry_run,
                )
                if result is None:
                    skipped += 1
                    continue

                image_bytes, source_url = result
                await upsert_product_image(
                    conn=cijene_conn,
                    chain_product_id=product["id"],
                    image_data=image_bytes,
                    source_url=source_url,
                )
                crawled += 1
                logger.info(
                    "Stored image for %s (%s) from %s",
                    product["name"],
                    product["code"],
                    source_url,
                )

        logger.info(
            "Image crawl complete: %d crawled, %d skipped (no URL pattern or error)",
            crawled,
            skipped,
        )

    finally:
        await atrium_conn.close()
        await cijene_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crawl product images for Atrium products and store thumbnails in cijene-api DB"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be crawled without actually fetching images",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    asyncio.run(run_crawl(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
