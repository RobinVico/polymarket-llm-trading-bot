# Polymarket v4

*[English](README.md)*

一个 Polymarket 预测市场交易 bot,核心是 edge-based 决策引擎 + 本地 Flask dashboard。概率校准走 Claude.ai 手动闭环,监控和执行是自动的。

## 概述

这不是纯算法交易,而是 **research-assist + monitor + execution** 的组合。日常工作流:

1. **扫描** — `scanner.py` 调 Polymarket Gamma API,按 tag 白名单和市场过滤器筛出候选市场。
2. **研究** — `prompts.py` 渲染 DISCOVERY / REEVAL prompt,贴到 Claude.ai 得到校准后的目标概率 `q`。
3. **写入元数据** — 用户在 dashboard 填入 `q`、入场价等,存入 SQLite 的 `position_meta` 表。
4. **监控** — `monitor.py` 每 3 分钟拉仓位和当前价,逐仓位算 `edge = q − p`,写入决策状态。
5. **执行** — 只有 `DISASTER` 和 `TIME_STOP` 触发自动卖 (走 `executor.py` / py-clob-client v2)。其他状态在 dashboard 等用户确认。

## 架构

| 路径 | 职责 |
|---|---|
| `main.py` | 入口。初始化 SQLite,启动 monitor 线程,Flask 跑在 `0.0.0.0:5051`。 |
| `modules/dashboard.py` | Flask UI 和路由。渲染仓位、决策、扫描结果、prompt。 |
| `modules/monitor.py` | 9 状态 edge-based 决策引擎。心跳 180s。 |
| `modules/scanner.py` | Polymarket Gamma 扫描器,带 `FILTERS` 字典 (成交量、结算时间、价格区间等)。 |
| `modules/executor.py` | py-clob-client v2 封装,拉取仓位 + 提交卖单。 |
| `modules/db.py` | SQLite schema + `position_meta` CRUD + 事件日志。 |
| `modules/prompts.py` | DISCOVERY 和 REEVAL prompt 模板。 |
| `modules/tags.py` | 22 个 tag 白名单。 |

## 决策引擎

心跳 180s。每个仓位独立评估,优先级从上到下,**第一个匹配胜出**:

1. **`NO_META`** — 仓位没有元数据,跳过。
2. **`DUST_HOLD`** — `size < 5` 股,持有等结算。
3. **`TIME_STOP`** *(自动卖)* — `距结算 ≤ 2 天` 且 `|p − entry| < 5pp`。临近结算且价格不动。
4. **`DISASTER`** *(自动卖)* — `entry − p ≥ 25pp`。硬止损。
5. **`BLACKSWAN_HEDGE`** — `p ≥ 0.97` 且 `距结算 > 1 天`。提示手动减半。
6. **Edge-based** (`edge = q − p`,单位 pp):
   - `HOLD` — `edge > +2pp`。当前价远低于目标,持有。
   - `MARGINAL` — `−3pp ≤ edge ≤ +2pp`。边缘地带。
   - `edge < −3pp` (当前价高于目标):
     - `AT_TARGET` — 用户从未调过 `q`,视为自然达标,建议清仓。
     - `SOFT_NEGATIVE` — 用户调过 `q`,第一次 edge 翻负。
     - `CONFIRMED_NEGATIVE` — 用户调过 `q`,第二次连续翻负。建议清仓。

只有 `DISASTER` 和 `TIME_STOP` 不需要确认就卖,其余写入 `monitor_state` 等用户在 dashboard 确认。

阈值常量在 `modules/monitor.py` 顶部。

## 安装

### 前置
- macOS 或 Linux
- Python 3.10+
- Polymarket 账号 + proxy wallet (Magic / GNOSIS_SAFE)

### 安装
```bash
git clone https://github.com/RobinVico/polymarket.git
cd polymarket/v4
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 配置
复制 `.env.example` 为 `.env`,填:

```
POLY_PRIVATE_KEY=0x...        # 从 https://polymarket.com/profile 导出
POLY_FUNDER=0x...             # proxy wallet 地址,不是 EOA
POLY_SIGNATURE_TYPE=1         # 1 = Magic / GNOSIS_SAFE
```

`POLY_API_KEY` / `POLY_API_SECRET` / `POLY_API_PASSPHRASE` 由 `py-clob-client` 首次运行时自动生成,留空即可。

> `POLY_FUNDER` 必须是 proxy wallet,不能填 EOA。`POLY_SIGNATURE_TYPE=1` 下签名是对 EOA 名下的 GNOSIS_SAFE 走的,两个混了会静默签名失败。

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
代码不会热重载,改完要走完整重启流程:

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
- `output.log` — `nohup` 接的 stdout / stderr。

两个都已 gitignore。

### 数据库
SQLite 在 `v4.db`。改 schema 之前先备份:

```bash
cp v4.db v4.db.bak_$(date +%s)
```

`v4.db` 和 `v4.db.bak_*` 都已 gitignore。运营状态不进版本控制,靠这些时间戳备份。

## 文件结构

```
polymarket/v4/
├── main.py                            # 入口
├── requirements.txt
├── .env.example                       # 模板,复制为 .env 后填
├── .gitignore
├── CLAUDE.md                          # 项目规则 (Claude Code 会话用)
├── README.md                          # 英文版
├── README.zh.md                       # 中文版 (本文件)
├── modules/
│   ├── dashboard.py                   # Flask UI
│   ├── db.py                          # SQLite + position_meta CRUD
│   ├── executor.py                    # py-clob-client v2 封装
│   ├── monitor.py                     # 决策引擎
│   ├── prompts.py                     # Claude.ai prompt 模板
│   ├── scanner.py                     # Gamma 扫描器
│   ├── tags.py                        # tag 白名单
│   └── *.bak_pre_*                    # 历史备份,不会被 import
└── v4.db                              # SQLite (gitignored)
```

## 备注

- 老的 v3 代码在 `<sibling-v3-dir, not in this repo>`,已冻结,不属于这个 repo。**不要改**。
- `modules/*.bak_pre_*` 是有意保留的历史备份,文件还在但代码里不会 import。
- `.env`、`v4.db`、`*.log`、`.venv` 都被 `.gitignore` 排除。
- 用的是 `py-clob-client` v2 (不是 v1)。装 v2 之后 import 路径还是 `py_clob_client`。
- `CLAUDE.md` 是 Claude Code 会话用的项目规则,记录了哪些阈值能调、`.env` 不要碰等约束。
