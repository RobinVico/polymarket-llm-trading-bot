# Polymarket v5.1

*[English](README.md)*

一个 Polymarket 预测市场交易 bot, 核心是 edge-based 决策引擎 + 两条止盈 + 三层止损 + 本地 Flask dashboard。概率校准走 Claude.ai 手动闭环, 监控和执行是自动的。

## 版本

| 版本 | 状态 | 路径 |
|---|---|---|
| **v5.1** | **当前** (本 README, 在 repo 根) | `./` |
| v4 | 归档 | [`past/v4/`](past/v4/) |

- **v4 → v5**: 止损规则改造 (单 25pp DISASTER → 3-tier, 详见 [v4 归档说明](past/v4/ARCHIVE.md))。
- **v5 → v5.1**: 加入两条最高优先级的自动止盈规则 (价 ≥ 90¢ / 浮盈 ≥ +100%, 触发即全卖)。

其他模块 (scanner / executor / dashboard / prompts / tags / db schema 增量) 全部共用。

## v5.1 自动止盈规则 (最高优先级)

两条无条件、独立于 edge 重评和冻结状态的自动卖出规则:

1. **价格止盈** — `cur_price ≥ 90¢` → bot 自动全卖。**为什么 90¢**: 90¢ 以上继续涨的边际收益很低 (年化只剩约 25%), 流动性变差, UMA 结算风险变大。
2. **浮盈翻倍** — `(cur − entry) / entry ≥ +100%` → bot 自动全卖。**为什么 +100%**: 翻倍是"已经赚到一倍"的心理和数学双重锚点, 后续回吐就是绝对损失。+70% 太早会过早锁住大赢家。

两条任一触发都直接整笔卖, 不分批。每条用独立的 `executed_action` 标记防重复触发。

## v5 止损规则 (vs v4 关键变化)

三层规则替代 v4 单一 −25pp 的 DISASTER 阈值:

1. **慢跌硬止损** — `cur_price ≤ stop_price(entry)` 且跌的过程超过 30 分钟 → bot 自动卖出。
2. **急跌冻结** — 同样跌到止损价但过程不到 30 分钟 → bot 冻结仓位 24 小时 (不卖, 不听重评信号)。如果 24h 内价回到 `entry − 10pp` 以内 → 自动解冻。24h 满后仍低于止损价 → bot 自动卖。
3. **绝对兜底** — `cur_price / entry < 40%` (亏掉成本的 60%) → 不管什么状态, bot 直接卖。

`stop_price(entry)` 按入场价分档:

| 入场价 | 跌幅 | 止损价示例 |
|---|---|---|
| ≥ 50¢ | 25pp | 70¢ → 45¢ |
| 30 – 50¢ | 18pp | 40¢ → 22¢ |
| 15 – 30¢ | 10pp | 17¢ → 7¢ |
| < 15¢ | (无) | 只靠规则 3 兜底 |

## 概述

这不是纯算法交易, 而是研究辅助 + 监控 + 执行的组合。日常工作流:

1. **扫描** — `scanner.py` 调 Polymarket Gamma API, 按 35-tag 白名单和市场过滤器筛出候选市场。
2. **研究** — `prompts.py` 渲染 DISCOVERY / REEVAL v5.2 prompt。用户贴到 Claude.ai 得到校准后的目标概率 `q`。
3. **写入元数据** — 用户在 dashboard 填入 `q`、入场价、信心。状态存入 SQLite 的 `position_meta` 表。
4. **监控** — `monitor.py` 每 3 分钟拉仓位和当前价, 逐仓位评估两条止盈 + 三层止损 + TIME_STOP + edge-based 逻辑。
5. **执行** — 自动卖触发: `TAKE_PROFIT_PRICE` / `TAKE_PROFIT_PNL` / `TIME_STOP` / `SLOW_DROP` / `FROZEN_EXPIRED` / `ABSOLUTE_FLOOR`。其他状态在 dashboard 等用户确认。

## 架构

| 路径 | 职责 |
|---|---|
| `main.py` | 入口。初始化 SQLite, 启动 monitor 线程, Flask 跑在 `0.0.0.0:5051`。 |
| `modules/dashboard.py` | Flask UI 和 HTTP 路由。渲染仓位、决策、扫描结果、prompt。 |
| `modules/monitor.py` | 两条止盈 + 三层止损 + edge-based 决策引擎。心跳 180 秒。 |
| `modules/scanner.py` | Polymarket Gamma 扫描器, 带 `FILTERS` 字典。 |
| `modules/executor.py` | py-clob-client v2 封装 (拉仓位 / 买 / 卖 / USDC 余额 / 历史价)。 |
| `modules/db.py` | SQLite schema + CRUD + portfolio_snapshot 远程备份导入导出。 |
| `modules/prompts.py` | DISCOVERY + REEVAL v5.2 prompt 模板。 |
| `modules/tags.py` | 39 个 tag 白名单 (Tier 1-4) + 黑名单 + 白名单优先。 |

## 安装

### 前置
- macOS 或 Linux
- Python 3.10+
- Polymarket 账号 + proxy wallet (Magic / GNOSIS_SAFE)

### 安装
```bash
git clone https://github.com/RobinVico/polymarket.git
cd polymarket
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 配置
复制 `.env.example` 为 `.env`, 填入:

```
POLY_PRIVATE_KEY=0x...        # 从 https://polymarket.com/profile 导出
POLY_FUNDER=0x...             # proxy wallet 地址, 不是 EOA
POLY_SIGNATURE_TYPE=1         # 1 = Magic / GNOSIS_SAFE
```

`POLY_API_KEY` / `POLY_API_SECRET` / `POLY_API_PASSPHRASE` 由 `py-clob-client` 首次运行时自动生成, 留空即可。

> `POLY_FUNDER` 必须是 proxy wallet, 不能填 EOA。`POLY_SIGNATURE_TYPE=1` 下签名是对 EOA 名下的 GNOSIS_SAFE 走的, 两个混了会静默签名失败。

### 启动
前台:
```bash
source .venv/bin/activate
python3 main.py
```

后台:
```bash
source .venv/bin/activate
nohup python3 main.py > output.log 2>&1 &
```

Dashboard: <http://localhost:5051>

## 运营

### 改代码后重启
代码不会热重载, 改完要走完整重启:

```bash
pkill -f "main.py" 2>/dev/null
sleep 2
lsof -ti:5051 2>/dev/null | xargs kill -9 2>/dev/null
sleep 1
source .venv/bin/activate
nohup python3 main.py > output.log 2>&1 &
sleep 5
tail -10 bot.log
```

### 日志
- `bot.log` — 应用日志 (Python `logging`)。
- `output.log` — `nohup` 收的标准输出与错误。

两个都已 gitignore。

### 数据库
SQLite 在 `v4.db` (文件名保留向后兼容)。改 schema 之前先备份:

```bash
cp v4.db v4.db.bak_$(date +%s)
```

`v4.db` 和 `v4.db.bak_*` 都已 gitignore。运营状态不进版本控制, 靠这些时间戳备份加下面的快照远程备份。

### 快照远程备份

`data/portfolio_snapshot.jsonl` 每 30 分钟由 `scripts/auto_backup.sh` (走 cron) 导出, 推到 GitHub repo。换机器恢复:

```bash
git pull origin main
bash scripts/restore_portfolio_snapshot.sh
```

`INSERT OR IGNORE` 用 `ts` 主键去重, 恢复脚本可重复跑。

详细机制看 [`技术报告.md`](技术报告.md) 第 12.3 节。

## 文件结构

```
polymarket/                            (= GitHub repo polymarket, v5 在根)
├── main.py                            # 入口
├── requirements.txt
├── .env.example                       # 模板, 复制为 .env 后填
├── .gitignore
├── CLAUDE.md                          # 项目规则 (Claude Code 用)
├── README.md                          # 英文版
├── README.zh.md                       # 中文版 (本文件)
├── 技术报告.md                         # 详细技术报告 (中文)
├── modules/                           # v5 代码
├── scripts/                           # cron + 恢复
├── data/portfolio_snapshot.jsonl      # 远程备份的快照
├── v4.db                              # SQLite (gitignored)
└── past/
    └── v4/                            # 归档的 v4 (不要跟 v5 同时跑)
        ├── ARCHIVE.md                 # 归档说明 + 跟 v5 的 diff
        ├── main.py / modules/ / ...
        └── v4.db                      # 归档时 v4 的 DB 快照
```

## 备注

- 老的 v3 代码在 `<sibling-v3-dir, not in this repo>`, 已冻结, 不属于本 repo。
- `past/v4/modules/*.bak_pre_*` 是有意保留的历史备份, 不会被 import。
- `.env` / `v4.db` / `*.log` / `.venv` 都被 `.gitignore` 排除。
- 用的是 `py-clob-client` v2 (不是 v1)。装 v2 之后 import 路径还是 `py_clob_client`。
- `CLAUDE.md` 是 Claude Code 会话用的项目规则, 记录了哪些阈值能调、`.env` 不要碰等约束。
- `past/v4/` 仅供历史参考。不要跟 v5 同时跑 (端口 5051 冲突)。
