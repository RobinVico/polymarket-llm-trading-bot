# Polymarket Bot — Technical Report (v5.9)

*[中文版本 (full / detailed)](技术报告.md)*

This document is the English overview. The full Chinese version (`技术报告.md`) contains 17 sections with detailed code, schema, and version-by-version history. Read the Chinese version for everything below in greater depth — this English file is for quick orientation.

---

## §0. Version & Positioning

| Layer | Path | Status |
|---|---|---|
| **v5.7** | `./` | **Current production** |
| v5.6 | `past/v5.6-archive/` | Frozen snapshot before public release |
| v5.0 | `past/v5/` | Archived (3-tier stop + freeze design) |
| v4 | `past/v4/` | Archived (single -25pp DISASTER) |
| v3 | sibling directory (not in this repo) | Historical, frozen |

**v5.7 = v5.6 + dashboard auth + 13 programming-layer bug fixes.** See §15 (Dashboard Security) and §16 (v5.7 Hardening) for the diff vs v5.6.

---

## §1. Architecture Overview

```
main.py
  ├─ load_dotenv()                                    # local .env (DASHBOARD_PASSWORD, FLASK_SECRET_KEY)
  ├─ init_db()                                        # creates schema if absent (WAL mode)
  ├─ PositionMonitor()  ──→ thread (180s heartbeat)   # decision engine
  └─ Flask app.run(host="127.0.0.1", port=5051)       # dashboard (localhost-only)

Modules:
  modules/dashboard.py   — Flask UI + 12 POST routes + password auth + login/logout
  modules/monitor.py     — heartbeat decision engine, sweep guard, closed_positions write
  modules/scanner.py     — Polymarket Gamma scanner with FILTERS
  modules/executor.py    — py-clob-client v2 wrapper, partial-fill detection
  modules/db.py          — SQLite (WAL) schema + CRUD + helpers
  modules/prompts.py     — DISCOVERY + REEVAL prompts (Claude Research feeds)
  modules/tags.py        — 39-tag whitelist + blacklist

Remote: Tailscale Serve (tailnet-only) exposes Flask via https://<hostname>.<tailnet>.ts.net
```

---

## §2. User Daily Loop

1. **Scan** — User triggers `/api/control action=scan` from dashboard → `scanner.scan_and_report()` writes `last_scan.md`
2. **Discovery** — User copies report to Claude.ai, gets DISCOVERY output with q, side, stop_loss_tier
3. **Manual buy** — User buys on Polymarket UI (or via dashboard `/api/buy_position`)
4. **Record meta** — User fills tp / confidence / tier in dashboard → POST `/api/record_position` → `position_meta` row
5. **Auto monitoring** — `PositionMonitor` heartbeat (180s) checks all positions, auto-sells on TP/SL/TIME_STOP triggers
6. **Reeval (≥24h)** — Dashboard shows reeval badge, user runs REEVAL prompt against Claude.ai, updates q (or marks hold)
7. **Position close** — `clear_position_meta()` runs after any sell (auto or user). `closed_positions` row is inserted before clearing.

---

## §3. Dashboard UI

See `modules/dashboard.py` for the inline HTML (rendered via `render_template_string`). Key elements per row:

- title / side / avg / cur_price / size / value / pnl%
- `tp` input (Claude's q at entry, %)
- `confidence` dropdown (high / medium / low)
- `stop_loss_tier` dropdown (convergent / hybrid / event_driven)
- Reeval badge — appears when ≥24h since last reeval or progress crossed thresholds
- Monitor-state badge — HOLD / MARGINAL / SOFT_NEGATIVE / AT_TARGET

Below the position list: portfolio history chart (ranges 1d/1w/1m/1y/all), events log (sell history with color-coded reasons), top movers, scan report, full prompt.

---

## §4. Decision Engine (v5.1+)

Priority high → low (first match wins, full sell):

| # | State | Trigger | Trigger price |
|---|---|---|---|
| 1a | `TAKE_PROFIT_PRICE` | `best_bid ≥ 0.90` | best_bid (real sell price, defends against Dell-style thin orderbook) |
| 1b | `TAKE_PROFIT_PNL` | `(best_bid − avg) / avg ≥ 1.00` (+100%) | best_bid |
| 2 | `STOP_LOSS` | depends on `stop_loss_tier`: `convergent` -20%, `hybrid` -35%, `event_driven` floor $0.05, NULL legacy -25% | cur_price (avoids transient bid evaporation) |
| 3 | `TIME_STOP` | `days_left ≤ 2 AND |cur − entry| × 100 < 5pp` | cur_price |

Below-threshold states (no auto action, user-facing badge only):
- `HOLD`: `edge > +5pp`
- `MARGINAL`: `-3pp ≤ edge ≤ +5pp`
- `SOFT_NEGATIVE`: `edge < -3pp` (reeval requested)
- `AT_TARGET`: `cur ≥ q` (user-confirmed sell available)

`edge = (q − cur_price) × 100` where `q = new_tp` (current calibrated probability) and `cur_price` is mid.

**Key asymmetry — best_bid for take-profit, cur for stop-loss.** Documented decision; do not change without revisiting §四 in the Chinese report.

---

## §5. REEVAL Prompt v5.2

Triggered when ≥24h since last reeval, or progress < 50%, or edge dropped < -3pp. User clicks reeval → dashboard generates filled prompt → user pastes in Claude.ai → Claude returns updated q or "hold" or "exit" recommendation. User clicks corresponding button (uplift / skip / close).

Prompt includes:
- Current cur_price, q, days_left, progress%
- Polymarket question + resolution description (fetched live from Gamma API)
- Original entry context (entry_price, side, claude_raw_estimate)

See `modules/prompts.py` for full template.

---

## §6. DISCOVERY Prompt (new positions)

User runs DISCOVERY against the latest `last_scan.md` to get position candidates. Claude returns for each recommended market:

- Side (Yes/No)
- q (calibrated probability)
- confidence (high / medium / low)
- **`stop_loss_tier`** (convergent / hybrid / event_driven) — REQUIRED, drives auto-sell rules
- Reasoning

User picks 1-3, executes manually, then records via `/api/record_position`.

---

## §7. Confidence 3-Tier Mechanism

| Confidence | Behavior | Use |
|---|---|---|
| `high` | Strict — only mechanical rule triggers (TP/SL/TIME_STOP) sell; user reeval cannot drop q by epistemic humility | Strong conviction, clear evidence |
| `medium` | Default — Claude may downgrade q by up to 5pp in reeval based on new info | Standard |
| `low` | Permissive — Claude can downgrade up to 10pp based on metacognition | Speculative |

---

## §8. Scanner Filters + 39-Tag Whitelist

`modules/scanner.py:FILTERS` defines: min_volume, min_liquidity, days_left range, min/max price thresholds, etc. Markets must also have a tag from the 39-entry whitelist (`modules/tags.py`). See full whitelist in the Chinese report §八.

---

## §9. Database Schema

WAL mode (v5.7). Tables:

| Table | Purpose |
|---|---|
| `position_meta` | Active position meta (q, side, entry, tier, confidence, monitor_state, reeval state) |
| `events` | All actions logged (buy/sell/update_q/reeval/etc.) for audit |
| `closed_positions` | **v5.7 new** — full PnL/duration/reason/raw_estimate for closed positions; the analytics table |
| `portfolio_snapshot` | Every 30 min by cron — value, cost, cash, pnl, assets_total (used for the chart) |
| `tier_sold` | Legacy — partial-tier sells tracking |
| `login_attempts` | **v5.7 new** — persistent rate limit counter (survives restart) |

See `modules/db.py:init_db()` for the full schema. v5.7 also adds `_utc_now_iso()` helper for tz-aware timestamps and `_parse_iso_to_aware()` for reading legacy naive data.

---

## §10. API Routes (dashboard)

All POST routes require authentication unless from `127.0.0.1` with no `X-Forwarded-For` header.

| Route | Method | Action |
|---|---|---|
| `/` | GET | Main dashboard |
| `/login`, `/logout` | GET/POST | Auth |
| `/api/control` | POST | Start/stop monitor, trigger scan |
| `/api/force_exit` | POST | User-initiated full sell |
| `/api/execute_state` | POST | Confirm and execute AT_TARGET |
| `/api/buy_position` | POST | Add to position (manual buy via executor.buy) |
| `/api/update_q`, `/update_tp`, `/update_confidence`, `/update_tier` | POST | Update meta fields |
| `/api/mark_reeval`, `/api/reeval_prompt` | POST/GET | Reeval workflow |
| `/api/record_position` | POST | Save new entry meta (after manual buy) |
| `/api/snapshot`, `/api/portfolio_history`, `/api/movers`, etc. | GET | Read-only data for UI |
| `/api/closed_positions` | GET | Closed-position history |

---

## §11. Key Formulas

```
edge_pp        = (q − cur_price) × 100
pnl_pct        = (cur_price − avg_price) / avg_price × 100        (Yes side)
pnl_pct        = (avg_price − cur_price) / avg_price × 100        (No side)
progress       = max(0, min(1, (cur − entry) / (tp − entry)))     (Yes side)
days_left      = (end_date − now) / 86400
```

For Kelly sizing (not yet implemented, see §16.5 + §17 roadmap):
```
edge           = q × (1 − c) − (1 − q) × c           (binary market with market price c on your side)
discounted_e   = edge × 0.6                          (LLM overconfidence haircut)
kelly_f        = discounted_e / (1 − c)
position_size  = bankroll × kelly_f × 0.25           (¼ Kelly)
```

---

## §12. Automation

| Task | Mechanism | Frequency |
|---|---|---|
| Monitor heartbeat | Python thread in main.py | 180s |
| Portfolio snapshot | monitor saves row via `save_portfolio_snapshot` | 30 min |
| Git auto-backup | `scripts/auto_backup.sh` via cron | 30 min |
| Restore | `scripts/restore_portfolio_snapshot.sh` (manual) | on demand |

Restart procedure (after code change):
```bash
pkill -f "main.py"; sleep 2
lsof -ti:5051 | xargs kill -9; sleep 1
source .venv/bin/activate && nohup python3 main.py > output.log 2>&1 &
sleep 5 && tail -10 bot.log
```

---

## §13. Version Evolution Highlights

See Chinese report §十三 for full per-commit history. Headlines:

- **v4** → **v5** (May 16): single -25pp DISASTER replaced with 3-tier entry-classified stop + 24h freeze + 40% floor
- **v5 → v5.1** (May 18): added 2 top-priority auto-take-profits (90¢ price, +100% pnl) + simplified to single -25% percent stop loss (removed all freeze + tier + floor)
- **v5.1 → v5.2** (May 18): deleted freeze entirely
- **v5.2 → v5.3 → v5.4 → v5.5** (May 19-24): a series of bugfixes — orphan sweep guards, partial-fill detection improvements, asymmetric trigger price (best_bid for TP, cur for SL), snapshot guards
- **v5.5 → v5.6** (May 24): STOP_LOSS upgraded from single -25% to LLM-driven 3-tier (convergent -20% / hybrid -35% / event_driven no-stop with $0.05 floor)
- **v5.6 → v5.7** (May 29-30): dashboard auth (Tailscale-only + Flask session) + 13 programming bug fixes (see §16)

---

## §14. Operational Observations (manual analysis, May 2026)

From 14 closed positions tracked manually before `closed_positions` table existed:
- Pure -25% stop-loss had **67% wrong-sell rate** in Politics/Geopolitics categories (Senate / Malta / Israel cases all rebounded ≥ 50% post-sell)
- → Drove the v5.6 3-tier design
- v5.7's `closed_positions` table makes future analyses SQL-driven instead of manual

See §十四 in Chinese report for the detailed 14-position breakdown.

---

## §15. Dashboard Public Access & Security (v5.7)

10 mutating POST endpoints — anyone with the URL could previously sell all positions. Now blocked by:

1. **Tailscale Serve** (not Funnel) — public DNS doesn't resolve outside the tailnet
2. **Flask password layer** — `DASHBOARD_PASSWORD` from .env; 90-day cookie; HttpOnly/Secure/SameSite=Lax
3. **localhost bypass** — `127.0.0.1` requests with no `X-Forwarded-For` header skip auth (zero friction for the operator)
4. **Rate limiting** — 5 fails → 30-min lockout per IP, persisted in SQLite (v5.7 P11)
5. **127.0.0.1 binding** — Flask only listens on localhost, Tailscale daemon does the reverse-proxy

Key implementation gotcha: Tailscale Serve sets `request.remote_addr` to `127.0.0.1` when reverse-proxying. Naive `remote_addr == "127.0.0.1"` bypass fails open. The fix: also require `X-Forwarded-For` header to be absent. See `modules/dashboard.py:_require_auth` for the production logic.

See §十五 in Chinese report for the full design (architecture diagram, every line of the auth code, verification curl checklist, threat model, what to never publish).

---

## §16. v5.7 Hardening (May 30)

13 programming-layer bugs fixed in a single refactor pass:

| # | Bug | Fix |
|---|---|---|
| P1 | Partial fill returned True at <95% | `executor.py:337` now returns False so monitor retries dust |
| P2 | SQLite default journal blocks reads during heartbeat | `db.py:get_conn` enables WAL + 10s busy_timeout |
| P3 | Naive `datetime.now()` mixed with aware UTC parsing → silent TypeError | `_utc_now_iso()` + `_parse_iso_to_aware()` everywhere |
| P4 | Reported missing `avg > 0` guard at line 148 | Verified original code already had the guard at line 137 — no change |
| P5 | `get_cash_balance() or 0` silently turned API failure into $0 | Explicit None check + log.warning in dashboard |
| P6 | `claude_raw_estimate` permanently NULL | record_position now writes `= tp` at entry (locked, never re-eval'd) |
| P7 | No `closed_positions` table — all sell history was string parsing | New table + monitor.auto_sell + force_exit + execute_state all write to it |
| P8 | `entry_reason` always empty | Stub fallback so column never null |
| P9 | POST endpoints accepted q=-99, size=-1, etc. | Range validation on record_position / buy_position / update_tp / execute_state state whitelist |
| P10 | `(unknown)` market_slug in 10+ historical update_q events | Fetch meta upfront, use real slug |
| P11 | In-memory login lockout cleared on restart | SQLite-backed `login_attempts` table |
| P12 | execute_state trusted stale db.monitor_state | Live re-verify cur_price ≥ q at AT_TARGET execution |
| P13 | `_last_snapshot_ts = 0` on restart caused duplicate row | Seed from `MAX(ts) FROM portfolio_snapshot` |

See §十六 in Chinese report for code snippets and the diagnostic methodology that found these (ultrathink: 3 parallel Explore agents + live data verification + Polymarket-specific research).

---

## §17. Roadmap (Future, F1-F19)

19 items deferred from v5.7. Categorized as **structural strategy** (need human judgment) vs **architecture upgrades** (mostly code). Top 5 by leverage:

1. **F1 Concentration cap** — Currently 75% Iran-narrative cluster across 4/6 positions. Manual rebalance needed; not a code issue.
2. **F2 Fractional Kelly sizing** — Equal-size entries leave 30-50% of compounding on the table. Add `bankroll` field + Kelly formula + 30% LLM haircut.
3. **F3 LLM probability haircut** — Claude q estimates are systematically overconfident (arxiv 2505.02151 — 20-60% drift); apply 30% haircut at entry.
4. **F4 Polymarket platform OPSEC** — Keep on-platform funds ≤ 30-day loss tolerance (May 22 2026 $520K UMA CTF Adapter leak as precedent).
5. **F6 Stop-loss as hedge trigger, not exit** — For `hybrid` tier, buy opposing side at threshold instead of selling (per PANews 27k-trade whale analysis).

Full 19-item table + composite groups (A-E) + recommended timeline (this week / this month / next quarter / next year) is in §十七 of the Chinese report.

**Explicitly do NOT do**:
- Don't rewrite Flask → FastAPI (Flask is fine for single-user)
- Don't introduce SQLAlchemy ORM (raw sqlite3 + parameterized queries already safe)
- Don't migrate to Postgres (SQLite + WAL handles <1M rows easily)
- Don't containerize (Mac mini + nohup + cron works)
- Don't replace Tailscale (device-level mTLS beats anything DIY)

---

## Reading Order Recommendation

For first-time readers:
1. This file (§0 to §3 for orientation)
2. README.md (operational quickstart)
3. Chinese `技术报告.md` §四 (decision engine — the heart of the bot)
4. Chinese `技术报告.md` §十六 (v5.7 bug fixes — useful debugging patterns)
5. Chinese `技术报告.md` §十七 (roadmap — what to build next)

For contributors:
- Read `CLAUDE.md` for project conventions
- Read `SECURITY.md` for vulnerability disclosure
- Read this English report's §15-16 for security and recent refactor context

---

## §18. v5.8 Concurrent Scan + One-Click + Per-Tag Copy (May 31)

User pain point: scanning all 27 Tier-1+2 tags took 12 minutes serial and the single `last_scan.md` got overwritten before the user could copy each one. v5.8 fixes both.

**Stage A — concurrent orderbook checks** (`modules/scanner.py`):
- Old: 50 markets × `time.sleep(0.5)` serial inside each tag scan
- New: `ThreadPoolExecutor(max_workers=8)`, no SDK client passed (HTTP `/book` is thread-safe)
- Per-tag time: **26s → 1.7s** (measured 4x speedup on Iran tag)
- `SCAN_PARALLEL=0` env-var fallback to old serial behavior, zero-code rollback
- Output marker `<!-- scan version: parallel-v1 -->` at the top of every report

**Stage B — one-click scan-all + per-tag cache**:
- `data/scan_reports/{slug}.md` one file per tag (gitignored)
- `data/scan_reports/manifest.json` tracks `{status, mtime, error}` per tag
- New routes: `POST /api/scan_all`, `GET /api/scan_all_status`, `GET /api/scan_report?tag=X`, `GET /api/full_prompt?tag=X`
- UI: 🚀 "Scan all Tier 1+2 (27 tags)" button, chip status badges (⏳/🔄/✓/❌), click cached chip = instant switch
- Measured end-to-end: **27 tags in 20 seconds** (5 tag-workers × 8 internal orderbook-workers, zero HTTP 429)

**Quality protection (5 layers)** — user's concern was "don't change scan output quality, only speed":
1. A/B verification before deploy: serial vs parallel on same tag → 0-line diff after sorting
2. Explicit sort by volume desc (not processing order)
3. HTTP retry already in `_s()` Session (`Retry(total=3)`)
4. `SCAN_PARALLEL=0` env fallback
5. Version marker in report header

**18.10 UX increments**:
- `copyP()` after successful clipboard write → auto-clears the current chip's `✓` (visual "已送 Claude")
- 🔄 Reset marks button — clears all chip states, cached files preserved

**18.11 Reeval mode tab** (mirroring scan-tab design):
- Position card grows a 3rd tab: 📦 当前持仓 / 🤖 重评模式 / 🧠 Cluster 分析 (v5.9)
- Reeval tab shows simplified rows + 📋 Copy Prompt button per position
- 🤖 "Ready all" marks all green ✓, 🔄 Reset clears marks, copy clears single chip

See Chinese 技术报告.md §十八 for full details (10+ subsections including measured performance, file change map, rollback recipe).

---

## §19. v5.9 Position Sizing Formula + Cluster Cap (Jun 1)

Goal: replace user's "always $5, $1 for longshots" gut-feel with a math-justified formula based on Kelly Criterion + LLM overconfidence research. Claude.ai Research produced a full report with academic citations (Kelly 1956, Thorp 2006, Page-Clemen 2013, Snowberg-Wolfers 2010, Sun et al. arxiv 2505.02151).

**19.1 Three critical insights from Research**:
1. User's 3 Iran positions are the same cluster — current-value exposure $21.07 > 20% × $87 bankroll cap = $17.29, **must not open a 4th Iran position**.
2. us-iran-peace has largest edge, should be $5 → $8.
3. Mazzei (q=0.87, p=0.131, 74pp edge) is unrealistically large → data entry error.

**19.2 Formula (`modules/sizing.py`)** — 5-step single-layer haircut:
```
1. edge_check:  edge = q - p; if <=0 return $0
2. kelly:       kelly_f = edge / (1-p); raw = bankroll × kelly_f × 0.25 (1/4 Kelly)
3. days+longshot: raw × sqrt(21/days)[0.40,1.0] × (1.0 if p>=0.15 else 0.5+0.5*p/0.15)
4. cluster+DD clip: min(raw, cluster_cap - exposure, (30 - exposed_dd) / TIER_DD[tier])
5. hard bound: round to [$1, $15]; below $1 returns $0
```

**19.3 Single-layer haircut decision** (user choice): DISCOVERY prompt already does `q' = p + 0.5 × (q_raw - p)` market-blend calibration. The formula does NOT apply a second LLM haircut. Total trust ≈ 0.5 (DISCOVERY layer only). `confidence` is still recorded as metadata but does not affect size — leaves room for a v6 confidence-fraction-Kelly adjustment.

**19.4 Critical: current-value cluster accounting (not cost-basis)** — bankroll = `cash + Σ(cur_price × size)` is current-value, so `cluster_exposure_usd()` must also use current-value or the formula contradicts itself. Iran cluster cost-basis is $15 (under $17.29 cap → would falsely permit a 4th Iran) but current-value is $21.07 (over cap → correctly rejects). Same for `portfolio_exposed_dd_usd()`.

**19.5 Cluster is correlation, not topic** — `<topic>-<direction>` kebab-case. Same direction wins together = same cluster; opposite direction = different cluster (even if same topic). Example: `iran-deescalation-no` (3 user positions) vs hypothetical `iran-deescalation-yes` (anti-correlated, distinct cluster).

**19.6 Golden case tests** — `scripts/test_sizing.py` has 9 hand-computed cases with strict `assert size == round(expected, 2)` (single-layer math is simple enough for exact arithmetic). All 9 pass including: cluster-full ($0), DD-budget-exhausted ($0), longshot-below-$1-floor ($0), hard-ceiling-$15.

**19.7 Live verification of Claude's #1 prediction**:
```
curl /api/suggested_size?cluster=iran-deescalation-no&...
→ {"size_usd": 0.0, "reason": "cluster full (exp $20.98 >= cap $17.27)"}
```
User physically cannot add a 4th Iran position via the formula. ✓

**19.8 Cluster analysis workflow**:
- New `polymarket-cluster-analyzer` skill for claude.ai (3rd skill, alongside discovery + reeval)
- Dashboard's 3rd position tab "🧠 Cluster 分析" lets user 🤖 copy snapshot to Claude → assign cluster_ids → 💾 save back
- One-time SQL UPDATE seeded the 4 existing positions (3 Iran → `iran-deescalation-no`, Mazzei → `mazzei-oklahoma-gov-no`)

**19.9 Tunable parameters** (env-var override, no code change):
```
SIZING_KELLY_FRACTION=0.25       SIZING_CLUSTER_CAP_PCT=0.20
SIZING_MONTHLY_DD_BUDGET=30.0    SIZING_MAX_SINGLE_POS=15.0
SIZING_REF_DAYS=21.0             SIZING_LONGSHOT_THRESH=0.15
SIZING_TIER_DD_{convergent,hybrid,event_driven}={0.20, 0.35, 0.70}
```

**19.10 Shadow mode**: `sizing_log` table records every `/api/suggested_size` call + final user choice. After 2-4 weeks, compare `size_usd_suggested` vs `initial_size` to calibrate parameters.

**19.12 LANGUAGE LOCK — force Chinese output** (Jun 3 patch after user complained Claude.ai still returns English reports even with the polymarket-discovery skill loaded):

Anthropic's Deep Research mode defaults to English output (English-language search + synthesis). The "中文输出" rule buried mid-document in `SKILL.md` HARD REQUIREMENTS got swamped by English source material during synthesis. Three-layer fix:

1. `/api/full_prompt` now prepends a bold `# 🇨🇳 LANGUAGE LOCK` block at the very top of the prompt, repeated in both Chinese and English so Claude can't miss it.
2. `DISCOVERY_PROMPT` first line now explicitly says `**请用简体中文回复整篇 markdown**` as a fallback if prompt was assembled elsewhere.
3. `polymarket-discovery/SKILL.md` HARD REQUIREMENT #1 now says the rule **overrides** the Deep Research default English preference, and any English narrative section requires a full rewrite.

User must re-upload the new `polymarket-discovery.zip` to claude.ai after each SKILL change (delete the old one, upload the new one — the Skills UI doesn't auto-refresh).

**19.11 DISCOVERY prompt auto-injects current cluster dictionary** (same-day patch after user asked "what if Claude doesn't know my existing clusters when picking new positions?"):
- `modules/clusters.py:get_cluster_dict_for_prompt()` returns markdown table of `(cluster_id, current_exposure_usd, position_count)` + reuse rule
- `/api/full_prompt` prepends this to the DISCOVERY prompt before sending to Claude
- `polymarket-discovery/SKILL.md` adds HARD requirement #5: "if prompt contains existing cluster dictionary, MUST check first for reuse — don't create synonym slugs like `iran-no-deescalation` when `iran-deescalation-no` exists"
- Empty dictionary (user just sold all) → graceful no-op, Claude freely creates new slugs
- User experience unchanged — still just one 🤖 button, dictionary injected automatically

See Chinese 技术报告.md §十九 for the full Claude Research report summary, formula derivation walkthrough, academic citation details, file-by-file change map.
