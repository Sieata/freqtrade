"""数据接缝完整性校验：最新时间戳分布 + 缺口 + 重复（2026-09-21 新增）。

2026-10-22 增加 metrics/OI 校验：OIFlushV2 回测直读 `-4h-metrics.feather`、
live 累积器写 `-oi_live.feather`，此前两者都不在校验范围内（哑区）。

用法:
  .venv/bin/python user_data/scripts/data_check.py                          # 默认 top10,core,volume
  .venv/bin/python user_data/scripts/data_check.py --pools top5,top2
  .venv/bin/python user_data/scripts/data_check.py --pairs BTC/USDT:USDT,ETH/USDT:USDT
  .venv/bin/python user_data/scripts/data_check.py --timeframes 4h --since 2026-01-01
  .venv/bin/python user_data/scripts/data_check.py --no-metrics             # 跳过 metrics/OI 校验

退出码: 0 = 全部干净；1 = 有缺口/重复/文件缺失（可直接作 cron 告警条件）。
与 ensure-data.sh / refresh_all.sh 配套：数据刷新后跑一次，证明接缝干净。
"""
import argparse
import glob
import os
import sys
from collections import Counter

import pandas as pd


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from research_lib import load_pool as _load_pool  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UNIVERSE = os.path.join(ROOT, "user_data", "universe")
DATA = os.path.join(ROOT, "user_data", "data", "binance", "futures")
METRICS = os.path.join(ROOT, "user_data", "data", "binance", "futures_metrics")
STEP = {"4h": 4, "1d": 24, "1h": 1, "8h": 8, "15m": 0.25, "5m": 5 / 60}

# 已知无法回补的空洞（源端缺数据）。不阻断校验，但每次打印提醒。
# 格式: (品种, 断点前, 断点后, 数据源, 原因)
KNOWN_GAPS = [
    # vision 日包缺件（404，重试无用）
    ("ZEC", "2023-12-12 20:00", "2023-12-14 00:00", "metrics", "vision 缺 2023-12-13 日包"),
    ("XMR", "2023-12-16 20:00", "2023-12-19 00:00", "metrics", "vision 缺 2023-12-17/18 日包"),
    # 2024-02-16 日包被截断（只到 13:30，正常应到 23:55），全品种一致 → 12h 缺口
    ("*", "2024-02-16 12:00", "2024-02-17 00:00", "metrics", "vision 2024-02-16 日包截断至 13:30"),
    # OI 累积器停跑，API 只留 30 天
    ("*", "2026-08-29 08:00", "2026-09-07 16:00", "oi_live", "累积器停跑 9 天，API 只留 30 天"),
]


def load_pool(name):
    try:
        return [p for p, _ in _load_pool(name)]
    except FileNotFoundError:
        print(f"[!] 池文件不存在: {os.path.join(UNIVERSE, f'pairs_{name}.txt')}")
        return []


def collect_pairs(args):
    pairs, seen = [], set()

    def add(p):
        if p and p not in seen:
            seen.add(p)
            pairs.append(p)

    if args.pairs:
        for p in args.pairs.split(","):
            add(p.strip())
    for name in (args.pools.split(",") if args.pools else ["top10", "core", "volume"]):
        for p in load_pool(name.strip()):
            add(p)
    return pairs


def fname(pair, tf):
    return os.path.join(DATA, pair.replace("/", "_").replace(":", "_") + f"-{tf}-futures.feather")


def metrics_fname(pair, kind):
    """kind = 'metrics'（回测源，vision 日包降采样）或 'oi_live'（live 累积器）。"""
    return os.path.join(METRICS, pair.replace("/", "_").replace(":", "_") + f"-4h-{kind}.feather")


def check_metrics(pairs, since=None):
    """校验 OIFlushV2 依赖的两套 4h OI 数据。返回问题列表（空 = 干净）。"""
    probs = []
    for kind, label in (("metrics", "回测 metrics"), ("oi_live", "live OI 累积")):
        rows = []
        for pair in pairs:
            f = metrics_fname(pair, kind)
            base = pair.split("/")[0]
            if not os.path.exists(f):
                # oi_live 只对累积器覆盖的品种存在，缺失不算错（尚未开始累积）
                if kind == "oi_live":
                    continue
                probs.append((label, base, "文件缺失"))
                continue
            t = pd.to_datetime(pd.read_feather(f)["date"], utc=True).sort_values().reset_index(drop=True)
            if since:
                t = t[t >= pd.Timestamp(since, tz="UTC")].reset_index(drop=True)
            if t.empty:
                continue
            gaps = t.diff().dropna()
            bad = gaps[gaps > pd.Timedelta(hours=4) * 1.5]
            dups = int(t.duplicated().sum())
            rows.append((base, t.min(), t.max(), len(t), len(bad), dups))
            if dups:
                probs.append((label, base, f"{dups} 处重复"))
            for pos in bad.index:
                lo_s, hi_s = str(t.iloc[pos - 1])[:16], str(t.iloc[pos])[:16]
                known = any(
                    k[3] == kind and k[0] in (base, "*") and k[1] == lo_s and k[2] == hi_s
                    for k in KNOWN_GAPS
                )
                if not known:
                    probs.append((label, base, f"未知缺口 {lo_s} -> {hi_s}"))
        if rows:
            print(f"== {label}（4h）==")
            for base, lo, hi, n, ngap, dup in rows:
                flag = "" if ngap == 0 else f"  [{ngap} 处缺口]"
                print(f"   {base:<6} n={n:<6} {str(lo)[:10]} -> {str(hi)[:16]}{flag}")
            print()
    # 已知空洞：只提醒不阻断
    shown = False
    for base, lo, hi, src, why in KNOWN_GAPS:
        if base == "*" or any(p.split("/")[0] == base for p in pairs):
            print(f"   [已知] {src:<8} {base:<4} {lo} -> {hi}（{why}，不可回补）")
            shown = True
    if shown:
        print()
    return probs


def main():
    ap = argparse.ArgumentParser(description="数据接缝完整性校验")
    ap.add_argument("--pools", default="top10,core,volume",
                    help="逗号分隔的池名（pairs_<name>.txt），默认 top10,core,volume")
    ap.add_argument("--pairs", default=None, help="额外显式指定品种，逗号分隔")
    ap.add_argument("--timeframes", default="4h,1d", help="默认 4h,1d")
    ap.add_argument("--since", default=None, help="只校验该日期（UTC）之后的区段，如 2026-01-01")
    ap.add_argument("--no-metrics", action="store_true",
                    help="跳过 metrics/OI 校验（OIFlushV2 回测与 live 数据源）")
    args = ap.parse_args()

    pairs = collect_pairs(args)
    if not pairs:
        print("[!] 没有可用品种")
        sys.exit(1)
    print(f"校验 {len(pairs)} 个品种 × {args.timeframes}（--since {args.since or '全历史'}）\n")

    problems = []
    for tf in args.timeframes.split(","):
        step = pd.Timedelta(hours=STEP[tf])
        latest, tf_prob = {}, []
        for p in pairs:
            f = fname(p, tf)
            if not os.path.exists(f):
                tf_prob.append((p, "文件缺失"))
                continue
            t = pd.to_datetime(pd.read_feather(f)["date"], utc=True).sort_values()
            if args.since:
                t = t[t >= pd.Timestamp(args.since, tz="UTC")]
                if t.empty:
                    continue
            latest[p] = t.max()
            gaps = int((t.diff().dropna() > step * 1.5).sum())
            dups = int(t.duplicated().sum())
            if gaps or dups:
                tf_prob.append((p, f"{gaps} 处缺口 / {dups} 处重复"))
        dist = Counter(str(v) for v in latest.values())
        print(f"== {tf} 最新时间戳分布 ==")
        for ts, n in sorted(dist.items(), reverse=True):
            print(f"   {ts}   {n} 个品种")
        if tf_prob:
            for p, msg in tf_prob:
                print(f"   ! {p:<22} {msg}")
            problems += [(tf,) + x for x in tf_prob]
        else:
            print("   无缺口、无重复")
        print()

    if not args.no_metrics:
        problems += [(m, p, msg) for m, p, msg in check_metrics(pairs, args.since)]

    if problems:
        print(f"[x] 接缝校验不通过：{len(problems)} 处问题")
        sys.exit(1)
    print("[ok] 接缝校验全部通过")


if __name__ == "__main__":
    main()
