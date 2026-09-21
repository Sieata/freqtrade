"""临时：水下曲线分析——把「煎熬」量化（TSMOM30 三品种 + 波动目标版）。"""
from pathlib import Path

import numpy as np
import pandas as pd

FD = Path("user_data/data/binance/futures")
FEE = 0.0005
TEST = ("2022-01-01", "2024-08-28")


def prep(sym):
    f4 = pd.read_feather(FD / f"{sym}_USDT_USDT-4h-futures.feather")
    f4["date"] = pd.to_datetime(f4["date"], utc=True)
    dly = f4.set_index("date")["close"].sort_index().resample("1D").last().dropna()
    fr = pd.read_feather(FD / f"{sym}_USDT_USDT-1h-funding_rate.feather")
    fr["date"] = pd.to_datetime(fr["date"], utc=True)
    rate = pd.Series(pd.to_numeric(fr["open"], errors="coerce").values,
                     index=pd.DatetimeIndex(fr["date"])).sort_index().dropna()
    day, hour = rate.index.floor("D"), rate.index.hour
    fund = pd.DataFrame(index=pd.DatetimeIndex(sorted(set(day))))
    fund["f00"] = rate[hour == 0].groupby(day[hour == 0]).sum()
    fund["f08"] = rate[hour == 8].groupby(day[hour == 8]).sum()
    fund["f16"] = rate[hour == 16].groupby(day[hour == 16]).sum()
    return dly, fund.fillna(0.0)


def net_series(dly, fund, pos, fee=FEE):
    ret = dly.pct_change().fillna(0.0)
    pos = pos.reindex(dly.index).fillna(0.0)
    prev = pos.shift(1).fillna(0.0)
    f = fund.reindex(dly.index).fillna(0.0)
    return pos * ret - (pos * (f["f08"] + f["f16"]) + prev * f["f00"]) \
        - (pos - prev).abs() * fee


def underwater(r):
    eq = (1 + r.dropna()).cumprod()
    dd = eq / eq.cummax() - 1
    uw = dd < 0
    spells, start = [], None
    for i, v in uw.items():
        if v and start is None:
            start = i
        elif not v and start is not None:
            spells.append((start, i))
            start = None
    if start is not None:
        spells.append((start, uw.index[-1]))
    longest = max(spells, key=lambda s: (s[1] - s[0]).days) if spells else (None, None)
    return dd, spells, longest


for sym in ("ETH", "SOL", "DOGE"):
    dly, fund = prep(sym)
    sig = np.sign(dly.pct_change(30)).replace(0, np.nan)
    for tag, pos in [
        ("裸 ±1", sig.shift(1).fillna(0.0)),
        ("40% 波动目标", (sig * (0.40 / (dly.pct_change().rolling(20).std().shift(1)
                                     * np.sqrt(365))).clip(upper=2.0)).shift(1).fillna(0.0)),
    ]:
        r = net_series(dly, fund, pos)
        r = r[r.index >= "2021-07-20"]
        dd, spells, longest = underwater(r)
        eq = (1 + r.dropna()).cumprod()
        n_under = (dd < 0).sum()
        # 最赚 20 天发生在水下多深
        top20 = r.dropna().sort_values(ascending=False).head(20)
        dd_at_top = dd.reindex(top20.index)
        # 裸版本最深回撤持续多久
        deepest = dd.idxmin()
        print(f"\n=== {sym} TSMOM30 [{tag}]（{str(r.index[0])[:10]} ~ {str(r.index[-1])[:10]}）===")
        print(f"  最大回撤 {dd.min():+.1%}   水下天数占比 {n_under / len(dd):.0%}")
        if longest[0] is not None:
            print(f"  最长水下期 {(longest[1] - longest[0]).days} 天"
                  f"（{str(longest[0])[:10]} -> {str(longest[1])[:10]}）")
        print(f"  最赚 20 天当时的水位：中位 {dd_at_top.median():+.0%}  "
              f"最深 {dd_at_top.min():+.0%}  仍在水下的 {(dd_at_top < 0).sum()}/20 天")
        # 深度>30% 的天数
        print(f"  回撤深于 30% 的天数：{100 * (dd < -0.30).mean():.0f}%  "
              f"深于 50%：{100 * (dd < -0.50).mean():.0f}%")
