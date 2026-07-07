# Polymarket v5.1 项目规则

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
   - 止损 (v5): SLOW_DROP_MIN_MINUTES / FREEZE_DURATION_HOURS / ABSOLUTE_FLOOR_PCT / _stop_price() 分档表
   - TIME_STOP: TIME_STOP_DAYS / TIME_STOP_DRIFT_PP
   - Edge: HOLD_MIN_EDGE_PP / SOFT_NEGATIVE_THRESHOLD_PP
   - 改完别忘了 modules/dashboard.py 的 import 列表和规则展示同步
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
