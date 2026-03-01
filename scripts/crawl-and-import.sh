#!/bin/bash
set -euo pipefail

# Crawl-and-import script for Railway cron job
# Crawls all configured retail chains and imports data into PostgreSQL

DATE="${1:-$(date +%Y-%m-%d)}"
OUTPUT_DIR="${OUTPUT_DIR:-/app/output}"
CHAINS="${CRAWL_CHAINS:-}"

echo "=== Crawl & Import: $DATE ==="

# Step 1: Crawl
echo "[1/2] Crawling price data..."
if [ -n "$CHAINS" ]; then
    uv run -m crawler.cli.crawl "$OUTPUT_DIR" -d "$DATE" -c "$CHAINS" -v info
else
    uv run -m crawler.cli.crawl "$OUTPUT_DIR" -d "$DATE" -v info
fi

# Step 2: Import into database
ARCHIVE="$OUTPUT_DIR/$DATE.zip"
if [ -f "$ARCHIVE" ]; then
    echo "[2/2] Importing data into database..."
    uv run -m service.db.import "$ARCHIVE"
    echo "=== Done: $DATE ==="
else
    echo "ERROR: Archive not found at $ARCHIVE"
    exit 1
fi
