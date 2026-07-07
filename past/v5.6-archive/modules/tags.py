"""
Polymarket Tag 扫描配置 - tier 1-4 白名单 + 扩展黑名单
所有 tag label 与 Polymarket Gamma API 实际返回完全一致
"""

# 通用研究提示 (按Tier)
TIER1_HINT = "外文一手资料 + 政府/议会原始文件,逐字读 resolution 规则的边界条件。"
TIER2_HINT = "读官方公告、政策文本、benchmark 数据,警惕内幕抢跑。"
TIER3_HINT = "此类别系统性过度自信,优先评估卖 NO。新闻反而损害准确率,优先用日历型事实(提名名单、过去得奖统计)。"
TIER4_HINT = "[少量市场] Polymarket 上此类事件稀少 (off-season / 长 fuse 6+月 / 单事件低 vol)。标准/中范围扫描经常 0 推荐, 但一旦有合适 event AI 优势仍强。建议大范围扫描或等 active events 出现再扫。"


# 白名单: tag_label → {tier, hint_extra, slug}
# tier: 1/2/3 主战场, 4 = 少量 (扫不到也正常)
TAGS = {
    # ===== Tier 1 重点扫 (15 个) =====
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
    "Brazil": {
        "tier": 1,
        "hint_extra": "Planalto 葡语公告 + AtlasIntel/Datafolha/Nexus 民调 + TSE (dadosabertos.tse.jus.br) 权威。优先副市场: margin of victory bracket / 2nd place / congress 席位 / STF 弹劾 longshot。outright in first round 需 >50%。头部 Brazil 选举池子已 sophisticated, edge 在副市场。",
        "slug": "brazil",
    },
    "Mexico": {
        "tier": 1,
        "hint_extra": "Presidencia.gob.mx mañanera 西语直播 + 美国务院声明 + Morena vs SCJN 宪法冲突时点。'ceases to be President for any period of time' = 临时离职即触发 Yes。",
        "slug": "mexico",
    },
    "Congress": {
        "tier": 1,
        "hint_extra": "Congress.gov 法案 status + CBO scoring + Senate.gov cloture tracker + govtrack.us + whitehouse.gov 签署仪式。严格 passed both chambers + signed by deadline ET (House 单独通过不够)。优先押 12-outcome 时间桶系统性偏乐观。",
        "slug": "congress",
    },
    "Global Elections": {
        "tier": 1,
        "hint_extra": "非美国选举聚合 tag, 跟单国 (Brazil/Mexico/Argentina/Germany) 互补。多语一手资料 + 各国选举委员会原始数据。优先看高 vol events, 跨国比较 base rate (现任连任率约 50%, 民调最后 2 周准确率 >80%)。注意一二轮制 + 比例代表 vs 单选区差异。",
        "slug": "global-elections",
    },

    # ===== Tier 2 中等优势 (12 个) =====
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
    "SpaceX": {
        "tier": 2,
        "hint_extra": "FAA Part 450 license 状态 + Cameron County 公告 + NASASpaceflight L2 schedule + Forge/Hiive secondary 估值 (IPO market)。避开 launch day 0DTE (社群 99/1 informed); 真正 edge 在 1-3 周排程窗口 + IPO 时间/估值 multi-outcome。scheduled launch ≠ successful flight test。",
        "slug": "spacex",
    },
    "Primaries": {
        "tier": 2,
        "hint_extra": "美国党内初选。FiveThirtyEight/RealClearPolitics 民调 + Iowa/NH 早期州 + endorsement tracker (Politico/EveryEndorsement)。注意 2028 早期 longshot 仓位价格不稳, 卖高估热门 (Newsom/Harris)、买被忽视者 (Shapiro/Whitmer)。",
        "slug": "primaries",
    },

    # ===== Tier 3 反向操作 / 高估 longshot (3 个) =====
    "Awards": {
        "tier": 3,
        "hint_extra": "[反向操作] Le 2026 显示该类别系统性过度自信。优先卖 NO,不是买 YES。读 Variety、Hollywood Reporter、Academy 评委构成。",
        "slug": "awards",
    },
    "Pop Culture": {
        "tier": 3,
        "hint_extra": "[反向类同 Awards] 涵盖 Eurovision / Met Gala / 名人事件 / celebrity-related 预测。系统性高估热门候选, 优先卖 NO。读 Variety / Hollywood Reporter / CinemaScore。",
        "slug": "pop-culture",
    },
    "Eurovision": {
        "tier": 3,
        "hint_extra": "[反向] Eurovision Song Contest 系统性高估热门国家 (UK/Sweden/Italy 等), 实际 jury+televoting 综合分散。看 OGAE Poll + Eurovisionworld.com 综合 ranking + 历年投票区域偏见 (Nordic/Balkan bloc voting)。",
        "slug": "eurovision",
    },

    # ===== Tier 4 少量 (off-season / 长 fuse / 低 vol, 9 个) =====
    "FDA": {
        "tier": 4,
        "hint_extra": "FDA.gov + drugs.fda.gov + ClinicalTrials.gov NCT 号 + PDUFA 日期 + 公司 8-K + AdComm briefing。严格区分 NDA/BLA 完整批准(Yes) vs CRL/EUA/510(k)/tentative approval(No)。base rate: Phase 3 → approval ~60% (BIO 报告)。AdComm 反对但 FDA 批准 → 仍 Yes。注: PDUFA 通常 6-12 月外, Polymarket 上较少短期可扫到的 events。",
        "slug": "fda",
    },
    "Immigration": {
        "tier": 4,
        "hint_extra": "CBP nationwide encounters 月度数据 + DHS/USCIS/ICE 公告 + White House Briefing Room RSS + Federal Register + TRAC Immigration + ACLU litigation tracker。严格分 EO signed vs implemented。CBP 数据公布前 24-48h 是 alpha 窗口。检查 active TRO/injunction 是否被市场忽略。",
        "slug": "immigration",
    },
    "Argentina": {
        "tier": 4,
        "hint_extra": "Casa Rosada 西语 + LLA Congress 席位 + Milei 政策实施时点。USD/dollarize bracket 系统性高估 tail, 优先卖 NO 在乐观日期 brackets。",
        "slug": "argentina",
    },
    "Germany": {
        "tier": 4,
        "hint_extra": "Bundestag.de Plenarprotokoll 德语 + Bundesverfassungsgericht + Politico Poll of Polls / Wahlkreisprognose。Coalition formation 子桶 + Article 68 Vertrauensfrage binary + snap election deadline。'cease to be Chancellor for any length of time' 临时即触发。注: 大多数事件 6 周以上 fuse, 标准/中范围扫不到。",
        "slug": "germany",
    },
    "France": {
        "tier": 4,
        "hint_extra": "Assemblée Nationale comptes rendus 法语 + Conseil constitutionnel 判决 + Politico FR 民调。Article 49.3 触发 binary + coalition formation + 'cease to be President for any length of time'。注: 长 fuse 居多。",
        "slug": "france",
    },
    "Box Office": {
        "tier": 4,
        "hint_extra": "The Numbers (权威 resolver, 不是 Box Office Mojo) + Deadline weekend recap + Box Office Pro 3-week tracking + CinemaScore + RT。Thursday previews (20:00 ET) × 6-8x multiplier 回归。bracket implied prob 偏差 >10pp 时下注。tie → alphabetical。注: 单 event vol 偏低。",
        "slug": "box-office",
    },
    "Olympics": {
        "tier": 4,
        "hint_extra": "IOC official medal table + SwimSwam / Track & Field News / FloSports + 各国奥委会赛前阵容 + Gracenote forecasts。4 年一届缺专业 model, 个项级利基媒体聚合是 alpha。注: 当前 (2026/Q2) 处 Milano-Cortina 后 / LA 2028 前 off-season, markets 极少。",
        "slug": "olympics",
    },
    "Nobel Peace Prize": {
        "tier": 4,
        "hint_extra": "[反向操作] Polymarket Oracle 自家披露 Peace Prize 14 年 top-3 命中仅 2 次 (86% surprise rate)。Clarivate Citation Laureates (每年 9 月发布) + Lasker Award + Inside Higher Ed/Nature 预测。重点找 Polymarket 上 <5% 的强候选 (committee 反向偏好); 卖 NO 高估政客 (Trump/Musk)。注: 颁奖 10 月, 现在 5 月还没挂市场。",
        "slug": "nobel-peace-prize",
    },
    "OPEC": {
        "tier": 4,
        "hint_extra": "[政策 binary, 不赌油价] opec.org press release + momr.opec.org + 沙特 SPA 阿语 + 俄 Minenergo 俄语 + IEA OMR compliance rate。只做政策 binary, 绝不赌油价 (CME 完全 efficient)。措辞严格分: new group-level cut vs voluntary vs compensation vs extension vs reaffirmation。注: OPEC 会议季度间隔, 通常 6 周外。",
        "slug": "opec",
    },
}


# 黑名单: 按 tag 名直接排除 (任一 tag 命中即跳过该 market)
BLACKLIST_TAGS = {
    # 金融加密
    "Crypto", "Bitcoin", "Ethereum", "Solana", "Crypto Prices", "Big Tech",
    "IPOs", "Stocks", "Finance", "Business", "Economy", "Economic Policy",
    "Fed", "Fed Rates", "Jerome Powell", "CPI", "Inflation",
    # 体育 + 电竞
    "Sports", "Soccer", "NFL", "NBA", "NHL", "MLB", "Tennis", "Golf",
    "EPL", "FIFA World Cup", "Cricket", "MLS",
    "Games", "Esports", "Dota 2", "Counter-Strike 2", "League of Legends", "baseball",
    # 天气/无关
    "Hurricane", "Weather", "Daily Temperature", "Highest temperature",
    "Earn 4%", "5M", "Pre-Market", "Hide From New",
    "Recurring", "Yearly", "Monthly", "Weekly", "Up or Down", "Hit Price",
}


# 黑名单: 按内容关键词兜底排除 (question/slug/description 出现就跳过)
# 这些关键词用单词边界匹配 (\b...\b), 避免误命中"resolution"中的"sol"
BLACKLIST_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "solana",
    "hurricane", "fed rate", "cpi", "inflation",
    "treasury yield",
]
# 注意: "eth"/"sol"/"gdp" 太短易误伤, 移除


def get_tag_hint(tag_label):
    """生成给Claude的研究提示 (通用Tier + 专项)"""
    tag_cfg = TAGS.get(tag_label)
    if not tag_cfg:
        return ""
    tier = tag_cfg["tier"]
    base = {1: TIER1_HINT, 2: TIER2_HINT, 3: TIER3_HINT, 4: TIER4_HINT}.get(tier, "")
    extra = tag_cfg.get("hint_extra", "")
    return f"{base}\n\n{extra}".strip()


def is_blacklisted(market_obj, event_obj=None):
    """
    判断market是否应被黑名单排除.
    优先级:
    0. 白名单 tag (TAGS 全部 tier 1-4) - 命中任一则强制放行 (e.g. Olympics 即使有 Sports tag 也放行)
    1. 检查 event 的 tags 是否含黑名单 tag
    2. 检查 market 的 question/slug/description 是否含黑名单 keyword
    """
    # 0. 白名单优先 (覆盖黑名单)
    if event_obj:
        event_tags = [t.get("label", "") for t in (event_obj.get("tags") or []) if isinstance(t, dict)]
        if any(t in TAGS for t in event_tags):
            return False  # 白名单命中, 强制放行

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
    by_tier = {1: [], 2: [], 3: [], 4: []}
    for label, cfg in TAGS.items():
        by_tier[cfg["tier"]].append(label)
    return by_tier
