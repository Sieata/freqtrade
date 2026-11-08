# -*- coding: utf-8 -*-
"""paper_recon 单测（轮13）：窗口判定、入场复刻、漏单分段逻辑。"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paper_recon as PR


def _df(dates, closes, opens=None, vols=None):
    n = len(dates)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates, utc=True),
            "open": opens if opens is not None else [c * 0.999 for c in closes],
            "close": closes,
            "volume": vols if vols is not None else [1000.0] * n,
        }
    )


def test_window_fri_mon():
    # 2026-11-06 Fri(dow=4), 11-07 Sat(5), 11-08 Sun(6), 11-09 Mon(0)
    dates = [
        "2026-11-06 04:00",  # Fri -> False
        "2026-11-07 04:00",  # Sat -> True
        "2026-11-08 04:00",  # Sun -> True
    ]
    m = PR.v2_window_mask(dates)
    assert list(m) == [False, True, True]


def test_window_monday_beijing_cutoff():
    # Monday 北京时 = UTC+8。UTC 12:00 -> 北京 20:00 (<=21) True；UTC 14:00 -> 北京 22:00 False
    m = PR.v2_window_mask(["2026-11-09 12:00", "2026-11-09 14:00"])
    assert list(m) == [True, False]


def test_long_entry_requires_prev_drop_and_green():
    # buy_drop default 0.02。构造：前一根跌 3%，但当根继续跌破 -2% -> False
    dates = ["2026-11-07 00:00", "2026-11-07 04:00", "2026-11-07 08:00"]
    closes = [100.0, 97.0, 96.0]  # 08:00 ret_1p = 96/97-1 = -1.03% ... 需 < -5%? 用更狠
    opens = [100.0, 100.0, 96.5]
    df = _df(dates, closes, opens)
    le = PR.v2_long_entry(df)
    # 08:00 bar: 收阳? 96.0 > 96.5? NO -> False（body 条件不满足）
    assert bool(le.iloc[2]) is False


def test_long_entry_positive_case():
    # 前一根跌 3%，当根小涨 -1.5%（仍 >= -2%）且收阳
    dates = ["2026-11-07 00:00", "2026-11-07 04:00", "2026-11-07 08:00"]
    closes = [100.0, 97.0, 98.5]  # 08:00 ret_1p=98.5/97-1=+1.5%, prev(97/100-1=-3%)<-2% ✓
    opens = [100.0, 100.0, 98.0]
    df = _df(dates, closes, opens)
    le = PR.v2_long_entry(df)
    assert bool(le.iloc[2]) is True


def test_long_entry_outside_window_false():
    # 同样形态但在周五（dow=4）-> 窗口外 -> False
    dates = ["2026-11-06 00:00", "2026-11-06 04:00", "2026-11-06 08:00"]
    closes = [100.0, 97.0, 98.5]
    opens = [100.0, 100.0, 98.0]
    df = _df(dates, closes, opens)
    le = PR.v2_long_entry(df)
    assert bool(le.iloc[2]) is False


def test_oi_flush_fires_on_crash_and_uptrend():
    # 需 >=360 根才能让 oi_q 分位非 NaN(oi_min_periods=360)。用 400 根上涨趋势，
    # 最后一根急跌约 6%(ret4<=-0.05)，metrics oi 最后一根急缩(oi_chg 极小 <= 分位)，
    # 且 mom30 仍 > 0（跌后 close 仍高于 30 根前）-> flush=True。
    n = 400
    dates = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    closes = [100.0 + i * 0.5 for i in range(n)]   # 每根 +0.5，趋势强，跌 6% 后仍高于 30 根前
    df = _df(list(dates), closes)
    oi = [1_000_000.0 + i for i in range(n)]
    oi[-1] = 100_000.0  # 相对 6 根前巨幅缩水
    mdf = pd.DataFrame({"date": dates, "oi_usd": oi})
    df2 = df.copy()
    df2.loc[df2.index[-1], "close"] = closes[-1] * 0.94  # 急跌 6%
    flush2 = PR.oi_flush_entry(df2, mdf)
    assert bool(flush2.iloc[-1]) is True


def test_oi_flush_no_fire_without_crash():
    # 平稳走势、oi 平稳 -> 无 flush
    n = 400
    dates = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    df = _df(list(dates), [100.0] * n)
    mdf = pd.DataFrame({"date": dates, "oi_usd": [1_000_000.0] * n})
    flush = PR.oi_flush_entry(df, mdf)
    assert not flush.any()


def test_oi_constants_match_source():
    # 锁住 OI 常量与冻结源码一致
    assert PR.OI_QUANTILE == 0.05
    assert PR.OI_WINDOW == 1080
    assert PR.OI_MIN_PERIODS == 360
    assert PR.OI_CRASH_THRESHOLD == -0.05
    assert PR.OI_MOM_WINDOW == 180
    assert PR.OI_SHIFT == 6


def test_buy_drop_constant_matches_source():
    # 锁住常量与冻结源码一致（源码 DecimalParameter default=0.02）
    assert PR.V2_BUY_DROP == 0.02
    assert PR.V2_BUY_WINDOW_MODE == "fri_mon"
