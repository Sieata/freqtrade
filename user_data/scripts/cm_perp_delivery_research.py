"""币本位（COIN-M）ETHUSD 永续 × 交割 基差套利研究 —— H8c 的币本位对照。

与 H8c（carry_h8c_perp_delivery.py，USDT-M 永续×交割）的结构差异：
  - 两腿**保证金同币种**（都是 ETH），没有 USDT/USD 汇率混淆项；
  - 两腿**同指数**（ETHUSD 指数），基差来源单一 = 交割条款 vs 永续条款；
  - 反向合约：USD 名义固定、盈亏以 ETH 计价 → 对持币者是「增币」，
    但就 USD 盈亏而言与线性合约等价（多头 PnL_usd = N×(P_exit/P_entry − 1)）。

口径（预注册；TEST 20220101-20240828，之后仅描述统计、不调参）：
  触发: 年化基差 ann = b/days_left×365（b = 交割收盘/永续收盘 − 1）
        正向（contango, b>0）ann ≥ θ ∈ {8,15,25}%；反向（backwardation）ann ≤ −θ ∈ {8,15}%；
        days_left ≥ 14；每合约每方向至多一次（首次触发）
  退出: 持有至交割（F_T → P_T，基差强制收敛）
  收益（USD 名义 N=1）= [P_T/P_0 − 1]·s_perp·(−1)^? ... 用逐段价格跟踪实现：
        多永续腿 + 空交割腿 → 累计 USD PnL = Σ[ΔP/P] − Σ[ΔF/F]
        funding: 多头付正费率（标准约定）→ −Σrate（s=+1 时成本），s=−1 时收入
  另报**币计价**收益（÷ P_T）= 持币者实际增币率 —— 币本位独有视角
数据: data.binance.vision cm 桶（4h klines / fundingRate），交割合约另存 cm/ 缓存；
      dapi 增量补尾（当月/近期）。已有 quarterly/ 缓存是 H8 时代 95 天窗口，本脚本
      **不覆盖**它，全部交割腿统一按 190 天窗口重采到 cm/。
用法: export https_proxy=http://127.0.0.1:7897 http_proxy=http://127.0.0.1:7897
      .venv/Scripts/python.exe user_data/scripts/cm_perp_delivery_research.py
"""
import io
import json
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd

SYM = "ETH"
PERP_SYM = f"{SYM}USD_PERP"
BASE = "https://data.binance.vision/data/futures"
CM = Path("user_data/data/binance/cm")
QTR = Path("user_data/data/binance/quarterly")

FEE = 0.0015                      # 主口径：开仓双腿 taker 0.05%×2 + 滑点缓冲（交割到期免手续费）
FEE_SENSE = [0.0010, 0.0030, 0.0050]
THETAS_POS = [0.08, 0.15, 0.25]
THETAS_NEG = [0.08, 0.15]
MIN_DAYS_LEFT = 14
T_START = pd.Timestamp("2022-01-01", tz="UTC")
T_END = pd.Timestamp("2024-08-28", tz="UTC")
LOOKBACK_DAYS = 190
CM.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 采集
def months_between(d0, d1):
    out, cur = [], date(d0.year, d0.month, 1)
    while cur <= d1:
        out.append(f"{cur.year}-{cur.month:02d}")
        cur = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
    return out


def _parse_kline_zip(b):
    z = zipfile.ZipFile(io.BytesIO(b))
    name = z.namelist()[0]
    head = z.open(name).readline().decode().strip()
    has_header = head.lower().startswith("open_time")
    df = pd.read_csv(z.open(name), header=0 if has_header else None)
    if not has_header:
        df.columns = ["open_time", "open", "high", "low", "close", "volume", "close_time",
                      "qv", "n", "tbb", "tbq", "ig"]
    df["date"] = pd.to_datetime(pd.to_numeric(df["open_time"]), unit="ms", utc=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df[["date", "close"]]


def _parse_funding_zip(b):
    z = zipfile.ZipFile(io.BytesIO(b))
    name = z.namelist()[0]
    head = z.open(name).readline().decode().strip()
    has_header = head.lower().startswith("calc_time")
    df = pd.read_csv(z.open(name), header=0 if has_header else None)
    if not has_header:
        df.columns = ["calc_time", "funding_interval_hours", "last_funding_rate"]
    df["date"] = pd.to_datetime(pd.to_numeric(df["calc_time"]), unit="ms", utc=True)
    df["last_funding_rate"] = pd.to_numeric(df["last_funding_rate"], errors="coerce")
    return df[["date", "last_funding_rate", "funding_interval_hours"]]


def fetch(url, parser):
    try:
        with urlopen(url, timeout=30) as r:
            return parser(r.read())
    except Exception:
        return None


def fetch_month(sym, ym, kind="klines", bucket="cm"):
    if kind == "klines":
        return fetch(f"{BASE}/{bucket}/monthly/klines/{sym}/4h/{sym}-4h-{ym}.zip", _parse_kline_zip)
    return fetch(f"{BASE}/{bucket}/monthly/fundingRate/{sym}/{sym}-fundingRate-{ym}.zip",
                 _parse_funding_zip)


def dapi(path):
    try:
        with urlopen(f"https://dapi.binance.com{path}", timeout=25) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"  [dapi 失败] {path}: {type(e).__name__}")
        return None


def dapi_klines_tail(sym, last_ms, contract_type=None):
    rows, since = [], last_ms + 1
    while True:
        if contract_type:  # PERP 走 continuousKlines
            p = (f"/dapi/v1/continuousKlines?pair={SYM}USD&contractType={contract_type}"
                 f"&interval=4h&startTime={since}&limit=1500")
        else:
            p = f"/dapi/v1/klines?symbol={sym}&interval=4h&startTime={since}&limit=1500"
        batch = dapi(p)
        if not batch:
            break
        rows += batch
        if len(batch) < 1500:
            break
        since = batch[-1][0] + 1
    if not rows:
        return None
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["ts", "open", "high", "low", "close", "v"]
    df["date"] = pd.to_datetime(pd.to_numeric(df["ts"]), unit="ms", utc=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df[["date", "close"]]


def dapi_funding_tail(sym, last_ms):
    rows, since = [], last_ms + 1
    while True:
        batch = dapi(f"/dapi/v1/fundingRate?symbol={sym}&startTime={since}&limit=1000")
        if not batch:
            break
        rows += batch
        if len(batch) < 1000:
            break
        since = batch[-1]["fundingTime"] + 1
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(pd.to_numeric(df["fundingTime"]), unit="ms", utc=True)
    df["last_funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    df["funding_interval_hours"] = 8
    return df[["date", "last_funding_rate", "funding_interval_hours"]]


def build_funding_dapi(sym, out_path, start="2020-01-01"):
    """dapi 全历史 funding（vision 的 cm fundingRate 只到 2022-07，覆盖不足）。"""
    if out_path.exists():
        return f"  [cache] {out_path.name}"
    since, rows, guard = int(pd.Timestamp(start, tz="UTC").value // 10**6), [], 0
    while guard < 40:
        guard += 1
        b = dapi(f"/dapi/v1/fundingRate?symbol={sym}&startTime={since}&limit=1000")
        if not b:
            break
        rows += b
        nxt = int(b[-1]["fundingTime"]) + 1
        if nxt <= since:
            break
        since = nxt
        if len(b) < 1000:
            break
    if not rows:
        return f"  [none] {sym} funding"
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(pd.to_numeric(df["fundingTime"]), unit="ms",
                                utc=True).dt.floor("h")
    df["last_funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    df["funding_interval_hours"] = 8
    df = (df[["date", "last_funding_rate", "funding_interval_hours"]]
          .sort_values("date").drop_duplicates("date").reset_index(drop=True))
    df.to_feather(out_path)
    return (f"  [ok] {out_path.name}: {len(df)} 行 "
            f"{str(df['date'].min())[:10]} -> {str(df['date'].max())[:10]}")


def build_series(sym, kind, out_path, month_from, month_to):
    """月度 vision 包拼接 + dapi 补尾，缓存 feather。"""
    if out_path.exists():
        return f"  [cache] {out_path.name}"
    parser = _parse_kline_zip if kind == "klines" else _parse_funding_zip
    got = []
    with ThreadPoolExecutor(8) as ex:
        for r in ex.map(lambda ym: fetch_month(sym, ym, kind), months_between(month_from, month_to)):
            if r is not None and len(r):
                got.append(r)
    if not got:
        return f"  [none] {sym} {kind}"
    df = pd.concat(got).sort_values("date").drop_duplicates("date").reset_index(drop=True)
    # 补尾
    last_ms = int(df["date"].max().value // 10**6)
    now_ms = int(pd.Timestamp.now("UTC").value // 10**6)
    if last_ms < now_ms - 2 * 86400 * 10**3:
        tail = (dapi_klines_tail(sym, last_ms, "PERPETUAL") if sym == PERP_SYM
                else dapi_klines_tail(sym, last_ms)) if kind == "klines" else \
               dapi_funding_tail(sym, last_ms)
        if tail is not None and len(tail):
            df = pd.concat([df, tail]).sort_values("date").drop_duplicates("date")
    df.to_feather(out_path)
    return (f"  [ok] {out_path.name}: {len(df)} 行 "
            f"{str(df['date'].min())[:10]} -> {str(df['date'].max())[:10]}")


def quarter_last_fridays(y0=2021, y1=2026):
    out = []
    for y in range(y0, y1 + 1):
        for m in (3, 6, 9, 12):
            last = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
            while last.weekday() != 4:
                last -= timedelta(days=1)
            out.append(last)
    return sorted(set(out))


def build_contracts(expiries):
    """交割腿：到期前 LOOKBACK_DAYS 起的窗口，统一采到 cm/。"""
    def one(e):
        sym = f"{SYM}USD_{e.strftime('%y%m%d')}"
        out = CM / f"{sym}-4h.feather"
        if out.exists():
            return f"  [cache] {sym}"
        start = e - timedelta(days=LOOKBACK_DAYS)
        end = min(e, date.today())
        if start >= end:
            return f"  [skip] {sym}"
        rows = []
        with ThreadPoolExecutor(8) as ex:
            for r in ex.map(lambda ym: fetch_month(sym, ym, "klines"),
                            months_between(start, end)):
                if r is not None and len(r):
                    rows.append(r)
        if not rows:
            return f"  [none] {sym}"
        df = pd.concat(rows).sort_values("date").drop_duplicates("date").reset_index(drop=True)
        expiry_ts = pd.Timestamp(e, tz="UTC") + pd.Timedelta(hours=8)
        df = df[df["date"] <= expiry_ts + pd.Timedelta(hours=4)]
        last_ms = int(df["date"].max().value // 10**6)
        if expiry_ts > pd.Timestamp.now("UTC") and \
           last_ms < int(pd.Timestamp.now("UTC").value // 10**6) - 2 * 86400 * 10**3:
            tail = dapi_klines_tail(sym, last_ms)
            if tail is not None and len(tail):
                df = pd.concat([df, tail]).sort_values("date").drop_duplicates("date")
        df.to_feather(out)
        return (f"  [ok] {sym}: {len(df)} 根 {str(df['date'].min())[:10]} -> "
                f"{str(df['date'].max())[:10]}")
    with ThreadPoolExecutor(6) as ex:
        for line in ex.map(one, expiries):
            print(line, flush=True)


# ---------------------------------------------------------------- 分析
def load_inputs():
    p = pd.read_feather(CM / f"{PERP_SYM}-4h.feather").set_index("date")["close"].rename("P")
    f = pd.read_feather(CM / f"{PERP_SYM}-funding.feather")
    r = pd.Series(f["last_funding_rate"].values, index=pd.DatetimeIndex(f["date"]))
    return p.sort_index(), r.sort_index()


def build_contract(e, perp):
    sym = f"{SYM}USD_{e.strftime('%y%m%d')}"
    fp = CM / f"{sym}-4h.feather"
    if not fp.exists():
        return sym, None
    F = pd.read_feather(fp).set_index("date")["close"].rename("F")
    df = F.to_frame().join(perp, how="inner")
    if len(df) < 50:
        return sym, None
    expiry_ts = pd.Timestamp(e, tz="UTC") + pd.Timedelta(hours=8)
    df["b"] = df["F"] / df["P"] - 1
    df["days_left"] = (expiry_ts - df.index).total_seconds() / 86400
    return sym, df[df["days_left"] >= 0]


def simulate(df, i0, s, rates):
    """逐 bar 跟踪两腿 USD 名义 PnL + funding，返回 (usd_ret, coin_ret, fund_sum, risk_min)。

    s=+1: 多永续 + 空交割；s=-1: 空永续 + 多交割。
    USD 名义 N=1；反向合约 USD 盈亏等价线性：dPnL = s·[ΔP/P] − s·[ΔF/F]。
    到期 F_T→P_T，故价格腿合计 = s·(P_T/P_0 − 1) − s·(F_T/F_0 − 1) ≈ s·b0。
    """
    seg = df.loc[i0:]
    P = seg["P"].values
    F = seg["F"].values
    P0, F0 = P[0], F[0]
    price_pnl = s * (P[-1] / P0 - 1) - s * (F[-1] / F0 - 1)
    # funding：多头永续付正费率 → s=+1 时成本
    t0, t1 = seg.index[0], seg.index[-1]
    m = (rates.index > t0) & (rates.index <= t1)
    rr = rates[m]
    # 币本位 funding 以币结算：每期成本(币) = 名义币数 × rate；按 USD 名义 1 计 = rate
    fund = -s * float(rr.sum()) if len(rr) else 0.0
    usd_ret = price_pnl + fund - FEE
    coin_ret = (1.0 + usd_ret) / (P[-1] / P0) - 1.0   # 换成 ETH 计价（本金未受 ETH 价格影响）
    risk_min = float((s * seg["b"]).min())
    n_expect = int((t1 - t0).total_seconds() / 3600 / 8)   # 8h 一次的应结算次数
    return usd_ret, coin_ret, fund, risk_min, len(rr), n_expect


def scan(expiries_list, perp, rates, entry_after=None):
    ev = []
    for e in expiries_list:
        sym, df = build_contract(e, perp)
        if df is None:
            continue
        d = df[df["days_left"] >= MIN_DAYS_LEFT]
        if entry_after is not None:
            d = d[d.index >= entry_after]
        if d.empty:
            continue
        ann = d["b"] / d["days_left"] * 365
        for s, thetas in ((1, THETAS_POS), (-1, THETAS_NEG)):
            for th in thetas:
                hit = d[s * ann >= th]
                if hit.empty:
                    continue
                i0 = hit.index[0]
                usd, coin, fund, risk, n_set, n_exp = simulate(df, i0, s, rates)
                ev.append({"sym": sym, "dir": "正" if s == 1 else "反", "theta": th,
                           "entry": i0, "year": i0.year, "expiry": e,
                           "days_left": df.loc[i0, "days_left"], "b_entry": df.loc[i0, "b"],
                           "lock_apr": s * df.loc[i0, "b"] / df.loc[i0, "days_left"] * 365,
                           "fund": fund, "usd": usd, "coin": coin, "risk_min": risk,
                           "n_settle": n_set, "n_expect": n_exp,
                           "fund_cov": (n_set / n_exp if n_exp else 1.0)})
    return pd.DataFrame(ev)


def report(events, title):
    print(f"\n=== {title} ===")
    if events.empty:
        print("  无事件")
        return
    for d in ("正", "反"):
        sub = events[events["dir"] == d]
        for th in sorted(sub["theta"].unique()):
            g = sub[sub["theta"] == th]
            print(f"{d}向 θ={th*100:.0f}%: n={len(g)}  入场基差 {g['b_entry'].mean()*100:+.2f}%  "
                  f"锁定APR {g['lock_apr'].mean()*100:+.1f}%  funding {g['fund'].mean()*100:+.3f}%  "
                  f"USD收益均值 {g['usd'].mean()*100:+.2f}%（胜率 {100*(g['usd']>0).mean():.0f}%，"
                  f"最差 {g['usd'].min()*100:+.2f}%）  ETH计价 {g['coin'].mean()*100:+.2f}%")
            for _, r in g.iterrows():
                cov = r["fund_cov"]
                warn = "" if cov >= 0.95 else f"  [WARN funding 覆盖仅 {cov*100:.0f}%]"
                print(f"    {r['sym']}[{r['dir']}] 剩{r['days_left']:.0f}天 b={r['b_entry']*100:+.2f}% "
                      f"锁定APR {r['lock_apr']*100:+.0f}% → USD {r['usd']*100:+.2f}% / "
                      f"ETH {r['coin']*100:+.2f}% (risk {r['risk_min']*100:+.2f}%){warn}")


def main():
    now = pd.Timestamp.now("UTC")
    print("=== 采集（vision cm 桶 + dapi 补尾）===")
    print(build_series(PERP_SYM, "klines", CM / f"{PERP_SYM}-4h.feather",
                       date(2020, 8, 1), (now - pd.Timedelta(days=1)).date()))
    print(build_funding_dapi(PERP_SYM, CM / f"{PERP_SYM}-funding.feather"))
    build_contracts(quarter_last_fridays(2021, 2026))
    print(build_series(PERP_SYM, "klines", CM / f"{PERP_SYM}-4h.feather",
                       date(2020, 8, 1), (now - pd.Timedelta(days=1)).date()))

    perp, rates = load_inputs()
    print(f"\n永续 {len(perp):,} 根 4h  {str(perp.index.min())[:10]} -> {str(perp.index.max())[:10]}"
          f"   最近价 {perp.iloc[-1]:,.1f}")
    print(f"funding {len(rates):,} 条  {str(rates.index.min())[:10]} -> {str(rates.index.max())[:10]}")

    expiries = quarter_last_fridays(2021, 2026)
    test_exp = [e for e in expiries if T_START <= pd.Timestamp(e, tz="UTC") < T_END]
    ev = scan(test_exp, perp, rates)
    report(ev, f"H 币本位 永续×交割 正式扫描（TEST {T_START.date()}~{T_END.date()}，fee {FEE*100:.2f}%）")

    if not ev.empty:
        print("\nfee 敏感度（USD 收益均值，全部事件）:")
        raw = ev["usd"] + FEE
        for fee in [FEE] + FEE_SENSE:
            print(f"  fee={fee*100:.2f}%: {(raw - fee).mean()*100:+.2f}%")

    # 描述区（2024-09 之后）
    desc_exp = [e for e in expiries
                if pd.Timestamp(e, tz="UTC") >= T_END and
                pd.Timestamp(e, tz="UTC") + pd.Timedelta(hours=8) < now]
    print(f"\n=== 描述区（{T_END.date()} 之后已交割合约，仅统计）===")
    for e in desc_exp:
        sym, df = build_contract(e, perp)
        if df is None or len(df) < 50:
            print(f"  {sym}: 数据不足")
            continue
        d14 = df[df["days_left"] >= MIN_DAYS_LEFT]
        ann = d14["b"] / d14["days_left"] * 365
        pos = ann.max() if len(ann) else np.nan
        neg = max(float((-ann).max()), 0.0) if len(ann) else np.nan
        print(f"  {sym}: b 均值 {df['b'].mean()*100:+.3f}%  [{df['b'].min()*100:+.2f}%, "
              f"{df['b'].max()*100:+.2f}%]  正向最优 APR {pos*100:+.0f}%  反向最优 {neg*100:+.0f}%  "
              f"ann>8% 占比 {100*(ann>0.08).mean():.0f}%")
    dd = scan(desc_exp, perp, rates, entry_after=T_END)
    report(dd, f"描述区模拟（θ=8% 回放，入场≥{T_END.date()}，未调参）")

    # 在市快照
    print("\n=== 在市合约快照 ===")
    for e in expiries:
        ets = pd.Timestamp(e, tz="UTC") + pd.Timedelta(hours=8)
        if ets <= now:
            continue
        sym, df = build_contract(e, perp)
        if df is None or df.empty:
            print(f"  {sym}: 无数据")
            continue
        last = df.iloc[-1]
        dl = last["days_left"]
        d30 = df[df.index >= df.index.max() - pd.Timedelta(days=30)]
        print(f"  {sym}: 最新 {str(df.index.max())[:16]}  b={last['b']*100:+.3f}%  剩余 {dl:.0f}天  "
              f"锁定APR {last['b']/dl*365*100:+.1f}%  近30天 b 均值 {d30['b'].mean()*100:+.3f}%")

    r90 = rates[rates.index >= now - pd.Timedelta(days=90)]
    if not r90.empty:
        print(f"\n近90天 币本位 ETHUSD_PERP funding 累计: {r90.sum()*100:+.2f}% "
              f"(年化 {r90.sum()/90*365*100:+.1f}%)  正费率占比 {100*(r90>0).mean():.0f}%")


if __name__ == "__main__":
    main()
