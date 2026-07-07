#!/usr/bin/env python3
"""v5.10: 一次性 backfill 老历史平仓位到 closed_positions 表.

拉 polymarket data-api /activity 所有历史 TRADE,
按 asset 分组按 round 切分 (累计 BUY → SELL 清零 → 新 round),
每个 closed round (size 已清零的) INSERT 一行 closed_positions.

为什么需要这个: v5.10 之前 db.closed_positions 只在 monitor.auto_sell /
force_exit / execute_state 成功时写, 老 v3/v4 早期数据 + 部分 v5 数据
不在表里. 用户 5 月以来卖了几十次只有 4 行 in db, /history 页面看不到全貌.

去重: 已有 (token_id, exit_at 日期) 的不重复 INSERT — 保留 monitor 写的精确 row.

用法:
    source .venv/bin/activate
    python3 scripts/backfill_closed_positions_from_trades.py --dry-run     # 看会插多少
    python3 scripts/backfill_closed_positions_from_trades.py               # 实际写入

跑完建议:
    python3 scripts/backfill_closed_tag.py                                 # 补 tag
    python3 scripts/backfill_closed_resolution.py                          # 补 resolution
"""
import sys
import os
import json
import requests
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from modules.db import get_conn

ACTIVITY_URL = "https://data-api.polymarket.com/activity"


def fetch_all_trades(wallet):
    """拉用户全部 TRADE activity (paginate)."""
    trades = []
    offset = 0
    while True:
        r = requests.get(
            ACTIVITY_URL,
            params={"user": wallet, "limit": 500, "offset": offset, "type": "TRADE"},
            timeout=20,
        ).json()
        if not r:
            break
        trades.extend(r)
        if len(r) < 500:
            break
        offset += 500
        if offset > 5000:
            print(f"  [warn] 超过 5000 trades, 截断 (实际可能更多)")
            break
    return trades


def group_into_rounds(trades_by_asset):
    """对每个 asset, 按 trade timestamp 排序, 切成 rounds.
    Round 定义: 累计 BUY (cur_sz 增) → 累计 SELL 直到 cur_sz < 0.01 → 这个 round 算 closed.
    再有 BUY → 新 round 开始. 这跟主页 /api/closed_positions 路由的 round 逻辑一致.
    """
    rounds_by_asset = {}
    for asset, all_t in trades_by_asset.items():
        all_t = sorted(all_t, key=lambda x: x["timestamp"])
        rounds = []
        cur_round = {"buys": [], "sells": []}
        cur_sz = 0.0
        for tr in all_t:
            if tr["side"] == "BUY":
                # 上一 round 已清零 (有 sells, 且累计 size 接近 0), 开新 round
                if cur_round["sells"] and cur_sz < 0.01:
                    rounds.append(cur_round)
                    cur_round = {"buys": [], "sells": []}
                cur_round["buys"].append(tr)
                cur_sz += tr["size"]
            else:  # SELL
                cur_round["sells"].append(tr)
                cur_sz -= tr["size"]
        # 收尾: 如果最后这个 round 有 sells 且 size 已清零, 是 closed round.
        # 仍在持有 (cur_sz > 0.01) 的, 不补到 closed_positions (因为还没真正"卖完").
        if cur_round["buys"] and cur_round["sells"] and cur_sz < 0.01:
            rounds.append(cur_round)
        rounds_by_asset[asset] = rounds
    return rounds_by_asset


def build_closed_row(asset, asset_meta, rnd):
    """从一个 closed round 构造 closed_positions 一行字典."""
    buys = rnd["buys"]
    sells = rnd["sells"]
    total_buy_size = sum(b["size"] for b in buys)
    total_buy_cost = sum(b["usdcSize"] for b in buys)
    avg_entry = total_buy_cost / total_buy_size if total_buy_size > 0 else 0.0

    total_sell_size = sum(s["size"] for s in sells)
    total_sell_revenue = sum(s["usdcSize"] for s in sells)
    avg_exit = total_sell_revenue / total_sell_size if total_sell_size > 0 else 0.0

    pnl_usd = total_sell_revenue - total_buy_cost
    pnl_pct = (pnl_usd / total_buy_cost * 100) if total_buy_cost > 0 else 0.0

    entry_ts = min(b["timestamp"] for b in buys)
    exit_ts = max(s["timestamp"] for s in sells)
    hold_hrs = (exit_ts - entry_ts) / 3600

    entry_at = datetime.fromtimestamp(entry_ts, tz=timezone.utc).isoformat()
    exit_at = datetime.fromtimestamp(exit_ts, tz=timezone.utc).isoformat()

    # side: 从 BUY trade 拿 outcome (Yes/No). 同一 asset 的所有 trade outcome 一致.
    side = buys[0].get("outcome") or "Yes"

    meta = asset_meta.get(asset, {})
    slug = meta.get("slug") or meta.get("title") or ""

    return {
        "token_id": asset,
        "market_slug": slug,
        "side": side,
        "avg_entry_price": round(avg_entry, 6),
        "exit_price": round(avg_exit, 6),
        "size": round(total_sell_size, 6),
        "realized_pnl_usd": round(pnl_usd, 6),
        "realized_pnl_pct": round(pnl_pct, 4),
        "exit_reason": "BACKFILL_FROM_TRADES",
        "stop_loss_tier": None,
        "claude_raw_estimate": None,
        "entry_at": entry_at,
        "exit_at": exit_at,
        "hold_duration_hours": round(hold_hrs, 4),
        "cluster_id": None,
        "tag": None,
        "is_resolved": 0,
    }


def main(dry_run=False):
    wallet = os.getenv("POLY_FUNDER")
    if not wallet:
        print("ERROR: POLY_FUNDER not in env (.env). 没法拉 trades.")
        return 1

    print(f"[backfill_history] wallet = {wallet}")
    print(f"[backfill_history] fetching all TRADE activity...")
    trades = fetch_all_trades(wallet)
    print(f"[backfill_history] total trades fetched: {len(trades)}")
    if not trades:
        print("  no trades, done.")
        return 0

    # 按 asset 分组 + 记 asset meta
    trades_by_asset = defaultdict(list)
    asset_meta = {}
    for t in trades:
        asset = t["asset"]
        trades_by_asset[asset].append(t)
        if asset not in asset_meta:
            asset_meta[asset] = {
                "title": t.get("title", ""),
                "slug": t.get("slug", ""),
                "conditionId": t.get("conditionId", ""),
            }

    print(f"[backfill_history] {len(trades_by_asset)} unique assets traded")
    rounds_by_asset = group_into_rounds(trades_by_asset)
    total_closed = sum(len(rs) for rs in rounds_by_asset.values())
    print(f"[backfill_history] {total_closed} closed rounds detected (size 清零的)")

    # 去重: 已有 (token_id, exit_at 日期) 的不再 INSERT
    # 用日期粒度 (而不是秒) 兼容 monitor 写的 row 跟 trades timestamp 可能差几秒
    conn = get_conn()
    existing = set()
    for r in conn.execute("SELECT token_id, exit_at FROM closed_positions").fetchall():
        eat = r["exit_at"] or ""
        existing.add((r["token_id"], eat[:10]))  # YYYY-MM-DD
    conn.close()

    rows_to_insert = []
    skipped_dup = 0
    for asset, rounds in rounds_by_asset.items():
        for rnd in rounds:
            row = build_closed_row(asset, asset_meta, rnd)
            key = (row["token_id"], row["exit_at"][:10])
            if key in existing:
                skipped_dup += 1
                continue
            rows_to_insert.append(row)

    print(f"[backfill_history] {skipped_dup} 已在 db (按 token+date 去重), {len(rows_to_insert)} 新行准备插入")

    if dry_run:
        print("\n[dry-run] 前 3 行预览:")
        for r in rows_to_insert[:3]:
            print(json.dumps(r, indent=2, ensure_ascii=False))
        print(f"\n... 还有 {max(0, len(rows_to_insert)-3)} 行 (--dry-run 不会实际写)")
        return 0

    if not rows_to_insert:
        print("  没有新行要插入, 退出.")
        return 0

    conn = get_conn()
    for r in rows_to_insert:
        conn.execute(
            """INSERT INTO closed_positions
            (token_id, market_slug, side, avg_entry_price, exit_price, size,
             realized_pnl_usd, realized_pnl_pct, exit_reason, stop_loss_tier,
             claude_raw_estimate, entry_at, exit_at, hold_duration_hours,
             cluster_id, tag, is_resolved)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r["token_id"], r["market_slug"], r["side"],
                r["avg_entry_price"], r["exit_price"], r["size"],
                r["realized_pnl_usd"], r["realized_pnl_pct"], r["exit_reason"],
                r["stop_loss_tier"], r["claude_raw_estimate"],
                r["entry_at"], r["exit_at"], r["hold_duration_hours"],
                r["cluster_id"], r["tag"], r["is_resolved"],
            ),
        )
    conn.commit()
    conn.close()
    print(f"[backfill_history] ✓ INSERT 完成 {len(rows_to_insert)} 行.")
    print(f"\n下一步建议:")
    print(f"  1. python3 scripts/backfill_closed_tag.py        # 补 tag 字段")
    print(f"  2. python3 scripts/backfill_closed_resolution.py # 补 is_resolved/final_outcome")
    return 0


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    rc = main(dry_run=dry)
    sys.exit(rc)
