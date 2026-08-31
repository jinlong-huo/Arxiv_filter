#!/usr/bin/env bash
#
# run_daily.sh — fetch → verify → download → rename → audit
#
# Runs the arXiv pipeline once, verifies the digest is healthy, then
# gates PDF download + rename on success.  Fails loudly and
# early if the fetch produced nothing usable (rate-limit, API outage, 0 matches).
#
# Usage:
#     ./run_daily.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIGEST_FILE="$SCRIPT_DIR/daily_digest.md"
TODAY="$(date '+%Y-%m-%d')"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

echo "══════════════════════════════════════════════════"
echo "  arXiv Daily Pipeline — $TODAY"
echo "══════════════════════════════════════════════════"
echo ""

# ──────────────────────────────────────────────
# Step 1 — Fetch + Filter + Digest
# ──────────────────────────────────────────────
echo "[1/3] Fetching & filtering …"
echo ""

cd "$SCRIPT_DIR"
python3 Arxiv_filter.py --wait
FETCH_RC=$?

echo ""

# ──────────────────────────────────────────────
# Step 2 — Verify
# ──────────────────────────────────────────────
echo "[2/3] Verifying digest …"

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
    echo "Aborting — PDFs NOT downloaded."
    exit 1
fi

echo -e "${GREEN}✓ Digest looks healthy${NC}  ($DIGEST_FILE)"
echo ""

# ──────────────────────────────────────────────
# Step 3 — Download PDFs
# ──────────────────────────────────────────────
echo "[3/3] Downloading PDFs …"
echo ""

python3 download_papers.py

echo ""

# ──────────────────────────────────────────────
# Step 4 — Rename PDFs
# ──────────────────────────────────────────────
echo "[rename] Renaming PDFs …"
echo ""

python3 rename_papers.py

echo ""

# ──────────────────────────────────────────────
# Step 5 — Verify downloads (report-only audit)
# ──────────────────────────────────────────────
echo "[verify] Auditing digest papers against download folder …"
echo ""

python3 verify_downloads.py || true

echo ""
echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Done — digest generated + PDFs downloaded + renamed${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
