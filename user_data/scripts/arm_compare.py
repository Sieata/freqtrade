"""两回测臂对照：逐年 PnL / 品种分布 / 持仓重叠 / 日 PnL 相关 / 出场结构 / 尾部风险。

用法: .venv/bin/python user_data/scripts/arm_compare.py <zipA> <zipB> [--names A,B] [--stake 1000]

每个 zip 取第一个策略的 trades 明细做臂间对照（独立口径 $stake/笔，pp = $利润/stake*100）：
  1) 总览（笔数/总$/均值/胜率/PF）与持仓区间；
  2) 逐年 PnL 对照（按开仓年份归组）；
  3) 品种分布对照；
  4) 重叠结构：同品种同日开仓比例、持仓窗口相交（同品种双臂共仓天数）；
  5) 日 PnL（按平仓日归组）Pearson 相关；
  6) exit_reason 分布对照；
  7) 各臂最差 5 笔（尾部风险形态）。
"""
import argparse
import json
import sys
import zipfile

import pandas as pd


def _naive(x):
    t = pd.Timestamp(x)
    return t.tz_localize(None) if t.tzinfo is None else t.tz_convert(None)


def load_arm(path):
    """从回测 zip 读第一个含 strategy dict 的 json，返回 (策略名, trades DataFrame)。"""
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if not n.endswith(".json"):
                continue
            try:
                d = json.loads(z.read(n))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not (isinstance(d, dict) and isinstance(d.get("strategy"), dict) and d["strategy"]):
                continue
            name, s = next(iter(d["strategy"].items()))
            rows = [{
                "pair": t["pair"],
                "open": _naive(t["open_date"]),
                "close": _naive(t["close_date"]),
                "profit": float(t["profit_abs"]),
                "ratio": float(t.get("profit_ratio", 0.0) or 0.0),
                "exit": t.get("exit_reason", "?"),
            } for t in s.get("trades", [])]
            df = pd.DataFrame(rows)
            if len(df):
                df = df.sort_values("open").reset_index(drop=True)
            return name, df
    raise SystemExit(f"{path}: 未找到 strategy/trades")


def overview(df, label):
    n = len(df)
    if n == 0:
        print(f"{label}: 0 笔")
        return
    total = df["profit"].sum()
    wins = df.loc[df.profit > 0, "profit"].sum()
    losses = df.loc[df.profit < 0, "profit"].sum()
    pf = wins / abs(losses) if losses < 0 else float("inf")
    print(f"{label}: n={n} 总${total:+,.0f} 均值${total / n:+,.1f} "
          f"胜率{(df.profit > 0).mean() * 100:.1f}% PF={pf:.2f}")
    print(f"  持仓区间: {df.open.min()} → {df.close.max()}")


def yearly_table(dfa, dfb, la, lb, stake):
    years = sorted(set(dfa["open"].dt.year) | set(dfb["open"].dt.year))
    ga = dfa.groupby(dfa["open"].dt.year)["profit"].agg(["count", "sum"])
    gb = dfb.groupby(dfb["open"].dt.year)["profit"].agg(["count", "sum"])
    print("\n== 逐年 PnL（按开仓年份，$ / pp = $÷stake×100） ==")
    print(f"{'年份':<6} {la + ' n':>7} {la + ' $':>10} {la + ' pp':>9}   "
          f"{lb + ' n':>7} {lb + ' $':>10} {lb + ' pp':>9}")
    for y in years:
        ca, sa = (ga.loc[y, "count"], ga.loc[y, "sum"]) if y in ga.index else (0, 0.0)
        cb, sb = (gb.loc[y, "count"], gb.loc[y, "sum"]) if y in gb.index else (0, 0.0)
        print(f"{y:<6} {ca:>7} {sa:>+10,.0f} {sa / stake * 100:>+9.1f}   "
              f"{cb:>7} {sb:>+10,.0f} {sb / stake * 100:>+9.1f}")
    print(f"{'合计':<6} {len(dfa):>7} {dfa.profit.sum():>+10,.0f} {dfa.profit.sum() / stake * 100:>+9.1f}   "
          f"{len(dfb):>7} {dfb.profit.sum():>+10,.0f} {dfb.profit.sum() / stake * 100:>+9.1f}")


def symbol_table(dfa, dfb, la, lb):
    ga = dfa.groupby("pair")["profit"].agg(["count", "sum"])
    gb = dfb.groupby("pair")["profit"].agg(["count", "sum"])
    pairs = sorted(set(ga.index) | set(gb.index))
    print("\n== 品种分布（n / $） ==")
    print(f"{'pair':<20} {la + ' n':>8} {la + ' $':>9}   {lb + ' n':>8} {lb + ' $':>9}")
    for p in pairs:
        ca, sa = (ga.loc[p, "count"], ga.loc[p, "sum"]) if p in ga.index else (0, 0.0)
        cb, sb = (gb.loc[p, "count"], gb.loc[p, "sum"]) if p in gb.index else (0, 0.0)
        mark = "  ←A亏" if sa < 0 and ca > 0 else ("  ←B亏" if sb < 0 and cb > 0 else "")
        print(f"{p:<20} {ca:>8} {sa:>+9,.0f}   {cb:>8} {sb:>+9,.0f}{mark}")


def overlap(dfa, dfb, la, lb):
    ka = set(zip(dfa["pair"], dfa["open"].dt.normalize()))
    kb = set(zip(dfb["pair"], dfb["open"].dt.normalize()))
    common = ka & kb
    a_in = sum(1 for k in zip(dfa["pair"], dfa["open"].dt.normalize()) if k in kb)
    b_in = sum(1 for k in zip(dfb["pair"], dfb["open"].dt.normalize()) if k in ka)
    print("\n== 入场重叠 ==")
    print(f"同品种同日开仓 key 数: {len(common)}；"
          f"{la} 的交易落在 {lb} 当日同品种开仓: {a_in}/{len(dfa)} ({a_in / len(dfa) * 100:.1f}%)；"
          f"{lb} 反向: {b_in}/{len(dfb)} ({b_in / len(dfb) * 100:.1f}%)")

    # 持仓窗口相交（同品种）
    inter = 0
    for p in set(dfa["pair"]) & set(dfb["pair"]):
        aa = dfa[dfa.pair == p]
        bb = dfb[dfb.pair == p]
        for _, ra in aa.iterrows():
            for _, rb in bb.iterrows():
                if ra["open"] <= rb["close"] and rb["open"] <= ra["close"]:
                    inter += 1
    # 同品种双臂共仓（按天）
    def day_sets(df):
        s = set()
        for _, r in df.iterrows():
            d = r["open"].normalize()
            while d <= r["close"]:
                s.add((r["pair"], d))
                d += pd.Timedelta(days=1)
        return s
    both = day_sets(dfa) & day_sets(dfb)
    print(f"持仓窗口相交(笔对): {inter}；同品种双臂共仓天数: {len(both)}")


def daily_corr(dfa, dfb, la, lb):
    da = dfa.groupby(dfa["close"].dt.normalize())["profit"].sum()
    db = dfb.groupby(dfb["close"].dt.normalize())["profit"].sum()
    idx = da.index.union(db.index)
    ja = da.reindex(idx).fillna(0.0)
    jb = db.reindex(idx).fillna(0.0)
    r_all = ja.corr(jb)
    nz = (ja != 0) | (jb != 0)
    r_nz = ja[nz].corr(jb[nz]) if nz.sum() > 2 else float("nan")
    print("\n== 日 PnL 相关（按平仓日） ==")
    print(f"全区间日历对齐(含双零日): r={r_all:+.3f}（{len(idx)} 天，其中任一臂有平仓 {int(nz.sum())} 天）")
    print(f"仅非零日: r={r_nz:+.3f}")
    top = (ja + jb).nlargest(5)
    print("联合日 PnL 最大 5 天:")
    for d, v in top.items():
        print(f"  {d.date()}: {la} {ja[d]:+.0f} / {lb} {jb[d]:+.0f} / 合计 {v:+.0f}")


def exits_and_tails(dfa, dfb, la, lb, stake, top=5):
    print("\n== exit_reason 分布 ==")
    for label, df in ((la, dfa), (lb, dfb)):
        g = df.groupby("exit")["profit"].agg(["count", "sum"])
        parts = ", ".join(f"{ix} n={int(r['count'])} ${r['sum']:+,.0f}" for ix, r in g.iterrows())
        print(f"{label}: {parts}")
    print(f"\n== 各臂最差 {top} 笔（尾部形态） ==")
    for label, df in ((la, dfa), (lb, dfb)):
        w = df.nsmallest(top, "profit")
        rows = "; ".join(
            f"{r['pair'].split('/')[0]} {r['open'].date()} {r['profit']:+.0f}$"
            f"({r['ratio'] * 100:+.0f}%) {r['exit']}" for _, r in w.iterrows())
        print(f"{label}: {rows}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zipA")
    ap.add_argument("zipB")
    ap.add_argument("--names", default=None, help="臂标签，逗号分隔，默认用策略名截短")
    ap.add_argument("--stake", type=float, default=1000.0)
    a = ap.parse_args()
    na, dfa = load_arm(a.zipA)
    nb, dfb = load_arm(a.zipB)
    if a.names:
        la, lb = [x.strip() for x in a.names.split(",")]
    else:
        la, lb = na[:16], nb[:16]
    print(f"臂A: {a.zipA} → {na} ({len(dfa)} 笔)")
    print(f"臂B: {a.zipB} → {nb} ({len(dfb)} 笔)")
    overview(dfa, la)
    overview(dfb, lb)
    yearly_table(dfa, dfb, la, lb, a.stake)
    symbol_table(dfa, dfb, la, lb)
    overlap(dfa, dfb, la, lb)
    daily_corr(dfa, dfb, la, lb)
    exits_and_tails(dfa, dfb, la, lb, a.stake)


if __name__ == "__main__":
    main()
