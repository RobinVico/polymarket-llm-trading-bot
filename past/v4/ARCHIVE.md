# v4 — Archived

This is the v4 codebase, archived for historical reference.

**Not actively maintained or run.** As of 2026-05-16, v5 (at the repo root) supersedes v4.

## Key differences vs v5

The main change in v5 was the stop-loss logic. v4 had:
- Single DISASTER threshold: `entry − cur ≥ 25pp` → auto-sell

v5 replaced this with a 3-tier system:
1. **慢跌硬止损** — `cur ≤ stop_price(entry)` AND drop > 30 min → auto-sell
2. **急跌冻结** — `cur ≤ stop_price(entry)` AND drop < 30 min → freeze 24h
3. **绝对兜底** — `cur / entry < 40%` → auto-sell (catches sub-15¢ tail)

Where `stop_price(entry)` is tier-based:
- entry ≥ 50¢ → drop 25pp
- 30 ≤ entry < 50¢ → drop 18pp
- 15 ≤ entry < 30¢ → drop 10pp
- entry < 15¢ → None (rule 3 only)

All other code (scanner, executor, dashboard, prompts, tags) is identical between v4 and v5.

## Why archived

v4 used a single DISASTER threshold (-25pp) that fired too late for low-entry positions (e.g., 17¢ → can't lose 25pp) and too early on V-bounce events (May 2026 had 2 confirmed misses: Trump publicly insult May 14 settled at 100% after disaster_sold at 13%, Malta turnout sold at 22% then bounced to 44%).

v5's 3-tier addresses both: tier-based stop scales with entry, freeze mechanism waits 24h on fast drops to catch V-bounces.

## Run v4 (not recommended)

If you want to run v4 for some reason (testing, comparison):
```bash
cd ~/polymarket/past/v4
source .venv/bin/activate
nohup python3 main.py > output.log 2>&1 &
```

Note: v4 binds port 5051 just like v5. Stop v5 first (`pkill -f main.py`) before starting v4.
