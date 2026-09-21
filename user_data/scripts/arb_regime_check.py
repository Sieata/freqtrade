"""套利族年景体检 —— 回答「某年还有没有机会」这类问题。

两个断面：
  ① funding 年景：U 本位 ETHUSDT vs 币本位 ETHUSD_PERP 的逐年/近期/逐月资金费率
     （正 = 多头付空头 = 「空永续 + 多现货」的毛收入）。
  ② 逐合约最优点净收益：币本位 ETHUSD 交割合约，取窗口内年化基差最大点入场、
     持有到交割，拆出「价格腿 / funding / fee / 净额 / 折年」。

② 与 `cm_perp_delivery_research.py` 的分工：那个脚本按**预注册 θ 门禁**扫描事件
（回答"规则化后有多少笔、胜率多少"）；本脚本不看门禁、强制逐合约取最优点，
回答"就算让我开天眼挑时点，这一年到底能不能赚"——是门禁策略的**上界**。
若上界都为负，规则化版本不必再看。

用法: ./.venv/Scripts/python.exe user_data/scripts/arb_regime_check.py
"""
from pathlib import Path

import pandas as pd

FD = Path("user_data/data/binance/futures")
CM = Path("user_data/data/binance/cm")
FEE = 0.0015          # 开仓双腿 taker 0.05%×2 + 滑点缓冲（交割到期免手续费）
MIN_DAYS = 14         # 与 cm_perp_delivery_research 保持一致
NOW = pd.Timestamp.now("UTC")
pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 30)

# 币本位交割合约的四个滚动到期日（年内最后一月的 26~28 号附近落在 3/6/9/12 月）
# 各年具体日期不同（2022-03-25 / 2023-03-31 / 2024-03-29 / 2025-03-28 / 2026-03-27），
# 故不硬编码，直接扫数据目录取真实存在的合约。


def delivery_files():
    """返回 [(sym, Path)]，按到期日排序。"""
    out = []
    for fp in CM.glob("ETHUSD_*-4h.feather"):
        sym = fp.name.replace("-4h.feather", "")
        if sym.startswith("ETHUSD_PERP"):
            continue
        tail = sym.split("_")[-1]
        if len(tail) != 6 or not tail.isdigit():
            continue
        out.append((sym, fp))
    return sorted(out, key=lambda x: x[0][-6:])


# ---------------------------------------------------------------- ① funding 年景
def _fund_series(path, col):
    df = pd.read_feather(path)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return (pd.Series(pd.to_numeric(df[col], errors="coerce").values,
                      index=pd.DatetimeIndex(df["date"]))
            .sort_index().dropna())


def _annual_rate(s, days):
    if len(s) == 0 or days <= 0:
        return float("nan")
    return s.sum() / days * 365


def regime():
    u = _fund_series(FD / "ETH_USDT_USDT-1h-funding_rate.feather", "open")
    c = _fund_series(CM / "ETHUSD_PERP-funding.feather", "last_funding_rate")
    print("=== ① 资金费年景（正 = 多头付空头 = 空永续+多现货 的毛收入）===")
    print(f"{'年份':<8}{'ETHUSDT':>12}{'ETHUSD_PERP':>14}   覆盖")
    for y in range(2020, NOW.year + 1):
        a = u[(u.index >= f"{y}-01-01") & (u.index < f"{y + 1}-01-01")]
        b = c[(c.index >= f"{y}-01-01") & (c.index < f"{y + 1}-01-01")]
        if len(a) == 0 and len(b) == 0:
            continue
        da = max((a.index[-1] - a.index[0]).days, 1) if len(a) else 0
        db = max((b.index[-1] - b.index[0]).days, 1) if len(b) else 0
        ta = _annual_rate(a, da) * 100
        tb = _annual_rate(b, db) * 100
        tag = "  <= 当年未满" if y == NOW.year else ""
        print(f"{y:<8}{ta:>11.1f}%{tb:>13.1f}%   n={len(a)}/{len(b)}{tag}")

    print()
    for w in (30, 90, 180, 365):
        t0 = NOW - pd.Timedelta(days=w)
        a, b = u[u.index >= t0], c[c.index >= t0]
        if len(a) == 0:
            continue
        print(f"近 {w:>3} 天：ETHUSDT {_annual_rate(a, w) * 100:+6.2f}%/年   "
              f"币本位 {_annual_rate(b, w) * 100:+6.2f}%/年   正费率占比 "
              f"{100 * (a > 0).mean():.0f}% / {100 * (b > 0).mean():.0f}%")

    print(f"\n{NOW.year} 年逐月（按该月已覆盖天数年化）：")
    for m in range(1, 13):
        s = pd.Timestamp(f"{NOW.year}-{m:02d}-01", tz="UTC")
        if s > NOW:
            break
        e = s + pd.offsets.MonthBegin(1)
        a, b = u[(u.index >= s) & (u.index < e)], c[(c.index >= s) & (c.index < e)]
        if len(a) == 0:
            continue
        da = (a.index[-1] - a.index[0]).days + 1
        db = (b.index[-1] - b.index[0]).days + 1 if len(b) else 0
        print(f"  {NOW.year}-{m:02d}  ETHUSDT {_annual_rate(a, da) * 100:+7.2f}%   "
              f"币本位 {_annual_rate(b, db) * 100 if db else float('nan'):+7.2f}%")


# ---------------------------------------------------------------- ② 逐合约净收益
def _load_contract(sym, perp, rates):
    fp = CM / f"{sym}-4h.feather"
    if not fp.exists():
        return None
    F = pd.read_feather(fp).set_index("date")["close"].rename("F")
    df = F.to_frame().join(perp, how="inner").dropna()
    if len(df) < 50:
        return None
    yy, mm, dd = int(sym[-6:-4]), int(sym[-4:-2]), int(sym[-2:])
    exp = pd.Timestamp(f"20{yy:02d}-{mm:02d}-{dd:02d}", tz="UTC") + pd.Timedelta(hours=8)
    df["b"] = df["F"] / df["P"] - 1
    df["days"] = (exp - df.index).total_seconds() / 86400
    return df[df["days"] >= 0], exp


def _sim(df, i0_ts, s, rates, fee=FEE):
    seg = df.loc[i0_ts:]
    P, F = seg["P"].values, seg["F"].values
    px = s * (P[-1] / P[0] - 1) - s * (F[-1] / F[0] - 1)
    t0, t1 = seg.index[0], seg.index[-1]
    rr = rates[(rates.index > t0) & (rates.index <= t1)]
    fund = -s * float(rr.sum())            # 正费率 = 多头付空头（标准约定）
    net = px + fund - fee
    yrs = (t1 - t0).total_seconds() / 86400 / 365
    return px, fund, net, (net / yrs if yrs > 0 else float("nan")), len(rr)


def contracts(years):
    perp = (pd.read_feather(CM / "ETHUSD_PERP-4h.feather")
            .set_index("date")["close"].rename("P").sort_index())
    rates = _fund_series(CM / "ETHUSD_PERP-funding.feather", "last_funding_rate")

    print("\n\n=== ② 币本位 ETHUSD 交割：逐合约「最优点入场」净收益（正向 = 多永续+空交割）===")
    print("（不看 θ 门禁、强制取窗口内年化基差最大点，故为门禁策略的**上界**）\n")
    rows = []
    for sym, _fp in delivery_files():
        y = 2000 + int(sym.split("_")[-1][:2])
        if y not in years:
            continue
        got = _load_contract(sym, perp, rates)
        if got is None:
            continue
        df, exp = got
        d = df[df["days"] >= MIN_DAYS]
        if d.empty:
            continue
        ann = d["b"] / d["days"] * 365
        i0_ts = ann.idxmax()
        i0 = d.loc[i0_ts]
        px, fund, net, apr, n = _sim(df, i0_ts, 1, rates)
        rows.append({
            "合约": sym, "到期": f"{exp:%Y-%m-%d}", "年": exp.year,
            "入场": str(i0_ts.date()), "剩天": round(i0["days"]),
            "入场b%": i0["b"] * 100, "锁定APR%": i0["b"] / i0["days"] * 365 * 100,
            "正向峰值APR%": ann.max() * 100, "反向峰值APR%": (-ann).max() * 100,
            "ann>8%占比%": 100 * (ann > 0.08).mean(),
            "价格腿%": px * 100, "funding%": fund * 100, "净%": net * 100,
            "折年%": apr * 100})
    t = pd.DataFrame(rows)
    if t.empty:
        print("  无合约数据")
        return
    print(t.round(2).to_string(index=False))
    print("\n按年汇总（净 %，正向）:")
    g = t.groupby("年")["净%"].agg(["count", "mean", "min", "max"]).round(2)
    print(g.to_string())
    print("\n按年汇总（折年 %，正向）:")
    print(t.groupby("年")["折年%"].agg(["mean", "min", "max"]).round(1).to_string())


# ---------------------------------------------------------------- ③ 在市前瞻
def live():
    perp = (pd.read_feather(CM / "ETHUSD_PERP-4h.feather")
            .set_index("date")["close"].rename("P").sort_index())
    rates = _fund_series(CM / "ETHUSD_PERP-funding.feather", "last_funding_rate")
    r90 = rates[rates.index >= NOW - pd.Timedelta(days=90)]
    f90 = _annual_rate(r90, 90)
    print(f"\n\n=== ③ 在市合约前瞻（最新 bar 入场持有到期，funding 按近 90 天 "
          f"{f90 * 100:+.2f}%/年折算）===")
    for sym, _fp in delivery_files():
        got = _load_contract(sym, perp, rates)
        if got is None:
            continue
        df, exp = got
        if exp <= NOW:
            continue
        last = df.iloc[-1]
        dl = last["days"]
        print(f"\n  {sym} 剩 {dl:.0f} 天  b={last['b'] * 100:+.3f}%  "
              f"（锁定价差 {last['b'] * 100:+.3f}%，折年 {last['b'] / dl * 365 * 100:+.1f}%）")
        for s, tag in ((1, "多永续+空交割"), (-1, "空永续+多交割")):
            fwd = f90 / 365 * dl
            net = s * last["b"] - s * fwd - FEE
            print(f"    [{tag}] 基差腿 {s * last['b'] * 100:+.3f}%  "
                  f"funding {-s * fwd * 100:+.3f}%  fee {-FEE * 100:+.2f}%  → "
                  f"预期净 {net * 100:+.3f}%  / 折年 {net / dl * 365 * 100:+.1f}%")


if __name__ == "__main__":
    regime()
    contracts(range(2022, NOW.year + 1))
    live()
