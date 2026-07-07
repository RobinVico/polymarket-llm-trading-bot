#!/usr/bin/env python3
"""v5.10.2 数据迁移: 修复 closed_positions 两个口径 bug (幂等, migrations 哨兵表防重复跑).

Bug 1: save_closed_position 对 No 仓把 PnL 算成 (avg - exit) × size.
       实际传入的 avg/exit 都是"持有 token 自己的价格" (No 仓就是 No token 价),
       正确公式不分 side 永远是 (exit - avg) × size. 受影响 = 非 BACKFILL 的 No 仓行
       (BACKFILL_FROM_TRADES 行是从 trade 数据直接算的, 本来就对).

Bug 2: update_closed_resolution 把 final_outcome 当"Yes 的最终概率"对 No 仓翻转 is_correct.
       实际 check_resolution 返回的已经是"持有 side 的最终概率",
       正确公式不分 side 永远是 is_correct = (final_outcome >= 0.5).
"""
import os
import shutil
import sqlite3
import sys
import time

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = "v4.db"
MIGRATION_NAME = "v5_10_2_pnl_iscorrect"


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY, applied_at TEXT)")
    if conn.execute("SELECT 1 FROM migrations WHERE name=?", (MIGRATION_NAME,)).fetchone():
        print(f"{MIGRATION_NAME} 已应用过, 跳过 (幂等守卫).")
        return 0

    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    bak = f"v4.db.bak_{int(time.time())}"
    shutil.copy2(DB, bak)
    print(f"备份 → {bak}")

    flips = conn.execute(
        """SELECT id, market_slug, exit_reason, realized_pnl_usd FROM closed_positions
           WHERE UPPER(side)='NO' AND exit_reason NOT LIKE 'BACKFILL%'"""
    ).fetchall()
    print(f"\n[Bug 1] PnL 符号翻转 {len(flips)} 行:")
    for r in flips:
        print(f"  id={r['id']:<3} {r['market_slug'][:42]:<44} {r['exit_reason']:<24} "
              f"{r['realized_pnl_usd']:+.2f} → {-r['realized_pnl_usd']:+.2f}")
    conn.execute(
        """UPDATE closed_positions
           SET realized_pnl_usd = -realized_pnl_usd, realized_pnl_pct = -realized_pnl_pct
           WHERE UPPER(side)='NO' AND exit_reason NOT LIKE 'BACKFILL%'"""
    )

    rows = conn.execute(
        """SELECT id, side, final_outcome, is_correct FROM closed_positions
           WHERE is_resolved=1 AND final_outcome IS NOT NULL"""
    ).fetchall()
    wrong = [r for r in rows if (1 if r["final_outcome"] >= 0.5 else 0) != r["is_correct"]]
    print(f"\n[Bug 2] is_correct 修正 {len(wrong)} 行: ids={[r['id'] for r in wrong]}")
    conn.execute(
        """UPDATE closed_positions
           SET is_correct = CASE WHEN final_outcome >= 0.5 THEN 1 ELSE 0 END
           WHERE is_resolved=1 AND final_outcome IS NOT NULL"""
    )

    conn.execute("INSERT INTO migrations (name, applied_at) VALUES (?, datetime('now'))",
                 (MIGRATION_NAME,))
    conn.commit()

    chk = conn.execute(
        """SELECT ROUND(SUM(realized_pnl_usd),2) AS pnl,
                  SUM(CASE WHEN is_resolved=1 AND is_correct=1 THEN 1 ELSE 0 END) AS wins,
                  SUM(is_resolved) AS resolved
           FROM closed_positions"""
    ).fetchone()
    print(f"\n完成. 修正后: 累计 PnL = {chk['pnl']}, 已结算胜率 = {chk['wins']}/{chk['resolved']}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
