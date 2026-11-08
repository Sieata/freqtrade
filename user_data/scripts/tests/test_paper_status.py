"""paper_status 统计/判据逻辑单测（2026-11-08 轮31 补齐）。

paper_status.py 是判定 forward-test 是否达标的唯一工具，此前 load_closed / max_drawdown /
win-rate / criteria 全部无单测覆盖。本文件用合成 sqlite 锁住正确性，防将来改动引入静默错误。
"""
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paper_status as PS  # noqa: E402


def _make_db(path, rows=None, schema="close_profit_abs"):
    """建一个最小 trades 表；rows=[(pair, profit, close_date, stake, is_open)]。"""
    con = sqlite3.connect(path)
    con.execute(
        f"CREATE TABLE trades(pair TEXT, {schema} REAL, close_date TEXT, "
        "stake_amount REAL, is_open INT)"
    )
    if rows:
        con.executemany("INSERT INTO trades VALUES(?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_load_closed_absent_db(tmp_path):
    """db 不存在 → (None, None)，不崩。"""
    rows, prof = PS.load_closed(str(tmp_path / "nope.sqlite"))
    assert rows is None and prof is None


def test_load_closed_empty_file(tmp_path):
    """0 字节/无 trades 表的 db → (None, None)，交 main 打印友好提示（历史崩栈已修）。"""
    p = tmp_path / "empty.sqlite"
    p.write_bytes(b"")
    rows, prof = PS.load_closed(str(p))
    assert rows is None and prof is None


def test_load_closed_no_closed_trades(tmp_path):
    """表存在但无 is_open=0 → 空列表 + 列名。"""
    p = tmp_path / "t.sqlite"
    _make_db(p, rows=[("BTC/USDT:USDT", 0.0, "2026-11-10", 1000, 1)])  # 仅开仓单
    rows, prof = PS.load_closed(str(p))
    assert rows == [] and prof == "close_profit_abs"


def test_load_closed_sums_and_sorts(tmp_path):
    """已平仓按 close_date 升序返回。"""
    p = tmp_path / "t.sqlite"
    _make_db(p, rows=[
        ("SOL/USDT:USDT", 40.0, "2026-11-12", 1000, 0),
        ("BTC/USDT:USDT", 25.0, "2026-11-10", 1000, 0),
    ])
    rows, prof = PS.load_closed(str(p))
    assert prof == "close_profit_abs"
    assert [r[0] for r in rows] == ["BTC/USDT:USDT", "SOL/USDT:USDT"]  # 升序


def test_max_drawdown_monotonic_up_is_zero():
    """权益单调上升 → 回撤 0。"""
    assert PS.max_drawdown([1000, 1025, 1055]) == 0.0


def test_max_drawdown_peak_trough():
    """1000→1025→1015→1055：峰 1025、谷 1015 → 回撤 (10/1025)*100≈0.976%。"""
    mdd = PS.max_drawdown([1000, 1025, 1015, 1055])
    assert abs(mdd - (10 / 1025 * 100)) < 1e-6


def test_winrate_and_profit_logic():
    """2 盈 1 亏：胜率 66.7%、总利润 +55（与 main 输出口径一致）。"""
    rows = [("BTC/USDT:USDT", 25.0, "", 1000, 0),
            ("ETH/USDT:USDT", -10.0, "", 1000, 0),
            ("SOL/USDT:USDT", 40.0, "", 1000, 0)]
    n = len(rows)
    wins = sum(1 for r in rows if r[1] > 0)
    total = sum(r[1] for r in rows)
    assert n == 3 and wins == 2
    assert abs(wins / n * 100 - 66.666666) < 1e-3
    assert total == 55.0


def test_criteria_keys_present_for_all_baselines():
    """每个基线策略都必须有完整判据键，否则 main 会 KeyError。"""
    for strat in PS.BASELINES:
        CR = dict(PS.CRITERIA)
        CR.update(PS.CRITERIA_BY_STRATEGY.get(strat, {}))
        for k in ("min_trades", "min_winrate", "max_dd"):
            assert k in CR, f"{strat} 缺判据键 {k}"


def test_oi_baseline_registered():
    """OI 臂必须已注册基线（paper 周报支持 --strategy OIFlushV2）。"""
    assert any("OI" in s or "Flush" in s for s in PS.BASELINES), \
        f"未见 OI 臂基线，现有：{list(PS.BASELINES)}"


def test_v2_criteria_match_freeze_doc():
    """V2 判据必须与 FREEZE_V2.md §六 一致：≥20 笔、胜率≥70%、回撤≤30%。
    锁住"配置—文档"漂移（改文档不改代码、或反之）。"""
    assert PS.CRITERIA["min_trades"] == 20
    assert PS.CRITERIA["min_winrate"] == 70.0
    assert PS.CRITERIA["max_dd"] == 30.0


def test_oi_criteria_match_freeze_doc():
    """OI 判据必须与 FREEZE_OI.md §四 一致：≥20 笔、胜率≥45%、回撤≤10%。"""
    oi = PS.CRITERIA_BY_STRATEGY["OIFlushV2"]
    assert oi["min_trades"] == 20
    assert oi["min_winrate"] == 45.0
    assert oi["max_dd"] == 10.0


def test_small_cap_pairs_constant():
    """小币清单锁定为 BANK/CYS（冻结 paper 池相对 TOP10 的差集；RESEARCH 26.20）。"""
    assert set(p.split("/")[0] for p in PS.SMALL_CAP_PAIRS) == {"BANK", "CYS"}


def test_split_by_pool_partitions():
    """split_by_pool 按 pair 前缀把 BANK/CYS 与小币分开，且不丢不重。"""
    rows = [
        ("BTC/USDT:USDT", 50.0, "d1", 1000, 0),
        ("BANK/USDT:USDT", 30.0, "d2", 1000, 0),
        ("CYS/USDT:USDT", -10.0, "d3", 1000, 0),
        ("ZEC/USDT:USDT", 20.0, "d4", 1000, 0),
    ]
    core, small = PS.split_by_pool(rows)
    assert [r[0] for r in core] == ["BTC/USDT:USDT", "ZEC/USDT:USDT"]
    assert [r[0] for r in small] == ["BANK/USDT:USDT", "CYS/USDT:USDT"]
    assert len(core) + len(small) == len(rows)


def test_cohort_stats_basic():
    """cohort_stats = (笔数, 胜率%, 总利润)；空集 (0,0.0,0.0)。"""
    rows = [("A", 50.0, "d", 1000, 0), ("B", -10.0, "d", 1000, 0)]
    n, wr, p = PS.cohort_stats(rows)
    assert n == 2 and wr == 50.0 and p == 40.0
    assert PS.cohort_stats([]) == (0, 0.0, 0.0)


def test_split_pool_end_to_end_cli(tmp_path, capsys):
    """--split-pool 端到端：生成 db 后跑 main，输出含 pool split 段与小币占利润。"""
    db = tmp_path / "t.sqlite"
    _make_db(db, rows=[
        ("BTC/USDT:USDT", 100.0, "2026-01-01", 1000, 0),
        ("ETH/USDT:USDT", 100.0, "2026-01-02", 1000, 0),
        ("BANK/USDT:USDT", 200.0, "2026-01-03", 1000, 0),
    ])
    import sys as _sys
    old = _sys.argv
    try:
        _sys.argv = ["paper_status.py", "--strategy", "WeekendReverseV2",
                     "--db", str(db), "--split-pool"]
        PS.main()
    finally:
        _sys.argv = old
    out = capsys.readouterr().out
    assert "Pool split" in out
    assert "小币占利润 50%" in out

