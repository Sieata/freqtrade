"""单品种 CTA（趋势跟随）可行性研究台 —— 预注册族，非挖掘。支持任意 USDT 永续品种。

问题形态：「X 币的 CTA 策略有得搞吗」。

预注册信号族（经典 CTA，写死参数，不做搜索）：
  TSMOM-k     p = sign(过去 k 日收益)，k ∈ {10, 30, 90, 180}
  MACROSS-f/s p = +1 若 SMA_f > SMA_s 否则 −1，(f,s) ∈ {(10,50),(20,100),(50,200)}
  DONCHIAN-N  收盘创 N 日新高 → +1；新低 → −1；否则维持，N ∈ {20, 55}
  变体：±1 双向（主族）与 long-only（p=max(0,p)）；另加 40% 年化波动率目标 overlay。
  基准：B&H（持永续多头，含 funding）。

口径：
  - 标的：`{SYM}_USDT_USDT` 永续（futures 4h → 日线收盘）；
  - t−1 收盘出信号，t 日起持仓（无前视）；
  - funding：正费率多头付空头，逐 8h 结算计入（00 点结算记在前一日持仓上）；
  - fee：0.05%/边（taker 永续），敏感度 0.025/0.05/0.10；
  - 分段：TEST 20220101-20240828（选型唯一依据）；
           VAL  20240829-（描述性确认，含 2026 下跌年）；
           全样本自数据起点为描述区（SMA200 预热期不参与统计）；
  - 预注册选型规则：主族（±1、无 overlay）里 TEST Sharpe 最高者。
  - **跨品种对照时注意起点差异**：SOL 2020-09 / DOGE 2020-07 上市，但全样本年化对
    起止点敏感（DOGE 首年暴涨会抬高全样），跨品种比一律以 TEST / VAL 分段为准。

用法:
  ./.venv/Scripts/python.exe user_data/scripts/cta_research.py --sym ETH
  ./.venv/Scripts/python.exe user_data/scripts/cta_research.py --sym ETH,SOL,DOGE --brief
  ./.venv/Scripts/python.exe user_data/scripts/cta_research.py --sym ALL
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

FD = Path("user_data/data/binance/futures")
FEE_SIDE = 0.0005          # 0.05%/边，taker 永续
FEE_SENSE = [0.00025, 0.0005, 0.0010]
VOL_TARGET = 0.40          # overlay 年化波动目标
VOL_WIN = 20
VOL_SCALE_CAP = 2.0
TEST = ("2022-01-01", "2024-08-28")
VAL = ("2024-08-29", "2099-01-01")
FAM = ["TSMOM10", "TSMOM30", "TSMOM90", "TSMOM180",
       "MA10x50", "MA20x100", "MA50x200", "DON20", "DON55"]
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 120)


# ---------------------------------------------------------------- 数据
def load(sym):
    f4p = FD / f"{sym}_USDT_USDT-4h-futures.feather"
    frp = FD / f"{sym}_USDT_USDT-1h-funding_rate.feather"
    if not f4p.exists() or not frp.exists():
        raise FileNotFoundError(f"{sym}: 缺 futures/funding 数据")
    f4 = pd.read_feather(f4p)
    f4["date"] = pd.to_datetime(f4["date"], utc=True)
    dly = f4.set_index("date")["close"].sort_index().resample("1D").last().dropna()

    fr = pd.read_feather(frp)
    fr["date"] = pd.to_datetime(fr["date"], utc=True)
    rate = pd.Series(pd.to_numeric(fr["open"], errors="coerce").values,
                     index=pd.DatetimeIndex(fr["date"])).sort_index().dropna()
    day, hour = rate.index.floor("D"), rate.index.hour
    fund = pd.DataFrame(index=pd.DatetimeIndex(sorted(set(day))))
    fund["f00"] = rate[hour == 0].groupby(day[hour == 0]).sum()
    fund["f08"] = rate[hour == 8].groupby(day[hour == 8]).sum()
    fund["f16"] = rate[hour == 16].groupby(day[hour == 16]).sum()
    return dly, fund.fillna(0.0)


# ---------------------------------------------------------------- 信号
def sig_tsmom(dly, k):
    return np.sign(dly.pct_change(k)).replace(0, np.nan)


def sig_macross(dly, f, s):
    return np.sign(dly.rolling(f).mean() - dly.rolling(s).mean()).replace(0, np.nan)


def sig_donchian(dly, n):
    hi = dly.shift(1).rolling(n).max()
    lo = dly.shift(1).rolling(n).min()
    raw = pd.Series(np.where(dly > hi, 1.0, np.where(dly < lo, -1.0, np.nan)), index=dly.index)
    return raw.ffill()


def build_signals(dly):
    sig = {"B&H": pd.Series(1.0, index=dly.index)}
    for k in (10, 30, 90, 180):
        sig[f"TSMOM{k}"] = sig_tsmom(dly, k)
    for f, s in ((10, 50), (20, 100), (50, 200)):
        sig[f"MA{f}x{s}"] = sig_macross(dly, f, s)
    for n in (20, 55):
        sig[f"DON{n}"] = sig_donchian(dly, n)
    return sig


# ---------------------------------------------------------------- 模拟
def pnl_from_pos(pos, dly, fund, fee_side=FEE_SIDE):
    ret = dly.pct_change().fillna(0.0)
    pos = pos.reindex(dly.index).fillna(0.0)
    prev = pos.shift(1).fillna(0.0)
    f = fund.reindex(dly.index).fillna(0.0)
    # 00 点结算记前一日持仓；08/16 记当日持仓
    f_pay = -(pos * (f["f08"] + f["f16"]) + prev * f["f00"])
    fee = (pos - prev).abs() * fee_side
    return pos * ret + f_pay - fee, {"price": pos * ret, "fund": f_pay, "fee": fee}


def vol_overlay(pos, dly):
    rv = dly.pct_change().rolling(VOL_WIN).std().shift(1) * np.sqrt(365)
    scale = (VOL_TARGET / rv).clip(upper=VOL_SCALE_CAP).fillna(0.0).clip(lower=0.0)
    return (pos * scale).fillna(0.0)


def stats(r, pos=None):
    r = r.dropna()
    if len(r) < 30:
        return {}
    yrs = len(r) / 365.0
    eq = (1 + r).cumprod()
    ann = eq.iloc[-1] ** (1 / yrs) - 1 if eq.iloc[-1] > 0 else -1.0
    up, dn = r[r > 0].sum(), -r[r < 0].sum()
    out = {"年化%": ann * 100, "Sharpe": r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else 0.0,
           "回撤%": (eq / eq.cummax() - 1).min() * 100,
           "PF": up / dn if dn > 0 else np.inf, "天数": len(r)}
    if pos is not None:
        p = pos.reindex(r.index).fillna(0.0)
        out["敞口%"] = p.abs().mean() * 100
    return out


def window(r, seg):
    return r[(r.index >= seg[0]) & (r.index < seg[1])]


# ---------------------------------------------------------------- 单品种
def one(sym, brief=False):
    dly, fund = load(sym)
    sig = build_signals(dly)
    print(f"\n{'=' * 92}\n### {sym}  日线 {str(dly.index[0])[:10]} -> {str(dly.index[-1])[:10]} "
          f"n={len(dly)}  最新 {dly.iloc[-1]:,.4g}\n{'=' * 92}")
    rows, rets = [], {}
    for nm, s in sig.items():
        pos = s.shift(1).fillna(0.0)
        net, _ = pnl_from_pos(pos, dly, fund)
        rets[nm] = net
        rows.append({"配置": nm,
                     "TEST Sharpe": stats(window(net, TEST), pos).get("Sharpe", np.nan),
                     "TEST年化%": stats(window(net, TEST), pos).get("年化%", np.nan),
                     "TEST回撤%": stats(window(net, TEST), pos).get("回撤%", np.nan),
                     "VAL Sharpe": stats(window(net, VAL), pos).get("Sharpe", np.nan),
                     "VAL年化%": stats(window(net, VAL), pos).get("年化%", np.nan),
                     "全样 Sharpe": stats(net, pos).get("Sharpe", np.nan),
                     "全样年化%": stats(net, pos).get("年化%", np.nan),
                     "敞口%": stats(net, pos).get("敞口%", np.nan)})
    t = pd.DataFrame(rows)
    if not brief:
        print("\n--- A. 主族（±1 双向，含 funding，fee 0.05%/边）---")
        print(t.round(2).to_string(index=False))

    fam_daily = pd.concat([rets[n] for n in FAM], axis=1).mean(axis=1)
    fd_t, fd_v, fd_f = stats(window(fam_daily, TEST)), stats(window(fam_daily, VAL)), stats(fam_daily)
    fam_t = t.loc[t["配置"].isin(FAM), "TEST Sharpe"]
    print(f"\n族均值·TEST Sharpe {fd_t.get('Sharpe', np.nan):+.2f}（单个信号中位 {fam_t.median():+.2f}）"
          f"   族均值·TEST 年化 {fd_t.get('年化%', np.nan):+.1f}%")
    print(f"族均值·VAL  Sharpe {fd_v.get('Sharpe', np.nan):+.2f}  年化 {fd_v.get('年化%', np.nan):+.1f}%")

    pick = t.loc[t["配置"] != "B&H"].sort_values("TEST Sharpe").iloc[-1]
    print(f"预注册选型（TEST Sharpe 最高）: ** {pick['配置']} **  "
          f"TEST {pick['TEST年化%']:+.1f}% / Sharpe {pick['TEST Sharpe']:.2f}  "
          f"VAL {pick['VAL年化%']:+.1f}% / Sharpe {pick['VAL Sharpe']:.2f}")

    # overlay
    posv = vol_overlay(sig[pick["配置"]], dly).shift(1).fillna(0.0)
    netv, _ = pnl_from_pos(posv, dly, fund)
    sv_t, sv_v = stats(window(netv, TEST), posv), stats(window(netv, VAL), posv)
    print(f"  + 40% 波动目标 overlay: TEST Sharpe {sv_t.get('Sharpe', np.nan):.2f} "
          f"({sv_t.get('年化%', np.nan):+.1f}%, 回撤 {sv_t.get('回撤%', np.nan):+.0f}%)  "
          f"VAL Sharpe {sv_v.get('Sharpe', np.nan):.2f} ({sv_v.get('年化%', np.nan):+.1f}%)")

    # 逐年（从最长预热 200 天完成日截起，避免首年受 SMA200 预热影响）
    rep = ["B&H", "TSMOM30", "TSMOM90", "MA20x100", "DON55"]
    daily = dict(rets)
    daily["族均值"] = fam_daily
    cols = rep + ["族均值"]
    warm = dly.index[0] + pd.Timedelta(days=200)
    yr = pd.DataFrame({nm: window(daily[nm], (warm, "2099-01-01")).groupby(
        lambda i: i.year).apply(lambda x: (1 + x).prod() - 1) * 100 for nm in cols})
    yr = yr.loc[[y for y in yr.index if y <= pd.Timestamp.now().year]]
    print(f"\n--- D. 逐年净收益%（含 funding 与 fee；自 {str(warm)[:10]} 预热完成后截起）---")
    print(yr.round(1).to_string())
    print(f"均值   " + "".join(f"{yr[nm].mean():>9.1f}" for nm in cols))

    # 费用敏感度
    print(f"\n--- E. {pick['配置']} 费用敏感度（TEST 年化%）---")
    line = []
    for fee in FEE_SENSE:
        pos = sig[pick["配置"]].shift(1).fillna(0.0)
        net, _ = pnl_from_pos(pos, dly, fund, fee_side=fee)
        line.append(f"fee {fee * 100:.3f}%: {stats(window(net, TEST)).get('年化%', np.nan):+.1f}%")
    print("   " + "   ".join(line))

    # 拆解 + 相关性 + 收益集中度（集中度是判死单品种 CTA 的关键证据）
    pos = sig[pick["配置"]].shift(1).fillna(0.0)
    net, comp = pnl_from_pos(pos, dly, fund)
    yrs = len(net) / 365
    print(f"\n--- F. 拆解（{pick['配置']}，全样本）---")
    print(f"   换向 {(pos.diff().abs() > 0).sum()} 次 / {yrs:.1f} 年 = "
          f"{(pos.diff().abs() > 0).sum() / yrs:.0f} 次/年")
    print(f"   累计 价格腿 {comp['price'].sum() * 100:+.0f}%  funding {comp['fund'].sum() * 100:+.0f}%"
          f"  fee {comp['fee'].sum() * 100:+.0f}%")
    r_ = net.dropna()
    for base, tag in (("B&H", "B&H"), ("族均值", "族均值")):
        common = r_.index.intersection(daily[base].dropna().index)
        print(f"   与{tag}日收益相关: "
              f"{np.corrcoef(r_[common], daily[base].reindex(common))[0, 1]:+.2f}", end="")
    print()

    # 集中度：TEST 段逐日贡献排序，剔除最赚 top-20 天后的复利
    t_net = window(net, TEST).dropna().sort_values(ascending=False)
    if len(t_net) > 40:
        top20 = t_net.head(20).sum()
        tot = t_net.sum()
        rest = t_net.iloc[20:]
        print(f"\n--- G. 收益集中度（TEST 段，{len(t_net)} 个交易日）---")
        print(f"   全段累计（算术）{tot * 100:+.0f}%   最赚 20 天贡献 {top20 * 100:+.0f}%"
              f"（占比 {top20 / tot * 100 if tot > 0 else float('nan'):.0f}%）")
        print(f"   剔除最赚 20 天后复利: {(1 + rest).prod() - 1:+.1%}"
              f"   <-- 若转负，说明收益全押在少数几天，不是可复制的信号")

    return {"sym": sym, "pick": pick["配置"], "TEST Sharpe": pick["TEST Sharpe"],
            "TEST年化%": pick["TEST年化%"], "VAL Sharpe": pick["VAL Sharpe"],
            "VAL年化%": pick["VAL年化%"], "族TEST Sharpe": fd_t.get("Sharpe", np.nan),
            "族VAL Sharpe": fd_v.get("Sharpe", np.nan),
            "overlay TEST Sharpe": sv_t.get("Sharpe", np.nan),
            "overlay VAL Sharpe": sv_v.get("Sharpe", np.nan),
            "剔Top20复利%": ((1 + t_net.iloc[20:]).prod() - 1) * 100 if len(t_net) > 40 else np.nan,
            "族均值日收益": fam_daily, "选型日收益": net}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym", default="ETH", help="ETH / SOL / DOGE / 逗号分隔 / ALL")
    ap.add_argument("--brief", action="store_true", help="只打印汇总，不打印逐信号大表")
    a = ap.parse_args()
    syms = a.sym.upper().split(",") if a.sym.upper() != "ALL" else \
        sorted({p.name.split("_USDT_USDT")[0] for p in FD.glob("*_USDT_USDT-4h-futures.feather")})

    out = [one(s.strip(), a.brief) for s in syms]

    if len(out) > 1:
        print(f"\n\n{'#' * 92}\n### 跨品种汇总\n{'#' * 92}")
        t = pd.DataFrame([{k: v for k, v in o.items() if not k.endswith("日收益")} for o in out])
        print(t.round(2).to_string(index=False))
        print("\n### 选型配置的日收益相关矩阵")
        d = pd.DataFrame({o["sym"]: o["选型日收益"] for o in out}).dropna()
        print(d.corr().round(2).to_string())
        print("\n### 族均值日收益相关矩阵")
        d2 = pd.DataFrame({o["sym"]: o["族均值日收益"] for o in out}).dropna()
        print(d2.corr().round(2).to_string())
        # 三品种等权组合
        combo = d2.mean(axis=1)
        print(f"\n三品种族均值等权组合: TEST Sharpe {stats(window(combo, TEST)).get('Sharpe', np.nan):.2f}"
              f" 年化 {stats(window(combo, TEST)).get('年化%', np.nan):+.1f}%   "
              f"VAL Sharpe {stats(window(combo, VAL)).get('Sharpe', np.nan):.2f}"
              f" 年化 {stats(window(combo, VAL)).get('年化%', np.nan):+.1f}%")


if __name__ == "__main__":
    main()
