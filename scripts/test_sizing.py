"""
v5.9: position_size_usd() golden case tests.

Hand-computed expected values for each case (precise, no tolerance).
Run: PYTHONPATH=. python3 scripts/test_sizing.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.sizing import position_size_usd


def case(name, expected_size, expected_reason_contains, **inputs):
    """Run one golden case, assert == expected. Returns (pass: bool, msg: str)."""
    got_size, got_reason = position_size_usd(**inputs)
    if got_size != expected_size:
        return False, f"{name}: expected ${expected_size:.2f} got ${got_size:.2f} (reason: {got_reason})"
    if expected_reason_contains and expected_reason_contains not in got_reason:
        return False, f"{name}: reason missing '{expected_reason_contains}'. Got: {got_reason}"
    return True, f"{name}: OK (${got_size:.2f}, {got_reason})"


def main():
    bankroll = 87.0
    cluster_cap = round(bankroll * 0.20, 2)   # = 17.40

    cases = [
        # ============ A: 第 1 个 iran 仓 (cluster 空) ============
        # edge=0.14, kelly_f=0.4667, raw=87*0.4667*0.25=10.15
        # days_f=sqrt(21/29)=0.8513, ls=1.0, size=10.15*0.8513=8.64
        # no clip (cluster_room=17.40, dd_cap=42.86)
        # final=8.64
        dict(name="A. iran 仓 1 (cluster 空)",
             expected_size=8.64,
             expected_reason_contains="edge=14.0pp",
             q=0.84, p=0.70, confidence="high", stop_loss_tier="event_driven",
             days_to_resolution=29, bankroll_usd=bankroll,
             cluster_current_exposure_usd=0.0,
             cluster_cap_usd=cluster_cap,
             exposed_dd_usd=0.0),

        # ============ B: 第 2 个 iran 仓 (cluster 已 $8.64) ============
        # edge=0.39, kelly_f=0.8298, raw=87*0.8298*0.25=18.05
        # days_f=sqrt(21/14)=1.22 → clip 1.0; ls=1.0; size=18.05
        # cluster_room=17.40-8.64=8.76, dd_cap=(30-6.05)/0.70=34.22
        # clip to 8.76 → final=8.76
        dict(name="B. iran 仓 2 (cluster 已 $8.64)",
             expected_size=8.76,
             expected_reason_contains="clip",
             q=0.92, p=0.53, confidence="medium", stop_loss_tier="event_driven",
             days_to_resolution=14, bankroll_usd=bankroll,
             cluster_current_exposure_usd=8.64,
             cluster_cap_usd=cluster_cap,
             exposed_dd_usd=8.64*0.70),

        # ============ C: cluster full (用户当前实际状态) ============
        # cluster_exp=17.40 → cluster_room=0 < MIN_SINGLE_POS=1.0 → return $0
        dict(name="C. cluster full",
             expected_size=0.0,
             expected_reason_contains="cluster full",
             q=0.80, p=0.51, confidence="medium", stop_loss_tier="event_driven",
             days_to_resolution=29, bankroll_usd=bankroll,
             cluster_current_exposure_usd=17.40,
             cluster_cap_usd=cluster_cap,
             exposed_dd_usd=12.19),

        # ============ D: Mazzei (q/p inputs 数据 sanity check 触发) ============
        # 如果用户填 q=0.87, p=0.131 (这是数据错误 — 73.9pp edge 不真实),
        # 公式没法判断"数据错", 会给 $8.36 推荐. 这是 user-error 边界.
        # edge=0.739, kelly_f=0.8504, raw=87*0.8504*0.25=18.50
        # days_f=sqrt(21/90)=0.4830, ls=0.5+0.5*(0.131/0.15)=0.9367
        # size=18.50*0.4830*0.9367=8.37
        # cluster_room=17.40, dd_cap=(30-12.19)/0.35=50.89
        # clip 8.37 → final=8.37
        dict(name="D. Mazzei (74pp edge 数据可疑, 公式照常算)",
             expected_size=8.37,
             expected_reason_contains="edge=73.9pp",
             q=0.87, p=0.131, confidence="medium", stop_loss_tier="hybrid",
             days_to_resolution=90, bankroll_usd=bankroll,
             cluster_current_exposure_usd=0.0,
             cluster_cap_usd=cluster_cap,
             exposed_dd_usd=12.19),

        # ============ E: 负 edge (q < p) ============
        dict(name="E. q<p (no edge)",
             expected_size=0.0,
             expected_reason_contains="no edge",
             q=0.50, p=0.70, confidence="high", stop_loss_tier="event_driven",
             days_to_resolution=29, bankroll_usd=bankroll,
             cluster_current_exposure_usd=0.0,
             cluster_cap_usd=cluster_cap,
             exposed_dd_usd=0.0),

        # ============ F: 零 edge (q == p) ============
        dict(name="F. q==p (no edge)",
             expected_size=0.0,
             expected_reason_contains="no edge",
             q=0.70, p=0.70, confidence="high", stop_loss_tier="event_driven",
             days_to_resolution=29, bankroll_usd=bankroll,
             cluster_current_exposure_usd=0.0,
             cluster_cap_usd=cluster_cap,
             exposed_dd_usd=0.0),

        # ============ G: longshot 减仓 (低 p 触发 longshot_mult < 1) ============
        # edge=0.06, kelly_f=0.06/0.92=0.0652, raw=87*0.0652*0.25=1.418
        # days_f=sqrt(21/30)=0.8367, ls=0.5+0.5*(0.08/0.15)=0.7667
        # size=1.418*0.8367*0.7667=0.909 < MIN $1 → return $0 with "cap below"
        dict(name="G. 低概率 longshot 触发 $0 (size 不达 $1 floor)",
             expected_size=0.0,
             expected_reason_contains="cap below",
             q=0.14, p=0.08, confidence="medium", stop_loss_tier="convergent",
             days_to_resolution=30, bankroll_usd=bankroll,
             cluster_current_exposure_usd=0.0,
             cluster_cap_usd=cluster_cap,
             exposed_dd_usd=0.0),

        # ============ H: DD budget exhausted ============
        # exposed_dd=30 → remaining=0 → dd_cap=0 < MIN_SINGLE_POS → return $0
        dict(name="H. DD budget exhausted",
             expected_size=0.0,
             expected_reason_contains="DD budget exhausted",
             q=0.80, p=0.55, confidence="high", stop_loss_tier="hybrid",
             days_to_resolution=21, bankroll_usd=bankroll,
             cluster_current_exposure_usd=0.0,
             cluster_cap_usd=cluster_cap,
             exposed_dd_usd=30.0),

        # ============ I: hard ceiling $15 ============
        # 大 bankroll + 大 edge → kelly raw > $15
        # bankroll=1000, edge=0.30, kelly_f=0.30/0.50=0.60, raw=1000*0.60*0.25=150
        # days_f=1.0, ls=1.0, size=150 → clip(cluster_room=200, dd_cap=85.7) → 85.7 →
        # final = min(15, max(1, 85.7)) = 15
        dict(name="I. hard ceiling $15",
             expected_size=15.0,
             expected_reason_contains="bound→$15.00",
             q=0.80, p=0.50, confidence="high", stop_loss_tier="hybrid",
             days_to_resolution=21, bankroll_usd=1000.0,
             cluster_current_exposure_usd=0.0,
             cluster_cap_usd=200.0,
             exposed_dd_usd=0.0),
    ]

    n_pass = n_fail = 0
    print("=" * 70)
    print("v5.9 position_size_usd() golden case tests")
    print("=" * 70)
    for c in cases:
        name = c.pop("name")
        expected_size = c.pop("expected_size")
        expected_reason = c.pop("expected_reason_contains")
        ok, msg = case(name, expected_size, expected_reason, **c)
        marker = "✓" if ok else "✗"
        print(f"  {marker} {msg}")
        if ok: n_pass += 1
        else: n_fail += 1
    print("=" * 70)
    print(f"  {n_pass} pass, {n_fail} fail")
    print("=" * 70)
    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
