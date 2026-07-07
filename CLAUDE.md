# Polymarket v7.4 项目规则

> v5.0 已归档到 past/v5/. v5.6 已归档到 past/v5.6-archive/ + git tag `v5.6-final`. v5.9 归档到 past/v5.9-archive/ + git tag `v5.9-final`. v5.10.3 锚点 = `v5.10.3-final`. v5.11.1 锚点 = `v5.11.1-final`.
> v5.0 → v7.4 是一系列 incremental 改动, 完整历史见 技术报告.md §十三 + §十五 + §十六 + §十八/§十九/§二十/§二十一/§二十二/§二十三/§二十四/§二十五/§二十六/§二十七/§二十八.
> **当前 v7.4.4** — 7.2 (持仓页拆3tab) / 7.3 (统计分析大改 + closed_positions 对齐) / 7.4 (测试仓生命周期 + 出场策略收紧: 事件型翻倍优先全卖·卖半后0.782保护·-60%入场锚, 混合型改移动止损35%) 明细见下方「版本号规则」变更日志。**基座 v7.1** = v6.0 (auto-reeval §二十五 + 副屏控制台 §二十六) + **[7.0] 出场策略重设计** (§二十七) + **[7.1] 测试仓/模拟盘 /paper** (§二十八: 不真下单的仓位验证, 跑同一套算法; 🔒 绝不碰钱/不调付费API).
> **补丁历史**: v6.0.1 = 自动重评 bug 审计修复 8 项 (闩锁/卡死复位/离线接管/部分成交/并发锁/夹q/datetime); v6.0.2 = 总资产变化提醒去抖 + 控制台修; v6.0.3 = 并发卖出原子锁 (claim_auto_reeval) + 紧急暂停 API 开关; v6.0.4 = 再评节流改"6h→基线→再多亏10pp"; v6.0.5 = 在线 manual 卡闪烁超时(默认2min)自动调 API(仍留 pending 等确认, 绝不在线盲卖); v6.0.6 = 重评历史归档折叠栏 + 冷却倒计时(清空不删数据, 主页历史栏 + 面板❄️徽章); v6.0.7 = 自动重评多模型 (智谱 GLM 默认 + Claude 兜底, GLM 失败自动降级); v6.0.8 = GLM 参数拉满 (glm-5.2 + thinking + reasoning_effort=max + search_pro) + JSON 解析正则兜底, GLM 路径已实测通过; **v7.0 (大版本) = 出场策略重设计 (§二十七): 重评喂砸盘前中枢反锚定 + 事件型 exit 护栏 + 止盈分档(事件型卖一半) + 收敛移动止损+确认 + 记数据校准**; **v7.1 = 测试仓/模拟盘 /paper (§二十八): 录入拿不准的推荐, 不真下单实时盯盘跑同一套算法; 阶段2 手动重评 (只复制提示词贴 Claude.ai, 零API零钱)**. **注: 「6.1/6.2」是 v6.0 的功能名(自动重评/控制台), 不是版本号; v6.0.x 补丁 + v7.0/v7.1 见这里 + git log.**

## 版本号规则 (v7.x, 2026-07-05 用户定; 单一来源 = `modules/version.py:VERSION`)

- **格式 `major.minor.patch`** (整数.小数1位.小数2位), 例 `7.1.4`。网页(主页/往期页导航) + 启动日志 + GitHub 发布版本**全部读 `modules/version.py`**, 改一处全同步。**别再往 HTML/日志写死版本号**。
- **整数 (major)**: ⚠️ **只有用户明确说才改, 需用户审批**。
- **minor (小数第1位)**: 重大更新(新增较大功能等) Claude **可自行改但必须告知用户**; 一次上了 N 个大功能就 **+N**(不打包成一次)。改动**详细**记入技术报告。
- **patch (小数第2位)**: 小改动 Claude **自行决定, 不用审批**; 技术报告记**关键词简介**即可。
- **文档版本同步 (2026-07-07 加, 公开库 README 标题停在 7.1 被用户发现)**: **minor 升级时**必须同步 6 处 —— README.md + README.zh.md (标题 + 「当前版本概要」块) / SECURITY.md + SECURITY.zh.md ("当前 vX.Y") / 技术报告.md + TECHNICAL_REPORT.md (标题 + 更新时间)。patch 只记变更日志不动标题。文档标题用 major.minor (如 v7.4), 版本概要块标到 patch。
- **变更日志**: `7.1.4` (2026-07-05, patch) = 单一版本源 `version.py` + 事件实时榜加 6h/1d 档 + 自动重评建议 >48h 自动清空归档 + 修 monitor 缺失 `from modules.executor import Executor`(HEAD 已缺, 会致重启崩溃)。
  - `7.1.5` (2026-07-05, patch) = 手机版加最新2个往期卡片(只看); 资产曲线美化(渐变填充/顺滑/买卖点三角标记/成本线+绿红盈亏区/悬停看每天数值); /history 卡片改长方形放大+多信息; 右上操作按钮移到统计栏上方(删检查持仓/刷新, 停止转红, 控制台/API/在线/监控中/30s 移下并各上色, 恐龙小屏隐藏不重叠)。
  - `7.1.6` (2026-07-05, patch) = 移除资产曲线的买卖点三角标记(用户觉得乱); 渐变/顺滑/成本线+绿红盈亏区/悬停tooltip 保留。(`/api/trade_markers` 接口留着但已不用。)
  - `7.2.0` (2026-07-05, **minor** = 前3波 dashboard 大改收官, 含新能力): **#6+#5 持仓页拆 3 tab** —— 「📦 当前持仓」纯**只看**(放最前 → 吃 updatePositions 第一匹配实时价; q/信心/止损/决策状态只显示不可点; 用 `.pos-row` 但**无 data-slug** → cjApplyPos 只认操作面板不冲突) | 「⚙️ 重评操作」(= 原 `pos-panel-current` 整套改名 `pos-panel-ops`+默认隐藏, **所有编辑+加仓/清仓/API重评/止盈止损开关 + #5 粘 Claude 重评 JSON 一键应用** `applyReevalJson`[按 action 走: update_q→改q / hold→维持 / exit→弹确认防误卖 / cancel_autostop→关止损]) | 「🤖 重评模式」不变。**ID 零重复**(编辑控件 tp-/conf-/tier-/sl-btn/newq/rvjson 只在操作面板), **真钱按钮原样未改**(零风险)。switchPosTab 加 'ops'。⚠️ 按用户版本规则本波有 #2(曲线大改)+#6(拆tab新能力)两个大功能, 严格 +N 应到 7.3.0; 暂作一次 overhaul release=7.2.0, 待用户定。
  - `7.2.1` (2026-07-05, patch) = 「当前持仓」只看面板美化: q/信心/止损 改彩色 chip(青/黄/按tier色); 整面板放大(字/行距, 盈亏%/$ 加大到 16.5px 加粗); 删决策状态行的 q/p/edge 冗余显示 + 删「要操作→切重评操作」提示。全部只圈 `#pos-panel-current`, 不动操作面板。
  - `7.2.2` (2026-07-05, patch) = 持仓详情按下单日期 (`meta.created_at`) **新→旧排序** (index 路由渲染前 `sorted(reverse=True)`, 无日期排最后, 不动缓存列表顺序; 所有 tab 一致因共用 positions)。
  - `7.2.3` (2026-07-05, patch, **修 #20 在线/离线 bug**) = "该离线时一直在线"根因 = 前端 `mousemove` 太敏感(鼠标微动就刷 presence_at, idle 永远攒不到阈值)。修: 主页+副屏活动监听**去掉 mousemove**(只认 scroll/click/keydown/touchstart 真操作); 阈值 30min→**10min 弹窗**(`IDLE_MODAL_SEC`/`P_IDLE_MODAL_SEC` 1800→600) + `PRESENCE_STALE_MIN` 35→**12min 硬兜底**。server 端逻辑本就对(effective_online=online且idle<STALE), 只是 mousemove 让 idle 攒不起来。
  - `7.2.4` (2026-07-05, patch, **#4 部分**) = /api_reeval 双模型对比页美化 + **顶部加"智谱 vs Claude 总揽"**(前端从 compare_json 算: 动作一致率/平均 q 差距/最大 q 差距/论点判断一致率 + 各自动作分布); 每条卡片头部加醒目「✓两家一致/✗不一致 + q差」徽章, 去掉底部重复的一致行。⚠️ **总揽字段是第一版**, 用户说"你想一想之后再问我" → 之后可能加/调字段。
  - `7.2.5` (2026-07-05, patch, #4 续) = 总揽按用户要求加 3 字段: **智谱失败次数**(尝试双评里智谱挂/降级次数, 带 Claude 也挂几次)、**谁更激进/保守**(exit清仓/hold按兵/cancel扛单 各模型次数+谁更多)、**平均信心对比**(high=3/med=2/low=1 各模型均值)。全前端从 compare_json 算。
  - `7.3.0` (2026-07-06, **minor** = #8 统计分析大改): ① **审计现有指标全对**(赚钱率=卖出价>成本全部笔/方向对率=已结算押对/累计盈亏/胜率表双口径/校准; 校准仅2小局限: q≤0.5不入桶 + 用桶中点非真实均值, 非bug)。② 新增 `db.get_history_extras()` (时间趋势按月+累计/卖飞分析=(final_outcome-exit_price)*size/盈亏分布4桶/按出场方式`_exit_category`归类) 挂 `/api/history/analytics` 的 extras。③ /history 加 **Chart.js**(时间趋势=月柱+累计线双数据集、盈亏分布柱)+ 卖飞3卡 + 出场方式表 + **每60s自动刷新**(setInterval loadAnalytics)。真数据洞察: 止盈11笔100%赚+$25 🟢 / 重评清仓14笔亏-$8.6 🔴 / 卖飞净$42.48。⚠️ 65笔 `BACKFILL_FROM_TRADES`=早期成交导入的老仓(非bot决策), 出场方式里单列「回填」。**第一版, 用户看完可能再迭代**。
  - `7.3.1` (2026-07-06, patch, **数据修正: closed_positions 跟 Polymarket 对齐**) = 用户发现"已平仓笔数/赚最多5笔"跟 Polymarket(显示98)对不上。对账(拉 /activity 254笔成交): 98=唯一token(80已完全平+18还持有); 旧表100行/85token是**两套口径混合**(backfill每回合 + monitor每次卖拆单重复) + **5个假平仓**(部分卖出误记成平仓, 实际还持有)。修: `scripts/rebuild_closed_positions_2026_07.py` 拿 Polymarket 真实成交**按 token 重建**(一个完全平掉的仓=一行, avg/pnl 跟 Polymarket 一致, 保留现有 tier/tag/estimate/resolution 元数据非空merge, 还持有的不入表) → **80行, 赚最多5笔/累计-$3.51 全对齐**。先备份 v4.db。`_exit_category` 加认 REBUILT。⚠️ **根因未修**: monitor/force_exit **部分卖出仍会写 closed_positions 行**(→ 以后又会漂移出假平仓), 下次要修"只在仓位完全平掉时才写 closed_positions"。
  - `7.3.2` (2026-07-06, patch, 根因守卫 + **重要发现**) = ① monitor check_once 加守卫: 只在 `pos.size - sell_size < 0.01`(完全卖光)才写 closed_positions + 清 meta, 部分卖出不写不清(防假平仓)。② **⚠️ 发现: 当前 monitor.py 里根本没有 take_profit_half/tp_half_sold/partial/half —— v7.0 文档写的"事件型 0.92 卖一半"实际代码里没有!** 1a/1b/止损全是 `sell_size=size` 全卖。所以守卫对当前代码是 no-op(全卖时 remaining=0 照写), 纯防御(万一卖一半加回来)。这跟之前"monitor.py 缺 Executor import"是同一现象: **monitor.py 被改残过, v7.0 出场策略(卖一半/收敛移动止损/事件型exit护栏)可能没在代码里**。⚠️ **待办: 全面核对 v7.0 出场策略 文档 vs 代码是否一致**(用户可能以为有、实际没有)。closed_positions 跟 Polymarket 的漂移主要来自"重入"(平了又买回)= 少见, 重跑 rebuild 脚本即可再同步。
  - `7.3.3` (2026-07-06, patch, **🔴 重大恢复: v7.0出场+GLM+双模型 被回退过, 已从 git 找回**) = 核对确认 monitor.py+auto_reeval.py 在 **commit `6561636`(auto-backup 2026-06-26 12:00)整体回退到老版本** —— `git log -S` 证实 TAKE_PROFIT_HALF/TRAILING_STOP/_pre_dump_center/_run_glm/run_reeval_dual 全在那次提交被删。**2026-06-26→07-06 这十天 bot 跑的是老 v5.x 出场(0.90全卖+分档%止损), v7.0(卖一半/移动止损/事件护栏/反锚定)+GLM+双模型全没跑**(启动日志却一直描述v7.0=撒谎)。修: 从父提交 `6561636^` 恢复(auto_reeval 221→731行, monitor 501→663行), 补回 autoclear 调用; 会话值(30s/120/18/Executor)6561636^ 已含。备份 `.bak_pre_v7recover_*`。⚠️⚠️ **根因未查明**: 什么把这俩覆盖成老版本(编辑器存旧文件?同步?)不知 → 可能再犯; 只这俩被回退(dashboard/db没事)。**建议尽快 git 提交锁定 + 留意再回退**。
  - `7.4.0` (2026-07-06, **minor** = #16 测试仓 /paper 大改): 测试仓改成"像真仓一样有生命周期"。① `/api/paper/list` 只返**进行中**(open 且 `would_sell_at_ts` 为空); 算法一"卖"(would_sell)就移出进行中。② 新增 `/api/paper/history` = 已卖(would_sell)+已清空+已结算 的往期测试仓, 每条算 最终模拟盈亏 / **最高点 peak(本可赚多少, 哪怕后来亏到底)** / 预测准不准结论 + 统计(模拟赚钱率/结算对率/模拟总盈亏)。③ PAPER_HTML 加 进行中标题 + 统计区(3卡)+ 往期测试仓区(判定=押对/押错+算法卖点+📈最高点), 每15s自动刷新。peak_price monitor 盯盘时早已在记, 这次显示出来。真数据: 历史10仓, 模拟总盈亏+$5.6, 有仓最高本可赚$13.49最终-$3.75(坐过山车)。🔒 全只读, 不真下单不调付费API(paper 铁律)。
  - `7.4.1` (2026-07-06, patch, 真钱: **事件型半仓保护**): 用户发现缺口 —— 事件型 0.92 卖一半后, 留的后半原来只有 $0.05 地板 + -30% 重评兜底, 可能从 0.92 坐过山车吐回大半利润。新增 monitor `_evaluate_position` 分支 (紧接 TAKE_PROFIT_HALF 后): `tier==event_driven` 且已 `tp_half_sold` 且 `best_bid < TAKE_PROFIT_PRICE_EVENT_DRIVEN*(1-TAKE_PROFIT_HALF_PROTECT_DROP_PCT)` (=0.92×0.85=**0.782**) → `TAKE_PROFIT_HALF_PROTECT` 全卖后半锁利润 (executed_action=`tp_half_protect_sold`)。常量 `TAKE_PROFIT_HALF_PROTECT_DROP_PCT=0.15`。直接止盈(不走重评, 半仓仍深度盈利)。启动日志已同步。**#13(搜索%现价)/#12(edge门槛) 用户看完决定不改; #11(止损) 未开。**
  - `7.4.2` (2026-07-06, patch, 真钱: **事件型翻倍先到→全卖**): 用户要"翻倍 vs 0.92卖半 比谁先到走谁"。新增 monitor `_evaluate_position` 1a **最顶部**分支: `tier==event_driven` 且 `avg>0` 且未 `tp_half_sold`/`take_profit_pnl_sold` 且 `(bid-avg)/avg ≥ TAKE_PROFIT_PNL_PCT`(翻倍) → **全卖锁翻倍**(不进0.92卖半)。原理: 低价入场(<$0.46)时 2×avg < 0.92, 翻倍价先触发→落袋; 高价入场(≥$0.46)够不到翻倍→先到0.92卖半。放最前=价格跳空同时满足时翻倍优先。**卖半后留的那半仍让它跑**(`tp_half_sold` 已置→本分支不再触发, 由 0.782 保护+跑结算)。1b(+100%)注释同步成"只管非事件型"。自测: $0.40→$0.80全卖 / $0.48→0.92卖半 / $0.50→0.92卖半。启动日志同步。
  - `7.4.3` (2026-07-06, patch, 真钱 = **#11 止损**): 用户 3 项. **(a) 事件型加 -60% %止损**: `STOP_LOSS_PCT_BY_TIER["event_driven"]` None→**0.60** (很松, 事件型震荡大); _evaluate_position (b) 段去掉 `tier != "event_driven"` 排除 → 事件型走入场锚 -60% (跟 hybrid 一样: 砸穿→PENDING_REEVAL→事件型 exit 护栏, $0.05 地板仍兜底)。不再只靠地板一路跌到底。**(b) 确认拍=30s** (CHECK_INTERVAL=30, 连6拍=3min总时长防抖, 不用改, 已是30s/拍)。**(c) 消灭"未分类"**: `_evaluate_position` + `_maybe_trigger_auto_reeval` 的 `tier = ... or "hybrid"` (未分类默认当 hybrid, STOP_LOSS_PCT_LEGACY -25% 弃用); 前端持仓 tier 下拉空白档改 `disabled`(禁止选未分类)+ 标签更新(事件-60%+地板)。现有5仓已全分类。自测: 事件型亏62%→砸穿/亏50%→持有, 未分类当hybrid亏40%→砸穿。#12/#13 用户看完不改。**至此 20 项 dashboard 大改 + 4 项策略全部收官。**
  - `7.4.4` (2026-07-06, patch, 真钱: **混合型改移动止损**): 用户要混合型跟收敛型同形式。混合型的入场锚 -35% 硬止损 → 改成 **从持有期最高价回撤 ≥35% + 连6拍确认** 的移动止损 (新常量 `TRAILING_STOP_PCT_HYBRID=0.35`; `_evaluate_position` (b) 段 `tier in ("convergent","hybrid")` 都走 trailing, 只 event_driven 留入场锚 -60%)。"重评未启用"硬卖 fallback 也带上 hybrid 回撤口径 (`[{tier} 移动止损] 从最高$X回撤Y%`)。`STOP_LOSS_PCT_BY_TIER["hybrid"]=0.35` 保留 (现在只给自动重评触发线 -30% 用, 不再是硬止损线)。启动日志 + 持仓 tier 下拉标签(混合回撤35%)同步。自测: 混合回撤35%→砸穿/31%→持有, 收敛20%照旧。

## v7.1 新增的关键设计 (不要随意改回)

> 测试仓 / 模拟盘 `/paper` —— 把拿不准/看着离谱的 Claude 推荐丢进去, **不真下单**, 按填的入场价实时盯盘, 跑跟真仓**一模一样**的 `_evaluate_position` 算法, 看预测准不准。完整说明见 技术报告.md §二十八。

- **🔒 铁律 (用户 2026-06-22 反复强调): 测试仓绝不碰任何跟钱有关的东西。** `monitor._evaluate_paper_positions` + 所有 `/api/paper/*` 路由 **永不调 `executor.sell/buy`, 永不调 `auto_reeval.run_and_store/run_auto_reeval/_run_glm/_run_claude` (付费 API)**。只允许只读 (Gamma 拉价 / `get_best_bid` / `_pre_dump_center` 拉历史价)。改这块前先跑审计 grep 确认无危险调用。paper 是独立表 + 独立代码路径, 真仓的自动 API 重评天生够不到它。
- **DB**: `paper_positions` 表 (db.py) + CRUD (`add/get/clear/clear_all/update_paper_tracking/set_paper_would_sell/resolve/update_paper_q`)。`status` open/cleared/resolved; 清空=cleared 不删。
- **盯盘 (阶段1)**: `monitor._evaluate_paper_positions()` 每心跳 → Gamma 拉持有 side 实时价 → 合成 pos/meta → 跑 `_evaluate_position(..., breach_store=self._paper_trail_breach)` (paper 独立确认计数, 不串真仓) → 更新 cur/peak/state → 命中硬动作记**首次** would_sell 快照 (止盈用 best_bid, 其余 cur; 算模拟盈亏) → **命中后继续盯到结算** (closed 且价收敛 0/1 → resolve)。executed_action='' 让规则始终评估。
- **手动重评 (阶段2)**: `/api/paper/reeval_prompt` 复用 `build_reeval_prompt` (+ `_pre_dump_center` 反锚定 + Gamma resolution, **全只读**) 生成提示词 → 前端复制 → 用户**自己贴去 Claude.ai 免费重评** → 读到新 q → `/api/paper/update_q` 手动应用。**全程零 API 零钱**。`/paper` 页每仓「📋 复制重评提示词」+「存q」。
- **页面**: `/paper` (PAPER_HTML 常量 + 路由), 录入 = 手动表单 + 粘 Claude JSON 块一键 (entry 默认用 rec 的 cur_price); 主页 + /history nav 加「🧪 测试仓」。`_evaluate_position` 加了 `breach_store` 参数 (默认 self._trail_breach; paper 传独立 dict)。
- ⚠️ **`/api/paper/add` 路由记得 `from modules.db import ... log_event`** (踩过: 漏 import → add 成功但 500)。
- **v7.1.1 录入免填金额 + 主页一键加测试仓**: `/api/paper/add` 金额 = 显式 `size_usd>0` 直接用; 否则按主页**同一套 `position_size_usd` 公式**自动算 (q/入场价/信心/止损档/距结算/cluster, 跟 `/api/suggested_size` 一致), 公式=0/无 bankroll → `fallback_usd`(默认10)。`/paper` 粘 JSON (`addJson`) 传 `auto_size:true`+`fallback_usd`(「每条金额」框降级「兜底$」); **主页 JSON快速通道每条加「🧪 加入测试仓」按钮 (`cjApplyPaper`)** 一键自动算金额录入 (不删该条)。🔒 自动算金额只调 `position_size_usd`+`bankroll_usd`(读 cash)+clusters(读持仓), **绝不下单** (守 paper 铁律)。
- **v7.1.2 测试仓显示抄主页主持仓 (一模一样, 用户要求)**: `/paper` 列表 `load()` 重写成主页同款 —— PAPER_HTML `:root` 换成主页配色 + 加主页 `.pos-hdr/.pos-row` 同一 grid + `.q-cell/.monitor-state-row/.ms-*` CSS; 列全抄主页 (名称/方向/距结算/入场价/当前价/份数/当前价值/盈亏%/盈亏$/q+信心+止损) + 下面「决策状态: <ms-badge> · q/p/edge」行。q 输入改整数 %(`saveQ` /100 存)。保留 paper 独有 would_sell 行 + 重评/存q/🗑。别改回旧 `.prow` 紧凑行。
- **v7.1.3**: ① paper 行 q 输入改 `value=`(原只填 placeholder, 看着像要重填; q 本来就存了)。② **测试仓自动算金额不吃 cluster cap** —— `/api/paper/add` 传 `cluster_current_exposure_usd=0`(用户: cluster 集中度是真钱风控, 测试仓不该被同类真仓挤额度; 实测同推荐真仓挤到$0 / paper给$3)。DD budget 仍照真仓(未豁免)。

## v7.0 新增的关键设计 (不要随意改回)

> 出场策略重设计, 修"把会赢的仓割在坑底" (#79/#86 卖飞 ~$16) + "止盈砍太早 / 绕一圈回原点"。完整说明见 技术报告.md §二十七。范围 = 根因 + 出场机制 (用户选, **不含自动加仓**)。

- **重评 q 反锚定 (根因①)**: 重评前算"大跌前价格中枢" `auto_reeval._pre_dump_center` (get_prices_history trailing 窗口**排除最近6h**取中位数; 失败/≈现价 → None; **永不抛**), 喂进 prompt 让 AI 别锚被砸的坑底现价 (这是 #79/#86 卖飞的根)。`run_and_store` 算一次挂 `pos['_pre_dump_center']`(给 prompt) + 落库 (`auto_reeval_suggestions.pre_dump_center`/`price_curve` 两新列)。`build_reeval_prompt(pre_dump_center=)` 新 `.format` key **必须无条件传** (None→空串; 手动 `/api/reeval_prompt` 也用它, 漏传会 KeyError)。GLM+Claude 都过 `_build_prompt` 自动一致。
- **event_driven exit 护栏 (根因②)**: `auto_reeval.guard_event_driven_exit(action,decision,tier,cur)` —— event_driven 的 exit 仅 `thesis_broken` 或 `(new_q−cur) ≤ -EXIT_GUARD_EDGE`(默认0.08) 才放行, 否则降级 `update_q` 继续持有。**默认安全: new_q 缺失/越界且非 thesis_broken → 持有, 绝不坑底盲卖**。两处调用 (卖之前/claim 之后): 离线 `_auto_execute` + 在线 `dashboard.auto_reeval_confirm`, 都取新鲜 `get_position_meta().stop_loss_tier`。
- **止盈分档 + 卖一半 (出场③)**: monitor `_evaluate_position` 1a 分 tier —— event_driven ≥0.92 **卖一半** (`partial:True`/flag `tp_half_sold`); convergent ≤3天 0.88 全卖; 其余 0.90 全卖。⚠️ **event_driven 不走 0.90 通用全卖**(否则留的半仓立刻被卖)。⚠️ **`check_once` partial 分支 + 卖失败 永不 `clear_position_meta`**(只全量成功才清), 余量继续被管 (size 下拍由 get_positions 自动减半)。
- **收敛型移动止损 + 确认 (出场④)**: 仅 convergent —— 从 `position_meta.peak_price`(每心跳 `max(old,cur)` 更新, 写 DB+改内存 meta) 回撤 ≥`TRAILING_STOP_PCT_CONVERGENT`(0.20; ≤3天 0.12) + 连 `TRAILING_CONFIRM_ROUNDS`(6) 拍确认 (内存 `self._trail_breach`, 恢复清零; **重启清零=只延迟不误触发**) → 走现有 PENDING_REEVAL/硬卖。hybrid/legacy 维持入场锚; event_driven 不变(只地板)。⚠️ **`_maybe_trigger_auto_reeval` 必须对 `state=='PENDING_REEVAL'` 跳过 entry 锚 gate**, 否则收敛型(相对入场仍盈利时)的移动止损会卡死(既不卖也不触发重评)。⚠️ **确认计数用内存, 别持久化"首次跌破时间戳"**(长停机重启会立即误卖)。
- **记数据校准 (出场⑤)**: `auto_reeval_suggestions` 加 `pre_dump_center`/`price_curve`; 每次重评落 (q/现价/大跌前中枢/价格曲线) 供日后用真实数据校准阈值。
- **⚠️ 阈值低置信度**: 0.92/0.88/20%/12%/8pp/6拍 都是 51 笔小样本方向值, 先观察 (bot.log + 重评卡的中枢/降级记录), 用⑤攒的数据校准再调。env: `AUTO_REEVAL_CENTER_WINDOW_H`(24)/`CENTER_SKIP_H`(6)/`EXIT_GUARD_EDGE`(0.08); monitor 常量 `TAKE_PROFIT_PRICE_EVENT_DRIVEN`(0.92)/`TAKE_PROFIT_HALF_FRACTION`(0.5)/`TAKE_PROFIT_PRICE_CONVERGENT_NEAR`(0.88)/`TAKE_PROFIT_CONVERGENT_NEAR_DAYS`(3)/`TRAILING_STOP_PCT_CONVERGENT`(0.20)/`_NEAR`(0.12)/`TRAILING_CONFIRM_ROUNDS`(6)。

## v6.0 新增的关键设计 (不要随意改回)

> 完整说明见 技术报告.md §二十五 (auto-reeval) + §二十六 (控制台). 需 `.env` 配 `ANTHROPIC_API_KEY` 才启用 auto-reeval (缺 key 静默禁用).

- **大跌自动重评主线 (auto-reeval)**: 仓位亏损超分档阈值 → `monitor._maybe_trigger_auto_reeval` 起后台线程调 Claude API (`modules/auto_reeval.py`: web_search + web_fetch + adaptive thinking + 强制 `submit_decision` 工具) → 结构化决策写 `auto_reeval_suggestions` 表 → 挂 dashboard. **决策 4 项**: hold / update_q / exit / cancel_autostop. 改决策项要同步 prompts.py + auto_reeval.py 的工具 schema.
- **分档触发阈值** (各档止损线前 5pp, 在 `_maybe_trigger_auto_reeval`): convergent -15% / hybrid -30% / event_driven -30%(固定不减5pp) / legacy -20%(默认待定).
- **PENDING_REEVAL = %止损不再盲卖**: convergent/hybrid/legacy 砸穿止损线 (含一拍大跳) → 不卖, `_evaluate_position` 返回 `PENDING_REEVAL` 交给重评决定. 只 `$0.05 地板` / 重评未启用 才硬卖; event_driven 与 `autostop_disabled` 仍只地板. 想要老的盲止损回来 = 关掉 auto-reeval (删 key). 主页徽章「⏸ 等重评·暂不止损」, 面板「⏸等重评」.
- **再评节流 (三层, v6.0.4)**: ① `has_inflight_auto_reeval`(进行中=analyzing/pending/manual → 锁住不重复起) ② 6h 冷却 `recent_auto_reeval_exists`(env `AUTO_REEVAL_COOLDOWN_H`) ③ 冷却过后基线: **过6h → 那拍把当前亏损记进 `position_meta.reeval_watch_loss` → 之后从基线再多亏 ≥ `RETRIGGER_DROP_PCT`(默认10pp, env `AUTO_REEVAL_RETRIGGER_DROP`) 才触发**(不是时间一到就放炮)。手动 🤖 全绕过; **「清空」不控制再评**(只从清单移除)。⚠️ 闩锁必须用 `has_inflight` 不是 `has_active`, 否则 AI 一次 hold 就把 %止损永久关。
- **重评历史 + 冷却倒计时 (v6.0.6)**: 「清空」=`status='cleared'` **从不删数据**。主页自动重评建议卡下方有折叠栏「📜 重评历史 & 冷却状态」: 列所有已清空记录(`db.get_auto_reeval_history`)+ 决策 + 三个时间 + 「更多」展开(reason/信心/论点破/来源, 跟在线卡同套字段) + **每仓冷却倒计时**(`/api/auto_reeval/history` 用 `db.auto_reeval_latest_per_token` 定位每 token 最新记录, 对照持仓+`COOLDOWN_HOURS` 算 cooling/armed/inflight/closed; cooling 给 `cd_end_ms` 前端每秒 tick "❄️还剩2h13m")。面板持仓行加紧凑「❄️Xh」徽章(`pCdBadge`+`pCool`)。**别把冷却显示绑到"非最新记录"**(老记录 superseded 不显示, 否则同仓多条都报冷却会误导)。
- **在线/离线 presence (动真钱边界)**: `app_state` 表 + 30 分钟无操作才弹“还在吗” + 60s 没点转离线 (活动驱动 + 双页同步, 见下). **在线 = 暂停自动 API (改手动复制 prompt 省钱); 离线 = 自动 API + `_auto_execute` 自动执行决策 (动真钱: exit 真卖)**. ⚠️ 离线自动卖出路径**未实测**.
  - **例外 — 手动「🤖 API重评」按钮永远等手动确认 (2026-06-18, 用户明确要求)**: `run_and_store` 加 `force_manual` 参数; 手动 route (`/api/auto_reeval/trigger`, dashboard.py) 传 `force_manual=True` → 结果一律挂 pending 等用户「✅ 确认执行」, **不分在不在线都不走 `_auto_execute`** (这按钮是出门快速一键重评用的, 绝不自动动钱). **只有 monitor 大跌自动触发 (monitor.py `_maybe_trigger_auto_reeval` → `run_and_store` 不传参 = `force_manual=False`) 才在离线时自动执行**. 别去掉 force_manual, 别给 monitor 那个调用加上它.
  - **在线 manual 卡闪烁超时自动调 API (v6.0.5, 2026-06-19, 用户明确要求)**: 在线时仓位砸穿触发点 (差5pp, 如 -15%/-30%) **不盲卖** → 进 PENDING_REEVAL + 存 `manual` 卡红闪等手动确认; **但卡闪烁 > `MANUAL_ESCALATE_MIN` 分(默认2, env `AUTO_REEVAL_MANUAL_ESCALATE_MIN`)还没人理 → monitor 心跳自动把它升级成调 API**。⚠️ **在线升级出来的结果仍只挂 pending 等你确认卖, 绝不在线自动卖** (run_and_store 在线分支留 pending, 不进 `_auto_execute` —— 用户原话"他不能直接卖了, 如果在线的话")。落地 = `monitor.PositionMonitor._escalate_stale_manual_reevals` (原 `_adopt_manual_reevals_if_offline` 改名扩容): 离线→立即接管所有 manual (动真钱); 在线→只接管 `created_at` 距今 ≥ `MANUAL_ESCALATE_MIN×60s` 的卡。**别让在线路径走 `_auto_execute`; 别把离线立即接管和在线超时升级合并成一条**。
  - **“还在吗”弹窗 = 活动驱动 + 双页同步 (2026-06-18 重做, 用户要求)**: 不再固定每 30min 弹。`get_presence` 返回 `idle_sec`(距 `presence_at` 秒数); 主页 + /panel 两页都: ①任何人为操作(scroll/click/keydown/mousemove/touchstart)→ 节流(≤60s)`presence_at` ping = 重置空闲钟, **干活时永不弹**; ②`idle_sec ≥ 1800`(30min 无操作)→ 两页**同时**弹(同源 `idle_sec` 驱动, 非各自本地计时器); ③任一页点“我在”或有活动 → `presence_at` 刷新 → 两页下次轮询(≤15s)都收起(**= 任一方答即可, 不用两边都点**); ④弹窗 60s 不点 → 转离线(自动 API 接管), 但 `presExpire`/`pExpire` 过期前再查一次 `idle_sec`, 已被别处刷新就不下线只收起(**防竞态误下线**)。`PRESENCE_STALE_MIN=35` 是关 tab(无 JS 跑)的兜底, 晚于 30+1min 弹窗流程。**别把弹窗改回固定 `setInterval`**(会破坏活动重置 + 双页同步); panel 旧的 blocking `confirm()` 已换成 `#p-pres-modal`(confirm 没法被别页答案自动关)。
  - **活动自动上线 (2026-06-18, 用户要求): 离线时任何人为操作直接自动上线, 开局不用先点“在线”**。`presActivity`/`pActivity` 离线分支 → POST `/api/presence {online:true,auto:true}`(服务器仅在未被抑制时才上线)。**但“手动点的下线”要留得住**: `set_presence(online, manual)` — 手动下线按钮(presToggle/pTog 传 `manual:true`)置 `presence_manual_off=1` → 活动**不**自动上线(人要走了, 收尾的滚动/点击不会又把你拉回在线); 上线(任何方式) / 系统空闲超时自动下线(`manual=False`)→ 清抑制。**页面加载(新会话)POST `{arm:true}`(`clear_presence_manual_off`)清抑制** → 即使上次手动下线过, 这次打开/刷新照样能靠活动自动上线。`get_presence` 多返回 `manual_off`; 两页 `presManualOff`/`pManualOff` 同步。**别把 auto-online 改成无条件 `set_presence(True)`**(会让“手动下线”被收尾活动立刻撤销, 也会破坏 offline=自动动钱的边界)。
- **cancel_autostop**: `position_meta.autostop_disabled=1` → monitor 跳过 %止损只留地板; 行显示 🛑止损OFF. 别复用 v5.6 已删的 freeze_*.
- **API prompt = 手动那份**: `auto_reeval._build_prompt` 用 `prompts.build_reeval_prompt` + 决策指令 (Claude 路径 `APPEND_DECISION_INSTRUCTION` 调 submit_decision 工具; GLM 路径 `_build_prompt(glm=True)` 用 `GLM_JSON_INSTRUCTION` 只输出 JSON). 改手动 reeval prompt 会同时影响 API 重评.
- **多模型: 智谱 GLM 默认 + Claude 兜底 (v6.0.7)**: 自动重评默认先用智谱 GLM (便宜), **GLM 调用失败/给不出合法决策 → 自动降级 Claude Opus 兜底** (两级, env `AUTO_REEVAL_PRIMARY` 可反)。编排在 `auto_reeval.run_auto_reeval`: `_provider_order()` (有 key 的才进, 默认 `['glm','claude']`) 依次试, 第一个出合法决策 (action∈4项) 的返回, 带 `_provider` 标记。`_run_claude`=原 Anthropic 那套 (web_search/web_fetch+submit_decision, **别动, 它是兜底+prompt契约基准**); `_run_glm`=新 (`zhipuai` SDK + 智谱原生 web_search + 只输出 JSON → `_parse_glm_decision` 严校验)。决策 dict 字段跟 Claude 完全一致, 下游不用改。⚠️ **GLM 是尽力而为的便宜主用, 不是直通**: 任何异常/非法决策都降级 Claude, **绝不让坏决策进 `_auto_execute` 动真钱** (parse 必须严, 编排器只认 4 项 action)。`provider` 列存哪个模型出的, 主页卡+历史栏显示「由 智谱GLM/Claude 决策」。`is_configured()` 放宽成"任一 key 即可" (没配 GLM key = 老的纯 Claude 行为不变)。SDK: `pip install zhipuai` (已装进 .venv)。
  - **v6.0.8 (用户加好 key, 参数拉满 + 实测过)**: `GLM_MODEL` 默认 `glm-5.2` (最强, 1M/128K); 开 thinking (`thinking={"type":"enabled"}`) + `reasoning_effort=max` (经 extra_body); web_search 用 `search_pro` + `count=20` + `content_size=high`; `max_tokens=32000`; client timeout 600s。`_glm_create` 容错: SDK 不认某高级参数 (TypeError) 就按序去掉重试 (extra_body→thinking→temperature→max_tokens), 只 TypeError 降级、真 API 错照抛→转 Claude。**已对真实仓位实测通过** (40s 出 update_q + 真实来源)。⚠️ **解析必须留正则兜底 `_glm_regex_extract`**: GLM 常在中文自由文本塞未转义的英文引号 `"` 把严格 JSON 撑崩 → 先 `json.loads`, 崩了按已知 schema 正则抠字段 (action/new_q 一定拿得到, 自由文本尽力, sources 用 URL 正则)。别删这个兜底, 否则 GLM 一塞引号整条重评就废了降级到 Claude。**配置**: `.env` 加 `ZHIPUAI_API_KEY` 启用; 可选 `AUTO_REEVAL_GLM_MODEL`(默认 glm-5.2)/`AUTO_REEVAL_GLM_MAX_TOKENS`(32000)/`AUTO_REEVAL_GLM_REASONING`(max)/`AUTO_REEVAL_GLM_SEARCH_COUNT`(20)/`AUTO_REEVAL_GLM_SEARCH_ENGINE`(search_pro)/`AUTO_REEVAL_GLM_TEMP`/`AUTO_REEVAL_PRIMARY`(glm).
- **/panel 副屏控制台**: 新页面 (`PANEL_HTML` 常量 + 路由), 横向 4 块 + 操作 + 红闪弹窗, 复用 API. **跟只读 /m 分开, 别把写操作加到 /m**.
- **DB 新增**: 表 `auto_reeval_suggestions` / `app_state`; `position_meta` 加 `autostop_disabled` 列; `/api/snapshot` 加 `autostop_disabled` 字段 (面板 🛑OFF 用). 老数据 NULL-tolerant.

## v5.12 新增的关键设计 (不要随意改回)

- **/m 永远只读**: 手机版不放任何写操作 (重评/扫描/录入/卖出/JSON 通道一概不加). 要操作 → 切桌面版. 这是用户明确要求的设计边界.
- **自动跳转语义**: `/` 对手机 UA (`iPhone|Android.*Mobile|Windows Phone`, iPad 算桌面) 302 → /m; `/?desktop=1` 设 force_desktop cookie 90 天退出自动跳; `/m?auto=1` 删 cookie 恢复. 改 UA 正则或 cookie 名要同步改 index + mobile_page 两处.
- **/api/snapshot 的 title/side/avg_price/size 字段是手机版的渲染依赖**, 不要删 (桌面 JS 不用但 /m 用).
- **/m 不引第三方 JS** (无 chart.js): 迷你曲线是手画 canvas. 保持 <15KB 的页面体积是设计目标.
- **JSON 快速通道草稿持久化 (localStorage, §二十四)**: 解析出的推荐 + 粘贴原文 + 计算器推荐金额存 3 个 key (`pm_cj_recs_v1`/`pm_cj_raw_v1`/`pm_cj_calc_v1`), 刷新不丢 (流程: 算金额 → 去 Polymarket 下注 → 刷新 → 回来录入). `cjPersist`(解析时)/`cjPersistCalc`(算金额成功时) 写, `cjRestore`(window load) 恢复, `cjRenderRecs` 是抽出来的渲染函数 (解析+恢复复用). 保留到点 `🗑 清理` (`cjClear`, 连计算器输入一起复位) 或 `📌 录入持仓` 成功 (`cjApplyPos` splice 掉那条 + 重写草稿). 只存页面上本就显示的推荐数据, 不存任何密钥. 改 cj* 函数记得三处 (写/读/渲染) 同步.

## v5.11 新增的关键设计 (不要随意改回)

- **DISCOVERY 输出的 ```json 块字段名是契约**: slug / side / cur_price / q / confidence / stop_loss_tier / end_date / days_to_resolution / cluster_id / tag / reason. dashboard `cjParse/cjApplyCalc/cjApplyPos` 按这些名字读, prompts.py 和 dashboard.py 要改必须两边同步.
- **JSON 快速通道双按钮语义**: `💵 填入计算器` = 买前算金额 (填 sg-* + calcSize); `📌 录入持仓` = 买后按 data-slug 匹配持仓行, 单次 POST /api/record_position 写全 q/信心/止损/cluster/tag/entry_reason. 持仓行的 data-slug/data-side/data-avg/data-size/data-end/data-idx 属性是匹配依据, 模板改版别删.
- **低频信息折叠不删**: TIER 3/4 tag chips 和 自动规则卡 用 <details> 收起. 要加新低频区块照这个模式, 别再堆首屏.

## v5.10.2 修复的关键口径 (不要随意改回, 2026-06-12, 见 技术报告 §二十一)

- **closed_positions PnL 口径**: avg_entry/exit_price 都是**持有 token 自己的价格** (No 仓=No token 价), PnL = `(exit - avg) × size` **不分 side**. 不要再给 No 仓翻符号 (那是把 Yes-price 口径搞混的老 bug, 历史数据已由 scripts/migrate_v5_10_2.py 迁移).
- **is_correct 口径**: final_outcome 已是"持有 side 的最终概率", `is_correct = (final_outcome >= 0.5)` **不分 side**. update_closed_resolution 不要再按 side 翻转.
- **Gamma /markets+/events 默认不返回 closed 市场/事件**: 查已结算的**必须显式带 `closed=true`**; 软结算市场 (closed=False 但价格 ≥0.99) 则裸查才命中 → 所以每档都要查两态. resolution_check._fetch_market_any 五档链: clob_token_ids+closed → clob_token_ids → slug+closed → slug → events?slug. scripts/backfill_closed_tag.py 同理 (events/markets 各两态 + event id 二跳). 不要删 closed=true 档 (删了 resolution 检测就回到 updated=0 空转).
- **本机 DNS 污染 + DoH guard**: 系统 DNS (Tailscale 上游) 对 *.polymarket.com 间歇性污染 (解析到 FB/Dropbox 假 IP). `modules/gamma_client.py:install_polymarket_dns_guard()` 在 main.py 最早处 monkey-patch getaddrinfo, *.polymarket.com 走 DoH (1.1.1.1/8.8.8.8) 优先, 失败回退系统 DNS. TLS SNI/证书校验不受影响. **不要移除**, 否则 DNS 发作时全 bot (positions/CLOB/gamma) 瘫痪且无报错特征 (症状: 主页 0 positions / 现价全 None / checked=N updated=0).
- **get_unresolved_closed_positions 按 token_id GROUP BY + limit 100**: 不要改回 per-row LIMIT 50 (积压 >50 时最老 token 永远轮不到).
- **/history UI 文案是大白话**: 判定行 "✅ 赌对了 — 押 NO, 结果就是 NO · 若持到结算可多赚 $X" 等. cluster 表 + 科研明细表默认 `<details>` 收起; 已结算样本 <20 时显示噪音警示条.

## v5.10 新增的关键设计 (不要随意改回)

- **`closed_positions` 加 6 列**: `cluster_id` (入场时 cluster slug), `tag` (入场时 scanner tag), `is_resolved` (默认 0), `resolved_at`, `final_outcome` (持有 side 的最终概率, Yes 仓+Yes 中=1, Yes 仓+No 中=0; No 仓反之), `is_correct` (=final_outcome>=0.5 自动算). 老数据 NULL-tolerant, 不破坏 schema.
- **`save_closed_position` 签名加 cluster_id + tag 两个 kwarg**, 3 个 caller 同步改 (`monitor.py:auto_sell`, `dashboard.py:/api/force_exit`, `dashboard.py:/api/execute_state`). 从 `meta.get("cluster_id")` / `meta.get("tag")` 读.
- **`save_position_meta` 加 tag kwarg**, `/api/record_position` 接受 `data["tag"]` 字段往下传. 入场时记一次, 卖出时复制.
- **Resolution 检测 cron**: `modules/resolution_check.py:update_unresolved_closed_positions(limit=50)` 调 Polymarket Gamma `markets?clob_token_ids=X` 查 `closed=true + outcomePrices`, 更新 db. monitor 心跳每 `RESOLUTION_CHECK_ROUNDS=120` 轮 (≈1h) 跑一次, 不阻塞主决策. 失败 log.warning 跳过.
- **`final_outcome` 语义**: 不是"Yes 的最终概率", 是"持有 side 的最终概率". 这样 `is_correct = (final_outcome >= 0.5)` 永远对 (无论我们持有 Yes 还是 No).
- **Tag backfill 用 events 端点 (不是 markets)**: Polymarket `tags` 字段在 `/events?slug=X` 不在 `/markets?slug=X`. `_pick_best_tag` 按 tier 排: scanner 白名单 tier=1 > tier=2 > tier=3 > 通用 (World/Politics/Geopolitics/Middle East) > fallback 第一个. 选 'Iran' 不选 'Middle East'.
- **/history 4 个 section**: 进行中 (is_resolved=0) / 已结算 (is_resolved=1, 含 is_correct ✓✗) / 统计分析 (4 个 stat-card + by_tag/tier/cluster 胜率表 + calibration report) / 科研全量 + CSV 导出. 全部展开, 无 tab, 数据源全是 db (单源真理).
- **顶部 nav 加 .pages-tab**: 主页 / 往期仓位监测 2 个 `<a>` 链接. 当前页 .ptab-active 高亮. 主页和 /history 各自 nav 独立 (HTML 复制粘贴, 不复用 template — render_template_string 不便 include 子模板).
- **主页删除 closed-card section**: `<div class="sl">📚 已结束仓位记录...</div>` 整段 + JS (`_closedData/loadClosed/renderClosed`) 删. 但 `/api/closed_positions` route **保留** (不同代码路径, 用 polymarket data-api + trade rounds, 跟 closed_positions 表互补, 也许将来 reuse).

## v5.9 新增的关键设计 (不要随意改回)

- **position_size_usd() 公式** (`modules/sizing.py`): 1/4 Kelly + days+longshot 调整 + cluster cap (20% bankroll) + 月 DD 预算 ($30, expected drawdown ceiling) + 硬边界 [$1, $15]. **单层折扣**: q 假设已是 DISCOVERY 0.5 calibrated, 公式不再二次折扣. confidence 字段保留但**不进公式**(留作 metadata, v6 可能用). 9/9 golden case 测试通过 (`scripts/test_sizing.py`).
- **Cluster 是相关性簇, 不是话题分类**: 命名 `<topic>-<direction>` kebab-case. 例 `iran-deescalation-no` (押"不缓和") vs `iran-deescalation-yes` (押"会缓和", 反相关). 不要按话题归类 (Iran/Politics/Trump).
- **现价口径** (`modules/clusters.py`): cluster_exposure_usd 和 portfolio_exposed_dd_usd **都用 cur_price × size**, 不是 cost_basis. 跟 bankroll = cash + Σ(cur_price × size) 一致, 否则会让公式自相矛盾 (cost 算"还有空间"但 cur_value 已超 cap).
- **TIER_DD 在 2 处定义**: `modules/sizing.py:SIZING_CFG["TIER_DD"]` 和 `modules/clusters.py:TIER_DD`. 这是为避免 circular import (clusters 调 db, sizing 不依赖 clusters), 修改时**两处都要改**.
- **shadow mode 数据收集**: `sizing_log` 表记录每次 /api/suggested_size 调用 + record_position 时填的 `size_usd_suggested`. 2-4 周后看公式推荐 vs 用户实际差多少, 调参数 (KELLY_FRACTION, MONTHLY_DD_BUDGET 等).
- **可调参数 env override**: `SIZING_KELLY_FRACTION=0.20` 等 env var 可覆盖 SIZING_CFG 默认值, 不改代码就能调.
- **3 个 Claude SKILL ZIP** (`data/claude-skills/`): polymarket-discovery / polymarket-reeval / polymarket-cluster-analyzer. 上传到 claude.ai → Settings → Capabilities → Skills. Description 字段**不能含 < > 字符** (Claude.ai 把它当 XML tag 校验), SKILL.md 主体可以含.
- **DISCOVERY prompt 自动注入 cluster 字典** (`modules/clusters.py:get_cluster_dict_for_prompt`): `/api/full_prompt` 在用户复制给 Claude 前, 自动 prepend 当前已有 cluster 字典 + 复用规则到 prompt 顶部. Claude 看到字典后必须**先检查复用**, 不创近义词 slug. 字典空时退化为原 DISCOVERY 行为 (zero-config bootstrap). 见 §十九.11.

## v5.8 新增的关键设计 (不要随意改回)

- **scanner.py 订单簿检查并发** (P-A1): `ThreadPoolExecutor(max_workers=8)` + 不传 clob SDK client (走 HTTP `/book`, 避免 SDK thread-safety 问题). 速度从 26s → 1-4s (per tag). 实测 27 tag 并发全扫 20s.
- **`SCAN_PARALLEL` 环境变量 fallback**: 用 `_scan_parallel()` 函数动态读 (不是 module-level constant), 改 .env 设 `SCAN_PARALLEL=0` 立刻回退串行. 默认 "1".
- **`data/scan_reports/{slug}.md` per-tag cache + `manifest.json`** (atomic rename 写, threading.Lock 防并发冲突). gitignored. `.gitkeep` 保留目录.
- **`scan_all_tags(tier_filter)`**: 5 个 tag 并发跑, 每个 tag 内部 8 个 orderbook 并发 → 总并发 40 但 CLOB 不限速.
- **报告头部 `<!-- scan version: parallel-v1 -->`** 标记 — 用户/Claude 看到这个就知道是新版.
- **4 个新 API 路由**: `POST /api/scan_all` / `GET /api/scan_all_status` / `GET /api/scan_report?tag=X` / `GET /api/full_prompt?tag=X` (后者改: 接 `?tag` 用 cached 报告).
- **chip 状态徽章 UI**: `.chip-pending` / `.chip-running` (pulse 动画) / `.chip-done` / `.chip-error`, JS `setChipStatus(slug, status)` 通过 `data-tag` 属性 + `slugifyTag(label)` mapping 更新.
- **复制即标记**: `copyP()` 成功后自动调 `setChipStatus(cur, '')` 把 ✓ 清掉, 表示"已送 Claude 处理过"; 缓存文件保留, 点 chip 仍可看. 旁边有 🔄 重置标记按钮一键清掉所有 chip 状态 (`doResetScanMarks()`).
- **持仓 tab + 重评模式** (§18.11): 持仓详情 card 顶部加 `📦 当前持仓` / `🤖 重评模式` 两 tab. 重评 tab 列每个仓位简化行 + 大 `📋 复制 Prompt` 按钮 + 顶部 `🤖 一键全部就绪` + `🔄 重置标记`. 复制后该按钮自动清 ✓. JS: `switchPosTab` / `reevalReadyAll` / `reevalResetMarks` / `copyReevalPromptMarked`. 复用 `/api/reeval_prompt` 路由.

## v5.7 新增的关键设计 (不要随意改回)

- **SQLite WAL mode** (P2): `db.py:get_conn()` 启用 `PRAGMA journal_mode=WAL` + `busy_timeout=10000`. 防 monitor 心跳与 Flask POST 并发锁. 这是结构性正确的方案, 不要回 rollback 默认 journal.
- **`closed_positions` 表** (P7): 任何卖出 (auto / user_force / at_target) 都 INSERT 一行完整 PnL 数据. **永远不要在 sell 流程之后再忘记 `save_closed_position()` 调用** — 否则统计层彻底瞎.
- **`login_attempts` 表持久化限流** (P11): 不要回 in-memory dict, 重启清零会让攻击者重启绕过.
- **时区一律 aware UTC** (P3): 用 `_utc_now_iso()` 写, 用 `_parse_iso_to_aware()` 读. 不要再用 `datetime.now()` (naive). 老数据是 naive 的, `_parse_iso_to_aware` 已经兼容.
- **`claude_raw_estimate` 入场时锁定** (P6): record_position 路由强制 `claude_raw_estimate=tp`. 这是 calibration 的基线, **不会随 reeval 漂移**. new_tp 会变, raw_estimate 不变.
- **partial fill < 95% return False** (P1): `executor.py:337` 卖出不到 95% 视为失败, 让 monitor 下轮重试. 不要回 silently return True.
- **dashboard 密码 + tailnet-only** (v5.7 早期, 见 §十五): 公网 Funnel 关掉, `tailscale serve` 只对 tailnet 设备开放. Flask 加 session 密码层. 本机 127.0.0.1 直通零摩擦; 反代过来的都要密码. 90 天 cookie. 限流 5 次错 → 30 分锁 (持久化到 db).
  - **v5.9 hot fix #2 (2026-06-01)**: middleware 改用 **Host 头**判断本机, 不再看 XFF. 浏览器地址栏决定 Host (`localhost:5051` / `127.0.0.1:5051` 直访 vs `*.tailXXXX.ts.net` 经 Tailscale serve 反代), 浏览器扩展 / 代理插件改不了 Host. 这比 XFF 头方案稳得多 — XFF 那次 fix 还是被某些扩展注入 LAN IP 绕过. **新规则**: `host in (localhost, 127.0.0.1, ::1)` → 直通; 其他 → 走 session auth.

## 继承自 v5.6 的关键设计 (不要随意改回)

- **止盈/止损用 `pos.avg_price` 而不是 `meta.entry_price`**: avg 是 Polymarket 实时算的当前持仓加权均价, 真实反映成本; entry 是 db 里第一次入场的历史价, 加仓后 / 二次入场后会失真. 数据回测里 5 月 10 个仓位踩了二次入场坑, 这一行多一层防御.
- **平仓后自动清理 `position_meta` 整行**: 三个触发点 — monitor 自动卖成功 / dashboard 手动卖 (`/api/force_exit`, `/api/execute_state`) 成功 / 每次心跳对比 `polymarket positions ⊃ db meta` 清孤儿. 心跳清理带双守卫: (1) positions 必须非空 (5-24 灾难教训), (2) orphan 必须连续 3 轮观察确认 (5-27 灾难教训).
- **触发用价不对称设计 (止盈 best_bid / 止损 cur_price)**:
  - `TAKE_PROFIT_PRICE` / `TAKE_PROFIT_PNL` 用 `executor.get_best_bid()` (你 sell 真能拿到的价). 防 Dell 类低流动假象: cur=$0.905 但 bid=$0.60, 看 cur 触发会"锁个假胜利"实际卖 60¢ 真亏.
  - `STOP_LOSS` 用 `pos.cur_price`. 防瞬时流动性蒸发, 同时跟历史回测数据一致.
  - `TIME_STOP` / Edge-based 也用 cur_price.
  - best_bid 拉失败 fallback 回 cur_price.
- **STOP_LOSS 三档设计 (LLM 入场分类驱动)**: 5-22 ~ 5-23 回测发现单一 -25% 在 Politics/Geopolitics 类卖错率 67%, Senate/Malta/Israel 都在卖后大反弹. 据此引入三档:
  - `convergent` (真相收敛型: 票房/营收/统计/汇率/比分): -20% 止损 (真相一锤定音, 跌就是跌)
  - `hybrid` (混合型: 候选人选举, 有民调+政治): -35% 止损 (中等容忍)
  - `event_driven` (事件驱动型: 政治/外交/谈判): **不止损**, 只用 $0.05 地板价兜底 (价格震荡 ≠ 真相变化)
  - 老仓位 / 未填 tier → fallback 用 -25% (向后兼容)
  - tier 来源: 每次入场新仓位时 Claude 在 DISCOVERY 输出里直接给出 `**止损档**: convergent/hybrid/event_driven`, 用户在 dashboard 的"止损" dropdown 选对应档.
- **`get_cash_balance` 失败返回 None (不是 0.0)**: 让调用方能区分 "API 挂了" 和 "账户真没钱". monitor snapshot 守卫据此防 assets_total 漏算 cash. dashboard 渲染层 v5.7 加了 `log.warning` 让 API 故障不再静默.

## 项目概述
- 全自动 Polymarket 预测市场交易 bot
- 跑在 Mac mini, port 5051, dashboard URL: http://localhost:5051
- 公网 URL: https://<hostname>.<tailnet>.ts.net (Tailscale Serve, tailnet-only, v5.7)
- 数据库: v4.db (SQLite), 共用 venv 在 ../polymarket-bot/.venv
- v3 在 <sibling-v3-dir, not in this repo> (frozen, 不要碰)

## 核心架构
- main.py 入口
- modules/dashboard.py: Flask UI + 路由
- modules/monitor.py: v5.7 决策引擎 (2 止盈 + 3-tier 止损 + edge-based, 心跳 30s, auto_sell 接入 closed_positions)
- modules/scanner.py: Polymarket Gamma 扫描器
- modules/executor.py: CLOB v2 SDK 卖出
- modules/db.py: SQLite + position_meta CRUD
- modules/prompts.py: DISCOVERY + REEVAL prompt 模板
- modules/tags.py: 22 白名单 tag

## 关键技术决策 (不要改)
- 用 py_clob_client_v2 (不是 v1)
- POLY_SIGNATURE_TYPE=1 (GNOSIS_SAFE)
- POLY_FUNDER 是 proxy wallet, 不是 EOA
- v4.1 sig 修复后 .env 不要再改

## 改代码 → 生效
所有代码改动需要重启 v4 服务才生效:
```bash
pkill -f "main.py" 2>/dev/null
sleep 2
lsof -ti:5051 2>/dev/null | xargs kill -9 2>/dev/null
sleep 1
source .venv/bin/activate
nohup python3 main.py > output.log 2>&1 &
sleep 5
tail -5 bot.log
```

## 重要约束
- 不修改 .env (sig_type / funder / private_key)
- 不修改 v3 代码 (<sibling-v3-dir, not in this repo>)
- 改完代码先看 bot.log 确认无报错再说成功
- shell 是 zsh, 命令里**不要**带井号注释 (zsh 把 # 当命令)
- 涉及 SQLite 改动前先备份 v4.db
- 不要碰 launchd / systemd, 启动方式用上面的 nohup
- bash 命令里中文注释要避开 zsh 误解析 (用纯 ASCII 或 # 之前留空格)

## 常见任务
1. 改 monitor 决策阈值 → modules/monitor.py 顶部常量
   - 止盈 (v7.0 分档, 见 §二十七 / "v7.0 新增的关键设计"): 事件型 best_bid≥0.92 卖一半留一半 (TAKE_PROFIT_PRICE_EVENT_DRIVEN, 且不走 0.90 全卖、不走 +100%) / 收敛型≤3天 0.88 (TAKE_PROFIT_PRICE_CONVERGENT_NEAR + TAKE_PROFIT_CONVERGENT_NEAR_DAYS) / 其余 0.90 (TAKE_PROFIT_PRICE) / +100% 翻倍全卖 (TAKE_PROFIT_PNL_PCT, 事件型除外)
   - 止损 (v7.0): 收敛型 = 从最高价 peak_price 回撤 (TRAILING_STOP_PCT_CONVERGENT=0.20 / ≤3天 _NEAR=0.12) + 连 TRAILING_CONFIRM_ROUNDS=6 拍确认; 混合型 = 入场锚 -35% (STOP_LOSS_PCT_BY_TIER); 事件型 = 不按%止损, 只 $0.05 地板 (EVENT_DRIVEN_FLOOR_PRICE); 老仓 = -25% (STOP_LOSS_PCT_LEGACY)。砸穿止损线→PENDING_REEVAL 交重评 (重评 q 锚砸盘前中枢; 事件型 exit 需 thesis_broken 或 edge≤-8pp = EXIT_GUARD_EDGE)
   - TIME_STOP: TIME_STOP_DAYS / TIME_STOP_DRIFT_PP
   - Edge: HOLD_MIN_EDGE_PP / SOFT_NEGATIVE_THRESHOLD_PP
   - 改完别忘了 modules/dashboard.py 的 import 列表和规则展示同步
   - ⚠️ 止盈止损用 `pos.avg_price` 不用 `meta.entry_price` (防御加仓 + 清理边界, 不要改回 entry)
2. 改扫描参数 → modules/scanner.py 的 FILTERS 字典
3. 改重评 prompt → modules/prompts.py 的 REEVAL_PROMPT
4. 改 dashboard UI → modules/dashboard.py 里的 HTML/CSS/JS
5. 数据库改 schema → modules/db.py 的 init_db, 但要兼容老数据

## 验证流程
1. 改代码后先 python3 -c "from modules.X import Y; print('OK')" 测加载
2. pkill + 重启
3. tail -10 bot.log 看启动日志
4. 浏览器访问 http://localhost:5051 验证

## 备份位置
- modules/*.bak_pre_* 是历史备份, 不要删
- v4.db 改 schema 前先 cp v4.db v4.db.bak_$(date +%s)
- 全局 git alias: git acp "msg" = add -A + commit + push (一句话提交)
- cron 每 30 分钟跑 scripts/auto_backup.sh, 有改动就 auto-backup <ts> 自动 commit+push 到 dev (私有)
- git log 里 "auto-backup ..." 是 cron 干的, 不是手动 commit
- .env / v4.db / *.log / .venv / .env.bak_* / data/portfolio_snapshot.jsonl-public 等已 gitignore
- portfolio_snapshot 表 (图表唯一数据源, 每 30 分钟一行) 有独立远程备份:
  - data/portfolio_snapshot.jsonl — JSON Lines 格式, 一行一 snapshot, 全表 dump
  - auto_backup.sh 每 30 分钟先调 db.export_portfolio_snapshot() 重写这个 jsonl, 再 git push
  - 灾难恢复: bash scripts/restore_portfolio_snapshot.sh (INSERT OR IGNORE 用 ts 去重, 可重复跑, 永远不会重复插入或覆盖更新数据)
  - 不要手动删 data/ 目录 (cron 会重新生成, 但中间窗口的备份历史会断)
  - 强制立即备份不等 cron: 任何时候 bash scripts/auto_backup.sh

## Dual Repo: 私有 dev + 公开 release (v5.7+)

- **两个 GitHub repo, 不要混**:
  - `RobinVico/polymarket-dev` (private) ← 日常工作 / cron auto-backup / 含全部真值
  - `RobinVico/polymarket-llm-trading-bot` (public) ← 只推 release / 全脱敏 / 1 个 commit
- **两个 remote** (本地 git):
  - `dev` → polymarket-dev (HTTPS, cron 默认推这里)
  - `public` → polymarket-llm-trading-bot
- **私有 main 永远保留真值**: CLAUDE.md / 技术报告.md / past/* 都含真实 Tailscale URL + v3 路径, 自己看着方便.
- **公开 release 的所有脱敏都在脚本里**: `scripts/prepare-public.sh` 用 sed/perl 替换真值 + 删 past/ runtime cruft (logs / dbs / bak / PnL jsonl) + 删根目录内部 PPT (*.pptx). 脚本本身也在 public repo 里透明可审计; **真值 pattern 自 v7.4.4 移到 gitignored `scripts/.release-patterns.sh`** (公开脚本零真值; 该文件丢了就按 4 个 REAL_* 变量重建, 见脚本头注释).
- **永远不要直接 push public**: 必须先开 orphan 分支 + 跑脚本 + force push, 否则真值会泄漏.

### 公开 release workflow (每次发布新版本)

```bash
# 1) 私有 dev 已经是想 release 的状态 (commit 都 push 到 dev 了)
git push dev main

# 2) 开 orphan 分支跑脱敏脚本 (一次性副本, 不污染 main)
#    ⚠️ 必须 --orphan, 不能 --detach+普通 commit: 普通 commit 带父提交, push 会把
#    整个私有历史一起推上公开库 (v5.7 就这么泄了 887 个历史提交带真值, 2026-07-07
#    发现, 已用单个无父 commit force push 覆盖; 老 SHA 在 GitHub GC 前或仍可按址访问)
git checkout --orphan pubrel
bash scripts/prepare-public.sh        # 自动 sed 真值 + 删 cruft/内部PPT + 加 .gitkeep

# 3) 单个无父 commit + force push public (公开库永远只有这 1 个 commit, 零历史)
git add -A
git commit -m "Public release vX.Y"
git push public pubrel:refs/heads/main --force

# 4) 回 private dev (脱敏改动自动丢弃), 删临时分支
git checkout -f main
git branch -D pubrel
```

### 不要做的事
- **不要把 public remote 加进 cron** (cron 会自动推所有改动到那, 但 cron 不会跑脱敏脚本 → 真值泄漏)
- **不要把私有 main 上的真值 commit 抄到 public release** (脚本永远在 orphan 分支上跑)
- **不要在私有 main 上跑 prepare-public.sh** (会污染 working tree, 必须先 `git checkout --orphan pubrel`)
- **不要用 --detach + 普通 commit 发布** (commit 带父提交 = 全部私有历史推上公开库, v5.7 踩过的坑)
- **不要往 public push 的时候省 --force** (public main 是被覆盖的, 不是累加历史)
