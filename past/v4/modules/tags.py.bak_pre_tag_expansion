"""
Polymarket Tag 扫描配置 - 25个白名单tag + 黑名单
所有 tag label 与 Polymarket Gamma API 实际返回完全一致
"""

# 通用研究提示 (按Tier)
TIER1_HINT = "外文一手资料 + 政府/议会原始文件,逐字读 resolution 规则的边界条件。"
TIER2_HINT = "读官方公告、政策文本、benchmark 数据,警惕内幕抢跑。"
TIER3_HINT = "此类别系统性过度自信,优先评估卖 NO。新闻反而损害准确率,优先用日历型事实(提名名单、过去得奖统计)。"


# 白名单: tag_label → {tier, hint_extra}
# tier: 1/2/3
# hint_extra: 该tag的专项提示(在通用Tier提示之上追加)
TAGS = {
    # ===== Tier 1 重点扫 (11个) =====
    "Iran": {
        "tier": 1,
        "hint_extra": "波斯语和希伯来语一手资料是 alpha 来源。注意 'halt' vs 'suspend' vs 'reduce' 在 resolution 里的区别。",
        "slug": "iran",
    },
    "Israel": {
        "tier": 1,
        "hint_extra": "区分 IDF 官方声明 vs 媒体报道。注意 'attack' 的定义边界(直接打击 vs 代理人 vs 网络战)。",
        "slug": "israel",
    },
    "Ukraine": {
        "tier": 1,
        "hint_extra": "区分 '框架性协议' vs '签署协议' vs '实际停火'。Trump-Zelensky 矿产协议案例是经典陷阱。",
        "slug": "ukraine",
    },
    "Ukraine Peace Deal": {
        "tier": 1,
        "hint_extra": "停火协议的法律措辞决定 resolution。同 Ukraine 标的可能 cluster,优先取 edge 最大者。",
        "slug": "ukraine-peace-deal",
    },
    "Russia": {
        "tier": 1,
        "hint_extra": "与 Ukraine 标的常 cluster。Putin 公开声明 vs 实际行动差距大,看克里姆林宫官方文件。",
        "slug": "russia",
    },
    "China": {
        "tier": 1,
        "hint_extra": "中文一手资料(人民日报、新华社)是关键。退役解放军智库分析是先行指标。",
        "slug": "china",
    },
    "Taiwan": {
        "tier": 1,
        "hint_extra": "台湾联合报、台军智库优先。区分军演 vs 入侵 vs 封锁的定义边界。",
        "slug": "taiwan",
    },
    "Geopolitics": {
        "tier": 1,
        "hint_extra": "范围最广,容易混入多议题。请按 event 分组,在每组内 cluster。",
        "slug": "geopolitics",
    },
    "Middle East": {
        "tier": 1,
        "hint_extra": "覆盖 Iran/Israel/Saudi 等。注意 '代理人冲突' vs '直接打击' 的边界。",
        "slug": "middle-east",
    },
    "World": {
        "tier": 1,
        "hint_extra": "范围最大,可能含噪声。优先看高成交量 events。",
        "slug": "world",
    },
    "Foreign Policy": {
        "tier": 1,
        "hint_extra": "美国国务院声明、白宫读出文本是核心。区分 '宣布' vs '签署' vs '生效'。",
        "slug": "foreign-policy",
    },
    
    # ===== Tier 2 中等优势 (10个) =====
    "Trump": {
        "tier": 2,
        "hint_extra": "范围广,涵盖cabinet/政策/言论。请按 event 分组,优先 cluster。",
        "slug": "trump",
    },
    "Trump Presidency": {
        "tier": 2,
        "hint_extra": "政策签署 vs 生效是不同 resolution 触发点。读行政命令原文。",
        "slug": "trump-presidency",
    },
    "SCOTUS": {
        "tier": 2,
        "hint_extra": "SCOTUSblog、oyez.org、case argument transcripts 是关键。法律文本解析是 Claude 优势。",
        "slug": "scotus",
    },
    "Politics": {
        "tier": 2,
        "hint_extra": "范围极大(576个events)。优先看具体事件,不要泛泛分析。",
        "slug": "politics",
    },
    "US Politics": {
        "tier": 2,
        "hint_extra": "比 Politics 范围窄。看州级民调和 whip count。",
        "slug": "us-politics",
    },
    "AI": {
        "tier": 2,
        "hint_extra": "警惕内幕抢跑(GPT-5.5 提前 3 周被定价 78% 是真实案例)。监控官方 blog、HuggingFace。",
        "slug": "ai",
    },
    "OpenAI": {
        "tier": 2,
        "hint_extra": "OpenAI 官方公告 + GitHub 是先行指标。注意 'announce' vs 'release' vs 'available' 的区别。",
        "slug": "openai",
    },
    "Tech": {
        "tier": 2,
        "hint_extra": "与 AI/OpenAI 重叠,按 event 区分主题。看公司财报和官方 blog。",
        "slug": "tech",
    },
    "Science": {
        "tier": 2,
        "hint_extra": "同行评议结果是金标准。预印本不算。避免炒作型 hype。",
        "slug": "science",
    },
    "Venezuela": {
        "tier": 2,
        "hint_extra": "西班牙语一手资料 + 美国国务院声明。区分 '承认胜选' vs '实际权力交接'。",
        "slug": "venezuela",
    },
    
    # ===== Tier 3 反向操作 (1个) =====
    "Awards": {
        "tier": 3,
        "hint_extra": "[反向操作] Le 2026 显示该类别系统性过度自信。优先卖 NO,不是买 YES。读 Variety、Hollywood Reporter、Academy 评委构成。",
        "slug": "awards",
    },
}


# 黑名单: 按 tag 名直接排除 (任一tag命中即跳过该market)
BLACKLIST_TAGS = {
    # 金融加密
    "Crypto", "Bitcoin", "Ethereum", "Solana", "Crypto Prices", "Big Tech",
    "IPOs", "Stocks", "Finance", "Business", "Economy", "Fed", "CPI", "Inflation",
    # 体育
    "Sports", "Soccer", "NFL", "NBA", "NHL", "MLB", "Tennis", "Golf",
    "EPL", "FIFA World Cup",
    # 天气/无关
    "Hurricane", "Weather", "Earn 4%", "5M", "Pre-Market", "Hide From New",
    "Recurring", "Yearly", "Up or Down", "Hit Price",
}


# 黑名单: 按内容关键词兜底排除 (question/slug/description里出现就跳过)
# 这些关键词用单词边界匹配 (\b...\b),避免误命中"resolution"中的"sol"
BLACKLIST_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "solana",
    "hurricane", "fed rate", "cpi", "inflation",
    "treasury yield",
]
# 注意: "eth"/"sol"/"gdp" 太短易误伤,移除


def get_tag_hint(tag_label):
    """生成给Claude的研究提示 (通用Tier + 专项)"""
    tag_cfg = TAGS.get(tag_label)
    if not tag_cfg:
        return ""
    tier = tag_cfg["tier"]
    base = {1: TIER1_HINT, 2: TIER2_HINT, 3: TIER3_HINT}.get(tier, "")
    extra = tag_cfg.get("hint_extra", "")
    return f"{base}\n\n{extra}".strip()


def is_blacklisted(market_obj, event_obj=None):
    """
    判断market是否应被黑名单排除.
    1. 检查event的tags(传入)是否含黑名单tag
    2. 检查market的question/slug/description是否含黑名单keyword
    """
    # 1. 检查 event tags
    if event_obj:
        event_tags = [t.get("label", "") for t in (event_obj.get("tags") or []) if isinstance(t, dict)]
        if any(t in BLACKLIST_TAGS for t in event_tags):
            return True
    
    # 2. 检查内容关键词
    text = (
        (market_obj.get("question", "") + " " +
         market_obj.get("slug", "") + " " +
         (market_obj.get("description", "") or "")).lower()
    )
    import re
    for kw in BLACKLIST_KEYWORDS:
        # 用单词边界,避免"sol"匹配"resolution"
        if re.search(r"\b" + re.escape(kw) + r"\b", text):
            return True
    
    return False


def list_tags_by_tier():
    """按tier分组返回, 用于UI展示"""
    by_tier = {1: [], 2: [], 3: []}
    for label, cfg in TAGS.items():
        by_tier[cfg["tier"]].append(label)
    return by_tier
