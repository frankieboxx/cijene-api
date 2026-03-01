#!/bin/bash
set -euo pipefail

# Crawl-and-import pipeline with email reporting
# Runs the Python orchestrator that crawls, imports, and sends MJML report

DATE="${1:-}"
OUTPUT_DIR="${OUTPUT_DIR:-/app/output}"
CHAINS="${CRAWL_CHAINS:-}"
EXTRA_ARGS=""

if [ -n "$DATE" ]; then
    EXTRA_ARGS="$EXTRA_ARGS --date $DATE"
fi

if [ -n "$CHAINS" ]; then
    EXTRA_ARGS="$EXTRA_ARGS --chains $CHAINS"
fi

echo "=== Crawl & Import Pipeline ==="
uv run python -m scripts.pipeline --output-dir "$OUTPUT_DIR" $EXTRA_ARGS
echo "=== Done ==="
