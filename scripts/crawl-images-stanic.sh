#!/bin/bash
# Stanic product image crawler — weekly Wednesday night run.
# Fetches og:image from horeca.hr product pages, resizes to 200x200 JPEG,
# stores in product_images table.
# Exits 0 even on failure so Railway does not restart the cron container.

echo "=== Stanic Image Crawler — $(date '+%Y-%m-%d %H:%M:%S') ==="

set +e

uv run python -m scripts.crawl_images_stanic
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "WARNING: Image crawler exited with code $EXIT_CODE — logged and continuing."
fi

echo "=== Done — $(date '+%Y-%m-%d %H:%M:%S') ==="
exit 0
