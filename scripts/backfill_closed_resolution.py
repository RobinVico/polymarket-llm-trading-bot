#!/usr/bin/env python3
"""v5.10: 一次性回填 closed_positions 的 resolution 状态.

跑一次, 把所有 is_resolved=0 的老 closed_positions 调 Polymarket Gamma 查
是否已经结算 (closed=true + outcomePrices=[0,1] / [1,0]).
能查到结果的 → 写 is_resolved=1 + final_outcome + resolved_at + is_correct.
查不到 (市场未结算 / 找不到 / Gamma 报错) → 保持 is_resolved=0, 下次 cron 心跳重试.

用法:
    source .venv/bin/activate
    python3 scripts/backfill_closed_resolution.py

注: 安全可重复跑, update_closed_resolution 有 WHERE is_resolved=0 守卫, 二次跑只补漏掉的.
"""
import sys
import os

# 确保能 import modules.* (从 polymarket 根目录跑)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.gamma_client import install_polymarket_dns_guard
install_polymarket_dns_guard()  # v5.10.2: 本机 DNS 污染兜底

from modules.resolution_check import update_unresolved_closed_positions
from modules.db import get_unresolved_closed_positions


def main():
    rows_before = get_unresolved_closed_positions(limit=10000)
    print(f"[backfill_resolution] 当前 is_resolved=0 共 {len(rows_before)} 行.")
    if not rows_before:
        print("[backfill_resolution] 无待处理行, 退出.")
        return
    checked, updated = update_unresolved_closed_positions(limit=10000)
    rows_after = get_unresolved_closed_positions(limit=10000)
    print(f"[backfill_resolution] 查 {checked} 行, 成功更新 {updated} 行. 剩余未结算 {len(rows_after)} 行 (这些应该是市场真没结算).")


if __name__ == "__main__":
    main()
