#!/usr/bin/env python3
"""v5.10: 一次性回填 closed_positions.tag (老仓位).

新版 (>=v5.10) closed_positions 入场就带 tag (从 position_meta.tag 复制),
但老 closed_positions 是空的. 这个脚本扫 db, 对 tag IS NULL 的逐个调 Gamma
用 market_slug 查市场拿 tags 字段, 取第一个 (主 tag) 写回.

注意: Polymarket 的 tags 是一组多分类, 一个市场可能有几个 (Politics + Geopolitics + Trump 都有).
为了 analytics "按 tag 聚合胜率" 的清晰度, 这里只取第一个 (跟 scanner.py 命中逻辑接近).

用法:
    source .venv/bin/activate
    python3 scripts/backfill_closed_tag.py
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.gamma_client import install_polymarket_dns_guard, gamma_get, GammaError
install_polymarket_dns_guard()  # v5.10.2: 本机 DNS 污染兜底

from modules.db import get_conn, update_closed_tag
from modules.tags import TAGS

WHITELIST_LABELS = set(TAGS.keys())  # 我们 scanner 用的 22 个白名单标签


def _pick_best_tag(tag_array):
    """从 polymarket events.tags 数组里挑一个最有意义的 label.
    优先级 (高 → 低):
      1) 白名单内 tier=1 (Iran / Israel / Trump / Ukraine ...)
      2) 白名单内 tier=2
      3) 白名单内 tier=3
      4) 通用 tag (World / Politics / Geopolitics / Middle East)
      5) 第一个原始 label
    用 tier 排能拿到信号最强的标签 (按 scanner.py 已有的 tier 配置)."""
    if not tag_array:
        return None
    labels = []
    for t in tag_array:
        if isinstance(t, dict):
            l = t.get("label")
            if l:
                labels.append(l)
        elif isinstance(t, str):
            labels.append(t)
    if not labels:
        return None
    # 按 tier 分桶
    by_tier = {1: [], 2: [], 3: [], 4: []}  # 4 = 通用兜底桶
    generic = {"World", "Politics", "Geopolitics", "Middle East"}
    for l in labels:
        if l in WHITELIST_LABELS:
            if l in generic:
                by_tier[4].append(l)
            else:
                tier = TAGS[l].get("tier", 3)
                by_tier[tier].append(l)
    for t in (1, 2, 3, 4):
        if by_tier[t]:
            return by_tier[t][0]
    return labels[0]


def _try(path, params):
    """gamma_get 包一层: 失败打 warn 返回 None, 空 list 也返回 None."""
    try:
        d = gamma_get(path, params)
        return d if (d and isinstance(d, list)) else None
    except GammaError as e:
        print(f"  [warn] {path} {params} fail: {str(e)[:60]}")
        return None


def _tags_from_event_ref(ev_ref):
    """从 market 对象里的 events[0] 引用拿 tags. 有的直接内嵌 tags;
    没有就用 event id / event slug 二跳 (closed 市场要加 closed=true 再试)."""
    tags = ev_ref.get("tags")
    if tags:
        return _pick_best_tag(tags)
    eid = ev_ref.get("id")
    if eid:
        for extra in ({}, {"closed": "true"}):
            d = _try("/events", {"id": eid, "limit": 1, **extra})
            if d and d[0].get("tags"):
                return _pick_best_tag(d[0]["tags"])
    eslug = ev_ref.get("slug")
    if eslug:
        for extra in ({}, {"closed": "true"}):
            d = _try("/events", {"slug": eslug, "limit": 1, **extra})
            if d and d[0].get("tags"):
                return _pick_best_tag(d[0]["tags"])
    return None


def fetch_tag_for_slug(slug, token_id=None):
    """v5.10.2 重写. 关键认知 (同 resolution_check):
    Gamma /markets 与 /events **默认过滤掉 closed=true 的市场/事件**, 查已结算的必须
    显式带 closed=true 再查一遍; 软结算市场 (closed=False) 则裸查才命中 — 所以每档都查两态.
    多市场打包 event (选举类) 的 event slug ≠ market slug, 要走 markets → events[0] 跳查."""
    # A: events?slug=market_slug 直接命中 (单市场 event, slug 一致时)
    if slug:
        for extra in ({}, {"closed": "true"}):
            d = _try("/events", {"slug": slug, "limit": 1, **extra})
            if d and d[0].get("tags"):
                picked = _pick_best_tag(d[0]["tags"])
                if picked:
                    return picked
    # B/C: markets?slug / markets?clob_token_ids (各两态) → events[0] 跳查 tags
    market_queries = []
    if slug:
        market_queries += [{"slug": slug, "limit": 1},
                           {"slug": slug, "closed": "true", "limit": 1}]
    if token_id:
        market_queries += [{"clob_token_ids": token_id, "limit": 1},
                           {"clob_token_ids": token_id, "closed": "true", "limit": 1}]
    for q in market_queries:
        d = _try("/markets", q)
        if not d:
            continue
        evs = d[0].get("events") or []
        if not evs:
            continue
        picked = _tags_from_event_ref(evs[0])
        if picked:
            return picked
    return None


def main():
    conn = get_conn()
    # v5.10.2: '' 也算未记录 (v5.10 早期行); 按 token 去重 (update_closed_tag 按 token 覆盖所有行)
    rows = conn.execute(
        """SELECT token_id, MAX(market_slug) AS market_slug FROM closed_positions
           WHERE tag IS NULL OR tag='' GROUP BY token_id ORDER BY MAX(exit_at) DESC"""
    ).fetchall()
    conn.close()
    print(f"[backfill_tag] 共 {len(rows)} 个 token 的 tag 未记录.")
    if not rows:
        return
    updated = 0
    for r in rows:
        token_id = r["token_id"]
        slug = r["market_slug"]
        tag = fetch_tag_for_slug(slug, token_id=token_id)
        if tag:
            update_closed_tag(token_id, tag)
            updated += 1
            print(f"  ✓ {token_id[:18]} slug={slug[:50]} → tag='{tag}'")
        else:
            print(f"  ⚠ {token_id[:18]} slug={slug[:50]} → 拿不到 tag, 保持未记录")
        time.sleep(0.15)  # 礼貌一点, 别 hammer Gamma
    print(f"[backfill_tag] 完成: 更新 {updated} / {len(rows)} 个 token.")


if __name__ == "__main__":
    main()
