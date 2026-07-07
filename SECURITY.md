# Security Policy

*[中文](SECURITY.zh.md)*

## Supported Versions

Only the version at `main` (currently v7.4) receives security updates. (v7.1 note: the paper-trading page `/paper` is strictly read-only — it never places orders and never calls the paid reeval API.)
Archived versions in `past/v4/`, `past/v5/`, and `past/v5.*-archive/` are frozen and not maintained.

## Reporting a Vulnerability

**Do NOT open a public GitHub issue for security problems.**

Email: open a GitHub issue with the title prefix `[SECURITY] - private contact request`
and the maintainer will reach out via a private channel.

### What we care about

High priority:
- Authentication bypass in the Flask dashboard (e.g., reaching `/api/force_exit` without `X-Forwarded-For` check)
- SQL injection or path traversal anywhere in `modules/`
- Secret leakage in commits / archived directories / log files
- Any way to trigger trades or fund movement without the configured password
- Polymarket CLOB API misuse that could lock or drain a funder wallet

Lower priority (still wanted):
- Race conditions in the SQLite layer that could cause data loss
- Untrusted input reaching `log.info` (log injection)
- Improper TLS settings, weak cookie configuration

Out of scope:
- Reports about strategy edge or trading logic (this is not a "secure trading strategy" claim — it's a personal trading bot)
- Third-party platform issues (Polymarket / UMA oracle / Polygon) — report those to the respective project
- Vulnerabilities in `past/` archives (frozen, not running)

## Response Window

This is a personal project. Best-effort response time is **7 days**. Critical issues that could drain funds are prioritized.

After we acknowledge and patch, we will disclose the issue publicly **28 days** after the fix is merged, crediting the reporter unless anonymity is requested.

## Threat Model (in case this is useful to other forks)

This bot intentionally operates with the following assumed-trusted boundaries:
- The machine running the bot (private keys are in plaintext `.env`)
- The Tailscale tailnet (devices in the tailnet are trusted)
- The dashboard password (32 char random, in `.env`)
- Browser `localStorage` on the trusted machine — the JSON fast lane caches drafts there (parsed recommendations, calculator inputs, recommended amount). **No secrets**: keys/password are never sent to the browser; this is only data already shown on the authenticated page.
- The `ANTHROPIC_API_KEY` (plaintext in `.env`, optional) — enables auto-reeval (v6.0): the bot calls the Claude API, sending the market title/slug for live web research, and **while you are "offline" it can place real sell orders without per-trade confirmation** (online it waits for your dashboard approval). Leave the key unset to disable the feature entirely. The key is never sent to the browser.

Anything reaching the bot from outside those boundaries should not be able to trigger trades. If you can demonstrate that, please report it.
