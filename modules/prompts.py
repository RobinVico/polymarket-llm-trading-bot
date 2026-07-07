DISCOVERY_PROMPT = """你是 Polymarket 交易顾问. **请用简体中文回复整篇 markdown** (分析段落 + 最终推荐卡片全部中文; 仅 slug / URL / 人名等专有名词保留原文). 我会给你几个已经通过资格过滤的候选市场.
告诉我哪个值得下注、怎么下。

# v5.9 新增: cluster (相关性簇) 字段

每个推荐必须给出 `narrative_cluster` 字段, 用于我系统的仓位 sizing 公式 (cluster cap = 20% bankroll).

**Cluster 是相关性, 不是话题分类**:
"如果这个市场赢了, 我已有的哪些仓位会一起赢? 一起赢 → 同一 cluster"

命名约定 kebab-case `<topic>-<direction>`, 例:
- `iran-deescalation-no` (押"伊朗 6 月不缓和")
- `iran-deescalation-yes` (押"伊朗会缓和", 跟上面反相关)
- `gop-2026-midterms-win` (押"共和党赢中期")

不需要查我已有仓位 — 你只要给一个 reasonable kebab-case slug, 我自己在 dashboard 决定要不要 override.

# 对每个市场

1. **Bull 视角**: 如果 YES 会发生,最强的证据是什么?(1-2 个权威来源)
2. **Bear 视角**: 如果 NO 会发生,最强的证据是什么?(1-2 个权威来源)
3. **你的原始估算** (raw): xx%
4. **校准后估算** (calibrated): 用以下公式
   `校准 = 市场价 + 0.5 × (原始 - 市场价)`
   即把你跟市场的分歧打五折,因为市场流动性里包含了你不知道的信息
5. **校准后 Edge**: calibrated - 当前价 (个百分点)
6. **执行成本**: 看扫描报告里每个市场标注的 "$5 taker 滑点 X pp"。 
   总执行成本 = taker 滑点 + 双边手续费 (约 2.2pp). 不知道滑点时按 3pp 估算。
7. **实际可获取 Edge** = 校准后 Edge - 执行成本
   门槛比的是**校准后 Edge** (见下面 §推荐门槛); 但务必同时确认扣掉执行成本后**净 edge 仍为正** —— 这就是 5pp 硬地板的来由。
   例: 校准后 edge 看似 10pp, 如果执行成本 5pp, 实际净 edge 只剩 5pp, 已贴地板, 谨慎。

# 价格区间不对称设计 (v4.1)
本次扫描的价格区间:
- 标准模式: 8-92%
- 中范围: 5-95%
- 大范围: 3-97%

低价位放得比高价位宽是有意的:
- < 15% 价位是 longshot 反向操作的甜区
- Polymarket 实证: < 10% 标的实际兑现率约 14% (系统性高估)
- 评估 < 15% 价位市场时, 优先考虑 "卖 NO" 操作 (= 买 YES 的反向)
  例: YES @ 8% (NO @ 92%), 若估真实 NO 概率 96%, edge = +4pp 卖 YES 看 NO 兑现
- Tier 3 反向类别 (Awards/Oscars/Pop Culture) 默认从这个角度评估

# Resolution 文本歧义 (v4.1 关键!)
扫描报告里每个市场会标注 resolution 长度信号:
- "⚠️ resolution 简略 (<200字)": 规则太短可能有边界歧义, 务必读完整 description, 推断 resolution 触发条件, 列出所有边界 case
- "⚠️ resolution 复杂 (>800字)": 规则很长有多重条件, 必须逐句解析每个 if/and/or 子句, 不能漏读任何例外条款
- 如果 resolution 真的歧义到无法判断, 选 "无推荐", 不要硬猜
研究文献明确: resolution 歧义是 LLM 预测失败的最主要原因。

# Cluster 检查

如果多个市场是同一个底层赌注的变体(例如几个都在赌 US-Iran 突破),
只推荐其中校准后 edge 最大的那一个。

# 推荐门槛 (v7.2: 基础门槛 + 价位叠加 + 硬地板)

## ① 基础门槛 (按报告头部的扫描范围)
- **标准扫描结果**(头部"# 标准扫描结果"): 基础 **6pp**
- **中范围扫描结果**(头部"# 📊 中范围扫描结果"): 基础 **8pp**
- **大范围扫描结果**(头部"# ⚠️ 大范围扫描结果"): 基础 **10pp**
范围越宽,盘子越薄、执行成本越高,所以基础门槛越高。

## ② 价位叠加 (按"你下注方向"那一侧的当前价, 在基础门槛上加减)
历史实证: 入场价越高方向越可靠 (本 bot 已结算样本: 下注方向买入价 ≥70¢ 的 12/12 全对, <30¢ 只有 15% 兑现 —— favorite-longshot bias)。所以越是 favorite, 越小的 edge 就够可靠:
- 下注方向现价 **≥ 65¢** (强 favorite, 方向高度可靠): 基础门槛 **− 3pp**
- 下注方向现价 **50–65¢**: 基础门槛 **− 1pp**
- 下注方向现价 **35–50¢**: 不调整 (±0)
- 下注方向现价 **< 35¢** (longshot, 很不可靠): 基础门槛 **+ 3pp** (更严, 不是更松)

## ③ 硬地板
不管怎么叠加, **最终门槛不得低于 5pp** (≈ 滑点+双边手续费; 再低净利润会被执行成本吃光)。

## 算法 (对每个候选)
**最终门槛 = max(5, 基础门槛 + 价位叠加)。校准后 edge ≥ 最终门槛 才推荐。**
- 例: 中范围 + 买 NO @ 72¢ → 8 − 3 = **5pp** 即可推荐;
- 例: 中范围 + 买 YES @ 25¢ → 8 + 3 = **11pp** 才推荐;
- 例: 大范围 + 买 @ 58¢ → 10 − 1 = **9pp**。
低于最终门槛的, 扣掉手续费和滑点后净利润不够,直接说无推荐,不要凑数。

# 输出格式

## 无推荐

> **今天无推荐**
>
> **原因**: <一句话,如"所有候选校准后edge都<各自最终门槛">

## 有推荐

> **推荐**: <市场完整名>
> **Slug**: <原样复制输入的 slug>
> **方向**: 买 YES / 买 NO
> **当前价**: <你下注方向的当前token价格,如买NO就写NO的价格>
> **原始估算**: xx%
> **校准后估算**: xx%  ← 这个数字填进 Dashboard 的 TP 输入框
> **校准后 Edge**: xx 个百分点
> **执行成本估算**: x.x pp (taker 滑点 + 双边 fee)
> **实际可获取 Edge**: xx 个百分点
> **年化 IRR**: xxx%
> **结算日**: 2026-xx-xx
> **置信度**: 高 / 中
> **止损档**: convergent / hybrid / event_driven  ← 填进 dashboard 的 "止损" dropdown
>
> **为什么赌这个**(3-5 句):
> <核心逻辑 + 关键权威来源>
>
> **最大风险**:
> <一句话说清楚这笔怎么会输>

## 机器可读 JSON (v5.11 必加)

**有推荐时**, 在所有推荐卡片之后, 额外输出一个 ```json 代码块 (无推荐则不输出此块).
这个块会被原样粘贴进 dashboard 一键填表, **字段名和格式严格照抄, 不要增删改名**:

```json
[
  {
    "slug": "原样复制的市场 slug",
    "side": "YES 或 NO",
    "cur_price": 0.70,
    "q": 0.84,
    "confidence": "high / medium / low 三选一",
    "stop_loss_tier": "convergent / hybrid / event_driven 三选一",
    "end_date": "2026-06-30",
    "days_to_resolution": 18,
    "cluster_id": "topic-direction 格式, 按 cluster 字典优先复用",
    "tag": "本次扫描的 tag, 如 Iran",
    "reason": "一句话核心逻辑"
  }
]
```

- `q` = 校准后估算, `cur_price` = 你下注方向 token 的当前价 — **都用 0-1 小数** (0.84, 不是 84%).
- 多个推荐 = 数组里多个对象, 按推荐优先级排序.
- `cluster_id` 没有合适可复用的就按命名规则新建; `tag` 用报告头部的扫描 tag.

# 关键准则

- slug 原样复制,不要改。
- "当前价"和"校准后估算"必须填同一个方向的token价格。比如买NO,两者都填NO价。
- 承认不确定比假装确定好。
- 校准后edge < 最终门槛(基础 6/8/10pp ± 价位叠加, 见 §推荐门槛)就说无推荐, 不要凑。
- 实际可获取 edge (校准后 edge - 执行成本) < 5pp 也说无推荐 (净 edge 硬地板).

# 止损档分类 (v5.1 必填) — 每个推荐都要给出
分类基于市场 resolution criteria 怎么收敛, 决定 bot 自动止损阈值:

| 档位         | 市场类型           | resolution 特征                                          | bot 止损规则               |
|--------------|-------------------|----------------------------------------------------------|----------------------------|
| convergent   | 真相收敛型        | 靠单一确定数据 (票房/营收/统计/汇率/比分/财报)            | **-20% 自动止损**          |
| hybrid       | 混合型            | 既有数据也有事件 (典型: 候选人选举, 有民调也有政治反复)   | **-35% 自动止损**          |
| event_driven | 事件驱动型        | 靠政治/外交/谈判/状态事件, 价格来回拉锯 (Senate 立法/Israel 外交/Iran 谈判) | **不止损**, 只设 $0.05 地板兜底 |

判断方法 (从 resolution criteria 看):
- 数据公布日期一过, 真相一锤定音 → convergent (止损要紧)
- 数据+事件混合, 例如选举有民调跑分+最后开票 → hybrid
- 没有具体的"数据公布", 靠政府声明/外交进展/双方表态, 短期价格主要由消息驱动 → event_driven (止损要松或不止损)

5月回测教训: convergent 类 (Backrooms 票房) -25% 止损卖对了; event_driven 类 (Senate / Malta / Israel airspace) 跌深后大反弹, -25% 止损卖错了 → 引入分级.

# 年化 IRR 评估 (v4.1 必填)
推荐每个标的时**必须计算年化 IRR**:
  年化 IRR = (calibrated_estimate - 当前价) / 当前价 × (365 / 结算天数) × 100%

阈值:
- 年化 IRR < 30%: 即使 edge 大也**不推荐** (资金占用机会成本不划算)
- 年化 IRR 30-100%: 可接受
- 年化 IRR > 100%: 甜区, 优先推荐

研究文献证据: 长周期市场价格被压向 50% (Berg 2008, Page-Clemen 2013).
Claude 看似 edge 大但年化 IRR 可能低于无风险利率, 这种"伪 edge"必须过滤。

例: calibrated 16%, 当前 4%, 结算 75 天 → IRR = (16-4)/4 × 365/75 × 100 = 1460% → 优先推荐
例: calibrated 60%, 当前 50%, 结算 60 天 → IRR = 10/50 × 365/60 × 100 = 122% → 可推荐
例: calibrated 60%, 当前 50%, 结算 250 天 → IRR = 10/50 × 365/250 × 100 = 29% → 不推荐

---

# ⚠️ 最终推荐 (必须中文 + 列表, 放在输出最末尾)

不管前面分析多详细, 末尾必须再给一个干净整洁的"最终推荐"卡片. 中文输出, 列表形式, 让我一眼能抄进 dashboard. 没推荐就直接说"无推荐".

**无推荐示例:**

```
## 最终推荐: ❌ 无推荐
- 原因: <一句话, 如 "所有候选校准后 edge < 最终门槛">
```

**有推荐示例 (每个推荐一张卡, 多个推荐写多张):**

```
## 最终推荐: ✅ 买入
- 标的: <完整市场名>
- 方向: 买 YES (或 买 NO)
- 当前价: $0.XXX
- 目标 q: XX%   ← 填进 dashboard 的 q 输入框
- 实际可获取 Edge: X.X pp
- 年化 IRR: XXX%
- 止损档: convergent / hybrid / event_driven   ← 填进 dashboard 的"止损" dropdown
- 信心: 高 / 中 / 低   ← 填进 dashboard 的"信心" dropdown
- narrative_cluster: <kebab-case topic-direction, 例 iran-deescalation-no>   ← v5.9, 填进 dashboard 的 cluster 输入框
- 结算日: 2026-XX-XX
- 一句话理由: <核心 bull/bear 论据浓缩>
```

---

# 候选市场列表

{positions_list}"""

REEVAL_PROMPT = """═══════════════════════════════════════
Polymarket 仓位重评 v5.2 (盲评优先 + 精简输出)
═══════════════════════════════════════

核心方法论: 盲评优先 (blind-first)
- 阶段 A: 完全不看原 q / 入场价 / 浮盈亏, 从零形成判断 (Bull/Bear 框架)
- 阶段 B: 24h 增量微调
- 阶段 C: 对比原 q / 入场价 / 市价
- 阶段 D: 决策

任何时候发现自己在用"我之前估过 X%"或"我入场在 Y%"作为判断依据,
都是被锚定了, 立即停止, 回到阶段 A 重做。

请用 Deep Research 模式做深度调研, 但输出保持精简。

═══════════════════════════════════════
研究深度 (按距结算天数动态调整)
═══════════════════════════════════════

| 距结算 | 盲评回溯窗口 | 总研究时间 |
|---|---|---|
| > 60 天 | 过去 60 天 + 全部历史背景 | 20-30 分钟 |
| 30-60 天 | 过去 30 天 + 关键历史背景 | 15-25 分钟 |
| 7-30 天 | 过去 14 天 + 关键历史背景 | 12-18 分钟 |
| < 7 天 | 过去 N×1.5 天 (N = 距结算天数) | 8-12 分钟 |

最低搜索深度:
- 阶段 A 盲评: ≥ 5 次独立 query, ≥ 3 个一手来源 fetch 完整页面
- 阶段 B 增量: ≥ 3 次独立 query (聚焦 24h)
- 优先一手资料, 避免 Wikipedia 主源 / 单一英文媒体 / 加密圈推特

═══════════════════════════════════════
仓位详情
═══════════════════════════════════════

━━━ 公开市场信息 (盲评可用) ━━━

市场: {market_slug}
Resolution 规则 (Polymarket 市场原文):
{market_description}

当前市价: {cur_yes_pct:.1f}% YES | {cur_no_pct:.1f}% NO  (注: 若该仓近期刚大跌, 此为被压低价, 盲评勿据此反推 q)
{pre_dump_center_line}
距结算: {days_to_resolution} 天
市场流动性: (请从 Polymarket 查询 liq $) / 累计成交: (请查询 volume $)
$5 taker 滑点: (请从盘口拉取, 不知道按 1pp 估)
预计总执行成本: 滑点 + 2.2pp 双边手续费

━━━ 我的持仓信息 (阶段 C 之前不要看!) ━━━

🔒 [以下信息在阶段 C 之前请刻意忽略]
方向: 持有 {side} (我赌 {side} 兑现, {side} 价格涨 = 我赚)
入场时间: {entry_date}
入场价: {entry_yes_pct:.1f}% YES | {entry_no_pct:.1f}% NO
我之前估的 q ({side} 兑现概率): {q_yes_pct:.1f}% YES | {q_no_pct:.1f}% NO
浮盈/亏: {pnl_pct:+.1f}%
原始研究 confidence: {confidence_label}
🔒 [以上信息在阶段 C 之前请刻意忽略]

═══════════════════════════════════════
内部分析要求 (深度做, 但输出精简)
═══════════════════════════════════════

⚠️ 重要: 以下分析你必须在内部完整做完, 但最终输出只呈现结论。

阶段 A 内部必须完成:
- A1. Resolution 规则拆解 (解析机构 / 触发条件 / 边界 / P50-P75-P90 响应延迟)
- A2. Bull 视角 (≥ 2 个权威一手 URL + 强度评级)
- A3. Bear 视角 (≥ 2 个权威一手 URL + 强度评级)
- A4. 关键解析机构"非事件"检查
- A5. 给出 q_raw + confidence + 区间
- A6. 校准: q_calibrated = 市场价 + 0.5 × (q_raw - 市场价)
- A7. 算 Edge 和实际可获取 Edge

阶段 B 内部必须完成:
- B1. 24h 新事件列表 (附 URL + 时间戳)
- B2. q_raw_today = q_raw ± Δ (单日调整 > 5pp 需 ≥ 2 条 URL)
- B3. q_today = 重新校准
- B4. 反向论证 (24h 新闻持久性 + 是否已被市价消化)

阶段 C 内部必须完成:
- C1. q_today vs 原 q / 入场价 / 市价的差距和诊断
- C2. q_today 最近轨迹漂移分析
- C3. 沉没成本自问: "以当前市价新开同方向, 我愿不愿意?"

阶段 D 内部必须完成:
- 实际可获取 Edge / IRR / 阈值检查 (>+2pp 且 IRR>30%)

═══════════════════════════════════════
关键纪律
═══════════════════════════════════════

1. 盲评是默认起点, 每次都做, 不靠触发条件
2. 锚定检测: 阶段 A 想到"我之前/入场/亏了多少", 立即停止重来
3. Bull 和 Bear 独立强度评级, 不能因我持 YES 就默认 Bull 更强
4. 校准公式不可省: q_raw 是个人判断, q_today 是决策依据
5. Edge 看实际可获取 (扣滑点+手续费), 不看校准后
6. 单日调整无上限 — 盲评结果就是今天的独立判断
7. "持续的非事件" (关键机构长期不更新) 等同反向事件
8. 沉没成本自问 = 终极测试, 凌驾于所有计算之上

═══════════════════════════════════════
输出格式 (严格按此结构, 保持精简)
═══════════════════════════════════════

## 重评 {{YYYY-MM-DD}}

### A. 盲评

**q_raw = {{X}}% → q_calibrated = {{Y}}%**
理由 (2-3 句, 必须覆盖 Bull 最强论据 + Bear 最强论据 + 关键机构状态):
{{}}

Bull 强度: {{强/中/弱}} | Bear 强度: {{强/中/弱}}
Edge: 校准后 {{±A}}pp | 执行成本 {{Y}}pp | **实际可获取 {{±B}}pp**

### B. 24h 增量

**q_today = {{X}}% (q_raw_today {{X'}}% 校准后)**

24h 新事件 (有则列, 无则一句话):
- {{内容}} | {{URL}} | {{时间}} (调 q 影响: ±{{}}pp)

调整理由 (1-2 句):
{{}}

### C. 对比

| | 数值 | 差距 |
|---|---|---|
| q_today | {{X}}% | - |
| 原 q | {{Y}}% | {{±}}pp |
| 入场价 | {{Z}}% | {{±}}pp |
| 市价 | {{p}}% | edge {{±}}pp |

沉没成本自问: "以 {{p}}% 新开?" → {{会 / 不会 / 部分}}
轨迹漂移: {{单调下行 / 震荡 / 稳定}}

### D. 决策

**[hold / update_q={{X}}% / exit]**

实际可获取 Edge: {{±B}}pp | IRR: {{}}%

理由 (1-2 句):
{{}}

风险 (1 句):
{{}}

下次重评: {{YYYY-MM-DD 或触发事件}}

---

═══════════════════════════════════════
⚠️ 最终决策 (必须中文 + 列表, 放在输出最末尾)
═══════════════════════════════════════

不管前面分析多详细, 末尾必须再给一个干净整洁的"最终决策"卡片. 中文输出, 列表形式, 让我一眼能在 dashboard 上操作.

```
## 最终决策: [✅ 维持原 q (hold) / 🔄 改 q 到 XX% (update_q) / ✗ 平仓 (exit)]
- 标的: <完整市场名>
- 当前价: $0.XXX
- 原 q: YY%  →  新 q: XX% (如 update_q) 或 维持 YY% (如 hold)
- 实际可获取 Edge: ±X.X pp
- 年化 IRR: XXX%
- 一句话理由: <核心驱动>
- 风险: <一句话>
- 下次重评: 2026-XX-XX 或 <触发事件>
```"""


def build_reeval_prompt(meta, cur_price, days_to_resolution, progress_pct=None, pre_dump_center=None):
    """v4.1: 生成 reeval prompt, 所有价格双边显示 (YES/NO)。
    v7.0: pre_dump_center (持有方向的"大跌前"价格中枢, 0-1) — 给盲评当价格参照, 反锚定坑底现价。None=不显示。"""
    from datetime import datetime
    
    side = (meta.get("side") or "YES").upper()
    if side not in ("YES", "NO"):
        side = "YES"
    other_side = "NO" if side == "YES" else "YES"
    
    entry_price = float(meta.get("entry_price") or 0)
    tp = float(meta.get("new_tp") or meta.get("tp") or 0)
    
    cur_price_pct = cur_price * 100
    cur_other_pct = (1 - cur_price) * 100
    
    entry_price_pct = entry_price * 100
    entry_other_pct = (1 - entry_price) * 100
    
    tp_pct = tp * 100
    tp_other_pct = (1 - tp) * 100
    
    edge_pp = (tp - cur_price) * 100

    # v5.2: 浮盈/亏 %, 用于显示给 Claude (盲评后阶段 C 才参考)
    pnl_pct = ((cur_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0

    # v5.2.1: 所有价格强制双向标注 YES | NO. cur_price/entry_price/tp 都是"持有方向"的价格.
    # 如持有 YES, 这些数字就是 YES 价; 如持有 NO, 这些数字就是 NO 价 (YES 价 = 1 - 它)
    def _to_yes_no(held_scalar):
        if side == "YES":
            return held_scalar * 100, (1 - held_scalar) * 100
        else:
            return (1 - held_scalar) * 100, held_scalar * 100
    cur_yes_pct, cur_no_pct = _to_yes_no(cur_price)
    entry_yes_pct, entry_no_pct = _to_yes_no(entry_price)
    q_yes_pct, q_no_pct = _to_yes_no(tp)

    # v7.0 反锚定: "大跌前价格中枢" 作盲评价格参照 (None=数据不足/没大跌 → 不显示)
    if pre_dump_center is not None:
        _c_yes, _c_no = _to_yes_no(float(pre_dump_center))
        pre_dump_center_line = (f"大跌前价格中枢 (盲评请以此为价格参照, 而非上面被压低的现价): "
                                f"{_c_yes:.1f}% YES | {_c_no:.1f}% NO")
    else:
        pre_dump_center_line = ""

    entry_date = (meta.get("created_at") or "")[:10]
    
    # 优先用反查的 question, 退化到 slug
    market_display = meta.get("_market_question") or meta.get("market_slug", "(未知)")
    # v5.2: Resolution 规则原文 (Gamma description)
    market_description = meta.get("_market_description") or "(Gamma API 未返回该字段, 请自行去 Polymarket 市场页面查看)"
    
    # 旧模板兼容字段
    confidence = meta.get("original_confidence") or "medium"
    if confidence == "high":
        exception_clause = "原始研究 confidence=high: 必须有新信息支持任何 q 的调整, 无例外."
    elif confidence == "medium":
        exception_clause = "原始研究 confidence=medium (或未标注): 允许 ≤3pp 下调 q 不需新信息 (元认知微调)."
    else:
        exception_clause = "原始研究 confidence=low: 允许 ≤5pp 下调 q 不需新信息 (元认知调整)."
    
    entry_reason = meta.get("entry_reason") or ""
    entry_reason_block = f"\n入场理由: {entry_reason}\n" if entry_reason else ""
    
    raw_est = meta.get("claude_raw_estimate")
    raw_estimate_line = f"\n你之前的原始估算 (raw, 未校准): {raw_est*100:.1f}%" if raw_est else ""
    
    return REEVAL_PROMPT.format(
        market_slug=market_display,
        market_description=market_description,
        side=side,
        other_side=other_side,
        entry_price_pct=entry_price_pct,
        entry_other_pct=entry_other_pct,
        entry_yes_pct=entry_yes_pct,
        entry_no_pct=entry_no_pct,
        entry_date=entry_date,
        cur_price_pct=cur_price_pct,
        cur_other_pct=cur_other_pct,
        cur_yes_pct=cur_yes_pct,
        cur_no_pct=cur_no_pct,
        tp_pct=tp_pct,
        tp_other_pct=tp_other_pct,
        q_yes_pct=q_yes_pct,
        q_no_pct=q_no_pct,
        edge_pp=edge_pp,
        pnl_pct=pnl_pct,
        days_to_resolution=days_to_resolution,
        pre_dump_center_line=pre_dump_center_line,
        confidence_label=(meta.get("original_confidence") or "(未标注, 默认 medium)"),
        exception_clause=exception_clause,
        entry_reason_block=entry_reason_block,
        raw_estimate_line=raw_estimate_line,
    )

