"""sync_oi_to_metrics.py 单测：只追加尾巴、不改历史、幂等。"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sync_oi_to_metrics as sync


def _write(df, path):
    df.to_feather(path)


def test_sync_appends_tail_only(tmp_path, monkeypatch):
    d = tmp_path
    monkeypatch.setattr(sync, "METRICS", str(d))
    # metrics: 3 rows through 11-06 20:00 with ratios
    md = pd.DataFrame({
        "date": pd.to_datetime(["2026-11-06 12:00", "2026-11-06 16:00", "2026-11-06 20:00"], utc=True),
        "oi_usd": [1.0, 2.0, 3.0],
        "top_ls_pos": [0.5, 0.6, 0.7],
    })
    # oi_live: history overlapping + 2 new rows (11-07, 11-08)
    od = pd.DataFrame({
        "date": pd.to_datetime([
            "2026-11-06 16:00", "2026-11-06 20:00", "2026-11-07 00:00", "2026-11-08 04:00"
        ], utc=True),
        "oi_usd": [2.05, 3.05, 4.0, 5.0],
    })
    _write(md, os.path.join(d, "BTC_USDT_USDT-4h-metrics.feather"))
    _write(od, os.path.join(d, "BTC_USDT_USDT-4h-oi_live.feather"))

    msg = sync.sync_one("BTC")
    assert "+2" in msg

    out = pd.read_feather(os.path.join(d, "BTC_USDT_USDT-4h-metrics.feather"))
    # history rows unchanged (stored oi_usd not overwritten by oi_live's 2.05/3.05)
    assert list(out["oi_usd"].iloc[:3]) == [1.0, 2.0, 3.0]
    # only 2 new rows appended, tail oi_usd from oi_live
    assert len(out) == 5
    assert out["date"].max() == pd.Timestamp("2026-11-08 04:00", tz="UTC")
    assert out["oi_usd"].iloc[-1] == 5.0
    # new rows have NaN for ratio col not present in oi_live
    assert pd.isna(out["top_ls_pos"].iloc[-1])


def test_sync_idempotent(tmp_path, monkeypatch):
    d = tmp_path
    monkeypatch.setattr(sync, "METRICS", str(d))
    md = pd.DataFrame({
        "date": pd.to_datetime(["2026-11-08 04:00"], utc=True),
        "oi_usd": [5.0],
    })
    od = pd.DataFrame({
        "date": pd.to_datetime(["2026-11-08 04:00"], utc=True),
        "oi_usd": [5.0],
    })
    _write(md, os.path.join(d, "BTC_USDT_USDT-4h-metrics.feather"))
    _write(od, os.path.join(d, "BTC_USDT_USDT-4h-oi_live.feather"))
    msg = sync.sync_one("BTC")
    # no rows later than metrics last -> no change
    assert "无需补" in msg or "+0" in msg


def test_sync_dry_run_no_write(tmp_path, monkeypatch):
    d = tmp_path
    monkeypatch.setattr(sync, "METRICS", str(d))
    md = pd.DataFrame({
        "date": pd.to_datetime(["2026-11-06 20:00"], utc=True),
        "oi_usd": [3.0],
    })
    od = pd.DataFrame({
        "date": pd.to_datetime(["2026-11-08 04:00"], utc=True),
        "oi_usd": [5.0],
    })
    mp = os.path.join(d, "BTC_USDT_USDT-4h-metrics.feather")
    _write(md, mp)
    _write(od, os.path.join(d, "BTC_USDT_USDT-4h-oi_live.feather"))
    before = pd.read_feather(mp)
    msg = sync.sync_one("BTC", dry_run=True)
    assert "[dry]" in msg
    after = pd.read_feather(mp)
    assert len(after) == len(before)  # not written
