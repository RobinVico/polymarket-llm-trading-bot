# Polymarket v5.6 项目规则

> v5.0 已归档到 past/v5/. v5.0 → v5.6 是一系列 incremental 改动, 完整历史见 技术报告.md §十三.
> 当前 v5.6 = LLM 入场三档 STOP_LOSS + best_bid 触发 + meta 自动清理 + 强化 sweep / snapshot 守卫.

## 关键设计决策 (不要随意改回)

- **止盈/止损用 `pos.avg_price` 而不是 `meta.entry_price`**: avg 是 Polymarket 实时算的当前持仓加权均价, 真实反映成本; entry 是 db 里第一次入场的历史价, 加仓后 / 二次入场后会失真. 数据回测里 5 月 10 个仓位踩了二次入场坑, 这一行多一层防御.
- **平仓后自动清理 `position_meta` 整行**: 三个触发点 — monitor 自动卖成功 / dashboard 手动卖 (`/api/force_exit`, `/api/execute_state`) 成功 / 每次心跳对比 `polymarket positions ⊃ db meta` 清孤儿. 心跳清理带守卫: `positions` 必须非空才 sweep (5-24 灾难教训: data-api 单边挂 + clob 活的情况下, "positions=[] + cash>0" 推论失效, 用最严格守卫防误删).
- **触发用价不对称设计 (止盈 best_bid / 止损 cur_price)**:
  - `TAKE_PROFIT_PRICE` / `TAKE_PROFIT_PNL` 用 `executor.get_best_bid()` (你 sell 真能拿到的价). 防 Dell 类低流动假象: cur=$0.905 但 bid=$0.60, 看 cur 触发会"锁个假胜利"实际卖 60¢ 真亏.
  - `STOP_LOSS` 用 `pos.cur_price`. 防瞬时流动性蒸发, 同时跟历史回测数据一致.
  - `TIME_STOP` / Edge-based 也用 cur_price.
  - best_bid 拉失败 fallback 回 cur_price.
- **STOP_LOSS 三档设计 (LLM 入场分类驱动)**: 5-22 ~ 5-23 回测发现单一 -25% 在 Politics/Geopolitics 类卖错率 67%, Senate/Malta/Israel 都在卖后大反弹. 据此引入三档:
  - `convergent` (真相收敛型: 票房/营收/统计/汇率/比分): -20% 止损 (真相一锤定音, 跌就是跌)
  - `hybrid` (混合型: 候选人选举, 有民调+政治): -35% 止损 (中等容忍)
  - `event_driven` (事件驱动型: 政治/外交/谈判): **不止损**, 只用 $0.05 地板价兜底 (价格震荡 ≠ 真相变化)
  - 老仓位 / 未填 tier → fallback 用 -25% (向后兼容)
  - tier 来源: 每次入场新仓位时 Claude 在 DISCOVERY 输出里直接给出 `**止损档**: convergent/hybrid/event_driven`, 用户在 dashboard 的"止损" dropdown 选对应档.
- **`get_cash_balance` 失败返回 None (不是 0.0)**: 让调用方能区分 "API 挂了" 和 "账户真没钱". monitor snapshot 守卫据此防 assets_total 漏算 cash.

## 项目概述
- 全自动 Polymarket 预测市场交易 bot
- 跑在 Mac mini, port 5051, dashboard URL: http://localhost:5051
- 公网 URL: https://<hostname>.<tailnet>.ts.net (Tailscale Funnel)
- 数据库: v4.db (SQLite), 共用 venv 在 ../polymarket-bot/.venv
- v3 在 <sibling-v3-dir, not in this repo> (frozen, 不要碰)

## 核心架构
- main.py 入口
- modules/dashboard.py: Flask UI + 路由
- modules/monitor.py: v5.1 决策引擎 (2 止盈 + 3-tier 止损 + edge-based, 心跳 180s)
- modules/scanner.py: Polymarket Gamma 扫描器
- modules/executor.py: CLOB v2 SDK 卖出
- modules/db.py: SQLite + position_meta CRUD
- modules/prompts.py: DISCOVERY + REEVAL prompt 模板
- modules/tags.py: 22 白名单 tag

## 关键技术决策 (不要改)
- 用 py_clob_client_v2 (不是 v1)
- POLY_SIGNATURE_TYPE=1 (GNOSIS_SAFE)
- POLY_FUNDER 是 proxy wallet, 不是 EOA
- v4.1 sig 修复后 .env 不要再改

## 改代码 → 生效
所有代码改动需要重启 v4 服务才生效:
```bash
pkill -f "main.py" 2>/dev/null
sleep 2
lsof -ti:5051 2>/dev/null | xargs kill -9 2>/dev/null
sleep 1
source .venv/bin/activate
nohup python3 main.py > output.log 2>&1 &
sleep 5
tail -5 bot.log
```

## 重要约束
- 不修改 .env (sig_type / funder / private_key)
- 不修改 v3 代码 (<sibling-v3-dir, not in this repo>)
- 改完代码先看 bot.log 确认无报错再说成功
- shell 是 zsh, 命令里**不要**带井号注释 (zsh 把 # 当命令)
- 涉及 SQLite 改动前先备份 v4.db
- 不要碰 launchd / systemd, 启动方式用上面的 nohup
- bash 命令里中文注释要避开 zsh 误解析 (用纯 ASCII 或 # 之前留空格)

## 常见任务
1. 改 monitor 决策阈值 → modules/monitor.py 顶部常量
   - 止盈 (v5.1, 最高优先级, 全卖): TAKE_PROFIT_PRICE=0.90 / TAKE_PROFIT_PNL_PCT=1.00
   - 止损 (v5.1, LLM 入场三档): STOP_LOSS_PCT_BY_TIER (convergent -20% / hybrid -35% / event_driven 不止损) + STOP_LOSS_PCT_LEGACY=0.25 + EVENT_DRIVEN_FLOOR_PRICE=0.05
   - TIME_STOP: TIME_STOP_DAYS / TIME_STOP_DRIFT_PP
   - Edge: HOLD_MIN_EDGE_PP / SOFT_NEGATIVE_THRESHOLD_PP
   - 改完别忘了 modules/dashboard.py 的 import 列表和规则展示同步
   - ⚠️ 止盈止损用 `pos.avg_price` 不用 `meta.entry_price` (防御加仓 + 清理边界, 不要改回 entry)
2. 改扫描参数 → modules/scanner.py 的 FILTERS 字典
3. 改重评 prompt → modules/prompts.py 的 REEVAL_PROMPT
4. 改 dashboard UI → modules/dashboard.py 里的 HTML/CSS/JS
5. 数据库改 schema → modules/db.py 的 init_db, 但要兼容老数据

## 验证流程
1. 改代码后先 python3 -c "from modules.X import Y; print('OK')" 测加载
2. pkill + 重启
3. tail -10 bot.log 看启动日志
4. 浏览器访问 http://localhost:5051 验证

## 备份位置
- modules/*.bak_pre_* 是历史备份, 不要删
- v4.db 改 schema 前先 cp v4.db v4.db.bak_$(date +%s)
- Repo: https://github.com/RobinVico/polymarket (private)
- 全局 git alias: git acp "msg" = add -A + commit + push (一句话提交)
- cron 每 30 分钟跑 scripts/auto_backup.sh, 有改动就 auto-backup <ts> 自动 commit+push
- git log 里 "auto-backup ..." 是 cron 干的, 不是手动 commit
- .env / v4.db / *.log / .venv 已 gitignore (cron 不会推这些)
- portfolio_snapshot 表 (图表唯一数据源, 每 30 分钟一行) 有独立远程备份:
  - data/portfolio_snapshot.jsonl — JSON Lines 格式, 一行一 snapshot, 全表 dump
  - auto_backup.sh 每 30 分钟先调 db.export_portfolio_snapshot() 重写这个 jsonl, 再 git push
  - 灾难恢复: bash scripts/restore_portfolio_snapshot.sh (INSERT OR IGNORE 用 ts 去重, 可重复跑, 永远不会重复插入或覆盖更新数据)
  - 不要手动删 data/ 目录 (cron 会重新生成, 但中间窗口的备份历史会断)
  - 强制立即备份不等 cron: 任何时候 bash scripts/auto_backup.sh
