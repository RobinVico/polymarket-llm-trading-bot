#!/bin/bash
set -u
# Resolve project root relative to this script (script lives in <project>/scripts/).
cd "$(dirname "$0")/.." || exit 1

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# 导出 portfolio_snapshot 到 JSONL,以便随 git 推到远端备份
python3 -c "from modules.db import export_portfolio_snapshot; n=export_portfolio_snapshot('data/portfolio_snapshot.jsonl'); print(f'exported {n} portfolio snapshots')" 2>&1 || echo "snapshot export failed"

ts=$(date '+%Y-%m-%d %H:%M')
changes="$(git status --porcelain)"

if [[ -n "$changes" ]]; then
  echo "[$ts] changes detected, auto-committing"
  git add -A
  git commit -m "auto-backup $ts" || exit 1
fi

# Retry the push even when this run had no new changes, so a previous run that
# committed but failed to push (e.g. expired credential) doesn't silently
# backlog. @{u} = upstream tracking branch (dev/main).
ahead=$(git rev-list --count @{u}..HEAD 2>/dev/null || echo 0)
if [[ -z "$changes" && "$ahead" == "0" ]]; then
  exit 0
fi

if git push 2>&1; then
  rm -f data/.backup_push_failing
else
  rc=$?
  fail_ts=$(date '+%Y-%m-%d %H:%M')
  upstream=$(git rev-parse --abbrev-ref @{u} 2>/dev/null || echo '?')
  echo "[$fail_ts] !!! PUSH FAILED (rc=$rc): $ahead commit(s) unpushed to $upstream"
  printf '%s push failed (rc=%s): %s commit(s) unpushed to %s\n' "$fail_ts" "$rc" "$ahead" "$upstream" > data/.backup_push_failing
  exit 1
fi
