"""
动态热门标签发现 (suggest-only).

世界热点会搬家 (俄乌 → 伊朗 → 世界杯), 手写的 tags.py 白名单跟不上. 这个模块每天/按需
从 Gamma 按交易量汇总活跃 events 的 tag, 排除黑名单 + Polymarket 结构性 meta tag + 已盯的,
产出"今日热门标签"建议榜. 用户在 dashboard 一键纳入/退场/拉黑 (suggest-only, 不自动改扫描集).

设计 (用户 2026-06-22 拍板):
- 只建议, 不自动轮换 (board + 一键采纳).
- 固定核心 = 手写 tags.py 的 TAGS (永不退场, 保留 tier 提示词).
- 动态热门 = 用户采纳的, 存 data/dynamic_tags.json (跟 tags.py 分开, 扫描时合并).
- 按交易量门槛 (数量浮动), 默认 7天成交 > $1M 且 >= 2 个 event (防单巨鲸市场把整类拽进来).
- 严格走黑名单 (BLACKLIST_TAGS) + META_TAGS + 用户额外拉黑.
"""
import json
import os
import re
import time
from datetime import datetime, timezone

from modules.scanner import _s, GAMMA_API
from modules.tags import TAGS, BLACKLIST_TAGS

# Polymarket 上的结构性/促销 meta tag — 不是真主题, 永远过滤掉
META_TAGS = {
    "Hide From New", "Tournament Futures", "Main Election", "Earn 4%", "5M",
    "Pre-Market", "Recurring", "Yearly", "Monthly", "Weekly", "Daily",
    "Up or Down", "Hit Price", "All", "New", "Trending", "Featured", "Popular",
    "Live", "Today", "This Week",
}

# 体育/币价/天气类变体兜底关键词 (这些类别 AI 没研究 edge). BLACKLIST_TAGS 是精确匹配,
# 但 Polymarket 有 "2026 FIFA World Cup" 这种年份变体能绕过 "FIFA World Cup", 用关键词兜底.
_BLOCK_KEYWORDS = (
    "world cup", "soccer", "fifa", "nfl", "nba", "nhl", "mlb", "tennis", "golf",
    "cricket", "esports", "league of legends", "counter-strike", "dota",
    "bitcoin", "ethereum", "solana", "crypto", "weather", "hurricane", "temperature",
)
_YEAR_PREFIX = re.compile(r"^(?:19|20)\d{2}\s+")


def _is_blocked(label, blacklist):
    """精确黑名单 + 去年份前缀再查 + 体育/币价关键词兜底."""
    if label in blacklist:
        return True
    if _YEAR_PREFIX.sub("", label).strip() in blacklist:
        return True
    low = label.lower()
    return any(kw in low for kw in _BLOCK_KEYWORDS)


# 默认门槛 (可在 discover 时覆盖, 也会在 board 上显示给用户调)
DEFAULT_MIN_VOL_7D = 1_000_000   # 7天成交 > $1M 才算"够热"
DEFAULT_MIN_EVENTS = 2           # 至少 2 个 event, 防一个巨量市场把整类垃圾带进来
DEFAULT_MAX_EVENTS = 300         # 拉前 N 个高量 event 做汇总

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STORE = os.path.join(_BASE, "data", "dynamic_tags.json")
_CACHE = os.path.join(_BASE, "data", "tag_suggestions.json")


def _utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


def _load_store():
    try:
        with open(_STORE, encoding="utf-8") as f:
            d = json.load(f)
            d.setdefault("dynamic", [])
            d.setdefault("extra_blacklist", [])
            return d
    except Exception:
        return {"dynamic": [], "extra_blacklist": []}


def _save_store(d):
    os.makedirs(os.path.dirname(_STORE), exist_ok=True)
    tmp = _STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _STORE)


def get_dynamic_tags():
    """用户已采纳的动态标签 [{label, slug, tier, added_at}]."""
    return _load_store().get("dynamic", [])


def get_extra_blacklist():
    return set(_load_store().get("extra_blacklist", []))


def _fetch_top_events(max_events=DEFAULT_MAX_EVENTS):
    """拉交易量最高的活跃 events (按 24h 量倒序, 分页)."""
    out = []
    page = 100
    for offset in range(0, max_events, page):
        try:
            r = _s().get(f"{GAMMA_API}/events", params={
                "active": "true", "closed": "false",
                "order": "volume24hr", "ascending": "false",
                "limit": page, "offset": offset,
            }, timeout=30)
            data = r.json()
        except Exception:
            break
        if not isinstance(data, list) or not data:
            break
        out.extend(data)
        if len(data) < page:
            break
    return out


def discover_hot_tags(min_vol_7d=DEFAULT_MIN_VOL_7D, min_events=DEFAULT_MIN_EVENTS,
                      max_events=DEFAULT_MAX_EVENTS):
    """
    返回热门标签建议榜 (排除黑名单/meta, 按 7天成交量排序).
    {
      generated_at, min_vol_7d, min_events, event_sample,
      new:     [{label, slug, vol7d, vol24h, events, samples}]  # 新热门, 可纳入
      tracked: [...]  # 已盯 (固定核心或已采纳) 且仍达标, 展示用
      cold:    [{label, ...}]  # 动态标签里这次没达标的 → 建议退场
    }
    """
    events = _fetch_top_events(max_events)
    blacklist = set(BLACKLIST_TAGS) | META_TAGS | get_extra_blacklist()
    pinned = set(TAGS.keys())
    dynamic_labels = {d["label"] for d in get_dynamic_tags()}

    agg = {}
    for e in events:
        v7 = float(e.get("volume1wk") or 0)
        v24 = float(e.get("volume24hr") or 0)
        title = (e.get("title") or "")[:60]
        for t in (e.get("tags") or []):
            if not isinstance(t, dict):
                continue
            lab = t.get("label")
            if not lab:
                continue
            a = agg.setdefault(lab, {"slug": t.get("slug"), "vol7d": 0.0,
                                     "vol24h": 0.0, "events": 0, "samples": []})
            a["vol7d"] += v7
            a["vol24h"] += v24
            a["events"] += 1
            if len(a["samples"]) < 3 and title:
                a["samples"].append(title)

    rows = []
    for lab, a in agg.items():
        if _is_blocked(lab, blacklist):
            continue
        if a["vol7d"] < min_vol_7d or a["events"] < min_events:
            continue
        status = "pinned" if lab in pinned else ("dynamic" if lab in dynamic_labels else "new")
        rows.append({"label": lab, "slug": a["slug"], "vol7d": round(a["vol7d"]),
                     "vol24h": round(a["vol24h"]), "events": a["events"],
                     "samples": a["samples"], "status": status})
    rows.sort(key=lambda x: -x["vol7d"])

    hot_labels = {r["label"] for r in rows}
    cold = [d for d in get_dynamic_tags() if d["label"] not in hot_labels]

    return {
        "generated_at": _utcnow_iso(),
        "min_vol_7d": min_vol_7d,
        "min_events": min_events,
        "event_sample": len(events),
        "new": [r for r in rows if r["status"] == "new"],
        "tracked": [r for r in rows if r["status"] in ("pinned", "dynamic")],
        "cold": cold,
    }


def get_suggestions(max_age_sec=6 * 3600, force=False, **kw):
    """带缓存的建议榜 (避免每次 dashboard 都重算 + 打 Gamma). 超过 max_age 或 force 才重算."""
    if not force:
        try:
            with open(_CACHE, encoding="utf-8") as f:
                c = json.load(f)
            if time.time() - c.get("_cached_at", 0) < max_age_sec:
                return c
        except Exception:
            pass
    res = discover_hot_tags(**kw)
    res["_cached_at"] = time.time()
    try:
        os.makedirs(os.path.dirname(_CACHE), exist_ok=True)
        tmp = _CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
        os.replace(tmp, _CACHE)
    except Exception:
        pass
    return res


# ---- 采纳 / 退场 / 拉黑 (dashboard 按钮调) ----
def adopt_tag(label, slug=None, tier=2):
    """把建议的热门标签纳入动态盯防集."""
    d = _load_store()
    if any(x["label"] == label for x in d["dynamic"]):
        return False
    d["dynamic"].append({"label": label, "slug": slug, "tier": int(tier),
                         "added_at": _utcnow_iso()})
    _save_store(d)
    return True


def adopt_many(items):
    """批量纳入热门标签. items=[{label,slug,tier}]. 跳过已纳入的, 返回新增个数."""
    d = _load_store()
    have = {x["label"] for x in d["dynamic"]}
    added = 0
    for it in (items or []):
        lab = (it.get("label") or "").strip()
        if not lab or lab in have:
            continue
        d["dynamic"].append({"label": lab, "slug": (it.get("slug") or None),
                             "tier": int(it.get("tier", 2)), "added_at": _utcnow_iso()})
        have.add(lab)
        added += 1
    if added:
        _save_store(d)
    return added


def retire_tag(label):
    """把动态标签退场 (不影响 tags.py 固定核心)."""
    d = _load_store()
    before = len(d["dynamic"])
    d["dynamic"] = [x for x in d["dynamic"] if x["label"] != label]
    _save_store(d)
    return len(d["dynamic"]) < before


def retire_all_tags():
    """一键清空所有动态采纳标签 (不碰固定核心/黑名单). 返回清掉的个数."""
    d = _load_store()
    n = len(d["dynamic"])
    d["dynamic"] = []
    _save_store(d)
    return n


def blacklist_tag(label):
    """额外拉黑一个标签 (以后永不再建议), 顺便退场."""
    d = _load_store()
    if label not in d["extra_blacklist"]:
        d["extra_blacklist"].append(label)
    d["dynamic"] = [x for x in d["dynamic"] if x["label"] != label]
    _save_store(d)
    return True
