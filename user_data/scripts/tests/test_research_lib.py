"""research_lib 单元测试：锁住框架审计修过的哑失败，防回归。

跑法: .venv/Scripts/python.exe -m pytest user_data/scripts/tests -q -p no:cacheprovider
全部离线、合成数据，不碰真实 reports/backtest_results。
"""
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import research_lib as rl  # noqa: E402


def _trade(pair, open_, close, ratio):
    return {"pair": pair, "open_date": open_, "close_date": close, "profit_ratio": ratio,
            "profit_abs": ratio * 1000}


def _zip(path, trades, start="2022-01-01 00:00:00", end="2024-08-28 00:00:00"):
    body = {"strategy": {"S": {"trades": trades, "backtest_start": start, "backtest_end": end,
                               "max_relative_drawdown": 0.3, "max_drawdown_account": 0.2}}}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(path.stem + ".json", json.dumps(body))
    return path


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    uni, bt, rep = tmp_path / "universe", tmp_path / "bt", tmp_path / "reports"
    for d in (uni, bt, rep):
        d.mkdir()
    # 中文注释 + UTF-8：Windows cp936 下曾直接崩
    (uni / "pairs_top2.txt").write_text("# 双雄池 注释\nBTC/USDT:USDT  # 比特币 rank=1\nETH/USDT:USDT\n",
                                        encoding="utf-8")
    (uni / "pairs_top5.txt").write_text("BTC/USDT:USDT\nETH/USDT:USDT\nSOL/USDT:USDT\n", encoding="utf-8")
    (uni / "pairs_empty.txt").write_text("# 只有注释\n", encoding="utf-8")
    (uni / "splits.json").write_text(json.dumps({"split_date": "20240828", "test_start": "20220101"}),
                                     encoding="utf-8")
    for k, v in {"UNIVERSE": uni, "BT_DIR": bt, "REPORT_DIR": rep}.items():
        monkeypatch.setattr(rl, k, v)
    return tmp_path


def _report(sandbox, strategy, pool, ts, legs, sidecar):
    """写一份验证报告；legs = {(pool, split): zipname}。"""
    rep = sandbox / "reports" / f"validate_{strategy}_{ts}.md"
    lines = [f"# validate_strategy: {strategy}", ""]
    for (p, split), z in legs.items():
        tr = "20220101-20240828" if split == "TEST" else "20240828-"
        lines += [f"## {p.upper()} × {split}（{tr}）", "实际区间: x → y", "", f"结果: `{z}`", ""]
    rep.write_text("\n".join(lines), encoding="utf-8")
    if sidecar:
        runs = [{"pool": p, "split": s, "timerange": "", "zip": z} for (p, s), z in legs.items()]
        rep.with_suffix(".json").write_text(json.dumps({"runs": runs}), encoding="utf-8")
    return rep


# ---------------------------------------------------------------- 币池
def test_load_pool_utf8_and_meta(sandbox):
    pool = rl.load_pool("top2")
    assert [p for p, _ in pool] == ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    assert pool[0][1] == {"rank": "1"}


def test_empty_pool_raises(sandbox):
    # 空池曾让 tier_b_eval 过滤掉全部交易、输出 0 笔且不报错
    with pytest.raises(SystemExit):
        rl.pool_bases("empty")


def test_pool_wallet(sandbox):
    assert rl.pool_wallet("top5") == 3 * 1.2 * 1000


# ---------------------------------------------------------------- 回测结果读取
def test_read_result_path_resolution(sandbox, monkeypatch):
    z = _zip(sandbox / "bt" / "backtest-result-x.zip", [_trade("BTC/USDT:USDT", "2022-01-03", "2022-01-04", 0.01)])
    assert len(rl.read_result(z)[1]) == 1                    # 绝对路径
    assert len(rl.read_result("backtest-result-x.zip")[1]) == 1  # 裸文件名 → BT_DIR
    monkeypatch.chdir(sandbox)
    assert len(rl.read_result("bt/backtest-result-x.zip")[1]) == 1  # CWD 相对路径不得再拼 BT_DIR


def test_span_uses_backtest_window_not_trades(sandbox):
    # 低频臂：唯一一笔在区间末尾，年化分母仍须是整个回测窗口
    z = _zip(sandbox / "bt" / "backtest-result-y.zip",
             [_trade("BTC/USDT:USDT", "2024-08-01", "2024-08-02", 0.05)])
    stats, _ = rl.read_result(z)
    assert rl.result_span_years(stats) == pytest.approx(2.65, abs=0.01)


def test_result_wallet(sandbox):
    # 0.4 v2：逐年 % 分母 = 回测钱包（独立口径回测 starting_balance = 池规模×1.2×$1,000）
    assert rl.result_wallet({"starting_balance": 12000.0}) == 12000
    assert rl.result_wallet({"dry_run_wallet": 6000}) == 6000
    assert rl.result_wallet({}) == rl.STAKE


# ---------------------------------------------------------------- 报告定位（防混池）
def test_find_validation_requires_pool(sandbox):
    with pytest.raises(ValueError):
        rl.find_validation("S", None)


def test_find_validation_skips_newer_report_of_other_pool(sandbox):
    import os
    import time
    _zip(sandbox / "bt" / "a_test.zip", [])
    old = _report(sandbox, "S", "top5", "1", {("top5", "TEST"): "backtest-result-1_1.zip",
                                               ("top5", "VAL"): "backtest-result-1_2.zip"}, sidecar=False)
    new = _report(sandbox, "S", "top2", "2", {("top2", "TEST"): "backtest-result-2_1.zip",
                                               ("top2", "VAL"): "backtest-result-2_2.zip"}, sidecar=True)
    t = time.time()
    os.utime(old, (t - 100, t - 100))
    os.utime(new, (t, t))
    v = rl.find_validation("S", "top5")  # 最新那份是 top2，必须跳过而不是混用
    assert v["report"] == old.name and v["TEST"]["zip"] == "backtest-result-1_1.zip"
    assert rl.find_validation("S", "TOP2")["VAL"]["zip"] == "backtest-result-2_2.zip"  # 旁车路径 + 大小写
    with pytest.raises(SystemExit):
        rl.find_validation("S", "core")


def test_find_validation_needs_both_legs(sandbox):
    _report(sandbox, "S", "top5", "1", {("top5", "VAL"): "backtest-result-v.zip"}, sidecar=True)
    with pytest.raises(SystemExit):
        rl.find_validation("S", "top5")


def test_load_arm_filters_pool_and_tags_leg(sandbox):
    _zip(sandbox / "bt" / "backtest-result-t.zip", [
        _trade("BTC/USDT:USDT", "2022-02-01", "2022-02-02", 0.01),
        _trade("DOGE/USDT:USDT", "2022-02-01", "2022-02-02", 0.50),   # 池外，须剔除
        _trade("ETH/USDT:USDT", "2024-08-27", "2024-08-30", 0.02),    # TEST 腿末尾跨切分平仓，仍属 TEST
    ])
    _zip(sandbox / "bt" / "backtest-result-v.zip", [_trade("ETH/USDT:USDT", "2025-01-01", "2025-01-02", -0.01)],
         start="2024-08-28 00:00:00", end="2026-09-01 00:00:00")
    _report(sandbox, "S", "top2", "1", {("top2", "TEST"): "backtest-result-t.zip",
                                         ("top2", "VAL"): "backtest-result-v.zip"}, sidecar=True)
    df, _ = rl.load_arm("S", "top2")
    assert set(df["pair_base"]) == {"BTC", "ETH"}
    assert list(df["seg"]) == ["TEST", "TEST", "VAL"]
    assert df["profit$"].tolist() == pytest.approx([10, 20, -10])


# ---------------------------------------------------------------- 月度序列与统计
def _df(rows):
    return rl.trades_frame([_trade("BTC/USDT:USDT", o, c, r) for o, c, r in rows])


def test_monthly_series_fills_empty_months(sandbox):
    lo, hi = pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2023-06-30", tz="UTC")
    m = rl.monthly_series(_df([("2023-01-05", "2023-01-06", 0.1), ("2023-04-05", "2023-04-06", -0.05)]), lo, hi)
    assert len(m) == 6 and m.tolist() == pytest.approx([100, 0, 0, -50, 0, 0])
    assert rl.monthly_series(_df([]), lo, hi).sum() == 0


def test_sharpe_scale_invariant_and_clone_margin_zero(sandbox):
    m = pd.Series([100.0, -50, 80, 20, -10, 60])
    assert rl.sharpe_m(m) == pytest.approx(rl.sharpe_m(m * 3))
    # 门禁5b：复制品 SR_臂 − ρ×SR_V2 = 0（不得通过）
    assert rl.sharpe_m(m) - m.corr(m) * rl.sharpe_m(m) == pytest.approx(0, abs=1e-12)


def test_max_dd(sandbox):
    assert rl.max_dd(pd.Series([100.0, -30, -50, 40])) == -80
    assert rl.max_dd(pd.Series([10.0, 20])) == 0


def test_stats_helpers(sandbox):
    d = pd.Series([10.0, 12, 8, 11, 9, 10, 13, 7])
    assert rl.one_sided_t_p(d) < 1e-4
    lo, hi = rl.boot_ci(d)
    assert lo < d.mean() < hi
    df = _df([("2023-01-05", "2023-01-06", 0.01), ("2023-02-05", "2023-02-06", 0.02),
              ("2023-03-05", "2023-03-06", 0.015), ("2023-03-07", "2023-03-08", 0.005)])
    lo, hi = rl.cluster_boot_ci(df)
    assert lo <= df["profit$"].mean() <= hi


# ---------------------------------------------------------------- 逐笔汇总（2026-10 去重）
def test_trade_pf(sandbox):
    assert rl.trade_pf(pd.Series([10.0, -5, 20, -15])) == pytest.approx(30 / 20)
    assert rl.trade_pf(pd.Series([10.0, 20])) == float("inf")     # 无亏损笔 → ∞
    assert rl.trade_pf(pd.Series([-10.0])) == 0.0                # 只有亏损
    assert rl.trade_pf(pd.Series([], dtype=float)) == 0.0         # 无交易 ≠ 完美策略
    assert rl.trade_pf(pd.Series([10.0, 20]), inf_if_no_loss=False) == 0.0


def test_concentration(sandbox):
    single = _df([("2022-02-01", "2022-02-02", 0.10), ("2023-02-01", "2023-02-02", 0.05)])
    top, share, best = rl.concentration(single)
    # 单品种 → 占比 100%；该品种利润 150 中 2022 年占 100 → 最优年集中 2/3
    assert top == "BTC" and share == pytest.approx(1.0) and best == pytest.approx(2 / 3)

    mixed = rl.trades_frame([
        _trade("BTC/USDT:USDT", "2023-02-01", "2023-02-02", 0.30),
        _trade("ETH/USDT:USDT", "2023-02-01", "2023-02-02", 0.10),
    ])
    top, share, best = rl.concentration(mixed)
    assert top == "BTC" and share == pytest.approx(0.75) and best == pytest.approx(1.0)

    losing = rl.trades_frame([_trade("BTC/USDT:USDT", "2023-02-01", "2023-02-02", -0.10)])
    assert rl.concentration(losing) is None      # 总利润 ≤ 0 时占比无意义


def test_yearly_pnl(sandbox):
    df = rl.trades_frame([
        _trade("BTC/USDT:USDT", "2022-02-01", "2022-02-02", 0.10),
        _trade("BTC/USDT:USDT", "2023-02-01", "2023-02-02", -0.05),
        _trade("ETH/USDT:USDT", "2023-03-01", "2023-03-02", 0.02),
    ])
    assert rl.yearly_pnl(df) == {"2022": pytest.approx(100.0), "2023": pytest.approx(-30.0)}
    pct = rl.yearly_pnl(df, wallet=1000)
    assert pct["2022"][1] == pytest.approx(0.10) and pct["2023"][1] == pytest.approx(-0.03)


def test_leg_window(sandbox):
    _zip(sandbox / "bt" / "backtest-result-t.zip", [], start="2022-01-01 00:00:00",
         end="2024-08-28 00:00:00")
    _zip(sandbox / "bt" / "backtest-result-v.zip", [], start="2024-08-28 00:00:00",
         end="2026-09-01 00:00:00")
    _report(sandbox, "S", "top2", "1", {("top2", "TEST"): "backtest-result-t.zip",
                                         ("top2", "VAL"): "backtest-result-v.zip"}, sidecar=True)
    lo, hi = rl.leg_window("S", "top2", "VAL")
    assert lo == pd.Timestamp("2024-08-28", tz="UTC") and hi == pd.Timestamp("2026-09-01", tz="UTC")


def test_iter_results_walks_all_strategies(sandbox):
    # bt_summary 需列出 zip 内全部策略；read_result 只取首个
    body = {"strategy": {"A": {"trades": [], "profit_total_abs": 1},
                         "B": {"trades": [{"pair": "X"}], "profit_total_abs": 2}},
            "timerange": "2022-2024"}
    p = sandbox / "bt" / "backtest-result-multi.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("multi.json", json.dumps(body))
    names = [n for n, _, _, _ in rl.iter_results(p)]
    assert names == ["A", "B"]
    assert rl.read_result(p)[0]["profit_total_abs"] == 1
    assert next(iter(rl.iter_results(p)))[3]["timerange"] == "2022-2024"


def test_enable_utf8_console_never_raises(sandbox):
    rl.enable_utf8_console()  # 在 pytest 捕获的流上 reconfigure 可能失败，不得抛出


def test_gbk_console_cannot_print_gate_marks():
    """回归锁：Windows cp936 下 ✅/❌ 会 UnicodeEncodeError，被 redirect 时更会被吞成 0 字符。

    这就是 tier_b_eval / portfolio_4arm 未设 PYTHONIOENCODING 时 exit 1 的原因；
    修法是 main() 先调 research_lib.enable_utf8_console()，而不是在文档里要求设环境变量。
    """
    import io
    with pytest.raises(UnicodeEncodeError):
        io.TextIOWrapper(io.BytesIO(), encoding="gbk").write("门禁 ✅")
    # 反证：utf-8 编码器可写（enable_utf8_console 就是让标准流换成它）
    buf = io.BytesIO()
    w = io.TextIOWrapper(buf, encoding="utf-8")
    w.write("门禁 ✅")
    w.flush()
    assert "✅" in buf.getvalue().decode("utf-8")


# ---------------------------------------------------------------- PF 口径（不依赖 stats）
def test_trade_pf_from_trades_uses_profit_abs():
    """固定仓位回测里 stake 会在余额约束下浮动（V2 VAL 最低 916.9），
    profit_ratio×1000 与 profit_abs 差得出 0.002 的 PF —— 必须用后者，才能与
    freqtrade 自己的 profit_factor 逐位一致。"""
    trades = [
        {"profit_abs": 10.0, "profit_ratio": 0.0100, "stake_amount": 950},
        {"profit_abs": -4.0, "profit_ratio": -0.0050, "stake_amount": 800},
        {"profit_abs": 6.0, "profit_ratio": 0.0060, "stake_amount": 1000},
    ]
    assert rl.trade_pf_from_trades(trades) == pytest.approx(16 / 4)
    # 反例：按 ratio×1000 会得到 (10+6)/5 = 3.2，与真实 4.0 不同
    assert rl.trade_pf(pd.Series([t["profit_ratio"] * 1000 for t in trades])) == pytest.approx(3.2)


def test_trade_pf_from_trades_fallbacks():
    assert rl.trade_pf_from_trades([]) == 0.0
    # 缺 profit_abs 的旧 zip → 回退 ratio × stake
    assert rl.trade_pf_from_trades([{"profit_ratio": 0.02}, {"profit_ratio": -0.01}], stake=500) == 2.0


# ---------------------------------------------------------------- 10 轮迭代锁（2026-11-04）
def test_portfolio_fit_weights_no_inf_or_nan():
    """某臂无交易（σ=0）或全部均值≤0 时，旧 fit_weights 产出 inf / 0-0=NaN，
    VAL 配比检验会静默打印 NaN 而不报错。修复后必须是有限权重。"""
    import portfolio_4arm as p4
    m = pd.DataFrame({"V2": [10.0, -5, 8, 2], "OI": [0.0] * 4})   # OI 整段无交易
    for name, w in p4.fit_weights(m).items():
        assert np.isfinite(w.to_numpy()).all(), name
        assert (w >= 0).all(), name
        if name != "1:1":  # 1:1 按定义全 1，无交易臂仍给 1 倍本金是设计行为
            assert w["OI"] == 0.0, name
    m2 = pd.DataFrame({"V2": [-1.0, -2, -3, -4], "OI": [-2.0, -1, -3, -4]})  # 全负 → 解被 clip 0
    for name, w in p4.fit_weights(m2).items():
        assert np.isfinite(w.to_numpy()).all(), name
    assert p4.fit_weights(m2)["最大Sharpe"].sum() == 0.0


def test_year_slice_losing_year_shares_are_finite():
    """亏损年旧口径用负数 total 作分母 → 最赚1笔/最大品种占比变负百分比（误导）。
    修复后分母 = 绝对盈亏和，占比非负且有限。"""
    import year_slice as ys
    rows = [_trade("BTC/USDT:USDT", "2026-01-05", "2026-01-06", -0.10),
            _trade("ETH/USDT:USDT", "2026-02-05", "2026-02-06", 0.01)]
    df = rl.trades_frame(rows)
    # 直接复用 stats_from_zip 的口径计算逻辑（构造一个内存 zip 走完整路径）
    z = _zip(Path(__file__).parent / "losing_year.zip", rows, start="2026-01-01", end="2026-03-01")
    try:
        s = ys.stats_from_zip(z)
        assert s["total"] < 0
        assert 0 <= s["best_share"] <= 100 and 0 <= s["top_share"] <= 100
        assert np.isfinite([s["best_share"], s["top_share"]]).all()
    finally:
        z.unlink(missing_ok=True)


def test_year_slice_monthly_is_full_calendar():
    """旧 stats_from_zip 的 mon 只含有平仓的月：低频臂"负月%"分母漏掉空月，
    worst_month 也漏掉 0。修复后走 monthly_series（全日历补 0）。"""
    import year_slice as ys
    rows = [_trade("BTC/USDT:USDT", "2026-01-05", "2026-01-06", 0.10),
            _trade("BTC/USDT:USDT", "2026-03-05", "2026-03-06", -0.02)]
    z = _zip(Path(__file__).parent / "full_calendar.zip", rows,
             start="2026-01-01 00:00:00", end="2026-06-01 00:00:00")
    try:
        s = ys.stats_from_zip(z)
        assert len(s["monthly"]) == 6                 # 1..6 月，2/4/5/6 月补 0
        assert s["monthly"].iloc[1] == 0.0
        assert s["neg_month"] == pytest.approx(1 / 6 * 100)  # 仅 3 月为负
        assert s["worst_month"] == pytest.approx(-20.0)
    finally:
        z.unlink(missing_ok=True)


def test_exit_anatomy_candle_lookup_tz_aware():
    """feather date 是 tz-aware、close_date 是 naive 字符串；直接比较会 TypeError，
    让 [5] 止损反事实整段静默失效。修复后两种 date dtype 都要能定位到行。"""
    import exit_anatomy as ea
    import tempfile
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=10, freq="4h", tz="UTC"),
                       "close": [100.0] * 10})
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "BTC_USDT_USDT-4h-futures.feather"
        df.to_feather(f)
        ea._cache.clear()
        ea.CAND_DIR = Path(d)
        t = {"pair": "BTC/USDT:USDT", "close_date": "2024-01-01 08:00:00",
             "open_rate": 100.0, "profit_ratio": -0.1}
        got = ea.candles("BTC/USDT:USDT")
        assert got is not None and len(got) == 10
        # 复现修复后的比较表达式（tz-aware → naive UTC）
        dates = got["date"].dt.tz_localize(None)
        ci = got.index[dates == pd.Timestamp(t["close_date"])]
        assert len(ci) == 1 and ci[0] == 2
    ea.CAND_DIR = rl.ROOT / "user_data" / "data" / "binance" / "futures"
    ea._cache.clear()


# ---------------------------------------------------------------- 补足 10 轮实质改进（2026-11-04）
def test_oi_merge_detects_gap_and_atomic_write(sandbox, tmp_path):
    """OI 是"漏跑即永久缺口"的数据件：只报 +N 会掩盖接缝断裂；非原子写入中断会毁全文件。"""
    import oi_accumulate as oi
    old = pd.DataFrame({"date": pd.to_datetime(["2026-08-01 00:00", "2026-08-01 04:00"], utc=True),
                        "oi_usd": [1.0, 2.0]})
    # 正常增量（相差 4h）→ 无缺口告警
    new = pd.DataFrame({"date": pd.to_datetime(["2026-08-01 08:00"], utc=True), "oi_usd": [3.0]})
    df, added, gap = oi.merge_old_new(old, new)
    assert added == 1 and gap is None and len(df) == 3
    # 只有重叠行时必须报 0（旧实现把 cutoff 回看窗口内的重取算成新增）
    _, overlap_added, _ = oi.merge_old_new(old, old.copy())
    assert overlap_added == 0
    # 断 9 天 → 必须报缺口（旧 API 窗口之外的部分已无法回补）
    late = pd.DataFrame({"date": pd.to_datetime(["2026-08-10 08:00"], utc=True), "oi_usd": [4.0]})
    _, _, gap = oi.merge_old_new(old, late)
    assert gap is not None and gap > 8
    # 原子写：落盘后没有 .tmp 残留，且可读回
    out = tmp_path / "x.feather"
    oi.atomic_to_feather(df, out)
    assert out.exists() and not out.with_name(out.name + ".tmp").exists()
    assert len(pd.read_feather(out)) == 3


def test_signal_recon_flags_signals_where_backtest_expected_none():
    """旧实现 `if e > 0 and abs(dev) > 30`：回测预期 0、live 有信号时 dev=inf 却不 FLAG，
    恰好漏掉最强的体制漂移信号。"""
    import signal_recon as sr
    assert sr.deviation(0, 0) == (0.0, False)
    dev, flag = sr.deviation(0, 3)
    assert flag is True and dev == float("inf")
    assert sr.deviation(10, 12)[1] is False          # +20% 正常
    assert sr.deviation(10, 20)[1] is True           # +100% FLAG
    assert sr.deviation(10, 5)[1] is True            # -50% FLAG


def test_bt_summary_lists_every_strategy(sandbox, capsys, monkeypatch):
    """多策略 zip 只打印第一个：与 iter_results 契约及其单测注释直接矛盾。"""
    import bt_summary
    body = {"strategy": {
        "A": {"trades": [], "profit_total_abs": 100, "profit_total": 0.1, "winrate": 0.5,
              "profit_factor": 1.5, "max_relative_drawdown": 0.2, "max_drawdown_account": 0.1,
              "total_trades": 3},
        "B": {"trades": [], "profit_total_abs": 200, "profit_total": 0.2, "winrate": 0.6,
              "profit_factor": 2.0, "max_relative_drawdown": 0.3, "max_drawdown_account": 0.2,
              "total_trades": 4}},
        "timerange": "2022-2024"}
    p = sandbox / "bt" / "backtest-result-multi2.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("multi2.json", json.dumps(body))
    bt_summary.summarize(p)
    out = capsys.readouterr().out
    assert " A " in out and " B " in out, out
    assert out.count("ddW=") == 2


# ---------------------------------------------------------------- 逐笔统计 / 自举 CI
def test_one_sided_t_p_matches_scipy_and_boundaries():
    """单边 t p 值：与 scipy 解析值一致；边界 n<3→nan、全正→极小、全负→≈1、零方差。"""
    from scipy import stats
    d = pd.Series([1.0, 2, 3, 4, 5, 2, 3, 6, 1, 4])
    t = d.mean() / (d.std(ddof=1) / len(d) ** 0.5)
    assert abs(rl.one_sided_t_p(d) - stats.t.sf(t, df=len(d) - 1)) < 1e-12
    assert np.isnan(rl.one_sided_t_p(pd.Series([1.0, 2])))          # n<3
    assert rl.one_sided_t_p(pd.Series([1.0, 2, 3, 4, 5, 6, 7])) < 0.01
    assert rl.one_sided_t_p(pd.Series([-1.0, -2, -3, -4, -5, -6, -7])) > 0.99
    assert rl.one_sided_t_p(pd.Series([1.0, 1, 1, 1])) == 0.0       # 零方差且均值>0
    assert rl.one_sided_t_p(pd.Series([-1.0, -1, -1, -1])) == 1.0   # 零方差且均值≤0


def test_boot_ci_deterministic_and_brackets_mean():
    """自举 CI：同 seed 可复现；95% 区间应包含样本均值；n<3 返回 nan。"""
    d = pd.Series(np.random.RandomState(0).normal(1, 2, 200))
    c1, c2 = rl.boot_ci(d), rl.boot_ci(d)
    assert c1 == c2                                                 # 确定性
    assert c1[0] < d.mean() < c1[1]
    lo, hi = rl.boot_ci(pd.Series([1.0, 2]))
    assert np.isnan(lo) and np.isnan(hi)


def test_cluster_boot_ci_respects_month_blocks():
    """按月聚类自举：同 seed 可复现；区间含总体均值；<3 个月返回 nan。"""
    rows = []
    rng = np.random.RandomState(1)
    for m in ("2024-01", "2024-02", "2024-03", "2024-04"):
        for _ in range(10):
            rows.append({"profit$": float(rng.normal(5, 3)),
                         "close_dt": pd.Timestamp(m + "-15", tz="UTC")})
    df = pd.DataFrame(rows)
    c1, c2 = rl.cluster_boot_ci(df), rl.cluster_boot_ci(df)
    assert c1 == c2
    assert c1[0] < df["profit$"].mean() < c1[1]
    one = pd.DataFrame({"profit$": [1.0, 2.0], "close_dt": pd.to_datetime(["2024-01-01", "2024-02-01"], utc=True)})
    lo, hi = rl.cluster_boot_ci(one)
    assert np.isnan(lo) and np.isnan(hi)


def test_split_ts_reads_sandbox_splits(sandbox):
    """split_ts 从沙箱 splits.json 读切分日期，tz-aware UTC。"""
    ts = rl.split_ts()
    assert ts == pd.Timestamp("2024-08-28", tz="UTC")


def test_trades_frame_segments_and_columns(sandbox):
    """trades_frame：profit$ = profit_ratio×1000；seg 给整帧打常量段标签（不过滤）。"""
    trades = [_trade("BTC/USDT:USDT", "2022-01-01", "2022-06-01", 0.01),
              _trade("ETH/USDT:USDT", "2024-01-01", "2024-06-01", 0.02)]
    df = rl.trades_frame(trades)
    assert set(["pair", "profit$", "close_dt", "open_dt", "pair_base"]).issubset(df.columns)
    assert abs(df["profit$"].sum() - 30.0) < 1e-9                   # (0.01+0.02)*1000
    assert list(df["pair_base"]) == ["BTC", "ETH"]
    tagged = rl.trades_frame(trades, seg="VAL")
    assert (tagged["seg"] == "VAL").all() and len(tagged) == 2


# ---------------------------------------------------------------- paper_status 空 db 容错
def test_paper_status_load_closed_handles_uninitialized_db(tmp_path):
    """paper_status.load_closed：db 存在但从未初始化（0 字节/无 trades 表）时须返回 (None,None)，
    而不是让 SELECT 抛 sqlite3.OperationalError 崩栈。本机三个 dry-run db 全零交易即为该场景。"""
    import sqlite3
    import paper_status as ps

    # 1) db 文件不存在
    missing = tmp_path / "nope.sqlite"
    assert ps.load_closed(str(missing)) == (None, None)

    # 2) 0 字节空文件（V2 tradesv3.dryrun.sqlite 的实际状况）
    empty = tmp_path / "empty.sqlite"
    empty.write_bytes(b"")
    assert ps.load_closed(str(empty)) == (None, None)

    # 3) 有 trades 表但零行 → 返回空列表（不是 None）
    inited = tmp_path / "inited.sqlite"
    con = sqlite3.connect(inited)
    con.execute("CREATE TABLE trades (pair TEXT, profit_abs REAL, close_date TEXT, "
                "stake_amount REAL, is_open INTEGER)")
    con.commit()
    con.close()
    rows, prof = ps.load_closed(str(inited))
    assert rows == [] and prof == "profit_abs"

    # 4) 有一行已平仓 → 读出
    con = sqlite3.connect(inited)
    con.execute("INSERT INTO trades VALUES ('BTC/USDT:USDT', 12.5, '2026-01-01 00:00:00', 1000, 0)")
    con.commit()
    con.close()
    rows, prof = ps.load_closed(str(inited))
    assert len(rows) == 1 and rows[0][0] == "BTC/USDT:USDT"


def test_iter_results_missing_zip_is_clear_error(sandbox):
    """不存在的 zip 应给出清晰的 SystemExit，而不是让 ZipFile 抛原始 FileNotFoundError 栈。"""
    with pytest.raises(SystemExit) as ei:
        list(rl.iter_results(sandbox / "bt" / "ghost.zip"))
    assert "不存在" in str(ei.value)


def test_val_gate_drawdown_uses_wallet_metric_not_account():
    """VAL 回撤门禁必须用钱包口径 dd（max_relative_drawdown），不是账户口径 dd_acct。
    2026-10-07 前的哑失败：门禁误用 max_drawdown_account（同回测 20.2% vs 32.1%），
    门禁系统性偏松。此测试锁住回归——即便 dd_acct 达标、只要 dd 超线就必须 FAIL。"""
    import validate_strategy as v

    base = {"profit_abs": 1000.0, "pf": 2.0, "trades": 50,
            "pairs_profitable": 10, "pairs_total": 10, "dd_slots": 3.2}

    def gate_result(portfolio):
        for name, result, detail in v.gate_check("VAL", portfolio, {}):
            if name == "回撤≤30%":
                return result, detail
        raise AssertionError("未找到回撤门禁")

    # dd=0.35 超 30% 线；dd_acct=0.15 看似达标 → 必须 FAIL（按 dd 判）
    over = dict(base, dd=0.35, dd_acct=0.15)
    r, detail = gate_result(over)
    assert r == "FAIL"

    # dd=0.25 在 30% 线内 → PASS
    under = dict(base, dd=0.25, dd_acct=0.15)
    assert gate_result(under)[0] == "PASS"

    # 说明文字应同时披露两口径，且以钱包口径为主
    assert "钱包口径" in detail and "max_relative_drawdown" in detail
