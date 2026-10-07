"""research_lib 单元测试：锁住框架审计修过的哑失败，防回归。

跑法: .venv/Scripts/python.exe -m pytest user_data/scripts/tests -q -p no:cacheprovider
全部离线、合成数据，不碰真实 reports/backtest_results。
"""
import json
import sys
import zipfile
from pathlib import Path

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
