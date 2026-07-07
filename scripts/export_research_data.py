#!/usr/bin/env python3
"""导出"出场策略深度研究"数据包: 成交 CSV + 每个仓位的真实价格曲线 (Polymarket prices-history)。

产出 (写到 research_data/, 已 gitignore):
  - closed_positions.csv   : 全部已平仓成交台账 (含 tier/tag/结算结果)
  - price_curves.jsonl     : 每个仓位一行 JSON, 带从入场到结算的持有 token 价格走势
  - open_positions.csv     : 当前在持 (2 笔)
  - DATA_README.md         : 字段说明 (给研究 AI 看)

价格口径: token_id = 持有的那个 outcome token; prices-history 返回的就是"持有 token 自己的价格"(0-1),
与 closed_positions.avg_entry_price / exit_price 同口径 (见技术报告 v5.10.2)。
用法: source .venv/bin/activate && python3 scripts/export_research_data.py
"""
import os, sys, csv, json, time, sqlite3
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from modules.executor import Executor

OUT = os.path.join(os.path.dirname(__file__), "..", "research_data")
OUT = os.path.abspath(OUT)
os.makedirs(OUT, exist_ok=True)

c = sqlite3.connect("v4.db"); c.row_factory = sqlite3.Row
closed = [dict(r) for r in c.execute("SELECT * FROM closed_positions ORDER BY exit_at ASC")]
print(f"closed_positions: {len(closed)}")

# 1) 成交台账 CSV
cols = list(closed[0].keys()) if closed else []
with open(os.path.join(OUT, "closed_positions.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
    for r in closed:
        w.writerow(r)
print("wrote closed_positions.csv")

# 2) 价格曲线 (按唯一 token 抓一次, 再贴回每行)
exe = Executor.get()
uniq = {}
for r in closed:
    uniq.setdefault(r["token_id"], None)
print(f"fetching price curves for {len(uniq)} unique tokens (interval=max, fidelity=60)...")
for i, tok in enumerate(list(uniq.keys()), 1):
    h = exe.get_prices_history(tok, interval="max", fidelity="60", force=True)
    uniq[tok] = [[p.get("t"), p.get("p")] for p in h if p.get("t") is not None]
    print(f"  [{i}/{len(uniq)}] {tok[:18]}… {len(uniq[tok])} pts")
    time.sleep(0.15)

with open(os.path.join(OUT, "price_curves.jsonl"), "w") as f:
    for r in closed:
        rec = {
            "id": r["id"], "token_id": r["token_id"], "slug": r["market_slug"],
            "side": r["side"], "tag": r["tag"], "tier": r["stop_loss_tier"],
            "entry_at": r["entry_at"], "avg_entry_price": r["avg_entry_price"],
            "exit_at": r["exit_at"], "exit_price": r["exit_price"], "size": r["size"],
            "exit_reason": r["exit_reason"], "realized_pnl_usd": r["realized_pnl_usd"],
            "is_resolved": r["is_resolved"], "final_outcome": r["final_outcome"],
            "is_correct": r["is_correct"], "claude_raw_estimate": r["claude_raw_estimate"],
            "curve": uniq.get(r["token_id"], []),   # [[unix_ts, price], ...] 持有 token 价格
        }
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print("wrote price_curves.jsonl")

# 3) 当前在持
try:
    ps = exe.get_positions() or []
    with open(os.path.join(OUT, "open_positions.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["token_id", "title", "side", "avg_price", "cur_price", "size"])
        for p in ps:
            w.writerow([p.get("asset"), p.get("title"), p.get("outcome"),
                        p.get("avg_price"), p.get("cur_price"), p.get("size")])
    print(f"wrote open_positions.csv ({len(ps)})")
except Exception as e:
    print("open positions skip:", e)

print("DONE ->", OUT)
