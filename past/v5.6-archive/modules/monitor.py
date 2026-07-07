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
#   convergent  — 真相收敛型 (票房/营收/统计/汇率/比分): -20% 止损
#   hybrid      — 混合型     (候选人选举, 有民调+政治):  -35% 止损
#   event_driven— 事件驱动型 (政治/外交/谈判):           不止损, 只用 $0.05 地板价兜底
# meta.stop_loss_tier 为 None (老仓位 / 未分类) → fallback 用 -25% (兼容 v5.1 早期).
STOP_LOSS_PCT_BY_TIER = {
    "convergent": 0.20,
    "hybrid": 0.35,
    "event_driven": None,  # 不按百分比, 用地板价
}
STOP_LOSS_PCT_LEGACY = 0.25  # 老仓位 / tier 未填的 fallback
EVENT_DRIVEN_FLOOR_PRICE = 0.05  # event_driven 类: 价 < $0.05 才触发地板止损

# === TIME_STOP (沿用) ===
TIME_STOP_DAYS = 2
TIME_STOP_DRIFT_PP = 5.0

# === Legacy compat: 老 import 还在, 不影响 v5.1 决策 ===
DISASTER_DROP_PP = 25.0  # deprecated, 仅供老代码 import 兼容

# === 心跳 ===
CHECK_INTERVAL = 180
SNAPSHOT_INTERVAL = 1800


class PositionMonitor:
    def __init__(self):
        self.executor = Executor.get()
        self.running = False
        self._last_snapshot_ts = 0
        self._orphan_seen = {}

    def _days_to_settle(self, end_date_str):
        if not end_date_str:
            return None
        try:
            end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            delta = end_dt - datetime.now(timezone.utc)
            return delta.days + (1 if delta.seconds > 43200 else 0)
        except Exception:
            return None

    def _evaluate_position(self, pos, meta):
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

        days_left = self._days_to_settle(end_date)

        # 拉 best_bid (止盈用). 失败 fallback 回 cur_price.
        best_bid = self.executor.get_best_bid(token_id)
        bid_price = best_bid if best_bid is not None and best_bid > 0 else cur_price

        # === 1a. TAKE_PROFIT_PRICE (用 best_bid, 真能卖到 90¢ 才触发) ===
        if (bid_price >= TAKE_PROFIT_PRICE
                and "take_profit_price_sold" not in executed):
            return ("TAKE_PROFIT_PRICE", {
                "action": "TAKE_PROFIT_PRICE",
                "reason": f"best_bid${bid_price:.3f} ≥ {TAKE_PROFIT_PRICE:.2f} (cur${cur_price:.3f}),自动全卖锁止盈",
                "sell_size": size,
                "executed_action": "take_profit_price_sold",
            })

        # === 1b. TAKE_PROFIT_PNL (用 best_bid 算真实浮盈) ===
        if (avg and avg > 0
                and (bid_price - avg) / avg >= TAKE_PROFIT_PNL_PCT
                and "take_profit_pnl_sold" not in executed):
            pnl_pct = (bid_price - avg) / avg * 100
            return ("TAKE_PROFIT_PNL", {
                "action": "TAKE_PROFIT_PNL",
                "reason": f"浮盈+{pnl_pct:.0f}% (best_bid${bid_price:.3f}/avg${avg:.3f} cur${cur_price:.3f}),自动全卖锁翻倍",
                "sell_size": size,
                "executed_action": "take_profit_pnl_sold",
            })

        # === 2. STOP_LOSS (LLM 入场分类的三档, 用 cur_price 算) ===
        tier = meta.get("stop_loss_tier")
        if avg and avg > 0 and "stop_loss_sold" not in executed:
            triggered = False
            tier_pct = STOP_LOSS_PCT_BY_TIER.get(tier, STOP_LOSS_PCT_LEGACY) if tier else STOP_LOSS_PCT_LEGACY
            tier_label = tier or "legacy(-25%)"
            if tier == "event_driven":
                # 事件驱动型: 不按百分比, 只在价格跌破 $0.05 才止损 (地板价兜底)
                if cur_price < EVENT_DRIVEN_FLOOR_PRICE:
                    triggered = True
                    reason = f"事件驱动型: 价${cur_price:.3f} 跌破地板${EVENT_DRIVEN_FLOOR_PRICE:.2f},自动全卖兜底"
            elif tier_pct is not None:
                # convergent (-20%) / hybrid (-35%) / legacy (-25%) 按百分比
                if (avg - cur_price) / avg >= tier_pct:
                    triggered = True
                    loss_pct = (avg - cur_price) / avg * 100
                    reason = f"[{tier_label}] 亏-{loss_pct:.0f}% (阈值-{int(tier_pct*100)}%, cur${cur_price:.3f}/avg${avg:.3f}),自动全卖止损"
            if triggered:
                return ("STOP_LOSS", {
                    "action": "STOP_LOSS",
                    "reason": reason,
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
        positions = self.executor.get_positions()
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
        # 新策略: orphan 每被观察到一次计数 +1, 连续 3 轮 (~9 min) 都缺才真删; 中间一旦
        # 恢复出现就清零计数. 副作用: 用户真平仓后 db 残留 meta 多撑 ~9 分钟, 无害.
        ORPHAN_CONFIRM_RUNS = 3
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
            state, action = self._evaluate_position(pos, meta)

            update_monitor_state(token_id, state)

            if action:
                log.info(f"→ AUTO_SELL [{state}] {title[:40]} size={action['sell_size']} | {action['reason']}")
                ok = self.executor.sell(token_id, action["sell_size"], action["reason"])
                if ok:
                    mark_executed_action(token_id, action["executed_action"])
                    log_event("auto_sell", title, f"{action['action']} size={action['sell_size']} {action['reason']}")
                    # 自动卖成功后立即清理 meta (跟二次入场逻辑一致, 让下次重新买入是白纸)
                    clear_position_meta(token_id)
                    log.info(f"auto_sell 后清理 meta: {title[:40]}")
                    action["success"] = True
                else:
                    action["success"] = False
                    log.warning(f"自动sell失败: {title[:40]}")
                results.append({**action, "title": title, "monitor_state": state})
        return results

    def run_loop(self):
        self.running = True
        log.info(f"Monitor v5.6 started (每{CHECK_INTERVAL}s)")
        log.info(f"v5.6 自动卖规则 (优先级从高到低):")
        log.info(f"  1a. 价格止盈:    best_bid ≥ {TAKE_PROFIT_PRICE:.2f} → 自动全卖")
        log.info(f"  1b. 浮盈翻倍:    (best_bid-avg)/avg ≥ +{TAKE_PROFIT_PNL_PCT*100:.0f}% → 自动全卖")
        log.info(f"  2.  止损 (LLM 入场分级):")
        log.info(f"      convergent   (真相收敛型) → -{int(STOP_LOSS_PCT_BY_TIER['convergent']*100)}%")
        log.info(f"      hybrid       (混合型)     → -{int(STOP_LOSS_PCT_BY_TIER['hybrid']*100)}%")
        log.info(f"      event_driven (事件驱动型) → 不止损, 价 < ${EVENT_DRIVEN_FLOOR_PRICE:.2f} 地板兜底")
        log.info(f"      (未分类老仓位 fallback -{int(STOP_LOSS_PCT_LEGACY*100)}%)")
        log.info(f"  3.  TIME_STOP:   距结算 ≤{TIME_STOP_DAYS}天 + 漂移<{TIME_STOP_DRIFT_PP}pp → 自动全卖")
        log.info(f"决策模式 (edge-based, 等用户确认):")
        log.info(f"  HOLD: edge > +{HOLD_MIN_EDGE_PP}pp")
        log.info(f"  MARGINAL: -{abs(SOFT_NEGATIVE_THRESHOLD_PP)} ≤ edge ≤ +{HOLD_MIN_EDGE_PP}pp")
        log.info(f"  SOFT_NEGATIVE: edge < {SOFT_NEGATIVE_THRESHOLD_PP}pp (重评过)")
        log.info(f"  AT_TARGET: edge < {SOFT_NEGATIVE_THRESHOLD_PP}pp (未重评)")
        while self.running:
            try:
                self.check_once()
            except Exception as e:
                log.exception(f"Monitor error: {e}")
            time.sleep(CHECK_INTERVAL)

    def stop(self):
        self.running = False
