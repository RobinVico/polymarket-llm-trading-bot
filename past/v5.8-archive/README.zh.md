# Polymarket v5.8

*[English](README.md)*

Polymarket 预测市场半自动交易 bot。结构 = edge-based 决策引擎 + 三档止损 + 两条止盈 + 本地 Flask dashboard + Tailscale-only 公网访问 (密码鉴权)。概率校准走 Claude.ai 手动闭环, 监控和执行是自动的。

**完整技术报告**: [TECHNICAL_REPORT.md](TECHNICAL_REPORT.md) (英文摘要) · [技术报告.md](技术报告.md) (中文完整版)

## 当前版本概要 (v5.8)

```
自动卖规则 (优先级从高到低):
  1a. TAKE_PROFIT_PRICE   best_bid ≥ 90¢ → 自动全卖
  1b. TAKE_PROFIT_PNL     (best_bid − avg) / avg ≥ +100% → 自动全卖
  2.  STOP_LOSS           按 LLM 入场分类的三档:
                            convergent  (硬数据) -20%
                            hybrid      (混合)   -35%
                            event_driven (事件)  不止损, $0.05 地板兜底
  3.  TIME_STOP           距结算 ≤ 2 天 + 价格漂移 < 5pp → 全卖

决策状态 (等用户操作):
  HOLD / MARGINAL / SOFT_NEGATIVE / AT_TARGET
```

## 架构

| 路径 | 职责 |
|---|---|
| `main.py` | 入口。初始化 SQLite, 启动 monitor 线程, Flask 跑在 `127.0.0.1:5051` (仅本机, 见下方 Security)。 |
| `modules/dashboard.py` | Flask UI + HTTP 路由 + 密码鉴权层 + login/logout。 |
| `modules/monitor.py` | v5.8 决策引擎 (3 档止损 + 双止盈 + TIME_STOP + edge-based + sweep 守卫 + 自动写 closed_positions), 180s 心跳。 |
| `modules/scanner.py` | Polymarket Gamma 扫描器, 带 `FILTERS` 字典。 |
| `modules/executor.py` | py-clob-client v2 封装。Partial-fill 检测 (成交 < 95% → 重试)。 |
| `modules/db.py` | SQLite schema (WAL 模式) + CRUD + portfolio_snapshot 备份 + closed_positions 分析 + login_attempts。 |
| `modules/prompts.py` | DISCOVERY + REEVAL v5.2 prompt 模板。 |
| `modules/tags.py` | 39 个 tag 白名单 + 黑名单 + 白名单优先。 |

## 版本归档

| 版本 | 状态 | 路径 |
|---|---|---|
| **v5.8** | **当前** (本 README, repo 根) | `./` |
| v5.6 | 归档 (公开重构前的快照) | [`past/v5.6-archive/`](past/v5.6-archive/) |
| v5.0 | 归档 (3-tier 止损 + 急跌冻结) | [`past/v5/`](past/v5/) |
| v4 | 归档 (单一 -25pp DISASTER) | [`past/v4/`](past/v4/) |

完整 v5.0 → v5.8 演进 (每个版本改了什么, 解决什么问题) 见 [TECHNICAL_REPORT.md](TECHNICAL_REPORT.md) (英文) 或 [技术报告.md §十三](技术报告.md) (中文)。

## 安装

```bash
git clone https://github.com/RobinVico/polymarket.git
cd polymarket
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 填入 POLY_PRIVATE_KEY, POLY_FUNDER, POLY_SIGNATURE_TYPE=1
                      # 还要填 DASHBOARD_PASSWORD, FLASK_SECRET_KEY (32+ 字符随机)
nohup python3 main.py > output.log 2>&1 &
```

Dashboard: <http://localhost:5051>

> `POLY_FUNDER` 必须是 proxy wallet, 不能填 EOA。`POLY_SIGNATURE_TYPE=1` 下签名走 EOA 名下的 GNOSIS_SAFE, 两个混了会静默签名失败。

## 安全 (v5.8)

Dashboard 默认 **只 listen 127.0.0.1:5051** (仅本机)。要远程访问:
1. 在 host 和任何客户端设备装 Tailscale (`tailscale.com/download`)
2. `tailscale serve --bg http://localhost:5051` 把 dashboard 暴露到你的 tailnet (仅你 Tailscale 账号下设备能访问)
3. 从 tailnet 设备访问 `https://<your-host>.<your-tailnet>.ts.net` → 第一次输 `DASHBOARD_PASSWORD` → cookie 90 天有效

如果还要公开 internet 访问 (比如分享给非 Tailscale 用户): `tailscale funnel --bg on`。密码层照样挡。爆破限流 (5 次错 → 30 分钟锁, 持久化跨重启)。

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
- `closed_positions` 表 (v5.8+) 存每个已平仓位的实现 PnL / 持有时长 / 退出原因 / Claude 原始 q 估计 — 用于 win-rate 和 calibration 分析。

## 文件结构

```
polymarket/                          (GitHub public repo, v5.8 在根)
├── main.py
├── requirements.txt
├── .env.example                     # 模板, 复制为 .env 后填
├── LICENSE                          # MIT
├── SECURITY.md / SECURITY.zh.md     # 漏洞披露
├── CLAUDE.md                        # 项目规则 (Claude Code 用)
├── README.md / README.zh.md         # 英文/中文 README (这个)
├── TECHNICAL_REPORT.md              # 英文技术报告 (high-level)
├── 技术报告.md                       # 中文完整技术报告 (17 节, 完整 history)
├── modules/                         # v5.8 代码
├── scripts/                         # cron + 恢复
├── data/.gitkeep                    # 占位; portfolio_snapshot.jsonl 已 gitignored
├── v4.db                            # SQLite (gitignored)
└── past/
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
