#!/usr/bin/env python3
"""
Crawl & Import pipeline with email reporting.

Replaces the bash crawl-and-import script with a Python orchestrator
that captures metrics and sends an MJML-formatted report via Mailgun.

Usage:
    python -m scripts.pipeline [--date YYYY-MM-DD] [--chains chain1,chain2] [--skip-email]
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from time import time

from crawler.crawl import crawl_chain, get_chains, STANDARD_CRAWLERS
from crawler.store.output import copy_archive_info, create_archive
from scripts.deals import run_deals_report
from scripts.report import ChainReport, PipelineReport, send_report
from service.config import settings

logger = logging.getLogger("pipeline")


async def run_import(archive_path: Path) -> None:
    """
    Run the import for the given archive.
    """
    import importlib

    import_module = importlib.import_module("service.db.import")
    import_archive = import_module.import_archive

    db = settings.get_db()
    await db.connect()

    try:
        await db.create_tables()
        await import_archive(archive_path)
    finally:
        await db.close()


def run_crawl(
    output_dir: Path,
    crawl_date: date,
    chain_list: list[str],
) -> tuple[list[ChainReport], float, Path]:
    """
    Run the crawl for all chains, collecting per-chain metrics.

    Returns:
        (chain_reports, total_elapsed, zip_path)
    """
    date_str = crawl_date.strftime("%Y-%m-%d")
    data_path = output_dir / date_str
    zip_path = output_dir / f"{date_str}.zip"
    os.makedirs(data_path, exist_ok=True)

    reports: list[ChainReport] = []

    t0 = time()
    for chain in chain_list:
        logger.info(f"Crawling {chain} for {date_str}...")
        result = crawl_chain(chain, crawl_date, data_path / chain)

        reports.append(
            ChainReport(
                chain=chain,
                stores=result.n_stores,
                products=result.n_products,
                prices=result.n_prices,
                crawl_time=result.elapsed_time,
                error=None if result.n_stores > 0 or result.elapsed_time > 0 else None,
            )
        )

    crawl_elapsed = time() - t0

    # Create the archive
    copy_archive_info(data_path)
    create_archive(data_path, zip_path)
    logger.info(f"Archive created: {zip_path}")

    return reports, crawl_elapsed, zip_path


async def run_pipeline(
    crawl_date: date,
    output_dir: Path,
    chain_list: list[str],
    skip_email: bool = False,
) -> None:
    """Run the full crawl -> import -> report pipeline."""

    logger.info(f"=== Pipeline start: {crawl_date} ===")

    # 1. Crawl
    logger.info("[1/4] Crawling...")
    chain_reports, crawl_elapsed, zip_path = run_crawl(
        output_dir, crawl_date, chain_list
    )

    for cr in chain_reports:
        logger.info(
            f"  {cr.chain}: {cr.stores} stores, {cr.products} products, "
            f"{cr.prices} prices ({cr.crawl_time:.1f}s)"
        )

    # 2. Import
    import_elapsed = 0.0
    import_error = None

    if zip_path.exists():
        logger.info("[2/4] Importing...")

        # We need to capture per-chain new prices from the import.
        # The importer logs these, so we capture via a custom handler.
        new_prices_map: dict[str, int] = {}

        class PriceCountHandler(logging.Handler):
            """Capture 'Imported N new prices for chain' log messages."""

            def emit(self, record: logging.LogRecord) -> None:
                msg = record.getMessage()
                if "Imported" in msg and "new prices for" in msg:
                    try:
                        parts = msg.split()
                        count = int(parts[1])
                        chain_code = parts[-1]
                        new_prices_map[chain_code] = count
                    except (ValueError, IndexError):
                        pass

        price_handler = PriceCountHandler()
        logging.getLogger("importer").addHandler(price_handler)

        t0 = time()
        try:
            await run_import(zip_path)
        except Exception as e:
            import_error = str(e)
            logger.error(f"Import failed: {e}")
        finally:
            import_elapsed = time() - t0
            logging.getLogger("importer").removeHandler(price_handler)

        # Merge new_prices into chain reports
        for cr in chain_reports:
            cr.new_prices = new_prices_map.get(cr.chain, 0)

        logger.info(f"Import completed in {import_elapsed:.0f}s")
    else:
        import_error = f"Archive not found: {zip_path}"
        logger.error(import_error)

    # 3. Report
    report = PipelineReport(
        run_date=crawl_date,
        chains=chain_reports,
        crawl_total_seconds=crawl_elapsed,
        import_total_seconds=import_elapsed,
        import_error=import_error,
    )

    if skip_email:
        logger.info("[3/4] Email skipped (--skip-email)")
        print(report.to_text())
    else:
        logger.info("[3/4] Sending report email...")
        sent = send_report(report)
        if sent:
            logger.info("Report email sent successfully.")
        else:
            logger.warning("Report email was not sent. Printing to stdout:")
            print(report.to_text())

    # 4. Dubrovnik deals report
    logger.info("[4/4] Dubrovnik deals report...")
    try:
        await run_deals_report(crawl_date, skip_email=skip_email)
    except Exception as e:
        logger.error(f"Dubrovnik deals report failed: {e}")

    logger.info(f"=== Pipeline complete: {crawl_date} ===")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Crawl, import, and email-report pipeline",
    )
    parser.add_argument(
        "--date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=None,
        help="Date to crawl (YYYY-MM-DD, default: today)",
    )
    parser.add_argument(
        "--chains",
        type=str,
        default=None,
        help="Comma-separated chain list (default: all)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: OUTPUT_DIR env or /app/output)",
    )
    parser.add_argument(
        "--skip-email",
        action="store_true",
        help="Skip sending email, print report to stdout",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s:%(name)s:%(levelname)s:%(message)s",
    )

    crawl_date = args.date or date.today()

    if args.chains:
        chain_list = [c.strip() for c in args.chains.split(",")]
    else:
        chain_list = list(STANDARD_CRAWLERS)

    output_dir = args.output_dir or Path(os.getenv("OUTPUT_DIR", "/app/output"))
    output_dir.mkdir(parents=True, exist_ok=True)

    asyncio.run(
        run_pipeline(
            crawl_date=crawl_date,
            output_dir=output_dir,
            chain_list=chain_list,
            skip_email=args.skip_email,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
