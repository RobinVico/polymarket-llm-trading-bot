# v5 — Archived

This is the v5 codebase, archived for historical reference.

**Not actively maintained or run.** As of 2026-05-18, v5.1 (at the repo root) supersedes v5.

## v5 状态 (本归档代表的内容)

v5 系列的最终代码状态,包括 v5 系列内部演进:

- v5.0 三层止损框架 (替换 v4 单一 25pp DISASTER)
- v5.x 内增加了两条自动止盈 (`TAKE_PROFIT_PRICE=0.90` 价格止盈 / `TAKE_PROFIT_PNL_PCT=1.00` 浮盈翻倍止盈)
- v5.x 删除了 `BLACKSWAN_HEDGE` 和 `CONFIRMED_NEGATIVE` 状态

代码里的 docstring 和 log 写的是 "v5.1" 是因为归档前 v5 系列内迭代到了这个标记;本归档作为 "v5 系列最终" 留存。

## v4 → v5 的关键差异 (回顾)

v4 用单一 `entry − cur ≥ 25pp` DISASTER 止损,过晚触发低价仓位 (17¢ 入场无法跌 25pp),又错杀 V-bounce (Trump publicly insult / Malta turnout 反弹案例)。

v5 引入了 3-tier 系统:

1. **慢跌硬止损** — `cur ≤ stop_price(entry)` 且持续 > 30 min → 自动卖
2. **急跌冻结** — `cur ≤ stop_price(entry)` 但 < 30 min → 冻结 24h
3. **绝对兜底** — `cur / entry < 40%` → 自动卖

`stop_price(entry)` 按入场分档:

| 入场价 | 跌幅 |
|---|---|
| ≥ 50¢ | 25pp |
| 30 – 50¢ | 18pp |
| 15 – 30¢ | 10pp |
| < 15¢ | (无, 走兜底) |

## v5 → v5.1 的关键差异 (为什么归档 v5)

2026-05-18 的回测显示,v5 急跌冻结机制的核心假设 "亏了会涨回来" 是**幻觉**:

- 5月起 30 个仓位的回测里, 8 个赢家的最深浮亏中位数仅为 **-13%** (87% 的赢家都在 -22% 以内反弹回正)
- 10 个亏家的最深浮亏中位数是 **-41%**, 一旦跌过 -20% 几乎不会再回来
- "亏损卖出后又涨回来" 的概率只有 **10%** (10 个亏损卖里只有 1 个 Malta 反弹)
- 当前 v5 入场分档隐含的允许浮亏百分比对低价仓位过度宽容 (30¢→12¢ 需要跌 -60% 才止损)

v5.1 据此重写止损:

- **删除** 急跌冻结整套机制 (`FROZEN_FRESH` / `FROZEN` / `FROZEN_EXPIRED` 状态 + 24h 冻结期 + 解冻判断 + `freeze_until` 字段)
- **删除** `_stop_price(entry)` 入场分档表
- **删除** `_drop_duration_minutes()` 急跌检测函数
- **删除** `ABSOLUTE_FLOOR` (40%) — 因为 25% 更严, 兜底永远不会触发
- **删除** `SLOW_DROP_MIN_MINUTES` / `FREEZE_DURATION_HOURS` / `UNFREEZE_RECOVERY_PP` / `ABSOLUTE_FLOOR_PCT` 常量
- **新增** `STOP_LOSS_PCT = 0.25` — 单一规则: `cur / entry < 0.75` → 自动全卖

## 共享部分

scanner / executor / prompts / tags / db schema (增量) 在 v5 和 v5.1 之间完全一致, 都跟 v4 一致。

## Run v5 (not recommended)

```bash
cd ~/polymarket/past/v5
# 共享 v5.1 的 .venv 和 v4.db, 直接跑会读现在的实时数据库
# 真要跑就先在新机器上单独建 venv, 用 .env.example 配新私钥, 起独立 SQLite
```

注意: v5 跟 v5.1 都绑端口 5051。要跑 v5 先 `pkill -f main.py` 停掉 v5.1。

## .env / v4.db 不在归档里

- `.env` (含 `POLY_PRIVATE_KEY`): 不归档, 看 `.env.example`
- `v4.db` (实时数据): 不归档, 用 `data/portfolio_snapshot.jsonl` 重建快照
- `bot.log` / `output.log` / `auto_backup.log`: 不归档
