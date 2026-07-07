#!/bin/bash
# Prepare a sanitized snapshot of the repo for publication to the public release repo.
#
# Workflow (assumes you are on the private `main` branch with real values):
#   git checkout --orphan pubrel               # ORPHAN branch: the release commit must have NO parents,
#                                              # otherwise the entire private history gets pushed public
#                                              # (this actually happened in the v5.7 release — 887 commits)
#   bash scripts/prepare-public.sh             # apply all sanitization in-place
#   git add -A
#   git commit -m "Public release vX.X"
#   git push public pubrel:main --force        # overwrite public main with this ONE clean commit
#   git checkout -f main && git branch -D pubrel   # back to private dev (sanitized copy discarded)
#
# This script is idempotent — running twice yields the same result.
# It MUST run from the project root.
#
# Implementation notes:
#   - The real-value patterns are NOT in this file — they live in gitignored
#     scripts/.release-patterns.sh (this script is published, the patterns are not;
#     pre-v7.4.4 the tailnet hostname was embedded here and visible publicly).
#   - Inside the patterns file, values are split via string concatenation so the
#     sed passes can never match/mangle them; both files are also excluded from
#     the sed find commands by path.

set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> prepare-public.sh: sanitizing repo for public release"

# Load private patterns (gitignored). Must define:
#   REAL_HOST_RE, REAL_V3_RE, REAL_USERHOME_V3, REAL_USERHOME_ROOT
PATTERNS_FILE="scripts/.release-patterns.sh"
if [ ! -f "$PATTERNS_FILE" ]; then
  echo "ERROR: $PATTERNS_FILE missing — it is gitignored and holds the private" >&2
  echo "       sanitization patterns (4 REAL_* vars). Recreate it before releasing." >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$PATTERNS_FILE"

PLACEHOLDER_HOST="<hostname>.<tailnet>.ts.net"
PLACEHOLDER_V3="<sibling-v3-dir, not in this repo>"
PLACEHOLDER_USERHOME_V3="<sibling-v3-dir>"
PLACEHOLDER_USERHOME_ROOT="<project-root>"

THIS_SCRIPT="scripts/prepare-public.sh"

# ---------------------------------------------------------------
# 1) Tailscale URL -> placeholder
# ---------------------------------------------------------------
echo "  - Tailscale URL -> placeholder"
find . -type f \( -name "*.md" -o -name "*.py" -o -name "*.sh" \) \
  -not -path "./.git/*" -not -path "./.venv/*" -not -path "./node_modules/*" \
  -not -path "./$THIS_SCRIPT" -not -path "./$PATTERNS_FILE" \
  -exec sed -i.bak "s|$REAL_HOST_RE|$PLACEHOLDER_HOST|g" {} +
find . -name "*.bak" -not -path "./.git/*" -delete

# ---------------------------------------------------------------
# 2) v3 path -> placeholder
# ---------------------------------------------------------------
echo "  - v3 path -> placeholder"
find . -type f \( -name "*.md" -o -name "*.py" -o -name "*.sh" \) \
  -not -path "./.git/*" -not -path "./.venv/*" \
  -not -path "./$THIS_SCRIPT" -not -path "./$PATTERNS_FILE" \
  -exec sed -i.bak "s|$REAL_V3_RE|$PLACEHOLDER_V3|g" {} +
find . -name "*.bak" -not -path "./.git/*" -delete

# ---------------------------------------------------------------
# 3) Delete past/ runtime cruft that shouldn't be in any repo (was archived by mistake).
#    These files have been seen leaking real paths, real SQLite DBs, real logs, real
#    wallet addresses, etc. They are NOT useful for historical reference — the source
#    code in past/v*/modules/ is the historical reference.
# ---------------------------------------------------------------
echo "  - Delete past/ runtime cruft (logs, dbs, backups, env)"
find past/ -type f \( \
  -name "*.log" \
  -o -name "v4.db" \
  -o -name "v4.db-*" \
  -o -name "v4.db.bak*" \
  -o -name ".env" \
  -o -name ".env.bak*" \
  -o -name "last_scan.md" \
  -o -name "portfolio_snapshot.jsonl" \
\) -delete 2>/dev/null || true

# ---------------------------------------------------------------
# 4) Hard-coded /Users/baymaxagent/... -> placeholders (past/ only, including .bak* legacy archives).
#    Use perl instead of sed because BSD sed -i.bak behavior with -exec ... + glob
#    against unusual filenames (e.g. .bak_pre_v2sdk) was unreliable; perl -i is portable.
# ---------------------------------------------------------------
echo "  - /Users/... -> placeholder (past/, all file types)"
find past/ -type f | while read -r f; do
  perl -i -pe "s|\Q$REAL_USERHOME_V3\E|$PLACEHOLDER_USERHOME_V3|g; s|\Q$REAL_USERHOME_ROOT\E|$PLACEHOLDER_USERHOME_ROOT|g" "$f" 2>/dev/null || true
done

# ---------------------------------------------------------------
# 5) Remove real PnL history (data/portfolio_snapshot.jsonl); add .gitkeep
# ---------------------------------------------------------------
echo "  - Remove data/portfolio_snapshot.jsonl"
rm -f data/portfolio_snapshot.jsonl
touch data/.gitkeep

# ---------------------------------------------------------------
# 5b) Remove internal strategy-overview PPT artifacts (generated decks
#     labelled 内部资料 with real account balances / live positions).
# ---------------------------------------------------------------
echo "  - Remove internal strategy PPT artifacts (*.pptx in root)"
rm -f ./*.pptx

# ---------------------------------------------------------------
# 6) Add portfolio_snapshot.jsonl to .gitignore
# ---------------------------------------------------------------
echo "  - Add portfolio_snapshot.jsonl to .gitignore"
if ! grep -q "data/portfolio_snapshot.jsonl" .gitignore; then
  printf '\n# Real-money PnL history (not in public repo)\ndata/portfolio_snapshot.jsonl\n' >> .gitignore
fi

# ---------------------------------------------------------------
# 7) Sanity check (excludes this script)
# ---------------------------------------------------------------
echo ""
echo "==> Sanity check"
SANITY_RE="$REAL_HOST_RE|$REAL_USERHOME_V3|$REAL_USERHOME_ROOT|polymarket""-semi-auto"
# Only TRACKED files matter — untracked/ignored runtime files (.env, *.log,
# v4.db, .env.bak_*) never enter the commit, so scanning the raw filesystem
# gives false positives. -I skips binaries; deleted-but-still-indexed paths
# are silently skipped via 2>/dev/null.
REMAINING=$(git ls-files | grep -v "^scripts/prepare-public\.sh$" | tr '\n' '\0' \
  | xargs -0 grep -nIE "$SANITY_RE" 2>/dev/null || true)
if [ -n "$REMAINING" ]; then
  echo "WARNING: real values still present:"
  echo "$REMAINING" | head -10
  exit 1
fi
echo "  OK: no real values found"

echo ""
echo "==> Done. Next steps (run from the orphan branch, see header):"
echo "    git add -A"
echo "    git commit -m \"Public release vX.X\""
echo "    git push public pubrel:main --force"
echo "    git checkout -f main && git branch -D pubrel"
