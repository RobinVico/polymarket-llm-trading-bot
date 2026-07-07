"""
Position Monitor v4 — Edge-based decision engine
核心: q vs p 决策, bot不主动sell (除了DISASTER和TIME_STOP兜底)
"""
import time
import logging
from datetime import datetime, timezone, timedelta
from modules.executor import Executor
from modules.db import (log_event, get_position_meta, update_monitor_state,
                        mark_executed_action, save_portfolio_snapshot)

log = logging.getLogger("monitor")

# === 决策阈值 (v4) ===
HOLD_MIN_EDGE_PP = 2.0          # edge > +2pp 持有
SOFT_NEGATIVE_THRESHOLD_PP = -3.0  # edge < -3pp 警戒

# === 兜底自动卖 ===
DISASTER_DROP_PP = 25.0         # p < entry-25pp 灾难止损 (自动卖)
TIME_STOP_DAYS = 2              # 距结算 ≤2天
TIME_STOP_DRIFT_PP = 5.0        # 价格漂移 < 5pp

# === 黑天鹅对冲 ===
BLACKSWAN_PRICE = 0.97          # p ≥ 0.97
BLACKSWAN_MIN_DAYS = 1          # 距结算 > 1天

# === 心跳 ===
CHECK_INTERVAL = 180            # 3分钟
SNAPSHOT_INTERVAL = 1800        # portfolio_snapshot 每 30 分钟一次 (心跳 ÷ snapshot 节流)


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

    def _evaluate_position(self, pos, meta):
        """
        返回: (monitor_state, action_dict_or_None)
        action_dict 仅在 DISASTER 或 TIME_STOP 触发自动卖时返回
        其他状态只是写到 monitor_state, 等待用户在 dashboard 确认
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

        # === 优先级1: 时间止损 (自动卖) ===
        if (days_left is not None and days_left <= TIME_STOP_DAYS
                and abs(cur_price - entry) * 100 < TIME_STOP_DRIFT_PP
                and "time_stop_sold" not in executed):
            return ("TIME_STOP", {
                "action": "TIME_STOP",
                "reason": f"距结算{days_left}天+价格仅动{abs(cur_price-entry)*100:.1f}pp,自动平仓",
                "sell_size": size,
                "executed_action": "time_stop_sold",
            })

        # === 优先级2: 灾难止损 (自动卖) ===
        drop_pp = (entry - cur_price) * 100
        if (drop_pp >= DISASTER_DROP_PP
                and "disaster_sold" not in executed):
            return ("DISASTER", {
                "action": "DISASTER",
                "reason": f"价${cur_price:.3f}跌破入场${entry:.3f}-25pp,灾难止损自动平仓",
                "sell_size": size,
                "executed_action": "disaster_sold",
            })

        # === 黑天鹅对冲 (优先级高于AT_TARGET, 因为p>=0.97时更紧迫) ===
        if (cur_price >= BLACKSWAN_PRICE
                and days_left is not None and days_left > BLACKSWAN_MIN_DAYS):
            return ("BLACKSWAN_HEDGE", None)

        # === edge-based 决策 (核心) ===
        edge_pp = (q - cur_price) * 100

        # === edge-based 决策 ===
        # HOLD:               edge > +2pp           (cur 远低于 q)
        # MARGINAL:    -3 ≤ edge ≤ +2 pp            (边缘地带,含cur接近q)
        # AT_TARGET / SOFT_NEGATIVE: edge < -3 pp   (cur 远高于 q, 区分按是否用户调过q)
        if edge_pp > HOLD_MIN_EDGE_PP:
            return ("HOLD", None)
        elif edge_pp >= SOFT_NEGATIVE_THRESHOLD_PP:
            return ("MARGINAL", None)
        else:
            # edge < -3pp: cur 显著高于 q
            user_changed_q = bool(meta.get("last_reeval_at"))
            if not user_changed_q:
                # 用户从未重评过 q, cur 自然涨到远高于 q = 真实概率实现, 建议清仓兑现
                return ("AT_TARGET", None)
            elif meta.get("last_q_update_with_negative_edge"):
                # 用户调过 q 且已记录过一次 negative, 升级
                return ("CONFIRMED_NEGATIVE", None)
            else:
                # 用户调过 q, 第一次 edge 翻负
                return ("SOFT_NEGATIVE", None)

    def check_once(self):
        positions = self.executor.get_positions()
        # === portfolio_snapshot: 节流到 30 分钟一次 (心跳 3 分钟还是要跑,只是不每次都写) ===
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
            
            # 写入 monitor_state (即使 NO_META 也写)
            update_monitor_state(token_id, state)
            
            # 仅 DISASTER 和 TIME_STOP 自动卖
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
        log.info(f"Monitor v4 started (每{CHECK_INTERVAL}s)")
        log.info(f"决策模式: edge-based (q vs p)")
        log.info(f"  HOLD: edge > +{HOLD_MIN_EDGE_PP}pp")
        log.info(f"  MARGINAL: -{abs(SOFT_NEGATIVE_THRESHOLD_PP)} ≤ edge ≤ +{HOLD_MIN_EDGE_PP}pp")
        log.info(f"  SOFT/CONFIRMED_NEGATIVE: edge < {SOFT_NEGATIVE_THRESHOLD_PP}pp (需2次q重评升级)")
        log.info(f"自动卖: 仅 DISASTER (-{DISASTER_DROP_PP}pp) 和 TIME_STOP (≤{TIME_STOP_DAYS}天+漂移<{TIME_STOP_DRIFT_PP}pp)")
        log.info(f"其他状态: 写入monitor_state, 等用户在dashboard确认")
        while self.running:
            try:
                self.check_once()
            except Exception as e:
                log.exception(f"Monitor error: {e}")
            time.sleep(CHECK_INTERVAL)

    def stop(self):
        self.running = False
