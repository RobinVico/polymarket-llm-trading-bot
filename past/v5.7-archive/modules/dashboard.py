from flask import Flask, render_template_string, jsonify, request as flask_request, redirect, session
from modules.db import get_recent_events, init_db
from modules.executor import Executor
from modules.monitor import (TIME_STOP_DAYS, TIME_STOP_DRIFT_PP, HOLD_MIN_EDGE_PP,
                              SOFT_NEGATIVE_THRESHOLD_PP,
                              TAKE_PROFIT_PRICE, TAKE_PROFIT_PNL_PCT,
                              STOP_LOSS_PCT_BY_TIER, STOP_LOSS_PCT_LEGACY, EVENT_DRIVEN_FLOOR_PRICE)
from modules.prompts import DISCOVERY_PROMPT
from modules.scanner import scan_and_report
from datetime import datetime, timedelta
import json, subprocess, threading, logging, os, secrets, time

log = logging.getLogger("dashboard")
_monitor = None

def set_monitor(m):
    global _monitor
    _monitor = m

# === 简单密码登录 (v5.7 2026-05-29) ===
# 仅对非 127.0.0.1 来源生效. 本机 zero-auth, tailnet/Funnel 来源需密码.
# 5 次错密码 → 30 分钟 lockout. v5.7 (P11): 持久化到 SQLite login_attempts 表,
# 进程重启不会清零计数, 防"重启绕过爆破".
LOGIN_LOCKOUT_MAX = 5
LOGIN_LOCKOUT_WINDOW = 1800  # 30 min

def _rate_check(ip):
    from modules.db import get_login_attempt, delete_login_attempt
    rec = get_login_attempt(ip)
    if not rec:
        return True
    count = rec.get("fail_count", 0)
    start_ts = rec.get("window_start_ts", 0)
    if time.time() - start_ts > LOGIN_LOCKOUT_WINDOW:
        delete_login_attempt(ip)
        return True
    return count < LOGIN_LOCKOUT_MAX

def _rate_fail(ip):
    from modules.db import get_login_attempt, upsert_login_attempt
    rec = get_login_attempt(ip)
    now = time.time()
    if not rec or now - rec.get("window_start_ts", 0) > LOGIN_LOCKOUT_WINDOW:
        upsert_login_attempt(ip, 1, now)
    else:
        upsert_login_attempt(ip, rec.get("fail_count", 0) + 1, rec.get("window_start_ts"))

def _rate_clear(ip):
    """v5.7 (P11): on successful login, clear failed attempts so legitimate user not locked next time."""
    from modules.db import delete_login_attempt
    delete_login_attempt(ip)

LOGIN_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Polymarket Dashboard · Login</title>
<style>
body{margin:0;font-family:-apple-system,'Helvetica Neue',sans-serif;background:#0a0a1e;color:#e0e0f0;display:flex;align-items:center;justify-content:center;min-height:100vh}
.box{background:#15152e;border:1px solid #2a2a4a;border-radius:12px;padding:32px 28px;width:300px;box-shadow:0 4px 24px rgba(0,0,0,0.4)}
h1{font-size:16px;margin:0 0 4px;color:#00c8ff;letter-spacing:0.5px}
.sub{font-size:11px;color:#7878a0;margin-bottom:22px}
label{font-size:11px;color:#9090b0;display:block;margin-bottom:6px}
input[type=password]{width:100%;box-sizing:border-box;background:#0a0a1e;border:1px solid #2a2a4a;color:#e0e0f0;padding:10px 12px;border-radius:6px;font-family:'JetBrains Mono',monospace;font-size:13px;outline:none}
input[type=password]:focus{border-color:#00c8ff}
button{width:100%;margin-top:18px;background:#00c8ff;color:#0a0a1e;border:0;padding:10px;border-radius:6px;font-size:13px;font-weight:700;cursor:pointer}
button:hover{background:#33d6ff}
.err{color:#ff4070;font-size:11px;margin-top:12px;text-align:center}
</style></head><body>
<form class="box" method="post" action="/login">
<h1>Polymarket Dashboard</h1>
<div class="sub">请输入访问密码</div>
<label>Password</label>
<input type="password" name="password" autofocus required>
<button type="submit">Sign In</button>
{% if error %}<div class="err">{{ error }}</div>{% endif %}
</form></body></html>
"""

HTML = r"""
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Polymarket Semi-Auto</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=JetBrains+Mono:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root{--bg:#0a0a14;--sf0:#0f0f1c;--sf:#16162a;--sf2:#1c1c36;--sf3:#23234a;--bd:rgba(255,255,255,0.06);--bd2:rgba(255,255,255,0.10);--tx:#e8e8ff;--tx2:#9898c8;--tx3:#6868b0;--ac:#00e5a0;--ac2:#00c8ff;--rd:#ff4070;--am:#ffc040;--vi:#8060ff;--acd:rgba(0,229,160,0.10);--rdd:rgba(255,64,112,0.10)}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Space Grotesk',sans-serif;background:var(--bg);color:var(--tx);min-height:100vh;background-image:radial-gradient(ellipse 1400px 700px at 50% -10%,rgba(0,200,255,0.05),transparent 65%),radial-gradient(ellipse 800px 500px at 90% 100%,rgba(128,96,255,0.04),transparent 60%);background-attachment:fixed;position:relative}
body::before{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(255,255,255,0.012) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,0.012) 1px,transparent 1px);background-size:32px 32px;pointer-events:none;z-index:0}
.wrap{position:relative;z-index:1}
nav{background:rgba(10,10,20,0.72);backdrop-filter:blur(28px) saturate(180%);-webkit-backdrop-filter:blur(28px) saturate(180%);border-bottom:1px solid var(--bd);padding:0 28px;height:56px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.nl{display:flex;align-items:center;gap:8px;flex:1;min-width:0}
.logo{width:28px;height:28px;border-radius:8px;background:linear-gradient(135deg,#00e5a0,#00c8ff);display:flex;align-items:center;justify-content:center;font-weight:700;color:#060610;font-size:14px;font-family:'JetBrains Mono'}
.nt{font-size:14px;font-weight:600}.nt span{color:var(--tx3);font-weight:400;margin-left:8px;font-size:12px}
/* Chrome 离线小恐龙: 恐龙原地, 地面/仙人掌从右往左滚, 多只恐龙挨在一起一组跳 */
.dino-track{position:relative;flex:1;height:34px;margin-right:24px;min-width:280px;overflow:hidden;user-select:none}
.dino-track .ground{position:absolute;bottom:7px;left:0;right:0;height:1px;background:var(--tx3);opacity:0.55}
/* 仙人掌: 4.5s 一轮, 3 个错开 1.5s 等距出现 */
.dino-track .cactus{position:absolute;bottom:8px;line-height:1;animation:cactus-roll 4.5s linear infinite;will-change:left}
.dino-track .c1{animation-delay:0s;font-size:14px}
.dino-track .c2{animation-delay:-1.5s;font-size:11px}
.dino-track .c3{animation-delay:-3.0s;font-size:13px}
@keyframes cactus-roll{0%{left:calc(100% + 4px)}100%{left:-22px}}
/* 恐龙: 3 只挨在左边, 大小不一. emoji 🦖/🦕 默认朝左, 用 scaleX(-1) 翻成朝右,
   跟 cactus 移动方向(右→左)相对的方向一致 */
.dino-track .dino{position:absolute;bottom:7px;line-height:1;will-change:transform;animation:dino-jump 4.5s cubic-bezier(0.45,0,0.55,1) infinite}
.dino-track .d1{left:6px;font-size:18px}
.dino-track .d2{left:26px;font-size:13px}
.dino-track .d3{left:42px;font-size:15px}
/* 3 次跳精确对应 cactus 经过 dino 群中心 (~26px) 的时刻:
   cactus 经过 26px 大概在每轮的 23% / 57% / 90% (反推自 3 个 delay 错位的 cactus) */
@keyframes dino-jump{
  0%,18%,30%,52%,64%,86%,98%,100%{transform:scaleX(-1) translateY(0)}
  20%,28%{transform:scaleX(-1) translateY(-15px)}
  54%,62%{transform:scaleX(-1) translateY(-15px)}
  88%,96%{transform:scaleX(-1) translateY(-15px)}
}
.nr{display:flex;align-items:center;gap:12px}
.lp{display:flex;align-items:center;gap:5px;padding:4px 12px;background:var(--acd);border:1px solid rgba(0,229,160,0.2);border-radius:20px;font-size:10px;font-weight:600;color:var(--ac)}
.ld{width:5px;height:5px;border-radius:50%;background:var(--ac);animation:p 2s ease-in-out infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
.chip{font-family:'JetBrains Mono';font-size:10px;padding:4px 9px;background:var(--sf);color:var(--tx2);border:1px solid var(--bd);border-radius:14px;cursor:pointer;transition:all 0.15s;letter-spacing:0.3px}
.chip:hover{background:var(--sf2);color:var(--tx);border-color:var(--ac)}
.chip:active{transform:scale(0.94)}
.chip-flash{background:var(--acd);color:var(--ac);border-color:var(--ac)}
.tab{font-family:'Space Grotesk';font-size:12px;font-weight:500;padding:8px 16px;background:transparent;color:var(--tx3);border:none;border-bottom:2px solid transparent;cursor:pointer;transition:all 0.15s}
.tab:hover{color:var(--tx2)}
.tab-active{color:var(--ac);border-bottom-color:var(--ac);font-weight:600}
.tag-chip{font-family:'JetBrains Mono';font-size:10px;padding:5px 10px;background:var(--sf);color:var(--tx);border:1px solid var(--bd);border-radius:14px;cursor:pointer;transition:all 0.15s;letter-spacing:0.3px}
.tag-chip:hover{transform:translateY(-1px)}
.tag-chip:active{transform:scale(0.94)}
.tag-chip.tier1{border-color:rgba(0,229,160,0.3)}
.tag-chip.tier1:hover{background:var(--acd);border-color:var(--ac);color:var(--ac)}
.tag-chip.tier2{border-color:rgba(0,200,255,0.3)}
.tag-chip.tier2:hover{background:rgba(0,200,255,0.1);border-color:var(--ac2);color:var(--ac2)}
.tag-chip.tier3{border-color:rgba(128,96,255,0.3)}
.tag-chip.tier3:hover{background:rgba(128,96,255,0.1);border-color:var(--vi);color:var(--vi)}
.tag-chip.tier4{border-color:rgba(255,192,64,0.3)}
.tag-chip.tier4:hover{background:rgba(255,192,64,0.1);border-color:var(--am);color:var(--am)}
.tag-chip.flash-tier1{background:var(--acd);border-color:var(--ac);color:var(--ac)}
.tag-chip.flash-tier2{background:rgba(0,200,255,0.15);border-color:var(--ac2);color:var(--ac2)}
.tag-chip.flash-tier3{background:rgba(128,96,255,0.15);border-color:var(--vi);color:var(--vi)}
.tag-chip.flash-tier4{background:rgba(255,192,64,0.15);border-color:var(--am);color:var(--am)}
.btn-primary{background:linear-gradient(135deg,#00e5a0,#00c8ff)!important;color:#060610!important;border:none!important;font-weight:600}
.btn-primary:hover{filter:brightness(1.1);transform:translateY(-1px)}
.rb{font-size:10px;color:var(--tx3);background:var(--sf);border:1px solid var(--bd);border-radius:12px;padding:3px 10px;font-family:'JetBrains Mono'}
.wrap{max-width:1400px;margin:0 auto;padding:20px 20px 60px}
.toast{position:fixed;top:70px;right:20px;padding:12px 18px;border-radius:10px;font-size:12px;z-index:200;opacity:0;transform:translateY(-10px);transition:all .3s;max-width:360px}
.toast.show{opacity:1;transform:translateY(0)}.toast.ok{background:var(--acd);border:1px solid rgba(0,229,160,0.3);color:var(--ac)}.toast.err{background:var(--rdd);border:1px solid rgba(255,64,112,0.3);color:var(--rd)}
.sl{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:2px;color:var(--tx3);margin:24px 0 12px 2px;position:relative;padding-left:12px;display:flex;align-items:center}
.sl::before{content:'';position:absolute;left:0;top:50%;transform:translateY(-50%);width:3px;height:14px;background:linear-gradient(180deg,var(--ac2),var(--vi));border-radius:2px}
.ms{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}
@media(max-width:900px){.ms{grid-template-columns:repeat(2,1fr)}}
.m{background:linear-gradient(180deg,var(--sf) 0%,var(--sf2) 100%);border:1px solid var(--bd);border-radius:14px;padding:16px 18px;position:relative;overflow:hidden;transition:transform 0.2s,border-color 0.2s,box-shadow 0.2s}
.m:hover{transform:translateY(-2px);border-color:var(--bd2);box-shadow:0 8px 24px rgba(0,0,0,0.3)}
.m::before{content:'';position:absolute;top:0;left:0;right:0;height:2px}
.m.g::before{background:linear-gradient(90deg,#00e5a0,#00c8ff)}.m.r::before{background:linear-gradient(90deg,#ff4070,#ff8060)}.m.b::before{background:linear-gradient(90deg,#00c8ff,#8060ff)}.m.v::before{background:linear-gradient(90deg,#8060ff,#c060ff)}
.mi{font-size:16px;margin-bottom:8px}.ml{font-size:9px;font-weight:600;text-transform:uppercase;letter-spacing:1.5px;color:var(--tx3);margin-bottom:6px}
.mv{font-size:24px;font-weight:700;font-family:'JetBrains Mono';letter-spacing:-1px}
.msb{font-size:10px;color:var(--tx3);margin-top:6px}
.card{background:linear-gradient(180deg,var(--sf) 0%,var(--sf2) 100%);border:1px solid var(--bd);border-radius:14px;overflow:hidden;margin-bottom:16px;transition:border-color 0.2s,box-shadow 0.2s}
.card:hover{border-color:var(--bd2)}
.chd{padding:14px 18px;border-bottom:1px solid var(--bd);display:flex;justify-content:space-between;align-items:center}
.chd h2{font-size:12px;font-weight:600;color:var(--tx2)}.cnt{font-size:9px;padding:3px 8px;border-radius:8px;font-weight:600;font-family:'JetBrains Mono';background:var(--acd);color:var(--ac)}
.cb{max-height:500px;overflow-y:auto;scrollbar-width:thin}.cb::-webkit-scrollbar{width:3px}.cb::-webkit-scrollbar-thumb{background:var(--bd)}
.pos-hdr,.pos-row{padding:10px 18px;border-bottom:1px solid var(--bd);display:grid;grid-template-columns:minmax(180px,2.5fr) 40px 55px 55px 45px 65px 55px 70px 240px;gap:5px;align-items:center;font-size:11px}
.pos-hdr{font-weight:700;color:var(--tx3);font-size:10px;background:var(--sf2)}
.pos-row:hover{background:linear-gradient(90deg,rgba(0,200,255,0.05),transparent 80%)}
.pos-row .nm{font-weight:500;white-space:normal;word-break:break-word;line-height:1.35;padding-right:6px}
.pos-row .mono{font-family:'JetBrains Mono';font-size:10px}
.pos-row .cur-value,.pos-row .cur-pnl,.pos-row .cur-pnl-d{font-size:12px;font-weight:600}
.conf-sel{padding:2px 1px;border:1px solid var(--bd);border-radius:3px;font-size:9px;background:var(--bg2);color:var(--tx2);cursor:pointer;width:54px;margin-right:4px;height:22px}
.conf-sel:hover{border-color:var(--ac)}
.q-cell{display:flex;align-items:center;gap:4px}
.q-cell .tp-input{width:48px;padding:4px 6px;font-size:11px;height:24px;text-align:center;border:1px solid var(--bd);border-radius:3px}
.q-cell .q-pct{font-size:10px;color:var(--tx3)}
.q-cell .conf-sel{padding:2px 4px;border:1px solid var(--bd);border-radius:3px;font-size:10px;background:var(--bg2);color:var(--tx2);cursor:pointer;height:24px;width:50px}
.q-cell .conf-sel:hover{border-color:var(--ac)}
.q-cell .btn-small{height:24px;padding:0 8px;font-size:11px}
.tp-input{background:var(--bg);border:1px solid var(--bd);color:var(--ac);padding:3px 6px;border-radius:5px;font-family:'JetBrains Mono';font-size:10px;width:48px}
.tp-input:focus{outline:none;border-color:var(--ac)}
.btn-small{background:var(--acd);border:1px solid rgba(0,229,160,0.3);color:var(--ac);padding:3px 8px;border-radius:5px;font-size:9px;cursor:pointer;font-family:'Space Grotesk';font-weight:600;white-space:nowrap}
.btn-small:hover{background:rgba(0,229,160,0.2)}
.triggers{padding:8px 18px 12px 18px;border-bottom:1px solid var(--bd);background:rgba(16,16,40,0.5);display:grid;grid-template-columns:1fr 1fr;gap:10px}
.trig-section{display:flex;flex-direction:column;gap:3px}
.trig-label{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--tx3);margin-bottom:4px}
.trig-item{font-size:10px;font-family:'JetBrains Mono';padding:3px 8px;border-radius:4px;color:var(--tx2)}
.trig-item.green{background:rgba(0,229,160,0.06);border-left:2px solid var(--ac)}
.trig-item.red{background:rgba(255,64,112,0.06);border-left:2px solid var(--rd)}
.trig-item b{color:var(--tx);font-weight:600}
.reeval-cell{display:inline-flex;align-items:center}
.reeval-badge{font-family:'Space Grotesk';font-size:10px;padding:5px 10px;border-radius:14px;border:1px solid;cursor:pointer;transition:all 0.15s;letter-spacing:0.2px}
.reeval-badge.pending{background:rgba(255,180,0,0.15);color:#ffb400;border-color:#ffb400;cursor:pointer;animation:reevalPulse 2s ease-in-out infinite}
.reeval-badge.pending:hover{background:rgba(255,180,0,0.3);transform:translateY(-1px)}
.reeval-badge.done{background:rgba(120,120,120,0.1);color:var(--tx3);border-color:var(--bd);cursor:default;font-size:9px}
@keyframes reevalPulse{0%,100%{opacity:1}50%{opacity:0.6}}
.reeval-menu{margin:6px 0 12px 18px;padding:10px 14px;background:rgba(255,180,0,0.06);border-left:3px solid #ffb400;border-radius:6px;display:flex;flex-direction:column;gap:8px}
.reeval-menu-row{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.btn-danger{background:rgba(255,64,112,0.1);color:#ff4070;border:1px solid #ff4070}
.btn-danger:hover{background:rgba(255,64,112,0.2)}
.conf-sel{padding:3px 6px;border:1px solid var(--bd);border-radius:4px;font-size:11px;background:var(--bg2);color:var(--tx2);cursor:pointer;height:26px;width:60px}
.conf-sel:hover{border-color:var(--ac)}
.monitor-state-row{padding:6px 14px;display:flex;align-items:center;gap:6px;background:rgba(0,168,132,0.04);border-left:3px solid var(--ac);margin:6px 0 12px 18px;border-radius:4px}
.ms-label{font-size:10px;color:var(--tx3);font-weight:600}
.ms-badge{font-family:'JetBrains Mono';font-size:11px;padding:3px 9px;border-radius:11px;font-weight:600;letter-spacing:0.3px}
.ms-HOLD{background:rgba(150,150,150,0.15);color:#666}
.ms-MARGINAL{background:rgba(255,180,0,0.12);color:#cc9900}
.ms-SOFT_NEGATIVE{background:rgba(140,220,170,0.22);color:#3a8a5a}
.ms-AT_TARGET{background:rgba(0,229,160,0.2);color:#00a884;animation:reevalPulse 2s ease-in-out infinite}
.ms-TIME_STOP{background:rgba(180,80,0,0.15);color:#b05000}
.ms-STOP_LOSS{background:rgba(180,0,0,0.22);color:#b00000;font-weight:600}
.ms-TAKE_PROFIT_PRICE{background:rgba(0,200,140,0.25);color:#008055;font-weight:600}
.ms-TAKE_PROFIT_PNL{background:rgba(0,200,140,0.25);color:#008055;font-weight:600}
.reeval-dot{position:absolute;top:-3px;right:-3px;width:8px;height:8px;background:#ff9800;border-radius:50%;border:1.5px solid white;animation:reevalPulse 1.5s ease-in-out infinite;box-shadow:0 0 4px rgba(255,152,0,0.6)}
.reeval-panel{margin:0 18px 12px 18px;padding:10px 14px;background:rgba(0,168,132,0.05);border:1px solid rgba(0,168,132,0.2);border-radius:6px;animation:fadeIn 0.2s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:translateY(0)}}
.rv-grid{display:flex;gap:18px;flex-wrap:wrap}
.rv-step{flex:1;min-width:240px}
.rv-step-title{font-size:11px;font-weight:600;color:var(--tx2);margin-bottom:6px}
.rv-hint{font-size:10px;color:var(--tx3);margin-top:4px}
.rv-actions{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.newq-inp{width:80px;padding:4px 8px;border:1px solid var(--bd);border-radius:4px;font-size:12px}
.btn-warn{background:#ff9800;color:white}
.btn-warn:hover{background:#f57c00}
.ms-clickable{cursor:pointer;transition:all 0.15s}
.ms-clickable:hover{transform:scale(1.05);box-shadow:0 2px 6px rgba(0,0,0,0.15)}
.ms-PENDING{background:rgba(120,120,120,0.08);color:#999}
.ms-NO_META{background:rgba(120,120,120,0.08);color:#999}
.triggers-empty{padding:8px 18px;font-size:10px;color:var(--tx3);font-style:italic;border-bottom:1px solid var(--bd);background:rgba(16,16,40,0.3)}
@media(max-width:900px){.triggers{grid-template-columns:1fr}}
.tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:8px;font-weight:700;text-transform:uppercase}
.tag-sell{background:var(--rdd);color:var(--rd)}.tag-info{background:var(--acd);color:var(--ac)}.tag-error{background:var(--rdd);color:var(--rd)}
.tag-auto_sell{background:rgba(255,64,112,0.16);color:var(--rd);border:1px solid rgba(255,64,112,0.4)}
.tag-auto_sell.is-tp{background:rgba(0,200,120,0.16);color:#00a866;border-color:rgba(0,200,120,0.45)}
.tag-user_sell{background:var(--rdd);color:var(--rd)}
.tag-update_q{background:rgba(255,192,64,0.12);color:var(--am)}
.tag-reeval{background:rgba(128,96,255,0.14);color:var(--vi)}
.tag-scan{background:rgba(0,200,255,0.12);color:var(--ac2)}
.tag-scan_tag{background:rgba(0,200,255,0.12);color:var(--ac2)}
.rules{padding:18px 22px}
.rule{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid rgba(30,30,74,0.3);font-size:13px}
.rule:last-child{border-bottom:none}.rule .k{color:var(--tx3)}.rule .v{font-family:'JetBrains Mono';font-weight:500;color:var(--ac)}.rule .v.red{color:var(--rd)}
.pbox{background:var(--bg);border:1px solid var(--bd);border-radius:8px;padding:14px;font-family:'JetBrains Mono';font-size:10px;line-height:1.6;color:var(--tx2);max-height:300px;overflow-y:auto;white-space:pre-wrap;margin:12px 18px 18px}
.btn{padding:8px 16px;border-radius:8px;border:1px solid var(--bd);background:var(--sf2);color:var(--tx2);font-family:'Space Grotesk';font-size:11px;font-weight:600;cursor:pointer;transition:all .15s}
.btn:hover{border-color:var(--ac);color:var(--ac);background:var(--acd)}.btn.d:hover{border-color:var(--rd);color:var(--rd)}
.ctrls{display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:900px){.g2{grid-template-columns:1fr}}
.lr{padding:8px 18px;border-bottom:1px solid rgba(30,30,74,0.5);font-size:10px;display:grid;grid-template-columns:60px 50px 1fr;gap:8px;align-items:start}
.lr:hover{background:linear-gradient(90deg,rgba(0,200,255,0.04),transparent 80%)}.lr:last-child{border-bottom:none}
.lr .lt{font-family:'JetBrains Mono';font-size:9px;color:var(--tx3)}.lr .ldd{color:var(--tx2);line-height:1.4}
.ll{background:linear-gradient(180deg,var(--sf0) 0%,var(--bg) 100%);border:1px solid var(--bd);border-radius:14px;overflow:hidden}
.ll .chd{background:var(--sf)}
.lc{height:560px;overflow-y:auto;padding:10px 14px;font-family:'JetBrains Mono';font-size:10px;line-height:1.7;scrollbar-width:thin}.lc::-webkit-scrollbar{width:3px}.lc::-webkit-scrollbar-thumb{background:var(--bd)}
.ll2{padding:1px 0}.ll2 .ts{color:var(--tx3)}.ll2 .INFO{color:var(--ac2)}.ll2 .WARNING{color:var(--am)}.ll2 .ERROR{color:var(--rd)}.ll2 .msg{color:var(--tx2)}
.events-big .chd{padding:18px 22px}
.events-big .chd h2{font-size:14px}
.events-big .chd .cnt{font-size:10px;padding:4px 10px}
.events-big .lr{padding:14px 20px;font-size:12px;grid-template-columns:130px 110px 1fr;gap:14px;align-items:start}
.events-big .lr .lt{font-size:11px;color:var(--tx2);font-weight:500;font-family:'JetBrains Mono'}
.events-big .lr .ldd{font-size:12px;line-height:1.5;color:var(--tx)}
.events-big .lr .tag{font-size:10px;padding:4px 10px;border-radius:6px;letter-spacing:0.5px}
.ev-title{font-size:12px;font-weight:600;color:var(--tx);margin-bottom:5px;line-height:1.4}
.ev-detail{font-size:11px;color:var(--tx2);line-height:1.5;font-family:'JetBrains Mono';word-break:break-word}
.ev-state{display:inline-block;font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;margin-right:6px;letter-spacing:0.4px}
.ev-state.tp-price{background:rgba(0,200,140,0.18);color:#008055}
.ev-state.tp-pnl{background:rgba(0,200,140,0.18);color:#008055}
.ev-state.sl{background:rgba(255,64,112,0.18);color:#b00000}
.ev-state.ts{background:rgba(180,80,0,0.18);color:#b05000}
.ev-state.force-exit{background:rgba(128,96,255,0.18);color:#5040b0}
.ev-state.at-target{background:rgba(0,229,160,0.18);color:#00a884}
.ev-num{color:#00c8ff;font-weight:600}
/* 默认只显示 sell 类, 其他 (scan_tag / update_q / discovery / etc) 隐藏.
   只 scope 到 #events-card, 不要影响 #closed-card 等其他 events-big 容器 */
#events-card:not(.show-all) .ev-row:not(.ev-auto_sell):not(.ev-user_sell){display:none}
.ev-line{display:flex;gap:8px;font-size:11px;line-height:1.6;margin-top:3px;flex-wrap:wrap}
.ev-line .ev-key{color:var(--tx3);font-weight:600;min-width:48px;font-size:10px;text-transform:uppercase;letter-spacing:0.4px}
.ev-line .ev-v{color:var(--tx);font-family:'JetBrains Mono';word-break:break-word;flex:1}
/* 卖出事件压紧版: state/size/detail 一行, 跟在 title 下面 */
.ev-inline{display:flex;gap:6px;flex-wrap:wrap;align-items:center;font-size:11px;line-height:1.5;margin-top:2px}
.ev-inline .sep{color:var(--tx3);font-size:11px}
.ev-inline .ev-detail-mini{color:var(--tx2);font-family:'JetBrains Mono';font-size:10.5px;word-break:break-word;flex:1;min-width:200px}
.events-big .lr{padding:10px 18px}  /* 减小 padding 压紧 */
.events-toolbar{display:flex;gap:6px;align-items:center}
.events-toolbar .btn-small{font-size:10px;padding:4px 10px}
/* 已结束仓位记录: 每个 asset 一行, 行内多个 sell 卡片横向排开 */
.sell-cards-wrap{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px}
.sell-card{display:flex;flex-direction:column;gap:6px;padding:10px 12px;background:rgba(0,200,255,0.05);border:1px solid rgba(0,200,255,0.20);border-radius:8px;font-family:'JetBrains Mono';font-size:11px;line-height:1.4;min-width:260px;max-width:300px}
.sell-card .sc-h{color:var(--tx3);font-size:10px;font-weight:700;letter-spacing:0.5px;text-transform:uppercase;border-bottom:1px solid rgba(0,200,255,0.15);padding-bottom:4px}
.sell-card .sc-grid{display:grid;grid-template-columns:auto 1fr;gap:3px 10px;align-items:center}
.sell-card .sc-k{color:var(--tx3);font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.3px}
.sell-card .sc-v{color:var(--tx);font-size:11px}
.sell-card .sc-v .sc-sz{color:#ffc040;font-weight:600}
.sell-card .sc-v .sc-pr{color:#00c8ff;font-weight:600}
.sell-card .sc-v .sc-trigger{display:inline-block;padding:1px 7px;background:rgba(255,192,64,0.12);border-radius:4px;font-size:10px;font-weight:600;color:#cc9900}
.sell-card .sc-f{font-size:10.5px;font-weight:700;border-top:1px solid var(--bd);padding-top:5px;margin-top:auto}
.ev-pnl-pos{color:#00a884;font-weight:600}
.ev-pnl-neg{color:#b00000;font-weight:600}
.range-btn{padding:4px 10px;border:1px solid var(--bd);background:transparent;color:var(--tx3);border-radius:6px;cursor:pointer;font-size:11px;font-family:'JetBrains Mono';letter-spacing:0.5px}
.range-btn:hover{border-color:var(--ac2);color:var(--ac2)}
.range-btn.active{background:rgba(0,200,255,0.1);border-color:#00c8ff;color:#00c8ff}
#portfolio-canvas{display:block;width:100%!important;height:100%!important}
.emp{padding:40px;text-align:center;color:var(--tx3);font-size:11px}
footer{text-align:center;padding:20px;font-size:10px;color:var(--tx3)}
</style>
</head>
<body>
<nav><div class="nl"><div class="dino-track" title="Chrome 离线小恐龙 — 恐龙家族躲仙人掌"><span class="ground"></span><span class="cactus c1">🌵</span><span class="cactus c2">🌵</span><span class="cactus c3">🌵</span><span class="dino d1">🦖</span><span class="dino d2">🦕</span><span class="dino d3">🦖</span></div></div><div class="nr"><div class="lp"><div class="ld"></div>监控中</div><div class="rb" id="rb">30s</div></div></nav>
<div id="toast" class="toast"></div>
<div class="wrap">
<div class="ctrls">
<button class="btn" onclick="doAction('check')">🔍 检查持仓</button>
<button class="btn" onclick="doAction('refresh')">🔄 刷新</button>
<button class="btn d" onclick="if(confirm('停止?'))doAction('stop')">⏹ 停止</button>
</div>
<div class="sl">🔍 市场扫描器</div>
<div class="card">
<div class="tabs" style="padding:10px 18px 0;display:flex;gap:4px;border-bottom:1px solid var(--bd);margin-bottom:0">
<button class="tab tab-active" id="tab-tag" onclick="switchTab('tag')">🏷️ Tag扫描</button>
<button class="tab" id="tab-kw" onclick="switchTab('kw')">🔍 关键词扫描</button>
</div>

<div id="panel-kw" class="tab-panel" style="display:none">
<div style="padding:14px 18px 6px;display:flex;gap:6px;flex-wrap:wrap;align-items:center">
<span style="font-size:10px;color:var(--tx3);margin-right:4px">快捷:</span>
<button class="chip" onclick="setKw('iran')">iran</button>
<button class="chip" onclick="setKw('israel')">israel</button>
<button class="chip" onclick="setKw('ukraine')">ukraine</button>
<button class="chip" onclick="setKw('russia')">russia</button>
<button class="chip" onclick="setKw('ceasefire')">ceasefire</button>
<button class="chip" onclick="setKw('taiwan')">taiwan</button>
<button class="chip" onclick="setKw('china')">china</button>
<button class="chip" onclick="setKw('north korea')">north korea</button>
<button class="chip" onclick="setKw('venezuela')">venezuela</button>
<button class="chip" onclick="setKw('election')">election</button>
<button class="chip" onclick="setKw('prime minister')">prime minister</button>
<button class="chip" onclick="setKw('parliament')">parliament</button>
<button class="chip" onclick="setKw('scotus')">scotus</button>
<button class="chip" onclick="setKw('fda approval')">fda approval</button>
<button class="chip" onclick="setKw('gpt')">gpt</button>
<button class="chip" onclick="setKw('agi')">agi</button>
<button class="chip" onclick="setKw('spacex')">spacex</button>
<button class="chip" onclick="setKw('oscar')">oscar</button>
<button class="chip" onclick="setKw('time person of the year')">time person of the year</button>
<button class="chip" onclick="setKw('nobel')">nobel</button>
</div>
<div style="padding:0 18px 14px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
<input id="scanKw" placeholder="关键词 (如 iran, bitcoin, trump)" style="padding:8px 14px;border-radius:8px;border:1px solid var(--bd);background:var(--bg);color:var(--tx);font-family:'Space Grotesk';font-size:12px;width:260px">
<button class="btn" onclick="doScan('standard')">🚀 标准扫描</button>
<button class="btn" onclick="doScan('medium')">📊 中范围扫描</button>
<button class="btn" onclick="doScan('wide')">🌐 大范围扫描</button>
<button class="btn" onclick="copyScan()">📋 复制报告</button>
<button class="btn btn-primary" onclick="copyP()">🤖 复制给Claude</button>
<span id="scanStatus" style="font-size:11px;color:var(--tx3)"></span>
</div>
</div>

<div id="panel-tag" class="tab-panel">
<div style="padding:14px 18px 8px">
<div style="display:flex;gap:14px;align-items:center;margin-bottom:10px">
<span style="font-size:11px;color:var(--tx3);font-weight:600">范围:</span>
<label style="display:flex;align-items:center;gap:4px;font-size:11px;cursor:pointer"><input type="radio" name="tagMode" value="standard" style="cursor:pointer"> 标准</label>
<label style="display:flex;align-items:center;gap:4px;font-size:11px;cursor:pointer"><input type="radio" name="tagMode" value="medium" checked style="cursor:pointer"> 中范围</label>
<label style="display:flex;align-items:center;gap:4px;font-size:11px;cursor:pointer"><input type="radio" name="tagMode" value="wide" style="cursor:pointer"> 大范围</label>
<span style="font-size:10px;color:var(--tx3);margin-left:auto">点tag chip立即扫描</span>
</div>

<div style="margin-bottom:8px"><span style="font-size:10px;color:var(--ac);font-weight:600;letter-spacing:0.5px">TIER 1 重点 ⭐</span></div>
<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px">
<button class="tag-chip tier1" onclick="doTagScan('Iran')">Iran</button>
<button class="tag-chip tier1" onclick="doTagScan('Israel')">Israel</button>
<button class="tag-chip tier1" onclick="doTagScan('Ukraine')">Ukraine</button>
<button class="tag-chip tier1" onclick="doTagScan('Ukraine Peace Deal')">Ukraine Peace Deal</button>
<button class="tag-chip tier1" onclick="doTagScan('Russia')">Russia</button>
<button class="tag-chip tier1" onclick="doTagScan('China')">China</button>
<button class="tag-chip tier1" onclick="doTagScan('Taiwan')">Taiwan</button>
<button class="tag-chip tier1" onclick="doTagScan('Geopolitics')">Geopolitics</button>
<button class="tag-chip tier1" onclick="doTagScan('Middle East')">Middle East</button>
<button class="tag-chip tier1" onclick="doTagScan('World')">World</button>
<button class="tag-chip tier1" onclick="doTagScan('Foreign Policy')">Foreign Policy</button>
<button class="tag-chip tier1" onclick="doTagScan('Brazil')">Brazil</button>
<button class="tag-chip tier1" onclick="doTagScan('Mexico')">Mexico</button>
<button class="tag-chip tier1" onclick="doTagScan('Congress')">Congress</button>
<button class="tag-chip tier1" onclick="doTagScan('Global Elections')">Global Elections</button>
</div>

<div style="margin-bottom:8px"><span style="font-size:10px;color:var(--ac2);font-weight:600;letter-spacing:0.5px">TIER 2 中等</span></div>
<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px">
<button class="tag-chip tier2" onclick="doTagScan('Trump')">Trump</button>
<button class="tag-chip tier2" onclick="doTagScan('Trump Presidency')">Trump Presidency</button>
<button class="tag-chip tier2" onclick="doTagScan('SCOTUS')">SCOTUS</button>
<button class="tag-chip tier2" onclick="doTagScan('Politics')">Politics</button>
<button class="tag-chip tier2" onclick="doTagScan('US Politics')">US Politics</button>
<button class="tag-chip tier2" onclick="doTagScan('AI')">AI</button>
<button class="tag-chip tier2" onclick="doTagScan('OpenAI')">OpenAI</button>
<button class="tag-chip tier2" onclick="doTagScan('Tech')">Tech</button>
<button class="tag-chip tier2" onclick="doTagScan('Science')">Science</button>
<button class="tag-chip tier2" onclick="doTagScan('Venezuela')">Venezuela</button>
<button class="tag-chip tier2" onclick="doTagScan('SpaceX')">SpaceX</button>
<button class="tag-chip tier2" onclick="doTagScan('Primaries')">Primaries</button>
</div>

<div style="margin-bottom:8px"><span style="font-size:10px;color:var(--vi);font-weight:600;letter-spacing:0.5px">TIER 3 反向操作 (优先卖NO)</span></div>
<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px">
<button class="tag-chip tier3" onclick="doTagScan('Awards')">Awards</button>
<button class="tag-chip tier3" onclick="doTagScan('Pop Culture')">Pop Culture</button>
<button class="tag-chip tier3" onclick="doTagScan('Eurovision')">Eurovision</button>
</div>

<div style="margin-bottom:8px"><span style="font-size:10px;color:var(--am);font-weight:600;letter-spacing:0.5px">TIER 4 少量 (off-season / 长 fuse / 低 vol, 扫不到正常)</span></div>
<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px">
<button class="tag-chip tier4" onclick="doTagScan('FDA')">FDA</button>
<button class="tag-chip tier4" onclick="doTagScan('Immigration')">Immigration</button>
<button class="tag-chip tier4" onclick="doTagScan('Argentina')">Argentina</button>
<button class="tag-chip tier4" onclick="doTagScan('Germany')">Germany</button>
<button class="tag-chip tier4" onclick="doTagScan('France')">France</button>
<button class="tag-chip tier4" onclick="doTagScan('Box Office')">Box Office</button>
<button class="tag-chip tier4" onclick="doTagScan('Olympics')">Olympics</button>
<button class="tag-chip tier4" onclick="doTagScan('Nobel Peace Prize')">Nobel Peace Prize</button>
<button class="tag-chip tier4" onclick="doTagScan('OPEC')">OPEC</button>
</div>

<div style="display:flex;gap:8px;align-items:center;padding-top:8px;border-top:1px solid var(--bd)">
<button class="btn" onclick="copyScan()">📋 复制报告</button>
<button class="btn btn-primary" onclick="copyP()">🤖 复制给Claude</button>
<span id="tagScanStatus" style="font-size:11px;color:var(--tx3)"></span>
</div>
</div>
</div>

<div class="pbox" id="scanReport" style="max-height:400px">点击扫描按钮拉取市场数据...</div>
</div>
</div>
<div class="ms">
<div class="m {{ 'g' if total_pnl >= 0 else 'r' }}"><div class="mi">💰</div><div class="ml">总盈亏</div><div class="mv" id="m-pnl" style="color:{{ '#00e5a0' if total_pnl >= 0 else '#ff4070' }}">${{ "%.2f"|format(total_pnl) }}</div><div class="msb">所有持仓</div></div>
<div class="m b"><div class="mi">📦</div><div class="ml">持仓数</div><div class="mv" id="m-count">{{ positions|length }}</div><div class="msb">活跃标的</div></div>
<div class="m v"><div class="mi">💵</div><div class="ml">总投入</div><div class="mv" id="m-cost" style="font-size:18px">${{ "%.2f"|format(total_cost) }}</div><div class="ml" style="margin-top:8px">总收入</div><div class="mv" id="m-revenue" style="color:#00c8ff;font-size:18px">${{ "%.2f"|format(total_value) }}</div></div>
<div class="m b"><div class="mi">💼</div><div class="ml">资产组合</div><div class="mv" id="m-portfolio" style="color:#00c8ff;font-size:18px">${{ "%.2f"|format(assets_total) }}</div><div class="ml" style="margin-top:8px">现金</div><div class="mv" id="m-cash" style="color:#ffc040;font-size:18px">${{ "%.2f"|format(cash) }}</div></div>
</div>
<div class="sl">📈 资产总值曲线</div>
<div class="card" style="padding:18px">
<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px;gap:12px;flex-wrap:wrap">
<div style="flex:1;min-width:260px">
<div id="chart-delta" style="font-size:26px;font-weight:700;font-family:'JetBrains Mono';color:#00c8ff;letter-spacing:-0.5px;line-height:1.1">$0.00</div>
<div id="chart-delta-label" style="font-size:10px;color:var(--tx2);margin-top:3px">数值 = 当前组合 − 起点组合</div>
<div style="font-size:9px;color:var(--tx3);margin-top:6px;font-family:'JetBrains Mono';letter-spacing:0.2px">组合 = SUM(SELL+REDEEM+MERGE+REBATE) − SUM(BUY+SPLIT) + 持仓市值</div>
</div>
<div style="display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end">
<button class="range-btn active" data-range="1d" onclick="loadChart('1d')">1D</button>
<button class="range-btn" data-range="1w" onclick="loadChart('1w')">1W</button>
<button class="range-btn" data-range="1m" onclick="loadChart('1m')">1M</button>
<button class="range-btn" data-range="1y" onclick="loadChart('1y')">1Y</button>
<button class="range-btn" data-range="ytd" onclick="loadChart('ytd')">YTD</button>
<button class="range-btn" data-range="all" onclick="loadChart('all')">ALL</button>
</div>
</div>
<div style="position:relative;height:260px"><canvas id="portfolio-canvas"></canvas></div>
</div>
<div class="sl">📦 持仓详情</div>
<div class="card"><div class="chd"><h2>当前持仓 (填 q + 信心 + 止损档)</h2><span class="cnt">{{ positions|length }}</span></div>
<div class="cb">
<div class="pos-hdr">
<span>名称</span><span>方向</span><span>入场价</span><span>当前价</span><span>份数</span><span>当前价值</span><span>盈亏%</span><span>盈亏$</span><span>q + 信心 + 止损 + 保存</span>
</div>
{% for p in positions %}
<div class="pos-row" data-asset="{{ p.asset }}">
{% set pd = (p.cur_price - p.avg_price) * p.size %}<span class="nm">{{ p.title }}</span>
<span class="mono" style="color:{{ '#00a884' if p.side|upper == 'YES' else '#cc3050' }};font-weight:600">{{ p.side }}</span>
<span class="mono" style="color:#8060ff">${{ "%.3f"|format(p.avg_price) }}</span>
<span class="mono cur-price" style="color:#00c8ff">${{ "%.3f"|format(p.cur_price) }}</span>
<span class="mono" style="color:#ffc040">{{ "%.1f"|format(p.size) }}</span>
<span class="mono cur-value" style="color:{{ '#00e5a0' if pd >= 0 else '#ff4070' }}">${{ "%.2f"|format(p.cur_price * p.size) }}</span>
<span class="mono cur-pnl" style="color:{{ '#00e5a0' if p.pnl_pct >= 0 else '#ff4070' }}">{{ "%+.1f"|format(p.pnl_pct) }}%</span>
<span class="mono cur-pnl-d" style="color:{{ '#00e5a0' if pd >= 0 else '#ff4070' }}">{{ '+' if pd >= 0 else '-' }}${{ "%.2f"|format(pd|abs) }}</span>
<div class="q-cell">
<input type="number" step="1" min="0" max="100" class="tp-input" id="tp-{{ loop.index0 }}" placeholder="18" value="{{ (p.current_tp*100)|round|int if p.current_tp else '' }}" />
<span class="q-pct">%</span>
<select id="conf-{{ loop.index0 }}" class="conf-sel" title="原始研究信心: 高=严格(必须新信息) / 中 / 低=允许Claude元认知下调5pp" onchange="saveConf('{{ p.asset }}', this.value)">
<option value="" {% if not (p.meta and p.meta.original_confidence) %}selected{% endif %}>信心</option>
<option value="high" {% if p.meta and p.meta.original_confidence == 'high' %}selected{% endif %}>高</option>
<option value="medium" {% if p.meta and p.meta.original_confidence == 'medium' %}selected{% endif %}>中</option>
<option value="low" {% if p.meta and p.meta.original_confidence == 'low' %}selected{% endif %}>低</option>
</select>
<select id="tier-{{ loop.index0 }}" class="conf-sel" title="止损分级 (Claude 入场分类决定): 真相收敛 -20% / 混合 -35% / 事件驱动 不止损只用 $0.05 地板" onchange="saveTier('{{ p.asset }}', this.value)">
<option value="" {% if not (p.meta and p.meta.stop_loss_tier) %}selected{% endif %}>止损</option>
<option value="convergent"   {% if p.meta and p.meta.stop_loss_tier == 'convergent'   %}selected{% endif %}>收敛-20%</option>
<option value="hybrid"       {% if p.meta and p.meta.stop_loss_tier == 'hybrid'       %}selected{% endif %}>混合-35%</option>
<option value="event_driven" {% if p.meta and p.meta.stop_loss_tier == 'event_driven' %}selected{% endif %}>事件 $0.05</option>
</select>
<button class="btn-small" onclick="saveTP('{{ p.asset }}','{{ p.market_slug }}','{{ p.side }}',{{ p.avg_price }},{{ loop.index0 }},'{{ p.end_date }}',{{ p.size }})">保存</button>
</div>
<span class="reeval-cell" data-asset="{{ p.asset }}">
{% if p.should_reeval %}
<button class="reeval-badge pending" onclick="toggleReevalMenu('{{ p.asset }}')">⚠️ 进度 {{ (p.progress_pct*100)|round|int }}% 重评 TP ▾</button>
{% elif p.reeval_status == 'done_uplift' %}
<span class="reeval-badge done">✓ 已重评 (上调至 {{ (p.reeval_new_tp*100)|round(1) }}%)</span>
{% elif p.reeval_status == 'done_skip' %}
<span class="reeval-badge done">已跳过重评</span>
{% elif p.reeval_status == 'done_close' %}
<span class="reeval-badge done">已重评清仓</span>
{% endif %}
</span>
</div>
{% if p.should_reeval %}
<div class="reeval-menu" id="reeval-menu-{{ p.asset }}" style="display:none">
<div class="reeval-menu-row">
<button class="btn-small" onclick="copyReevalPrompt('{{ p.asset }}')">📋 复制 Claude Prompt</button>
<span style="font-size:10px;color:var(--tx3);margin-left:8px">→ 粘贴到 Claude.ai Research</span>
</div>
<div class="reeval-menu-row">
<span style="font-size:11px;font-weight:600">A. 上调 TP 到</span>
<input type="number" step="1" min="0" max="99" class="tp-input" id="reeval-tp-{{ p.asset }}" placeholder="95" />
<span style="font-size:10px;color:var(--tx3)">%</span>
<button class="btn-small" onclick="markReeval('{{ p.asset }}','uplift')">✓ 应用</button>
</div>
<div class="reeval-menu-row">
<button class="btn-small" onclick="markReeval('{{ p.asset }}','skip')">B. 跳过 (维持原TP)</button>
<button class="btn-small btn-danger" onclick="markReeval('{{ p.asset }}','close')">C. 提前清仓</button>
</div>
</div>
{% endif %}
{% if p.meta and p.meta.monitor_state %}
{% if p.meta and (p.meta.last_reeval_at or p.meta.created_at) or p.meta.entry_reason %}
<div class="meta-info" style="font-size:10px;color:var(--tx3);margin:0 18px 4px 18px;display:flex;gap:14px;flex-wrap:wrap">
{% if p.meta.last_reeval_at %}
<span>📅 上次重评: {{ p.meta.last_reeval_at[:16].replace('T',' ') }}</span>
{% elif p.meta.created_at %}
<span>📅 入场: {{ p.meta.created_at[:16].replace('T',' ') }} (从未重评)</span>
{% endif %}
{% if p.meta.original_confidence %}<span>🎯 confidence: {{ p.meta.original_confidence }}</span>{% endif %}
{% if p.meta.entry_reason %}<span style="max-width:380px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{{ p.meta.entry_reason }}">💡 {{ p.meta.entry_reason[:60] }}{% if p.meta.entry_reason|length > 60 %}...{% endif %}</span>{% endif %}
</div>
{% endif %}
<div class="monitor-state-row">
<span class="ms-label">决策状态:</span>
{% set st = p.meta.monitor_state %}
{% if st == "AT_TARGET" %}
<span class="ms-badge ms-AT_TARGET ms-clickable" onclick="executeState('{{ p.asset }}','AT_TARGET','{{ p.title|replace("'","&apos;")|truncate(50) }}', {{ p.size }}, {{ p.cur_price }})">AT_TARGET <span style="font-size:9px;opacity:0.7">点击执行</span></span>
<button class="btn-small" onclick="toggleReeval('{{ p.asset }}')" style="margin-left:8px;font-size:10px;padding:4px 10px">🔄 重评</button>
{% elif st in ("HOLD", "MARGINAL", "SOFT_NEGATIVE") %}
<span class="ms-badge ms-{{ st }} ms-clickable" onclick="toggleReeval('{{ p.asset }}')" style="position:relative">{{ st }} <span style="font-size:9px;opacity:0.7">点击重评</span>{% if p.needs_reeval %}<span class="reeval-dot" title="距上次重评 ≥24h"></span>{% endif %}</span>
{% else %}
<span class="ms-badge ms-{{ st }}">{{ st }}</span>
{% endif %}
<span class="ms-meta" style="font-size:10px;color:var(--tx3);margin-left:8px">
{% if p.current_tp %}q={{ (p.current_tp*100)|round(1) }}% &nbsp;|&nbsp; p={{ (p.cur_price*100)|round(1) }}% &nbsp;|&nbsp; edge={{ ((p.current_tp - p.cur_price)*100)|round(1) }}pp{% endif %}
{% if p.meta.executed_action %}&nbsp;|&nbsp; <span style="color:#888">已执行: {{ p.meta.executed_action }}</span>{% endif %}
</span>
<span style="margin-left:12px;display:flex;gap:6px;align-items:center">
<input id="addusd-{{ p.asset }}" type="number" step="0.5" min="1" placeholder="$" style="width:56px;padding:4px 8px;border:1px solid var(--bd);border-radius:6px;background:var(--bg);color:var(--tx);font-family:'JetBrains Mono';font-size:10px;height:24px" title="加仓美元金额">
<button class="btn-small" onclick="addPosition('{{ p.asset }}','{{ p.side }}','{{ p.title|replace("'","&apos;")|truncate(50) }}')" style="font-size:10px;padding:4px 10px;background:rgba(0,229,160,0.12);color:var(--ac);border-color:rgba(0,229,160,0.4)">＋加仓</button>
<button class="btn-small" onclick="forceLiquidate('{{ p.asset }}','{{ p.title|replace("'","&apos;")|truncate(50) }}',{{ p.size }},{{ p.cur_price }})" style="font-size:10px;padding:4px 10px;background:rgba(255,64,112,0.12);color:var(--rd);border-color:rgba(255,64,112,0.4)">✗清仓</button>
</span>
</div>
{% if st in ("HOLD", "MARGINAL", "SOFT_NEGATIVE", "AT_TARGET") %}
<div class="reeval-panel" id="rv-{{ p.asset }}" style="display:none">
<div class="rv-grid">
<div class="rv-step">
<div class="rv-step-title">1. 复制 Claude 重评 prompt</div>
<button class="btn-small" onclick="copyReevalPrompt('{{ p.asset }}')">📋 复制 prompt</button>
<div class="rv-hint">粘贴到 Claude.ai Research 模式, 等 5-10 分钟</div>
</div>
<div class="rv-step">
<div class="rv-step-title">2. 选 hold/update_q/exit</div>
<div class="rv-actions">
<button class="btn-small" onclick="markReevalHold('{{ p.asset }}')">维持 q (hold)</button>
<input type="number" id="newq-{{ p.asset }}" placeholder="新 q %" step="0.1" min="1" max="99" class="newq-inp">
<button class="btn-small btn-warn" onclick="submitNewQ('{{ p.asset }}')">更新 q</button>
<button class="btn-small btn-danger" onclick="reevalExit('{{ p.asset }}','{{ p.title|replace("'","&apos;")|truncate(50) }}', {{ p.size }}, {{ p.cur_price }})">建议清仓 (exit)</button>
</div>
</div>
</div>
</div>
{% endif %}
{% else %}
<div class="triggers-empty">👉 填入 q (Claude 校准估算) 后显示决策状态</div>
{% endif %}
{% endfor %}
{% if not positions %}<div class="emp">暂无持仓</div>{% endif %}
</div></div>
<div class="sl">🎯 事件中心</div>
<div class="card" style="padding:18px">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;gap:12px;flex-wrap:wrap">
<div style="display:flex;gap:4px">
<button class="range-btn active" data-mtab="realtime" onclick="switchMoverTab('realtime')">实时榜</button>
<button class="range-btn" data-mtab="movers" onclick="switchMoverTab('movers')">涨跌榜</button>
<button class="range-btn" data-mtab="value" onclick="switchMoverTab('value')">现值榜</button>
</div>
<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
<div id="realtime-range-group" style="display:flex;gap:4px">
<button class="range-btn" data-rtrange="30m" onclick="loadRealtime('30m')">30m</button>
<button class="range-btn active" data-rtrange="1h" onclick="loadRealtime('1h')">1h</button>
</div>
<div id="movers-range-group" style="display:none;gap:4px">
<button class="range-btn active" data-mrange="1d" onclick="loadMovers('1d')">1D</button>
<button class="range-btn" data-mrange="1w" onclick="loadMovers('1w')">1W</button>
</div>
<div id="metric-toggle-group" style="display:flex;gap:4px">
<button class="range-btn active" data-metric="pp" onclick="setMetricMode('pp')" title="按百分点排序">pp</button>
<button class="range-btn" data-metric="dollar" onclick="setMetricMode('dollar')" title="按美元盈亏排序">$</button>
</div>
<button class="range-btn" onclick="refreshMoverTab()" title="刷新最新数据 (绕过缓存)">↻</button>
<button class="range-btn" id="mover-more-btn" onclick="toggleMoverExpanded()">More</button>
</div>
</div>
<div id="realtime-section">
<div style="font-size:11px;color:#ffc040;font-weight:700;letter-spacing:0.5px;margin-bottom:6px">⚡ 异动 <span id="realtime-suffix">Top 3</span></div>
<div id="realtime-list" style="max-height:400px;overflow-y:auto"><div style="font-size:10px;color:var(--tx3);padding:8px 0">loading...</div></div>
</div>
<div id="movers-section" style="display:none">
<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">
<div>
<div style="font-size:11px;color:#00e5a0;font-weight:700;letter-spacing:0.5px;margin-bottom:6px">📈 涨幅 <span id="movers-g-suffix">Top 3</span></div>
<div id="movers-gainers" style="max-height:400px;overflow-y:auto"><div style="font-size:10px;color:var(--tx3);padding:8px 0">loading...</div></div>
</div>
<div>
<div style="font-size:11px;color:#ff4070;font-weight:700;letter-spacing:0.5px;margin-bottom:6px">📉 跌幅 <span id="movers-l-suffix">Top 3</span></div>
<div id="movers-losers" style="max-height:400px;overflow-y:auto"><div style="font-size:10px;color:var(--tx3);padding:8px 0">loading...</div></div>
</div>
</div>
</div>
<div id="value-section" style="display:none">
<div style="font-size:11px;color:#00c8ff;font-weight:700;letter-spacing:0.5px;margin-bottom:6px">💼 持仓现值 <span id="value-suffix">Top 3</span></div>
<div id="value-list" style="max-height:400px;overflow-y:auto"><div style="font-size:10px;color:var(--tx3);padding:8px 0">loading...</div></div>
</div>
</div>
<div class="sl">📝 操作记录</div>
<div class="card events-big" id="events-card"><div class="chd"><h2>卖出事件</h2><div class="events-toolbar"><span class="cnt" id="ev-cnt">{{ events|selectattr('event_type','in',['auto_sell','user_sell'])|list|length }}</span><button class="btn-small" id="ev-more-btn" onclick="toggleAllEvents()">更多</button></div></div><div class="cb" style="max-height:540px">
{% for e in events %}
{% set d = e.detail or '' %}
{% set is_tp_sell = e.event_type == 'auto_sell' and ('TAKE_PROFIT_PRICE' in d or 'TAKE_PROFIT_PNL' in d or 'AT_TARGET' in d) %}
<div class="lr ev-row ev-{{ e.event_type }}">
  <div class="lt">{{ e.timestamp[5:19].replace('T',' ') }}</div>
  <div><span class="tag tag-{{ e.event_type }}{% if is_tp_sell %} is-tp{% endif %}">{{ e.event_type }}</span></div>
  <div class="ldd">
    <div class="ev-title">{{ e.market_slug or '(无标题)' }}</div>
    {% if e.event_type in ('auto_sell','user_sell') %}
      {# 卖出事件: 压紧到一行 (state · size · detail), 跟在 title 下面 #}
      {% set state_badge = '' %}
      {% if 'TAKE_PROFIT_PRICE' in d %}{% set state_badge = '<span class="ev-state tp-price">📈 价格止盈</span>' %}
      {% elif 'TAKE_PROFIT_PNL' in d %}{% set state_badge = '<span class="ev-state tp-pnl">📈 浮盈翻倍</span>' %}
      {% elif 'STOP_LOSS' in d %}{% set state_badge = '<span class="ev-state sl">📉 止损</span>' %}
      {% elif 'TIME_STOP' in d %}{% set state_badge = '<span class="ev-state ts">⏰ 临结算锁定</span>' %}
      {% elif 'FORCE_EXIT' in d or 'force_exit' in d %}{% set state_badge = '<span class="ev-state force-exit">✗ 手动清仓</span>' %}
      {% elif 'AT_TARGET' in d %}{% set state_badge = '<span class="ev-state at-target">🎯 到目标</span>' %}
      {% endif %}
      {% set size_part = d.split('size=')[1].split(' ')[0] if 'size=' in d else '' %}
      <div class="ev-inline">
        {% if state_badge %}{{ state_badge|safe }}{% endif %}
        {% if size_part %}<span class="ev-num">{{ size_part }} 股</span><span class="sep">·</span>{% endif %}
        <span class="ev-detail-mini">{{ d }}</span>
      </div>
    {% else %}
      <div class="ev-detail">{{ d }}</div>
    {% endif %}
  </div>
</div>
{% endfor %}
{% if not events %}<div class="emp">暂无</div>{% endif %}
</div></div>

<div class="sl">📚 已结束仓位记录 (卖出后跟踪)</div>
<div class="card events-big" id="closed-card"><div class="chd"><h2>已卖出的仓位 + 卖后价格走势</h2><div class="events-toolbar"><button class="range-btn active" data-cprange="1w" onclick="loadClosed('1w')">1W</button><button class="range-btn" data-cprange="1m" onclick="loadClosed('1m')">1M</button><button class="range-btn" data-cprange="all" onclick="loadClosed('all')">全部</button><span class="cnt" id="closed-cnt">—</span></div></div><div class="cb" style="max-height:540px;overflow-y:auto" id="closed-list"><div style="padding:20px;color:var(--tx3);font-size:11px">加载中...</div></div></div>

<div class="sl">📏 自动规则</div>
<div class="card"><div class="rules">
<div class="rule"><span class="k">TAKE_PROFIT_PRICE (自动)</span><span class="v" style="color:#008055;font-weight:600">best_bid ≥ {{ take_profit_price_cent }}¢ → 全卖锁止盈 (最高优先级)</span></div>
<div class="rule"><span class="k">TAKE_PROFIT_PNL (自动)</span><span class="v" style="color:#008055;font-weight:600">(best_bid - avg)/avg ≥ +{{ take_profit_pnl_pct }}% (翻倍) → 全卖锁本</span></div>
<div class="rule"><span class="k">STOP_LOSS (LLM 入场分级)</span><span class="v red">收敛型 -{{ sl_convergent }}% / 混合型 -{{ sl_hybrid }}% / 事件驱动型 不止损 (价&lt;{{ sl_floor_cent }}¢ 地板兜底) / 未分类老仓位 -{{ sl_legacy }}%</span></div>
<div class="rule"><span class="k">TIME_STOP (自动)</span><span class="v red">距结算 ≤{{ time_stop_days }}天 且 漂移 &lt;{{ time_stop_drift_pp }}pp → 自动整笔卖</span></div>
<div class="rule"><span class="k">HOLD</span><span class="v" style="color:#888">edge > +{{ hold_min_edge_pp }}pp 持有不动</span></div>
<div class="rule"><span class="k">MARGINAL</span><span class="v" style="color:#cc9900">-{{ soft_neg_pp_abs }} ≤ edge ≤ +{{ hold_min_edge_pp }}pp 边缘地带</span></div>
<div class="rule"><span class="k">SOFT_NEGATIVE</span><span class="v" style="color:#3a8a5a">edge &lt; -{{ soft_neg_pp_abs }}pp (重评过) 已知, 自己决定</span></div>
<div class="rule"><span class="k">AT_TARGET</span><span class="v" style="color:#00a884">p ≥ q 达目标 建议清仓</span></div>
<div class="rule"><span class="k">检查频率</span><span class="v">每3分钟</span></div>
<div class="rule"><span class="k">触发用价</span><span class="v" style="color:var(--tx2);font-size:10px">止盈用 best_bid (防虚高假触发) / 止损用 cur (防虚低早卖)</span></div>
</div></div>
<div class="sl">🖥 实时日志</div>
<div class="ll"><div class="chd"><h2>Monitor Log</h2><div style="display:flex;gap:8px"><div class="lp" style="font-size:9px"><div class="ld"></div>3s</div><button class="btn" onclick="document.getElementById('lb').scrollTop=999999" style="font-size:10px;padding:4px 10px">↓</button></div></div><div class="lc" id="lb">Loading...</div></div>
</div>
<footer>Polymarket Semi-Auto v5.6</footer>
<textarea id="pt" style="position:absolute;left:-9999px">{{ prompt }}</textarea>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<script>
// 整页刷新已禁用 - 改用局部刷新
function syncSnapshot(){fetch('/api/snapshot').then(r=>r.json()).then(d=>{if(!d.ok)return;updateMetrics(d);updatePositions(d.positions||[])}).catch(e=>{})}
function updateMetrics(d){const el=(id)=>document.getElementById(id);if(el('m-pnl')){el('m-pnl').textContent='$'+d.total_pnl.toFixed(2);el('m-pnl').style.color=d.total_pnl>=0?'#00e5a0':'#ff4070'}if(el('m-count')){if(parseInt(el('m-count').textContent)!==d.position_count){location.reload();return}}if(el('m-cost'))el('m-cost').textContent='$'+d.total_cost.toFixed(2);if(el('m-revenue')&&d.total_value!=null)el('m-revenue').textContent='$'+d.total_value.toFixed(2);if(el('m-portfolio')&&d.assets_total!=null)el('m-portfolio').textContent='$'+d.assets_total.toFixed(2);if(el('m-cash')&&d.cash!=null)el('m-cash').textContent='$'+d.cash.toFixed(2)}
function updatePositions(rows){rows.forEach(p=>{const row=document.querySelector(`[data-asset='${p.asset}']`);if(!row)return;const cp=row.querySelector('.cur-price');if(cp)cp.textContent='$'+p.cur_price.toFixed(3);const cv=row.querySelector('.cur-value');if(cv){cv.textContent='$'+p.value.toFixed(2);if(p.pnl_dollar!=null)cv.style.color=p.pnl_dollar>=0?'#00e5a0':'#ff4070'}const pn=row.querySelector('.cur-pnl');if(pn){pn.textContent=(p.pnl_pct>=0?'+':'')+p.pnl_pct.toFixed(1)+'%';pn.style.color=p.pnl_pct>=0?'#00e5a0':'#ff4070'}const pd=row.querySelector('.cur-pnl-d');if(pd&&p.pnl_dollar!=null){pd.textContent=(p.pnl_dollar>=0?'+$':'-$')+Math.abs(p.pnl_dollar).toFixed(2);pd.style.color=p.pnl_dollar>=0?'#00e5a0':'#ff4070'}});const rb=document.getElementById('rb');if(rb)rb.textContent='\u{2713} '+new Date().toLocaleTimeString().slice(0,5)}
syncSnapshot();setInterval(syncSnapshot,30000);
function fl(){fetch('/api/logs').then(r=>r.json()).then(d=>{if(!d.ok)return;const b=document.getElementById('lb');const a=b.scrollHeight-b.scrollTop-b.clientHeight<40;b.innerHTML=d.lines.map(l=>{let c='';if(l.includes('[INFO]'))c='INFO';else if(l.includes('[WARNING]'))c='WARNING';else if(l.includes('[ERROR]'))c='ERROR';return'<div class="ll2"><span class="ts">'+l.substring(0,19)+'</span> <span class="'+c+'">['+c+']</span> <span class="msg">'+l.substring(20).replace(/\[(?:INFO|WARNING|ERROR)\]\s?/,'')+'</span></div>'}).join('');if(a)b.scrollTop=b.scrollHeight})}
fl();setInterval(fl,3000);
function doAction(a){showT('ok',a==='check'?'检查中...':'...');fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:a})}).then(r=>r.json()).then(d=>{showT(d.ok?'ok':'err',d.message);if(a==='refresh')setTimeout(syncSnapshot,800)}).catch(()=>showT('err','错误'))}
function showT(t,m){const e=document.getElementById('toast');e.className='toast '+t+' show';e.textContent=m;setTimeout(()=>e.classList.remove('show'),3000)}
function switchTab(name){
  const ids=['kw','tag'];
  ids.forEach(id=>{
    const tab=document.getElementById('tab-'+id);
    const panel=document.getElementById('panel-'+id);
    if(id===name){
      tab.classList.add('tab-active');
      panel.style.display='';
    }else{
      tab.classList.remove('tab-active');
      panel.style.display='none';
    }
  });
}

function doTagScan(tagLabel){
  const radios=document.querySelectorAll('input[name="tagMode"]');
  let mode='standard';
  for(const r of radios){if(r.checked){mode=r.value;break}}
  const startTs=Date.now()/1000;
  const modeLabel=mode==='wide'?'大范围':mode==='medium'?'中范围':'标准';
  document.getElementById('scanReport').textContent='🏷️ Tag扫描 ['+tagLabel+'] '+modeLabel+'模式 进行中...';
  const status=document.getElementById('tagScanStatus');
  if(status)status.textContent='⏳ 扫描中...';
  // chip flash
  const btns=document.querySelectorAll('.tag-chip');
  btns.forEach(b=>{
    if(b.textContent===tagLabel){
      // 根据tier类加对应flash
      if(b.classList.contains('tier1'))b.classList.add('flash-tier1');
      else if(b.classList.contains('tier2'))b.classList.add('flash-tier2');
      else if(b.classList.contains('tier3'))b.classList.add('flash-tier3');
      setTimeout(()=>{b.classList.remove('flash-tier1','flash-tier2','flash-tier3')},600);
    }
  });
  if(_scanPollTimer){clearInterval(_scanPollTimer);_scanPollTimer=null}
  fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'scan_tag',tag:tagLabel,mode:mode})})
    .then(r=>r.json()).then(d=>{
      if(!d.ok){showT('err',d.message);if(status)status.textContent='❌ '+d.message;return}
      showT('ok',d.message);
      pollScan(startTs,0);
    }).catch(()=>{showT('err','网络错误');if(status)status.textContent='❌ 网络错误'})
}

function setKw(kw){const el=document.getElementById('scanKw');el.value=kw;el.focus();const btns=document.querySelectorAll('.chip');btns.forEach(b=>{if(b.textContent===kw){b.classList.add('chip-flash');setTimeout(()=>b.classList.remove('chip-flash'),400)}})}
let _scanPollTimer=null;
function doScan(mode){
  mode = mode || 'standard';
  const kw=document.getElementById('scanKw').value;
  const startTs=Date.now()/1000;
  document.getElementById('scanReport').textContent = (mode==='wide'?'🌐 大范围扫描':mode==='medium'?'📊 中范围扫描':'🚀 标准扫描')+' 进行中,请等待...';
  document.getElementById('scanStatus').textContent='⏳ '+(mode==='wide'?'大范围扫描':mode==='medium'?'中范围扫描':'标准扫描')+'中...';
  if(_scanPollTimer){clearInterval(_scanPollTimer);_scanPollTimer=null}
  fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'scan',keyword:kw,mode:mode})})
    .then(r=>r.json()).then(d=>{
      if(!d.ok){showT('err',d.message);document.getElementById('scanStatus').textContent='❌ '+d.message;return}
      showT('ok',d.message);
      pollScan(startTs,0);
    }).catch(()=>{showT('err','请求错误');document.getElementById('scanStatus').textContent='❌ 网络错误'})
}
let _closedData=[];
function renderClosed(){
  const list=document.getElementById('closed-list');
  if(!list)return;
  if(!_closedData.length){list.innerHTML='<div style="padding:20px;color:var(--tx3);font-size:11px">暂无</div>';return}
  list.innerHTML=_closedData.map(r=>{
    const cards=r.sells.map(s=>{
      const chg=s.change_pp;
      const chgColor=chg>5?'#00a884':chg<-5?'#b00000':'#888';
      const chgStr=(chg>=0?'+':'')+chg.toFixed(1)+'pp';
      const verdict=chg>5?'❌ 卖了又涨':chg<-5?'✅ 卖了继续跌':'⚪ 持平';
      const pnl=s.pnl;const roi=s.roi;
      const pnlColor=pnl==null?'#888':(pnl>0.01?'#00a884':(pnl<-0.01?'#b00000':'#888'));
      const pnlStr=pnl==null?'—':((pnl>=0?'+$':'-$')+Math.abs(pnl).toFixed(2));
      const roiStr=roi==null?'':' ('+(roi>=0?'+':'')+roi.toFixed(1)+'%)';
      return `<div class="sell-card">
        <div class="sc-h">第 ${s.seq} 次卖出 · ${s.date}</div>
        <div class="sc-grid">
          <div class="sc-k">卖出</div><div class="sc-v"><span class="sc-sz">${s.size.toFixed(2)} 股</span> @ <span class="sc-pr">$${s.price.toFixed(3)}</span></div>
          <div class="sc-k">入场</div><div class="sc-v">${s.entry_avg!=null?('avg <span class="sc-pr">$'+s.entry_avg.toFixed(3)+'</span>'):'—'}</div>
          ${s.trigger?`<div class="sc-k">触发</div><div class="sc-v"><span class="sc-trigger">${s.trigger}</span></div>`:''}
          <div class="sc-k">收入</div><div class="sc-v">$${s.revenue.toFixed(2)}${s.cost!=null?(' (成本 $'+s.cost.toFixed(2)+')'):''}</div>
          <div class="sc-k">盈亏</div><div class="sc-v" style="color:${pnlColor};font-weight:700">${pnlStr}${roiStr}</div>
          ${s.duration?`<div class="sc-k">持仓</div><div class="sc-v" style="color:var(--tx2)">${s.duration}</div>`:''}
        </div>
        <div class="sc-f" style="color:${chgColor}">卖后: ${verdict} (${chgStr})</div>
      </div>`;
    }).join('');
    return `<div class="lr ev-row" style="grid-template-columns:130px 1fr"><div class="lt">${r.latest_sell_date}</div><div class="ldd"><div class="ev-title">${r.title} <span style="color:var(--tx3);font-weight:400;margin-left:6px">现价 $${r.cur_price.toFixed(3)} · 卖了 ${r.sell_count} 次</span></div><div class="sell-cards-wrap">${cards}</div></div></div>`;
  }).join('');
  const cnt=document.getElementById('closed-cnt');
  if(cnt)cnt.textContent=_closedData.length+' 个仓位';
}
async function loadClosed(range){
  document.querySelectorAll('[data-cprange]').forEach(b=>b.classList.toggle('active',b.dataset.cprange===range));
  const list=document.getElementById('closed-list');
  if(list)list.innerHTML='<div style="padding:20px;color:var(--tx3);font-size:11px">加载中 ('+range+')...</div>';
  try{
    const r=await fetch('/api/closed_positions?range='+range);
    const d=await r.json();
    if(!d.ok){list.innerHTML='<div style="padding:20px;color:var(--rd);font-size:11px">'+d.message+'</div>';return}
    _closedData=d.rows||[];
    renderClosed();
  }catch(e){list.innerHTML='<div style="padding:20px;color:var(--rd);font-size:11px">加载失败</div>'}
}
loadClosed('1w');

function toggleAllEvents(){
  const card=document.getElementById('events-card');
  const btn=document.getElementById('ev-more-btn');
  const cnt=document.getElementById('ev-cnt');
  if(!card)return;
  card.classList.toggle('show-all');
  const showAll=card.classList.contains('show-all');
  if(btn)btn.textContent=showAll?'收起':'更多';
  if(cnt){
    const all=card.querySelectorAll('.ev-row').length;
    const sells=card.querySelectorAll('.ev-row.ev-auto_sell, .ev-row.ev-user_sell').length;
    cnt.textContent=showAll?(all+' 全部'):(sells+' 卖出');
  }
}
function setScanStatus(text){
  ['scanStatus','tagScanStatus'].forEach(id=>{
    const el=document.getElementById(id);
    if(el)el.textContent=text;
  });
}
function pollScan(startTs,attempt){
  const MAX_ATTEMPTS=30;
  fetch('/api/scan_report').then(r=>r.json()).then(d=>{
    const elapsed=Math.floor(Date.now()/1000-startTs);
    if(d.ok&&d.mtime&&d.mtime>=startTs){
      document.getElementById('scanReport').textContent=d.report;
      setScanStatus('✅ 扫描完成 ('+elapsed+'秒) '+new Date().toLocaleTimeString());
      return;
    }
    if(attempt>=MAX_ATTEMPTS){
      setScanStatus('⚠️ 扫描超时(60秒), 请重试');
      return;
    }
    setScanStatus('⏳ 扫描中... '+elapsed+'秒');
    setTimeout(()=>pollScan(startTs,attempt+1),2000);
  }).catch(()=>setTimeout(()=>pollScan(startTs,attempt+1),2000))
}
function loadScan(){fetch('/api/scan_report').then(r=>r.json()).then(d=>{document.getElementById('scanReport').textContent=d.report;if(d.ok&&d.mtime){const dt=new Date(d.mtime*1000);document.getElementById('scanStatus').textContent='上次扫描: '+dt.toLocaleString()}}).catch(()=>{})}
function copyScan(){const t=document.getElementById('scanReport').textContent;navigator.clipboard.writeText(t).then(()=>showT('ok','报告已复制！粘贴到Claude Research'));if(!navigator.clipboard){const a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();document.execCommand('copy');document.body.removeChild(a);showT('ok','报告已复制！')}}
loadScan();
async function toggleReeval(tokenId){
  const el = document.getElementById('rv-' + tokenId);
  if(!el) return;
  el.style.display = (el.style.display === 'none' || !el.style.display) ? 'block' : 'none';
}

function copyReevalPrompt(tokenId){
  fetch('/api/reeval_prompt?token_id=' + tokenId)
    .then(r=>r.json()).then(d=>{
      if(!d.ok){ showT('err', d.message||'生成失败'); return; }
      navigator.clipboard.writeText(d.prompt).then(()=>{
        showT('ok', '已复制 prompt (' + d.prompt.length + ' 字符), 粘贴到 Claude.ai Research 模式');
      }).catch(()=>showT('err','复制失败, 浏览器需 https 或 localhost'));
    }).catch(()=>showT('err','网络失败'));
}

function markReevalHold(tokenId){
  if(!confirm('维持当前 q 不变. 继续?')) return;
  fetch('/api/update_q',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({token_id:tokenId, new_q:null, reason:'reeval_hold_no_change'})
  }).then(r=>r.json()).then(d=>{
    showT(d.ok?'ok':'err', d.message||(d.ok?'已记录':'失败'));
    if(d.ok) setTimeout(()=>location.reload(), 1500);
  });
}

function submitNewQ(tokenId){
  const inp = document.getElementById('newq-' + tokenId);
  if(!inp) return;
  const v = parseFloat(inp.value);
  if(isNaN(v) || v <= 0 || v >= 100){ showT('err', '请输入百分比 (1-99)'); return; }
  const new_q = v / 100;
  if(!confirm('更新 q 为 ' + v + '% (= ' + new_q.toFixed(3) + '). 确认?')) return;
  fetch('/api/update_q',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({token_id:tokenId, new_q:new_q, reason:'reeval_user_update'})
  }).then(r=>r.json()).then(d=>{
    showT(d.ok?'ok':'err', d.message||(d.ok?'已更新':'失败'));
    if(d.ok) setTimeout(()=>location.reload(), 1500);
  });
}

function reevalExit(tokenId, title, size, curPrice){
  // 用户在重评里选了 exit, 走 force_exit 整笔清仓
  // 绕过 monitor_state 校验, 因为 reeval 决定就直接 sell
  const expectedRecv = (size * curPrice).toFixed(3);
  const msg = '建议清仓 (Claude 推荐 exit)\n\n' +
              '标的: ' + title + '\n' +
              '动作: 整笔清仓 (' + size + ' 股)\n' +
              '当前价: $' + curPrice.toFixed(3) + '\n' +
              '预计收回: ~$' + expectedRecv + '\n\n' +
              '确认?';
  if(!confirm(msg)) return;
  fetch('/api/force_exit',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({token_id:tokenId, reason:'reeval_exit'})
  }).then(r=>r.json()).then(d=>{
    showT(d.ok?'ok':'err', d.message||(d.ok?'已执行':'失败'));
    if(d.ok) setTimeout(()=>location.reload(), 1500);
  });
}

function executeState(tokenId, state, title, size, curPrice){
  let action_text = "";
  let sell_size = size;
  if(state === "AT_TARGET"){
    action_text = "整笔清仓 (" + size + " 股) - 价格已达目标";
  }
  const expectedRecv = (sell_size * curPrice).toFixed(3);
  const msg = "确认执行: " + state + "\n\n" +
              "标的: " + title + "\n" +
              "动作: " + action_text + "\n" +
              "当前价: $" + curPrice.toFixed(3) + "\n" +
              "预计收回: ~$" + expectedRecv + " (按当前价估算, 实际按最优 bid 成交)\n\n" +
              "确认?";
  if(!confirm(msg)) return;
  fetch('/api/execute_state',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({token_id:tokenId, state:state})
  }).then(r=>r.json()).then(d=>{
    showT(d.ok?'ok':'err', d.message||(d.ok?'已执行':'失败'));
    if(d.ok) setTimeout(()=>location.reload(), 1500);
  }).catch(()=>showT('err','网络失败'));
}

async function copyP(){
  showT('ok','正在准备最新Prompt...');
  try{
    const r=await fetch('/api/full_prompt');
    const d=await r.json();
    if(!d.ok){showT('err','获取Prompt失败: '+(d.message||''));return}
    if(navigator.clipboard&&window.isSecureContext){
      await navigator.clipboard.writeText(d.prompt);
    }else{
      const a=document.createElement('textarea');
      a.value=d.prompt;a.style.position='fixed';a.style.left='-9999px';
      document.body.appendChild(a);a.select();document.execCommand('copy');document.body.removeChild(a);
    }
    showT('ok','✅ 最新Prompt已复制! 去Claude.ai粘贴');
  }catch(e){
    showT('err','复制失败: '+e.message);
  }
}



async function addPosition(tokenId, side, title){
  const inp = document.getElementById('addusd-' + tokenId);
  const usd = parseFloat(inp.value);
  if(!usd || usd < 1){ showT('err','请输入 ≥$1 的金额'); inp.focus(); return; }
  showT('ok','拉盘口预览...');
  try{
    const r = await fetch(`/api/buy_preview?token_id=${tokenId}&usd=${usd}`);
    const pv = await r.json();
    if(!pv.ok){ showT('err', pv.message || '预览失败'); return; }
    const askPct = (pv.best_ask*100).toFixed(1);
    const ok = confirm(`确认加仓?\n\n${title}\n方向: ${side}\n金额: $${usd.toFixed(2)}\n@ ${askPct}% (best_ask)\n→ 约 ${pv.size} 股 (实际成交 $${pv.estimated_cost.toFixed(2)})`);
    if(!ok){ showT('ok','已取消'); return; }
    showT('ok','下单中...');
    const r2 = await fetch('/api/buy_position', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({token_id: tokenId, usd_amount: usd, reason: 'manual_add'})
    });
    const d = await r2.json();
    showT(d.ok?'ok':'err', d.message || (d.ok?'加仓成功':'加仓失败'));
    if(d.ok){ inp.value=''; setTimeout(syncSnapshot, 1500); }
  }catch(e){ showT('err','请求失败: ' + e.message); }
}

function forceLiquidate(tokenId, title, size, curPrice){
  const ok = confirm(`确认清仓?\n\n${title}\n卖出全部 ${size} 股 @ ~${(curPrice*100).toFixed(1)}% (best_bid)\n≈ $${(size*curPrice).toFixed(2)}`);
  if(!ok){ showT('ok','已取消'); return; }
  showT('ok','清仓下单中...');
  fetch('/api/force_exit', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({token_id: tokenId, reason: 'manual_liquidate'})
  }).then(r=>r.json()).then(d=>{
    showT(d.ok?'ok':'err', d.message || (d.ok?'清仓完成':'清仓失败'));
    if(d.ok) setTimeout(syncSnapshot, 1500);
  }).catch(e=>showT('err','请求失败: ' + e.message));
}

function saveConf(tokenId,value){
  fetch('/api/update_confidence',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token_id:tokenId,confidence:value})})
    .then(r=>r.json()).then(d=>{
      if(d.ok&&d.rows>0)showT('ok','信心已保存: '+(value||'(空)'));
      else if(d.ok)showT('err','请先保存 TP, 信心未持久化');
      else showT('err',d.message||'保存失败');
    }).catch(()=>showT('err','保存失败'))
}

function saveTier(tokenId,value){
  fetch('/api/update_tier',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token_id:tokenId,stop_loss_tier:value})})
    .then(r=>r.json()).then(d=>{
      if(d.ok&&d.rows>0)showT('ok','止损档已保存: '+(value||'(空)'));
      else if(d.ok)showT('err','请先保存 TP, 止损档未持久化');
      else showT('err',d.message||'保存失败');
    }).catch(()=>showT('err','保存失败'))
}

function saveTP(tokenId,slug,side,entryPrice,idx,endDate,size){
  const confEl=document.getElementById('conf-'+idx);
  const confidence=confEl?confEl.value:'';
  const tierEl=document.getElementById('tier-'+idx);
  const stopLossTier=tierEl?tierEl.value:'';
  const tpVal=document.getElementById('tp-'+idx).value;
  if(!tpVal||isNaN(parseFloat(tpVal))){showT('err','请输入百分比 (1-99)');return}
  const tpPct=parseFloat(tpVal);
  if(tpPct<=0||tpPct>=100){showT('err','百分比必须在1-99之间');return}
  const tp=tpPct/100;
  fetch('/api/record_position',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    token_id:tokenId,slug:slug,side:side,entry_price:entryPrice,tp:tp,end_date:endDate,size:size,original_confidence:confidence,stop_loss_tier:stopLossTier
  })}).then(r=>r.json()).then(d=>{showT(d.ok?'ok':'err',d.message||'已保存 TP='+tpPct+'%')}).catch(()=>showT('err','保存失败'))
}

let portfolioChart=null;let currentRange='1d';
const RANGE_LABELS={'1d':'1D 变化 · 当前组合 − 24h 前','1w':'1W 变化 · 当前组合 − 一周前','1m':'1M 变化 · 当前组合 − 30 天前','1y':'1Y 变化 · 当前组合 − 一年前','ytd':'YTD · 当前组合 − 年初','all':'全部 · 当前组合 − 首次记录'};
function setChartDelta(idx){
  const dEl=document.getElementById('chart-delta');
  const lEl=document.getElementById('chart-delta-label');
  const pts=portfolioChart?portfolioChart.data.datasets[0].data:[];
  if(!pts||pts.length===0){
    if(dEl){dEl.textContent='$0.00';dEl.style.color='#5858a0'}
    if(lEl)lEl.textContent=RANGE_LABELS[currentRange]||'';
    return;
  }
  const t=(idx==null)?pts.length-1:idx;
  const delta=pts[t].y-pts[0].y;
  if(dEl){
    dEl.textContent=(delta>=0?'+$':'-$')+Math.abs(delta).toFixed(2);
    dEl.style.color=delta>=0?'#00e5a0':'#ff4070';
  }
  if(lEl){
    if(idx==null){
      lEl.textContent=RANGE_LABELS[currentRange]||'';
    }else{
      const dt=new Date(pts[t].x);
      lEl.textContent=dt.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})+' · 当时组合 − 起点组合';
    }
  }
}
async function loadChart(range){
  currentRange=range;
  document.querySelectorAll('.range-btn[data-range]').forEach(b=>b.classList.toggle('active',b.dataset.range===range));
  try{
    const r=await fetch('/api/portfolio_history?range='+range);
    const d=await r.json();
    if(!d.ok)return;
    const points=(d.points||[]).map(p=>({x:p.ts*1000,y:p.assets_total}));
    if(portfolioChart){
      portfolioChart.data.datasets[0].data=points;
      portfolioChart.update('none');
      setChartDelta(null);
      return;
    }
    if(typeof Chart==='undefined'){console.warn('Chart.js not loaded');return}
    const ctx=document.getElementById('portfolio-canvas').getContext('2d');
    portfolioChart=new Chart(ctx,{
      type:'line',
      data:{datasets:[{label:'资产总值',data:points,borderColor:'#00c8ff',backgroundColor:'rgba(0,200,255,0.1)',fill:true,tension:0.2,pointRadius:0,pointHoverRadius:4,borderWidth:2}]},
      options:{
        responsive:true,maintainAspectRatio:false,
        interaction:{mode:'index',intersect:false},
        onHover:(e,active)=>setChartDelta(active.length>0?active[0].index:null),
        plugins:{
          legend:{display:false},
          tooltip:{
            backgroundColor:'rgba(17,17,40,0.95)',borderColor:'#1e1e4a',borderWidth:1,
            titleColor:'#e8e8ff',bodyColor:'#00c8ff',padding:10,
            callbacks:{
              title:(items)=>{const dt=new Date(items[0].parsed.x);return dt.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})},
              label:(item)=>'资产: $'+item.parsed.y.toFixed(2)
            }
          }
        },
        scales:{
          x:{type:'time',ticks:{color:'#5858a0',font:{size:10},maxRotation:0,autoSkipPadding:20},grid:{color:'rgba(255,255,255,0.04)'}},
          y:{ticks:{color:'#5858a0',font:{size:10},callback:v=>'$'+v.toFixed(0)},grid:{color:'rgba(255,255,255,0.04)'}}
        }
      }
    });
    document.getElementById('portfolio-canvas').addEventListener('mouseleave',()=>setChartDelta(null));
    setChartDelta(null);
  }catch(e){console.error('chart load failed',e)}
}
window.addEventListener('load',()=>loadChart('1d'));
setInterval(()=>loadChart(currentRange),60000);

let moverTab='realtime';let moverRange='1d';let realtimeRange='1h';let moverExpanded=false;let metricMode='pp';let lastMovers=null;let lastValueRank=null;let lastRealtime=null;
function switchMoverTab(tab){
  moverTab=tab;
  document.querySelectorAll('.range-btn[data-mtab]').forEach(b=>b.classList.toggle('active',b.dataset.mtab===tab));
  document.getElementById('realtime-section').style.display=tab==='realtime'?'':'none';
  document.getElementById('movers-section').style.display=tab==='movers'?'':'none';
  document.getElementById('value-section').style.display=tab==='value'?'':'none';
  document.getElementById('realtime-range-group').style.display=tab==='realtime'?'flex':'none';
  document.getElementById('movers-range-group').style.display=tab==='movers'?'flex':'none';
  document.getElementById('metric-toggle-group').style.display=tab==='value'?'none':'flex';
  if(tab==='realtime')loadRealtime(realtimeRange);
  else if(tab==='value')loadValueRank();
  else loadMovers(moverRange);
}
function setMetricMode(m){
  metricMode=m;
  document.querySelectorAll('.range-btn[data-metric]').forEach(b=>b.classList.toggle('active',b.dataset.metric===m));
  if(moverTab==='realtime'&&lastRealtime)renderRealtimeInto(lastRealtime);
  else if(moverTab==='movers'&&lastMovers)renderMoversInto(lastMovers);
}
function toggleMoverExpanded(){
  moverExpanded=!moverExpanded;
  document.getElementById('mover-more-btn').textContent=moverExpanded?'Less':'More';
  if(moverTab==='realtime'&&lastRealtime)renderRealtimeInto(lastRealtime);
  else if(moverTab==='movers'&&lastMovers)renderMoversInto(lastMovers);
  else if(moverTab==='value'&&lastValueRank)renderValueInto(lastValueRank);
}
function refreshMoverTab(){
  if(moverTab==='realtime')loadRealtime(realtimeRange,true);
  else if(moverTab==='movers')loadMovers(moverRange,true);
  else loadValueRank(true);
}
async function loadRealtime(range,force){
  realtimeRange=range;
  document.querySelectorAll('.range-btn[data-rtrange]').forEach(b=>b.classList.toggle('active',b.dataset.rtrange===range));
  const el=document.getElementById('realtime-list');
  if(el)el.innerHTML='<div style="font-size:10px;color:var(--tx3);padding:8px 0">loading...</div>';
  try{
    const r=await fetch('/api/realtime_movers?range='+range+(force?'&force=1':''));
    const d=await r.json();
    if(!d.ok)return;
    lastRealtime=d;
    renderRealtimeInto(d);
  }catch(e){console.error('realtime load failed',e)}
}
function renderRealtimeInto(d){
  let items=(d.items||[]).slice();
  if(metricMode==='dollar')items.sort((a,b)=>Math.abs(b.change_dollar||0)-Math.abs(a.change_dollar||0));
  const show=moverExpanded?items:items.slice(0,3);
  document.getElementById('realtime-suffix').textContent=moverExpanded?'(全部 '+items.length+')':'Top 3';
  const el=document.getElementById('realtime-list');
  if(!el)return;
  if(show.length===0){el.innerHTML='<div style="font-size:10px;color:var(--tx3);padding:8px 0">暂无数据 (该时间窗口内未发生显著变化)</div>';return}
  el.innerHTML=show.map((m,i)=>{
    const color=m.change_pp>=0?'#00e5a0':'#ff4070';
    const sign=m.change_pp>=0?'+':'';
    const title=(m.title||'').replace(/"/g,'&quot;');
    const primary=metricMode==='dollar'?`${sign}$${Math.abs(m.change_dollar||0).toFixed(2)}`:`${sign}${m.change_pp.toFixed(1)}pp`;
    return `<div style="padding:8px 0;border-bottom:1px solid rgba(30,30,74,0.4);font-size:11px">
<div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start;margin-bottom:3px">
<div style="font-weight:500;color:var(--tx);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.35;flex:1" title="${title}">${i+1}. ${title}</div>
<div style="font-family:'JetBrains Mono';font-weight:700;color:${color};font-size:12px;white-space:nowrap">${primary}</div>
</div>
<div style="display:flex;gap:10px;font-family:'JetBrains Mono';font-size:10px;color:var(--tx3);flex-wrap:wrap">
<span>${m.side}</span>
<span style="color:${color}">(${sign}${m.change_pct.toFixed(1)}%)</span>
<span>${(m.first_price*100).toFixed(1)}% → ${(m.last_price*100).toFixed(1)}%</span>
</div>
</div>`;
  }).join('');
}
async function loadMovers(range,force){
  moverRange=range;
  document.querySelectorAll('.range-btn[data-mrange]').forEach(b=>b.classList.toggle('active',b.dataset.mrange===range));
  const gEl=document.getElementById('movers-gainers');
  const lEl=document.getElementById('movers-losers');
  if(gEl)gEl.innerHTML='<div style="font-size:10px;color:var(--tx3);padding:8px 0">loading...</div>';
  if(lEl)lEl.innerHTML='<div style="font-size:10px;color:var(--tx3);padding:8px 0">loading...</div>';
  try{
    const r=await fetch('/api/movers?range='+range+(force?'&force=1':''));
    const d=await r.json();
    if(!d.ok)return;
    lastMovers=d;
    renderMoversInto(d);
  }catch(e){console.error('movers load failed',e)}
}
async function loadValueRank(force){
  const el=document.getElementById('value-list');
  if(el)el.innerHTML='<div style="font-size:10px;color:var(--tx3);padding:8px 0">loading...</div>';
  try{
    const r=await fetch('/api/holdings_rank'+(force?'?force=1':''));
    const d=await r.json();
    if(!d.ok)return;
    lastValueRank=d;
    renderValueInto(d);
  }catch(e){console.error('value rank load failed',e)}
}
function renderMoversInto(d){
  let gAll,lAll;
  if(metricMode==='dollar'){
    const all=[...(d.gainers||[]),...(d.losers||[])];
    gAll=all.filter(m=>m.change_dollar>0).sort((a,b)=>b.change_dollar-a.change_dollar);
    lAll=all.filter(m=>m.change_dollar<0).sort((a,b)=>a.change_dollar-b.change_dollar);
  }else{
    gAll=d.gainers||[];lAll=d.losers||[];
  }
  const gainers=moverExpanded?gAll:gAll.slice(0,3);
  const losers=moverExpanded?lAll:lAll.slice(0,3);
  document.getElementById('movers-g-suffix').textContent=moverExpanded?'(全部 '+gAll.length+')':'Top 3';
  document.getElementById('movers-l-suffix').textContent=moverExpanded?'(全部 '+lAll.length+')':'Top 3';
  const gEl=document.getElementById('movers-gainers');
  const lEl=document.getElementById('movers-losers');
  if(gEl)gEl.innerHTML=renderMoverList(gainers);
  if(lEl)lEl.innerHTML=renderMoverList(losers);
}
function renderValueInto(d){
  const items=d.items||[];
  const show=moverExpanded?items:items.slice(0,3);
  document.getElementById('value-suffix').textContent=moverExpanded?'(全部 '+items.length+')':'Top 3';
  const el=document.getElementById('value-list');
  if(!el)return;
  if(show.length===0){el.innerHTML='<div style="font-size:10px;color:var(--tx3);padding:8px 0">暂无</div>';return}
  el.innerHTML=show.map((m,i)=>{
    const pnlColor=m.pnl_dollar>=0?'#00e5a0':'#ff4070';
    const pnlSign=m.pnl_dollar>=0?'+':'';
    const title=(m.title||'').replace(/"/g,'&quot;');
    return `<div style="padding:9px 0;border-bottom:1px solid rgba(30,30,74,0.4);font-size:11px">
<div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start;margin-bottom:4px">
<div style="font-weight:500;color:var(--tx);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.35;flex:1" title="${title}">${i+1}. ${title}</div>
<div style="font-family:'JetBrains Mono';font-weight:700;color:#00c8ff;font-size:12px;white-space:nowrap">$${m.value.toFixed(2)}</div>
</div>
<div style="display:flex;gap:10px;font-family:'JetBrains Mono';font-size:10px;color:var(--tx3);flex-wrap:wrap">
<span>${m.side}</span>
<span>${(m.cur_price*100).toFixed(1)}% × ${m.size.toFixed(1)}股</span>
<span style="color:${pnlColor}">${pnlSign}$${Math.abs(m.pnl_dollar).toFixed(2)}</span>
</div>
</div>`;
  }).join('');
}
function renderMoverList(items){
  if(!items||items.length===0)return '<div style="font-size:10px;color:var(--tx3);padding:8px 0">暂无</div>';
  return items.map((m,i)=>{
    const color=m.change_pp>=0?'#00e5a0':'#ff4070';
    const sign=m.change_pp>=0?'+':'';
    const title=(m.title||'').replace(/"/g,'&quot;');
    const primary=metricMode==='dollar'?`${sign}$${Math.abs(m.change_dollar||0).toFixed(2)}`:`${sign}${m.change_pp.toFixed(1)}pp`;
    return `<div style="padding:8px 0;border-bottom:1px solid rgba(30,30,74,0.4);font-size:11px">
<div style="font-weight:500;color:var(--tx);margin-bottom:4px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.35" title="${title}">${i+1}. ${title}</div>
<div style="display:flex;gap:10px;font-family:'JetBrains Mono';font-size:10px;flex-wrap:wrap">
<span style="color:${color};font-weight:700">${primary}</span>
<span style="color:${color}">(${sign}${m.change_pct.toFixed(1)}%)</span>
<span style="color:var(--tx3)">${(m.first_price*100).toFixed(1)}% → ${(m.last_price*100).toFixed(1)}%</span>
</div>
</div>`;
  }).join('');
}
window.addEventListener('load',()=>loadRealtime('1h'));
setInterval(()=>{if(moverTab==='realtime')loadRealtime(realtimeRange);else if(moverTab==='movers')loadMovers(moverRange);else loadValueRank()},300000);

</script>
</body>
</html>
"""

def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_urlsafe(32)
    app.config.update(
        PERMANENT_SESSION_LIFETIME=timedelta(days=90),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=True,
    )

    @app.before_request
    def _require_auth():
        # 本机直连 = remote_addr 是 localhost AND 没有 X-Forwarded-For 头.
        # Tailscale serve 反代请求时 remote_addr 也是 127.0.0.1 (daemon 在本机), 但会塞
        # X-Forwarded-For 头. 不区分这两种情况会导致 tailnet/Funnel 流量被当本机直通,
        # 整层鉴权失效 (2026-05-29 实测确认).
        is_local_direct = (
            flask_request.remote_addr in ("127.0.0.1", "::1")
            and not flask_request.headers.get("X-Forwarded-For")
        )
        if is_local_direct:
            return None
        # 已登录 → 直通
        if session.get("authed"):
            return None
        # 登录/静态路径 → 直通
        if flask_request.path in ("/login", "/favicon.ico"):
            return None
        # POST 类返回 401 JSON (避免 fetch 被静默重定向)
        if flask_request.method == "POST":
            return jsonify({"ok": False, "error": "auth required"}), 401
        # GET → 跳登录页
        return redirect("/login")

    @app.route("/login", methods=["GET", "POST"])
    def login_route():
        ip = flask_request.remote_addr or "unknown"
        if flask_request.method == "POST":
            if not _rate_check(ip):
                log.warning(f"login lockout: ip={ip}")
                return render_template_string(LOGIN_HTML, error="尝试次数过多, 30 分钟后再试"), 429
            pw = flask_request.form.get("password", "")
            expected = os.environ.get("DASHBOARD_PASSWORD", "")
            if expected and pw == expected:
                session["authed"] = True
                session.permanent = True
                _rate_clear(ip)  # v5.7 (P11): db-backed clear
                log.info(f"login ok: ip={ip}")
                return redirect("/")
            _rate_fail(ip)
            # v5.7 (P11): use db to read counter for log message
            from modules.db import get_login_attempt
            _rec = get_login_attempt(ip)
            _attempts = _rec.get("fail_count", 0) if _rec else 0
            log.warning(f"login fail: ip={ip} attempts={_attempts}")
            return render_template_string(LOGIN_HTML, error="密码错误"), 401
        return render_template_string(LOGIN_HTML, error=None)

    @app.route("/logout", methods=["POST"])
    def logout_route():
        session.clear()
        return redirect("/login")

    @app.route("/")
    def index():
        from modules.db import get_position_meta
        # v4: 不再计算触发价, monitor_state 由 monitor 心跳写入数据库
        # dashboard 直接读 meta.monitor_state 显示徽章
        from datetime import datetime, timezone
        
        init_db()
        exe = Executor.get()
        positions = exe.get_positions()
        
        # v4.1: 补充 slug + end_date (Executor 不返回这两个字段)
        from modules.db import needs_reeval
        import requests as _req
        for p in positions:
            meta = get_position_meta(p["asset"])
            p["meta"] = meta or {}
            p["current_tp"] = (meta.get("new_tp") if meta and meta.get("new_tp") else (meta.get("tp") if meta else None))
            p["needs_reeval"] = needs_reeval(p["asset"], hours=24) if meta else False
            
            # 优先用 meta 里存的 (一旦保存过就不再调 API)
            p["market_slug"] = (meta.get("market_slug") if meta else "") or ""
            p["end_date"] = (meta.get("end_date") if meta else "") or ""
            
            # 如果 meta 里没有, 调 Gamma API 反查 (按 token id)
            if not p["market_slug"] or not p["end_date"]:
                try:
                    r = _req.get("https://gamma-api.polymarket.com/markets",
                                 params={"clob_token_ids": p["asset"], "limit": 1},
                                 timeout=8).json()
                    if r and isinstance(r, list) and len(r) > 0:
                        m = r[0]
                        if not p["market_slug"]:
                            p["market_slug"] = m.get("slug", "") or ""
                        if not p["end_date"]:
                            p["end_date"] = m.get("endDate", "") or ""
                except Exception:
                    pass
        
        events = get_recent_events(100)
        total_pnl = sum((p["cur_price"]-p["avg_price"])*p["size"] for p in positions)
        total_cost = sum(p["avg_price"]*p["size"] for p in positions)
        total_value = sum(p["cur_price"]*p["size"] for p in positions)
        # v5.7 (P5): explicit None check + warning. UI still falls back to 0 for rendering safety;
        # monitor.save_portfolio_snapshot path already skips writes on None (avoids history corruption).
        _cash_raw = exe.get_cash_balance()
        if _cash_raw is None:
            log.warning("dashboard render: cash API returned None — UI shows $0 but this is API failure, not zero balance")
        cash = _cash_raw if _cash_raw is not None else 0
        assets_total = total_value + cash
        # 尝试读取最新的扫描报告作为候选
        try:
            with open("last_scan.md", "r") as f:
                scan_content = f.read()
            prompt = DISCOVERY_PROMPT.replace("{positions_list}", scan_content)
        except:
            prompt = DISCOVERY_PROMPT.replace("{positions_list}", "(请先用扫描器生成候选市场列表)")
        return render_template_string(HTML, positions=positions, events=events, total_pnl=total_pnl, total_cost=total_cost, total_value=total_value, cash=cash, assets_total=assets_total, time_stop_days=TIME_STOP_DAYS, time_stop_drift_pp=int(TIME_STOP_DRIFT_PP), hold_min_edge_pp=int(HOLD_MIN_EDGE_PP), soft_neg_pp_abs=int(abs(SOFT_NEGATIVE_THRESHOLD_PP)), sl_convergent=int(STOP_LOSS_PCT_BY_TIER['convergent']*100), sl_hybrid=int(STOP_LOSS_PCT_BY_TIER['hybrid']*100), sl_legacy=int(STOP_LOSS_PCT_LEGACY*100), sl_floor_cent=int(EVENT_DRIVEN_FLOOR_PRICE*100), take_profit_price_cent=int(TAKE_PROFIT_PRICE*100), take_profit_pnl_pct=int(TAKE_PROFIT_PNL_PCT*100), prompt=prompt)

    @app.route("/api/control", methods=["POST"])
    def control():
        data = flask_request.get_json() or {}
        action = data.get("action","")
        if action == "check":
            if _monitor:
                threading.Thread(target=_monitor.check_once, daemon=True).start()
                return jsonify({"ok":True,"message":"持仓检查已触发"})
            return jsonify({"ok":False,"message":"Monitor未运行"})
        elif action == "refresh":
            return jsonify({"ok":True,"message":"已刷新"})
        elif action == "stop":
            if _monitor: _monitor.stop()
            import os,signal; os.kill(os.getpid(),signal.SIGTERM)
            return jsonify({"ok":True,"message":"停止中"})
        elif action == "scan":
            keyword = data.get("keyword", "")
            mode = data.get("mode", "standard")
            def do_scan():
                report = scan_and_report(keyword=keyword if keyword else None, include_orderbook=True, mode=mode)
                with open("last_scan.md", "w") as f:
                    f.write(report)
                from modules.db import log_event
                log_event("scan", keyword or "all", f"{len(report)} chars")
            threading.Thread(target=do_scan, daemon=True).start()
            return jsonify({"ok":True,"message":f"扫描启动: {keyword or '全部市场'}"})
        elif action == "scan_tag":
            tag = data.get("tag", "")
            mode = data.get("mode", "standard")
            if not tag:
                return jsonify({"ok":False,"message":"缺少tag参数"})
            from modules.scanner import scan_by_tag
            def do_tag_scan():
                report = scan_by_tag(tag, mode=mode)
                with open("last_scan.md", "w") as f:
                    f.write(report)
                from modules.db import log_event
                log_event("scan_tag", tag, f"mode={mode} {len(report)} chars")
            threading.Thread(target=do_tag_scan, daemon=True).start()
            return jsonify({"ok":True,"message":f"Tag扫描启动: {tag} ({mode})"})
        return jsonify({"ok":False,"message":"未知"})


    @app.route("/api/force_exit", methods=["POST"])
    def force_exit():
        """用户在重评流程中选 exit 时直接清仓"""
        from modules.db import (log_event, mark_executed_action, clear_position_meta,
                                 get_position_meta, save_closed_position)
        try:
            data = flask_request.get_json() or {}
            token_id = data.get("token_id", "")
            reason = (data.get("reason") or "force_exit").strip()[:500]  # v5.7 (P9): cap reason length
            if not token_id:
                return jsonify({"ok": False, "message": "缺少 token_id"})

            exe = Executor.get()
            pos = None
            for p in exe.get_positions():
                if p.get("asset") == token_id:
                    pos = p
                    break
            if not pos:
                return jsonify({"ok": False, "message": "找不到该持仓"})

            size = pos.get("size") or 0
            title = pos.get("title", "")
            # v5.7 (P7): capture entry context BEFORE sell so we can build closed_positions row.
            meta_pre = get_position_meta(token_id) or {}
            avg_entry = pos.get("avg_price") or meta_pre.get("entry_price") or 0
            cur_price_at_exit = pos.get("cur_price") or 0
            side = pos.get("outcome") or meta_pre.get("side") or ""

            log.info(f"FORCE_EXIT [{reason}] {title[:40]} size={size}")
            ok = exe.sell(token_id, size, f"force_exit:{reason}")

            if ok:
                log_event("user_sell", title, f"FORCE_EXIT size={size} {reason}")
                # v5.7 (P7): persist closed_positions row for PnL/calibration analytics.
                try:
                    save_closed_position(
                        token_id=token_id,
                        market_slug=meta_pre.get("market_slug") or title,
                        side=side,
                        avg_entry=avg_entry,
                        exit_price=cur_price_at_exit,
                        size=size,
                        exit_reason=f"FORCE_EXIT:{reason}",
                        stop_loss_tier=meta_pre.get("stop_loss_tier"),
                        claude_raw_estimate=meta_pre.get("claude_raw_estimate") or meta_pre.get("tp"),
                        entry_at=meta_pre.get("created_at"),
                    )
                except Exception as e:
                    log.warning(f"save_closed_position failed (sell still succeeded): {e}")
                # 平仓后清理 meta, 下次买回是白纸
                clear_position_meta(token_id)
                log.info(f"force_exit 后清理 meta: {title[:40]}")
                return jsonify({"ok": True, "message": f"已整笔清仓 ({size} 股)"})
            else:
                return jsonify({"ok": False, "message": "卖出失败, 看 bot.log"})
        except Exception as e:
            log.exception(f"force_exit error: {e}")
            return jsonify({"ok": False, "message": str(e)})

    @app.route("/api/execute_state", methods=["POST"])
    def execute_state():
        """用户点击徽章后, 按当前 monitor_state 执行清仓"""
        from modules.db import (get_position_meta, log_event, mark_executed_action,
                                 update_monitor_state, clear_position_meta, save_closed_position)
        try:
            data = flask_request.get_json() or {}
            token_id = data.get("token_id", "")
            confirmed_state = data.get("state", "")
            # v5.7 (P9): whitelist confirmed_state
            if confirmed_state not in ("AT_TARGET",):
                return jsonify({"ok": False, "message": f"不允许的 state: {confirmed_state}"})
            if not token_id:
                return jsonify({"ok": False, "message": "缺少 token_id"})

            meta = get_position_meta(token_id)
            if not meta:
                return jsonify({"ok": False, "message": "找不到 meta"})

            # 拉持仓
            exe = Executor.get()
            positions = exe.get_positions()
            pos = None
            for p in positions:
                if p.get("asset") == token_id:
                    pos = p
                    break
            if not pos:
                return jsonify({"ok": False, "message": "找不到该持仓"})

            current_state = meta.get("monitor_state") or "?"
            if current_state != confirmed_state:
                return jsonify({"ok": False, "message": f"状态已变化 ({current_state}), 请刷新页面再试"})

            # v5.7 (P12): re-verify the AT_TARGET condition live (cur_price ≥ q) instead of
            # only trusting db.monitor_state which may be stale between heartbeats.
            cur_p = pos.get("cur_price") or 0
            q = meta.get("new_tp") or meta.get("tp") or 0
            if confirmed_state == "AT_TARGET" and not (q > 0 and cur_p >= q):
                return jsonify({"ok": False,
                                "message": f"已不再 AT_TARGET (cur=${cur_p:.3f} q=${q:.3f}). 请刷新页面"})

            size = pos.get("size") or 0
            title = pos.get("title", "")
            avg_entry = pos.get("avg_price") or meta.get("entry_price") or 0
            side = pos.get("outcome") or meta.get("side") or ""

            # 决定卖多少
            sell_size = size
            action_tag = "user_exited_at_target"
            reason = f"AT_TARGET: 用户确认整笔清仓 ({size} 股)"

            log.info(f"USER_EXECUTE [{confirmed_state}] {title[:40]} sell={sell_size}")
            ok = exe.sell(token_id, sell_size, reason)

            if ok:
                log_event("user_sell", title, f"{confirmed_state} size={sell_size} {reason}")
                # v5.7 (P7): persist closed_positions for analytics.
                try:
                    save_closed_position(
                        token_id=token_id,
                        market_slug=meta.get("market_slug") or title,
                        side=side,
                        avg_entry=avg_entry,
                        exit_price=cur_p,
                        size=size,
                        exit_reason=f"USER_AT_TARGET",
                        stop_loss_tier=meta.get("stop_loss_tier"),
                        claude_raw_estimate=meta.get("claude_raw_estimate") or meta.get("tp"),
                        entry_at=meta.get("created_at"),
                    )
                except Exception as e:
                    log.warning(f"save_closed_position failed (sell still succeeded): {e}")
                # 平仓后清理 meta, 下次买回是白纸
                clear_position_meta(token_id)
                log.info(f"execute_state 后清理 meta: {title[:40]}")
                return jsonify({"ok": True, "message": f"已执行: {reason}"})
            else:
                return jsonify({"ok": False, "message": "卖出失败, 看 bot.log"})
        except Exception as e:
            log.exception(f"execute_state error: {e}")
            return jsonify({"ok": False, "message": str(e)})

    @app.route("/api/movers")
    def movers():
        """涨跌榜: 持仓里过去 1d/1w 价格变化, 返回全部 (前端切 Top 3 / All)."""
        from concurrent.futures import ThreadPoolExecutor
        exe = Executor.get()
        rng = (flask_request.args.get("range") or "1d").lower()
        force = (flask_request.args.get("force") or "0") == "1"
        interval = "1w" if rng in ("1w", "7d") else "1d"
        positions = exe.get_positions() or []
        if not positions:
            return jsonify({"ok": True, "range": rng, "gainers": [], "losers": []})
        def compute(p):
            tid = p.get("asset")
            if not tid:
                return None
            hist = exe.get_prices_history(tid, interval=interval, force=force)
            if not hist or len(hist) < 2:
                return None
            first_price = hist[0].get("p") or 0
            last_price = hist[-1].get("p") or p.get("cur_price") or 0
            if first_price <= 0:
                return None
            change_pp = (last_price - first_price) * 100
            change_pct = (last_price - first_price) / first_price * 100
            sz = p.get("size") or 0
            return {
                "title": p.get("title", ""),
                "asset": tid,
                "side": p.get("side", ""),
                "size": sz,
                "first_price": first_price,
                "last_price": last_price,
                "change_pp": change_pp,
                "change_pct": change_pct,
                "change_dollar": (last_price - first_price) * sz,
            }
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = [r for r in pool.map(compute, positions) if r]
        sorted_by_change = sorted(results, key=lambda x: x["change_pp"], reverse=True)
        gainers = [m for m in sorted_by_change if m["change_pp"] > 0]
        losers = [m for m in reversed(sorted_by_change) if m["change_pp"] < 0]
        return jsonify({"ok": True, "range": rng, "gainers": gainers, "losers": losers})

    @app.route("/api/buy_preview")
    def buy_preview():
        """加仓预览: 拉 best_ask + 估算 size. 不下单."""
        import math
        exe = Executor.get()
        token_id = flask_request.args.get("token_id", "")
        try:
            usd = float(flask_request.args.get("usd", "0"))
        except Exception:
            return jsonify({"ok": False, "message": "usd 参数无效"})
        if not token_id or usd < 1:
            return jsonify({"ok": False, "message": "需要 token_id 和 usd≥1"})
        best_ask = exe.get_best_ask(token_id)
        if not best_ask:
            return jsonify({"ok": False, "message": "盘口无 ask"})
        size = math.floor(usd / best_ask * 100) / 100
        if size < 0.01:
            return jsonify({"ok": False, "message": f"金额太小, ask={best_ask:.3f} 估算 size={size}"})
        return jsonify({"ok": True, "best_ask": best_ask, "size": size, "estimated_cost": size * best_ask})

    @app.route("/api/buy_position", methods=["POST"])
    def buy_position():
        """加仓: 调 executor.buy()"""
        exe = Executor.get()
        data = flask_request.get_json() or {}
        token_id = data.get("token_id", "")
        try:
            usd = float(data.get("usd_amount", 0))
        except Exception:
            return jsonify({"ok": False, "message": "usd_amount 无效"})
        # v5.7 (P9): cap usd <= 500 to prevent runaway POST (one-button-press disaster)
        if not token_id or usd < 1 or usd > 500:
            return jsonify({"ok": False, "message": "token_id 缺失或 usd 不在 [1, 500] 内"})
        # 拿仓位 title (for events log)
        positions = exe.get_positions() or []
        title = next((p.get("title", "") for p in positions if p.get("asset") == token_id), token_id[:20])
        ok, msg = exe.buy(token_id, usd, reason=data.get("reason", "manual_add"))
        if ok:
            from modules.db import log_event
            log_event("user_buy", title, f"加仓 ${usd:.2f}  {msg}")
        return jsonify({"ok": ok, "message": msg})

    @app.route("/api/realtime_movers")
    def realtime_movers():
        """实时榜: 短窗口 (30m/1h) 变化最大的 Top N, 按 |change| 排序 (混合涨跌)."""
        from concurrent.futures import ThreadPoolExecutor
        exe = Executor.get()
        rng = (flask_request.args.get("range") or "30m").lower()
        force = (flask_request.args.get("force") or "0") == "1"
        positions = exe.get_positions() or []
        if not positions:
            return jsonify({"ok": True, "range": rng, "items": []})
        # 30m 用 fidelity=5 后取倒数第7个点 (5 × 6 = 30 min). 1h 用第一个点 (完整 1 小时).
        offset_from_end = {"30m": 7, "1h": 0}.get(rng, 7)
        def compute(p):
            tid = p.get("asset")
            if not tid:
                return None
            hist = exe.get_prices_history(tid, interval="1h", fidelity="5", force=force, max_age=60)
            if not hist or len(hist) < 2:
                return None
            last = hist[-1]
            if rng == "30m":
                first = hist[-min(offset_from_end, len(hist))]
            else:
                first = hist[0]
            first_price = first.get("p") or 0
            last_price = last.get("p") or p.get("cur_price") or 0
            if first_price <= 0:
                return None
            change_pp = (last_price - first_price) * 100
            sz = p.get("size") or 0
            return {
                "title": p.get("title", ""),
                "asset": tid,
                "side": p.get("side", ""),
                "size": sz,
                "first_price": first_price,
                "last_price": last_price,
                "change_pp": change_pp,
                "change_pct": (last_price - first_price) / first_price * 100,
                "change_dollar": (last_price - first_price) * sz,
                "first_ts": first.get("t"),
                "last_ts": last.get("t"),
            }
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = [r for r in pool.map(compute, positions) if r]
        results.sort(key=lambda x: abs(x["change_pp"]), reverse=True)
        return jsonify({"ok": True, "range": rng, "items": results})

    @app.route("/api/holdings_rank")
    def holdings_rank():
        """持仓现值榜: 按 cur_price × size 降序."""
        exe = Executor.get()
        positions = exe.get_positions() or []
        items = []
        for p in positions:
            cur = p.get("cur_price") or 0
            size = p.get("size") or 0
            avg = p.get("avg_price") or 0
            value = cur * size
            items.append({
                "title": p.get("title", ""),
                "asset": p.get("asset", ""),
                "side": p.get("side", ""),
                "cur_price": cur,
                "avg_price": avg,
                "size": size,
                "value": value,
                "pnl_dollar": (cur - avg) * size,
                "pnl_pct": p.get("pnl_pct", 0),
            })
        items.sort(key=lambda x: x["value"], reverse=True)
        return jsonify({"ok": True, "items": items})

    @app.route("/api/portfolio_history")
    def portfolio_history():
        """资产总值历史曲线. range: 1d|1w|1m|1y|ytd|all"""
        from modules.db import get_portfolio_history
        import time
        from datetime import datetime, timezone
        r = (flask_request.args.get("range") or "1d").lower()
        now = int(time.time())
        if r == "1d":
            since = now - 86400
        elif r == "1w":
            since = now - 7*86400
        elif r == "1m":
            since = now - 30*86400
        elif r == "1y":
            since = now - 365*86400
        elif r == "ytd":
            ytd_dt = datetime(datetime.now(timezone.utc).year, 1, 1, tzinfo=timezone.utc)
            since = int(ytd_dt.timestamp())
        else:
            since = 0
        try:
            rows = get_portfolio_history(since)
            return jsonify({"ok": True, "range": r, "points": rows})
        except Exception as e:
            return jsonify({"ok": False, "message": str(e)})

    @app.route("/api/snapshot")
    def snapshot():
        """v4: 返回持仓快照 + monitor_state + edge计算"""
        try:
            from modules.db import get_position_meta, needs_reeval
            from datetime import datetime, timezone, timedelta
            
            exe = Executor.get()
            positions = exe.get_positions()
            rows = []
            total_pnl = 0.0
            total_cost = 0.0
            total_value = 0.0
            # v5.7 (P5): explicit None check; UI fallback 0, log warns so failure not silent.
            _cash_raw = exe.get_cash_balance()
            if _cash_raw is None:
                log.warning("/api/snapshot: cash API returned None (showing $0)")
            cash = _cash_raw if _cash_raw is not None else 0

            for p in positions:
                cp = p.get("cur_price") or 0
                ap = p.get("avg_price") or 0
                sz = p.get("size") or 0
                asset = p.get("asset", "")
                pnl_pct = ((cp - ap) / ap * 100) if ap > 0 else 0
                
                meta = get_position_meta(asset) or {}
                q = meta.get("new_tp") or meta.get("tp")
                edge_pp = ((q - cp) * 100) if q else None
                
                # 24h重评提醒
                reeval_due = needs_reeval(asset, hours=24) if meta else False
                
                # 距结算天数
                days_left = None
                end_date = meta.get("end_date") or ""
                if end_date:
                    try:
                        end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                        days_left = (end_dt - datetime.now(timezone.utc)).days
                    except Exception:
                        pass
                
                rows.append({
                    "asset": asset,
                    "cur_price": cp,
                    "value": cp * sz,
                    "pnl_pct": pnl_pct,
                    "pnl_dollar": (cp - ap) * sz,
                    "q": q,
                    "edge_pp": edge_pp,
                    "monitor_state": meta.get("monitor_state") or "PENDING",
                    "executed_action": meta.get("executed_action") or "",
                    "days_left": days_left,
                    "needs_reeval": reeval_due,
                    "last_reeval_at": meta.get("last_reeval_at") or meta.get("created_at"),
                })
                total_pnl += (cp - ap) * sz
                total_cost += ap * sz
                total_value += cp * sz
            
            return jsonify({
                "ok": True,
                "positions": rows,
                "total_pnl": total_pnl,
                "total_cost": total_cost,
                "total_value": total_value,
                "cash": cash,
                "assets_total": total_value + cash,
                "position_count": len(rows),
            })
        except Exception as e:
            log.exception(f"snapshot error: {e}")
            return jsonify({"ok": False, "message": str(e)})

    @app.route("/api/update_q", methods=["POST"])
    def update_q_route():
        """
        v4: 用户重评后更新 q (calibrated probability).
        会自动: 
          1. 写入 new_tp + last_reeval_at
          2. 维护 last_q_update_with_negative_edge (SOFT/CONFIRMED 状态机)
          3. 触发一次 monitor 心跳重新计算 monitor_state
        body: { token_id, new_q (0-1), reason? }
        return: { ok, monitor_state, edge_pp, message }
        """
        from modules.db import get_position_meta, update_q_value, log_event
        from modules.monitor import PositionMonitor
        try:
            data = flask_request.get_json() or {}
            token_id = data.get("token_id", "")
            new_q = data.get("new_q")
            reason = (data.get("reason") or "").strip()
            
            if not token_id:
                return jsonify({"ok": False, "message": "缺少 token_id"})
            # v5.7 (P10): fetch meta upfront so log_event can use real market_slug
            # (previously hard-coded "(unknown)" which broke audit trail).
            meta_for_slug = get_position_meta(token_id)
            real_slug = (meta_for_slug.get("market_slug") if meta_for_slug else None) or "(unknown)"
            # new_q 为 None 表示"维持原 q, 仅记录重评时间" (markReevalHold 用)
            if new_q is None:
                from modules.db import get_conn, _utc_now_iso  # v5.7 (P3): aware UTC
                conn = get_conn()
                conn.execute("UPDATE position_meta SET last_reeval_at=? WHERE token_id=?",
                             (_utc_now_iso(), token_id))
                conn.commit(); conn.close()
                log_event("update_q", real_slug, f"hold_no_change | {reason[:80]}")
                return jsonify({"ok": True, "message": "已记录重评 (q 维持原值)", "monitor_state": "(unchanged)"})
            try:
                new_q = float(new_q)
            except (ValueError, TypeError):
                return jsonify({"ok": False, "message": "new_q 必须是数字"})
            if not (0 < new_q < 1):
                return jsonify({"ok": False, "message": "new_q 必须在 0-1 之间"})
            
            meta = get_position_meta(token_id)
            if not meta:
                return jsonify({"ok": False, "message": "找不到该仓位 meta, 先填 TP 再重评"})
            
            entry = meta.get("entry_price") or 0
            if new_q <= entry:
                return jsonify({"ok": False, 
                    "message": f"new_q ({new_q:.3f}) 必须 > entry_price ({entry:.3f})"})
            
            # 写入 (会自动维护 last_q_update_with_negative_edge)
            update_q_value(token_id, new_q)
            
            # 记 event log
            old_q = meta.get("new_tp") or meta.get("tp") or 0
            log_event("update_q", meta.get("market_slug") or "?", 
                      f"q: {old_q:.3f} -> {new_q:.3f} | {reason[:80]}")
            
            # 触发心跳让 monitor_state 立即更新
            PositionMonitor().check_once()
            
            # 读最新状态返回给前端
            updated_meta = get_position_meta(token_id)
            new_state = updated_meta.get("monitor_state") if updated_meta else "?"
            
            # 拉当前价算 edge
            exe = Executor.get()
            cur_price = None
            for p in exe.get_positions():
                if p.get("asset") == token_id:
                    cur_price = p.get("cur_price")
                    break
            edge_pp = ((new_q - cur_price) * 100) if cur_price else None
            
            return jsonify({
                "ok": True,
                "monitor_state": new_state,
                "edge_pp": edge_pp,
                "new_q": new_q,
                "old_q": old_q,
                "message": f"q 已更新: {old_q:.3f} -> {new_q:.3f} | 状态: {new_state}"
            })
        except Exception as e:
            log.exception(f"update_q error: {e}")
            return jsonify({"ok": False, "message": str(e)})

    @app.route("/api/reeval_prompt")
    def reeval_prompt_route():
        """生成某仓位的重评 prompt"""
        from modules.db import get_position_meta
        from modules.prompts import build_reeval_prompt
        from datetime import datetime, timezone
        token_id = flask_request.args.get("token_id", "")
        if not token_id:
            return jsonify({"ok": False, "message": "缺少 token_id"})
        meta = get_position_meta(token_id)
        if not meta:
            return jsonify({"ok": False, "message": "找不到该仓位元数据"})
        # 拉当前价
        try:
            exe = Executor.get()
            positions = exe.get_positions()
            cur_price = None
            live_avg = 0
            for p in positions:
                if p.get("asset") == token_id:
                    cur_price = p.get("cur_price") or 0
                    live_avg = p.get("avg_price") or 0
                    break
            if cur_price is None:
                return jsonify({"ok": False, "message": "找不到该仓位的当前价"})
            # Self-heal: 如果 meta.entry_price 为 0 但 Polymarket 实时 avg_price 有值, 写回 DB
            db_entry = float(meta.get("entry_price") or 0)
            if db_entry == 0 and live_avg and live_avg > 0:
                from modules.db import update_entry_price
                update_entry_price(token_id, live_avg)
                meta["entry_price"] = live_avg
                log.info(f"healed entry_price for {token_id[:20]} → ${live_avg:.4f}")
            
            # 距结算天数
            end_date = meta.get("end_date") or ""
            days_left = 0
            if end_date:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                days_left = (end_dt - datetime.now(timezone.utc)).days
            
            # 进度
            entry = meta.get("entry_price") or 0
            tp = meta.get("new_tp") or meta.get("tp") or 0
            gap = tp - entry
            progress = max(0.0, min(1.0, (cur_price - entry) / gap)) if gap > 0 else 0
            
            # v4.1: 反查 Gamma 拿真实 question (而不是 slug)
            # v5.2: 同时拉 description (Resolution 规则全文) 嵌进 prompt
            try:
                import requests as _req
                gr = _req.get("https://gamma-api.polymarket.com/markets",
                              params={"clob_token_ids": token_id, "limit": 1},
                              timeout=8).json()
                if gr and isinstance(gr, list) and len(gr) > 0:
                    m_data = gr[0]
                    q_text = m_data.get("question", "") or ""
                    desc_text = m_data.get("description", "") or ""
                    if q_text:
                        meta["_market_question"] = q_text
                    if desc_text:
                        meta["_market_description"] = desc_text
            except Exception:
                pass
            
            prompt = build_reeval_prompt(meta, cur_price, days_left, progress)
            return jsonify({"ok": True, "prompt": prompt, 
                            "market_slug": meta.get("market_slug", ""),
                            "tp": tp, "cur_price": cur_price})
        except Exception as e:
            log.exception(f"reeval_prompt error: {e}")
            return jsonify({"ok": False, "message": str(e)})

    @app.route("/api/mark_reeval", methods=["POST"])
    def mark_reeval_route():
        """标记重评结果. body: {token_id, action: uplift|skip|close, new_tp?}"""
        from modules.db import mark_reeval, log_event
        data = flask_request.get_json() or {}
        token_id = data.get("token_id", "")
        action = data.get("action", "")
        new_tp = data.get("new_tp")
        if not token_id or action not in ("uplift", "skip", "close"):
            return jsonify({"ok": False, "message": "参数错误"})
        if action == "uplift":
            if new_tp is None:
                return jsonify({"ok": False, "message": "uplift 必须提供 new_tp"})
            try:
                new_tp = float(new_tp)
                if not (0 < new_tp < 1):
                    return jsonify({"ok": False, "message": "new_tp 必须是 0-1 之间的小数"})
            except Exception:
                return jsonify({"ok": False, "message": "new_tp 格式错误"})
        ok = mark_reeval(token_id, action, new_tp=new_tp if action == "uplift" else None)
        if ok:
            log_event("reeval", token_id[:20], f"action={action} new_tp={new_tp}")
            msg_map = {
                "uplift": f"已上调 TP 到 {new_tp*100:.1f}%",
                "skip": "已跳过重评 (维持原 TP)",
                "close": "已标记重评清仓 (请去 Polymarket 网页手动卖出)"
            }
            return jsonify({"ok": True, "message": msg_map[action]})
        return jsonify({"ok": False, "message": "标记失败"})

    @app.route("/api/record_position", methods=["POST"])
    def record_position():
        from modules.db import save_position_meta
        data = flask_request.get_json() or {}
        try:
            entry_price = float(data["entry_price"])
            tp = float(data["tp"])
            side = data.get("side","YES")
            # 兜底: 前端传 entry_price=0 (polymarket avg API 还没算好等场景)
            # → server 实时从 polymarket 拉 avgPrice 兜底. 防止 entry=0 污染 db.
            if entry_price <= 0:
                token_id = data.get("token_id", "")
                try:
                    exe = Executor.get()
                    for p in exe.get_positions():
                        if p.get("asset") == token_id:
                            live_avg = float(p.get("avg_price") or 0)
                            if live_avg > 0:
                                entry_price = live_avg
                                log.warning(f"healed entry_price=0 for {token_id[:20]} → ${entry_price:.4f} (polymarket avg)")
                            break
                except Exception as e:
                    log.warning(f"entry_price heal failed: {e}")
                if entry_price <= 0:
                    return jsonify({"ok": False, "message": "❌ entry_price=0 且无法从 polymarket 拉到 avg, 请刷新页面重试"})
            # Sanity check: TP必须高于持仓token买入价 (持有token涨=赚钱)
            if tp <= entry_price:
                return jsonify({
                    "ok": False,
                    "message": f"❌ TP方向错误! 你的{side}仓位买入价是{entry_price*100:.1f}%, "
                               f"TP({tp*100:.1f}%)必须>买入价。"
                               f"提醒: TP填的是你持仓token的目标价 (买NO就填NO的目标价)"
                })
            if tp >= 1.0:
                return jsonify({"ok": False, "message": "TP不能>=100%"})
                        # v4.1 防御: slug 或 end_date 为空时, 服务端反查 Gamma
            if not data.get("slug") or not data.get("end_date"):
                try:
                    import requests as _req
                    r = _req.get("https://gamma-api.polymarket.com/markets",
                                 params={"clob_token_ids": data["token_id"], "limit": 1},
                                 timeout=8).json()
                    if r and isinstance(r, list) and len(r) > 0:
                        m = r[0]
                        if not data.get("slug"):
                            data["slug"] = m.get("slug", "") or ""
                        if not data.get("end_date"):
                            data["end_date"] = m.get("endDate", "") or ""
                except Exception:
                    pass  # gamma_lookup 失败也不阻塞保存
            
            # 校验 stop_loss_tier (可选)
            stop_loss_tier = data.get("stop_loss_tier") or None
            if stop_loss_tier and stop_loss_tier not in ("convergent", "hybrid", "event_driven"):
                return jsonify({"ok": False, "message": f"❌ 止损档无效: {stop_loss_tier} (允许: convergent/hybrid/event_driven)"})
            # v5.7 (P9): defensive numeric validation — reject obviously bad inputs
            if not (0 < tp < 1):
                return jsonify({"ok": False, "message": f"❌ tp 范围错: {tp} (需 0-1)"})
            if not (0 < entry_price < 1):
                return jsonify({"ok": False, "message": f"❌ entry_price 范围错: {entry_price} (需 0-1)"})
            _size_raw = float(data.get("size", 0))
            if _size_raw < 0 or _size_raw > 100000:
                return jsonify({"ok": False, "message": f"❌ size 不合理: {_size_raw}"})
            # v5.7 (P8): entry_reason fallback — stub so column not永远 NULL.
            # User-supplied reason from request takes precedence; otherwise generate minimal trace.
            entry_reason_raw = (data.get("entry_reason") or "").strip()[:2000]
            if not entry_reason_raw:
                from datetime import datetime, timezone
                _now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                _conf = data.get("original_confidence") or "?"
                _tier = stop_loss_tier or "(legacy)"
                entry_reason_raw = f"[auto-stub {_now}] tp={tp:.3f} conf={_conf} tier={_tier} — user did not provide reason"
            # v5.7 (P6): snapshot claude_raw_estimate = tp at entry. Frozen across future re-evals so
            # calibration check can compare original LLM estimate vs final resolution.
            save_position_meta(
                token_id=data["token_id"],
                market_slug=data.get("slug",""),
                side=side,
                entry_price=entry_price,
                tp=tp,
                end_date=data.get("end_date",""),
                initial_size=_size_raw,
                notes=data.get("notes",""),
                entry_reason=entry_reason_raw,
                claude_raw_estimate=tp,
                original_confidence=(data.get("original_confidence") or None),
                stop_loss_tier=stop_loss_tier,
            )
            return jsonify({"ok":True,"message":"持仓元数据已记录"})
        except Exception as e:
            return jsonify({"ok":False,"message":str(e)})

    @app.route("/api/update_confidence", methods=["POST"])
    def update_confidence_api():
        from modules.db import update_confidence
        data = flask_request.get_json() or {}
        token_id = data.get("token_id")
        confidence = data.get("confidence") or None
        if not token_id:
            return jsonify({"ok": False, "message": "missing token_id"})
        if confidence and confidence not in ("high", "medium", "low"):
            return jsonify({"ok": False, "message": "invalid confidence value"})
        rows = update_confidence(token_id, confidence)
        return jsonify({"ok": True, "rows": rows})

    @app.route("/api/update_tier", methods=["POST"])
    def update_tier_api():
        from modules.db import update_stop_loss_tier
        data = flask_request.get_json() or {}
        token_id = data.get("token_id")
        tier = data.get("stop_loss_tier") or None
        if not token_id:
            return jsonify({"ok": False, "message": "missing token_id"})
        if tier and tier not in ("convergent", "hybrid", "event_driven"):
            return jsonify({"ok": False, "message": "invalid tier value"})
        rows = update_stop_loss_tier(token_id, tier)
        return jsonify({"ok": True, "rows": rows})

    @app.route("/api/update_tp", methods=["POST"])
    def update_tp_api():
        from modules.db import update_tp, get_position_meta
        data = flask_request.get_json() or {}
        try:
            new_tp = float(data["new_tp"])
            token_id = data["token_id"]
            meta = get_position_meta(token_id)
            if meta:
                entry_price = meta.get("entry_price")
                if entry_price and new_tp <= entry_price:
                    return jsonify({
                        "ok": False,
                        "message": f"❌ TP方向错误! 持仓买入价{entry_price*100:.1f}%, "
                                   f"TP({new_tp*100:.1f}%)必须>买入价"
                    })
            if new_tp >= 1.0 or new_tp <= 0:  # v5.7 (P9): also reject ≤0
                return jsonify({"ok": False, "message": "TP 必须在 (0, 1) 之间"})
            update_tp(token_id, new_tp)
            return jsonify({"ok":True,"message":"tp已更新"})
        except Exception as e:
            return jsonify({"ok":False,"message":str(e)})

    @app.route("/api/full_prompt")
    def full_prompt():
        """返回最新的Prompt+扫描报告 (实时拼接)"""
        try:
            try:
                with open("last_scan.md", "r") as f:
                    scan_content = f.read()
            except:
                scan_content = "(请先用扫描器生成候选市场列表)"
            full = DISCOVERY_PROMPT.replace("{positions_list}", scan_content)
            return jsonify({"ok": True, "prompt": full})
        except Exception as e:
            log.exception(f"full_prompt error: {e}")
            return jsonify({"ok": False, "message": str(e)})

    @app.route("/api/closed_positions")
    def closed_positions():
        """已卖出仓位 + 卖后价格走势. range = 1w / 1m. 跳过已结算到 0/1 的市场."""
        from datetime import datetime, timezone, timedelta
        from collections import defaultdict
        import os, json as J, time
        try:
            rng = (flask_request.args.get("range") or "1w").lower()
            if rng == "all":
                since_ts = 0  # 不限时间, 拉全部历史
            else:
                days = 7 if rng == "1w" else 30
                since_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())

            wallet = os.getenv("POLY_FUNDER")
            if not wallet:
                return jsonify({"ok": False, "message": "POLY_FUNDER not set"})

            import requests as _req
            # 1. 拉 trades
            trades = []
            offset = 0
            while True:
                r = _req.get("https://data-api.polymarket.com/activity",
                             params={"user": wallet, "limit": 500, "offset": offset, "type": "TRADE"},
                             timeout=20).json()
                if not r: break
                trades.extend(r)
                if len(r) < 500: break
                offset += 500
                if offset > 2000: break  # safety
            trades_in_range = [t for t in trades if t["timestamp"] >= since_ts]

            # 2. 按 asset 分组. 同时记每个 asset 的全部 trades (包括 range 外, 用于 round 入场均价)
            asset_meta = {}
            sells_by_asset = defaultdict(list)
            all_trades_by_asset = defaultdict(list)
            for t in trades:  # 全部 trades, 不只 range 内
                asset_meta[t["asset"]] = (t["title"], t["conditionId"])
                all_trades_by_asset[t["asset"]].append(t)
            for t in trades_in_range:
                if t["side"] == "SELL":
                    sells_by_asset[t["asset"]].append(t)
            if not sells_by_asset:
                return jsonify({"ok": True, "rows": []})

            # 2b. 查 db.events 拿每个 sell 的触发类型 (auto_sell/user_sell detail 含 STATE)
            import sqlite3 as _sql
            from modules.db import DB_PATH as _DB_PATH
            db_conn = _sql.connect(_DB_PATH)
            db_events = db_conn.execute("SELECT timestamp, market_slug, detail FROM events WHERE event_type IN ('auto_sell','user_sell') AND timestamp >= ?", ("2026-04-01",)).fetchall()
            db_conn.close()
            # 索引: title → list[(ts, detail)]
            db_by_title = defaultdict(list)
            for ts_str, slug, detail in db_events:
                try:
                    db_dt = datetime.fromisoformat(ts_str)
                    # log_event 写入用的是 datetime.now() (local time, naive). Python
                    # naive datetime.timestamp() 默认按 system local tz 转 unix, 跟
                    # polymarket trade.timestamp (unix UTC) 自然对齐.
                    db_by_title[slug].append((db_dt.timestamp(), detail))
                except Exception: pass
            def detect_trigger(title, trade_ts):
                """找 trade 时间 ±120s 内 db 最近的 sell event, 返回 (label, raw_state)"""
                cands = db_by_title.get(title, [])
                if not cands: return None
                best = None; best_diff = 999
                for db_ts, detail in cands:
                    d = abs(db_ts - trade_ts)
                    if d < best_diff: best_diff = d; best = detail
                if best is None or best_diff > 300: return None
                state = best.split(" ")[0] if best else ""
                label_map = {
                    "TAKE_PROFIT_PRICE": "📈 价格止盈",
                    "TAKE_PROFIT_PNL":   "📈 浮盈翻倍",
                    "STOP_LOSS":         "📉 自动止损",
                    "TIME_STOP":         "⏰ 临结算锁定",
                    "FORCE_EXIT":        "✗ 手动清仓",
                    "AT_TARGET":         "🎯 到目标",
                    "DISASTER":          "💥 灾难止损 (v4)",
                    "FROZEN_EXPIRED":    "❄️ 冻结到期 (v5)",
                }
                return label_map.get(state, state)

            # 3. batch gamma-api 拉 current prices
            cond_ids = list({asset_meta[a][1] for a in sells_by_asset})
            markets = {}
            for i in range(0, len(cond_ids), 20):
                batch = cond_ids[i:i+20]
                params = [("condition_ids", c) for c in batch]
                try:
                    r = _req.get("https://gamma-api.polymarket.com/markets", params=params, timeout=20).json()
                    for m in r:
                        markets[m["conditionId"]] = m
                except Exception:
                    pass

            # 4. 每个 asset 一行, 行内含 sells 数组 (按时间倒序, 最新在前)
            rows = []
            for asset, sells in sells_by_asset.items():
                title, cond = asset_meta[asset]
                m = markets.get(cond, {})
                cur_price = None; closed = False
                try:
                    ctids = J.loads(m.get("clobTokenIds", "[]"))
                    prices = J.loads(m.get("outcomePrices", "[]"))
                    if asset in ctids:
                        cur_price = float(prices[ctids.index(asset)])
                    closed = bool(m.get("closed"))
                except Exception:
                    pass
                if cur_price is None: continue
                if closed or cur_price >= 0.98 or cur_price <= 0.02:
                    continue
                # 把该 asset 全部 trades (无论 range) 按 round 切分,
                # 一个 round = 累计 BUY 后跟一组 SELL 直到 size 清零 (再次买入开新 round)
                all_t = sorted(all_trades_by_asset[asset], key=lambda x: x["timestamp"])
                rounds = []
                cur_round = {"buys": [], "sells": []}
                cur_sz = 0.0
                for tr in all_t:
                    if tr["side"] == "BUY":
                        if cur_round["sells"] and cur_sz < 0.01:
                            rounds.append(cur_round)
                            cur_round = {"buys": [], "sells": []}
                        cur_round["buys"].append(tr)
                        cur_sz += tr["size"]
                    else:
                        cur_round["sells"].append(tr)
                        cur_sz -= tr["size"]
                if cur_round["buys"] or cur_round["sells"]:
                    rounds.append(cur_round)
                # 找每个 sell 属于哪个 round → 算 round 入场均价
                def find_round(sell_t):
                    for r in rounds:
                        if any(s["timestamp"] == sell_t["timestamp"] for s in r["sells"]):
                            return r
                    return None

                sells_sorted = sorted(sells, key=lambda s: s["timestamp"], reverse=True)
                n = len(sells_sorted)
                sell_cards = []
                for idx, t in enumerate(sells_sorted):
                    seq = n - idx
                    rnd = find_round(t)
                    entry_avg = None; first_buy_ts = None
                    if rnd and rnd["buys"]:
                        bcost = sum(b["usdcSize"] for b in rnd["buys"])
                        bsize = sum(b["size"] for b in rnd["buys"])
                        if bsize > 0:
                            entry_avg = bcost / bsize
                        first_buy_ts = min(b["timestamp"] for b in rnd["buys"])
                    cost = (entry_avg * float(t["size"])) if entry_avg else None
                    pnl = (float(t["usdcSize"]) - cost) if cost is not None else None
                    roi = (pnl / cost * 100) if (cost and cost > 0) else None
                    # 持仓时长
                    duration = None
                    if first_buy_ts:
                        secs = t["timestamp"] - first_buy_ts
                        d, rem = divmod(secs, 86400)
                        h = rem // 3600
                        duration = f"{int(d)}天{int(h)}小时" if d > 0 else f"{int(h)}小时{int((rem%3600)//60)}分"
                    trigger = detect_trigger(title, t["timestamp"])
                    sell_cards.append({
                        "seq": seq,
                        "date": datetime.fromtimestamp(t["timestamp"], tz=timezone.utc).strftime("%m-%d %H:%M"),
                        "size": round(float(t["size"]), 4),
                        "price": round(float(t["price"]), 4),
                        "revenue": round(float(t["usdcSize"]), 4),
                        "entry_avg": round(entry_avg, 4) if entry_avg else None,
                        "cost": round(cost, 4) if cost is not None else None,
                        "pnl": round(pnl, 4) if pnl is not None else None,
                        "roi": round(roi, 2) if roi is not None else None,
                        "duration": duration,
                        "trigger": trigger,
                        "change_pp": round((cur_price - float(t["price"])) * 100, 2),
                    })
                latest_ts = sells_sorted[0]["timestamp"]
                rows.append({
                    "title": title,
                    "latest_sell_ts": latest_ts,
                    "latest_sell_date": datetime.fromtimestamp(latest_ts, tz=timezone.utc).strftime("%m-%d %H:%M"),
                    "cur_price": round(cur_price, 4),
                    "sells": sell_cards,
                    "sell_count": n,
                })
            rows.sort(key=lambda r: r["latest_sell_ts"], reverse=True)
            return jsonify({"ok": True, "rows": rows})
        except Exception as e:
            log.exception(f"closed_positions error: {e}")
            return jsonify({"ok": False, "message": str(e)})

    @app.route("/api/scan_report")
    def scan_report():
        import os
        try:
            mtime = os.path.getmtime("last_scan.md")
            with open("last_scan.md", "r") as f:
                return jsonify({"ok":True, "report": f.read(), "mtime": mtime})
        except:
            return jsonify({"ok":False, "report": "暂无扫描报告。点击扫描按钮开始。", "mtime": 0})

    @app.route("/api/logs")
    def api_logs():
        try:
            r = subprocess.run(["tail","-80","bot.log"],capture_output=True,text=True,timeout=5)
            lines = r.stdout.strip().split("\n") if r.stdout else []
            filtered = [l for l in lines if "/api/" not in l and "GET / " not in l]
            return jsonify({"ok":True,"lines":filtered[-40:]})
        except:
            return jsonify({"ok":False,"lines":[]})

    return app
