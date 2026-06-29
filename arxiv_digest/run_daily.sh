#!/usr/bin/env bash
#
# run_daily.sh — fetch → verify → send → download → rename
#
# Runs the arXiv pipeline once, verifies the digest is healthy, then
# gates email + PDF download + rename on success.  Fails loudly and
# early if the fetch produced nothing usable (rate-limit, API outage, 0 matches).
#
# Usage:
#     ./run_daily.sh              # full run (no flags)
#     ./run_daily.sh --skip-send  # fetch + verify + download, skip email
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIGEST_FILE="$SCRIPT_DIR/daily_digest.md"
TODAY="$(date '+%Y-%m-%d')"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "══════════════════════════════════════════════════"
echo "  arXiv Daily Pipeline — $TODAY"
echo "══════════════════════════════════════════════════"
echo ""

# ──────────────────────────────────────────────
# Step 1 — Fetch + Filter + Digest
# ──────────────────────────────────────────────
echo "[1/4] Fetching & filtering …"
echo ""

cd "$SCRIPT_DIR"
python3 Arxiv_filter.py
FETCH_RC=$?

echo ""

# ──────────────────────────────────────────────
# Step 2 — Verify
# ──────────────────────────────────────────────
echo "[2/4] Verifying digest …"

FAILS=()

if [[ $FETCH_RC -ne 0 ]]; then
    FAILS+=("Arxiv_filter.py exited with code $FETCH_RC")
fi

if [[ ! -f "$DIGEST_FILE" ]]; then
    FAILS+=("Digest file missing: $DIGEST_FILE")
elif [[ ! -s "$DIGEST_FILE" ]]; then
    FAILS+=("Digest file is empty: $DIGEST_FILE")
else
    if ! grep -q "$TODAY" "$DIGEST_FILE"; then
        FAILS+=("Today's date ($TODAY) not found in digest")
    fi
    # At least one paper entry (### N.)
    if ! grep -qE '^### \d+\.' "$DIGEST_FILE"; then
        FAILS+=("No paper entries (### N.) found in digest")
    fi
fi

if [[ ${#FAILS[@]} -gt 0 ]]; then
    echo -e "${RED}✗ Verification FAILED:${NC}"
    for f in "${FAILS[@]}"; do
        echo -e "  ${RED}•${NC} $f"
    done
    echo ""
    echo "Aborting — email NOT sent, PDFs NOT downloaded."
    exit 1
fi

echo -e "${GREEN}✓ Digest looks healthy${NC}  ($DIGEST_FILE)"
echo ""

# ──────────────────────────────────────────────
# Step 3 — Send email (unless --skip-send)
# ──────────────────────────────────────────────
if [[ "${1:-}" == "--skip-send" ]]; then
    echo "[3/5] Email skipped (--skip-send flag)"
else
    echo "[3/5] Sending email …"
    if python3 Arxiv_filter.py --send-only; then
        echo -e "${GREEN}✓ Email sent${NC}"
    else
        echo -e "${YELLOW}⚠ Email failed — continuing to download anyway${NC}"
    fi
fi

echo ""

# ──────────────────────────────────────────────
# Step 4 — Download PDFs
# ──────────────────────────────────────────────
echo "[4/5] Downloading PDFs …"
echo ""

python3 download_papers.py

echo ""

# ──────────────────────────────────────────────
# Step 5 — Rename PDFs
# ──────────────────────────────────────────────
echo "[5/5] Renaming PDFs …"
echo ""

python3 rename_papers.py

echo ""
echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Done — digest sent + PDFs downloaded + renamed${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
