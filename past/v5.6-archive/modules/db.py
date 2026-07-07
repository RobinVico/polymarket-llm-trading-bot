import sqlite3
from datetime import datetime

DB_PATH = "v4.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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
            stop_loss_tier TEXT
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
    ]:
        try:
            conn.execute(f"ALTER TABLE position_meta ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError:
            pass
    conn.commit(); conn.close()

def log_event(event_type, market_slug="", detail=""):
    conn = get_conn()
    conn.execute("INSERT INTO events (timestamp, event_type, market_slug, detail) VALUES (?,?,?,?)",
                 (datetime.now().isoformat(), event_type, market_slug, detail))
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
                 (token_id, tier_name, datetime.now().isoformat()))
    conn.commit(); conn.close()

def save_position_meta(token_id, market_slug, side, entry_price, tp, end_date, initial_size, notes="", entry_reason="", claude_raw_estimate=None, original_confidence=None, stop_loss_tier=None):
    """下单后手动记录元数据，供monitor使用.
    stop_loss_tier: convergent (-20%) / hybrid (-35%) / event_driven (only $0.05 floor) / None (legacy -25%)"""
    target_gap = tp - entry_price if side.upper() in ("YES","Yes") else entry_price - tp
    conn = get_conn()
    conn.execute("""INSERT OR REPLACE INTO position_meta
        (token_id, market_slug, side, entry_price, tp, target_gap, end_date, initial_size, created_at, notes, entry_reason, claude_raw_estimate, original_confidence, stop_loss_tier, reeval_status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending')""",
        (token_id, market_slug, side, entry_price, tp, target_gap, end_date, initial_size, datetime.now().isoformat(), notes, entry_reason, claude_raw_estimate, original_confidence, stop_loss_tier))
    conn.commit(); conn.close()

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
                 (new_tp, datetime.now().isoformat(), token_id))
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
    rows = conn.execute("SELECT ts, assets_total, total_value, total_pnl, cash FROM portfolio_snapshot WHERE ts >= ? ORDER BY ts ASC", (int(since_ts),)).fetchall()
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
            (status, datetime.now().isoformat(), new_tp, action, new_tp, datetime.now().isoformat(), token_id))
    else:
        conn.execute("""UPDATE position_meta 
            SET reeval_status=?, reeval_at=?, reeval_action=?
            WHERE token_id=?""",
            (status, datetime.now().isoformat(), action, token_id))
    conn.commit(); conn.close()
    return True

def update_monitor_state(token_id, state):
    """每次心跳更新仓位的当前 monitor_state"""
    conn = get_conn()
    conn.execute("""UPDATE position_meta 
        SET monitor_state=?, monitor_state_at=? WHERE token_id=?""",
        (state, datetime.now().isoformat(), token_id))
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
    now = datetime.now().isoformat()
    
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
    """判断是否距上次重评 >= hours 小时"""
    from datetime import timedelta
    conn = get_conn()
    row = conn.execute("SELECT last_reeval_at, created_at FROM position_meta WHERE token_id=?",
                       (token_id,)).fetchone()
    conn.close()
    if not row: return False
    d = dict(row)
    ref = d.get("last_reeval_at") or d.get("created_at")
    if not ref: return False
    try:
        last_dt = datetime.fromisoformat(ref)
        return (datetime.now() - last_dt) >= timedelta(hours=hours)
    except Exception:
        return False

