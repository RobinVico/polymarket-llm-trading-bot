"""
v5.10: Polymarket 市场 resolution 状态检查器.

设计目标: 把 closed_positions 表里所有 is_resolved=0 的行, 一个个查 Polymarket Gamma
看市场是否结算 (closed=true + outcomePrices ∈ {[1,0], [0,1]}), 把 final_outcome 写回 db.
这样 /history 页面才能区分"已卖但未结算 (进行中)"和"已结算 (可以算赌对没)".

调用入口:
- monitor.py 心跳每 N 轮 (默认 20 = 1 小时) 跑一次 update_unresolved_closed_positions(limit=50).
- scripts/backfill_closed_resolution.py 一次性回填老 closed_positions.

不会阻塞主决策, 失败 log.warning 跳过, 下次再来.
"""
import json
import logging

from modules.gamma_client import gamma_get, GammaError

log = logging.getLogger("resolution_check")


def _parse_maybe_json_array(raw):
    """Gamma 有时返回 JSON 字符串, 有时直接是 list. 统一成 list."""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
    if isinstance(raw, list):
        return raw
    return []


def _try_markets(params, token_id):
    """查 /markets, 命中且 token_id 在 clobTokenIds 里才算数 (slug 查询防串市场)."""
    try:
        data = gamma_get("/markets", params)
    except GammaError as e:
        log.debug(f"markets {params} fail: {e}")
        return None
    if not data or not isinstance(data, list):
        return None
    m = data[0]
    clob_ids = _parse_maybe_json_array(m.get("clobTokenIds"))
    if token_id in clob_ids:
        return m
    return None


def _fetch_market_any(token_id, market_slug):
    """v5.10.2 关键修复: Gamma /markets **默认过滤掉 closed=true 的市场** —
    而 resolution 检查找的恰恰就是已关闭市场, 所以旧版查询永远 EMPTY, updated=0.
    (之前 13 行能结算全靠"closed=False 但价格 ≥0.99"的软结算路径侥幸捞到.)

    查询链 (命中即停):
      1. markets?clob_token_ids+closed=true   ← 正式结算的主路径
      2. markets?clob_token_ids               ← 还开着的市场 (软结算判定用)
      3. markets?slug+closed=true             ← token 索引被清但 slug 还在
      4. markets?slug
      5. events?slug 反查                      ← 最后兜底
    """
    chain = [{"clob_token_ids": token_id, "closed": "true", "limit": 1},
             {"clob_token_ids": token_id, "limit": 1}]
    if market_slug:
        chain += [{"slug": market_slug, "closed": "true", "limit": 1},
                  {"slug": market_slug, "limit": 1}]
    for params in chain:
        m = _try_markets(params, token_id)
        if m is not None:
            return m
    return _fetch_via_events_fallback(market_slug, token_id)


def _fetch_via_events_fallback(market_slug, token_id):
    """fallback: 老 archived 市场 markets?clob_token_ids 返回空, 但 events?slug 仍能拿到.
    返回 markets[] 子数组里 clobTokenIds 含 token_id 的那个 market dict, 或 None.
    注意: market_slug 是市场 slug, 对"多市场打包 event" (例: 韩国地方选举多候选人)
    events?slug 会查不到 — 所以前面还有 markets?slug 这一档."""
    if not market_slug:
        return None
    try:
        data = gamma_get("/events", {"slug": market_slug, "limit": 1})
    except GammaError as e:
        log.debug(f"events fallback fetch fail for slug={market_slug[:40]}: {e}")
        return None
    if not data or not isinstance(data, list):
        return None
    for ev in data:
        for m in ev.get("markets") or []:
            clob_ids = _parse_maybe_json_array(m.get("clobTokenIds"))
            if token_id in clob_ids:
                return m
    return None


def check_resolution(token_id, side=None, market_slug=None):
    """查 token_id 对应市场是否已结算.

    返回:
        {"final_outcome": 0.0 or 1.0, "resolved_at": ISO} - 已结算, final_outcome 是 "你持有的 side 那一边" 的最终概率
        None - 还在进行中, 或查询失败

    final_outcome 语义 (关键!):
        我们存的 final_outcome 是**持有 side 的最终概率**, 不是 "Yes 的最终概率".
        Yes 仓位 + Yes 中 → final_outcome=1 (赌赢了)
        Yes 仓位 + No 中 → final_outcome=0 (赌输了)
        No 仓位 + No 中  → final_outcome=1 (赌赢了, 我们手里的 No token 兑现到 1.0)
        No 仓位 + Yes 中 → final_outcome=0 (赌输了)
        这样 is_correct = (final_outcome >= 0.5) 永远对 (调用方 db.update_closed_resolution 已实现).

    v5.10: 加 events fallback (老 archived 市场 markets endpoint 返回空, events 仍有).
    v5.10.2: 改用 gamma_client (DNS 污染 DoH 兜底) + 五档查询链 (见 _fetch_market_any).
             核心修复: Gamma /markets 默认不返回 closed 市场, 必须显式带 closed=true 查.
    """
    m = _fetch_market_any(token_id, market_slug)
    if m is None:
        log.debug(f"check_resolution: empty response for {token_id[:20]} (五档查询链全空)")
        return None
    prices = [float(p) for p in _parse_maybe_json_array(m.get("outcomePrices"))]
    clob_ids = _parse_maybe_json_array(m.get("clobTokenIds"))
    if len(prices) < 2:
        return None  # 数据不完整

    # 解析持有 outcome 的 index (优先 clobTokenIds.index, fallback side 推断)
    idx = None
    if token_id in clob_ids:
        idx = clob_ids.index(token_id)
    elif side:
        idx = 0 if side.upper() in ("YES",) else 1
    if idx is None or idx >= len(prices):
        log.warning(f"check_resolution: can't determine index for {token_id[:20]} side={side}")
        return None

    final_outcome = prices[idx]
    closed_flag = bool(m.get("closed"))

    # 两条判定路径都能视为已结算:
    # (1) closed=True + outcome 接近 0 或 1 → 正式结算 (主流)
    # (2) closed=False 但 outcome ≥0.99 或 ≤0.01 → 软结算 (老 Polymarket 市场常出现:
    #     市场已经事实上一边倒, 但官方 `closed` flag 没翻. backfill 时这种最多).
    # 中间值 (0.02-0.98) 一律视为未结算 (真的还在波动).
    decisive = (final_outcome >= 0.99 or final_outcome <= 0.01)
    if not decisive:
        return None
    if not closed_flag:
        log.debug(f"check_resolution: soft-resolved (closed=False but outcome={final_outcome}) for {token_id[:20]}")

    resolved_at = m.get("endDate") or m.get("closeTime") or ""
    return {"final_outcome": round(final_outcome), "resolved_at": resolved_at}


def update_unresolved_closed_positions(limit=50):
    """扫 db 里 is_resolved=0 的 closed_positions, 逐个查 Gamma 更新.

    返回 (checked_count, updated_count).
    被设计成幂等的: 每行能被多次调用, 已是 is_resolved=1 的不会再 UPDATE (WHERE is_resolved=0 守卫).
    """
    from modules.db import get_unresolved_closed_positions, update_closed_resolution

    rows = get_unresolved_closed_positions(limit=limit)
    checked = 0
    updated = 0
    for r in rows:
        token_id = r["token_id"]
        side = r["side"]
        slug = r.get("market_slug")
        result = check_resolution(token_id, side=side, market_slug=slug)
        checked += 1
        if result:
            n = update_closed_resolution(
                token_id=token_id,
                final_outcome=result["final_outcome"],
                resolved_at_iso=result["resolved_at"],
                side=side,
            )
            if n > 0:
                updated += 1
                log.info(
                    f"resolution: {token_id[:20]} side={side} → outcome={result['final_outcome']} "
                    f"(slug={r.get('market_slug','')})"
                )
    return checked, updated
