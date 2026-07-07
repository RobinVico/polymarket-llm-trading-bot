#!/usr/bin/env python3
"""v7.3.1 (#8 数据修正, 2026-07-06): 重建 closed_positions 使其与 Polymarket 对齐。

问题 (对账发现):
  - closed_positions 混了两套口径: backfill(一个买卖回合一行) + monitor(一次卖出一行, 拆单重复计).
  - 5 个"部分卖出误记成平仓"的假平仓行 (仓位其实还持有).
  - 结果: 行数100/唯一token85, 跟 Polymarket(98唯一token, 其中80已完全平掉) 对不上;
    "赚最多5笔"排名也错 (单次卖出拆散了整体 PnL).

修法 (Polymarket /activity 真实成交 = ground truth):
  对每个"完全平掉(净size≈0)"的 token 聚合成【一行】:
    avg_entry = Σ买成本/Σ买size, avg_exit = Σ卖收入/Σ卖size, pnl = Σ卖收入-Σ买成本 (跟 Polymarket 一致).
  保留现有行的元数据 (按 token merge 非空): stop_loss_tier / tag / cluster_id / claude_raw_estimate /
    is_resolved / final_outcome / is_correct / resolved_at; exit_reason 优先取现有的真实决策(非BACKFILL).
  还持有的 token 不进 closed_positions (它们是活跃仓位).

用法:
    python3 scripts/rebuild_closed_positions_2026_07.py --dry-run   # 预览, 不写
    python3 scripts/rebuild_closed_positions_2026_07.py             # 执行 (务必先备份 v4.db!)
"""
import os, sys, requests
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
from modules.db import get_conn

ACTIVITY_URL = "https://data-api.polymarket.com/activity"
CLOSED_EPS = 0.01   # 净 size < 此值 视为完全平掉


def fetch_all_trades(wallet):
    trades, off = [], 0
    while True:
        r = requests.get(ACTIVITY_URL, params={"user": wallet, "limit": 500, "offset": off, "type": "TRADE"}, timeout=25).json()
        if not r:
            break
        trades.extend(r)
        if len(r) < 500:
            break
        off += 500
        if off > 8000:
            print("  [warn] >8000 trades, 截断"); break
    return trades


def build_meta_map(conn):
    """从现有 closed_positions 按 token merge 元数据 (非空优先; is_resolved 取 max; 真实exit_reason优先)."""
    meta = defaultdict(lambda: {"stop_loss_tier": None, "tag": None, "cluster_id": None,
                                "claude_raw_estimate": None, "is_resolved": 0, "final_outcome": None,
                                "is_correct": None, "resolved_at": None, "exit_reason": None})
    for r in conn.execute("SELECT * FROM closed_positions ORDER BY id ASC").fetchall():
        m = meta[r["token_id"]]
        for k in ("stop_loss_tier", "tag", "cluster_id", "claude_raw_estimate"):
            if (m[k] in (None, "")) and (r[k] not in (None, "")):
                m[k] = r[k]
        if r["is_resolved"] == 1:
            m["is_resolved"] = 1
            if m["final_outcome"] is None and r["final_outcome"] is not None:
                m["final_outcome"] = r["final_outcome"]
            if m["is_correct"] is None and r["is_correct"] is not None:
                m["is_correct"] = r["is_correct"]
            if m["resolved_at"] in (None, "") and r["resolved_at"] not in (None, ""):
                m["resolved_at"] = r["resolved_at"]
        er = r["exit_reason"] or ""
        if er and not er.startswith("BACKFILL") and not m["exit_reason"]:
            m["exit_reason"] = er   # 真实决策优先
    return meta


def main(dry_run=False):
    wallet = os.getenv("POLY_FUNDER")
    if not wallet:
        print("ERROR: POLY_FUNDER 缺失"); return 1
    print(f"[rebuild] wallet={wallet[:10]}...  拉 Polymarket /activity ...")
    trades = fetch_all_trades(wallet)
    print(f"[rebuild] 总 TRADE={len(trades)}")
    by_asset = defaultdict(list)
    for t in trades:
        by_asset[t["asset"]].append(t)
    print(f"[rebuild] 唯一 token={len(by_asset)}")

    conn = get_conn()
    meta_map = build_meta_map(conn)

    rows, open_cnt = [], 0
    for asset, ts in by_asset.items():
        ts = sorted(ts, key=lambda x: x["timestamp"])
        buy_sz = sum(t["size"] for t in ts if t["side"] == "BUY")
        sell_sz = sum(t["size"] for t in ts if t["side"] == "SELL")
        if (buy_sz - sell_sz) >= CLOSED_EPS:
            open_cnt += 1
            continue   # 还持有, 不算平仓
        buy_cost = sum(t["usdcSize"] for t in ts if t["side"] == "BUY")
        sell_rev = sum(t["usdcSize"] for t in ts if t["side"] == "SELL")
        if buy_sz <= 0 or sell_sz <= 0:
            continue   # 异常 (只买没卖 / 只卖没买), 跳过
        avg_entry = buy_cost / buy_sz
        avg_exit = sell_rev / sell_sz
        pnl = sell_rev - buy_cost
        pnl_pct = (pnl / buy_cost * 100) if buy_cost > 0 else 0.0
        entry_ts = min(t["timestamp"] for t in ts if t["side"] == "BUY")
        exit_ts = max(t["timestamp"] for t in ts if t["side"] == "SELL")
        side = next((t.get("outcome") for t in ts if t["side"] == "BUY" and t.get("outcome")), "Yes")
        slug = next((t.get("slug") or t.get("title") for t in ts if (t.get("slug") or t.get("title"))), "")
        m = meta_map.get(asset, {})
        rows.append({
            "token_id": asset, "market_slug": slug, "side": side,
            "avg_entry_price": round(avg_entry, 6), "exit_price": round(avg_exit, 6),
            "size": round(sell_sz, 6), "realized_pnl_usd": round(pnl, 6), "realized_pnl_pct": round(pnl_pct, 4),
            "exit_reason": m.get("exit_reason") or "REBUILT_FROM_TRADES",
            "stop_loss_tier": m.get("stop_loss_tier"), "claude_raw_estimate": m.get("claude_raw_estimate"),
            "entry_at": datetime.fromtimestamp(entry_ts, tz=timezone.utc).isoformat(),
            "exit_at": datetime.fromtimestamp(exit_ts, tz=timezone.utc).isoformat(),
            "hold_duration_hours": round((exit_ts - entry_ts) / 3600, 4),
            "cluster_id": m.get("cluster_id"), "tag": m.get("tag"),
            "is_resolved": m.get("is_resolved") or 0, "resolved_at": m.get("resolved_at"),
            "final_outcome": m.get("final_outcome"), "is_correct": m.get("is_correct"),
        })

    print(f"[rebuild] 完全平掉的 token={len(rows)}  还持有(不入表)={open_cnt}")
    old_count = conn.execute("SELECT COUNT(*) FROM closed_positions").fetchone()[0]
    print(f"[rebuild] 旧 closed_positions 行数={old_count} → 新={len(rows)}")
    resolved_n = sum(1 for r in rows if r["is_resolved"] == 1)
    tier_n = sum(1 for r in rows if r["stop_loss_tier"])
    print(f"[rebuild] 新表中: is_resolved=1 有 {resolved_n} 行, 有tier {tier_n} 行, 有tag {sum(1 for r in rows if r['tag'])} 行")
    top = sorted(rows, key=lambda r: r["realized_pnl_usd"], reverse=True)[:5]
    print("[rebuild] 重建后 赚最多5笔 (应跟 Polymarket 一致):")
    for r in top:
        print(f"    +${r['realized_pnl_usd']:.2f}  {(r['market_slug'] or '')[:44]}")
    total_pnl = sum(r["realized_pnl_usd"] for r in rows)
    print(f"[rebuild] 新表累计已实现盈亏 = ${total_pnl:.2f}")

    if dry_run:
        conn.close()
        print("\n[dry-run] 未写入。确认无误后去掉 --dry-run 再跑。")
        return 0

    conn.execute("DELETE FROM closed_positions")
    for r in rows:
        conn.execute(
            """INSERT INTO closed_positions
            (token_id, market_slug, side, avg_entry_price, exit_price, size,
             realized_pnl_usd, realized_pnl_pct, exit_reason, stop_loss_tier, claude_raw_estimate,
             entry_at, exit_at, hold_duration_hours, cluster_id, tag,
             is_resolved, resolved_at, final_outcome, is_correct)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["token_id"], r["market_slug"], r["side"], r["avg_entry_price"], r["exit_price"], r["size"],
             r["realized_pnl_usd"], r["realized_pnl_pct"], r["exit_reason"], r["stop_loss_tier"], r["claude_raw_estimate"],
             r["entry_at"], r["exit_at"], r["hold_duration_hours"], r["cluster_id"], r["tag"],
             r["is_resolved"], r["resolved_at"], r["final_outcome"], r["is_correct"]))
    conn.commit(); conn.close()
    print(f"\n✅ 重建完成: closed_positions 现在 {len(rows)} 行 (跟 Polymarket 对齐)。")
    print("   建议再跑: python3 scripts/backfill_closed_resolution.py  (补未结算的 is_resolved/final_outcome)")
    return 0


if __name__ == "__main__":
    sys.exit(main(dry_run=("--dry-run" in sys.argv)))
