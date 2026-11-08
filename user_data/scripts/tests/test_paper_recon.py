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


def test_recon_oi_skipped():
    r = PR.recon_arm("oi", 14)
    assert r.get("skipped") is True


def test_buy_drop_constant_matches_source():
    # 锁住常量与冻结源码一致（源码 DecimalParameter default=0.02）
    assert PR.V2_BUY_DROP == 0.02
    assert PR.V2_BUY_WINDOW_MODE == "fri_mon"
