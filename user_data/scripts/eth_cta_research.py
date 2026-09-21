"""ETH 单品种 CTA（趋势跟随）可行性研究 —— 预注册族，非挖掘。

问题：「ETH 的 CTA 策略有得搞吗」。

预注册信号族（经典 CTA，写死参数，不做搜索）：
  TSMOM-k     p = sign(过去 k 日收益)，k ∈ {10, 30, 90, 180}
  MACROSS-f/s p = +1 若 SMA_f > SMA_s 否则 −1，(f,s) ∈ {(10,50),(20,100),(50,200)}
  DONCHIAN-N  收盘创 N 日新高 → +1；新低 → −1；否则维持，N ∈ {20, 55}
  变体：±1 双向（主族）与 long-only（p=max(0,p)）；另加 40% 年化波动率目标 overlay。
  基准：B&H（持永续多头，含 funding）。

口径：
  - 标的：ETHUSDT 永续（futures 4h → 日线收盘）；
  - t−1 收盘出信号，t 日起持仓（无前视）；
  - funding：正费率多头付空头，逐 8h 结算计入（00 点结算记在前一日持仓上）；
  - fee：0.05%/边（taker 永续），敏感度 0.025/0.05/0.10；
  - 分段：TEST 20220101-20240828（选型唯一依据）；
           VAL  20240829-（描述性确认，含 2026 下跌年）；
           全样本 2021 起为描述区（SMA200 预热后 ~2021-08 起）；
  - 预注册选型规则：主族（±1、无 overlay）里 TEST Sharpe 最高者。

用法: ./.venv/Scripts/python.exe user_data/scripts/eth_cta_research.py
"""
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
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 100)


# ---------------------------------------------------------------- 数据
def load():
    f4 = pd.read_feather(FD / "ETH_USDT_USDT-4h-futures.feather")
    f4["date"] = pd.to_datetime(f4["date"], utc=True)
    close4 = f4.set_index("date")["close"].sort_index()
    dly = close4.resample("1D").last().dropna()

    fr = pd.read_feather(FD / "ETH_USDT_USDT-1h-funding_rate.feather")
    fr["date"] = pd.to_datetime(fr["date"], utc=True)
    rate = pd.Series(pd.to_numeric(fr["open"], errors="coerce").values,
                     index=pd.DatetimeIndex(fr["date"])).sort_index().dropna()
    # 按 00/08/16 结算时刻分桶到自然日
    day = rate.index.floor("D")
    hour = rate.index.hour
    fund = pd.DataFrame(index=pd.DatetimeIndex(sorted(set(day))))
    fund["f00"] = rate[(hour == 0)].groupby(day[(hour == 0)]).sum()
    fund["f08"] = rate[(hour == 8)].groupby(day[(hour == 8)]).sum()
    fund["f16"] = rate[(hour == 16)].groupby(day[(hour == 16)]).sum()
    fund = fund.fillna(0.0)
    return dly, fund


# ---------------------------------------------------------------- 信号
def sig_tsmom(dly, k):
    return np.sign(dly.pct_change(k)).replace(0, np.nan)


def sig_macross(dly, f, s):
    return np.sign(dly.rolling(f).mean() - dly.rolling(s).mean()).replace(0, np.nan)


def sig_donchian(dly, n):
    hi = dly.shift(1).rolling(n).max()
    lo = dly.shift(1).rolling(n).min()
    raw = pd.Series(np.where(dly > hi, 1.0, np.where(dly < lo, -1.0, np.nan)), index=dly.index)
    return raw.ffill()          # 状态保持：无新突破则维持原方向


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
    """pos: 日频目标持仓（已含方向与杠杆系数；t 日起生效的值）。返回日净收益序列。"""
    ret = dly.pct_change().fillna(0.0)
    pos = pos.reindex(dly.index).fillna(0.0)
    prev_pos = pos.shift(1).fillna(0.0)
    # 00 点结算记在前一日持仓；08/16 记在当日持仓
    f_all = fund.reindex(dly.index).fillna(0.0)
    f_pay = -(pos * (f_all["f08"] + f_all["f16"]) + prev_pos * f_all["f00"])
    fee = (pos - prev_pos).abs() * fee_side
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
    vol = r.std() * np.sqrt(365)
    dd = (eq / eq.cummax() - 1).min()
    up, dn = r[r > 0].sum(), -r[r < 0].sum()
    out = {"年化%": ann * 100, "波动%": vol * 100, "Sharpe": (r.mean() / r.std() * np.sqrt(365))
           if r.std() > 0 else 0.0, "最大回撤%": dd * 100, "PF": up / dn if dn > 0 else np.inf,
           "天数": len(r)}
    if pos is not None:
        p = pos.reindex(r.index).fillna(0.0)
        out["敞口%"] = (p.abs().mean()) * 100
        out["净多%"] = (p > 0).mean() * 100
    return out


def window(r, seg):
    return r[(r.index >= seg[0]) & (r.index < seg[1])]


# ---------------------------------------------------------------- 主流程
def main():
    dly, fund = load()
    print(f"ETHUSDT 永续日线 {str(dly.index[0])[:10]} -> {str(dly.index[-1])[:10]}  n={len(dly)}"
          f"  最新 {dly.iloc[-1]:,.1f}")
    sig = build_signals(dly)
    names = list(sig)

    # ---- 主族：±1 双向，无 overlay
    print("\n=== A. 主族（±1 双向，fee 0.05%/边，含 funding）===")
    rowsA, retsA = [], {}
    for nm in names:
        pos = sig[nm].shift(1).fillna(0.0)          # t−1 信号 → t 日持仓
        net, _ = pnl_from_pos(pos, dly, fund)
        retsA[nm] = net
        s_full = stats(net, pos)
        s_t = stats(window(net, TEST), pos)
        s_v = stats(window(net, VAL), pos)
        rowsA.append({"配置": nm,
                      "全样 Sharpe": s_full.get("Sharpe", np.nan),
                      "全样年化%": s_full.get("年化%", np.nan),
                      "全样回撤%": s_full.get("最大回撤%", np.nan),
                      "TEST Sharpe": s_t.get("Sharpe", np.nan),
                      "TEST年化%": s_t.get("年化%", np.nan),
                      "VAL Sharpe": s_v.get("Sharpe", np.nan),
                      "VAL年化%": s_v.get("年化%", np.nan),
                      "敞口%": s_full.get("敞口%", np.nan)})
    tA = pd.DataFrame(rowsA)
    print(tA.round(2).to_string(index=False))

    fam = [n for n in names if n != "B&H"]
    fam_mean_daily = pd.concat([retsA[n] for n in fam], axis=1).mean(axis=1)
    s_fm_t, s_fm_v = stats(window(fam_mean_daily, TEST)), stats(window(fam_mean_daily, VAL))
    print(f"\n族均值（{len(fam)} 个信号等权日频平均）:  TEST Sharpe {s_fm_t.get('Sharpe', np.nan):.2f} "
          f"年化 {s_fm_t.get('年化%', np.nan):+.1f}%   VAL Sharpe {s_fm_v.get('Sharpe', np.nan):.2f} "
          f"年化 {s_fm_v.get('年化%', np.nan):+.1f}%")

    pick = tA.loc[tA["配置"] != "B&H"].sort_values("TEST Sharpe").iloc[-1]["配置"]
    print(f"\n预注册选型（TEST Sharpe 最高，主族）: ** {pick} **")
    print(f"  {pick}  VAL: {tA.loc[tA['配置'] == pick, 'VAL Sharpe'].iloc[0]:.2f} / "
          f"{tA.loc[tA['配置'] == pick, 'VAL年化%'].iloc[0]:+.1f}%   "
          f"全样: {tA.loc[tA['配置'] == pick, '全样 Sharpe'].iloc[0]:.2f}")

    # ---- B. long-only 变体
    print("\n=== B. long-only 变体（p = max(0, p)，其余同 A）===")
    rowsB = []
    for nm in fam:
        pos = sig[nm].clip(lower=0.0).shift(1).fillna(0.0)
        net, _ = pnl_from_pos(pos, dly, fund)
        s_t, s_v = stats(window(net, TEST), pos), stats(window(net, VAL), pos)
        rowsB.append({"配置": nm, "TEST Sharpe": s_t.get("Sharpe", np.nan),
                      "TEST年化%": s_t.get("年化%", np.nan),
                      "TEST回撤%": s_t.get("最大回撤%", np.nan),
                      "VAL Sharpe": s_v.get("Sharpe", np.nan),
                      "VAL年化%": s_v.get("年化%", np.nan),
                      "敞口%": s_t.get("敞口%", np.nan)})
    tB = pd.DataFrame(rowsB)
    print(tB.round(2).to_string(index=False))

    # ---- C. 波动率目标 overlay（主族）
    print(f"\n=== C. 40% 年化波动目标 overlay（主族 ±1，20 日已实现波动，cap {VOL_SCALE_CAP}x）===")
    rowsC = []
    for nm in fam:
        pos = vol_overlay(sig[nm], dly).shift(1).fillna(0.0)
        net, _ = pnl_from_pos(pos, dly, fund)
        s_t, s_v = stats(window(net, TEST), pos), stats(window(net, VAL), pos)
        rowsC.append({"配置": nm, "TEST Sharpe": s_t.get("Sharpe", np.nan),
                      "TEST年化%": s_t.get("年化%", np.nan),
                      "TEST回撤%": s_t.get("最大回撤%", np.nan),
                      "VAL Sharpe": s_v.get("Sharpe", np.nan),
                      "VAL年化%": s_v.get("年化%", np.nan)})
    tC = pd.DataFrame(rowsC)
    print(tC.round(2).to_string(index=False))

    # ---- D. 逐年（族均值 + 代表配置 + B&H）
    print("\n=== D. 逐年净收益%（日频复利，含 funding 与 fee）===")
    reps = ["B&H", "TSMOM30", "TSMOM90", "MA20x100", "DON55", "族均值"]
    daily = dict(retsA)
    daily["族均值"] = fam_mean_daily
    yr = pd.DataFrame({nm: window(daily[nm], ("2021-06-01", "2099-01-01")).groupby(
        window(daily[nm], ("2021-06-01", "2099-01-01")).index.year).apply(
        lambda x: (1 + x).prod() - 1) * 100 for nm in reps})
    yr = yr.loc[[y for y in yr.index if y <= 2026]]
    yr.loc["均值"] = yr.mean()
    print(yr.round(1).to_string())

    # ---- E. 费用敏感度（选型候选）
    print(f"\n=== E. {pick} 费用敏感度（TEST 段年化%）===")
    for fee in FEE_SENSE:
        pos = sig[pick].shift(1).fillna(0.0)
        net, _ = pnl_from_pos(pos, dly, fund, fee_side=fee)
        s = stats(window(net, TEST))
        print(f"  fee {fee * 100:.3f}%/边: TEST 年化 {s.get('年化%', np.nan):+.1f}%  "
              f"Sharpe {s.get('Sharpe', np.nan):.2f}")

    # ---- F. 换手与 funding 拆解（选型候选，全样本）
    pos = sig[pick].shift(1).fillna(0.0)
    net, comp = pnl_from_pos(pos, dly, fund)
    yrs = len(net) / 365
    trade_days = (pos.diff().abs() > 0).sum()
    print(f"\n=== F. {pick} 全样本拆解 ===")
    print(f"  换手 {trade_days} 个换向 / {yrs:.1f} 年（{trade_days / yrs:.0f} 次/年）")
    print(f"  累计价格腿 {comp['price'].sum() * 100:+.0f}%   累计 funding {comp['fund'].sum() * 100:+.0f}%"
          f"   累计 fee {comp['fee'].sum() * 100:+.0f}%   累计净 {net.sum() * 100:+.0f}%（算术和）")
    print(f"  与 B&H 日收益相关: {np.corrcoef(net.dropna(), retsA['B&H'].reindex(net.dropna().index))[0, 1]:.2f}")


if __name__ == "__main__":
    main()
