#!/usr/bin/env python3
"""Polymarket v4"""
import threading
import logging
from modules.db import init_db
from modules.monitor import PositionMonitor
from modules.dashboard import create_app, set_monitor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()])
log = logging.getLogger("main")

if __name__ == "__main__":
    init_db()
    # 部署后 seed 一行 portfolio_snapshot,避免首次打开图表为空
    try:
        import time
        from modules.executor import Executor
        from modules.db import save_portfolio_snapshot
        _exe = Executor.get()
        _pos = _exe.get_positions() or []
        _tv = sum((p.get("cur_price") or 0) * (p.get("size") or 0) for p in _pos)
        _tc = sum((p.get("avg_price") or 0) * (p.get("size") or 0) for p in _pos)
        _cash = _exe.get_cash_balance()
        save_portfolio_snapshot(int(time.time()), _tv, _tc, _cash, _tv - _tc, _tv + _cash)
        log.info(f"seeded portfolio_snapshot: value=${_tv:.2f} cost=${_tc:.2f} cash=${_cash:.2f}")
    except Exception as e:
        log.warning(f"seed snapshot failed: {e}")
    monitor = PositionMonitor()
    set_monitor(monitor)
    app = create_app()
    log.info("=== Polymarket v4 ===")
    log.info("Dashboard: http://localhost:5051")
    log.info("Monitor: every 3 min | TP: +100%/+300%/+500% | SL: -50%")
    threading.Thread(target=monitor.run_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5051, debug=False)
