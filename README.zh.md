# Polymarket v7.4

*[English](README.md)*

Polymarket 预测市场半自动交易 bot。结构 = edge-based 决策引擎 + 三档止损 + 两条止盈 + Kelly 仓位公式 + 大跌自动重评 (Claude API 联网调研) + 本地 Flask dashboard (桌面 / 手机 / 控制台 / 往期分析页面) + Tailscale-only 公网访问 (密码鉴权)。概率校准走 Claude.ai 手动闭环 **或** 大跌时自动调 Claude API 联网重评 (v6.0), 监控和执行是自动的。

**完整技术报告**: [TECHNICAL_REPORT.md](TECHNICAL_REPORT.md) (英文摘要) · [技术报告.md](技术报告.md) (中文完整版)

## 当前版本概要 (v7.4.4)

```
自动卖规则 (优先级从高到低):
  1a. 止盈 (分档):
        event_driven    谁先到听谁的 (v7.4.2):
                          (best_bid−avg)/avg ≥ +100% → 全卖锁翻倍 (入场 <$0.46 才可能先到)
                          best_bid ≥ 0.92            → 卖一半, 另一半跑到结算
                          留的后半: best_bid < 0.782 (从 0.92 回撤 15%) → 全卖锁利 (v7.4.1)
        convergent ≤3天 best_bid ≥ 0.88 → 全卖
        其余            best_bid ≥ 0.90 → 全卖
  1b. TAKE_PROFIT_PNL      (best_bid − avg) / avg ≥ +100% → 全卖 (event_driven 除外)
  2.  STOP_LOSS           按 LLM 入场分类的三档:
        convergent  (硬数据) → 移动止损: 从最高价回撤 ≥20% (≤3天 12%) + 连 6 拍确认
        hybrid      (混合)   → 移动止损: 从最高价回撤 ≥35% + 连 6 拍确认 (v7.4.4)
        event_driven (事件)  → 入场锚 −60% (很松, v7.4.3) + $0.05 地板兜底
        未分类               → 一律按 hybrid 处理 (v7.4.3; 老的 -25% 档弃用)
        (auto-reeval 开着: 砸穿止损线 → 进 PENDING_REEVAL 交给重评决定, 只 $0.05 地板盲卖。
         重评 q 锚"大跌前中枢"而非被砸现价; event_driven 的 exit 仅在 论点破 或
         edge ≤ -8pp 才放行, 否则降级 update_q 继续持有)
  3.  TIME_STOP           距结算 ≤ 2 天 + 价格漂移 < 5pp → 全卖

决策状态 (等用户操作):
  HOLD / MARGINAL / SOFT_NEGATIVE / AT_TARGET
```

**页面**: `/` 桌面 dashboard (扫描/入场金额/持仓/事件/日志) · `/panel` 副屏控制台 · `/m` 手机只读版 (手机 UA 自动跳转) · `/history` 往期仓位监测 (结算追踪, 赚钱率 vs 方向对率双口径, 校准报告, Chart.js 趋势图) · `/paper` 测试仓/模拟盘 · `/tags` 动态热门标签榜。

**v5.10 之后的新东西**: held-token PnL 与 is_correct 口径修复 + 历史数据迁移 (§二十一), Gamma `closed=true` 结算查询链 + 进程级 DoH DNS 污染兜底 (§二十一), Claude JSON 快速通道 — 粘贴 DISCOVERY 的机器可读块一键填计算器/一键录持仓 (§二十二), 手机只读版 `/m` (§二十三), JSON 快速通道草稿持久化 — 解析出的推荐和推荐金额刷新不丢, 直到点清理或录入持仓 (§二十四)。

**v5.12 之后的新东西**: **大跌自动重评 (§二十五)** — 大跌时 bot 调 Claude API 联网调研给出结构化决策 (hold / update_q / exit / cancel_autostop); **在线你确认, 离线自动执行 (动真钱)**。砸穿 %止损线现在进 `PENDING_REEVAL` (交给重评决定) 而不是盲卖; 每仓加 **🤖 API重评** 按钮 + 6h 冷却。外加副屏 **控制台 `/panel`**、录入漏填提醒、紧急红闪弹窗、距结算天数列 (§二十六)。

**v7.0 新增 — 出场策略重设计 (§二十七)**: 专治"把会赢的仓割在坑底"。① 重评的 q 锚到"大跌前价格中枢"而非被砸的现价 (堵 #79/#86 卖飞的根); ② event_driven 的 exit 仅在 论点破 或 edge ≤ -8pp 才放行, 否则降级 update_q 继续持有; ③ 止盈分档 (event_driven 0.92 卖一半留一半跑, convergent 临近结算 0.88); ④ convergent 改"从最高价回撤 + 连拍确认"的移动止损; ⑤ 重评 (q/现价/大跌前中枢) + 价格曲线 落库供日后校准。

**v7.1 新增 — 测试仓/模拟盘 (§二十八)**: 一个 `/paper` 测试本, 把拿不准/看着离谱的 Claude 推荐丢进去 (不真下单), 按你填的入场价实时盯盘、跑**跟真仓同一套算法**, 看预测怎么走。只支持手动重评 (复制提示词 → 自己贴去 Claude.ai 免费 → 应用新 q)。**绝不真下单、绝不调付费 API** —— 已 grep 审计。

**v7.2–v7.4 新增**: 持仓卡拆 3 tab — 只读持仓 / 操作面板 (全部编辑 + 粘 Claude 重评 JSON 一键应用) / 重评模式 (7.2); `/history` 统计大改 — Chart.js 月度趋势+累计线、盈亏分布、卖飞分析、按出场方式归类, `closed_positions` 按 Polymarket 真实成交 1:1 重建 (7.3); 测试仓生命周期 — 进行中 vs 往期 (最高点/预测对错/模拟胜率) (7.4.0); 真金白银的出场收紧 — 事件型"翻倍 vs 0.92 卖半谁先到" (7.4.2)、卖半后 0.782 保护 (7.4.1)、事件型加很松的 −60% 止损 (7.4.3)、混合型改从最高价回撤 35% 的移动止损 (7.4.4); 自动重评双模型 — Claude 权威 + 智谱 GLM 对比页 `/api_reeval`。版本号单一来源 `modules/version.py`。完整变更日志见 [技术报告.md](技术报告.md) 版本号规则段 · [TECHNICAL_REPORT.md](TECHNICAL_REPORT.md) 头部。

## 架构

| 路径 | 职责 |
|---|---|
| `main.py` | 入口。安装 polymarket DNS guard, 初始化 SQLite, 启动 monitor 线程, Flask 跑在 `127.0.0.1:5051` (仅本机, 见下方安全)。 |
| `modules/dashboard.py` | Flask UI (桌面 `/` + 手机 `/m` + 分析 `/history`) + HTTP 路由 + 密码鉴权层。 |
| `modules/monitor.py` | 决策引擎 (3 档止损 + 双止盈 + TIME_STOP + edge-based + sweep 守卫 + 自动写 closed_positions), 30s 心跳 + 每小时结算检查。砸穿 %止损线进 `PENDING_REEVAL` (v6.0)。 |
| `modules/auto_reeval.py` | v6.0 大跌自动重评 — Claude API 联网调研 (web_search + web_fetch + 强制 `submit_decision`) → 结构化决策; 在线确认/离线自动执行; 闩锁 + 6h 冷却。 |
| `modules/scanner.py` | Polymarket Gamma 扫描器, 带 `FILTERS` 字典, 订单簿并发检查 (v5.8)。 |
| `modules/executor.py` | py-clob-client v2 封装。Partial-fill 检测 (成交 < 95% → 重试)。 |
| `modules/db.py` | SQLite schema (WAL 模式) + CRUD + portfolio_snapshot 备份 + closed_positions 分析 + login_attempts。 |
| `modules/sizing.py` | v5.9 仓位公式 (¼-Kelly + 月回撤预算 + cluster cap)。 |
| `modules/clusters.py` | 相关性簇暴露统计 + DISCOVERY cluster 字典注入。 |
| `modules/resolution_check.py` | 每小时市场结算检测 (五档 Gamma 查询链, 含 `closed=true`)。 |
| `modules/gamma_client.py` | Gamma HTTP 客户端, DoH 固定 IP 兜底 + 进程级 DNS guard (v5.10.2)。 |
| `modules/prompts.py` | DISCOVERY + REEVAL prompt 模板 (含 v5.11 机器可读 JSON 输出契约)。 |
| `modules/tags.py` | 39 个 tag 白名单 + 黑名单 + 白名单优先。 |

## 版本归档

| 版本 | 状态 | 路径 |
|---|---|---|
| **v7.4.4** | **当前** (本 README, repo 根) | `./` |
| v5.9 | 归档 (仓位公式 + clusters) | [`past/v5.9-archive/`](past/v5.9-archive/) |
| v5.8 | 归档 (并发扫描器) | [`past/v5.8-archive/`](past/v5.8-archive/) |
| v5.7 | 归档 (安全 + 持久化加固) | [`past/v5.7-archive/`](past/v5.7-archive/) |
| v5.6 | 归档 (公开重构前的快照) | [`past/v5.6-archive/`](past/v5.6-archive/) |
| v5.0 | 归档 (3-tier 止损 + 急跌冻结) | [`past/v5/`](past/v5/) |
| v4 | 归档 (单一 -25pp DISASTER) | [`past/v4/`](past/v4/) |

完整 v5.0 → v7.4.4 演进 (每个版本改了什么, 解决什么问题) 见 [TECHNICAL_REPORT.md](TECHNICAL_REPORT.md) (英文) 或 [技术报告.md §十三](技术报告.md) (中文)。

## 安装

```bash
git clone https://github.com/RobinVico/polymarket.git
cd polymarket
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 填入 POLY_PRIVATE_KEY, POLY_FUNDER, POLY_SIGNATURE_TYPE=1
                      # 还要填 DASHBOARD_PASSWORD, FLASK_SECRET_KEY (32+ 字符随机)
                      # 可选: ANTHROPIC_API_KEY 开启大跌自动重评 (§二十五); 不填=功能关闭
nohup python3 main.py > output.log 2>&1 &
```

Dashboard: <http://localhost:5051> (手机浏览器自动跳 `/m`, 页内"切回桌面版"可退出)

> `POLY_FUNDER` 必须是 proxy wallet, 不能填 EOA。`POLY_SIGNATURE_TYPE=1` 下签名走 EOA 名下的 GNOSIS_SAFE, 两个混了会静默签名失败。

## 安全 (v7.4)

Dashboard 默认 **只 listen 127.0.0.1:5051** (仅本机)。三个页面 (`/`、`/m`、`/history`) 都在同一层 session 鉴权后面。要远程访问:
1. 在 host 和任何客户端设备装 Tailscale (`tailscale.com/download`)
2. `tailscale serve --bg http://localhost:5051` 把 dashboard 暴露到你的 tailnet (仅你 Tailscale 账号下设备能访问)
3. 从 tailnet 设备访问 `https://<your-host>.<your-tailnet>.ts.net` → 第一次输 `DASHBOARD_PASSWORD` → cookie 90 天有效

如果还要公开 internet 访问 (比如分享给非 Tailscale 用户): `tailscale funnel --bg on`。密码层照样挡。爆破限流 (5 次错 → 30 分钟锁, 持久化跨重启)。

**自动重评 (v6.0)**: 若设了 `ANTHROPIC_API_KEY`, bot 会调 Claude API 并把市场标题/slug 发去联网调研; **你"离线"时它能不经确认直接下真实卖单** (在线则等你批准)。`ANTHROPIC_API_KEY` 是密钥, 放 `.env` (已 gitignored)。不填 = 整个功能关闭。

详见 [技术报告.md §十五](技术报告.md) 或 [TECHNICAL_REPORT.md](TECHNICAL_REPORT.md) §15。

## 改代码后重启

```bash
pkill -f "main.py" 2>/dev/null; sleep 2
lsof -ti:5051 2>/dev/null | xargs kill -9 2>/dev/null; sleep 1
source .venv/bin/activate && nohup python3 main.py > output.log 2>&1 &
sleep 5 && tail -10 bot.log
```

## 数据持久化

- `v4.db` (SQLite, WAL 模式, gitignored). 改 schema 前 `cp v4.db v4.db.bak_$(date +%s)`.
- `data/portfolio_snapshot.jsonl` 每 30 分钟由 `scripts/auto_backup.sh` 本地导出。**不进公开 repo** (含真实 PnL 历史)。你自己的备份请推到你 control 的私有 remote.
- `closed_positions` 表 (v5.10+) 存每个已平仓位的实现 PnL / 持有时长 / 退出原因 / Claude 原始 q 估计, 外加结算字段 (`is_resolved` / `final_outcome` / `is_correct`, 持有侧口径) 由每小时检测器回填 — 支撑 `/history` 的赚钱率、方向对率、校准分析。一次性数据迁移记录在 `migrations` 哨兵表。

## 文件结构

```
polymarket/                          (GitHub public repo, v7.4.4 在根)
├── main.py
├── requirements.txt
├── .env.example                     # 模板, 复制为 .env 后填
├── LICENSE                          # MIT
├── SECURITY.md / SECURITY.zh.md     # 漏洞披露
├── CLAUDE.md                        # 项目规则 (Claude Code 用)
├── README.md / README.zh.md         # 英文/中文 README (这个)
├── TECHNICAL_REPORT.md              # 英文技术报告 (high-level)
├── 技术报告.md                       # 中文完整技术报告 (28 节 + 变更日志, 完整 history)
├── modules/                         # 当前 (v7.4.4) 代码
├── scripts/                         # cron + 恢复 + 回填 + 迁移
├── data/claude-skills/              # claude.ai SKILL zip (discovery / reeval / cluster-analyzer)
├── data/.gitkeep                    # 占位; portfolio_snapshot.jsonl 已 gitignored
├── v4.db                            # SQLite (gitignored)
└── past/
    ├── v5.9-archive/                # 归档的 v5.9
    ├── v5.8-archive/                # 归档的 v5.8
    ├── v5.7-archive/                # 归档的 v5.7
    ├── v5.6-archive/                # 归档的 v5.6
    ├── v5/                          # 归档的 v5.0
    └── v4/                          # 归档的 v4
```

## 备注

- 老的 v3 代码在 `<sibling-v3-dir, not in this repo>`, 已冻结, 不在本 repo。
- `.env` / `v4.db` / `*.log` / `.venv` 都 gitignored。
- 用 `py-clob-client` v2 (装后 import 路径还是 `py_clob_client`)。
- 不要跟 past/v4/ 或 past/v5/ 同时跑 (端口 5051 冲突)。

## 免责声明

本软件**自动用真钱在 Polymarket 交易**。运行前请自行 review 源代码 + 了解 Polymarket 的费用结构、resolution 规则、和你本地的法律 (预测市场在某些司法管辖区受限, 包括美国 CFTC 管辖)。不要用承担不起损失的钱跑这个。详见 [LICENSE](LICENSE)。
