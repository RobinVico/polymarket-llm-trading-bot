# Polymarket v5.8 项目规则

> v5.0 已归档到 past/v5/. v5.6 已归档到 past/v5.6-archive/ + git tag `v5.6-final`.
> v5.0 → v5.7 是一系列 incremental 改动, 完整历史见 技术报告.md §十三 + §十五 + §十六.
> **当前 v5.7** = v5.6 (LLM 三档止损 + best_bid + 自动清理) + dashboard 密码 + tailnet-only + **13 个编程层 bug 修复 (P1-P13)**.

## v5.8 新增的关键设计 (不要随意改回)

- **scanner.py 订单簿检查并发** (P-A1): `ThreadPoolExecutor(max_workers=8)` + 不传 clob SDK client (走 HTTP `/book`, 避免 SDK thread-safety 问题). 速度从 26s → 1-4s (per tag). 实测 27 tag 并发全扫 20s.
- **`SCAN_PARALLEL` 环境变量 fallback**: 用 `_scan_parallel()` 函数动态读 (不是 module-level constant), 改 .env 设 `SCAN_PARALLEL=0` 立刻回退串行. 默认 "1".
- **`data/scan_reports/{slug}.md` per-tag cache + `manifest.json`** (atomic rename 写, threading.Lock 防并发冲突). gitignored. `.gitkeep` 保留目录.
- **`scan_all_tags(tier_filter)`**: 5 个 tag 并发跑, 每个 tag 内部 8 个 orderbook 并发 → 总并发 40 但 CLOB 不限速.
- **报告头部 `<!-- scan version: parallel-v1 -->`** 标记 — 用户/Claude 看到这个就知道是新版.
- **4 个新 API 路由**: `POST /api/scan_all` / `GET /api/scan_all_status` / `GET /api/scan_report?tag=X` / `GET /api/full_prompt?tag=X` (后者改: 接 `?tag` 用 cached 报告).
- **chip 状态徽章 UI**: `.chip-pending` / `.chip-running` (pulse 动画) / `.chip-done` / `.chip-error`, JS `setChipStatus(slug, status)` 通过 `data-tag` 属性 + `slugifyTag(label)` mapping 更新.
- **复制即标记**: `copyP()` 成功后自动调 `setChipStatus(cur, '')` 把 ✓ 清掉, 表示"已送 Claude 处理过"; 缓存文件保留, 点 chip 仍可看. 旁边有 🔄 重置标记按钮一键清掉所有 chip 状态 (`doResetScanMarks()`).
- **持仓 tab + 重评模式** (§18.11): 持仓详情 card 顶部加 `📦 当前持仓` / `🤖 重评模式` 两 tab. 重评 tab 列每个仓位简化行 + 大 `📋 复制 Prompt` 按钮 + 顶部 `🤖 一键全部就绪` + `🔄 重置标记`. 复制后该按钮自动清 ✓. JS: `switchPosTab` / `reevalReadyAll` / `reevalResetMarks` / `copyReevalPromptMarked`. 复用 `/api/reeval_prompt` 路由.

## v5.7 新增的关键设计 (不要随意改回)

- **SQLite WAL mode** (P2): `db.py:get_conn()` 启用 `PRAGMA journal_mode=WAL` + `busy_timeout=10000`. 防 monitor 心跳与 Flask POST 并发锁. 这是结构性正确的方案, 不要回 rollback 默认 journal.
- **`closed_positions` 表** (P7): 任何卖出 (auto / user_force / at_target) 都 INSERT 一行完整 PnL 数据. **永远不要在 sell 流程之后再忘记 `save_closed_position()` 调用** — 否则统计层彻底瞎.
- **`login_attempts` 表持久化限流** (P11): 不要回 in-memory dict, 重启清零会让攻击者重启绕过.
- **时区一律 aware UTC** (P3): 用 `_utc_now_iso()` 写, 用 `_parse_iso_to_aware()` 读. 不要再用 `datetime.now()` (naive). 老数据是 naive 的, `_parse_iso_to_aware` 已经兼容.
- **`claude_raw_estimate` 入场时锁定** (P6): record_position 路由强制 `claude_raw_estimate=tp`. 这是 calibration 的基线, **不会随 reeval 漂移**. new_tp 会变, raw_estimate 不变.
- **partial fill < 95% return False** (P1): `executor.py:337` 卖出不到 95% 视为失败, 让 monitor 下轮重试. 不要回 silently return True.
- **dashboard 密码 + tailnet-only** (v5.7 早期, 见 §十五): 公网 Funnel 关掉, `tailscale serve` 只对 tailnet 设备开放. Flask 加 session 密码层. 本机 127.0.0.1 (且无 X-Forwarded-For 头) 直通零摩擦; 反代过来的都要密码. 90 天 cookie. 限流 5 次错 → 30 分锁 (持久化到 db).

## 继承自 v5.6 的关键设计 (不要随意改回)

- **止盈/止损用 `pos.avg_price` 而不是 `meta.entry_price`**: avg 是 Polymarket 实时算的当前持仓加权均价, 真实反映成本; entry 是 db 里第一次入场的历史价, 加仓后 / 二次入场后会失真. 数据回测里 5 月 10 个仓位踩了二次入场坑, 这一行多一层防御.
- **平仓后自动清理 `position_meta` 整行**: 三个触发点 — monitor 自动卖成功 / dashboard 手动卖 (`/api/force_exit`, `/api/execute_state`) 成功 / 每次心跳对比 `polymarket positions ⊃ db meta` 清孤儿. 心跳清理带双守卫: (1) positions 必须非空 (5-24 灾难教训), (2) orphan 必须连续 3 轮观察确认 (5-27 灾难教训).
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
- **`get_cash_balance` 失败返回 None (不是 0.0)**: 让调用方能区分 "API 挂了" 和 "账户真没钱". monitor snapshot 守卫据此防 assets_total 漏算 cash. dashboard 渲染层 v5.7 加了 `log.warning` 让 API 故障不再静默.

## 项目概述
- 全自动 Polymarket 预测市场交易 bot
- 跑在 Mac mini, port 5051, dashboard URL: http://localhost:5051
- 公网 URL: https://<hostname>.<tailnet>.ts.net (Tailscale Serve, tailnet-only, v5.7)
- 数据库: v4.db (SQLite), 共用 venv 在 ../polymarket-bot/.venv
- v3 在 <sibling-v3-dir, not in this repo> (frozen, 不要碰)

## 核心架构
- main.py 入口
- modules/dashboard.py: Flask UI + 路由
- modules/monitor.py: v5.7 决策引擎 (2 止盈 + 3-tier 止损 + edge-based, 心跳 180s, auto_sell 接入 closed_positions)
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
- 全局 git alias: git acp "msg" = add -A + commit + push (一句话提交)
- cron 每 30 分钟跑 scripts/auto_backup.sh, 有改动就 auto-backup <ts> 自动 commit+push 到 dev (私有)
- git log 里 "auto-backup ..." 是 cron 干的, 不是手动 commit
- .env / v4.db / *.log / .venv / .env.bak_* / data/portfolio_snapshot.jsonl-public 等已 gitignore
- portfolio_snapshot 表 (图表唯一数据源, 每 30 分钟一行) 有独立远程备份:
  - data/portfolio_snapshot.jsonl — JSON Lines 格式, 一行一 snapshot, 全表 dump
  - auto_backup.sh 每 30 分钟先调 db.export_portfolio_snapshot() 重写这个 jsonl, 再 git push
  - 灾难恢复: bash scripts/restore_portfolio_snapshot.sh (INSERT OR IGNORE 用 ts 去重, 可重复跑, 永远不会重复插入或覆盖更新数据)
  - 不要手动删 data/ 目录 (cron 会重新生成, 但中间窗口的备份历史会断)
  - 强制立即备份不等 cron: 任何时候 bash scripts/auto_backup.sh

## Dual Repo: 私有 dev + 公开 release (v5.7+)

- **两个 GitHub repo, 不要混**:
  - `RobinVico/polymarket-dev` (private) ← 日常工作 / cron auto-backup / 含全部真值
  - `RobinVico/polymarket-llm-trading-bot` (public) ← 只推 release / 全脱敏 / 1 个 commit
- **两个 remote** (本地 git):
  - `dev` → polymarket-dev (HTTPS, cron 默认推这里)
  - `public` → polymarket-llm-trading-bot
- **私有 main 永远保留真值**: CLAUDE.md / 技术报告.md / past/* 都含真实 Tailscale URL + v3 路径, 自己看着方便.
- **公开 release 的所有脱敏都在脚本里**: `scripts/prepare-public.sh` 用 perl 替换真值 + 删 past/ 下 runtime cruft (logs / dbs / bak). 脚本本身也在 public repo 里, 透明可审计.
- **永远不要直接 push public**: 必须先 detach + 跑脚本 + force push, 否则真值会泄漏.

### 公开 release workflow (每次发布新版本)

```bash
# 1) 私有 dev 已经是想 release 的状态 (commit 都 push 到 dev 了)
git push dev main

# 2) detach HEAD, 跑脱敏脚本 (一次性副本, 不污染 main)
git checkout --detach
bash scripts/prepare-public.sh        # 自动 sed 真值 + 删 cruft + 加 .gitkeep

# 3) commit + force push public (overwrite 之前的 release commit)
git add -A
git commit -m "Public release vX.Y"
git push public HEAD:refs/heads/main --force

# 4) 回 private dev (脱敏改动自动丢弃)
git checkout main
```

### 不要做的事
- **不要把 public remote 加进 cron** (cron 会自动推所有改动到那, 但 cron 不会跑脱敏脚本 → 真值泄漏)
- **不要把私有 main 上的真值 commit 抄到 public release** (脚本永远是 detached HEAD 上做)
- **不要在私有 main 上跑 prepare-public.sh** (会污染 working tree, 必须先 detach)
- **不要往 public push 的时候省 --force** (public main 是被覆盖的, 不是累加历史)
