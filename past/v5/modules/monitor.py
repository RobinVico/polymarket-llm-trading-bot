"""
Position Monitor v5.1 — 三层止损 + 两条止盈 + edge-based 决策
v5.1 改动 vs v5:
  - 新增两条最高优先级的自动止盈规则 (90¢ 价格触发 / +100% 浮盈翻倍), 触发后全卖.
  - 删除 BLACKSWAN_HEDGE (97¢ 减半) 状态 — 90¢ 止盈已完全覆盖.
  - 删除 CONFIRMED_NEGATIVE 二级负 edge 状态 — 简化为 SOFT_NEGATIVE 一档.
v5 改动 vs v4: 替换 DISASTER 单一 25pp 阈值, 改为入场价分档的 stop_price + 跌速判断 (急/慢) + 24h 冻结机制 + 60% 价值兜底.
其他逻辑 (TIME_STOP / edge-based) 跟 v4 一致.
"""
import time
import logging
from datetime import datetime, timezone, timedelta
from modules.executor import Executor
from modules.db import (log_event, get_position_meta, update_monitor_state,
                        mark_executed_action, save_portfolio_snapshot,
                        set_freeze_until, clear_freeze)

log = logging.getLogger("monitor")

# === 决策阈值 (v4 沿用) ===
HOLD_MIN_EDGE_PP = 2.0
SOFT_NEGATIVE_THRESHOLD_PP = -3.0

# === v5.1 自动止盈 (最高优先级, 触发即全卖) ===
TAKE_PROFIT_PRICE = 0.90     # 价 ≥ 90¢ → 自动全卖 (边际收益低 + 流动性差 + UMA 结算风险)
TAKE_PROFIT_PNL_PCT = 1.00   # 浮盈 ≥ +100% (翻倍) → 自动全卖 (锁定一倍利润)

# === TIME_STOP (v4 沿用, 不动) ===
TIME_STOP_DAYS = 2
TIME_STOP_DRIFT_PP = 5.0

# === Legacy compat: dashboard.py 还 import 这个做 UI 显示, 不影响 v5 决策 ===
DISASTER_DROP_PP = 25.0  # deprecated in v5, kept only for dashboard.py import compat

# === v5 新止损规则 ===
SLOW_DROP_MIN_MINUTES = 30   # 跌破 stop 已经持续 >30 分钟 = 慢跌, 触发 bot 自动卖
FREEZE_DURATION_HOURS = 24   # 急跌冻结时长
UNFREEZE_RECOVERY_PP = 10    # 冻结期间价回到 entry - 10pp 以内自动解冻 (单位 pp 绝对值)
ABSOLUTE_FLOOR_PCT = 0.40    # 价格 / entry < 40% 触发绝对兜底 (亏 60%)

# === 心跳 ===
CHECK_INTERVAL = 180
SNAPSHOT_INTERVAL = 1800


def _stop_price(entry):
    """
    从入场价算止损价 (近似分档表). 用户的 reference 表:
    | entry | stop | drop |
    |  70¢  | 44¢  | 26pp |
    |  60¢  | 33¢  | 27pp |
    |  50¢  | 25¢  | 25pp |
    |  40¢  | 18¢  | 22pp |
    |  30¢  | 12¢  | 18pp |
    |  20¢  |  8¢  | 12pp |
    |  17¢  |  6¢  | 11pp |
    |  10¢  |  4¢  |  6pp | ← <15¢ 走规则 3 (兜底) 反而稳
    简化分档:
    | entry ≥ 50¢   | drop 25pp |
    | 30 ≤ entry <50¢ | drop 18pp |
    | 15 ≤ entry <30¢ | drop 10pp |
    | entry < 15¢     | None (靠 rule 3 兜底)
    """
    if entry is None or entry <= 0:
        return None
    if entry >= 0.50:
        return entry - 0.25
    if entry >= 0.30:
        return entry - 0.18
    if entry >= 0.15:
        return entry - 0.10
    return None


class PositionMonitor:
    def __init__(self):
        self.executor = Executor.get()
        self.running = False
        self._last_snapshot_ts = 0

    def _days_to_settle(self, end_date_str):
        if not end_date_str:
            return None
        try:
            end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            delta = end_dt - datetime.now(timezone.utc)
            return delta.days + (1 if delta.seconds > 43200 else 0)
        except Exception:
            return None

    def _drop_duration_minutes(self, token_id, stop):
        """
        拉过去 1 小时的 1-min 历史价 (60 个点), 找最近一次 price > stop 的时间,
        计算到现在已经持续低于 stop 多少分钟. 失败返回 None.
        """
        try:
            hist = self.executor.get_prices_history(token_id, interval="1h", fidelity="1", force=True)
            if not hist or len(hist) < 2:
                return None
            # 从最新往回扫, 找最近一个 > stop 的点
            last_above_idx = None
            for i in range(len(hist) - 1, -1, -1):
                p = hist[i].get("p") or 0
                if p > stop:
                    last_above_idx = i
                    break
            if last_above_idx is None:
                # 整 1h 内价格都 <= stop, 算作"已经持续很久"
                return 60
            # 跌破发生在 last_above_idx + 1 这个点 (大约)
            drop_start_idx = last_above_idx + 1
            if drop_start_idx >= len(hist):
                return 0  # 上一次还在上面, 现在刚跌
            drop_start_ts = hist[drop_start_idx].get("t") or int(time.time())
            duration_min = (int(time.time()) - drop_start_ts) // 60
            return max(0, min(duration_min, 60))
        except Exception as e:
            log.warning(f"drop duration calc failed for {token_id[:20]}: {e}")
            return None

    def _evaluate_position(self, pos, meta):
        """
        v5.1 优先级 (第一个匹配胜出):
        0. NO_META
        1a. TAKE_PROFIT_PRICE (价 ≥ 90¢)             (自动全卖, v5.1 新增, 最高优先级)
        1b. TAKE_PROFIT_PNL (浮盈 ≥ +100%)           (自动全卖, v5.1 新增, 最高优先级)
        2. TIME_STOP                                 (自动卖, 跟 v4 一致)
        3. FROZEN 状态检查 (急跌后 24h 冻结期)        (不评估, 不卖, 或解冻 / 到期卖)
        4. ABSOLUTE_FLOOR (价值 <40% of entry)       (自动卖, 兜底)
        5. STOP_HIT (价格跌破 stop_price)
           - 慢跌 (>30min): SLOW_DROP 自动卖
           - 急跌 (<30min): 触发 24h 冻结, FROZEN_FRESH 状态
        6. Edge-based (HOLD / MARGINAL / SOFT / AT_TARGET)
        """
        title = pos["title"]
        cur_price = pos["cur_price"]
        size = pos["size"]
        token_id = pos["asset"]

        if not meta:
            return ("NO_META", None)

        entry = meta["entry_price"]
        q = meta.get("new_tp") if meta.get("new_tp") else meta["tp"]
        end_date = meta.get("end_date", "")
        executed = meta.get("executed_action") or ""

        days_left = self._days_to_settle(end_date)

        # === 1a. TAKE_PROFIT_PRICE (价 ≥ 90¢, 自动全卖, v5.1 最高优先级) ===
        if (cur_price >= TAKE_PROFIT_PRICE
                and "take_profit_price_sold" not in executed):
            return ("TAKE_PROFIT_PRICE", {
                "action": "TAKE_PROFIT_PRICE",
                "reason": f"价${cur_price:.3f} ≥ {TAKE_PROFIT_PRICE:.2f},自动全卖锁定止盈",
                "sell_size": size,
                "executed_action": "take_profit_price_sold",
            })

        # === 1b. TAKE_PROFIT_PNL (浮盈 ≥ +100%, 自动全卖, v5.1 最高优先级) ===
        if (entry and entry > 0
                and (cur_price - entry) / entry >= TAKE_PROFIT_PNL_PCT
                and "take_profit_pnl_sold" not in executed):
            pnl_pct = (cur_price - entry) / entry * 100
            return ("TAKE_PROFIT_PNL", {
                "action": "TAKE_PROFIT_PNL",
                "reason": f"浮盈+{pnl_pct:.0f}% (cur${cur_price:.3f}/entry${entry:.3f}),自动全卖锁定翻倍",
                "sell_size": size,
                "executed_action": "take_profit_pnl_sold",
            })

        # === 2. TIME_STOP (自动卖) ===
        if (days_left is not None and days_left <= TIME_STOP_DAYS
                and abs(cur_price - entry) * 100 < TIME_STOP_DRIFT_PP
                and "time_stop_sold" not in executed):
            return ("TIME_STOP", {
                "action": "TIME_STOP",
                "reason": f"距结算{days_left}天+价格仅动{abs(cur_price-entry)*100:.1f}pp,自动平仓",
                "sell_size": size,
                "executed_action": "time_stop_sold",
            })

        # === 2. FROZEN 状态检查 ===
        freeze_until_str = meta.get("freeze_until")
        if freeze_until_str:
            try:
                freeze_until = datetime.fromisoformat(freeze_until_str)
                if freeze_until.tzinfo is None:
                    freeze_until = freeze_until.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                # 检查是否回到 entry - 10pp 以内
                recovery_threshold = entry - (UNFREEZE_RECOVERY_PP / 100.0)
                if cur_price >= recovery_threshold:
                    clear_freeze(token_id)
                    log.info(f"unfreeze {title[:40]}: 价${cur_price:.3f} 回到入场-{UNFREEZE_RECOVERY_PP}pp 以内")
                    # 继续走正常评估 (fall through)
                elif now < freeze_until:
                    # 还在冻结期 → 不卖, 不评估其他
                    return ("FROZEN", None)
                else:
                    # 冻结期满 + 价还没回来 → 卖
                    clear_freeze(token_id)
                    if "frozen_expired_sold" not in executed:
                        return ("FROZEN_EXPIRED", {
                            "action": "FROZEN_EXPIRED",
                            "reason": f"冻结24h后价${cur_price:.3f}仍低于止损,自动平仓",
                            "sell_size": size,
                            "executed_action": "frozen_expired_sold",
                        })
            except Exception as e:
                log.warning(f"freeze parse error for {token_id[:20]}: {e}")

        # === 3. ABSOLUTE_FLOOR (任何情况 价值 < 40% of entry) ===
        if entry > 0 and "floor_sold" not in executed:
            if cur_price / entry < ABSOLUTE_FLOOR_PCT:
                return ("ABSOLUTE_FLOOR", {
                    "action": "ABSOLUTE_FLOOR",
                    "reason": f"价${cur_price:.3f}={(cur_price/entry*100):.0f}% of入场${entry:.3f},绝对兜底自动平仓",
                    "sell_size": size,
                    "executed_action": "floor_sold",
                })

        # === 4. STOP_HIT (慢跌卖 / 急跌冻结) ===
        stop = _stop_price(entry)
        if stop and cur_price <= stop and "stop_loss_sold" not in executed:
            drop_minutes = self._drop_duration_minutes(token_id, stop)
            if drop_minutes is None or drop_minutes >= SLOW_DROP_MIN_MINUTES:
                # 慢跌 → 卖
                return ("SLOW_DROP", {
                    "action": "SLOW_DROP",
                    "reason": f"价${cur_price:.3f}跌破止损${stop:.3f}已{drop_minutes if drop_minutes is not None else '?'}分钟,慢跌自动平仓",
                    "sell_size": size,
                    "executed_action": "stop_loss_sold",
                })
            else:
                # 急跌 → 冻结 24h
                freeze_until = datetime.now(timezone.utc) + timedelta(hours=FREEZE_DURATION_HOURS)
                set_freeze_until(token_id, freeze_until.isoformat(), stop)
                log.info(f"急跌冻结 {title[:40]} 24h | 跌破止损${stop:.3f}用时{drop_minutes}分钟")
                return ("FROZEN_FRESH", None)

        # === 6. Edge-based 决策 ===
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
        if now_ts - self._last_snapshot_ts >= SNAPSHOT_INTERVAL:
            try:
                total_value = sum((p.get("cur_price") or 0) * (p.get("size") or 0) for p in (positions or []))
                total_cost = sum((p.get("avg_price") or 0) * (p.get("size") or 0) for p in (positions or []))
                total_pnl = total_value - total_cost
                cash = self.executor.get_cash_balance()
                save_portfolio_snapshot(now_ts, total_value, total_cost, cash, total_pnl, total_value + cash)
                self._last_snapshot_ts = now_ts
            except Exception as e:
                log.warning(f"portfolio_snapshot save failed: {e}")
        if not positions:
            return []
        results = []
        for pos in positions:
            title = pos["title"]
            token_id = pos["asset"]
            if not token_id:
                continue
            meta = get_position_meta(token_id)
            state, action = self._evaluate_position(pos, meta)

            update_monitor_state(token_id, state)

            if action:
                log.info(f"→ AUTO_SELL [{state}] {title[:40]} size={action['sell_size']} | {action['reason']}")
                ok = self.executor.sell(token_id, action["sell_size"], action["reason"])
                if ok:
                    mark_executed_action(token_id, action["executed_action"])
                    log_event("auto_sell", title, f"{action['action']} size={action['sell_size']} {action['reason']}")
                    action["success"] = True
                else:
                    action["success"] = False
                    log.warning(f"自动sell失败: {title[:40]}")
                results.append({**action, "title": title, "monitor_state": state})
        return results

    def run_loop(self):
        self.running = True
        log.info(f"Monitor v5.1 started (每{CHECK_INTERVAL}s)")
        log.info(f"v5.1 止盈规则 (最高优先级, 全卖):")
        log.info(f"  - 价格止盈:   cur ≥ {TAKE_PROFIT_PRICE:.2f} → 自动全卖")
        log.info(f"  - 浮盈翻倍:   (cur-entry)/entry ≥ +{TAKE_PROFIT_PNL_PCT*100:.0f}% → 自动全卖")
        log.info(f"v5 止损规则:")
        log.info(f"  - 慢跌硬止损: cur ≤ stop_price(entry) 且 >{SLOW_DROP_MIN_MINUTES}min → 自动卖")
        log.info(f"  - 急跌冻结:   cur ≤ stop_price(entry) 且 <{SLOW_DROP_MIN_MINUTES}min → 冻结 {FREEZE_DURATION_HOURS}h")
        log.info(f"  - 绝对兜底:   cur/entry < {ABSOLUTE_FLOOR_PCT*100:.0f}% → 自动卖")
        log.info(f"决策模式: edge-based (q vs p)")
        log.info(f"  HOLD: edge > +{HOLD_MIN_EDGE_PP}pp")
        log.info(f"  MARGINAL: -{abs(SOFT_NEGATIVE_THRESHOLD_PP)} ≤ edge ≤ +{HOLD_MIN_EDGE_PP}pp")
        log.info(f"  SOFT_NEGATIVE: edge < {SOFT_NEGATIVE_THRESHOLD_PP}pp (重评过)")
        log.info(f"  AT_TARGET: edge < {SOFT_NEGATIVE_THRESHOLD_PP}pp (未重评)")
        log.info(f"TIME_STOP: ≤{TIME_STOP_DAYS}天 + 漂移<{TIME_STOP_DRIFT_PP}pp")
        while self.running:
            try:
                self.check_once()
            except Exception as e:
                log.exception(f"Monitor error: {e}")
            time.sleep(CHECK_INTERVAL)

    def stop(self):
        self.running = False
