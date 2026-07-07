import sqlite3
import json
from datetime import datetime, timezone, timedelta

DB_PATH = "v4.db"

def get_conn():
    # v5.7 (P2): WAL mode allows concurrent reads while monitor heartbeat writes.
    # busy_timeout gives Flask requests a window to retry instead of immediate fail.
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn

def _parse_iso_to_aware(s):
    """v5.7 (P3): Parse ISO string -> always aware UTC datetime. Handles both naive (old data) and aware (new data) inputs."""
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def _utc_now_iso():
    """v5.7 (P3): Canonical UTC-aware ISO timestamp used throughout writes."""
    return datetime.now(timezone.utc).isoformat()

def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            market_slug TEXT,
            detail TEXT
        );
        CREATE TABLE IF NOT EXISTS tier_sold (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id TEXT NOT NULL,
            tier_name TEXT NOT NULL,
            timestamp TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS position_meta (
            token_id TEXT PRIMARY KEY,
            market_slug TEXT,
            side TEXT,
            entry_price REAL,
            tp REAL,
            target_gap REAL,
            end_date TEXT,
            initial_size REAL,
            created_at TEXT,
            new_tp REAL,
            tp_updated_at TEXT,
            notes TEXT,
            entry_reason TEXT,
            claude_raw_estimate REAL,
            reeval_status TEXT DEFAULT 'pending',
            reeval_at TEXT,
            reeval_new_tp REAL,
            reeval_action TEXT,
            monitor_state TEXT DEFAULT 'PENDING',
            monitor_state_at TEXT,
            last_q_update_with_negative_edge TEXT,
            last_reeval_at TEXT,
            executed_action TEXT,
            original_confidence TEXT,
            freeze_until TEXT,
            freeze_stop_price REAL,
            stop_loss_tier TEXT,
            autostop_disabled INTEGER DEFAULT 0,
            reeval_watch_loss REAL,
            peak_price REAL
        );
        CREATE TABLE IF NOT EXISTS portfolio_snapshot (
            ts INTEGER PRIMARY KEY,
            total_value REAL,
            total_cost REAL,
            cash REAL,
            total_pnl REAL,
            assets_total REAL
        );
        CREATE INDEX IF NOT EXISTS idx_portfolio_ts ON portfolio_snapshot(ts);
        -- v5.7 (P7): full closed-position history for PnL / win-rate / calibration analytics.
        -- Populated on any successful sell (auto or user). Existing events.detail string remains for backward compat.
        CREATE TABLE IF NOT EXISTS closed_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id TEXT NOT NULL,
            market_slug TEXT,
            side TEXT,
            avg_entry_price REAL,
            exit_price REAL,
            size REAL,
            realized_pnl_usd REAL,
            realized_pnl_pct REAL,
            exit_reason TEXT,
            stop_loss_tier TEXT,
            claude_raw_estimate REAL,
            entry_at TEXT,
            exit_at TEXT,
            hold_duration_hours REAL
        );
        CREATE INDEX IF NOT EXISTS idx_closed_exit_at ON closed_positions(exit_at);
        -- v5.7 (P11): persistent login-failure counter (survives process restart, prevents brute-force restart bypass).
        CREATE TABLE IF NOT EXISTS login_attempts (
            ip TEXT PRIMARY KEY,
            fail_count INTEGER NOT NULL,
            window_start_ts INTEGER NOT NULL
        );
        -- v5.9: shadow-mode log for position sizing formula. Records every suggestion + what
        -- the user actually chose. Used after 2-4 weeks to calibrate formula parameters.
        CREATE TABLE IF NOT EXISTS sizing_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            token_id TEXT,
            market_slug TEXT,
            q REAL,
            p REAL,
            confidence TEXT,
            stop_loss_tier TEXT,
            days_to_resolution INTEGER,
            cluster_id TEXT,
            bankroll_usd REAL,
            cluster_exposure_usd REAL,
            cluster_cap_usd REAL,
            exposed_dd_usd REAL,
            size_usd_suggested REAL,
            size_usd_actual REAL,
            reason TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sizing_log_ts ON sizing_log(ts);
        -- v5.13: 大跌自动重评建议. monitor 触发联网调研后写 pending, 用户在 dashboard 确认才执行.
        CREATE TABLE IF NOT EXISTS auto_reeval_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id TEXT NOT NULL,
            slug TEXT,
            title TEXT,
            side TEXT,
            avg_price REAL,
            cur_price REAL,
            loss_pct REAL,
            trigger_reason TEXT,
            action TEXT,
            new_q REAL,
            orig_q REAL,
            confidence TEXT,
            thesis_broken INTEGER,
            headline_event TEXT,
            reason TEXT,
            sources TEXT,
            raw_text TEXT,
            provider TEXT,
            pre_dump_center REAL,
            price_curve TEXT,
            status TEXT NOT NULL DEFAULT 'analyzing',
            error TEXT,
            created_at TEXT NOT NULL,
            decided_at TEXT,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_auto_reeval_status ON auto_reeval_suggestions(status);
        -- v5.14: 通用 key-value 状态 (在线/离线 presence 等)
        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        );
        -- v7.1: 模拟盘/测试仓 (paper trading). 不真下单, 按用户填的入场价实时盯盘, 跑跟真仓一样的算法.
        CREATE TABLE IF NOT EXISTS paper_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id TEXT,
            market_slug TEXT,
            title TEXT,
            side TEXT,
            entry_price REAL,
            size_usd REAL,
            shares REAL,
            q REAL,
            confidence TEXT,
            stop_loss_tier TEXT,
            cluster_id TEXT,
            tag TEXT,
            end_date TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            cur_price REAL,
            peak_price REAL,
            monitor_state TEXT,
            last_checked_at TEXT,
            would_sell_at_ts TEXT,
            would_sell_price REAL,
            would_sell_reason TEXT,
            would_sell_pnl_usd REAL,
            status TEXT NOT NULL DEFAULT 'open',
            is_resolved INTEGER DEFAULT 0,
            final_outcome REAL,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_positions(status);
    """)
    # Migration: 给已有 position_meta 加新字段(忽略已存在错误)
    for col, decl in [
        ("entry_reason", "TEXT"),
        ("claude_raw_estimate", "REAL"),
        ("reeval_status", "TEXT DEFAULT 'pending'"),
        ("reeval_at", "TEXT"),
        ("reeval_new_tp", "REAL"),
        ("reeval_action", "TEXT"),
        ("monitor_state", "TEXT DEFAULT 'PENDING'"),
        ("monitor_state_at", "TEXT"),
        ("last_q_update_with_negative_edge", "TEXT"),
        ("last_reeval_at", "TEXT"),
        ("executed_action", "TEXT"),
        ("original_confidence", "TEXT"),
        ("freeze_until", "TEXT"),
        ("freeze_stop_price", "REAL"),
        ("stop_loss_tier", "TEXT"),
        ("autostop_disabled", "INTEGER DEFAULT 0"),
        ("reeval_watch_loss", "REAL"),
        # v5.9: correlation-based cluster grouping (Claude cluster-analyzer skill assigns)
        ("cluster_id", "TEXT"),
        # v5.9: shadow-mode size suggestion from formula at entry time (immutable across re-evals)
        ("size_usd_suggested", "REAL"),
        # v5.10: scanner tag (Iran/Politics/etc) recorded at entry, copied to closed_positions on sell
        ("tag", "TEXT"),
        # v5.10.1: 用户开关 — 1=本仓位禁用自动止盈 (TAKE_PROFIT_PRICE + TAKE_PROFIT_PNL 不触发)
        ("disable_take_profit", "INTEGER DEFAULT 0"),
        # v7.0: 收敛型移动止损 — 持有期最高价 (每心跳更新, 仅 convergent 消费)
        ("peak_price", "REAL"),
    ]:
        try:
            conn.execute(f"ALTER TABLE position_meta ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError:
            pass
    # v5.10: closed_positions 加 6 列 (cluster_id, tag, resolution 状态四件套)
    for col, decl in [
        ("cluster_id", "TEXT"),
        ("tag", "TEXT"),
        ("is_resolved", "INTEGER DEFAULT 0"),
        ("resolved_at", "TEXT"),
        ("final_outcome", "REAL"),  # 买方向赢了=1, 输了=0, NULL=未结算
        ("is_correct", "INTEGER"),  # 1=赌赢了, 0=赌输了, NULL=未结算
    ]:
        try:
            conn.execute(f"ALTER TABLE closed_positions ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError:
            pass
    # v6.0.2: auto_reeval_suggestions 加 orig_q (触发时该仓的 q, 用于"原 q → 新 q"显示)
    try:
        conn.execute("ALTER TABLE auto_reeval_suggestions ADD COLUMN orig_q REAL")
    except sqlite3.OperationalError:
        pass
    # v6.0.7: auto_reeval_suggestions 加 provider (哪个模型出的决策: glm / claude)
    try:
        conn.execute("ALTER TABLE auto_reeval_suggestions ADD COLUMN provider TEXT")
    except sqlite3.OperationalError:
        pass
    # v7.0: 反锚定校准 — 大跌前价格中枢 + 触发时价格曲线 JSON (供日后用真实数据校准 q vs center vs cur)
    try:
        conn.execute("ALTER TABLE auto_reeval_suggestions ADD COLUMN pre_dump_center REAL")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE auto_reeval_suggestions ADD COLUMN price_curve TEXT")
    except sqlite3.OperationalError:
        pass
    # v7.x: compare_json — Claude + GLM 两模型完整输出 (只给「API重评」对比页; 主列仍是权威=Claude)
    try:
        conn.execute("ALTER TABLE auto_reeval_suggestions ADD COLUMN compare_json TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit(); conn.close()

def log_event(event_type, market_slug="", detail=""):
    conn = get_conn()
    conn.execute("INSERT INTO events (timestamp, event_type, market_slug, detail) VALUES (?,?,?,?)",
                 (_utc_now_iso(), event_type, market_slug, detail))
    conn.commit(); conn.close()

def get_recent_events(limit=50):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_sold_tiers(token_id):
    conn = get_conn()
    rows = conn.execute("SELECT tier_name FROM tier_sold WHERE token_id=?", (token_id,)).fetchall()
    conn.close()
    return [r["tier_name"] for r in rows]

def record_tier_sold(token_id, tier_name):
    conn = get_conn()
    conn.execute("INSERT INTO tier_sold (token_id, tier_name, timestamp) VALUES (?,?,?)",
                 (token_id, tier_name, _utc_now_iso()))
    conn.commit(); conn.close()

def save_position_meta(token_id, market_slug, side, entry_price, tp, end_date, initial_size,
                       notes="", entry_reason="", claude_raw_estimate=None,
                       original_confidence=None, stop_loss_tier=None,
                       cluster_id=None, size_usd_suggested=None, tag=None):
    """下单后手动记录元数据, 供 monitor 使用.
    stop_loss_tier: convergent (-20%) / hybrid (-35%) / event_driven (only $0.05 floor) / None (legacy -25%)
    v5.9 新增: cluster_id (相关性簇 kebab-case), size_usd_suggested (公式当时推荐, 不变 across re-evals)
    v5.10 新增: tag (scanner 命中的 polymarket 标签, 用于历史 analytics 按 tag 聚合胜率)"""
    target_gap = tp - entry_price if side.upper() in ("YES","Yes") else entry_price - tp
    conn = get_conn()
    conn.execute("""INSERT OR REPLACE INTO position_meta
        (token_id, market_slug, side, entry_price, tp, target_gap, end_date, initial_size,
         created_at, notes, entry_reason, claude_raw_estimate, original_confidence,
         stop_loss_tier, cluster_id, size_usd_suggested, tag, reeval_status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending')""",
        (token_id, market_slug, side, entry_price, tp, target_gap, end_date, initial_size,
         _utc_now_iso(), notes, entry_reason, claude_raw_estimate, original_confidence,
         stop_loss_tier, cluster_id, size_usd_suggested, tag))
    conn.commit(); conn.close()

def set_disable_take_profit(token_id, disabled):
    """v5.10.1: 用户切换"禁止自动止盈". disabled=1 → monitor 跳过 TAKE_PROFIT_PRICE+PNL; 0 → 恢复.
    返回更新行数."""
    conn = get_conn()
    cur = conn.execute(
        "UPDATE position_meta SET disable_take_profit=? WHERE token_id=?",
        (int(bool(disabled)), token_id),
    )
    rows = cur.rowcount
    conn.commit(); conn.close()
    return rows


def update_stop_loss_tier(token_id, tier):
    """更新仓位的 stop_loss_tier (convergent / hybrid / event_driven / None)."""
    conn = get_conn()
    cur = conn.execute("UPDATE position_meta SET stop_loss_tier=? WHERE token_id=?", (tier, token_id))
    rows = cur.rowcount
    conn.commit(); conn.close()
    return rows

def get_position_meta(token_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM position_meta WHERE token_id=?", (token_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def clear_position_meta(token_id):
    """仓位平仓后调用. 整行删除 position_meta, 让下次重新买入是一张白纸 (NO_META 状态).
    events 表 / portfolio_snapshot / tier_sold 等历史数据不动. 返回删除的行数."""
    conn = get_conn()
    cur = conn.execute("DELETE FROM position_meta WHERE token_id=?", (token_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted

def get_all_meta_token_ids():
    """返回 position_meta 表里所有 token_id 的 set, 用于 monitor 心跳扫描."""
    conn = get_conn()
    rows = conn.execute("SELECT token_id FROM position_meta").fetchall()
    conn.close()
    return {r[0] for r in rows}

def update_tp(token_id, new_tp):
    conn = get_conn()
    conn.execute("UPDATE position_meta SET new_tp=?, tp_updated_at=? WHERE token_id=?",
                 (new_tp, _utc_now_iso(), token_id))
    conn.commit(); conn.close()

def set_freeze_until(token_id, until_iso, stop_price=None):
    """v5: 设置冻结过期时间 + 记录触发冻结的 stop_price"""
    conn = get_conn()
    conn.execute("UPDATE position_meta SET freeze_until=?, freeze_stop_price=? WHERE token_id=?",
                 (until_iso, stop_price, token_id))
    conn.commit(); conn.close()

def clear_freeze(token_id):
    """v5: 清除冻结状态 (价回到 entry-10pp 以内 或 冻结期满)"""
    conn = get_conn()
    conn.execute("UPDATE position_meta SET freeze_until=NULL, freeze_stop_price=NULL WHERE token_id=?",
                 (token_id,))
    conn.commit(); conn.close()

def update_entry_price(token_id, entry_price):
    conn = get_conn()
    cur = conn.execute("UPDATE position_meta SET entry_price=? WHERE token_id=?",
                       (entry_price, token_id))
    rows = cur.rowcount
    conn.commit(); conn.close()
    return rows

def update_confidence(token_id, confidence):
    conn = get_conn()
    cur = conn.execute("UPDATE position_meta SET original_confidence=? WHERE token_id=?",
                       (confidence, token_id))
    rows = cur.rowcount
    conn.commit(); conn.close()
    return rows

def save_portfolio_snapshot(ts, total_value, total_cost, cash, total_pnl, assets_total):
    """每次心跳写一行 portfolio_snapshot. ts 为 unix epoch 秒."""
    conn = get_conn()
    conn.execute("""INSERT OR REPLACE INTO portfolio_snapshot
        (ts, total_value, total_cost, cash, total_pnl, assets_total)
        VALUES (?,?,?,?,?,?)""",
        (int(ts), total_value, total_cost, cash, total_pnl, assets_total))
    conn.commit(); conn.close()

def export_portfolio_snapshot(path):
    """导出整张 portfolio_snapshot 到 JSONL (一行一 snapshot). 给备份用."""
    import json, os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = get_conn()
    rows = conn.execute("SELECT ts, total_value, total_cost, cash, total_pnl, assets_total FROM portfolio_snapshot ORDER BY ts ASC").fetchall()
    conn.close()
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(dict(r), separators=(',', ':')) + "\n")
    return len(rows)

def import_portfolio_snapshot(path):
    """从 JSONL 导入回 portfolio_snapshot. INSERT OR IGNORE,主键 ts 去重."""
    import json
    conn = get_conn()
    count = 0
    skipped = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                cur = conn.execute("""INSERT OR IGNORE INTO portfolio_snapshot
                    (ts, total_value, total_cost, cash, total_pnl, assets_total)
                    VALUES (?,?,?,?,?,?)""",
                    (d['ts'], d['total_value'], d['total_cost'], d['cash'], d['total_pnl'], d['assets_total']))
                if cur.rowcount > 0:
                    count += 1
                else:
                    skipped += 1
            except Exception as e:
                print(f"skip line: {e}")
    conn.commit()
    conn.close()
    return count, skipped

def get_portfolio_history(since_ts, max_points=500):
    """读 since_ts 之后所有 snapshot. 超过 max_points 自动均匀降采样 (跨越完整时间窗口).
    旧 bug: rows[::step][:max_points] 只保留前 max_points 个点, 时间窗后段的数据被丢
    导致 1M / 1Y / ALL 视图后段一条横线 (chart 用直线连首尾 2 个点)."""
    conn = get_conn()
    rows = conn.execute("SELECT ts, assets_total, total_value, total_cost, total_pnl, cash FROM portfolio_snapshot WHERE ts >= ? ORDER BY ts ASC", (int(since_ts),)).fetchall()
    conn.close()
    rows = [dict(r) for r in rows]
    if len(rows) > max_points:
        n = len(rows)
        # 均匀采样 max_points 个 index, 再加最后一个保证终点不丢, 去重排序
        indices = sorted(set([int(i * n / max_points) for i in range(max_points)] + [n - 1]))
        rows = [rows[i] for i in indices]
    return rows

def get_all_position_meta():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM position_meta").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_daily_spend():
    """保留兼容性"""
    from datetime import date
    today = date.today().isoformat()
    conn = get_conn()
    row = conn.execute("""SELECT COUNT(*) as n FROM events 
        WHERE event_type IN ('buy','add') AND timestamp LIKE ?""", (today+"%",)).fetchone()
    conn.close()
    return 0.0

def get_current_phase():
    return "phase1"

def mark_reeval(token_id, action, new_tp=None):
    """
    标记重评状态.
    action: 'uplift' / 'skip' / 'close'
    new_tp: 仅 action=uplift 时填写新TP
    """
    status_map = {
        "uplift": "done_uplift",
        "skip": "done_skip",
        "close": "done_close",
    }
    status = status_map.get(action)
    if not status:
        return False
    conn = get_conn()
    if action == "uplift" and new_tp is not None:
        conn.execute("""UPDATE position_meta 
            SET reeval_status=?, reeval_at=?, reeval_new_tp=?, reeval_action=?, new_tp=?, tp_updated_at=?
            WHERE token_id=?""",
            (status, _utc_now_iso(), new_tp, action, new_tp, _utc_now_iso(), token_id))
    else:
        conn.execute("""UPDATE position_meta 
            SET reeval_status=?, reeval_at=?, reeval_action=?
            WHERE token_id=?""",
            (status, _utc_now_iso(), action, token_id))
    conn.commit(); conn.close()
    return True

def update_monitor_state(token_id, state):
    """每次心跳更新仓位的当前 monitor_state"""
    conn = get_conn()
    conn.execute("""UPDATE position_meta 
        SET monitor_state=?, monitor_state_at=? WHERE token_id=?""",
        (state, _utc_now_iso(), token_id))
    conn.commit(); conn.close()


def mark_executed_action(token_id, action_tag):
    """标记自动执行的动作 (avoid 重复执行 DISASTER/TIME_STOP)"""
    conn = get_conn()
    cur = conn.execute("SELECT executed_action FROM position_meta WHERE token_id=?", (token_id,))
    row = cur.fetchone()
    existing = (dict(row).get("executed_action") if row else "") or ""
    new_value = (existing + "|" + action_tag) if existing else action_tag
    conn.execute("UPDATE position_meta SET executed_action=? WHERE token_id=?",
                 (new_value, token_id))
    conn.commit(); conn.close()


def update_q_value(token_id, new_q):
    """
    用户在 dashboard 重评 q 后调用.
    根据新 edge 维护 last_q_update_with_negative_edge:
      - edge < -3pp 且字段为空 → 记录时间 (SOFT_NEGATIVE)
      - edge < -3pp 且字段非空 → 不变 (下次心跳会判 CONFIRMED_NEGATIVE)
      - edge >= -3pp → 清空字段
    """
    from modules.executor import Executor
    SOFT_NEG_PP = -3.0
    
    # 拉当前价
    cur_price = None
    try:
        for p in Executor.get().get_positions():
            if p.get("asset") == token_id:
                cur_price = p.get("cur_price") or 0
                break
    except Exception:
        pass
    
    conn = get_conn()
    now = _utc_now_iso()
    
    if cur_price is not None:
        edge_pp = (new_q - cur_price) * 100
        if edge_pp < SOFT_NEG_PP:
            # 检查字段是否已有
            row = conn.execute("SELECT last_q_update_with_negative_edge FROM position_meta WHERE token_id=?", (token_id,)).fetchone()
            already = (dict(row).get("last_q_update_with_negative_edge") if row else None)
            if not already:
                # 第一次 negative
                conn.execute("""UPDATE position_meta SET 
                    new_tp=?, tp_updated_at=?, last_reeval_at=?,
                    last_q_update_with_negative_edge=?
                    WHERE token_id=?""",
                    (new_q, now, now, now, token_id))
            else:
                # 已经有了, 保留时间戳, 升级到 CONFIRMED 由 monitor 心跳读取
                conn.execute("""UPDATE position_meta SET 
                    new_tp=?, tp_updated_at=?, last_reeval_at=?
                    WHERE token_id=?""",
                    (new_q, now, now, token_id))
        else:
            # edge 回升, 清空 negative 字段
            conn.execute("""UPDATE position_meta SET 
                new_tp=?, tp_updated_at=?, last_reeval_at=?,
                last_q_update_with_negative_edge=NULL
                WHERE token_id=?""",
                (new_q, now, now, token_id))
    else:
        # 拿不到当前价, 只更新 q 和重评时间
        conn.execute("""UPDATE position_meta SET 
            new_tp=?, tp_updated_at=?, last_reeval_at=?
            WHERE token_id=?""",
            (new_q, now, now, token_id))
    
    conn.commit(); conn.close()


def needs_reeval(token_id, hours=24):
    """判断是否距上次重评 >= hours 小时. v5.7 (P3): both sides aware UTC."""
    conn = get_conn()
    row = conn.execute("SELECT last_reeval_at, created_at FROM position_meta WHERE token_id=?",
                       (token_id,)).fetchone()
    conn.close()
    if not row: return False
    d = dict(row)
    ref = d.get("last_reeval_at") or d.get("created_at")
    if not ref: return False
    try:
        last_dt = _parse_iso_to_aware(ref)
        return (datetime.now(timezone.utc) - last_dt) >= timedelta(hours=hours)
    except Exception:
        return False


# === v5.7 (P7): closed_positions CRUD ===
# v5.10: 加 cluster_id + tag 字段 (resolution 4 字段在另外的 update_closed_resolution 路径写入)
def save_closed_position(token_id, market_slug, side, avg_entry, exit_price, size,
                         exit_reason, stop_loss_tier=None, claude_raw_estimate=None,
                         entry_at=None,
                         cluster_id=None, tag=None):
    """卖出成功后调用. 持久化已平仓详情用于 PnL / win-rate / calibration 分析.
    价格口径 (v5.10.2 修正): avg_entry / exit_price 都是"持有的那个 outcome token 自己的价格"
    (Polymarket positions API 的 avg_price / cur_price 就是这个口径, No 仓传的就是 No token 价).
    所以 PnL = (exit - avg) × size, **不分 side**.
    旧版对 No 仓做 (avg - exit) 翻转是 bug (把 Yes-price 口径和 held-token 口径搞混),
    历史数据已由 scripts/migrate_v5_10_2.py 修复.
    v5.10: cluster_id 和 tag 从 position_meta 传过来. resolution 4 字段稍后由 cron 写入."""
    pnl_usd = (exit_price - avg_entry) * size
    pnl_pct = (pnl_usd / (avg_entry * size) * 100) if avg_entry and size else 0.0
    hold_hrs = None
    if entry_at:
        try:
            entry_dt = _parse_iso_to_aware(entry_at)
            if entry_dt:
                hold_hrs = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
        except Exception:
            pass
    conn = get_conn()
    conn.execute("""INSERT INTO closed_positions
        (token_id, market_slug, side, avg_entry_price, exit_price, size,
         realized_pnl_usd, realized_pnl_pct, exit_reason, stop_loss_tier,
         claude_raw_estimate, entry_at, exit_at, hold_duration_hours,
         cluster_id, tag, is_resolved)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (token_id, market_slug, side, avg_entry, exit_price, size,
         pnl_usd, pnl_pct, exit_reason, stop_loss_tier,
         claude_raw_estimate, entry_at, _utc_now_iso(), hold_hrs,
         cluster_id, tag, 0))
    conn.commit(); conn.close()


# === v5.10: resolution 状态更新 (cron 调) ===
def update_closed_resolution(token_id, final_outcome, resolved_at_iso, side=None):
    """更新 closed_positions 的 resolution 4 字段. final_outcome 0 或 1.
    final_outcome 语义 = "持有 side 的最终概率" (check_resolution 已按持有 token 的 index 取价),
    所以 is_correct = (final_outcome >= 0.5), **不分 side**.
    旧版对 No 仓再翻一次是 bug (v5.10.2 修复, 历史数据已迁移). side 参数仅为兼容保留, 不再使用."""
    is_correct = 1 if float(final_outcome) >= 0.5 else 0
    conn = get_conn()
    conn.execute("""UPDATE closed_positions
        SET is_resolved=1, resolved_at=?, final_outcome=?, is_correct=?
        WHERE token_id=? AND is_resolved=0""",
        (resolved_at_iso, float(final_outcome), is_correct, token_id))
    rows = conn.total_changes
    conn.commit(); conn.close()
    return rows


def get_unresolved_closed_positions(limit=100):
    """v5.10: 待 resolution 检查的 closed_positions.
    v5.10.2: 按 token_id 去重 (同 token 多次卖出只查一次, update 时 WHERE token_id 覆盖所有行),
    limit 默认 50→100 (旧版 ORDER BY exit_at DESC LIMIT 50 会让最老的 token 永远轮不到检查)."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT token_id, MAX(market_slug) AS market_slug, MAX(side) AS side
           FROM closed_positions WHERE is_resolved=0
           GROUP BY token_id ORDER BY MAX(exit_at) DESC LIMIT ?""",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_closed_tag(token_id, tag):
    """v5.10: 回填 tag (老 closed_positions). token_id 是 PRIMARY KEY 无歧义."""
    conn = get_conn()
    conn.execute("UPDATE closed_positions SET tag=? WHERE token_id=?", (tag, token_id))
    conn.commit(); conn.close()


# === v5.10: analytics helpers for /history page ===
# 全部纯 SQL, 不依赖 polymarket api. 老仓位 NULL-tolerant.

def get_closed_positions_in_progress(limit=200):
    """已卖但 is_resolved=0 的, 按 exit_at desc. /history "进行中" 区块."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM closed_positions
           WHERE is_resolved=0
           ORDER BY exit_at DESC
           LIMIT ?""",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_closed_positions_resolved(limit=500):
    """is_resolved=1 的, 按 resolved_at desc. 已结算区块."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM closed_positions
           WHERE is_resolved=1
           ORDER BY resolved_at DESC, exit_at DESC
           LIMIT ?""",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_closed_positions():
    """v5.10: 全量 closed_positions, 按 exit_at desc. /history 科研全量 + CSV 导出用."""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM closed_positions ORDER BY exit_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pnl_summary():
    """全局 PnL summary:
    {
      total_count, win_count, loss_count, win_rate,        # 仅含 is_resolved=1 的
      total_realized_pnl_usd,                              # 所有已平仓的 PnL 加总 (不论 resolved)
      avg_hold_hours,
      top_winners: [{slug, pnl, side, ...}, ...],
      top_losers:  [{slug, pnl, side, ...}, ...],
      unresolved_count,
    }
    胜率口径: 只算 is_resolved=1 的 (其他无法判定 is_correct).
    PnL 口径: 所有 closed_positions 都算 (因为卖出价已经定了 PnL, 跟最终结算无关)."""
    conn = get_conn()
    # 已结算的统计
    resolved = conn.execute(
        """SELECT
            COUNT(*) AS n,
            SUM(CASE WHEN is_correct=1 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN is_correct=0 THEN 1 ELSE 0 END) AS losses
           FROM closed_positions WHERE is_resolved=1"""
    ).fetchone()
    # 全体 PnL + 平均持仓时长 + 赚钱笔数 (v5.10.3: 卖出价>成本即赚钱, 不依赖结算)
    overall = conn.execute(
        """SELECT
            COUNT(*) AS total,
            COALESCE(SUM(realized_pnl_usd), 0) AS total_pnl,
            COALESCE(AVG(hold_duration_hours), 0) AS avg_hours,
            SUM(CASE WHEN realized_pnl_usd > 0 THEN 1 ELSE 0 END) AS profit_count
           FROM closed_positions"""
    ).fetchone()
    # Top 5 winners / losers (按 realized_pnl_usd)
    winners = conn.execute(
        """SELECT token_id, market_slug, side, realized_pnl_usd, realized_pnl_pct, exit_at
           FROM closed_positions ORDER BY realized_pnl_usd DESC LIMIT 5"""
    ).fetchall()
    losers = conn.execute(
        """SELECT token_id, market_slug, side, realized_pnl_usd, realized_pnl_pct, exit_at
           FROM closed_positions ORDER BY realized_pnl_usd ASC LIMIT 5"""
    ).fetchall()
    unresolved = conn.execute(
        "SELECT COUNT(*) AS n FROM closed_positions WHERE is_resolved=0"
    ).fetchone()
    conn.close()
    n_resolved = resolved["n"] or 0
    wins = resolved["wins"] or 0
    losses = resolved["losses"] or 0
    total = overall["total"] or 0
    profit_count = overall["profit_count"] or 0
    return {
        "resolved_count": n_resolved,
        "win_count": wins,
        "loss_count": losses,
        "win_rate": (wins / n_resolved) if n_resolved else None,
        "total_closed_count": total,
        # v5.10.3: 赚钱率 = realized_pnl > 0 的比例, 全部笔数, 不等结算 — 这是操作层主指标
        "profit_count": profit_count,
        "profit_rate": (profit_count / total) if total else None,
        "total_realized_pnl_usd": float(overall["total_pnl"]),
        "avg_hold_hours": float(overall["avg_hours"]),
        "top_winners": [dict(r) for r in winners],
        "top_losers": [dict(r) for r in losers],
        "unresolved_count": unresolved["n"] or 0,
    }


def get_win_rate_by_dim(dim):
    """按维度聚合. dim ∈ ('tag', 'stop_loss_tier', 'cluster_id').

    v5.10.3 双口径 (用户反馈: 策略是"找到 pp 就卖", 赚没赚钱比最终结算重要):
    - 赚钱率 profit_rate: realized_pnl > 0 的比例, **全部已平仓笔数**, 不等结算 — 操作层主指标.
    - 方向对率 win_rate: is_correct=1 / resolved_count, 只算已结算 — 判断力校准指标.
    返回 [{dim_value, count, profit_count, profit_rate, total_pnl_usd,
           resolved_count, win_count, win_rate}, ...] 按 count desc."""
    allowed = {"tag", "stop_loss_tier", "cluster_id"}
    if dim not in allowed:
        raise ValueError(f"dim must be one of {allowed}, got {dim}")
    conn = get_conn()
    # NULLIF 把空字符串 tag/cluster 也归入 (未分类), 不再单独成桶.
    rows = conn.execute(
        f"""SELECT
            COALESCE(NULLIF({dim}, ''), '(未分类)') AS dim_value,
            COUNT(*) AS count,
            SUM(CASE WHEN realized_pnl_usd > 0 THEN 1 ELSE 0 END) AS profit_count,
            COALESCE(SUM(realized_pnl_usd), 0) AS total_pnl_usd,
            SUM(CASE WHEN is_resolved=1 THEN 1 ELSE 0 END) AS resolved_count,
            SUM(CASE WHEN is_resolved=1 AND is_correct=1 THEN 1 ELSE 0 END) AS win_count
           FROM closed_positions
           GROUP BY COALESCE(NULLIF({dim}, ''), '(未分类)')
           ORDER BY count DESC"""
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        n = r["count"]
        profits = r["profit_count"] or 0
        n_resolved = r["resolved_count"] or 0
        wins = r["win_count"] or 0
        out.append({
            "dim_value": r["dim_value"],
            "count": n,
            "profit_count": profits,
            "profit_rate": profits / n if n else None,
            "total_pnl_usd": float(r["total_pnl_usd"]),
            "resolved_count": n_resolved,
            "win_count": wins,
            "win_rate": wins / n_resolved if n_resolved else None,
        })
    return out


def get_calibration_report():
    """Claude 入场估计 (claude_raw_estimate) vs 实际结算 outcome 校准.
    桶: (0.5, 0.6], (0.6, 0.7], ..., (0.9, 1.0]
    每桶: count, actual_win_rate, calibration_gap (估计中位 - 实际胜率)
    仅含 is_resolved=1 的行.
    估计映射: claude_raw_estimate 是"我们这边 tp 价", 已经是我们买的 side 的目标概率.
              所以胜率应该 ≈ raw_estimate.
    """
    conn = get_conn()
    rows = conn.execute(
        """SELECT claude_raw_estimate, is_correct FROM closed_positions
           WHERE is_resolved=1 AND claude_raw_estimate IS NOT NULL"""
    ).fetchall()
    conn.close()
    buckets = [
        ((0.5, 0.6), "50-60%"),
        ((0.6, 0.7), "60-70%"),
        ((0.7, 0.8), "70-80%"),
        ((0.8, 0.9), "80-90%"),
        ((0.9, 1.0), "90-100%"),
    ]
    report = []
    for (lo, hi), label in buckets:
        sub = [r for r in rows if lo < r["claude_raw_estimate"] <= hi]
        n = len(sub)
        wins = sum(1 for r in sub if r["is_correct"] == 1)
        mid = (lo + hi) / 2
        actual = wins / n if n else None
        gap = (actual - mid) if actual is not None else None  # 负=Claude过自信(实际比估的低)
        report.append({
            "bucket": label,
            "estimated_mid": mid,
            "count": n,
            "win_count": wins,
            "actual_win_rate": actual,
            "calibration_gap": gap,  # actual - mid: 负=Claude 过自信, 正=Claude 保守
        })
    return report


def _exit_category(er):
    """v7.x (#8): 归类 exit_reason → 出场方式大类 (原始值太散/太长)."""
    er = er or ""
    if er.startswith("TAKE_PROFIT"): return "止盈"
    if er == "STOP_LOSS": return "止损"
    if er == "TIME_STOP": return "时间止损"
    if "reeval" in er or er.startswith("AUTO_REEVAL"): return "重评清仓"
    if "manual" in er: return "手动清仓"
    if er.startswith("BACKFILL") or er.startswith("REBUILT"): return "回填/重建(历史成交)"
    return "其它"


def get_history_extras():
    """v7.x (#8): 统计分析新角度 — 时间趋势 / 卖飞 / 盈亏分布 / 按出场方式. 一次读全表算。"""
    from collections import defaultdict
    conn = get_conn()
    rows = conn.execute(
        "SELECT realized_pnl_usd, exit_at, exit_price, size, final_outcome, is_resolved, exit_reason "
        "FROM closed_positions").fetchall()
    conn.close()
    # 1. 时间趋势 (按月, 带累计)
    mon = defaultdict(lambda: {"pnl": 0.0, "n": 0})
    for r in rows:
        m = (r["exit_at"] or "?")[:7]
        mon[m]["pnl"] += (r["realized_pnl_usd"] or 0)
        mon[m]["n"] += 1
    cum = 0.0; monthly = []
    for m in sorted(k for k in mon if k != "?"):
        cum += mon[m]["pnl"]
        monthly.append({"month": m, "pnl": round(mon[m]["pnl"], 2), "count": mon[m]["n"], "cum_pnl": round(cum, 2)})
    # 2. 卖飞分析 (仅已结算): missed=(final_outcome-exit_price)*size; 正=卖早留了钱, 负=卖早避了亏
    left = saved = 0.0; early_n = good_n = 0
    for r in rows:
        if r["is_resolved"] == 1 and r["final_outcome"] is not None and r["exit_price"] is not None:
            miss = (r["final_outcome"] - r["exit_price"]) * (r["size"] or 0)
            if miss > 0.01: left += miss; early_n += 1
            elif miss < -0.01: saved += -miss; good_n += 1
    sold_early = {"left_on_table_usd": round(left, 2), "saved_usd": round(saved, 2),
                  "early_count": early_n, "good_count": good_n, "net_usd": round(left - saved, 2)}
    # 3. 盈亏分布
    dist = [{"bucket": "大亏 (<-$3)", "count": 0}, {"bucket": "小亏 (-$3~0)", "count": 0},
            {"bucket": "小赚 ($0~3)", "count": 0}, {"bucket": "大赚 (>$3)", "count": 0}]
    for r in rows:
        p = r["realized_pnl_usd"] or 0
        dist[0 if p <= -3 else 1 if p < 0 else 2 if p < 3 else 3]["count"] += 1
    # 4. 按出场方式 (归类)
    be = defaultdict(lambda: {"count": 0, "pnl": 0.0, "profit_count": 0})
    for r in rows:
        cat = _exit_category(r["exit_reason"]); p = r["realized_pnl_usd"] or 0
        be[cat]["count"] += 1; be[cat]["pnl"] += p; be[cat]["profit_count"] += 1 if p > 0 else 0
    by_exit = [{"reason": k, "count": v["count"], "pnl": round(v["pnl"], 2),
                "profit_count": v["profit_count"], "profit_rate": (v["profit_count"] / v["count"]) if v["count"] else None}
               for k, v in sorted(be.items(), key=lambda x: -x[1]["count"])]
    return {"monthly": monthly, "sold_early": sold_early, "pnl_dist": dist, "by_exit": by_exit}


# === v5.7 (P11): persistent login_attempts CRUD ===
def get_login_attempt(ip):
    conn = get_conn()
    row = conn.execute("SELECT fail_count, window_start_ts FROM login_attempts WHERE ip=?", (ip,)).fetchone()
    conn.close()
    return dict(row) if row else None

def upsert_login_attempt(ip, fail_count, window_start_ts):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO login_attempts (ip, fail_count, window_start_ts) VALUES (?,?,?)",
                 (ip, int(fail_count), int(window_start_ts)))
    conn.commit(); conn.close()

def delete_login_attempt(ip):
    conn = get_conn()
    conn.execute("DELETE FROM login_attempts WHERE ip=?", (ip,))
    conn.commit(); conn.close()


# ===== v5.13: auto_reeval_suggestions (大跌自动重评) =====
def save_auto_reeval_pending(token_id, pos, meta, loss_pct, cur_price, avg_price, status="analyzing"):
    """触发时立即插一行占位 (防重复触发), 返回 id。status: analyzing(自动联网中) / manual(在线手动)。"""
    pos = pos or {}; meta = meta or {}
    title = pos.get("title", "")
    slug = meta.get("market_slug") or ""
    side = pos.get("outcome") or meta.get("side") or ""
    orig_q = meta.get("new_tp") or meta.get("tp")  # v6.0.2: 触发时该仓的 q (原始), 供"原 q→新 q"显示
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO auto_reeval_suggestions
           (token_id, slug, title, side, avg_price, cur_price, loss_pct, trigger_reason, orig_q, status, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (token_id, slug, title, side, avg_price, cur_price, loss_pct,
         (f"亏 -{loss_pct*100:.0f}%" if loss_pct >= 0 else f"自最高回撤(仍浮盈) {abs(loss_pct)*100:.0f}%"),
         orig_q, status, _utc_now_iso()))
    conn.commit(); rid = cur.lastrowid; conn.close()
    return rid


def update_auto_reeval_decision(sug_id, d):
    """API 调研完成 → 写入决策, status='pending' 等用户确认。"""
    conn = get_conn()
    conn.execute(
        """UPDATE auto_reeval_suggestions SET
           action=?, new_q=?, confidence=?, thesis_broken=?, headline_event=?,
           reason=?, sources=?, raw_text=?, provider=?, pre_dump_center=?, price_curve=?,
           compare_json=?,
           status='pending', decided_at=?
           WHERE id=?""",
        (d.get("action"), d.get("new_q"), d.get("confidence"),
         1 if d.get("thesis_broken") else 0, d.get("headline_event"),
         d.get("reason"), json.dumps(d.get("sources") or [], ensure_ascii=False),
         (d.get("raw_text") or "")[:2000], d.get("_provider"),
         d.get("_pre_dump_center"), d.get("_price_curve_json"),
         d.get("_compare_json"),
         _utc_now_iso(), sug_id))
    conn.commit(); conn.close()


def update_auto_reeval_error(sug_id, err):
    conn = get_conn()
    conn.execute("UPDATE auto_reeval_suggestions SET status='error', error=?, decided_at=? WHERE id=?",
                 ((err or "")[:1000], _utc_now_iso(), sug_id))
    conn.commit(); conn.close()


def recent_auto_reeval_exists(token_id, hours):
    """冷却: 该 token 在最近 hours 小时内是否已有过任何重评记录 (防重复触发)。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT created_at FROM auto_reeval_suggestions WHERE token_id=? ORDER BY id DESC LIMIT 1",
        (token_id,)).fetchone()
    conn.close()
    if not row:
        return False
    dt = _parse_iso_to_aware(row["created_at"])
    if not dt:
        return False
    return (datetime.now(timezone.utc) - dt) < timedelta(hours=hours)


def last_auto_reeval_loss(token_id):
    """v6.0.1 (#1): 该仓最近一条重评记录的 loss_pct (任何状态; 无则 None)。
    用于'又多亏 >5pp 才再评'的判断 (配合 6h 冷却: 两者同时满足才自动再评)。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT loss_pct FROM auto_reeval_suggestions WHERE token_id=? ORDER BY id DESC LIMIT 1",
        (token_id,)).fetchone()
    conn.close()
    return row["loss_pct"] if (row and row["loss_pct"] is not None) else None


def has_active_auto_reeval(token_id):
    """v5.13.1 闩锁: 该 token 是否已有'未清空'(status != cleared)的重评记录。
    有 → 不再自动触发, 直到用户手动清空才重新武装。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM auto_reeval_suggestions WHERE token_id=? AND status != 'cleared' LIMIT 1",
        (token_id,)).fetchone()
    conn.close()
    return bool(row)


def clear_all_auto_reeval():
    """v5.13.1: 一键清空 — 把所有'未清空'记录置 cleared (= 全部重新武装)。返回清掉几条。"""
    conn = get_conn()
    cur = conn.execute(
        "UPDATE auto_reeval_suggestions SET status='cleared', resolved_at=? WHERE status != 'cleared'",
        (_utc_now_iso(),))
    conn.commit(); n = cur.rowcount; conn.close()
    return n


def expire_stale_auto_reeval(analyzing_min=30, executing_min=10):
    """v6.0.1 (#2/#6): 复位卡死的重评行, 否则会永久闩住该仓的 %止损/再评。
    - analyzing 超 analyzing_min (API timeout 才10min, 超了多半进程重启/线程中断) → error, 由 6h 冷却接管。
    - executing 超 executing_min (确认/执行中途进程死) → 退回 pending 可重试 (重试会重拉实时 size, 不会双卖)。
    返回复位行数。"""
    conn = get_conn()
    now = datetime.now(timezone.utc)
    cut_a = now - timedelta(minutes=analyzing_min)
    cut_e = now - timedelta(minutes=executing_min)
    n = 0
    for r in conn.execute("SELECT id, created_at FROM auto_reeval_suggestions WHERE status='analyzing'").fetchall():
        dt = _parse_iso_to_aware(r["created_at"])
        if dt and dt < cut_a:
            conn.execute("UPDATE auto_reeval_suggestions SET status='error', "
                         "error='stale analyzing: 进程重启/线程中断, 自动复位', resolved_at=? WHERE id=?",
                         (_utc_now_iso(), r["id"])); n += 1
    for r in conn.execute("SELECT id, resolved_at, created_at FROM auto_reeval_suggestions WHERE status='executing'").fetchall():
        dt = _parse_iso_to_aware(r["resolved_at"] or r["created_at"])
        if dt and dt < cut_e:
            conn.execute("UPDATE auto_reeval_suggestions SET status='pending', "
                         "error='stale executing: 中途中断, 退回待确认' WHERE id=?", (r["id"],)); n += 1
    if n:
        conn.commit()
    conn.close()
    return n


def autoclear_old_auto_reeval(hours=48):
    """v7.x (#3): 自动重评建议放超过 hours 小时 (默认 48=2天) 自动清空 (status='cleared', 数据从不删)。
    清空=从活跃清单移除 + 重新武装该仓 (清空不卖不动钱, 冷却由 6h 逻辑另管)。返回清掉的行数。
    用 _parse_iso_to_aware 逐行判 (兼容老的 naive created_at)。"""
    conn = get_conn()
    cut = datetime.now(timezone.utc) - timedelta(hours=hours)
    n = 0
    for r in conn.execute("SELECT id, created_at FROM auto_reeval_suggestions WHERE status != 'cleared'").fetchall():
        dt = _parse_iso_to_aware(r["created_at"])
        if dt and dt < cut:
            conn.execute("UPDATE auto_reeval_suggestions SET status='cleared', resolved_at=? WHERE id=?",
                         (_utc_now_iso(), r["id"])); n += 1
    if n:
        conn.commit()
    conn.close()
    return n


def get_pending_auto_reeval():
    """dashboard 拉取: 所有'未清空'记录 (analyzing/pending/executed/dismissed/error)。
    v5.13.1 闩锁语义: 只要 status != 'cleared' 就显示且算闩住, 用户手动清空才解除。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM auto_reeval_suggestions WHERE status != 'cleared' ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_auto_reeval_history(limit=60):
    """v6.0.6: 已清空(归档)的重评记录, 最新在前。给「重评历史」折叠栏用。
    数据从不删 —— 清空只是 status='cleared', 所有决策/reason/sources/raw 全保留。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM auto_reeval_suggestions WHERE status='cleared' ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_auto_reeval_compare(limit=100):
    """v7.x:「API重评」对比页数据 — 所有出过决策的重评记录 (任何状态, 最新在前)。
    每行含基本信息 + 权威 provider + 主列决策 + compare_json (两模型完整输出, 老记录可能 NULL)。
    只这一个页面读 compare_json; 主页/面板/重评建议区一律只读主列 (= 权威, 默认 Claude)。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM auto_reeval_suggestions WHERE compare_json IS NOT NULL ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def auto_reeval_latest_per_token():
    """v6.0.6: 每个 token 最新一条重评记录 (任何状态), 给冷却倒计时定位'代表行'用。
    返回 {token_id: {id, created_at, status}}。"""
    conn = get_conn()
    rows = conn.execute(
        """SELECT t.token_id, t.id, t.created_at, t.status
           FROM auto_reeval_suggestions t
           JOIN (SELECT token_id, MAX(id) mid FROM auto_reeval_suggestions GROUP BY token_id) m
             ON t.id = m.mid"""
    ).fetchall()
    conn.close()
    return {r["token_id"]: {"id": r["id"], "created_at": r["created_at"], "status": r["status"]} for r in rows}


def get_auto_reeval(sug_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM auto_reeval_suggestions WHERE id=?", (sug_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_auto_reeval_status(sug_id, status):
    conn = get_conn()
    conn.execute("UPDATE auto_reeval_suggestions SET status=?, resolved_at=? WHERE id=?",
                 (status, _utc_now_iso(), sug_id))
    conn.commit(); conn.close()


def claim_auto_reeval(sug_id, expect_status="pending"):
    """v6.0.1 (#6): 原子抢占一条建议去执行 (compare-and-set): status expect_status→'executing'。
    返回 True=抢到(可执行), False=别人已抢/状态已变(跳过)。防主页+面板/双击 并发确认 → 同一建议下两笔卖单。
    stamp resolved_at 作为'抢占时刻', 供 expire_stale 回收死在 executing 的行。"""
    conn = get_conn()
    cur = conn.execute(
        "UPDATE auto_reeval_suggestions SET status='executing', resolved_at=? WHERE id=? AND status=?",
        (_utc_now_iso(), sug_id, expect_status))
    conn.commit(); ok = (cur.rowcount == 1); conn.close()
    return ok


def apply_auto_reeval_q(token_id, new_q):
    """update_q 确认: 把新 q 写进 position_meta.new_tp (跟重评改 q 同口径)。"""
    conn = get_conn()
    conn.execute(
        "UPDATE position_meta SET new_tp=?, tp_updated_at=?, last_reeval_at=? WHERE token_id=?",
        (new_q, _utc_now_iso(), _utc_now_iso(), token_id))
    conn.commit(); conn.close()


# ===== v5.14: app_state + 在线/离线 presence =====
PRESENCE_STALE_MIN = 12  # v7.x(#20): 标记在线但超这么久没真活动 → 视为离线 (硬兜底, 配合 10min 弹窗; 原 35min)

def set_app_state(key, value):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO app_state(key, value, updated_at) VALUES (?,?,?)",
                 (key, str(value), _utc_now_iso()))
    conn.commit(); conn.close()

def get_app_state(key, default=None):
    conn = get_conn()
    row = conn.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def set_api_paused(paused):
    """v6.0.3: 紧急暂停/恢复 所有自动重评 API (auto 触发 + 手动 🤖 + 离线执行 全停)。"""
    set_app_state("api_paused", "1" if paused else "0")

def get_api_paused():
    """v6.0.3: 是否处于'API 紧急暂停'。默认 False (不暂停)。"""
    return get_app_state("api_paused", "0") == "1"

def set_reeval_watch_loss(token_id, loss):
    """v6.0.4: 记/清 该仓"冷却(6h)后再评基线亏损"。loss=None 清除 (冷却中 / 触发后重置)。"""
    conn = get_conn()
    conn.execute("UPDATE position_meta SET reeval_watch_loss=? WHERE token_id=?", (loss, token_id))
    conn.commit(); conn.close()

def get_reeval_watch_loss(token_id):
    """v6.0.4: 取该仓再评基线亏损 (None=未设, 即还没到6h或刚触发过)。"""
    conn = get_conn()
    row = conn.execute("SELECT reeval_watch_loss FROM position_meta WHERE token_id=?", (token_id,)).fetchone()
    conn.close()
    return row["reeval_watch_loss"] if (row and row["reeval_watch_loss"] is not None) else None

def update_peak_price(token_id, peak):
    """v7.0: 记该仓持有期最高价 (收敛型移动止损用)。monitor 每心跳 max(old, cur) 更新。"""
    conn = get_conn()
    conn.execute("UPDATE position_meta SET peak_price=? WHERE token_id=?", (peak, token_id))
    conn.commit(); conn.close()

def set_presence(online, manual=False):
    """manual=True 表示用户手动点的(在线/离线按钮)。
    手动下线 → 置 presence_manual_off=1, 抑制"活动自动上线"(人要走了, 别被收尾的滚动/点击又拉回在线)。
    上线(任何方式) / 系统自动下线(空闲超时, manual=False) → 清抑制, 让回来的活动能自动上线。"""
    set_app_state("presence", "online" if online else "offline")
    set_app_state("presence_at", _utc_now_iso())
    set_app_state("presence_manual_off", "1" if (not online and manual) else "0")

def clear_presence_manual_off():
    """页面加载(新会话)调一次: 解除"手动下线"抑制, 让本次会话的活动可自动上线。不改在线/离线本身。"""
    set_app_state("presence_manual_off", "0")

def presence_ping():
    """在线时刷新时间戳(保持 fresh); 离线时不动。"""
    if get_app_state("presence") == "online":
        set_app_state("presence_at", _utc_now_iso())

def get_presence():
    """返回 {online, at, fresh, effective_online, idle_sec}。effective_online = 标记在线 且 未过期。
    idle_sec = 距上次活动(presence_at)秒数; 前端据此决定要不要弹"还在吗"(主页/总控台同源, 任一页活动或确认都刷新它 → 双页同步)。
    monitor 用 effective_online 判断要不要暂停自动 API。"""
    raw = get_app_state("presence", "offline")
    at = get_app_state("presence_at")
    online = (raw == "online")
    manual_off = get_app_state("presence_manual_off", "0") == "1"   # v5.16: 用户手动下线 → 抑制活动自动上线
    fresh = False
    idle_sec = None
    if at:
        dt = _parse_iso_to_aware(at)
        if dt:
            idle_sec = (datetime.now(timezone.utc) - dt).total_seconds()
            fresh = online and idle_sec < PRESENCE_STALE_MIN * 60
    return {"online": online, "at": at, "fresh": fresh,
            "effective_online": bool(online and fresh), "idle_sec": idle_sec, "manual_off": manual_off}


# ===== v5.14: cancel_autostop (取消某仓自动止损) + 重评进行中查询 =====
def set_autostop_disabled(token_id, disabled=True):
    """取消/恢复某仓的自动止损 (AI+用户 或 离线自动 决定)。取消后 monitor 只留 $0.05 地板兜底。返回更新行数。"""
    conn = get_conn()
    cur = conn.execute("UPDATE position_meta SET autostop_disabled=? WHERE token_id=?",
                 (1 if disabled else 0, token_id))
    rows = cur.rowcount
    conn.commit(); conn.close()
    return rows

def has_inflight_auto_reeval(token_id):
    """该仓是否有'进行中/待处理'的重评 (analyzing/pending/manual) → monitor 暂缓 %止损, 等结果。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM auto_reeval_suggestions WHERE token_id=? AND status IN ('analyzing','pending','manual') LIMIT 1",
        (token_id,)).fetchone()
    conn.close()
    return bool(row)


# ===== v7.1: 模拟盘/测试仓 (paper trading) — 不真下单, 实时盯盘跑同一套算法 =====

def add_paper_position(token_id, market_slug, title, side, entry_price, size_usd, q=None,
                       confidence=None, stop_loss_tier=None, cluster_id=None, tag=None,
                       end_date=None, notes=None):
    """录入一条测试仓。shares = size_usd / entry_price (持有 token 口径)。peak_price 初始化为入场价。"""
    shares = (float(size_usd) / float(entry_price)) if (entry_price and float(entry_price) > 0) else 0
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO paper_positions
           (token_id, market_slug, title, side, entry_price, size_usd, shares, q, confidence,
            stop_loss_tier, cluster_id, tag, end_date, notes, created_at, cur_price, peak_price,
            monitor_state, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open')""",
        (token_id, market_slug, title, side, entry_price, size_usd, shares, q, confidence,
         stop_loss_tier, cluster_id, tag, end_date, notes, _utc_now_iso(),
         entry_price, entry_price, "PENDING"))
    conn.commit(); rid = cur.lastrowid; conn.close()
    return rid


def get_paper_positions(status="open"):
    """取测试仓。status=None → 全部; 否则按 status 过滤 (open/cleared/resolved)。"""
    conn = get_conn()
    if status:
        rows = conn.execute("SELECT * FROM paper_positions WHERE status=? ORDER BY id DESC", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM paper_positions ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_open_paper_positions():
    """monitor 心跳用: 所有 open (未清空/未结算) 的测试仓。"""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM paper_positions WHERE status='open'").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_paper_position(pid):
    """清空一条 (status='cleared', 不删数据)。返回是否清到。"""
    conn = get_conn()
    cur = conn.execute("UPDATE paper_positions SET status='cleared' WHERE id=? AND status!='cleared'", (pid,))
    conn.commit(); n = cur.rowcount; conn.close()
    return n > 0


def clear_all_paper_positions():
    """一键清空所有 open 测试仓。返回清掉几条。"""
    conn = get_conn()
    cur = conn.execute("UPDATE paper_positions SET status='cleared' WHERE status='open'")
    conn.commit(); n = cur.rowcount; conn.close()
    return n


def update_paper_tracking(pid, cur_price, peak_price, monitor_state):
    """心跳更新测试仓的实时价/最高价/算法状态。"""
    conn = get_conn()
    conn.execute(
        "UPDATE paper_positions SET cur_price=?, peak_price=?, monitor_state=?, last_checked_at=? WHERE id=?",
        (cur_price, peak_price, monitor_state, _utc_now_iso(), pid))
    conn.commit(); conn.close()


def set_paper_would_sell(pid, price, reason, pnl_usd):
    """记录'算法本会在此卖出'的首次快照 (只记一次, 之后继续盯盘到结算)。"""
    conn = get_conn()
    conn.execute(
        """UPDATE paper_positions SET would_sell_at_ts=?, would_sell_price=?, would_sell_reason=?,
           would_sell_pnl_usd=? WHERE id=? AND would_sell_at_ts IS NULL""",
        (_utc_now_iso(), price, reason, pnl_usd, pid))
    conn.commit(); conn.close()


def resolve_paper_position(pid, final_outcome):
    """市场结算 → 标记测试仓 resolved (持有 side 最终兑现概率: 1=赢, 0=输)。"""
    conn = get_conn()
    conn.execute(
        "UPDATE paper_positions SET is_resolved=1, final_outcome=?, resolved_at=?, status='resolved' WHERE id=?",
        (final_outcome, _utc_now_iso(), pid))
    conn.commit(); conn.close()


def update_paper_q(pid, q):
    """v7.1 阶段2: 手动重评后, 用户把读到的新 q 应用到测试仓 (下一拍算法用新 q 重算 edge)。"""
    conn = get_conn()
    cur = conn.execute("UPDATE paper_positions SET q=? WHERE id=? AND status='open'", (q, pid))
    conn.commit(); n = cur.rowcount; conn.close()
    return n > 0

