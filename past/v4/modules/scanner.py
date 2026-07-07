"""
Market Scanner — 从Polymarket API拉取结构化数据
输出markdown格式，直接喂给Claude Research
"""
import requests
import json
import logging
import time
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("scanner")

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

def _s():
    s = requests.Session()
    s.mount('https://', HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1)))
    return s


# === 硬性过滤规则（标准档）===
MIN_PRICE = 0.05           # 下限5%
MAX_PRICE = 0.92           # 上限92%
MIN_VOLUME_24H = 500       # 最低24h成交量$500
MIN_DAYS_TO_SETTLE = 5     # 结算至少5天
MAX_DAYS_TO_SETTLE = 30    # 结算最多30天
MAX_SPREAD_PCT = 0.05      # 最大价差5pp
BET_SIZE_USD = 0.75        # 单笔金额
MIN_DEPTH_USD = 3.0        # 订单簿深度≥$3
AMBIGUOUS_KEYWORDS = ["likely", "roughly", "approximately", "or equivalent", "substantial", "significant"]

# === 三档过滤配置 ===
# v4.1: 修订过滤参数 (基于研究文献 + 真实7仓数据回测)
# 改动:
#   1. 价格区间不对称: 低端宽 (longshot alpha 密集), 高端紧 (avoid 极端)
#   2. 用 volume1wk (7天) 替代 24h, 阈值放低 (24h 是噪音)
#   3. 模拟 $5 taker 滑点替代单边深度 (低价位市场失真)
#   4. 相对 spread (按价位分段) 替代绝对 spread
#   5. 结算窗口收紧, 1-3周 = 甜区
#   6. edge 阈值 8/11/14 (在 prompts.py 里)
FILTERS = {
    "standard": {
        "label": "标准扫描",
        "price_min": 0.08, "price_max": 0.92,    # 不对称 (低端宽)
        "vol_7d_min": 1500,                       # 7day成交 ≥ $1500
        "taker_slippage_max_pp": 1.5,             # $5 taker 滑点 ≤ 1.5pp
        "rel_spread_max": 0.08,                   # 相对 spread ≤ 8%
        "abs_spread_max_pp": 4.0,                 # 兜底: 绝对 spread ≤ 4pp
        "days_min": 7, "days_max": 21,            # 甜区 1-3周
    },
    "medium": {
        "label": "中范围扫描",
        "price_min": 0.05, "price_max": 0.95,
        "vol_7d_min": 500,
        "taker_slippage_max_pp": 3.0,
        "rel_spread_max": 0.15,
        "abs_spread_max_pp": 6.0,
        "days_min": 5, "days_max": 35,
    },
    "wide": {
        "label": "大范围扫描",
        "price_min": 0.03, "price_max": 0.97,    # 极端两端开放, 主要捕捉卖NO
        "vol_7d_min": 200,
        "taker_slippage_max_pp": 5.0,
        "rel_spread_max": 0.25,
        "abs_spread_max_pp": 10.0,
        "days_min": 3, "days_max": 60,
    },
}


def _check_settlement_date(end_date_str, cfg=None):
    """检查结算日是否在窗口内"""
    if cfg is None: cfg = FILTERS["standard"]
    if not end_date_str:
        return False, "无结算日"
    try:
        from datetime import datetime, timezone
        end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        days = (end_dt - now).days
        if days < cfg["days_min"]:
            return False, f"<{cfg['days_min']}天"
        if days > cfg["days_max"]:
            return False, f">{cfg['days_max']}天"
        return True, f"{days}天"
    except:
        return False, "日期错误"


def _check_ambiguous(description, question, cfg=None):
    """检查结算规则是否含歧义关键词. 三档都严格排除"""
    text = (description + " " + question).lower()
    for kw in AMBIGUOUS_KEYWORDS:
        if kw in text:
            return False, kw
    return True, ""


def fetch_markets(keyword=None, category=None, limit=500, cfg=None):
    """
    分页拉取所有活跃市场 + 应用硬性过滤。
    """
    stats = {"total": 0, "price": 0, "volume": 0, "keyword": 0, "date": 0, "ambiguous": 0, "passed": 0}
    all_markets = []
    
    try:
        # 分页拉取 (Gamma API限制单次500)
        offset = 0
        page_size = 500
        max_pages = 10  # 最多5000个
        for page in range(max_pages):
            params = {"active": "true", "closed": "false", "limit": page_size, "offset": offset, "order": "volume24hr", "ascending": "false"}
            page_resp = _s().get(f"{GAMMA_API}/markets", params=params, timeout=30).json()
            if not isinstance(page_resp, list):
                log.warning(f"Page {page} returned {type(page_resp)}")
                break
            if not page_resp:
                break
            all_markets.extend(page_resp)
            log.info(f"Page {page+1}: got {len(page_resp)} markets (total so far: {len(all_markets)})")
            if len(page_resp) < page_size:
                break
            offset += page_size
            time.sleep(0.3)  # rate limit
        
        resp = all_markets
        log.info(f"Total fetched: {len(resp)} markets across pages")
        
        stats["total"] = len(resp)
        filtered = []
        
        for m in resp:
            prices = m.get("outcomePrices", "")
            if isinstance(prices, str) and prices:
                try: prices = json.loads(prices)
                except: continue
            if not prices or len(prices) < 2:
                continue
            
            yes_price = float(prices[0])
            vol_24h = float(m.get("volume24hr", 0) or 0)
            vol_7d = float(m.get("volume1wk", 0) or 0)  # v4.1: 用7day替代24h
            question = m.get("question", "")
            slug = m.get("slug", "")
            desc = m.get("description", "") or ""
            end_date = m.get("endDate", "")
            
            # 关键词过滤
            if keyword:
                kw_lower = keyword.lower()
                if kw_lower not in question.lower() and kw_lower not in slug.lower() and kw_lower not in desc.lower():
                    stats["keyword"] += 1
                    continue
            
            # 1. 价格范围 8%-85%
            if not (cfg["price_min"] <= yes_price <= cfg["price_max"]):
                stats["price"] += 1
                continue
            
            # 2. v4.1: 7day 成交量过滤 (替代 24h, 24h 太噪音)
            if vol_7d < cfg["vol_7d_min"]:
                stats["volume"] += 1
                continue
            
            # 3. 结算日7-21天
            date_ok, date_info = _check_settlement_date(end_date, cfg)
            if not date_ok:
                stats["date"] += 1
                continue
            
            # 4. 结算规则不含歧义词
            amb_ok, amb_kw = _check_ambiguous(desc, question, cfg)
            if not amb_ok:
                stats["ambiguous"] += 1
                log.debug(f"Ambiguous ({amb_kw}): {question[:40]}")
                continue
            
            # tokens
            tids = m.get("clobTokenIds", "")
            if isinstance(tids, str) and tids:
                try: tids = json.loads(tids)
                except: tids = []
            
            filtered.append({
                "question": question,
                "slug": slug,
                "yes_price": yes_price,
                "no_price": float(prices[1]),
                "volume_24h": vol_24h,
                "volume_7d": vol_7d,
                "volume_total": float(m.get("volumeNum", 0) or 0),
                "end_date": end_date,
                "days_to_settle": date_info,
                "description": desc[:500],  # 多保留一些
                "description_len": len(desc),  # v4.1 (建议7)
                "token_yes": tids[0] if tids and len(tids) >= 1 else "",
                "token_no": tids[1] if tids and len(tids) >= 2 else "",
                "orderbook_ok": None,
                "rel_spread": None,
                "best_ask": None,
                "best_bid": None,
                "taker_slippage_pp": None,  # v4.1 (建议3)
            })
            stats["passed"] += 1
        
        log.info(f"Filter stats: total={stats['total']} kw_excluded={stats['keyword']} price={stats['price']} vol={stats['volume']} date={stats['date']} ambiguous={stats['ambiguous']} → passed={stats['passed']}")
        return filtered, stats
    
    except Exception as e:
        log.exception(f"fetch_markets failed: {e}")
        return [], stats


def check_orderbook_quality(market, clob_client=None, cfg=None):
    """
    v4.1: 订单簿质量检查 (建议3+4)
    - 相对 spread (按价位分段)
    - 模拟 $5 taker 滑点 (替代单边深度)
    """
    tid = market.get("token_yes") or market.get("token_no")
    if not tid:
        market["orderbook_ok"] = False
        return False
    
    try:
        if clob_client:
            book = clob_client.get_order_book(tid)
            # v4.1: 兼容 v1 (object .asks) 和 v2 SDK (dict ['asks'])
            if isinstance(book, dict):
                raw_asks = book.get("asks", [])
                raw_bids = book.get("bids", [])
            else:
                raw_asks = getattr(book, "asks", []) or []
                raw_bids = getattr(book, "bids", []) or []
            
            def _parse_level(x):
                # x 可能是 dict {"price":"0.5","size":"100"}, 或对象 .price/.size, 或 [price, size]
                if isinstance(x, dict):
                    return float(x.get("price", 0)), float(x.get("size", 0))
                if hasattr(x, "price"):
                    return float(x.price), float(x.size)
                if isinstance(x, (list, tuple)) and len(x) >= 2:
                    return float(x[0]), float(x[1])
                return 0.0, 0.0
            
            asks = [_parse_level(a) for a in raw_asks]
            bids = [_parse_level(b) for b in raw_bids]
        else:
            resp = _s().get(f"{CLOB_API}/book", params={"token_id": tid}, timeout=15).json()
            asks = [(float(a.get("price",0)), float(a.get("size",0))) for a in resp.get("asks",[])]
            bids = [(float(b.get("price",0)), float(b.get("size",0))) for b in resp.get("bids",[])]
        
        if not asks or not bids:
            market["orderbook_ok"] = False
            market["reject_reason"] = "无订单簿"
            return False
        
        asks_sorted = sorted(asks, key=lambda x: x[0])    # 低 -> 高
        bids_sorted = sorted(bids, key=lambda x: -x[0])   # 高 -> 低
        
        best_ask = asks_sorted[0][0]
        best_bid = bids_sorted[0][0]
        spread = best_ask - best_bid
        midprice = (best_ask + best_bid) / 2
        
        market["best_ask"] = best_ask
        market["best_bid"] = best_bid
        market["spread_abs"] = spread
        
        # === 检查1: 相对 spread (建议4) ===
        rel_spread = spread / midprice if midprice > 0 else 1.0
        market["rel_spread"] = rel_spread
        
        if rel_spread > cfg["rel_spread_max"]:
            market["orderbook_ok"] = False
            market["reject_reason"] = f"相对spread {rel_spread*100:.1f}%>{cfg['rel_spread_max']*100:.0f}%"
            return False
        
        # === 检查2: 绝对 spread 兜底 (避免极端中段市场过松) ===
        if spread * 100 > cfg["abs_spread_max_pp"]:
            market["orderbook_ok"] = False
            market["reject_reason"] = f"绝对spread {spread*100:.1f}pp>{cfg['abs_spread_max_pp']:.0f}pp"
            return False
        
        # === 检查3: 模拟 $5 taker 成交滑点 (建议3) ===
        # 从 best_ask 开始累计股数, 直到名义金额 >= $5
        target_usd = 5.0
        accumulated_usd = 0.0
        accumulated_shares = 0.0
        weighted_price_sum = 0.0
        
        for price, size in asks_sorted:
            level_usd = price * size
            if accumulated_usd + level_usd >= target_usd:
                # 该档只吃到目标
                remaining_usd = target_usd - accumulated_usd
                remaining_shares = remaining_usd / price
                weighted_price_sum += price * remaining_shares
                accumulated_shares += remaining_shares
                accumulated_usd = target_usd
                break
            weighted_price_sum += price * size
            accumulated_shares += size
            accumulated_usd += level_usd
        
        if accumulated_usd < target_usd:
            # 全部 ask 加起来都不够 $5, 极端缺流动性
            market["orderbook_ok"] = False
            market["reject_reason"] = f"ask 总深度 ${accumulated_usd:.2f} < $5"
            return False
        
        avg_taker_price = weighted_price_sum / accumulated_shares
        slippage_pp = (avg_taker_price - best_ask) * 100
        market["taker_slippage_pp"] = slippage_pp
        market["taker_avg_price"] = avg_taker_price
        
        if slippage_pp > cfg["taker_slippage_max_pp"]:
            market["orderbook_ok"] = False
            market["reject_reason"] = f"$5 taker 滑点 {slippage_pp:.1f}pp>{cfg['taker_slippage_max_pp']:.1f}pp"
            return False
        
        market["orderbook_ok"] = True
        return True
    
    except Exception as e:
        log.warning(f"Orderbook check failed: {e}")
        market["orderbook_ok"] = False
        market["reject_reason"] = "查询失败"
        return False


def fetch_events(keyword=None, min_volume=1000, max_price=0.15, min_price=0.01):
    """从Events API拉取，适合多选项市场（如世界杯小组赛）"""
    try:
        params = {"active": "true", "closed": "false", "limit": 50, "order": "volume24hr", "ascending": "false"}
        resp = _s().get(f"{GAMMA_API}/events", params=params, timeout=30).json()
        if not isinstance(resp, list):
            return []
        
        results = []
        for event in resp:
            title = event.get("title", "")
            if keyword and keyword.lower() not in title.lower():
                continue
            
            markets = event.get("markets", [])
            for m in markets:
                prices = m.get("outcomePrices", "")
                if isinstance(prices, str) and prices:
                    try: prices = json.loads(prices)
                    except: continue
                if not prices: continue
                
                yes_price = float(prices[0])
                vol = float(m.get("volume24hr", 0) or 0)
                
                if min_price <= yes_price <= max_price and vol >= min_volume:
                    tids = m.get("clobTokenIds", "")
                    if isinstance(tids, str) and tids:
                        try: tids = json.loads(tids)
                        except: tids = []
                    
                    results.append({
                        "event": title,
                        "question": m.get("question", ""),
                        "slug": m.get("slug", ""),
                        "yes_price": yes_price,
                        "no_price": float(prices[1]) if len(prices) > 1 else 0,
                        "volume_24h": vol,
                        "end_date": m.get("endDate", ""),
                        "description": m.get("description", "")[:200],
                        "token_yes": tids[0] if tids and len(tids) >= 1 else "",
                        "token_no": tids[1] if tids and len(tids) >= 2 else "",
                    })
        
        log.info(f"Events: {len(results)} markets match from {len(resp)} events")
        return results
    
    except Exception as e:
        log.exception(f"fetch_events failed: {e}")
        return []


def fetch_orderbook(token_id, clob_client=None):
    """获取订单簿深度"""
    try:
        if clob_client:
            book = clob_client.get_order_book(token_id)
            asks = getattr(book, 'asks', [])
            bids = getattr(book, 'bids', [])
            
            ask_list = []
            for a in asks[:5]:
                p = float(getattr(a, 'price', 0))
                s = float(getattr(a, 'size', 0))
                ask_list.append({"price": p, "size": s})
            
            bid_list = []
            for b in bids[:5]:
                p = float(getattr(b, 'price', 0))
                s = float(getattr(b, 'size', 0))
                bid_list.append({"price": p, "size": s})
            
            return {"asks": ask_list, "bids": bid_list}
        else:
            resp = _s().get(f"{CLOB_API}/book", params={"token_id": token_id}, timeout=15).json()
            return {
                "asks": [{"price": float(a.get("price",0)), "size": float(a.get("size",0))} for a in resp.get("asks",[])[:5]],
                "bids": [{"price": float(b.get("price",0)), "size": float(b.get("size",0))} for b in resp.get("bids",[])[:5]],
            }
    except Exception as e:
        log.warning(f"Orderbook failed for {token_id[:20]}: {e}")
        return {"asks": [], "bids": []}


def generate_report(markets, stats=None, cfg=None, domain_label=None, domain_hint=None):
    """
    生成markdown报告 — 只包含通过所有过滤的市场
    markets: 已经通过硬性过滤 + 订单簿检查的市场列表
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    if cfg is None: cfg = FILTERS["standard"]
    label = cfg.get("label", "")
    is_domain = domain_label is not None
    
    lines = []
    if label == "大范围扫描":
        lines.extend([
            "# ⚠️ 大范围扫描结果",
            "",
            "**重要**: 此模式放宽了硬过滤 (v4.1: 价格3%-97%, 周期3-60天, 7day成交≥$200, taker滑点≤5pp, 相对spread≤25%)。",
            "标的执行成本更高,Claude推荐门槛 **校准后 edge ≥ 14pp** 才推荐。",
            '低于此值一律说"无推荐"。',
            "",
            "---",
            "",
        ])
    elif label == "中范围扫描":
        lines.extend([
            "# 📊 中范围扫描结果",
            "",
            "**重要**: 此模式适度放宽硬过滤 (v4.1: 价格5%-95%, 周期5-35天, 7day成交≥$500, taker滑点≤3pp, 相对spread≤15%)。",
            "Claude推荐门槛 **校准后 edge ≥ 11pp** 才推荐。",
            '低于此值一律说"无推荐"。',
            "",
            "---",
            "",
        ])
    else:
        lines.extend([
            "# 标准扫描结果",
            "",
        ])
    
    # 领域扫描时附加领域信息
    if is_domain:
        lines.extend([
            f"## 🎯 领域: {domain_label}",
            "",
            "**研究提示**:",
            "",
            f"{domain_hint or '(无)'}",
            "",
            "---",
            "",
        ])
    
    lines.extend([
        f"扫描时间: {now} | 模式: **{cfg['label']}**",
        f"",
        f"## 已通过的硬性过滤",
        f"- 价格范围: {cfg['price_min']*100:.0f}%-{cfg['price_max']*100:.0f}%",
        f"- 7day 成交量: ≥ ${cfg['vol_7d_min']}",
        f"- 结算日: {cfg['days_min']}-{cfg['days_max']}天",
        f"- 相对 spread: ≤ {cfg['rel_spread_max']*100:.0f}%",
        f"- 绝对 spread 兜底: ≤ {cfg['abs_spread_max_pp']:.0f}pp",
        f"- $5 taker 滑点: ≤ {cfg['taker_slippage_max_pp']:.1f}pp",
        f"- 排除歧义结算规则",
        f"",
    ])
    
    if stats:
        lines.append(f"## 过滤漏斗")
        lines.append(f"- 总市场: {stats.get('total',0)}")
        lines.append(f"- 关键词不匹配: {stats.get('keyword',0)}")
        lines.append(f"- 价格不符: {stats.get('price',0)}")
        lines.append(f"- 成交量不足: {stats.get('volume',0)}")
        lines.append(f"- 结算日不符: {stats.get('date',0)}")
        lines.append(f"- 结算规则歧义: {stats.get('ambiguous',0)}")
        lines.append(f"- 订单簿不合格: {stats.get('orderbook',0)}")
        lines.append(f"- **最终合格**: {len(markets)}")
        lines.append(f"")
    
    lines.append(f"## 给Claude的任务")
    lines.append(f"以下 {len(markets)} 个市场已经通过资格筛选（流动性、结算日、价格范围）。")
    if label == "大范围扫描":
        threshold = "15pp"
    elif label == "中范围扫描":
        threshold = "12pp"
    else:
        threshold = "8pp"
    lines.append(f"请基于基本面分析和最新新闻,推荐校准后 edge ≥ {threshold} 的交易。")
    lines.append(f'如果没有明显edge，明确说"无推荐"。')
    lines.append(f"")
    lines.append(f"---")
    
    for i, m in enumerate(markets, 1):
        lines.append(f"")
        lines.append(f"## 市场 #{i}: {m['question']}")
        lines.append(f"- Slug: `{m['slug']}`")
        lines.append(f"- YES价格: {m['yes_price']:.1%}  |  NO价格: {m['no_price']:.1%}")
        lines.append(f"- 最低卖价: ${m.get('best_ask',0):.3f}  |  最高买价: ${m.get('best_bid',0):.3f}")
        rel_sp = m.get('rel_spread', 0) or 0
        slippage = m.get('taker_slippage_pp', 0) or 0
        lines.append(f"- 相对 spread: {rel_sp*100:.1f}% (绝对 {(m.get('spread_abs',0) or 0)*100:.1f}pp)")
        lines.append(f"- $5 taker 滑点: {slippage:.2f}pp")
        lines.append(f"- 7day 成交: ${m.get('volume_7d',0):,.0f}")
        # v4.1 (建议7): resolution 文本长度信号
        desc_len = m.get('description_len', 0)
        if desc_len < 200:
            lines.append(f"- ⚠️ resolution 简略 ({desc_len}字), 可能有歧义边界, 仔细读 description")
        elif desc_len > 800:
            lines.append(f"- ⚠️ resolution 复杂 ({desc_len}字), Claude 必须逐句读边界条件")
        lines.append(f"- 24h交易量: ${m['volume_24h']:,.0f}")
        lines.append(f"- 结算日: {m['end_date'][:10]} ({m.get('days_to_settle','')})")
        if m.get('event'):
            lines.append(f"- 所属事件: {m['event']}")
        if m.get('description'):
            lines.append(f"- 描述: {m['description']}")
        lines.append(f"")
        lines.append(f"---")
    
    return "\n".join(lines)


def scan_by_domain(domain_id, mode="standard", **kwargs):
    """
    领域扫描入口
    domain_id: 领域ID (见 modules/domains.py 的 DOMAINS)
    mode: standard / medium / wide
    """
    from modules.domains import DOMAINS, match_domain
    from modules.executor import Executor
    
    if domain_id not in DOMAINS:
        log.error(f"未知领域: {domain_id}")
        return f"# 错误\n\n未知领域 ID: {domain_id}\n\n可用领域: {', '.join(DOMAINS.keys())}"
    
    domain_cfg = DOMAINS[domain_id]
    cfg = FILTERS.get(mode, FILTERS["standard"])
    exe = Executor.get()
    clob = exe.client if exe else None
    
    log.info(f"Domain scan: {domain_id} ({domain_cfg['label']}) mode={mode}")
    
    # 第1步: 拉所有市场 (不带keyword, 拿全集)
    markets, stats = fetch_markets(keyword=None, cfg=cfg)
    log.info(f"After hard filters: {len(markets)} markets")
    
    # 第2步: 用领域逻辑筛选
    domain_filtered = [m for m in markets if match_domain(m, domain_cfg, mode)]
    stats["domain_filtered"] = len(markets) - len(domain_filtered)
    log.info(f"After domain match ({domain_id}): {len(domain_filtered)} markets")
    
    if not domain_filtered:
        return generate_report([], stats=stats, cfg=cfg, 
                               domain_label=domain_cfg["label"],
                               domain_hint=domain_cfg.get("research_hint", ""))
    
    # 第3步: 订单簿检查
    passed = []
    rejected = 0
    for m in domain_filtered[:50]:
        time.sleep(0.5)
        if check_orderbook_quality(m, clob, cfg=cfg):
            passed.append(m)
        else:
            rejected += 1
    stats["orderbook"] = rejected
    
    # 第4步: 排序 + 生成报告
    passed.sort(key=lambda x: x["volume_24h"], reverse=True)
    return generate_report(passed[:20], stats=stats, cfg=cfg,
                           domain_label=domain_cfg["label"],
                           domain_hint=domain_cfg.get("research_hint", ""))


def fetch_events_by_tag(tag_slug, limit=300):
    """通过 Gamma API 用 tag_slug 拉该 tag 下的 events (含 markets)"""
    import requests
    s = _s()
    all_events = []
    for offset in range(0, limit, 100):
        try:
            r = s.get(f"{GAMMA_API}/events",
                      params={"tag_slug": tag_slug, "active": "true", "closed": "false",
                              "limit": 100, "offset": offset},
                      timeout=15)
            if r.status_code != 200:
                log.warning(f"fetch_events_by_tag slug={tag_slug} status={r.status_code}")
                break
            evs = r.json()
            if not evs: break
            all_events.extend(evs)
            if len(evs) < 100: break
            time.sleep(0.3)
        except Exception as e:
            log.warning(f"fetch_events_by_tag err: {e}")
            break
    return all_events


def scan_by_tag(tag_label, mode="standard", **kwargs):
    """
    Tag 扫描入口.
    流程: /events?tag=X → 拉所有markets → 黑名单排除 → 硬过滤 → 订单簿检查 → 按event分组生成报告
    """
    from modules.tags import TAGS, get_tag_hint, is_blacklisted
    from modules.executor import Executor
    
    if tag_label not in TAGS:
        log.error(f"未知 tag: {tag_label}")
        return f"# 错误\n\n未知 tag: {tag_label}\n\n可用 tag 见 modules/tags.py"
    
    tag_cfg = TAGS[tag_label]
    cfg = FILTERS.get(mode, FILTERS["standard"])
    exe = Executor.get()
    clob = exe.client if exe else None
    
    log.info(f"Tag scan: {tag_label} (Tier {tag_cfg['tier']}) mode={mode}")
    
    # 1. 拉该tag下的events
    tag_slug = tag_cfg.get("slug", tag_label.lower().replace(" ", "-"))
    events = fetch_events_by_tag(tag_slug, limit=300)
    log.info(f"拉到 {len(events)} events")
    
    # 2. 提取所有markets, 用 event_id 关联回去
    markets_by_event = {}  # event_id → {event, markets:[]}
    for ev in events:
        ev_markets = ev.get("markets", []) or []
        for m in ev_markets:
            # 黑名单过滤
            if is_blacklisted(m, ev):
                continue
            # 提取 outcomePrices
            prices = m.get("outcomePrices", "")
            if isinstance(prices, str) and prices:
                try:
                    import json as _json
                    prices = _json.loads(prices)
                except:
                    continue
            if not prices or len(prices) < 2:
                continue
            
            yes_price = float(prices[0])
            
            # token ids
            tids = m.get("clobTokenIds", "")
            if isinstance(tids, str) and tids:
                try:
                    import json as _json
                    tids = _json.loads(tids)
                except:
                    tids = []
            
            mkt_obj = {
                "question": m.get("question", ""),
                "slug": m.get("slug", ""),
                "yes_price": yes_price,
                "no_price": float(prices[1]),
                "volume_24h": float(m.get("volume24hr", 0) or 0),
                "volume_7d": float(m.get("volume1wk", 0) or 0),  # v4.1
                "volume_total": float(m.get("volumeNum", 0) or 0),
                "description_len": len(m.get("description", "") or ""),  # v4.1 (建议7)
                "end_date": m.get("endDate", ""),
                "days_to_settle": "",
                "description": (m.get("description", "") or "")[:300],
                "token_yes": tids[0] if tids and len(tids) >= 1 else "",
                "token_no": tids[1] if tids and len(tids) >= 2 else "",
                "orderbook_ok": None,
                "rel_spread": None,
                "best_ask": None,
                "taker_slippage_pp": None,
                "event_title": ev.get("title", ""),
                "event_id": ev.get("id", ""),
            }
            
            ev_id = ev.get("id", "?")
            markets_by_event.setdefault(ev_id, {
                "event_title": ev.get("title", ""),
                "event_slug": ev.get("slug", ""),
                "markets": []
            })
            markets_by_event[ev_id]["markets"].append(mkt_obj)
    
    # 3. 应用硬过滤 (价格/成交量/结算日/歧义)
    stats = {"raw_markets": sum(len(g["markets"]) for g in markets_by_event.values()),
             "blacklist": 0, "price": 0, "volume": 0, "date": 0,
             "ambiguous": 0, "orderbook": 0, "passed": 0}
    
    filtered_by_event = {}
    for ev_id, group in markets_by_event.items():
        kept = []
        for m in group["markets"]:
            yp = m["yes_price"]
            if not (cfg["price_min"] <= yp <= cfg["price_max"]):
                stats["price"] += 1
                continue
            # v4.1: 用真实 7day 成交
            if m.get("volume_7d", 0) < cfg["vol_7d_min"]:
                stats["volume"] += 1
                continue
            date_ok, date_info = _check_settlement_date(m["end_date"], cfg)
            if not date_ok:
                stats["date"] += 1
                continue
            m["days_to_settle"] = date_info
            amb_ok, _ = _check_ambiguous(m["description"], m["question"], cfg)
            if not amb_ok:
                stats["ambiguous"] += 1
                continue
            kept.append(m)
            stats["passed"] += 1
        if kept:
            filtered_by_event[ev_id] = {
                "event_title": group["event_title"],
                "event_slug": group["event_slug"],
                "markets": kept,
            }
    
    log.info(f"硬过滤后: {stats['passed']} markets, {len(filtered_by_event)} events")
    
    # 4. 订单簿检查
    rejected = 0
    final_by_event = {}
    checked = 0
    for ev_id, group in filtered_by_event.items():
        kept = []
        for m in group["markets"]:
            if checked >= 50:
                break
            checked += 1
            time.sleep(0.5)
            if check_orderbook_quality(m, clob, cfg=cfg):
                kept.append(m)
            else:
                rejected += 1
        if kept:
            final_by_event[ev_id] = {
                "event_title": group["event_title"],
                "event_slug": group["event_slug"],
                "markets": kept,
            }
        if checked >= 50:
            break
    stats["orderbook"] = rejected
    
    log.info(f"订单簿后: {sum(len(g['markets']) for g in final_by_event.values())} markets in {len(final_by_event)} events")
    
    # 5. 生成报告 (按event分组)
    return generate_tag_report(tag_label, tag_cfg, final_by_event, stats, cfg)


def generate_tag_report(tag_label, tag_cfg, events_dict, stats, cfg):
    """Tag扫描专用报告生成器: 按event分组展示"""
    from modules.tags import get_tag_hint
    
    tier = tag_cfg["tier"]
    label = cfg.get("label", "")
    threshold = {"标准扫描": "8pp", "中范围扫描": "11pp", "大范围扫描": "14pp"}.get(label, "8pp")  # v4.1 (建议6)
    
    lines = []
    
    # 头部
    if tier == 3:
        lines.append(f"# 🎭 Tag 扫描结果 - 反向操作")
    else:
        lines.append(f"# 🏷️  Tag 扫描结果")
    lines.append("")
    lines.append(f"## 标签: **{tag_label}** (Tier {tier})  |  范围: **{label}**")
    lines.append("")
    lines.append(f"**研究提示**:")
    lines.append("")
    lines.append(get_tag_hint(tag_label))
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # 统计
    total_markets = sum(len(g["markets"]) for g in events_dict.values())
    lines.append(f"## 扫描统计")
    lines.append(f"- 原始 markets: {stats['raw_markets']}")
    lines.append(f"- 价格不符: {stats.get('price', 0)}")
    lines.append(f"- 成交量不足: {stats.get('volume', 0)}")
    lines.append(f"- 结算日不符: {stats.get('date', 0)}")
    lines.append(f"- 结算规则歧义: {stats.get('ambiguous', 0)}")
    lines.append(f"- 订单簿不合格: {stats.get('orderbook', 0)}")
    lines.append(f"- **最终合格**: {total_markets} markets in {len(events_dict)} events")
    lines.append("")
    
    if total_markets == 0:
        lines.append("## 结果")
        lines.append("")
        lines.append("无市场通过过滤。建议:")
        lines.append("- 切换到中范围或大范围扫描")
        lines.append("- 或选择其他 tag")
        return "\n".join(lines)
    
    # 推荐门槛说明
    lines.append(f"## 给Claude的任务")
    lines.append(f"")
    lines.append(f"以下市场已经通过资格筛选(流动性、结算日、价格范围)。")
    lines.append(f"请基于基本面分析和最新新闻,推荐**校准后 edge ≥ {threshold}** 的交易。")
    if tier == 3:
        lines.append(f"")
        lines.append(f"⚠️ **此 tag 是反向操作类**。优先评估 **卖 NO**,而不是买 YES。")
    lines.append(f"")
    lines.append(f"**Cluster 规则**: 同一 Event 下多个 markets,只推 edge 最大的 1 个。")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # 按 event 分组展示
    for i, (ev_id, group) in enumerate(events_dict.items(), 1):
        lines.append(f"## Event #{i}: {group['event_title']}")
        if group.get("event_slug"):
            lines.append(f"- Event Slug: `{group['event_slug']}`")
        lines.append(f"- 该 Event 下 {len(group['markets'])} 个 markets:")
        lines.append("")
        
        for j, m in enumerate(group["markets"], 1):
            lines.append(f"### 市场 {i}.{j}: {m['question']}")
            lines.append(f"- Slug: `{m['slug']}`")
            lines.append(f"- YES价格: {m['yes_price']:.1%}  |  NO价格: {m['no_price']:.1%}")
            lines.append(f"- 最低卖价: ${m.get('best_ask',0):.3f}  |  最高买价: ${m.get('best_bid',0):.3f}")
            rel_sp = m.get('rel_spread', 0) or 0
            slippage = m.get('taker_slippage_pp', 0) or 0
            lines.append(f"- 相对 spread: {rel_sp*100:.1f}% (绝对 {(m.get('spread_abs',0) or 0)*100:.1f}pp)")
            lines.append(f"- $5 taker 滑点: {slippage:.2f}pp")
            lines.append(f"- 7day 成交: ${m.get('volume_7d',0):,.0f}")
            desc_len = m.get('description_len', 0)
            if desc_len < 200:
                lines.append(f"- ⚠️ resolution 简略 ({desc_len}字), 可能有歧义边界")
            elif desc_len > 800:
                lines.append(f"- ⚠️ resolution 复杂 ({desc_len}字), 必须逐句读边界条件")
            lines.append(f"- 24h交易量: ${m['volume_24h']:,.0f}  |  累计: ${m['volume_total']:,.0f}")
            lines.append(f"- 结算日: {m['end_date'][:10]} ({m['days_to_settle']})")
            if m.get("description"):
                lines.append(f"- 描述: {m['description'][:200]}")
            lines.append("")
        
        lines.append("---")
        lines.append("")
    
    return "\n".join(lines)


def scan_and_report(keyword=None, category=None, mode="standard", **kwargs):
    """
    一键扫描+生成报告
    mode: "standard" 标准扫描 / "wide" 大范围扫描
    """
    from modules.executor import Executor
    exe = Executor.get()
    clob = exe.client if exe else None
    
    cfg = FILTERS.get(mode, FILTERS["standard"])
    log.info(f"Scan starting: keyword={keyword or 'all'} mode={mode} ({cfg['label']})")
    
    # Step 1: 硬性过滤（价格、成交量、结算日、歧义）
    markets, stats = fetch_markets(keyword=keyword, cfg=cfg)
    
    if not markets:
        return generate_report([], stats=stats, cfg=cfg)
    
    log.info(f"After hard filters: {len(markets)} markets, checking orderbooks...")
    
    # Step 2: 订单簿检查（spread + depth）
    passed_orderbook = []
    orderbook_rejected = 0
    for i, m in enumerate(markets[:50]):  # 最多检查50个，避免rate limit
        time.sleep(0.5)  # 避免打爆API
        if check_orderbook_quality(m, clob, cfg=cfg):
            passed_orderbook.append(m)
        else:
            orderbook_rejected += 1
            log.debug(f"Orderbook reject: {m['question'][:30]} - {m.get('reject_reason','?')}")
    
    stats["orderbook"] = orderbook_rejected
    
    # Step 3: 按成交量排序
    passed_orderbook.sort(key=lambda x: x["volume_24h"], reverse=True)
    
    report = generate_report(passed_orderbook[:20], stats=stats, cfg=cfg)
    log.info(f"Final: {len(passed_orderbook)} qualified markets, {len(report)} chars")
    return report
