# Polymarket v7.4

*[中文](README.zh.md)*

Semi-automatic Polymarket prediction-market trading bot. Stack = edge-based decision engine + 3-tier stop loss + 2 take-profit rules + Kelly-based position sizing + auto-reeval on big drops (Claude-API web research) + local Flask dashboard (desktop / mobile / control-panel / history-analytics pages) + Tailscale-only public access with password auth. Probability calibration runs through a manual Claude.ai loop **or** an automatic Claude-API web-research reeval on big drawdowns (v6.0); monitoring and execution are automatic.

**Full technical report**: [TECHNICAL_REPORT.md](TECHNICAL_REPORT.md) (English summary) · [技术报告.md](技术报告.md) (full Chinese version)

## Current version at a glance (v7.4.4)

```
Auto-sell rules (priority high → low):
  1a. TAKE_PROFIT (tiered):
        event_driven   whichever fires FIRST (v7.4.2):
                         (best_bid−avg)/avg ≥ +100% → sell FULL (locks the double; entry <$0.46)
                         best_bid ≥ 0.92            → sell HALF, the rest runs to resolution
                         held half: best_bid < 0.782 (−15% off 0.92) → sell the rest (v7.4.1)
        convergent ≤3d best_bid ≥ 0.88 → sell full
        others         best_bid ≥ 0.90 → sell full
  1b. TAKE_PROFIT_PNL     (best_bid − avg) / avg ≥ +100% → sell full (non-event_driven)
  2.  STOP_LOSS           tiered by LLM classification at entry:
        convergent   (hard data) → TRAILING: ≥20% off peak (≤3d: 12%) + 6-tick confirm
        hybrid       (mixed)     → TRAILING: ≥35% off peak + 6-tick confirm (v7.4.4)
        event_driven (politics)  → entry-anchored −60% (very loose, v7.4.3) + $0.05 floor
        unclassified             → treated as hybrid (v7.4.3; the −25% legacy tier retired)
        (auto-reeval ON: a breached stop defers to PENDING_REEVAL → the reeval decides;
         the reeval's q anchors to the PRE-DUMP price center, not the depressed price;
         an event_driven 'exit' is honored only if thesis_broken or edge ≤ -8pp, else → update_q)
  3.  TIME_STOP           days to settle ≤ 2 + price drift < 5pp → sell full

Decision states (user-confirmed):
  HOLD / MARGINAL / SOFT_NEGATIVE / AT_TARGET
```

**Pages**: `/` desktop dashboard (scan / sizing / positions / events / logs) · `/panel` secondary-screen control panel · `/m` read-only mobile page (mobile UAs auto-redirect) · `/history` closed-position analytics (resolution tracking, profit rate vs direction accuracy, calibration, Chart.js trends) · `/paper` paper-trading book · `/tags` dynamic hot-tag board.

**New since v5.10**: held-token PnL & is_correct semantics fixed with data migration (§21), Gamma `closed=true` resolution fetch chain + per-process DoH DNS guard against local DNS poisoning (§21), Claude JSON fast lane — paste DISCOVERY's machine-readable block to fill the sizing calculator and record a position in one click (§22), read-only mobile page `/m` (§23), JSON fast-lane draft persistence — the parsed recs and recommended amount survive a page refresh until you clear them or record the position (§24).

**New since v5.12**: **auto-reeval on big drawdown (§25)** — on a big drop the bot calls the Claude API to do live web research and emit a structured decision (hold / update_q / exit / cancel_autostop); **online you approve it, offline it auto-executes (real money)**. A breached %-stop now defers to `PENDING_REEVAL` (the reeval decides) instead of blind-selling; a per-position **🤖 API-reeval** button + 6h cooldown. Plus a secondary-monitor **control panel `/panel`**, a record-field reminder, an urgent red-flash popup, and a days-to-resolution column (§26).

**New in v7.0**: exit-strategy redesign (§27) to stop selling winners at the bottom — ① the reeval anchors q to the **pre-dump price center** (not the depressed price; the root of the #79/#86 bottom-sells); ② an event_driven `exit` is honored only if the thesis broke or edge ≤ -8pp, else downgraded to `update_q` (keep holding); ③ **tiered take-profit** (event_driven sells half at 0.92 and lets the rest run; convergent near settlement at 0.88); ④ convergent switches to a **confirmed trailing stop** from the peak; ⑤ the reeval (q / price / pre-dump center) + the price curve are logged for calibration.

**New in v7.1**: paper trading — a `/paper` test book where you drop uncertain / absurd-looking Claude recs (no real order placed), and the bot tracks live price from your entry running the **same algorithm** as real positions, so you can see how the call plays out (§28). Manual reeval only (copy the prompt → paste into Claude.ai for free → apply the new q). **It never places orders and never calls the paid API** — grep-audited.

**New in v7.2–v7.4**: positions card split into 3 tabs — read-only holdings / an ops panel (all edits + one-click apply of a pasted Claude reeval JSON) / reeval mode (7.2); `/history` stats overhaul — Chart.js monthly trend + cumulative line, PnL distribution, sold-too-early analysis, by-exit-category table, `closed_positions` rebuilt 1:1 from Polymarket fills (7.3); paper-trading lifecycle — active vs history with peak price, prediction-correct verdicts and simulated win-rate (7.4.0); real-money exit tightening — event-driven "double vs 0.92-half, whichever first" (7.4.2), post-half-sell 0.782 protection (7.4.1), a loose −60% event stop replacing floor-only (7.4.3), hybrid switched to a 35% trailing stop off the peak (7.4.4); auto-reeval is dual-model — Claude authoritative + Zhipu GLM on the `/api_reeval` comparison page. Version numbering now has a single source (`modules/version.py`). Full changelog: [TECHNICAL_REPORT.md](TECHNICAL_REPORT.md) header · [技术报告.md](技术报告.md) 版本号规则.

## Architecture

| Path | Role |
|---|---|
| `main.py` | Entry point. Installs the polymarket DNS guard, initializes SQLite, starts monitor thread, runs Flask on `127.0.0.1:5051` (localhost-only, see Security below). |
| `modules/dashboard.py` | Flask UI (desktop `/` + mobile `/m` + analytics `/history`) + HTTP routes + password auth layer. |
| `modules/monitor.py` | Decision engine (3-tier stop + 2 TP + TIME_STOP + edge-based, sweep guard, auto save to closed_positions), 30s heartbeat + hourly resolution check. A breached %-stop defers to `PENDING_REEVAL` (v6.0). |
| `modules/auto_reeval.py` | v6.0 auto-reeval — Claude-API live web research (web_search + web_fetch + forced `submit_decision`) on big drops → structured decision; online-approve / offline-auto-execute; latch + 6h cooldown. |
| `modules/scanner.py` | Polymarket Gamma scanner with `FILTERS`, parallel order-book checks (v5.8). |
| `modules/executor.py` | py-clob-client v2 wrapper. Partial-fill detection (< 95% → retry). |
| `modules/db.py` | SQLite schema (WAL mode) + CRUD + portfolio_snapshot remote backup + closed_positions analytics + login_attempts. |
| `modules/sizing.py` | v5.9 position-size formula (¼-Kelly + drawdown budget + cluster cap). |
| `modules/clusters.py` | Correlation-cluster exposure accounting + cluster dictionary injection for DISCOVERY. |
| `modules/resolution_check.py` | Hourly market-resolution detector (5-rung Gamma fetch chain incl. `closed=true`). |
| `modules/gamma_client.py` | Gamma HTTP client with DoH pinned-IP fallback + process-wide DNS guard (v5.10.2). |
| `modules/prompts.py` | DISCOVERY + REEVAL prompts (incl. v5.11 machine-readable JSON output contract). |
| `modules/tags.py` | 39-tag whitelist + blacklist + whitelist priority. |

## Versions archive

| Version | Status | Path |
|---|---|---|
| **v7.4.4** | **Current** (this README, repo root) | `./` |
| v5.9 | Archived (sizing formula + clusters) | [`past/v5.9-archive/`](past/v5.9-archive/) |
| v5.8 | Archived (parallel scanner) | [`past/v5.8-archive/`](past/v5.8-archive/) |
| v5.7 | Archived (security + persistence hardening) | [`past/v5.7-archive/`](past/v5.7-archive/) |
| v5.6 | Archived (final v5.6 snapshot before public refactor) | [`past/v5.6-archive/`](past/v5.6-archive/) |
| v5.0 | Archived (3-tier stop + fast-drop freeze) | [`past/v5/`](past/v5/) |
| v4 | Archived (single -25pp DISASTER) | [`past/v4/`](past/v4/) |

Full v5.0 → v7.4.4 evolution (what changed each version, what problem it fixed) — see [TECHNICAL_REPORT.md](TECHNICAL_REPORT.md) (English) or [技术报告.md §十三](技术报告.md) (Chinese).

## Setup

```bash
git clone https://github.com/RobinVico/polymarket.git
cd polymarket
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in POLY_PRIVATE_KEY, POLY_FUNDER, POLY_SIGNATURE_TYPE=1
                      # and DASHBOARD_PASSWORD, FLASK_SECRET_KEY (32+ char random)
                      # optional: ANTHROPIC_API_KEY to enable auto-reeval (§25); unset = feature off
nohup python3 main.py > output.log 2>&1 &
```

Dashboard: <http://localhost:5051> (mobile browsers are redirected to `/m`; opt out via the in-page "switch to desktop" link)

> `POLY_FUNDER` must be the proxy wallet (NOT the EOA). With `POLY_SIGNATURE_TYPE=1`, signing happens against the GNOSIS_SAFE owned by the EOA — mixing these silently fails.

## Security (v7.4)

Dashboard is **localhost-only by default** (`127.0.0.1:5051`). All pages (`/`, `/m`, `/history`) sit behind the same session auth. For remote access:
1. Set up Tailscale (`tailscale.com/download`) on the host and any client device
2. `tailscale serve --bg http://localhost:5051` exposes the dashboard to your tailnet only
3. From a tailnet device, visit `https://<your-host>.<your-tailnet>.ts.net` → first time requires `DASHBOARD_PASSWORD` → cookie valid 90 days

If you also want public-internet access (e.g., to share with non-Tailscale clients): `tailscale funnel --bg on`. The password layer still protects the dashboard. Brute-force attempts are rate-limited (5 fails → 30 min lockout, persisted across restarts).

**Auto-reeval (v6.0)**: if you set `ANTHROPIC_API_KEY`, the bot calls the Claude API and sends the market title/slug for live web research; **while you are "offline" it can place real sell orders without confirmation** (online it waits for your approval). `ANTHROPIC_API_KEY` is a secret — keep it in `.env` (gitignored). Leave it unset to disable the whole feature.

See [TECHNICAL_REPORT.md](TECHNICAL_REPORT.md) §15 or [技术报告.md §十五](技术报告.md) for the full design.

## Restart after code change

```bash
pkill -f "main.py" 2>/dev/null; sleep 2
lsof -ti:5051 2>/dev/null | xargs kill -9 2>/dev/null; sleep 1
source .venv/bin/activate && nohup python3 main.py > output.log 2>&1 &
sleep 5 && tail -10 bot.log
```

## Persistence

- `v4.db` (SQLite, WAL mode, gitignored). Before any schema change: `cp v4.db v4.db.bak_$(date +%s)`.
- `data/portfolio_snapshot.jsonl` is exported locally every 30 min by `scripts/auto_backup.sh`. **Not pushed to the public repo** (contains real PnL history). For your own backup, push it to a private remote you control.
- `closed_positions` table (v5.10+) stores realized PnL / hold duration / exit reason / Claude original q-estimate for every closed position, plus resolution fields (`is_resolved` / `final_outcome` / `is_correct`, held-side semantics) filled by the hourly checker — feeds the `/history` profit-rate, direction-accuracy and calibration analytics. One-off data migrations are tracked in a `migrations` sentinel table.

## File layout

```
polymarket/                          (GitHub public repo, v7.4.4 at root)
├── main.py
├── requirements.txt
├── .env.example                     # template — copy to .env and fill in
├── LICENSE                          # MIT
├── SECURITY.md / SECURITY.zh.md     # vulnerability disclosure
├── CLAUDE.md                        # project conventions (for Claude Code)
├── README.md / README.zh.md         # English / Chinese READMEs (this)
├── TECHNICAL_REPORT.md              # English technical report (high-level)
├── 技术报告.md                       # full Chinese technical report (28 sections + changelog, complete history)
├── modules/                         # current (v7.4.4) code
├── scripts/                         # cron + restore + backfills + migrations
├── data/claude-skills/              # claude.ai SKILL zips (discovery / reeval / cluster-analyzer)
├── data/.gitkeep                    # placeholder; portfolio_snapshot.jsonl is gitignored
├── v4.db                            # SQLite (gitignored)
└── past/
    ├── v5.9-archive/                # archived v5.9
    ├── v5.8-archive/                # archived v5.8
    ├── v5.7-archive/                # archived v5.7
    ├── v5.6-archive/                # archived v5.6
    ├── v5/                          # archived v5.0
    └── v4/                          # archived v4
```

## Notes

- An older v3 codebase lives at `<sibling-v3-dir, not in this repo>`, frozen, not part of this repo.
- `.env` / `v4.db` / `*.log` / `.venv` are all gitignored.
- Uses `py-clob-client` v2 (import path remains `py_clob_client` after install).
- Do not run `past/v4/` or `past/v5/` alongside the current version (port 5051 conflict).

## Disclaimer

This software **trades real money on Polymarket automatically**. Review the source code and understand Polymarket's fee structure, resolution rules, and your local laws before running it (prediction markets are restricted in some jurisdictions, including under US CFTC rules). Do not run it with money you cannot afford to lose. See [LICENSE](LICENSE).
