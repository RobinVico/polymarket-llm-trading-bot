# 安全策略

*[English](SECURITY.md)*

## 支持版本

只有 `main` 分支 (当前 v7.4) 接受安全更新。(v7.1 说明: 测试仓页 `/paper` 严格只读 —— 绝不真下单、绝不调付费重评 API。)
归档版本 (`past/v4/`, `past/v5/`, `past/v5.*-archive/`) 已冻结, 不维护。

## 报告漏洞

**不要在 GitHub 上开公开 issue 报告安全问题。**

请用标题前缀 `[SECURITY] - private contact request` 开一个 issue, 维护者会通过私下渠道联系你。

### 我们关注的问题

高优先级:
- Flask dashboard 鉴权绕过 (例如不带 `X-Forwarded-For` 头能到达 `/api/force_exit`)
- `modules/` 内 SQL injection 或路径穿越
- commit / 归档目录 / log 文件里的凭据泄漏
- 不需要密码就能触发交易或资金转移的任何路径
- 可能导致 funder wallet 被锁/被掏空的 CLOB API 误用

中等优先 (也欢迎):
- SQLite 层 race condition 导致数据丢失
- 不可信输入进 `log.info` (log injection)
- TLS 设置不当, cookie 配置弱

不在范围内:
- 关于策略 edge 或交易逻辑的反馈 (这不是一个"安全交易策略", 是个人 trading bot)
- 第三方平台问题 (Polymarket / UMA oracle / Polygon) — 报给对应项目
- `past/` 归档里的漏洞 (已冻结, 不运行)

## 响应时间

个人项目, 尽力 **7 天**响应。可能导致资金损失的关键问题优先处理。

修复合并后 **28 天**进行公开披露, 致谢报告者 (除非要求匿名)。

## 威胁模型 (供其他 fork 参考)

本 bot 假设以下边界内可信:
- 运行 bot 的机器 (private key 明文在 `.env`)
- Tailscale tailnet (tailnet 内设备可信)
- Dashboard 密码 (32 字符随机, 在 `.env`)
- 可信机器上的浏览器 `localStorage` — JSON 快速通道把草稿缓存在这 (解析出的推荐 / 计算器输入 / 推荐金额)。**不含任何密钥**: key/密码从不发到浏览器, 只是已显示在已登录页面上的数据。
- `ANTHROPIC_API_KEY` (明文在 `.env`, 可选) — 开启大跌自动重评 (v6.0): bot 会调 Claude API, 把市场标题/slug 发去联网调研, 且**你"离线"时它能不经逐笔确认直接下真实卖单** (在线则等你在 dashboard 批准)。不填这个 key = 整个功能关闭。key 从不发到浏览器。

任何**从这些边界之外**到达 bot 的请求都不该触发交易。如果你能 demo 出可以触发, 请联系报告。
