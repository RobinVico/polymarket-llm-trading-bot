#!/usr/bin/env python3
"""Polymarket v7.1 — 入口"""
import threading
import logging
from modules.version import VERSION  # v7.x (#1): 单一版本号来源
from dotenv import load_dotenv
load_dotenv()  # 本目录 .env (DASHBOARD_PASSWORD / FLASK_SECRET_KEY); v3 的 .env 由 executor.py 顶部另行加载
# v5.10.2: 本机系统 DNS (Tailscale 上游) 对 *.polymarket.com 间歇性污染 (解析到 FB/Dropbox 假 IP),
# 必须在任何 polymarket 连接建立前打 DNS guard (DoH 优先). 详见 modules/gamma_client.py.
from modules.gamma_client import install_polymarket_dns_guard
install_polymarket_dns_guard()
from modules.db import init_db
from modules.monitor import PositionMonitor
from modules.dashboard import create_app, set_monitor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()])
log = logging.getLogger("main")

if __name__ == "__main__":
    init_db()
    # 部署后 seed 一行 portfolio_snapshot,避免首次打开图表为空
    # 守卫: 跟 monitor.py check_once 一致 — positions=[] AND cash=0 几乎肯定 API 失败
    # (geoblock / 网络抖动), 跳过 seed 避免写一行全 0 污染曲线.
    try:
        import time
        from modules.executor import Executor
        from modules.db import save_portfolio_snapshot
        _exe = Executor.get()
        _pos = _exe.get_positions() or []
        _cash = _exe.get_cash_balance()
        if not _pos and (_cash is None or _cash == 0):
            log.warning("skip seed portfolio_snapshot: positions=[] AND cash=0 (likely API failure)")
        else:
            _tv = sum((p.get("cur_price") or 0) * (p.get("size") or 0) for p in _pos)
            _tc = sum((p.get("avg_price") or 0) * (p.get("size") or 0) for p in _pos)
            save_portfolio_snapshot(int(time.time()), _tv, _tc, _cash, _tv - _tc, _tv + _cash)
            log.info(f"seeded portfolio_snapshot: value=${_tv:.2f} cost=${_tc:.2f} cash=${_cash:.2f}")
    except Exception as e:
        log.warning(f"seed snapshot failed: {e}")
    monitor = PositionMonitor()
    set_monitor(monitor)
    app = create_app()
    log.info(f"=== Polymarket v{VERSION} ===")
    log.info("Dashboard: http://localhost:5051")
    log.info("Monitor: 每 30 秒 | TP: 事件型 翻倍(2×avg)先到则全卖, 否则0.92卖半(后半跌<0.78再卖)/收敛≤3天 0.88/其余 0.90 或 +100% | SL: 收敛=最高价回撤20%(≤3天12%)+确认 / 混合=最高价回撤35%+确认 / 事件 入场-60%+$0.05地板 / 未分类默认当混合 (砸穿→重评)")
    threading.Thread(target=monitor.run_loop, daemon=True).start()
    app.run(host="127.0.0.1", port=5051, debug=False)
