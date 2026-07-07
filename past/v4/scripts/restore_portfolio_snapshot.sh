#!/bin/bash
# 从 data/portfolio_snapshot.jsonl 恢复 portfolio_snapshot 表
# 用法: bash scripts/restore_portfolio_snapshot.sh
# 安全: INSERT OR IGNORE, 主键 ts 去重, 可重复跑

set -u
cd <project-root>/v4 || exit 1

if [[ ! -f data/portfolio_snapshot.jsonl ]]; then
  echo "ERROR: data/portfolio_snapshot.jsonl not found"
  exit 1
fi

source .venv/bin/activate 2>/dev/null

python3 -c "
from modules.db import init_db, import_portfolio_snapshot
init_db()
inserted, skipped = import_portfolio_snapshot('data/portfolio_snapshot.jsonl')
print(f'restored: {inserted} new rows inserted, {skipped} already existed (deduped by ts)')
"
