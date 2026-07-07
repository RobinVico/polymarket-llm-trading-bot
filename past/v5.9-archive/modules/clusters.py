"""
v5.9: Cluster (相关性簇) helpers.

Cluster 不是按话题分, 是按相关性分:
  "如果此仓位赢了, 其他哪些会一起赢? 一起赢 → 同 cluster"

命名约定: <topic>-<direction> kebab-case (例 "iran-deescalation-no").

Claude SKILL 端强制输出标准 kebab-case, 用户复制粘贴的就是 canonical, 后台不需要 alias 字典.

⚠️ 所有 exposure / drawdown 计算使用现价口径 (cur_price × size),
   跟 bankroll (= cash + Σ cur_price × size) 一致, 避免成本/现价混用导致的边界 bug.
"""
import logging
from modules.executor import Executor
from modules.db import get_position_meta

log = logging.getLogger("clusters")

# 同步 modules.sizing.SIZING_CFG["TIER_DD"]. 这里独立保留一份以避免 circular import,
# 修改时两边都要改 (TIER_DD 是少数会被两个模块都访问的常量).
TIER_DD = {
    "convergent": 0.20,
    "hybrid": 0.35,
    "event_driven": 0.70,
}


def _iter_live_positions_with_meta():
    """yield (pos_dict, meta_dict) for every active position.
    pos_dict 含 cur_price + size + asset (来自 polymarket data-api).
    meta_dict 含 cluster_id + stop_loss_tier 等 (来自 db).
    """
    try:
        exe = Executor.get()
        positions = exe.get_positions() or []
    except Exception as e:
        log.warning(f"_iter_live_positions: get_positions failed: {e}")
        return
    for p in positions:
        meta = get_position_meta(p.get("asset")) or {}
        yield p, meta


def cluster_exposure_usd(cluster_id):
    """Σ (cur_price × size) for active positions whose meta.cluster_id == cluster_id.

    现价口径 (跟 bankroll 一致), 不是 cost_basis.
    cluster_id 为 None / 空 串 时 return 0 (不匹配任何仓位).
    """
    if not cluster_id:
        return 0.0
    total = 0.0
    for p, meta in _iter_live_positions_with_meta():
        if meta.get("cluster_id") == cluster_id:
            total += (p.get("cur_price") or 0) * (p.get("size") or 0)
    return total


def portfolio_exposed_dd_usd():
    """Σ (cur_price × size) × TIER_DD[tier] — 现价口径 expected drawdown.

    tier 缺失 (NULL meta) fallback 为 hybrid 0.35 (保守中位).
    """
    total = 0.0
    for p, meta in _iter_live_positions_with_meta():
        tier = meta.get("stop_loss_tier") or "hybrid"
        if tier not in TIER_DD:
            tier = "hybrid"
        cur_value = (p.get("cur_price") or 0) * (p.get("size") or 0)
        total += cur_value * TIER_DD[tier]
    return total


def list_active_clusters():
    """Return [(cluster_id, exposure_usd, count), ...] sorted by exposure desc.

    Used by dashboard UI dropdown / cluster analysis tab.
    NULL / empty cluster_id positions are grouped under '(uncategorized)'.
    """
    by_cluster = {}  # cluster_id -> (exposure_sum, count)
    for p, meta in _iter_live_positions_with_meta():
        cid = meta.get("cluster_id") or "(uncategorized)"
        exp = (p.get("cur_price") or 0) * (p.get("size") or 0)
        cur_exp, cur_count = by_cluster.get(cid, (0.0, 0))
        by_cluster[cid] = (cur_exp + exp, cur_count + 1)
    return sorted(
        [(cid, exp, count) for cid, (exp, count) in by_cluster.items()],
        key=lambda x: -x[1]
    )


def get_cluster_dict_for_prompt():
    """v5.9: 把当前所有 cluster 状态拼成 markdown 表, 供 DISCOVERY / cluster-analyzer prompt 自动嵌入.

    目的: Claude 看到字典后, 给新仓 cluster_id 时优先复用现有 slug, 而不是创类似新名
    (例如不会创 'iran-no-deescalation' 当已有 'iran-deescalation-no').

    返回: markdown 字符串. 如果没有任何已分类仓位, 返回友好提示.
    """
    clusters = list_active_clusters()
    # 过滤掉 (uncategorized)
    real = [(cid, exp, count) for cid, exp, count in clusters if cid != "(uncategorized)"]
    if not real:
        return (
            "## 我现有的 cluster 字典\n\n"
            "(暂无已分类的 cluster — 你可以自由创新 slug, 但仍要符合命名约定 `<topic>-<direction>`.)\n"
        )
    lines = [
        "## 我现有的 cluster 字典 (优先复用, 不要创类似新名)",
        "",
        "| cluster_id | 现价暴露 USD | 仓数 |",
        "|---|---|---|",
    ]
    for cid, exp, count in real:
        lines.append(f"| {cid} | ${exp:.2f} | {count} |")
    lines.extend([
        "",
        "**规则**:",
        "- 新候选如果跟以上某个 cluster 同方向 (一起赢一起输), **必须复用同 slug**, 不创新名.",
        "- 例如已有 `iran-deescalation-no`, 新伊朗 No 仓应继续用这个, 不创 `iran-anti-escalation-no` / `iran-no-thaw` 等近义词.",
        "- 只有跟所有现有 cluster 都不相关时, 才创新 slug.",
    ])
    return "\n".join(lines)


def bankroll_usd():
    """Total bankroll = cash + Σ (cur_price × size) for active positions.

    Returns None on cash API failure (caller must handle).
    """
    try:
        exe = Executor.get()
        cash = exe.get_cash_balance()
        if cash is None:
            return None
        positions_value = 0.0
        for p in (exe.get_positions() or []):
            positions_value += (p.get("cur_price") or 0) * (p.get("size") or 0)
        return cash + positions_value
    except Exception as e:
        log.warning(f"bankroll_usd failed: {e}")
        return None
