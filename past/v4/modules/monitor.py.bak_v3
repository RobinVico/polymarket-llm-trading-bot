"""
Position Monitor v3 — 止盈分档 + 三档反向止损 + 时间止损
"""
import time
import logging
from datetime import datetime, timezone
from modules.executor import Executor
from modules.db import log_event, get_sold_tiers, record_tier_sold, get_position_meta

log = logging.getLogger("monitor")

# === 止盈配置（绝对pp，买入价方向） ===
SMALL_EDGE_THRESHOLD_PP = 10   # <10pp视为小edge, 单次平仓
SMALL_EDGE_MIN_MOVE_PP = 3
SMALL_EDGE_MAX_TARGET_PP = 8

# 大edge三档
TIER_1_MOVE_PP = 5
TIER_1_SELL_PCT = 25
TIER_2_MOVE_PP = 10
TIER_2_SELL_PCT = 35
TIER_3_MOVE_PP = 15
TIER_3_SELL_PCT = 40
TIER_3_MAX_NEAR_TP_PP = 2

# === 反向止损三档（反向方向） ===
WARNING1_GAP_RATIO = 0.33
WARNING1_MIN_PP = 5
WARNING1_MAX_PP = 8
WARNING1_SELL_PCT = 30

WARNING2_GAP_RATIO = 0.67
WARNING2_MIN_PP = 10
WARNING2_MAX_PP = 14
WARNING2_SELL_PCT = 40

WARNING3_GAP_RATIO = 1.0
WARNING3_MIN_PP = 15
WARNING3_MAX_PP = 20
WARNING3_SELL_PCT = 30

# === 时间止损 ===
TIME_STOP_DAYS = 2
TIME_STOP_MOVE_PP = 0.05

CHECK_INTERVAL = 180

# 兼容dashboard展示
TAKE_PROFIT_RULES = [
    {"name": "T1", "move_pp": TIER_1_MOVE_PP, "sell_pct": TIER_1_SELL_PCT},
    {"name": "T2", "move_pp": TIER_2_MOVE_PP, "sell_pct": TIER_2_SELL_PCT},
    {"name": "T3", "move_pp": TIER_3_MOVE_PP, "sell_pct": TIER_3_SELL_PCT},
]


class PositionMonitor:
    def __init__(self):
        self.executor = Executor.get()
        self.running = False

    def _days_to_settle(self, end_date_str):
        if not end_date_str: return None
        try:
            end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            return (end_dt - datetime.now(timezone.utc)).days
        except:
            return None

    def _evaluate_position(self, pos, meta):
        """
        Polymarket持仓通用逻辑:
          - 不管YES还是NO,持有的token价格向上=赚钱,向下=亏钱
          - mp (meta.entry_price): 持仓的token买入价
          - tp: 用户对下注方向的真实概率估计 = 目标token价格
          - 止盈向上, 止损向下, 方向统一
        """
        actions = []
        title = pos["title"]
        side = pos["side"]
        cur_price = pos["cur_price"]
        size = pos["size"]
        token_id = pos["asset"]

        if not meta:
            return actions

        mp = meta["entry_price"]
        tp = meta.get("new_tp") if meta.get("new_tp") else meta["tp"]
        end_date = meta.get("end_date", "")

        target_gap = tp - mp
        # Sanity check: tp必须高于mp才有edge
        if target_gap <= 0:
            log.warning(f"⚠ {title[:30]}: tp({tp})<=mp({mp}) 无edge, side={side} 跳过")
            return actions

        gap_pp = target_gap * 100
        sold_tiers = get_sold_tiers(token_id)

        # === 时间止损（最优先） ===
        days = self._days_to_settle(end_date)
        if days is not None and days <= TIME_STOP_DAYS:
            if abs(cur_price - mp) < TIME_STOP_MOVE_PP:
                actions.append({
                    "action": "TIME_STOP",
                    "reason": f"距结算{days}天+价格仅动{abs(cur_price-mp)*100:.1f}pp",
                    "sell_size": size,
                    "tier": "TIME_STOP"
                })
                return actions

        # === 止盈（价格向上触发） ===
        if gap_pp < SMALL_EDGE_THRESHOLD_PP:
            # 小edge单次平仓
            if "SMALL_EXIT" not in sold_tiers:
                target_pp = max(min(gap_pp - 2, SMALL_EDGE_MAX_TARGET_PP), SMALL_EDGE_MIN_MOVE_PP)
                trigger_price = mp + target_pp / 100
                if cur_price >= trigger_price:
                    actions.append({
                        "action": "SMALL_EDGE_EXIT",
                        "reason": f"小edge({gap_pp:.1f}pp) 价到${trigger_price:.3f} 全平",
                        "sell_size": size,
                        "tier": "SMALL_EXIT"
                    })
                    return actions
        else:
            # 大edge三档
            tiers = [
                ("T1", TIER_1_MOVE_PP, TIER_1_SELL_PCT),
                ("T2", TIER_2_MOVE_PP, TIER_2_SELL_PCT),
                ("T3", TIER_3_MOVE_PP, TIER_3_SELL_PCT),
            ]
            for tier_name, move_pp, sell_pct in tiers:
                if tier_name in sold_tiers:
                    continue
                base = mp + move_pp / 100
                if tier_name == "T3":
                    cap = tp - TIER_3_MAX_NEAR_TP_PP / 100
                    trigger_price = min(base, cap)
                else:
                    trigger_price = base
                if cur_price >= trigger_price:
                    initial = meta.get("initial_size") or size
                    sell_size = round(initial * sell_pct / 100, 2)
                    sell_size = min(sell_size, size)
                    if sell_size < 0.1: sell_size = size
                    actions.append({
                        "action": f"TAKE_PROFIT_{tier_name}",
                        "reason": f"{tier_name}: 价到${trigger_price:.3f} (+{move_pp}pp) 卖{sell_pct}%",
                        "sell_size": sell_size,
                        "tier": tier_name
                    })
                    return actions

        # === 反向止损三档（价格向下触发） ===
        w1_dist = max(WARNING1_MIN_PP, min(gap_pp * WARNING1_GAP_RATIO, WARNING1_MAX_PP)) / 100
        w2_dist = max(WARNING2_MIN_PP, min(gap_pp * WARNING2_GAP_RATIO, WARNING2_MAX_PP)) / 100
        w3_dist = max(WARNING3_MIN_PP, min(gap_pp * WARNING3_GAP_RATIO, WARNING3_MAX_PP)) / 100

        w1_price = mp - w1_dist
        w2_price = mp - w2_dist
        w3_price = mp - w3_dist

        # 从最严重到最轻
        if cur_price <= w3_price and "WARNING3" not in sold_tiers:
            actions.append({
                "action": "WARNING3_FULL_EXIT",
                "reason": f"W3: 价${cur_price:.3f}跌破${w3_price:.3f} (-{w3_dist*100:.1f}pp) 全平",
                "sell_size": size,
                "tier": "WARNING3"
            })
        elif cur_price <= w2_price and "WARNING2" not in sold_tiers:
            initial = meta.get("initial_size") or size
            sell_size = round(initial * WARNING2_SELL_PCT / 100, 2)
            sell_size = min(sell_size, size)
            if sell_size < 0.1: sell_size = size
            actions.append({
                "action": "WARNING2_40",
                "reason": f"W2: 价${cur_price:.3f}跌破${w2_price:.3f} (-{w2_dist*100:.1f}pp) 卖{WARNING2_SELL_PCT}%",
                "sell_size": sell_size,
                "tier": "WARNING2"
            })
        elif cur_price <= w1_price and "WARNING1" not in sold_tiers:
            initial = meta.get("initial_size") or size
            sell_size = round(initial * WARNING1_SELL_PCT / 100, 2)
            sell_size = min(sell_size, size)
            if sell_size < 0.1: sell_size = size
            actions.append({
                "action": "WARNING1_30",
                "reason": f"W1: 价${cur_price:.3f}跌破${w1_price:.3f} (-{w1_dist*100:.1f}pp) 卖{WARNING1_SELL_PCT}%",
                "sell_size": sell_size,
                "tier": "WARNING1"
            })

        return actions

    def check_once(self):
        positions = self.executor.get_positions()
        if not positions: return []
        all_actions = []
        for pos in positions:
            title = pos["title"]
            token_id = pos["asset"]
            if not token_id: continue
            meta = get_position_meta(token_id)
            actions = self._evaluate_position(pos, meta)
            for a in actions:
                if a["sell_size"] > 0:
                    log.info(f"→ {a['action']}: {title[:40]} sell={a['sell_size']} | {a['reason']}")
                    ok = self.executor.sell(token_id, a["sell_size"], a["reason"])
                    if ok and a["tier"]:
                        record_tier_sold(token_id, a["tier"])
                    log_event("sell", title, f"{a['action']} size={a['sell_size']} {a['reason']}")
                    a["success"] = ok
                    all_actions.append({**a, "title": title})
        return all_actions

    def run_loop(self):
        self.running = True
        log.info(f"Monitor v3 started (每{CHECK_INTERVAL}s)")
        log.info(f"止盈: 小edge单次 | 大edge三档 +5/+10/+15pp 卖25/35/40%")
        log.info(f"反向止损三档: 警告1(~7pp卖30%) / 警告2(~14pp卖40%) / 警告3(~20pp全平)")
        log.info(f"时间止损: ≤{TIME_STOP_DAYS}天 且 偏移<{TIME_STOP_MOVE_PP*100:.0f}pp")
        while self.running:
            try:
                self.check_once()
            except Exception as e:
                log.exception(f"Monitor error: {e}")
            time.sleep(CHECK_INTERVAL)

    def stop(self):
        self.running = False
