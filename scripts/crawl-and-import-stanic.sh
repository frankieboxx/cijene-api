#!/bin/bash
# Stanić / wholesale nightly crawl & import pipeline
# Scheduled at 23:00 Zagreb time (21:00 UTC) — separate from the standard 16:00 retail crawl.
# Sleep between HTTP requests is handled inside StanicCrawler (REQUEST_DELAY).
# Script exits 0 even on failure so Railway does not restart the cron container.

DATE="${1:-}"
OUTPUT_DIR="${OUTPUT_DIR:-/app/output}"
EXTRA_ARGS="--chains stanic"

if [ -n "$DATE" ]; then
    EXTRA_ARGS="$EXTRA_ARGS --date $DATE"
fi

echo "=== Stanić Crawl & Import Pipeline — $(date '+%Y-%m-%d %H:%M:%S') ==="

# Disable errexit so a pipeline failure continues past this point
set +e

uv run python -m scripts.pipeline --output-dir "$OUTPUT_DIR" $EXTRA_ARGS
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "WARNING: Pipeline exited with code $EXIT_CODE — logged and continuing."
fi

echo "=== Done — $(date '+%Y-%m-%d %H:%M:%S') ==="
exit 0
