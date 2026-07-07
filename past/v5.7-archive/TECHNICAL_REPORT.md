# Polymarket Bot — Technical Report (v5.7)

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
