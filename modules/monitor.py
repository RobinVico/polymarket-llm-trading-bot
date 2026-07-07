"""
Position Monitor v5.6 — LLM 入场三档止损 + 两条止盈 + edge-based 决策
版本历史见 技术报告.md §十三. 早期 v5.1 改动 vs v5:
  - 删除急跌冻结整套机制 (FROZEN_FRESH / FROZEN / FROZEN_EXPIRED 状态 + 24h 冻结 + 解冻判断 + freeze_until / freeze_stop_price 读写).
    回测发现 "亏了会涨回来" 只有 10% 概率, 冻结机制基于幻觉.
  - 删除 _stop_price(entry) 入场分档表 + _drop_duration_minutes 急跌检测.
  - 删除 ABSOLUTE_FLOOR (40% 兜底), 因为 25% 更严, 兜底永远到不了.
  - 新增 STOP_LOSS_PCT = 0.25 — 单一规则: cur/entry < 0.75 (亏 ≥ 25%) → 自动全卖.
v5 改动 vs v4: 已归档, 见 past/v5/ARCHIVE.md.
其他逻辑 (TIME_STOP / TAKE_PROFIT_PRICE / TAKE_PROFIT_PNL / edge-based) 沿用.
"""
import time
import threading
import logging
from datetime import datetime, timezone
from modules.executor import Executor
from modules.db import (log_event, get_position_meta, update_monitor_state,
                        mark_executed_action, save_portfolio_snapshot,
                        clear_position_meta, get_all_meta_token_ids,
                        update_entry_price)

log = logging.getLogger("monitor")

# === 决策阈值 ===
HOLD_MIN_EDGE_PP = 2.0
SOFT_NEGATIVE_THRESHOLD_PP = -3.0

# === v5.1 自动止盈 (最高优先级, 触发即全卖) ===
TAKE_PROFIT_PRICE = 0.90     # 价 ≥ 90¢ → 自动全卖 (边际收益低 + 流动性差 + UMA 结算风险)
TAKE_PROFIT_PNL_PCT = 1.00   # 浮盈 ≥ +100% (翻倍) → 自动全卖 (锁定一倍利润)

# === v5.1 自动止损 (LLM 入场分类的三档, 触发即全卖) ===
# 每个新仓位入场时由 Claude 分类, 存入 meta.stop_loss_tier:
#   convergent  — 真相收敛型 (票房/营收/统计/汇率/比分): 从最高价回撤 -20%(≤3天12%)+确认
#   hybrid      — 混合型     (候选人选举, 有民调+政治):  入场 -35%
#   event_driven— 事件驱动型 (政治/外交/谈判):           入场 -60% (v7.4.3, 很松) + $0.05 地板兜底
# meta.stop_loss_tier 为 None (老仓/未分类) → v7.4.3 默认当 hybrid (不再走 -25% legacy; 前端也禁止选空白档).
STOP_LOSS_PCT_BY_TIER = {
    "convergent": 0.20,
    "hybrid": 0.35,
    "event_driven": 0.60,  # v7.4.3: 从"只地板"改成很松的 -60% %止损 (震荡大故设很松; 仍留 $0.05 地板兜底 + 砸穿走重评+护栏)
}
STOP_LOSS_PCT_LEGACY = 0.25  # (v7.4.3 已弃用: 未分类一律默认当 hybrid, 见 _evaluate_position / _maybe_trigger; 保留常量防意外引用)
EVENT_DRIVEN_FLOOR_PRICE = 0.05  # event_driven 类: 价 < $0.05 才触发地板止损

# === v7.0 出场机制重设计 ===
# 止盈分档 (基于剩余赔率/市场类型, 不再一刀切 0.90 全卖):
TAKE_PROFIT_PRICE_EVENT_DRIVEN = 0.92      # 事件型: best_bid ≥ 0.92 → 先卖一半留一半 (剩余空间小但论点没破可能续涨)
TAKE_PROFIT_HALF_FRACTION = 0.5            # "卖一半" 的比例
TAKE_PROFIT_HALF_PROTECT_DROP_PCT = 0.15   # v7.4.1: 事件型卖半后, best_bid 从 0.92 触发点相对跌 ≥15% (< 0.782) → 把留的后半也卖了锁利润 (防半仓坐过山车吐回利润)
TAKE_PROFIT_PRICE_CONVERGENT_NEAR = 0.88   # 收敛型临近结算: 0.88 提前全卖 (留滑点)
TAKE_PROFIT_CONVERGENT_NEAR_DAYS = 3
# 移动止损 (从持有期最高价回撤, 取代对入场价的固定 %; convergent + hybrid 都用):
TRAILING_STOP_PCT_CONVERGENT = 0.20        # 收敛型 >3 天: 从最高价回撤 ≥ 20% 触发
TRAILING_STOP_PCT_CONVERGENT_NEAR = 0.12   # 收敛型 ≤3 天: 收紧到 12%
TRAILING_STOP_PCT_HYBRID = 0.35            # v7.4.4: 混合型也改成移动止损 (从最高价回撤 ≥35%, 取代旧的入场锚 -35%)
TRAILING_CONFIRM_ROUNDS = 6                # 连续这么多心跳跌破才算 (≈3min@30s, 防一抖就卖飞)

# === TIME_STOP (沿用) ===
TIME_STOP_DAYS = 2
TIME_STOP_DRIFT_PP = 5.0

# === Legacy compat: 老 import 还在, 不影响 v5.1 决策 ===
DISASTER_DROP_PP = 25.0  # deprecated, 仅供老代码 import 兼容

# === 心跳 ===
CHECK_INTERVAL = 30
SNAPSHOT_INTERVAL = 1800
# v5.10: resolution 检查频率 — 每 N 轮跑一次. 120 × 30s = 3600s = 1 小时.
# Gamma 查询不阻塞主决策, 失败 log.warning 跳过. 改这里 (而不是改 sleep) 让心跳节奏不变.
# v6.0 (2026-06-18): 心跳 180s→30s; RESOLUTION_CHECK_ROUNDS 20→120 同步, 保持 resolution 仍 1 小时.
RESOLUTION_CHECK_ROUNDS = 120


class PositionMonitor:
    def __init__(self):
        self.executor = Executor.get()
        self.running = False
        # v5.7 (P13): seed _last_snapshot_ts from last persisted row so a process restart
        # doesn't trigger an immediate duplicate snapshot (which caused the 5-25 / 5-27 曲线 gap).
        try:
            from modules.db import get_conn
            conn = get_conn()
            row = conn.execute("SELECT MAX(ts) FROM portfolio_snapshot").fetchone()
            conn.close()
            self._last_snapshot_ts = int(row[0]) if row and row[0] else 0
        except Exception:
            self._last_snapshot_ts = 0
        self._orphan_seen = {}
        self._trail_breach = {}  # v7.0: token -> 连续跌破移动止损线的心跳数 (内存; 重启清零=只延迟不误触发)
        self._paper_trail_breach = {}  # v7.1: 模拟盘独立的移动止损确认计数 (不跟真仓串)

    def _days_to_settle(self, end_date_str):
        if not end_date_str:
            return None
        try:
            end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            delta = end_dt - datetime.now(timezone.utc)
            return delta.days + (1 if delta.seconds > 43200 else 0)
        except Exception:
            return None

    def _evaluate_position(self, pos, meta, breach_store=None):
        """
        v5.1 优先级 (第一个匹配胜出):
        0. NO_META
        1a. TAKE_PROFIT_PRICE (best_bid ≥ 90¢)        → 自动全卖
        1b. TAKE_PROFIT_PNL (浮盈 ≥ +100%, 用 best_bid)→ 自动全卖
        2.  STOP_LOSS (亏 ≥ 25%, 用 cur_price)        → 自动全卖
        3.  TIME_STOP (临结算 + 价漂移小, 用 cur)      → 自动全卖
        4.  Edge-based (HOLD / MARGINAL / SOFT / AT_TARGET, 用 cur)

        触发用价不对称设计:
        - 止盈 (take_profit): 用 best_bid (你真能卖到的价), 防 Dell 类虚高假触发
          (cur=$0.905 但 best_bid=$0.60 → 锁个假胜利, 实际卖到 60¢ = 真亏).
        - 止损 (stop_loss):   用 cur_price (polymarket 参考价), 防瞬时流动性蒸发误触发
          (cur=$0.20 但 bid 临时空只剩 $0.10 → 用 bid 触发会卖在最差成交价).
        - 同时也保持跟历史回测一致 (-25% 甜蜜区是基于 cur 类价算的).
        best_bid 拉失败 fallback 回 cur_price (避免 bot 卡死).
        """
        cur_price = pos["cur_price"]
        size = pos["size"]
        token_id = pos["asset"]

        if not meta:
            return ("NO_META", None)

        entry = meta["entry_price"]
        # avg_price (加权均价, 反映加仓后的实际成本) — 止盈/止损用 avg 而非 entry,
        # 否则用户加仓后阈值会失真 (entry 还是原始价, avg 已被加仓拉到不同位置).
        avg = pos.get("avg_price") or entry or 0
        q = meta.get("new_tp") if meta.get("new_tp") else meta["tp"]
        end_date = meta.get("end_date", "")
        executed = meta.get("executed_action") or ""
        tier = meta.get("stop_loss_tier") or "hybrid"  # v7.0 止盈分档要用. v7.4.3: 未分类默认当 hybrid (消灭"老仓-25%"未分类档)

        days_left = self._days_to_settle(end_date)

        # 拉 best_bid (止盈用). 失败 fallback 回 cur_price.
        best_bid = self.executor.get_best_bid(token_id)
        bid_price = best_bid if best_bid is not None and best_bid > 0 else cur_price

        # v5.10.1: 用户手动关止盈的仓位 (meta.disable_take_profit=1), 跳过 1a + 1b.
        # 止损 / TIME_STOP / edge-based 仍按规则跑.
        tp_disabled = bool(meta.get("disable_take_profit"))

        # === 1a. TAKE_PROFIT_PRICE (用 best_bid, 真能卖到才触发) — v7.0 分档 ===
        if not tp_disabled:
            # v7.4.2 事件型: "翻倍"先到就直接全卖 (不进 0.92 卖半) —— 低价入场(<$0.46)时 2×avg < 0.92, 翻倍价先触发。
            #   只在还没卖半时判 (卖半后留的那半让它跑, 由 0.782 保护, 不再被 +100% 秒卖)。放最前 → 价格跳空同时满足时翻倍优先。
            if (tier == "event_driven" and avg and avg > 0
                    and "tp_half_sold" not in executed and "take_profit_pnl_sold" not in executed
                    and (bid_price - avg) / avg >= TAKE_PROFIT_PNL_PCT):
                pnl_pct = (bid_price - avg) / avg * 100
                return ("TAKE_PROFIT_PNL", {
                    "action": "TAKE_PROFIT_PNL",
                    "reason": f"事件型翻倍先到: 浮盈+{pnl_pct:.0f}% (best_bid${bid_price:.3f}/avg${avg:.3f}) 在0.92前触发,直接全卖锁翻倍",
                    "sell_size": size,
                    "executed_action": "take_profit_pnl_sold",
                })
            # 事件型: ≥0.92 先卖一半留一半 (还没翻倍才走这; 只一次; 论点没破让另一半续跑)
            if (tier == "event_driven" and bid_price >= TAKE_PROFIT_PRICE_EVENT_DRIVEN
                    and "tp_half_sold" not in executed):
                half = int(size * TAKE_PROFIT_HALF_FRACTION * 100) / 100   # v7.1: 截断(非round)防 dust 把"半仓"算成全仓
                if half >= 0.01 and (size - half) >= 0.01:
                    return ("TAKE_PROFIT_HALF", {
                        "action": "TAKE_PROFIT_HALF",
                        "reason": f"事件型: best_bid${bid_price:.3f} ≥ {TAKE_PROFIT_PRICE_EVENT_DRIVEN:.2f},先卖一半({half})留一半跑",
                        "sell_size": half,
                        "executed_action": "tp_half_sold",
                        "partial": True,
                    })
            # v7.4.1 事件型半仓保护: 卖半后 best_bid 从 0.92 触发点相对跌 ≥15% (< 0.782) → 把留的后半也卖了锁利润
            #   (否则留的半仓只有 $0.05 地板 + -30% 重评兜底, 可能从 0.92 坐过山车吐回大半利润)
            if (tier == "event_driven" and "tp_half_sold" in executed
                    and "tp_half_protect_sold" not in executed
                    and bid_price < TAKE_PROFIT_PRICE_EVENT_DRIVEN * (1 - TAKE_PROFIT_HALF_PROTECT_DROP_PCT)):
                _floor = TAKE_PROFIT_PRICE_EVENT_DRIVEN * (1 - TAKE_PROFIT_HALF_PROTECT_DROP_PCT)
                return ("TAKE_PROFIT_HALF_PROTECT", {
                    "action": "TAKE_PROFIT_HALF_PROTECT",
                    "reason": f"事件型半仓保护: best_bid${bid_price:.3f} 从触发点{TAKE_PROFIT_PRICE_EVENT_DRIVEN:.2f}相对跌≥{int(TAKE_PROFIT_HALF_PROTECT_DROP_PCT*100)}% (<${_floor:.3f}),把留的后半也卖了锁利润",
                    "sell_size": size,
                    "executed_action": "tp_half_protect_sold",
                })
            # 收敛型临近结算 (≤3天): 0.88 提前全卖锁 (留滑点)
            if (tier == "convergent" and days_left is not None and days_left <= TAKE_PROFIT_CONVERGENT_NEAR_DAYS
                    and bid_price >= TAKE_PROFIT_PRICE_CONVERGENT_NEAR
                    and "take_profit_price_sold" not in executed):
                return ("TAKE_PROFIT_PRICE", {
                    "action": "TAKE_PROFIT_PRICE",
                    "reason": f"收敛型临近结算({days_left}天): best_bid${bid_price:.3f} ≥ {TAKE_PROFIT_PRICE_CONVERGENT_NEAR:.2f},提前全卖锁",
                    "sell_size": size,
                    "executed_action": "take_profit_price_sold",
                })
            # 其余 (收敛型>3天 / hybrid / legacy): 0.90 全卖。event_driven 不在此 —— 它只在 0.92 卖一半,
            # 另一半让它跑 (到结算/回撤重评/地板), 否则 0.90 这条会立刻把留下的半仓也卖掉, "留一半跑" 就废了。
            if (tier != "event_driven" and bid_price >= TAKE_PROFIT_PRICE and "take_profit_price_sold" not in executed):
                return ("TAKE_PROFIT_PRICE", {
                    "action": "TAKE_PROFIT_PRICE",
                    "reason": f"best_bid${bid_price:.3f} ≥ {TAKE_PROFIT_PRICE:.2f} (cur${cur_price:.3f}),自动全卖锁止盈",
                    "sell_size": size,
                    "executed_action": "take_profit_price_sold",
                })

        # === 1b. TAKE_PROFIT_PNL (用 best_bid 算真实浮盈) — 非事件型 ===
        # v7.4.2: 事件型的 +100% 已在 1a 顶部单独处理 (翻倍先到→全卖 / 卖半后留的那半让它跑, 不被 +100% 秒卖), 这里只管非事件型。
        if (not tp_disabled
                and tier != "event_driven"
                and avg and avg > 0
                and (bid_price - avg) / avg >= TAKE_PROFIT_PNL_PCT
                and "take_profit_pnl_sold" not in executed):
            pnl_pct = (bid_price - avg) / avg * 100
            return ("TAKE_PROFIT_PNL", {
                "action": "TAKE_PROFIT_PNL",
                "reason": f"浮盈+{pnl_pct:.0f}% (best_bid${bid_price:.3f}/avg${avg:.3f} cur${cur_price:.3f}),自动全卖锁翻倍",
                "sell_size": size,
                "executed_action": "take_profit_pnl_sold",
            })

        # === 2. STOP_LOSS ===
        # v5.15: %止损不再盲卖 — 到线(含大跳直接砸穿)先交给自动重评决定: 进 PENDING_REEVAL, 不卖,
        #        loop 的 else 会触发重评(或 latch 跳过), 等重评结果(离线自动执行/在线手动)再动。
        #        只有 $0.05 地板兜底 / 重评未启用 时才硬卖。event_driven / 已取消止损 = 只地板。
        _autostop_off = bool(meta.get("autostop_disabled"))
        if avg and avg > 0 and "stop_loss_sold" not in executed:
            tier_pct = STOP_LOSS_PCT_BY_TIER.get(tier, STOP_LOSS_PCT_LEGACY) if tier else STOP_LOSS_PCT_LEGACY
            tier_label = tier or "legacy(-25%)"
            # (a) $0.05 地板: 任何情况都兜底全卖 (防真归零; 含 event_driven / 已取消止损 / 等重评中)
            if cur_price < EVENT_DRIVEN_FLOOR_PRICE:
                _lbl = "已取消止损" if _autostop_off else ("事件驱动型" if tier == "event_driven" else tier_label)
                return ("STOP_LOSS", {
                    "action": "STOP_LOSS",
                    "reason": f"{_lbl}: 价${cur_price:.3f} 跌破地板${EVENT_DRIVEN_FLOOR_PRICE:.2f},自动全卖兜底",
                    "sell_size": size,
                    "executed_action": "stop_loss_sold",
                })
            # (b) 止损线触发判定 — convergent + hybrid 从最高价回撤+确认 (移动止损); event_driven -60% 入场锚
            #     (v7.4.4: 混合型也改成移动止损, 跟收敛型同形式, 回撤35%); 只有"已取消止损"才没有 %止损 (只地板)。
            _breached = False
            breach = breach_store if breach_store is not None else self._trail_breach  # v7.1: paper 用独立 store, 不串真仓
            if not _autostop_off:
                if tier in ("convergent", "hybrid"):
                    # 移动止损: 从持有期最高价回撤 (convergent 20%/≤3天12%; hybrid 35%) + 连拍确认
                    peak = meta.get("peak_price") or avg or cur_price
                    if tier == "convergent":
                        trail_pct = (TRAILING_STOP_PCT_CONVERGENT_NEAR
                                     if (days_left is not None and days_left <= TAKE_PROFIT_CONVERGENT_NEAR_DAYS)
                                     else TRAILING_STOP_PCT_CONVERGENT)
                    else:  # hybrid (v7.4.4)
                        trail_pct = TRAILING_STOP_PCT_HYBRID
                    if peak and peak > 0 and (peak - cur_price) / peak >= trail_pct:
                        n = breach.get(token_id, 0) + 1   # 确认: 连续 N 拍跌破才算 (防一抖卖飞)
                        breach[token_id] = n
                        _breached = n >= TRAILING_CONFIRM_ROUNDS
                    else:
                        breach.pop(token_id, None)        # 恢复 → 清零确认计数
                elif tier_pct is not None and (avg - cur_price) / avg >= tier_pct:
                    _breached = True                                  # event_driven -60%: 入场锚
            if _breached:
                try:
                    from modules import auto_reeval as _ar
                    _reeval_on = _ar.is_configured()  # v6.0.3: 用 is_configured (忽略紧急暂停) → 暂停期间也别盲卖, 冻结在 PENDING_REEVAL
                except Exception:
                    _reeval_on = False
                if _reeval_on:
                    # 不盲卖 → 进 pending, 交给重评决定 (大跳也先进这个状态; _maybe_trigger 见 PENDING_REEVAL 会放行)
                    return ("PENDING_REEVAL", None)
                # 重评未启用 → 退回硬止损 (安全网)
                if tier in ("convergent", "hybrid"):
                    peak = meta.get("peak_price") or avg or cur_price
                    dd = (peak - cur_price) / peak * 100 if peak else 0
                    return ("STOP_LOSS", {
                        "action": "STOP_LOSS",
                        "reason": f"[{tier} 移动止损] 从最高${peak:.3f}回撤{dd:.0f}% (cur${cur_price:.3f}),自动全卖 (重评未启用)",
                        "sell_size": size,
                        "executed_action": "stop_loss_sold",
                    })
                loss_pct = (avg - cur_price) / avg * 100
                return ("STOP_LOSS", {
                    "action": "STOP_LOSS",
                    "reason": f"[{tier_label}] 亏-{loss_pct:.0f}% (阈值-{int(tier_pct*100)}%, cur${cur_price:.3f}/avg${avg:.3f}),自动全卖止损 (重评未启用)",
                    "sell_size": size,
                    "executed_action": "stop_loss_sold",
                })

        # === 3. TIME_STOP ===
        if (days_left is not None and days_left <= TIME_STOP_DAYS
                and abs(cur_price - entry) * 100 < TIME_STOP_DRIFT_PP
                and "time_stop_sold" not in executed):
            return ("TIME_STOP", {
                "action": "TIME_STOP",
                "reason": f"距结算{days_left}天+价格仅动{abs(cur_price-entry)*100:.1f}pp,自动平仓",
                "sell_size": size,
                "executed_action": "time_stop_sold",
            })

        # === 4. Edge-based 决策 ===
        edge_pp = (q - cur_price) * 100
        if edge_pp > HOLD_MIN_EDGE_PP:
            return ("HOLD", None)
        elif edge_pp >= SOFT_NEGATIVE_THRESHOLD_PP:
            return ("MARGINAL", None)
        else:
            user_changed_q = bool(meta.get("last_reeval_at"))
            if not user_changed_q:
                return ("AT_TARGET", None)
            else:
                return ("SOFT_NEGATIVE", None)

    def check_once(self):
        positions = self.executor.get_positions()   # v7.1: 同时刷新 Executor._live_positions 缓存
        try:
            self.executor.get_cash_balance()         # v7.1 提速: 每心跳暖一次现金缓存, 让 /api/snapshot 等只读接口能用 get_cash_cached
        except Exception:
            pass
        # === portfolio_snapshot: 节流到 30 分钟一次 ===
        now_ts = int(time.time())
        cash = None  # 提前定义, 后面 meta sweep 用同一个守卫判 API 是否失败
        if now_ts - self._last_snapshot_ts >= SNAPSHOT_INTERVAL:
            try:
                total_value = sum((p.get("cur_price") or 0) * (p.get("size") or 0) for p in (positions or []))
                total_cost = sum((p.get("avg_price") or 0) * (p.get("size") or 0) for p in (positions or []))
                cash = self.executor.get_cash_balance()
                # v5.1 强化守卫: 三道
                #  (a) cash 失败 (None) 直接跳过, 不允许 assets_total 漏算 cash.
                #  (b) positions 空 + cash 真为 0 跳过 (理论上 "钱真全花光", 极罕见且可疑).
                #  (c) positions 空 但 cash > 0, 检查 db 是否有 meta 行 — 有就说明
                #      历史上账户非空, positions=[] 几乎肯定是 API 失败, 跳过避免 total_value
                #      被错记为 0 污染曲线.
                if cash is None:
                    log.warning("skip portfolio_snapshot: cash API failed (would corrupt assets_total)")
                elif not positions and cash == 0:
                    log.warning("skip portfolio_snapshot: positions=[] AND cash=0 (likely API failure)")
                elif not positions and get_all_meta_token_ids():
                    log.warning("skip portfolio_snapshot: positions=[] but db has meta (positions API likely failed)")
                else:
                    total_pnl = total_value - total_cost
                    save_portfolio_snapshot(now_ts, total_value, total_cost, cash, total_pnl, total_value + cash)
                    self._last_snapshot_ts = now_ts
            except Exception as e:
                log.warning(f"portfolio_snapshot save failed: {e}")

        # === meta sweep: 清理已平仓的孤儿 meta 行 (二次入场根治) ===
        # 守卫 1 (2026-05-24): 只在 polymarket 真返回非空 positions 时才 sweep.
        # 守卫 2 (2026-05-27): 同一 orphan 必须连续 ORPHAN_CONFIRM_RUNS 轮都被观察到才真删.
        # 历史教训 1 (5-24 00:41): data-api 单边故障返 0 positions, 全清 7 个 meta.
        # 历史教训 2 (5-27 12:39): data-api 部分故障 — 0 → 4 → 6 渐恢复, 在 4 那一档 sweep
        # 误清 2 个 (us-iran-peace / israel-lebanon). 守卫 1 挡不住部分缺失.
        # 新策略: orphan 每被观察到一次计数 +1, 连续 18 轮 (~9 min) 都缺才真删; 中间一旦
        # 恢复出现就清零计数. 副作用: 用户真平仓后 db 残留 meta 多撑 ~9 分钟, 无害.
        # v6.0 (2026-06-18): 心跳 30s 后由 3→18 轮, 保持 ~9 分钟确认窗口不变 (护 5-27 灾难教训).
        ORPHAN_CONFIRM_RUNS = 18
        if positions:
            try:
                current_token_ids = {p["asset"] for p in positions if p.get("asset")}
                meta_token_ids = get_all_meta_token_ids()
                orphans_now = meta_token_ids - current_token_ids
                # 重置不再是 orphan 的计数 (说明 data-api 又看到了)
                for tid in list(self._orphan_seen.keys()):
                    if tid not in orphans_now:
                        del self._orphan_seen[tid]
                for orphan_id in orphans_now:
                    self._orphan_seen[orphan_id] = self._orphan_seen.get(orphan_id, 0) + 1
                    seen = self._orphan_seen[orphan_id]
                    if seen >= ORPHAN_CONFIRM_RUNS:
                        deleted = clear_position_meta(orphan_id)
                        if deleted:
                            log.info(f"meta sweep: 清理孤儿 meta {orphan_id[:20]}... (连续 {seen} 轮确认)")
                            del self._orphan_seen[orphan_id]
                    else:
                        log.info(f"meta sweep: 孤儿 {orphan_id[:20]}... 第 {seen}/{ORPHAN_CONFIRM_RUNS} 轮观察, 暂不清理")
            except Exception as e:
                log.warning(f"meta sweep failed: {e}")

        if not positions:
            return []
        results = []
        for pos in positions:
            title = pos["title"]
            token_id = pos["asset"]
            if not token_id:
                continue
            meta = get_position_meta(token_id)
            # Self-heal: db.entry_price=0 且 polymarket avg>0 → 用 avg 回写 db.
            # 防御 record_position 在 entry 兜底前已写入的污染行.
            if meta and (meta.get("entry_price") or 0) <= 0:
                live_avg = float(pos.get("avg_price") or 0)
                if live_avg > 0:
                    update_entry_price(token_id, live_avg)
                    meta["entry_price"] = live_avg
                    log.info(f"healed entry_price=0 in db: {title[:40]} → ${live_avg:.4f}")
            # v7.0: 每心跳更新持有期最高价 (收敛型移动止损用; 写 DB + 改内存 meta, 仿上面 entry 自愈)
            try:
                _cur = pos.get("cur_price") or 0
                if meta and _cur > 0 and _cur > (meta.get("peak_price") or 0):
                    from modules.db import update_peak_price
                    update_peak_price(token_id, _cur)
                    meta["peak_price"] = _cur
            except Exception as _e:
                log.warning(f"peak_price update err: {_e}")
            state, action = self._evaluate_position(pos, meta)

            update_monitor_state(token_id, state)

            if action:
                log.info(f"→ AUTO_SELL [{state}] {title[:40]} size={action['sell_size']} | {action['reason']}")
                # v5.7 (P7): capture exit context BEFORE sell so closed_positions row uses correct prices.
                _avg_for_close = pos.get("avg_price") or meta.get("entry_price") or 0
                _exit_price = pos.get("cur_price") or 0
                _side_for_close = pos.get("outcome") or meta.get("side") or ""
                ok = self.executor.sell(token_id, action["sell_size"], action["reason"])
                if ok:
                    mark_executed_action(token_id, action["executed_action"])
                    log_event("auto_sell", title, f"{action['action']} size={action['sell_size']} {action['reason']}")
                    # v5.7 (P7): persist closed_positions row before meta is cleared.
                    try:
                        from modules.db import save_closed_position
                        save_closed_position(
                            token_id=token_id,
                            market_slug=meta.get("market_slug") or title,
                            side=_side_for_close,
                            avg_entry=_avg_for_close,
                            exit_price=_exit_price,
                            size=action["sell_size"],
                            exit_reason=action["action"],
                            stop_loss_tier=meta.get("stop_loss_tier"),
                            claude_raw_estimate=meta.get("claude_raw_estimate") or meta.get("tp"),
                            entry_at=meta.get("created_at"),
                            cluster_id=meta.get("cluster_id"),   # v5.10
                            tag=meta.get("tag"),                  # v5.10
                        )
                    except Exception as e:
                        log.warning(f"save_closed_position failed (sell still succeeded): {e}")
                    # v7.0: 分批卖一半 (partial) 绝不清 meta — 余量继续被管理 (size 下一拍由 get_positions 自动减半)
                    if action.get("partial"):
                        log.info(f"partial 卖出成功, 保留 meta 继续管理余量: {title[:40]}")
                    else:
                        # 全量卖成功后立即清理 meta (跟二次入场逻辑一致, 让下次重新买入是白纸)
                        clear_position_meta(token_id)
                        self._trail_breach.pop(token_id, None)   # 清移动止损确认计数 (防同 token 再入场误判)
                        log.info(f"auto_sell 后清理 meta: {title[:40]}")
                    action["success"] = True
                else:
                    action["success"] = False
                    log.warning(f"自动sell失败: {title[:40]}")
                results.append({**action, "title": title, "monitor_state": state})
            else:
                # v5.13: 没有自动卖动作 → 检查大跌自动重评触发 (亏>阈值 → 后台联网调研 → 挂 dashboard 等确认)
                try:
                    self._maybe_trigger_auto_reeval(pos, meta, state)
                except Exception as _e:
                    log.warning(f"auto_reeval trigger err (主决策不受影响): {_e}")
        return results

    def _maybe_trigger_auto_reeval(self, pos, meta, state):
        """v5.13: 亏损超阈值 → 后台线程联网调研 → 出建议挂 dashboard 等用户确认。不自动卖。"""
        # v5.16: 用户手动关了该仓全部自动止损 (止损OFF / cancel_autostop) → 不触发亏损自动重评
        # (重评-on-loss 是 v5.15 止损机制的现代形态, 属于"止损"范畴). 只剩 $0.05 地板兜底
        # (在 _evaluate_position 里, 不经此路径), 防真归零. 让 止损OFF 真正"所有止损全部失效"。
        if (meta or {}).get("autostop_disabled"):
            return
        from modules import auto_reeval
        if not auto_reeval.is_enabled():
            return
        avg = float(pos.get("avg_price") or (meta or {}).get("entry_price") or 0)
        cur = float(pos.get("cur_price") or 0)
        if avg <= 0 or cur <= 0:
            return
        loss_pct = (avg - cur) / avg
        # v5.14 (清单#1a): 分档触发, 各档止损线前 5pp; event_driven 固定 -30%(无硬止损)
        tier = (meta or {}).get("stop_loss_tier") or "hybrid"   # v7.4.3: 未分类默认当 hybrid (不再走 legacy)
        if tier == "event_driven":
            trig_thr = 0.30
        elif tier in STOP_LOSS_PCT_BY_TIER and STOP_LOSS_PCT_BY_TIER.get(tier):
            trig_thr = STOP_LOSS_PCT_BY_TIER[tier] - 0.05   # convergent→-15%, hybrid→-30%
        else:
            trig_thr = STOP_LOSS_PCT_LEGACY - 0.05          # legacy/未分类→-20%
        # v7.0: state==PENDING_REEVAL = _evaluate_position 已判定"该交给重评"(含 convergent 移动止损确认),
        #       直接放行(只受下面节流约束); 其它无动作状态仍需 entry 锚 loss ≥ trig_thr 才早触发。
        if state != "PENDING_REEVAL" and loss_pct < trig_thr:
            return
        token_id = pos.get("asset")
        if not token_id:
            return
        from modules.db import (has_inflight_auto_reeval, save_auto_reeval_pending, recent_auto_reeval_exists,
                                last_auto_reeval_loss, get_reeval_watch_loss, set_reeval_watch_loss)
        # 进行中(调研中/待确认/手动卡) → 锁住, 不重复起 API
        if has_inflight_auto_reeval(token_id):
            return
        # v6.0.4 (用户定 2026-06-19): 同一仓位再评的节流 = "过6h → 重设基线 → 从基线再多亏≥10pp 才触发"。
        # (取代旧的"过6h 且 比上次评时已多亏5pp 就立刻触发" —— 那个时间一到就放炮。)
        # 流程: 冷却(6h)内不评且清基线; 6h 一过的那一拍把"当前亏损"记成新基线(本拍不触发);
        #       之后只有从该基线又多亏 ≥ RETRIGGER_DROP_PCT(默认10pp) 才再触发; 每次真触发重置基线+冷却。
        _last_loss = last_auto_reeval_loss(token_id)
        if _last_loss is not None:                                  # 该仓评过 → 走节流
            if recent_auto_reeval_exists(token_id, auto_reeval.COOLDOWN_HOURS):
                set_reeval_watch_loss(token_id, None)              # 冷却中: 清基线(等6h后重记), 不触发
                return
            _watch = get_reeval_watch_loss(token_id)
            if _watch is None:
                set_reeval_watch_loss(token_id, loss_pct)         # 6h 刚过这一拍: 记下基线, 本拍不触发
                return
            if loss_pct < _watch + auto_reeval.RETRIGGER_DROP_PCT:
                return                                            # 没从6h基线再多亏≥10pp → 不触发
            set_reeval_watch_loss(token_id, None)                 # 达标 → 触发(清基线, 进新一轮)
        # _last_loss is None = 从没评过该仓 → 首次, 直接按 tier 阈值触发 (走下面 online/offline)
        # v5.14: 用户在线时暂停自动 API, 改记一条 manual 让用户手动复评 (省钱)
        online = False
        try:
            from modules.db import get_presence
            online = get_presence().get("effective_online", False)
        except Exception:
            online = False
        if online:
            sug_id = save_auto_reeval_pending(token_id, pos, meta, loss_pct, cur, avg, status="manual")
            log.info(f"→ AUTO_REEVAL [在线·手动] {pos.get('title','')[:40]} 亏-{loss_pct*100:.0f}% "
                     f"(id={sug_id}); 自动 API 暂停, 等你手动复评")
            return
        sug_id = save_auto_reeval_pending(token_id, pos, meta, loss_pct, cur, avg)
        log.info(f"→ AUTO_REEVAL 触发 [{state}] {pos.get('title','')[:40]} 亏-{loss_pct*100:.0f}% "
                 f"(id={sug_id}); 后台联网调研中…")
        threading.Thread(target=auto_reeval.run_and_store, args=(sug_id, pos, meta), daemon=True).start()

    def _escalate_stale_manual_reevals(self):
        """v6.0.5: 把 manual 重评卡升级成自动跑 API。两种触发:
          - 离线: 立即接管所有 manual 卡 (offline = 自动模式), 跑完直接自动执行(动真钱)。
            防"在线触发 manual → 没点清空就转离线"导致该仓既不自动执行、也不再评、止损还一直关着 (v6.0.1 #3)。
          - 在线: manual 卡闪烁超过 MANUAL_ESCALATE_MIN 分钟(默认2)还没人确认 → 自动调 API,
            但结果仍挂 pending 等你确认卖, 绝不在线自动卖 (run_and_store 在线分支会 leave pending)。
        两条路都调 run_and_store(force_manual=False): 离线→_auto_execute, 在线→留 pending。"""
        from modules import auto_reeval
        if not auto_reeval.is_enabled():
            return
        from modules.db import (get_presence, get_pending_auto_reeval, set_auto_reeval_status,
                                get_position_meta, _parse_iso_to_aware)
        manual_rows = [r for r in get_pending_auto_reeval() if r.get("status") == "manual"]
        if not manual_rows:
            return
        online = get_presence().get("effective_online", False)
        if online:
            # v7.x (用户 2026-06-25 拍板): 在线 **完全不自动补 API** — 手动卡留着等你手动处理, 纯手动省钱.
            # (原 v6.0.5 是超时 MANUAL_ESCALATE_MIN=2 分钟自动调 API, 但 2 分钟太短, 抢在手动评完前白花钱.)
            # 离线路径不受影响: 一离线照样自动接管所有 manual 卡 (下面 offline 分支自动执行)。
            return
        pos_by_tok = {p.get("asset"): p for p in (self.executor.get_positions() or [])}
        for r in manual_rows:
            tok, sid = r.get("token_id"), r.get("id")
            pos = pos_by_tok.get(tok)
            if not pos:
                set_auto_reeval_status(sid, "cleared")  # 仓位已不在 → 归档
                continue
            meta = get_position_meta(tok) or {}
            set_auto_reeval_status(sid, "analyzing")  # 占位, 防下一轮重复接管
            _how = "在线闪烁超时自动调API(留pending等确认)" if online else "离线接管(自动执行)"
            log.info(f"→ AUTO_REEVAL {_how} (id={sid}) {pos.get('title','')[:40]}: manual → 自动跑")
            threading.Thread(target=auto_reeval.run_and_store, args=(sid, pos, meta), daemon=True).start()

    def _evaluate_paper_positions(self):
        """v7.1: 模拟盘 — 对每个 open 测试仓实时盯盘, 跑跟真仓**一模一样**的 _evaluate_position (dry-run),
        更新 实时价/最高价/算法状态 + 记录'本会卖'首次快照 + 检测结算。
        ⚠️ 绝不调用 executor.sell/buy —— 只读行情、只算、只写 paper_positions 表。"""
        try:
            from modules.db import (get_open_paper_positions, update_paper_tracking,
                                    set_paper_would_sell, resolve_paper_position)
        except Exception:
            return
        papers = get_open_paper_positions()
        if not papers:
            return
        import json as _json
        for p in papers:
            try:
                tok = p.get("token_id")
                if not tok:
                    continue
                side = (p.get("side") or "").strip()
                entry = float(p.get("entry_price") or 0)
                shares = float(p.get("shares") or 0)
                # --- 实时价 (Gamma outcomePrices, 取持有 side 的 token 价) + 结算检测 —— 走 DoH guard 防 DNS 污染 ---
                def _px_from(mkt):
                    tks = mkt.get("clobTokenIds"); pcs = mkt.get("outcomePrices")
                    tks = _json.loads(tks) if isinstance(tks, str) else (tks or [])
                    pcs = _json.loads(pcs) if isinstance(pcs, str) else (pcs or [])
                    return float(pcs[tks.index(tok)]) if (tok in tks and len(pcs) > tks.index(tok)) else None
                cur = None; closed = False
                try:
                    from modules.gamma_client import gamma_get
                    gr = gamma_get("/markets", {"clob_token_ids": tok, "limit": 1})
                    if gr and isinstance(gr, list) and gr:
                        cur = _px_from(gr[0]); closed = bool(gr[0].get("closed"))
                    if cur is None:  # 普通查询查不到 → Gamma 默认过滤已结算市场; 带 closed=true 再查一次
                        gr2 = gamma_get("/markets", {"clob_token_ids": tok, "closed": "true", "limit": 1})
                        if gr2 and isinstance(gr2, list) and gr2:
                            cur = _px_from(gr2[0]); closed = True
                except Exception as e:
                    log.warning(f"paper price fetch err id={p.get('id')}: {e}")
                if cur is None:
                    continue
                old_peak = float(p.get("peak_price") or entry or cur)
                peak = max(old_peak, cur)
                # --- 结算: closed=true 或 价格收敛到 0/1 (软结算) → resolve (持有 side 最终兑现概率) ---
                if closed or cur >= 0.99 or cur <= 0.01:
                    fo = 1.0 if cur >= 0.5 else 0.0
                    resolve_paper_position(p["id"], fo)
                    log.info(f"paper id={p['id']} 已结算: 持有 {side} {'赢' if fo>=0.5 else '输'} (cur=${cur:.3f})")
                    continue
                # --- 跑同一套算法 (dry-run, paper 独立 breach store, executed_action='' 让规则始终评估) ---
                spos = {"asset": tok, "cur_price": cur, "size": shares, "avg_price": entry,
                        "outcome": side, "title": p.get("title") or p.get("market_slug") or ""}
                smeta = {"entry_price": entry, "tp": p.get("q") or entry, "new_tp": p.get("q"),
                         "end_date": p.get("end_date") or "", "stop_loss_tier": p.get("stop_loss_tier"),
                         "peak_price": peak, "executed_action": "", "autostop_disabled": 0,
                         "disable_take_profit": 0, "market_slug": p.get("market_slug"),
                         "created_at": p.get("created_at")}
                state, action = self._evaluate_position(spos, smeta, breach_store=self._paper_trail_breach)
                update_paper_tracking(p["id"], cur, peak, state)
                # --- 首次'本会卖'快照 (只记一次; PENDING_REEVAL 是 action=None 不算卖, 继续盯到结算) ---
                if action and not p.get("would_sell_at_ts"):
                    act = action.get("action", "")
                    wprice = cur
                    if act in ("TAKE_PROFIT_PRICE", "TAKE_PROFIT_PNL", "TAKE_PROFIT_HALF"):
                        try:
                            bb = self.executor.get_best_bid(tok)
                            if bb and bb > 0:
                                wprice = float(bb)
                        except Exception:
                            pass
                    sim_shares = shares * TAKE_PROFIT_HALF_FRACTION if act == "TAKE_PROFIT_HALF" else shares
                    sim_pnl = (wprice - entry) * sim_shares   # v7.1: 卖一半的模拟盈亏只算半仓
                    set_paper_would_sell(p["id"], wprice, action.get("reason", act), sim_pnl)
                    log.info(f"paper id={p['id']} 模拟触发 [{act}] @ ${wprice:.3f} 模拟盈亏 ${sim_pnl:+.2f} (继续盯到结算)")
            except Exception as e:
                log.warning(f"paper eval err id={p.get('id')}: {e}")

    def run_loop(self):
        self.running = True
        self._round_count = 0  # v5.10: 用来 schedule resolution_check 每 RESOLUTION_CHECK_ROUNDS 轮跑一次
        log.info(f"Monitor v7.1 started (每{CHECK_INTERVAL}s)")
        log.info(f"v7.0 自动卖规则 (优先级从高到低):")
        log.info(f"  1a. 价格止盈 (分档): 事件型 best_bid ≥ {TAKE_PROFIT_PRICE_EVENT_DRIVEN:.2f} 卖一半 / "
                 f"收敛型≤{TAKE_PROFIT_CONVERGENT_NEAR_DAYS}天 ≥ {TAKE_PROFIT_PRICE_CONVERGENT_NEAR:.2f} 全卖 / 其余 ≥ {TAKE_PROFIT_PRICE:.2f} 全卖")
        log.info(f"  1b. 浮盈翻倍:    (best_bid-avg)/avg ≥ +{TAKE_PROFIT_PNL_PCT*100:.0f}% → 自动全卖")
        log.info(f"  2.  止损 (LLM 入场分级):")
        log.info(f"      convergent   (真相收敛型) → v7.0 移动止损: 从最高价回撤 ≥{int(TRAILING_STOP_PCT_CONVERGENT*100)}% (≤3天 {int(TRAILING_STOP_PCT_CONVERGENT_NEAR*100)}%) + 连{TRAILING_CONFIRM_ROUNDS}拍确认")
        log.info(f"      hybrid       (混合型)     → 入场锚 -{int(STOP_LOSS_PCT_BY_TIER['hybrid']*100)}%")
        log.info(f"      event_driven (事件驱动型) → 不止损, 价 < ${EVENT_DRIVEN_FLOOR_PRICE:.2f} 地板兜底")
        log.info(f"      (未分类老仓位 fallback 入场锚 -{int(STOP_LOSS_PCT_LEGACY*100)}%)")
        log.info(f"  2b. v5.15: 止损线砸穿(含大跳/收敛回撤确认) → 不盲卖, 进 PENDING_REEVAL → 触发自动重评等结果")
        log.info(f"      (重评未启用才退回硬卖; ${EVENT_DRIVEN_FLOOR_PRICE:.2f} 地板永远兜底)")
        log.info(f"  3.  TIME_STOP:   距结算 ≤{TIME_STOP_DAYS}天 + 漂移<{TIME_STOP_DRIFT_PP}pp → 自动全卖")
        log.info(f"决策模式 (edge-based, 等用户确认):")
        log.info(f"  HOLD: edge > +{HOLD_MIN_EDGE_PP}pp")
        log.info(f"  MARGINAL: -{abs(SOFT_NEGATIVE_THRESHOLD_PP)} ≤ edge ≤ +{HOLD_MIN_EDGE_PP}pp")
        log.info(f"  SOFT_NEGATIVE: edge < {SOFT_NEGATIVE_THRESHOLD_PP}pp (重评过)")
        log.info(f"  AT_TARGET: edge < {SOFT_NEGATIVE_THRESHOLD_PP}pp (未重评)")
        while self.running:
            # v6.0.1 (#2 修): 复位卡死的 analyzing 重评 (进程重启/死线程), 否则 has_inflight 永久闩住该仓止损+再评
            try:
                from modules.db import expire_stale_auto_reeval
                _ns = expire_stale_auto_reeval(30)
                if _ns:
                    log.warning(f"复位 {_ns} 条卡死的 analyzing 自动重评 (进程重启/线程中断)")
            except Exception as _e:
                log.warning(f"expire_stale_auto_reeval err: {_e}")
            # v7.x (#3): 自动重评建议放超过 48h 自动清空归档 (数据保留, 不卖不动钱)
            try:
                from modules.db import autoclear_old_auto_reeval
                _nc = autoclear_old_auto_reeval(48)
                if _nc:
                    log.info(f"自动清空 {_nc} 条超过 48h 的重评建议 (归档, 数据保留)")
            except Exception as _e:
                log.warning(f"autoclear_old_auto_reeval err: {_e}")
            try:
                self.check_once()
            except Exception as e:
                log.exception(f"Monitor error: {e}")
            # v6.0.5: 离线立即接管 / 在线 manual 卡闪烁超时(默认2分钟)自动调API的 manual 重评卡
            try:
                self._escalate_stale_manual_reevals()
            except Exception as _e:
                log.warning(f"escalate manual reeval err: {_e}")
            # v7.1: 模拟盘/测试仓 dry-run 盯盘 (绝不真下单)
            try:
                self._evaluate_paper_positions()
            except Exception as _e:
                log.warning(f"paper eval err: {_e}")
            # v5.10: 每 RESOLUTION_CHECK_ROUNDS 轮跑一次 resolution_check (默认 1 小时一次).
            # 失败不阻塞主决策, 异常 log.warning 跳过, 下一周期重试.
            self._round_count += 1
            if self._round_count % RESOLUTION_CHECK_ROUNDS == 0:
                try:
                    from modules.resolution_check import update_unresolved_closed_positions
                    checked, updated = update_unresolved_closed_positions(limit=100)
                    if checked > 0:
                        log.info(f"resolution_check: checked={checked} updated={updated}")
                except Exception as e:
                    log.warning(f"resolution_check failed (主决策不受影响): {e}")
            time.sleep(CHECK_INTERVAL)

    def stop(self):
        self.running = False
