# 任务: 为我的 Polymarket 个人交易 bot 设计仓位 sizing 策略

你是金融工程顾问. 我有一个跑在自己 Mac 上的 Polymarket 个人交易 bot, **现在所有仓位都用固定 $5** (低概率题手动减到 $1-2), 这显然不是最优 sizing. 我需要你设计一个**数学合理 + 跟我现有信号结合 + 我能直接落地的公式**.

请用 Deep Research 模式. 必要时引用 Kelly Criterion 文献 / prediction-market 实证研究 / LLM 概率校准研究.

---

## 第一部分: 我现有的系统 (你必须先理解, 公式要跟这些信号集成)

### 1.1 项目定位

- **Polymarket 个人半自动 bot**, 跑在 Mac mini, 24/7 监控持仓
- 决策闭环: 我用 Claude.ai Research 估每个市场的真概率 q → 跟市场价 p 比较算 edge → 入场 → bot 自动监控 → 触发止盈/止损时自动卖
- 当前版本 v5.8, 完整代码在 https://github.com/RobinVico/polymarket-llm-trading-bot

### 1.2 当前真实状态 (2026-06-01 实测)

- **Bankroll (总资产)**: $87.05
  - Cash 空闲: $64.34 (74%)
  - Position value: $22.72 (26%)
- **当前持仓**: 4 个

| Side | 入场价 (avg) | 当前价 | 成本 | Slug |
|---|---|---|---|---|
| No | 0.700 | 0.790 | **$5.00** | iran-agrees-to-end-enrichment-of-uranium-by-june-30 |
| No | 0.530 | 0.855 | **$5.00** | us-x-iran-permanent-peace-deal-by-june-15-2026 |
| No | 0.510 | 0.815 | **$5.00** | strait-of-hormuz-traffic-returns-to-normal-by-end-of-june |
| No | 0.131 | 0.138 | **$1.00** | will-mike-mazzei-win-the-2026-oklahoma-governor-republican |

**观察**: 3 个高概率 No 题 (入场价 0.5-0.7) 我都买 $5, 1 个低概率 longshot (入场价 0.13) 我自觉买 $1. **没有公式, 全凭感觉**. 我自己也不知道为什么是 $5 不是 $7 或 $3.

### 1.3 我对每个入场仓位掌握的所有信号 (你的公式可用这些作为输入)

每次入场时, Claude.ai Research 给我以下数据 (来自 DISCOVERY prompt):

| 信号 | 含义 | 范围 |
|---|---|---|
| `q` | Claude 估的真实概率 (持有方向兑现概率) | 0-1 |
| `p` | 市场当前价 (持有方向 token 价) | 0-1 |
| `edge_calibrated` | 校准后 edge = q - p | -1 到 +1 |
| `edge_actionable` | 实际可获取 edge = edge_calibrated - 执行成本 (~3pp) | -1 到 +1 |
| `confidence` | Claude 对这次研究的信心 | `high` / `medium` / `low` |
| `stop_loss_tier` | 止损分档 (决定 bot 自动卖阈值) | `convergent` / `hybrid` / `event_driven` |
| `days_to_resolution` | 距结算天数 | 1-365 |
| `annual_IRR` | 年化收益 = edge/p × 365/days × 100% | 0-2000% |

### 1.4 止损档分类 (重要 — 直接影响下行风险)

| Tier | 类型 | bot 自动止损 |
|---|---|---|
| `convergent` | 真相收敛型 (票房/汇率/比分等可一锤定音的数字) | **-20%** |
| `hybrid` | 混合型 (候选人选举, 有民调 + 政治反复) | **-35%** |
| `event_driven` | 事件驱动 (政治/外交/谈判) | **不百分比止损**, 只价格 < $0.05 才砍 (= 最大损失约 -90% 到 -95% 取决于入场) |

不同 tier 的 **expected drawdown** 完全不同, 这必须影响 sizing.

### 1.5 我现有的其他约束

1. **Polymarket 平台 OPSEC 风险**: 2026-05-22 有过 $520K UMA Adapter 漏出事件. 我不想在平台留太多钱. **30 天可接受损失约 $30** 作为约束 (= 单月最大愿意亏的金额).
2. **集中度限制 (我自己定的)**: 同一 narrative cluster 暴露 ≤ 20% bankroll. 目前 4 仓里 3 个全押"伊朗 6 月不出事", 实际是 ~1 个 cluster 占 75%, 已经违规 (这是另一个待解问题, 但 sizing 公式应该有 "cluster 满了就把后续 size 砍到 0" 的 hard cap).
3. **DISCOVERY 推荐门槛**: edge_actionable < 8pp (标准模式) 就不入场. 所以你设计的公式入参 edge_actionable 已经 ≥ 8pp.
4. **IRR 门槛**: 年化 IRR < 30% 不入场 (避免长 fuse 占资金).
5. **LLM 过度自信**: arxiv 2505.02151 等论文显示 LLM 的概率估计普遍**过度自信 20-60%**. 我的 q 需要打折扣.

---

## 第二部分: 我的直觉 (你可以参考, 但不要被绑死)

我手动经验:
- **高概率 (p > 0.5)** 题: 买 $5
- **中等 (0.3 < p < 0.5)**: 还没遇到过, 估计也 $5
- **低概率 (p < 0.3) longshot**: 买 $1-2

但我不知道为什么是这些数字. 我的直觉是:
- Longshot 单仓 EV 高 (兑现倍数大), 但兑现概率低, 输的次数多, 心理承受不了大仓
- 高概率 q 离 0.5 不远, 输赢 binary, 中等仓 size 合理

但我没数学依据.

---

## 第三部分: 我希望你做什么 (RESEARCH GOAL)

### 3.1 设计一个 `position_size_usd(...)` 公式

**输入**:
- `q` (Claude 估的概率, 0-1)
- `p` (市场当前价, 0-1)
- `confidence` ∈ {high, medium, low}
- `stop_loss_tier` ∈ {convergent, hybrid, event_driven}
- `days_to_resolution` (天数)
- `bankroll_usd` (当前总资产)
- `cluster_current_exposure_usd` (这个 narrative cluster 当前已占金额)
- `cluster_cap_usd` (默认 = bankroll × 0.20)

**输出**:
- `position_size_usd` (建议下注金额, 美元)
- `reason_breakdown` (一行字解释为什么是这个 size, 含: kelly_raw / haircut / cluster_clip / floor / ceil 哪些起作用)

**约束**:
- 单仓 size ∈ [$1, $15] (上下 hard cap, 不能更小也不能更大)
- 如果 cluster 已经满 (cluster_current_exposure + size > cluster_cap), 自动 clip 到 cap 余额; 余额 < $1 时 return $0 + reason="cluster full"
- 输出必须是整数美分 (.01 精度)

### 3.2 公式必须解决以下问题

请明确说明每个机制如何处理:

1. **Kelly 用全 Kelly 还是分数 Kelly**? 推荐多少分数 (1/4, 1/2, ...)? 学术依据?
2. **LLM 过度自信折扣**: q 怎么打折扣? 折扣率 (30%? 40%?)? 是 q 缩放, 还是 edge 缩放? 还是分 confidence 不同折扣?
3. **Tier 风险调整**: convergent / hybrid / event_driven 不同 tier 的 max loss 完全不同 (-20% / -35% / -90%+), 这应该让 sizing 不同吗? 怎么调?
4. **Longshot 处理**: 当 p < 0.15 (低概率买 longshot 反向, edge 可能很大), 公式应该减仓还是加仓? 为什么?
5. **Days to resolution**: 同样 edge, 7 天和 60 天到期应该 size 一样吗? 怎么折现?
6. **Cluster 满了**: 怎么 gracefully 处理? 完全砍到 0 (太极端), 还是按比例缩? 给出具体规则.

### 3.3 给我一组**实测验证**

用我当前 4 个仓位的数据 (上面表格), 假设我**今天重新入场**, 你的公式会推荐多少 size? 跟我现在的固定 $5 / $1 比对, 哪几个该买更多, 哪几个该买更少, 为什么?

对每个仓位输出一行:

| Slug | 当前 size (我的) | 公式推荐 size | 公式 size 的 reason | 应该改吗? |
|---|---|---|---|---|
| iran-end-enrichment | $5 | $X | ... | ✓/✗ |
| us-iran-peace | $5 | $X | ... | ✓/✗ |
| ... | ... | ... | ... | ... |

### 3.4 给我代码落地建议 (伪代码够, 不需要可执行)

我的 bot 用 Python + Flask + SQLite. 当前 `modules/dashboard.py` 有一个 record_position 路由接收用户填的 q / confidence / tier. 你的 sizing 公式应该插在哪个流程节点?

- 选项 A: dashboard 在用户填完 q + confidence + tier **之后**, 调你的公式自动算出 size, 显示给用户 (用户仍可手动 override)
- 选项 B: 加一个独立 `/api/suggested_size` 路由, 前端调用显示推荐 size
- 选项 C: ?

推荐哪个? 给出大致代码骨架 (Python function 签名 + 关键计算步骤).

### 3.5 风险 / 回退

- 公式 worst-case 推荐了 $15 但仓位 -90% 怎么办? 单仓 max loss 可接受范围?
- 公式参数 (Kelly 分数 / LLM 折扣率 / cluster cap %) 都应该是**可配置常量** (env var 或 config dict), 方便我事后调. 列一下哪些参数是 tunable 的.
- 如果公式跑出来全是 < $1 或 > $15 边界值, 说明输入数据有问题 — 怎么 sanity check?

---

## 第四部分: 输出格式 (严格遵守)

### A. 学术依据 (3-5 段, 含 inline citation)

引用关键文献:
- Kelly Criterion 原论文 / 分数 Kelly 文献
- Prediction market 长 fuse 价格压向 50% (Berg 2008, Page-Clemen 2013)
- LLM 概率过度自信 (arxiv 2505.02151 等)
- 任何你认为相关的 sizing / risk-of-ruin 研究

### B. 最终公式 (清晰的数学 + 中文注释 + Python 伪代码)

```python
def position_size_usd(q, p, confidence, stop_loss_tier, days_to_resolution,
                     bankroll_usd, cluster_current_exposure_usd, cluster_cap_usd) -> tuple[float, str]:
    # ... 步骤 1: LLM 过度自信折扣
    # ... 步骤 2: Kelly 分数
    # ... 步骤 3: Tier 风险调整
    # ... 步骤 4: Days/IRR 折扣
    # ... 步骤 5: cluster cap clip
    # ... 步骤 6: $1-$15 hard bound
    return size_usd, reason
```

### C. 实测验证表 (我现有 4 仓位, 见 3.3)

### D. 落地建议 (代码骨架 + 哪个 dashboard 节点插入)

### E. 可调参数列表 (env var / config dict)

### F. 风险 / 边界 case 处理

---

## ⚠️ 最终输出契约 (必须遵守)

1. **全程中文** (英文专有名词 / 论文引用 / 代码 ✓)
2. **末尾必须有一个用 ``` 包围的"最终公式"卡片**, 一目了然:

```
## 最终 sizing 公式

position_size_usd(q, p, conf, tier, days, bankroll, cluster_exp, cluster_cap):
  haircut_q = q × ___ if conf=high else ___ × q if medium else ___
  edge_haircut = haircut_q × (1-p) - (1-haircut_q) × p
  kelly_f = edge_haircut / (1-p)
  scaled = kelly_f × ___  # fraction Kelly
  raw_size = bankroll × scaled
  ... (tier / days 调整) ...
  clipped = min(raw_size, cluster_cap - cluster_exp)
  final = max($1, min($15, clipped))
  return final, reason
```

3. 末尾**给个具体推荐参数值** (Kelly fraction, LLM haircut by confidence, tier adjustment), **不要含糊**.

研究开始. 不用客套, 直接进入分析.
