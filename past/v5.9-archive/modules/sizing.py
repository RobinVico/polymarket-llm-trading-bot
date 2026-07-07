"""
v5.9: Position sizing formula.

设计: 1/4 Kelly + days+longshot 调整 + cluster cap + 月 DD 预算 + 硬边界 [$1, $15].
单层折扣: q 假设已经是 DISCOVERY calibrated (市场价 + 0.5 × (q_raw - 市场价)).
       公式不再二次折扣. confidence 字段接收但不影响 size (留作 metadata).

总信任度 = 0.5 (DISCOVERY 那一层).

参见: 技术报告.md §十九.
"""
import os
import logging

log = logging.getLogger("sizing")

# 默认参数 (可通过 env var SIZING_<KEY> 覆盖)
SIZING_CFG = {
    "KELLY_FRACTION": 0.25,        # 1/4 Kelly. Thorp 闭式解 P(本金跌到 50%) = 0.5^7 ≈ 0.78%.
    "MONTHLY_DD_BUDGET": 30.0,     # 用户偏好的月度组合 expected drawdown 上限.
    "CLUSTER_CAP_PCT": 0.20,       # 单 cluster 暴露 ≤ 20% bankroll.
    "MAX_SINGLE_POS": 15.0,        # 单仓硬上限.
    "MIN_SINGLE_POS": 1.0,         # 单仓硬下限 (低于此返回 $0).
    "REF_DAYS": 21.0,              # days 折现参考 horizon.
    "LONGSHOT_THRESH": 0.15,       # 触发 longshot 减仓的 p 阈值.
    "TIER_DD": {                   # 每档预期最大跌幅 (用于 DD 预算配给).
        "convergent": 0.20,
        "hybrid": 0.35,
        "event_driven": 0.70,
    },
}


def _cfg(key):
    """读 SIZING_CFG, 优先用 env var 覆盖. 仅支持 scalar 参数; TIER_DD 走专用 helper."""
    if key not in SIZING_CFG or isinstance(SIZING_CFG[key], dict):
        return SIZING_CFG.get(key)
    env_val = os.environ.get(f"SIZING_{key}")
    if env_val is not None:
        try:
            return float(env_val)
        except (ValueError, TypeError):
            log.warning(f"SIZING_{key}={env_val!r} not parseable as float, using default {SIZING_CFG[key]}")
    return SIZING_CFG[key]


def _tier_dd(tier):
    """读 TIER_DD[tier], 优先用 env var SIZING_TIER_DD_<tier> 覆盖."""
    if tier not in SIZING_CFG["TIER_DD"]:
        log.warning(f"unknown tier {tier!r}, fallback to hybrid")
        tier = "hybrid"
    env_val = os.environ.get(f"SIZING_TIER_DD_{tier}")
    if env_val is not None:
        try:
            return float(env_val)
        except (ValueError, TypeError):
            pass
    return SIZING_CFG["TIER_DD"][tier]


def position_size_usd(q, p, confidence, stop_loss_tier, days_to_resolution,
                      bankroll_usd, cluster_current_exposure_usd,
                      cluster_cap_usd, exposed_dd_usd):
    """
    Args:
        q: Claude calibrated probability (持有方向兑现概率, DISCOVERY 已经 0.5 收缩过).
        p: 持有方向的市场当前价 (token 价格 0-1).
        confidence: 'high'/'medium'/'low'. 接收但不进公式 (单层折扣方案, metadata only).
        stop_loss_tier: 'convergent'/'hybrid'/'event_driven'. 决定 TIER_DD.
        days_to_resolution: 距结算天数.
        bankroll_usd: 当前总资产 = cash + Σ(cur_price × size).
        cluster_current_exposure_usd: 同 cluster 已暴露 (现价口径, 不是 cost_basis).
        cluster_cap_usd: 该 cluster 的上限 = bankroll × CLUSTER_CAP_PCT.
        exposed_dd_usd: 已暴露的 expected drawdown = Σ (cur_value × TIER_DD[tier]).

    Returns:
        (size_usd: float, reason: str)
    """
    # ---- Step 1: edge 检查 (无 LLM haircut, 单层折扣已在 DISCOVERY) ----
    edge = q - p
    if edge <= 0:
        return 0.0, f"no edge (q={q:.3f} <= p={p:.3f})"

    # ---- Step 2: 单 token Kelly + 分数 Kelly ----
    # f* = edge / (1 - p) 对买持有方向 token 的 binary Kelly.
    kelly_f = edge / (1.0 - p)
    raw = bankroll_usd * kelly_f * _cfg("KELLY_FRACTION")

    # ---- Step 3: days + longshot 调整 ----
    # days_factor: sqrt(REF_DAYS / days), clip 到 [0.40, 1.0].
    #   长 fuse (days > REF) 减仓; 短窗口 (days < REF) days_factor > 1 但 clip 到 1.
    ref_days = _cfg("REF_DAYS")
    days_factor = min(1.0, max(0.40, (ref_days / max(days_to_resolution, 1.0)) ** 0.5))

    # longshot: p 低于阈值时线性减仓 (p=0 → 0.5x, p=THRESH → 1.0x).
    longshot_thresh = _cfg("LONGSHOT_THRESH")
    if p >= longshot_thresh:
        longshot_mult = 1.0
    else:
        longshot_mult = 0.5 + 0.5 * (p / longshot_thresh)

    size = raw * days_factor * longshot_mult

    # ---- Step 4: cluster + DD budget clip ----
    cluster_room = cluster_cap_usd - cluster_current_exposure_usd
    tier_dd = _tier_dd(stop_loss_tier)
    dd_budget_remain = _cfg("MONTHLY_DD_BUDGET") - exposed_dd_usd
    dd_cap = dd_budget_remain / tier_dd if tier_dd > 0 else float("inf")

    min_pos = _cfg("MIN_SINGLE_POS")
    if cluster_room < min_pos:
        return 0.0, f"cluster full (exp ${cluster_current_exposure_usd:.2f} >= cap ${cluster_cap_usd:.2f})"
    if dd_cap < min_pos:
        return 0.0, f"DD budget exhausted (exposed ${exposed_dd_usd:.2f} of ${_cfg('MONTHLY_DD_BUDGET')})"

    clipped = min(size, cluster_room, dd_cap)
    if clipped < min_pos:
        return 0.0, f"cap below ${min_pos:.0f} floor (clipped ${clipped:.2f})"

    # ---- Step 5: hard bound [MIN, MAX] ----
    max_pos = _cfg("MAX_SINGLE_POS")
    final = round(min(max_pos, max(min_pos, clipped)), 2)

    reason = (
        f"edge={edge*100:.1f}pp kelly_raw=${raw:.2f} days×{days_factor:.2f} "
        f"ls×{longshot_mult:.2f}→${size:.2f}; clip(cluster_room=${cluster_room:.2f}, "
        f"dd_cap=${dd_cap:.2f})→${clipped:.2f}; bound→${final:.2f}"
    )
    return final, reason
