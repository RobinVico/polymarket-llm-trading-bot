# Polymarket v5.9

*[中文](README.zh.md)*

Semi-automatic Polymarket prediction-market trading bot. Stack = edge-based decision engine + 3-tier stop loss + 2 take-profit rules + local Flask dashboard + Tailscale-only public access with password auth. Probability calibration runs through a manual Claude.ai loop; monitoring and execution are automatic.

**Full technical report**: [TECHNICAL_REPORT.md](TECHNICAL_REPORT.md) (English summary) · [技术报告.md](技术报告.md) (full Chinese version)

## Current version at a glance (v5.9)

```
Auto-sell rules (priority high → low):
  1a. TAKE_PROFIT_PRICE   best_bid ≥ 90¢ → sell full
  1b. TAKE_PROFIT_PNL     (best_bid − avg) / avg ≥ +100% → sell full
  2.  STOP_LOSS           tiered by LLM classification at entry:
                            convergent  (hard data)  -20%
                            hybrid      (mixed)      -35%
                            event_driven (politics)  no %-stop, $0.05 floor
  3.  TIME_STOP           days to settle ≤ 2 + price drift < 5pp → sell full

Decision states (user-confirmed):
  HOLD / MARGINAL / SOFT_NEGATIVE / AT_TARGET
```

## Architecture

| Path | Role |
|---|---|
| `main.py` | Entry point. Initializes SQLite, starts monitor thread, runs Flask on `127.0.0.1:5051` (localhost-only, see Security below). |
| `modules/dashboard.py` | Flask UI + HTTP routes + password auth layer + login/logout. |
| `modules/monitor.py` | v5.9 decision engine (3-tier stop + 2 TP + TIME_STOP + edge-based, sweep guard, auto save to closed_positions), 180s heartbeat. |
| `modules/scanner.py` | Polymarket Gamma scanner with `FILTERS`. |
| `modules/executor.py` | py-clob-client v2 wrapper. Partial-fill detection (< 95% → retry). |
| `modules/db.py` | SQLite schema (WAL mode) + CRUD + portfolio_snapshot remote backup + closed_positions analytics + login_attempts. |
| `modules/prompts.py` | DISCOVERY + REEVAL v5.2 prompts. |
| `modules/tags.py` | 39-tag whitelist + blacklist + whitelist priority. |

## Versions archive

| Version | Status | Path |
|---|---|---|
| **v5.9** | **Current** (this README, repo root) | `./` |
| v5.6 | Archived (final v5.6 snapshot before public refactor) | [`past/v5.6-archive/`](past/v5.6-archive/) |
| v5.0 | Archived (3-tier stop + fast-drop freeze) | [`past/v5/`](past/v5/) |
| v4 | Archived (single -25pp DISASTER) | [`past/v4/`](past/v4/) |

Full v5.0 → v5.9 evolution (what changed each version, what problem it fixed) — see [TECHNICAL_REPORT.md](TECHNICAL_REPORT.md) (English) or [技术报告.md §十三](技术报告.md) (Chinese).

## Setup

```bash
git clone https://github.com/RobinVico/polymarket.git
cd polymarket
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in POLY_PRIVATE_KEY, POLY_FUNDER, POLY_SIGNATURE_TYPE=1
                      # and DASHBOARD_PASSWORD, FLASK_SECRET_KEY (32+ char random)
nohup python3 main.py > output.log 2>&1 &
```

Dashboard: <http://localhost:5051>

> `POLY_FUNDER` must be the proxy wallet (NOT the EOA). With `POLY_SIGNATURE_TYPE=1`, signing happens against the GNOSIS_SAFE owned by the EOA — mixing these silently fails.

## Security (v5.9)

Dashboard is **localhost-only by default** (`127.0.0.1:5051`). For remote access:
1. Set up Tailscale (`tailscale.com/download`) on the host and any client device
2. `tailscale serve --bg http://localhost:5051` exposes the dashboard to your tailnet only
3. From a tailnet device, visit `https://<your-host>.<your-tailnet>.ts.net` → first time requires `DASHBOARD_PASSWORD` → cookie valid 90 days

If you also want public-internet access (e.g., to share with non-Tailscale clients): `tailscale funnel --bg on`. The password layer still protects the dashboard. Brute-force attempts are rate-limited (5 fails → 30 min lockout, persisted across restarts).

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
- `closed_positions` table (v5.9+) stores realized PnL / hold duration / exit reason / Claude original q-estimate for every closed position — used for win-rate and calibration analysis.

## File layout

```
polymarket/                          (GitHub public repo, v5.9 at root)
├── main.py
├── requirements.txt
├── .env.example                     # template — copy to .env and fill in
├── LICENSE                          # MIT
├── SECURITY.md / SECURITY.zh.md     # vulnerability disclosure
├── CLAUDE.md                        # project conventions (for Claude Code)
├── README.md / README.zh.md         # English / Chinese READMEs (this)
├── TECHNICAL_REPORT.md              # English technical report (high-level)
├── 技术报告.md                       # full Chinese technical report (17 sections, complete history)
├── modules/                         # v5.9 code
├── scripts/                         # cron + restore
├── data/.gitkeep                    # placeholder; portfolio_snapshot.jsonl is gitignored
├── v4.db                            # SQLite (gitignored)
└── past/
    ├── v5.6-archive/                # archived v5.6
    ├── v5/                          # archived v5.0
    └── v4/                          # archived v4
```

## Notes

- An older v3 codebase lives at `<sibling-v3-dir, not in this repo>`, frozen, not part of this repo.
- `.env` / `v4.db` / `*.log` / `.venv` are all gitignored.
- Uses `py-clob-client` v2 (import path remains `py_clob_client` after install).
- Do not run `past/v4/` or `past/v5/` alongside v5.6 (port 5051 conflict).
