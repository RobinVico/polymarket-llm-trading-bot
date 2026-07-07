#!/usr/bin/env python3
"""导出"该砍 vs 该扛"深度研究数据包: 每次重评决策 + 之后真实价格走势 + 最终结局。

产出 (写到 research_data/, 已 gitignore):
  - reeval_decisions.jsonl : 每条重评决策一行 JSON, 带 决策当时的价/q + 之后的完整价格曲线 + 结局
  - closed_positions.csv   : 全部已平仓台账 (复用 export_research_data 的口径, 给上下文)
  - REEVAL_DATA_README.md   : 字段说明

核心: 让研究 AI 能对每次"重评说扛/卖"的决策, 看 created_at 之后价格到底怎么走 (回弹 or 继续跌) + 最终输赢,
从而学出"什么时候该砍、什么时候该扛"的判别信号。价格口径 = 持有 token 自身价 (0-1)。
用法: source .venv/bin/activate && python3 scripts/export_reeval_research.py
"""
import os, sys, csv, json, time, sqlite3
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from modules.executor import Executor

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "research_data"))
os.makedirs(OUT, exist_ok=True)
c = sqlite3.connect("v4.db"); c.row_factory = sqlite3.Row

# 1) 所有重评决策 (含已清空的; status != cleared 也含, 全要)
sugs = [dict(r) for r in c.execute("SELECT * FROM auto_reeval_suggestions ORDER BY created_at ASC")]
print(f"auto_reeval_suggestions: {len(sugs)} 条")

# 2) closed_positions 全量 (给结局 join + 单独导 csv)
closed = [dict(r) for r in c.execute("SELECT * FROM closed_positions ORDER BY exit_at ASC")]
cols = list(closed[0].keys()) if closed else []
with open(os.path.join(OUT, "closed_positions.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
    for r in closed:
        w.writerow(r)
print("wrote closed_positions.csv")

# 3) 每个唯一 token 抓一次价格曲线
exe = Executor.get()
toks = sorted({s["token_id"] for s in sugs if s.get("token_id")})
print(f"fetching price curves for {len(toks)} unique tokens…")
curves = {}
for i, tk in enumerate(toks, 1):
    h = exe.get_prices_history(tk, interval="max", fidelity="60", force=True)
    curves[tk] = [[p.get("t"), p.get("p")] for p in (h or []) if p.get("t") is not None]
    print(f"  [{i}/{len(toks)}] {tk[:18]}… {len(curves[tk])} pts")
    time.sleep(0.15)


def outcome_for(tok, created_at):
    """该 token 在该决策之后最早的一次平仓 (给结局)。无 → None (仍持有/未平)。"""
    cand = [r for r in closed if r["token_id"] == tok and (r.get("exit_at") or "") > (created_at or "")]
    if not cand:
        # 退一步: 该 token 任意平仓记录 (可能 created_at 时间口径略错)
        cand = [r for r in closed if r["token_id"] == tok]
    if not cand:
        return None
    r = sorted(cand, key=lambda x: x.get("exit_at") or "")[0]
    return {k: r.get(k) for k in ("exit_price", "exit_reason", "exit_at", "is_resolved",
                                  "final_outcome", "is_correct", "realized_pnl_usd")}


with open(os.path.join(OUT, "reeval_decisions.jsonl"), "w") as f:
    for s in sugs:
        rec = {
            "id": s["id"], "token_id": s["token_id"], "slug": s.get("slug"), "title": s.get("title"),
            "side": s.get("side"), "action": s.get("action"), "orig_q": s.get("orig_q"),
            "new_q": s.get("new_q"), "cur_price_at_decision": s.get("cur_price"),
            "avg_price": s.get("avg_price"), "loss_pct": s.get("loss_pct"),
            "thesis_broken": s.get("thesis_broken"), "confidence": s.get("confidence"),
            "provider": s.get("provider"), "pre_dump_center": s.get("pre_dump_center"),
            "created_at": s.get("created_at"), "decided_at": s.get("decided_at"),
            "status": s.get("status"), "reason": s.get("reason"), "headline_event": s.get("headline_event"),
            "outcome": outcome_for(s["token_id"], s.get("created_at")),
            "curve": curves.get(s["token_id"], []),   # [[unix_ts, price], ...] 持有 token 价 (全时段)
        }
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print("wrote reeval_decisions.jsonl")
print("DONE ->", OUT)
