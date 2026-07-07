#!/bin/bash
set -u
cd <project-root>/v4 || exit 1

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# 导出 portfolio_snapshot 到 JSONL,以便随 git 推到远端备份
python3 -c "from modules.db import export_portfolio_snapshot; n=export_portfolio_snapshot('data/portfolio_snapshot.jsonl'); print(f'exported {n} portfolio snapshots')" 2>&1 || echo "snapshot export failed"

if [[ -z "$(git status --porcelain)" ]]; then
  exit 0
fi

ts=$(date '+%Y-%m-%d %H:%M')
echo "[$ts] changes detected, auto-committing"
git add -A
git commit -m "auto-backup $ts" || exit 1
git push 2>&1
