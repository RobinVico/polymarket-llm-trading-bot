# Polymarket Bot — Technical Report (v7.4)

*[中文版本 (full / detailed)](技术报告.md)*

This document is the English overview. The full Chinese version (`技术报告.md`) contains 20 sections with detailed code, schema, and version-by-version history. Read the Chinese version for everything below in greater depth — this English file is for quick orientation.

> **Version scheme (2026-07-05)**: single source `modules/version.py:VERSION` (`major.minor.patch`). Major = user-approval only; minor = big features (Claude bumps + notifies + logs in detail); patch = small changes (Claude's call, keyword note). Full rule in CLAUDE.md. **`7.1.4`** (patch) = single version source + event realtime board 6h/1d ranges + auto-reeval suggestions auto-cleared after >48h + fixed a missing `Executor` import in monitor (crashed on restart). **`7.1.5`** (patch) = mobile: latest-2 past-position cards (read-only); asset curve polish (gradient/smooth/buy-sell markers/cost line + green-red PnL zones/hover tooltip); /history cards → wider rectangular, bigger, more info; top-right op buttons moved above the stats row, recolored, dino responsive. `7.1.6` = removed the asset-curve buy/sell triangle markers (cost line + PnL zones kept). **`7.2.0`** (minor, first-3-waves overhaul complete) = **#6+#5 positions split into 3 tabs**: "current holdings" is now read-only (placed first → gets live prices; uses `.pos-row` without `data-slug` so cjApplyPos only targets the ops panel) | "reeval ops" (the old panel renamed + hidden by default, holding all edits/add/liquidate/API-reeval/TP-SL toggles + #5 paste-a-Claude-reeval-JSON one-click apply, routed by action, exit still confirms) | "reeval mode" unchanged. Zero duplicate IDs; money buttons untouched. **`7.3.0`** (minor, #8 stats-analytics overhaul) = audited existing metrics (all correct) + `db.get_history_extras()` (monthly trend w/ cumulative, sold-too-early `(final_outcome-exit_price)*size`, PnL distribution, by-exit-category) added to the analytics endpoint + /history gains Chart.js (trend bars+cumulative line, distribution bars) + sold-early cards + by-exit table + 60s auto-refresh. Insight: take-profit 100% win, reeval-exits are cutting winners, net sold-too-early ~$42. **`7.3.1~7.3.3`** = closed_positions rebuilt from Polymarket /activity to match (80 rows) + partial-sell guard + **🔴 v7.0 exit/GLM/dual-model had been reverted by a stale VS Code buffer (2026-06-26), recovered from git `6561636^`** (root cause: VS Code SSH stale tab overwrote the files). **`7.4.0`** (minor, #16 paper overhaul) = /paper now has a real lifecycle: active (open, not would-sell) vs history (`/api/paper/history`: sold/cleared/resolved + peak price + prediction-correct verdict + simulated win-rate stats). **`7.4.1`** (real-money, event-driven half-position protection) = after an event-driven position sells half at 0.92, if the held half drops ≥15% from 0.92 (best_bid<0.782) it is fully sold to lock the profit (`TAKE_PROFIT_HALF_PROTECT`), closing the gap where the held half previously rode with only the $0.05 floor. **`7.4.2`** (real-money, event-driven double-first → full sell) = "double vs 0.92-half, whichever comes first": for an event-driven position, if `(bid-avg)/avg ≥ +100%` triggers before 0.92 (low entry <$0.46 → 2×avg < 0.92) it is fully sold to lock the double; only when doubling is out of reach (entry ≥$0.46) does it reach 0.92 and sell half. The branch sits at the very top of 1a (double wins on a price gap up); the held half after a half-sell is no longer re-caught by +100% (it runs to resolution under the 0.782 protection). **`7.4.3`** (real-money, #11 stop-loss) = (a) event-driven gains a **-60%** entry-anchored % stop (`STOP_LOSS_PCT_BY_TIER["event_driven"]` None→0.60, very loose; breach → reeval + exit-guard, $0.05 floor still backstops), no longer floor-only; (b) confirm-tick is already 30s/tick (6 ticks = 3 min anti-spike, unchanged); (c) **no more "unclassified"**: unclassified now defaults to hybrid (monitor `tier = ... or "hybrid"` in two places, the -25% legacy retired), and the position tier dropdown forbids the blank option. (7.2.1–7.2.5 in CLAUDE.md changelog: view-panel polish / position sort / online-offline bug fix / api_reeval overview.) **This completes all 20 dashboard items + 4 strategy items.** **`7.4.4`** (real-money, hybrid → trailing stop) = hybrid's stop-loss changes from a fixed cost-anchored -35% to a trailing stop (≥35% retrace from the holding-period peak + 6-tick confirm), same form as convergent (new constant `TRAILING_STOP_PCT_HYBRID=0.35`; the (b) branch now runs trailing for `tier in ("convergent","hybrid")`, only event_driven keeps the entry anchor -60%).

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
  ├─ PositionMonitor()  ──→ thread (30s heartbeat)   # decision engine
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
5. **Auto monitoring** — `PositionMonitor` heartbeat (30s) checks all positions, auto-sells on TP/SL/TIME_STOP triggers
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
| Monitor heartbeat | Python thread in main.py | 30s |
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


---

## §20. v5.10 History Page + Research-Grade Analytics (Jun 3)

**Pain point**: v5.9's homepage "已结束仓位记录" section dumped all sold positions + post-sell price chart into the home view, drowning out the current-positions decision view. Worse, it actively **filtered out resolved markets** (`cur_price >= 0.98 or <= 0.02: skip`) — exactly the data a research project needs most (did we bet correctly? what's the win rate by tag/tier/cluster? what's the Claude estimate vs actual calibration gap?).

**Design philosophy**:
1. Page separation, not tab switch — new `/history` route, top nav adds 🏠 主页 / 📊 往期仓位监测.
2. Data first, UI second — extend `closed_positions` schema + resolution_check cron + tag capture at entry + backfill old data, *then* UI.
3. Research-grade = full + exportable. No pagination (few hundred rows), no filtering (resolved positions are the most valuable data).
4. Five core analytics dimensions: tag / tier / cluster / pnl / calibration.

**Schema changes**:
- `closed_positions` +6 cols: `cluster_id`, `tag`, `is_resolved` (default 0), `resolved_at`, `final_outcome` (held-side probability — Yes-held + Yes-won → 1, etc.), `is_correct` (auto-computed via `final_outcome >= 0.5`)
- `position_meta` +1 col: `tag` (copied to `closed_positions.tag` on sell via monitor / force_exit / execute_state)

**Resolution detection**:
- `modules/resolution_check.py:check_resolution(token_id, side)` queries Gamma `markets?clob_token_ids=X`, parses `closed=true` + `outcomePrices`, maps to held-side via `clobTokenIds.index(token_id)` (fallback: side inference).
- Returns `None` for unsettled markets or ambiguous outcomes (only commits if outcome ∈ {≥0.99, ≤0.01}).
- `update_unresolved_closed_positions(limit=50)` scans `is_resolved=0`, runs check_resolution per row, calls `db.update_closed_resolution` (idempotent — `WHERE is_resolved=0` guards re-runs).
- Hooked into `monitor.run_loop` every `RESOLUTION_CHECK_ROUNDS=120` rounds (≈1h at 30s heartbeat). Non-blocking — failure logs warning, main decision loop continues.
- `scripts/backfill_closed_resolution.py` one-off backfill for legacy rows.

**Tag backfill**:
- Polymarket's `tags` field lives at the *events* endpoint, not markets. `scripts/backfill_closed_tag.py:fetch_tag_for_slug` tries `events?slug=<market_slug>` first, falls back via `markets?slug=X → events[0].id → events?id=N → tags`.
- `_pick_best_tag` ranks by scanner whitelist tier: tier=1 (Iran/Trump/Russia/...) > tier=2 > tier=3 > generic (World/Politics/Geopolitics/Middle East) > raw first. Picks 'Iran' over 'Middle East' when both present.

**Analytics helpers in `db.py`** (5 new pure-SQL functions):
| Function | Purpose |
|---|---|
| `get_closed_positions_in_progress(limit=200)` | `is_resolved=0` rows for "进行中" section |
| `get_closed_positions_resolved(limit=500)` | `is_resolved=1` rows for "已结算" section |
| `get_all_closed_positions()` | Full dump for CSV export + research table |
| `get_pnl_summary()` | total_count / win_rate / total_pnl_usd / avg_hold_hours / top_5_winners + losers |
| `get_win_rate_by_dim(dim)` | `dim ∈ {tag, stop_loss_tier, cluster_id}`. Group-by aggregation. |
| `get_calibration_report()` | Buckets `claude_raw_estimate` ∈ {50-60%, 60-70%, ..., 90-100%}, returns count + actual_win_rate + calibration_gap per bucket |

**`calibration_gap` interpretation**:
- Negative = Claude overconfident (estimated 80%, actually won 60% → gap=-20pp)
- Positive = Claude conservative
- arxiv 2505.02151 LLM overconfidence literature predicts -20 to -60pp gap. We're validating with real data.

**New API routes**:
- `GET /api/history/in_progress` → rows + batch cur_price + change_since_sell_pp
- `GET /api/history/resolved` → rows with is_correct
- `GET /api/history/analytics` → summary + by_tag + by_tier + by_cluster + calibration
- `GET /api/history/export?format={csv,json}` → CSV stream (Content-Disposition: attachment) or JSON

**`/history` page layout** (HISTORY_HTML template, 4 sections stacked, no tabs):
1. 🔵 **In Progress**: sold but unresolved. Table with sell_price / cur_price / change_since_sell_pp / tier / tag / cluster.
2. ✅ **Resolved**: did we bet correctly? Table with final_outcome + **is_correct (✓ won / ✗ lost, color-coded)**.
3. 📈 **Analytics**: 4 stat cards (total / cumulative PnL / win rate / avg hold) + Top 5 Winners/Losers + win-rate-by-{tag,tier,cluster} + Claude calibration report.
4. 📥 **Research dump + CSV export**: full table with all fields + 📥 export button.

**Top nav switcher**: `.pages-tab` div with 2 `<a>` (🏠 主页 / 📊 往期仓位监测). Active page gets `.ptab-active` class.

**Homepage closed-card section removed**: HTML block + `_closedData` / `loadClosed` / `renderClosed` JS deleted. `/api/closed_positions` route kept (different code path — uses polymarket data-api + trade rounds) but no longer called by home.

**File-by-file change map**:
| File | Type | Change |
|---|---|---|
| `modules/db.py:init_db` | edit | ALTER TABLE +6 cols (closed_positions) + 1 col (position_meta) |
| `modules/db.py:save_closed_position` | edit | signature adds cluster_id + tag kwargs |
| `modules/db.py:save_position_meta` | edit | signature adds tag kwarg |
| `modules/db.py` | add | update_closed_resolution / get_unresolved_closed_positions / update_closed_tag + 5 analytics helpers |
| `modules/monitor.py:auto_sell` | edit | pass cluster_id + tag to save_closed_position |
| `modules/monitor.py:run_loop` | edit | invoke update_unresolved_closed_positions every 20 rounds |
| `modules/dashboard.py:/api/force_exit, /api/execute_state, /api/record_position` | edit | pass cluster_id + tag through |
| `modules/dashboard.py` HTML nav | edit | add .pages-tab + 2 ptab links |
| `modules/dashboard.py` HTML closed-card section | **delete** | (CSS dead classes kept) |
| `modules/dashboard.py` | add | HISTORY_HTML template + /history route + 4 /api/history/* routes |
| `modules/resolution_check.py` | **new** | check_resolution + update_unresolved_closed_positions |
| `scripts/backfill_closed_resolution.py` | **new** | one-off resolution backfill |
| `scripts/backfill_closed_tag.py` | **new** | one-off tag backfill (events-endpoint + tier-priority pick) |

**Backfill verification**:
```
$ python3 scripts/backfill_closed_resolution.py
4 rows is_resolved=0 → 0 updated (markets not yet resolved as expected)

$ python3 scripts/backfill_closed_tag.py
4 rows tag=NULL → 4 updated
  ✓ gyeongsangnam → Global Elections
  ✓ trump-blockade → Iran
  ✓ abelardo-espriella → Global Elections
  ✓ us-iran-nuclear-deal → Iran
```

**Risk / rollback**:
- Gamma API outcome ambiguous (e.g., 0.5/0.5) → skip (treated as unresolved), retry next round.
- resolution_check failure does NOT block main heartbeat.
- Old backfill failure → NULL kept, analytics SQL uses `COALESCE(dim, '(未分类)')`.
- Full rollback: `git revert` v5.10 commits, schema additions are NULL-tolerant. `v4.db.bak_v510_<ts>` backup exists.

**Future (v5.11+)**: mobile responsive layout for /history, full historical price curve (Polymarket prices_history fetch), PDF report generation, ML calibration model (v6), multi-account comparison (N/A).

See Chinese 技术报告.md §二十 for full code listing, backfill walkthrough, and design rationale.

## 21. v5.10.2 — /history data-correctness overhaul + DNS-poisoning resilience (2026-06-12)

User reported the /history page was confusing and the numbers looked wrong. Self-audit found **three data-layer bugs** (the stats were largely garbage) plus local DNS poisoning that silently disabled live prices and resolution detection.

**Bug 1 — No-side PnL sign flipped.** `save_closed_position()` computed No-position PnL as `(avg - exit) × size`, assuming Yes-price convention. All three callers actually pass **held-token prices** (a No position passes No-token prices), so the correct formula is side-agnostic: `(exit - avg) × size`. Symptom: TAKE_PROFIT rows showed -75% losses, STOP_LOSS rows showed +58% gains. Fixed formula + migrated 9 historical rows (`scripts/migrate_v5_10_2.py`, idempotent via a `migrations` sentinel table, auto-backs-up v4.db).

**Bug 2 — No-side is_correct inverted.** `check_resolution()` already returns `final_outcome` as the **held side's** final probability, but `update_closed_resolution()` treated it as the Yes probability and re-flipped for No positions. Fixed to side-agnostic `is_correct = final_outcome >= 0.5`; migrated 7 rows. Resolved win rate corrected from 3/13 (23%) to 8/13 (62%).

**Bug 3 — Gamma /markets silently filters closed markets.** The hourly resolution cron logged `checked=50 updated=0` for weeks with zero errors. Root cause: **Gamma `/markets` does not return `closed=true` markets unless explicitly asked** — and closed markets are exactly what a resolution checker looks for. New 5-rung fetch chain in `_fetch_market_any()`: `clob_token_ids+closed=true` → `clob_token_ids` → `slug+closed=true` → `slug` → `events?slug`, with token-membership validation on slug paths. One backfill pass resolved **35 of 56** pending tokens (remaining 21 are genuinely open). Also fixed checker starvation: `get_unresolved_closed_positions()` now GROUPs BY token_id with limit 100 (old `ORDER BY exit_at DESC LIMIT 50` never reached the oldest tokens once backlog exceeded 50).

**DNS poisoning + process-wide DoH guard.** The machine's default resolver (Tailscale 100.100.100.100 → upstream) intermittently resolves `gamma-api.polymarket.com` to a Facebook IP and `clob.polymarket.com` to a Dropbox IP (classic GFW-style poisoning), breaking live prices, data-api positions (homepage showed 0 positions), and CLOB handshakes. New `modules/gamma_client.py`:
- `install_polymarket_dns_guard()` — monkey-patches `socket.getaddrinfo`: `*.polymarket.com` resolves via DoH (1.1.1.1 primary / 8.8.8.8 fallback, accessed by IP) with system-DNS fallback and a 60s negative cache. TLS SNI/cert verification still uses the original hostname — **zero security downgrade**. Covers requests/urllib3/httpx/CLOB SDK process-wide; installed at the top of main.py.
- `gamma_get()` — gamma-specific GET with a second pinned-IP retry layer (`urllib3.HTTPSConnectionPool(ip, server_hostname=host, assert_hostname=host)`), used by resolution_check and the dashboard's `_batch_cur_prices`.

**UI rewrite in plain language.** Section descriptions lost the SQL jargon; resolved cards now read "✅ 赌对了 — 押 NO, 结果就是 NO · 若持到结算可多赚 $X" (incl. new sold-vs-held-to-settlement delta); in-progress cards read "😬 卖早了 / 👍 卖对了"; analytics slimmed down (4 stat cards → small-sample warning banner when resolved < 20 → top-5 winners/losers → tag/tier tables side-by-side → cluster table and the raw research table collapsed behind `<details>`; calibration shows only non-empty buckets with a how-to-read note). Empty-string tags now bucket into `(未分类)` via SQL `NULLIF`.

**Verified**: post-migration totals 78 closed rows / 57 resolved / 30-27 W-L (52.6%) / realized PnL -$4.65; 21/21 in-progress cards fetch live prices through the guard; CSV export intact. Rollback: comment out the two `install_polymarket_dns_guard()` lines in main.py; db backup `v4.db.bak_<ts>` + sentinel prevents re-application.

See Chinese 技术报告.md §二十一 for the full walkthrough.

**§21.9 same-day addendum (2nd user feedback round)**: the "(未分类)" buckets and 0%/100% win rates in the dimension tables had two causes. (1) 41/57 resolved rows had no tag recorded — the old `backfill_closed_tag.py` suffered the same closed-market filtering bug, queried grouped events by market slug, and lacked the DNS guard. Rewritten with the dual-state query chain (bare + `closed=true` at every rung, market→event id/slug two-hop for tags): **42/42 tokens backfilled** (Iran 18, Global Elections 10, Ukraine 8 …), eliminating the unclassified bucket. (2) Tiny-sample percentages are noise: tables now show "样本少" instead of a percentage when n<3; "(未分类)" renders as greyed "(未记录)"; tier values are translated with a note that pre-v5.7 rows never recorded the field (unfixable); the tag table notes that **win rate and PnL can point in opposite directions** (wrong direction sold early can profit; right direction stopped out can lose) — which explains Global Elections at 20% win rate yet +$4.6. Real signal surfaced: Iran 17/18 (94%) vs Global Elections 2/10 and Ukraine 2/8.

**§21.10 third feedback round — dual win-rate semantics**: the user's strategy is "sell once a few pp of profit appear; final settlement is irrelevant", but §21's win rate measured settlement direction (is_correct) only. v5.10.3 splits the metrics: **💰 profit rate** (primary) = share of closed trades with realized_pnl > 0, computed over ALL closed rows with no settlement dependency (new profit_count/profit_rate in get_pnl_summary; get_win_rate_by_dim now aggregates all rows); **🎯 direction accuracy** (calibration) = is_correct over resolved rows only, kept as a judgment-quality check. Dim tables show both as x/y with percentages suppressed below n=3. First readings: overall profit rate 33/78 (42%), direction 30/57 (53%); Iran strong on both (16/24 money, 17/18 direction, +$12.79 cumulative); Trump/Science bleed money (0/4 each); Global Elections wrong on direction (2/10) yet nearly flat on money thanks to early exits.

## 22. v5.11 — site polish + Claude JSON fast lane (2026-06-12)

Rollback anchor: git tag `v5.10.3-final` (pushed to dev); full revert = `git reset --hard v5.10.3-final` + restart.

**JSON fast lane**: DISCOVERY's output spec now requires a machine-readable ```json array (slug/side/cur_price/q/confidence/stop_loss_tier/end_date/days_to_resolution/cluster_id/tag/reason — field names are a contract). The sizing card gained a paste box: `cjExtract()` robustly pulls JSON out of whatever the user pastes (direct parse → fenced block → bracket slice); each recommendation renders with two actions — "fill calculator" (populates all 6 sizing inputs + auto-computes; auto-applied when there's a single rec) and "record to position" (matches the live position row by slug and POSTs the complete `/api/record_position` payload — q, confidence, tier, cluster_id, tag, entry_reason — in one click, syncing row controls in place). Position rows now carry data-slug/side/avg/size/end/idx attributes; side mismatches prompt a confirm; missing positions toast a "buy first" hint.

**UI tidy-up (information preserved, low-frequency content folded)**: brand block (logo + version) on both navs, 🦖 favicon (kills the 404), smooth-scroll anchor chips (scan/sizing/positions/events/logs), TIER 3+4 tag chips and the auto-rules card collapsed into `<details>`, dead homepage sell-card CSS and stale version strings removed (footer said v5.6 since v5.6).

Verified: both pages 200 with all new elements present; no new errors in bot.log. See 技术报告.md §二十二.

**§22.5 addendum**: the JSON contract was synced into the claude.ai skill — `data/claude-skills/polymarket-discovery/SKILL.md` now carries the same ```json output requirement as prompts.py (hard requirement #6, a "part 3: machine-readable JSON" schema, and both self-check lists), and polymarket-discovery.zip was rebuilt with the original layout. The user must re-upload the zip manually (claude.ai → Settings → Capabilities → Skills). The reeval skill intentionally stays JSON-free — the dashboard's reeval panel is button-driven with no JSON consumer yet.

## 23. v5.12 — read-only mobile page /m (2026-06-12)

Rollback anchor: git tag `v5.11.1-final`. A 12.9KB zero-interaction page (desktop home is 191KB + chart.js) reusing three existing APIs: `/api/snapshot` (30s poll, extended additively with title/side/avg_price/size), `/api/realtime_movers` (30m/1h), `/api/portfolio_history` (hand-drawn canvas sparkline, no chart.js, 1D/1W). Layout: sticky header → 2×2 big numbers (assets/cash/PnL/count) → mini equity curve → movers list (pp + $) → position cards (side badge, big PnL%, avg→cur, value, q, days-left, monitor-state badge) → footer switches. Mobile UAs (`iPhone|Android.*Mobile|Windows Phone`; iPad counts as desktop) hitting `/` get a 302 to `/m`; `/?desktop=1` sets a 90-day `force_desktop` cookie to opt out, `/m?auto=1` clears it; desktop navs gained a 📱 link. PWA meta enables add-to-home-screen. The page is read-only by design rule — no reeval/scan/record/sell controls, ever. All six verification checks passed. See 技术报告.md §二十三.

## 24. v5.12 — JSON fast-lane draft persistence (2026-06-18)

Incremental change, no new git tag (builds on v5.12 /m). Rollback = revert this dashboard.py change (front-end only, no schema/API change).

**Pain point**: the JSON fast lane (§22) kept all its state in memory/DOM — the parsed recommendations (`cjRecs`), the pasted text, and the calculator's recommended amount. The real workflow is *fill the calculator to see the size → go buy on Polymarket → come back and record the position*, which always involves a page refresh in between — and a refresh wiped the whole lane plus the recommended amount, forcing a re-paste and re-compute.

**Fix (localStorage)**: three keys (browser localStorage, no secrets — only the recommendation data already shown on screen): `pm_cj_recs_v1` (parsed recs), `pm_cj_raw_v1` (raw paste), `pm_cj_calc_v1` (the 6 calculator inputs + the recommended-amount result HTML). `cjPersist()` writes recs+raw on a successful parse; `cjPersistCalc()` writes the calculator + amount on a successful `calcSize`. `cjRestore()` (bound to `window load`) restores the textarea, `cjRecs` (re-rendered), the calculator inputs and the amount HTML; the row-rendering was extracted into `cjRenderRecs()` so parse and restore share it. State persists until one of two things: the new `🗑 Clear` button (`cjClear` — also resets the calculator inputs, conf/tier back to medium/hybrid, and deletes the three keys), or a successful `📌 record-to-position` (`cjApplyPos` splices that rec out, re-renders, and rewrites the draft; clearing the last one zeroes it).

**Bug audit (record path)**: confirmed correct — `/api/record_position` validates `0 < tp < 1` (decimal) and the lane sends `r.q`, already decimal; `entry_price` from `data-avg` is decimal too; position rows carry all of `data-asset/slug/side/avg/size/end/idx`; the post-success inline-sync IDs `tp-/conf-/tier-{idx}` all exist and match. No unit mismatch or missing attribute.

**Verified**: `ast.parse` OK; after restart GET / 200 (240KB) with no template error; `🗑 Clear`/`cjClear`/`cjRestore`/`cjRenderRecs`/`cjPersist`/`cjPersistCalc`/the load hook all present in the rendered HTML; bot.log clean (9 positions). localStorage is per-browser/device (drafts don't follow you across browsers); first load needs one hard refresh to pick up the new JS. See 技术报告.md §二十四.

## 25. v6.0 · 6.1 — auto-reeval on big drawdown (the Claude-API connection): web research → human-in-loop / offline auto-execute (2026-06-18)

**v6.0 major release** — auto-reeval is the bot's first "automatic AI decision + offline real-money" capability, so it gets a major-version bump (the internal v5.13–v5.15 micro-versions are folded into v6.0). **v6.0 splits into two**: **6.1 = auto-reeval** (this §25 — the whole Claude-API connection + decisions + execution) / **6.2 = control panel + misc** (§26). New module `modules/auto_reeval.py` + two new tables (`auto_reeval_suggestions`, `app_state`) + an `autostop_disabled` column on `position_meta`. Requires `ANTHROPIC_API_KEY` in `.env` (silently disabled if absent).

**Motivation**: the old REEVAL was a manual copy-prompt→Claude.ai→refill-q loop; on a big drop a position only had the monitor's blind stop, while REEVAL itself often said "hold" by anchoring q to price. Goal: position drops → call the Claude API to do live web research → emit a structured decision → surface on the dashboard for approval (online) or auto-execute (offline). Let research decide whether to sell, not a blind threshold.

**Engine**: `run_auto_reeval()` runs synchronously on a background thread. Tools = `web_search_20260209` + `web_fetch_20260209` (max_content_tokens 8000) + a custom `submit_decision` tool. `thinking={type:"adaptive"}` + `output_config={effort}`; an agentic loop bounded by MAX_ROUNDS handles `pause_turn`, then `tool_choice` forces `submit_decision`; a `refusal` stop returns an error. The `submit_decision` schema (action / new_q / confidence / thesis_broken / headline_event / reason / sources) is the program-read contract. Table `auto_reeval_suggestions` (status: analyzing→pending→executed/dismissed/cleared/error). `monitor._maybe_trigger_auto_reeval` fires (only on the heartbeat branch where there is no auto-sell action) when loss exceeds the threshold — **never auto-sells**, surfaces a banner via `/api/auto_reeval/pending`. Latch (`has_active_auto_reeval`, status!=cleared) = one run per position until the user clears/re-arms. Cost knobs: `EFFORT=medium` + `MAX_TOKENS` cap.

**Online/offline + cancel_autostop + tiered triggers**: each tier triggers a reeval 5pp before its stop — convergent -15%, hybrid -30%, event_driven -30% (fixed, no -5pp), legacy -20% (default, TBD). A 4th decision `cancel_autostop` sets `position_meta.autostop_disabled=1` (monitor then skips the % stop, keeps only the $0.05 floor; row shows `🛑止损OFF`; do not reuse the deleted v5.6 freeze_*). Online/offline presence (`app_state` table): top toggle + a 30-min "are you here?" prompt + 1-min-no-reply → offline. **Online = pause the auto-API (copy-prompt manually, save money); offline = auto-API + auto-execute decisions (real money)** via `_auto_execute` (exit → re-fetch live position + sell + closed_positions row + clear meta; update_q → apply + recompute state; cancel_autostop → flag; hold → noop). update_q recomputes monitor_state immediately (no ≤30s wait). Routes: `/api/auto_reeval/{pending,trigger,confirm,dismiss,clear,clear_all}` + `/api/presence` (+ping).

**Gap-down guard (PENDING_REEVAL) + 6h cooldown + manual button + prompt unify**: the % stop no longer blind-sells — when the line is breached (incl. a one-heartbeat gap-down, e.g. SpaceX from above -15% to -33% in a single heartbeat tick — 180s back then) `_evaluate_position` returns `PENDING_REEVAL` (no action), the loop triggers a reeval, and it waits for the result. Only the $0.05 floor / reeval-disabled fall back to a hard sell; event_driven & autostop_off stay floor-only. While any non-cleared reeval exists it stays PENDING_REEVAL (no sell) until cleared. UI: badge "⏸ 等重评·暂不止损" (amber pulse) on home + panel. **Re-trigger throttle (v6.0.4, 2026-06-19)**: a position re-reevals only when "6h cooldown passed → a fresh baseline (current loss) is recorded on that tick → loss then becomes ≥10pp worse than that baseline" — replacing the old "6h + already ≥5pp worse than the last reeval → fire immediately" (which fired the moment time was up). Baseline lives in `position_meta.reeval_watch_loss` (cleared during cooldown and on each real trigger); `RETRIGGER_DROP_PCT` default 0.10 (`AUTO_REEVAL_RETRIGGER_DROP`); cooldown `AUTO_REEVAL_COOLDOWN_H` default 6h; latch = `has_inflight_auto_reeval` (analyzing/pending/manual). Manual 🤖 bypasses all of it. **🤖 API-reeval manual button** on every position (home state row + panel rows) calls `/api/auto_reeval/trigger` (bypasses cooldown/latch; runs the API regardless of presence) — for when you're away and don't want the copy-paste flow. **Prompt unified**: `auto_reeval._build_prompt` now uses `prompts.build_reeval_prompt` (the same prompt you copy to Claude, incl. the Gamma-fetched resolution rules) + an appended submit_decision instruction; falls back to the built-in `PROMPT_TMPL`. **Online stale-manual escalation (v6.0.5, 2026-06-19)**: when online, a breach (5pp before the stop) does **not** blind-sell — it enters PENDING_REEVAL + stores a `manual` card (home/panel red-flash) awaiting your confirm. If that card keeps flashing for more than `MANUAL_ESCALATE_MIN` minutes (default 2, `AUTO_REEVAL_MANUAL_ESCALATE_MIN`) with no action → the monitor heartbeat auto-escalates it to running the API (`run_and_store`). **Crucially the online-escalated result still only sits as pending awaiting your sell-confirm — it never auto-sells while online** (`run_and_store`'s online branch leaves it pending, never calls `_auto_execute`). Impl: `monitor.PositionMonitor._escalate_stale_manual_reevals` (renamed/expanded from `_adopt_manual_reevals_if_offline`): **offline** → adopt all manual cards immediately (offline = auto mode → `_auto_execute`, real money; preserves the v6.0.1 #3 "online-triggered card left behind after going offline" fix); **online** → only escalate manual cards whose `created_at` is ≥ `MANUAL_ESCALATE_MIN×60s` old (age via `_parse_iso_to_aware`). Both set status `analyzing` (placeholder, prevents re-adoption) + spawn `run_and_store(force_manual=False)`. Runs once per heartbeat (≤30s) so "2 min" is really a 2–2.5-min bucket. **Reeval history archive + cooldown countdown (v6.0.6, 2026-06-19)**: "clear" was always just `status='cleared'` (never a delete) — but cleared rows then vanished from view. Now a collapsed **"📜 重评历史 & 冷却状态"** section under the suggestion card (`<details>`, closed by default) lists every cleared record (`get_auto_reeval_history(60)`, newest first) with its decision (exit / q X%→Y% / cancel-autostop / hold) + trigger/decided/cleared timestamps + a "更多" expander (reason/confidence/thesis-broken/sources/headline — same fields as the live card). Each row shows **how long until that position can auto-reeval again**: `/api/auto_reeval/history` uses `auto_reeval_latest_per_token()` to find each token's latest record (only it shows cooldown; older same-token rows are `superseded`), checks it against current holdings + `COOLDOWN_HOURS`, and returns a state — **cooling** (`cd_end_ms` → client `cdTick()` updates "❄️ 还剩 2h13m" every second) / **armed** (6h passed → shows the `reeval_watch_loss` baseline + "再跌10pp 触发") / **inflight** (a live eval exists) / **closed** (position gone). Front end `arhLoad()` re-polls every 30s; the per-second countdown ticks locally without re-fetching. The **panel** adds a compact `❄️Xh` badge to position rows (`pCdBadge` reading `pCool()`'s 30s `/history` fetch).

**Multi-model: Zhipu GLM default + Claude fallback (v6.0.7, 2026-06-19)**: auto-reeval used Claude Opus only (expensive). Now it tries **Zhipu GLM (cheap) first; if the GLM call fails or yields no valid decision → it auto-falls-back to Claude Opus** — exactly the user's ask ("GLM default, on failure use Claude"). Orchestrator `run_auto_reeval` walks `_provider_order()` (default `['glm','claude']`, only providers with a key present; `AUTO_REEVAL_PRIMARY=claude` flips it) and returns the first **valid** decision (action ∈ the 4), tagged with `_provider`; all-fail → `{error}`. `_run_claude` = the original Anthropic web_search/web_fetch + submit_decision agentic loop (untouched). `_run_glm` = new: `zhipuai` SDK (`pip install zhipuai`, reads `ZHIPUAI_API_KEY`) → `chat.completions.create(model=GLM_MODEL, tools=[{type:web_search}])` (Zhipu's native web search) → model emits **one JSON decision** → `_parse_glm_decision` extracts + strictly validates (legal action; new_q coerced to 0-1, % handled; update_q must carry new_q); sources prefer GLM's returned web links. The decision dict is **field-identical** to Claude's, so `update_auto_reeval_decision`/`_auto_execute` need no changes. **Defensive invariant**: any GLM hiccup (SDK missing / no key / API error / JSON parse fail / illegal action) → error/exception → fall back to Claude; a bad GLM decision can never reach `_auto_execute` (verified with 5 mocked cases). Added a `provider` column (NULL-tolerant) to `auto_reeval_suggestions`; the home card + history "更多" show "由 智谱GLM / Claude 决策" so you can see which brain actually decided (esp. to catch GLM silently always-falling-back). `is_configured()` broadened from "ANTHROPIC_API_KEY present" to "`_ENABLED` and `_provider_order()` non-empty" — **no behavior change** when GLM key absent (`['claude']`, pure-Claude as before). **v6.0.8 (params maxed + live-tested, key added)**: per the user ("best model, push params high — it's cheap"), `GLM_MODEL` default is now **`glm-5.2`** (1M ctx / 128K out), with **thinking** (`thinking={"type":"enabled"}`) + **`reasoning_effort=max`** (via `extra_body`), web search via **`search_pro`** with `count=20` + `content_size=high`, `max_tokens=32000`, 600s client timeout. `_glm_create` degrades gracefully on `TypeError` (drops extra_body→thinking→temperature→max_tokens in order; only param-not-accepted degrades, real API errors propagate → Claude fallback). **Live-verified** against a real position (Russia/Kostyantynivka): GLM-5.2 researched ISW maps/deepstatemap, returned `update_q→0.28` in 40s with a full rationale + 3 real source URLs. **Parser hardening (important)**: GLM frequently emits **unescaped inner `"`** inside Chinese free-text, breaking strict JSON → `_parse_glm_decision` now falls back to `_glm_regex_extract` (strict `json.loads` first; on failure, regex-extract by the known schema — action/new_q/confidence/thesis_broken are simple types and always recoverable, reason/headline best-effort, sources via URL regex); unit-tested to recover decisions from quote-broken JSON. Config: add `ZHIPUAI_API_KEY` to `.env`; optional `AUTO_REEVAL_GLM_MODEL` (default `glm-5.2`), `AUTO_REEVAL_GLM_MAX_TOKENS` (32000), `AUTO_REEVAL_GLM_REASONING` (max), `AUTO_REEVAL_GLM_SEARCH_COUNT/ENGINE`, `AUTO_REEVAL_PRIMARY` (glm).

**Invariants / env**: decisions hold/update_q/exit/cancel_autostop (keep prompts + tool schema in sync); latch vs cooldown are two separate layers (don't merge); online-escalate vs offline-adopt are two distinct paths (don't merge) — **the online path must never call `_auto_execute`** (the user explicitly requires that online never blind-sells; the timeout only runs the API for you, it does not sell for you); GLM is best-effort primary, Claude is the reliable fallback — **`_run_glm` must return a strictly-validated decision or error out; never let a half-baked GLM decision reach `_auto_execute`**, and don't touch `_run_claude` (it's the fallback + prompt-contract baseline); env: `AUTO_REEVAL_{ENABLED,LOSS_PCT,COOLDOWN_H,RETRIGGER_DROP,MANUAL_ESCALATE_MIN,MODEL,MAX_TOKENS,MAX_ROUNDS,EFFORT}` + `ZHIPUAI_API_KEY` / `AUTO_REEVAL_{GLM_MODEL,GLM_MAX_TOKENS,PRIMARY}`. ⚠️ **Untested**: the offline auto-sell (`_auto_execute` exit branch) is in place but has never live-fired — real-money path; the GLM path also hasn't live-fired until a key is added. See 技术报告.md §二十五.

## 26. v6.0 · 6.2 — secondary-monitor control panel + misc (record-field reminder / urgent popup / days-left column) (2026-06-18)

Incremental, no new tag. New page `/panel` (module-level `PANEL_HTML` + route) + some front-end. Does not touch the read-only /m.

**/panel control panel**: an always-on "mission control" window for a secondary monitor, separate from the read-only /m (design rule: /m stays read-only). Landscape, all-JS, reuses existing APIs (`/api/snapshot` extended additively with `autostop_disabled`, `/api/auto_reeval/pending`, `/api/presence` (+ping), `/api/realtime_movers`). Four blocks: ① big numbers (assets/cash/PnL/count) + online state ② position table (side/name/reeval light/PnL%/state badge/days-left/🛑OFF/🤖) ③ to-do (pending/manual/analyzing/error, whole block flashes red when there's a pending) ④ movers (1h top-3, ▲▼pp) + recent auto-actions. Controls: big online/offline toggle (green/red), reeval confirm/dismiss/clear/copy-prompt/🤖, clear-all. Home nav gained a "🖥️ 控制台" button → `window.open('/panel', 1000×620)`.

**Record-field reminder**: saving a position row (`saveTP`) with any of stop-loss tier / q / confidence / cluster blank → a confirm popup (you can still save). Prevents "forgot the tier → becomes legacy".

**Urgent popup (no sound)**: a *new* pending/manual reeval pops a red-flashing overlay — inside the panel if it's open (+ the to-do block flashes), else on the home page (`#urgent-pop`). Tracks seen IDs so only new ones pop. No sound, by request.

**Days-left column**: every position on home + mobile /m shows "N days to resolution" (after the side). See 技术报告.md §二十六.

## 27. v7.0 — exit-strategy redesign (fix "selling winners at the bottom" + "round-trip to origin") (2026-06-22)

**Why**: across the 16 closes since 6/8, net realized ≈ **−$0.12** (take-profit gains almost exactly cancelled by cut-loss losses). Two event_driven positions (#79 Iran airspace −54%, #86 JD Vance −43%) were cut at the bottom on a "reeval said exit" yet both **resolved as wins** — ~$16 left on the table (~20% of the account). Theory: Kaminski & Lo (2014) — in mean-reverting / negatively-autocorrelated markets a price-based stop is negative-EV; Snowberg & Wolfers (2010) favorite-longshot bias. User scope = **root-cause + exit mechanics (no auto-scaling)**.

**Root-cause ① — anti-anchoring via a pre-dump price center**: the reeval used to feed only the depressed current price + entry + old q + news to the model → it anchored q to the bottom → slight-negative edge → "exit" → bottom-sell. New `auto_reeval._pre_dump_center(token_id, cur, hist=None)` reuses `executor.get_prices_history(interval='max', fidelity='60')`, takes a trailing window **excluding the last `CENTER_SKIP_H` hours (default 6h = the dump)**, and returns the **median**; returns None on insufficient data / `|center−cur|<0.01`; never raises. Threaded with minimal churn (no orchestration-signature change): `run_and_store` computes it once → `pos['_pre_dump_center']` (for the prompt) + `d['_pre_dump_center']/d['_price_curve_json']` (for the DB). `_build_prompt` reads it off `pos`, passes `build_reeval_prompt(pre_dump_center=)`, and appends a "anchor on the pre-dump center, not the depressed price" line. Both GLM + Claude flow through `_build_prompt` → consistent. `REEVAL_PROMPT` reframes the current price as "post-dump depressed" + adds a "pre-dump center (use as the blind-phase reference)" line via `_to_yes_no`; the new `.format` key is **always passed** (fallback empty string when None) to avoid a KeyError (the manual `/api/reeval_prompt` uses the same builder).

**Root-cause ② — event_driven exit guard**: `auto_reeval.guard_event_driven_exit(action, decision, tier, cur) -> (action_eff, downgraded, why)` (pure; no DB / no sell). An event_driven `exit` is honored only if `thesis_broken` OR `(new_q − cur) ≤ −EXIT_GUARD_EDGE` (default 0.08); else downgraded to `update_q` (apply new_q, keep holding). **Default-safe**: missing/unparseable new_q + not thesis_broken → keep holding. Wired into BOTH execution paths (offline `_auto_execute`, online `dashboard.auto_reeval_confirm`), before any sell, reading fresh `get_position_meta().stop_loss_tier`; online update_q return carries `why`.

**Exit ③ — tiered take-profit + partial half-sell**: `_evaluate_position` block 1a branches by tier — event_driven best_bid ≥ `TAKE_PROFIT_PRICE_EVENT_DRIVEN` (0.92) → **sell half** (`partial:True`, flag `tp_half_sold`); convergent within `TAKE_PROFIT_CONVERGENT_NEAR_DAYS` (3) days → 0.88 full; others → 0.90 full. **event_driven is excluded from the generic 0.90 full-sell** (else the retained half would be sold immediately, defeating "let it run"). `check_once` partial branch: sell size×0.5, on success → mark `tp_half_sold` + `save_closed_position(size=half, exit_reason='TAKE_PROFIT_HALF')` + **never clear_position_meta** (remainder keeps being managed; size halves on the next `get_positions`) + flag prevents repeat; on failure → no flag/no clear, retry next heartbeat.

**Exit ④ — convergent trailing stop + confirmation**: new `position_meta.peak_price` (NULL-tolerant), updated each heartbeat to `max(old, cur)` (DB + in-memory meta, mirroring the entry self-heal). Convergent stop switches to peak-anchored: drop ≥ `TRAILING_STOP_PCT_CONVERGENT` (0.20; ≤3d `_NEAR` 0.12) from peak, AND `self._trail_breach[token]` consecutive count ≥ `TRAILING_CONFIRM_ROUNDS` (6) (reset on recovery) → routes through the existing PENDING_REEVAL / hard-sell path. hybrid/legacy keep entry-anchored; event_driven unchanged (floor only). **Key fix in `_maybe_trigger_auto_reeval`**: when `state=='PENDING_REEVAL'`, skip the entry-anchored loss gate — otherwise a convergent trailing breach while still in profit-from-entry would deadlock (neither sell nor trigger a reeval). Confirmation is in-memory: a restart resets it (only *delays* a stop, never fabricates one — safe direction); peak_price is durable in the DB and PENDING_REEVAL is the durable consequential state.

**Exit ⑤ — logging for calibration**: `auto_reeval_suggestions` gains `pre_dump_center REAL` + `price_curve TEXT`; `update_auto_reeval_decision` writes them (cur_price already stored at trigger + new_q + center + curve → the triple co-located). `SELECT *` flows them to the dashboard with no route change.

**Invariants / don't revert**: partial / failed sell must **never** `clear_position_meta` (only a full successful sell clears); guard default-safe = hold when unsure; the $0.05 floor is always first and unconditional; confirmation count is **in-memory** (do NOT persist a "first-breach timestamp" — a long downtime would fire immediately on boot); center fetch failure → None, reeval proceeds; the new `build_reeval_prompt` `.format` key is always passed; the stop path uses `is_configured()` (not `is_enabled()`) so an emergency pause freezes rather than blind-sells; event_driven is excluded from the generic 0.90 full-sell. env: `AUTO_REEVAL_{CENTER_WINDOW_H=24, CENTER_SKIP_H=6, EXIT_GUARD_EDGE=0.08}`; monitor constants `TAKE_PROFIT_PRICE_EVENT_DRIVEN=0.92 / TAKE_PROFIT_HALF_FRACTION=0.5 / TAKE_PROFIT_PRICE_CONVERGENT_NEAR=0.88 / TAKE_PROFIT_CONVERGENT_NEAR_DAYS=3 / TRAILING_STOP_PCT_CONVERGENT=0.20 / _NEAR=0.12 / TRAILING_CONFIRM_ROUNDS=6`. ⚠️ thresholds are **low-confidence** directional values from a 51-trade sample — observe + recalibrate with the ⑤ logged data before tuning. See 技术报告.md §二十七 for the full Chinese version.

**Verification**: all 5 modules ast/import OK; guard 6 cases (incl. missing new_q → safe hold); partial (half-sell keeps meta + no repeat + remainder not full-sold by the 0.90 rule at 0.93); convergent trailing needs 6 consecutive confirms + resets on recovery; `_pre_dump_center` on a synthetic dump curve → 0.50 (not the 0.15 bottom); clean restart HTTP 200, peak_price live-written (both holds convergent). Kostyantynivka "data mismatch" was a false alarm (we hold No@0.787; the report mis-compared the Yes side).

## 28. v7.1 — paper trading / test positions (no real orders) (2026-06-22)

Drop uncertain / absurd-looking Claude recs into a **test book**: no real order is placed, but the bot tracks live price from your entry and runs the **exact same `_evaluate_position` algorithm** as real positions, so you can see whether the prediction plays out. Phase 1 = tracking + sim P&L + clear; Phase 2 = manual reeval (zero API, zero money).

**🔒 Hard rule (the user stressed repeatedly): paper positions never touch money.** `monitor._evaluate_paper_positions` + every `/api/paper/*` route are grep-audited to contain **no** `executor.sell/buy`, **no** `auto_reeval.run_and_store/run_auto_reeval/_run_glm/_run_claude`, **no** `messages.create/place_order`. Only read-only allowed: Gamma price fetch / `executor.get_best_bid` / `auto_reeval._pre_dump_center` (price-history read). Paper is a separate table + separate code path — the real paid auto-reeval structurally cannot reach it.

**Phase 1 — tracking + sim P&L + clear**: new `paper_positions` table (db.py) + CRUD. `_evaluate_paper_positions()` runs each heartbeat: Gamma fetches the held-side live price → builds a synthetic pos/meta → runs `_evaluate_position(..., breach_store=self._paper_trail_breach)` (a paper-only trailing-confirm counter, isolated from real) → updates cur/peak/state → on a hard action records the **first** would-sell snapshot (take-profit uses best_bid, else cur; computes sim P&L) → **keeps tracking to resolution** (closed + price converged to 0/1 → resolve). `executed_action=''` so rules always evaluate. `_evaluate_position` gained a `breach_store` param (defaults to `self._trail_breach`). Page `/paper` (PAPER_HTML): input via a manual form + paste-Claude-JSON one-click (entry defaults to the rec's cur_price); the add route resolves token_id/title/end_date from Gamma by slug+side. Nav links added on home + /history.

**Phase 2 — manual reeval (zero API/money)**: `/api/paper/reeval_prompt?id=` reuses `build_reeval_prompt` (the same prompt real positions use) + `_pre_dump_center` anti-anchoring + Gamma resolution text, **all read-only**, to generate a prompt. The page's "📋 copy reeval prompt" copies it; the user pastes it into Claude.ai for a **free** reeval, reads the new q, types it into the row's `q→` box and clicks "存q" → `/api/paper/update_q` (db.update_paper_q) applies it; the next heartbeat recomputes edge/state with the new q. **There is no "🤖 API reeval" button and nothing auto-calls the API** — fully isolated from the real paid auto-reeval.

**Invariants / verification**: no paper path ever calls `executor.sell/buy` or the paid auto-reeval API (grep-audit before changing); `/api/paper/add` must import `log_event`. Live-tested: added Kostyantynivka No@0.54 → after one heartbeat cur→0.786, sim +$4.56 (+46%), state HOLD; safety grep over all 6 routes + the evaluator returned NONE. Full Chinese version: 技术报告.md §二十八.
