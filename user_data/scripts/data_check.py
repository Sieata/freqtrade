"""数据接缝完整性校验：最新时间戳分布 + 缺口 + 重复（2026-09-21 新增）。

用法:
  .venv/bin/python user_data/scripts/data_check.py                          # 默认 top10,core,volume
  .venv/bin/python user_data/scripts/data_check.py --pools top5,top2
  .venv/bin/python user_data/scripts/data_check.py --pairs BTC/USDT:USDT,ETH/USDT:USDT
  .venv/bin/python user_data/scripts/data_check.py --timeframes 4h --since 2026-01-01

退出码: 0 = 全部干净；1 = 有缺口/重复/文件缺失（可直接作 cron 告警条件）。
与 ensure-data.sh / refresh_all.sh 配套：数据刷新后跑一次，证明接缝干净。
"""
import argparse
import glob
import os
import sys
from collections import Counter

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UNIVERSE = os.path.join(ROOT, "user_data", "universe")
DATA = os.path.join(ROOT, "user_data", "data", "binance", "futures")
STEP = {"4h": 4, "1d": 24, "1h": 1, "8h": 8, "15m": 0.25, "5m": 5 / 60}


def load_pool(name):
    path = os.path.join(UNIVERSE, f"pairs_{name}.txt")
    if not os.path.exists(path):
        print(f"[!] 池文件不存在: {path}")
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.split("#")[0].strip()
        if line:
            out.append(line)
    return out


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


def main():
    ap = argparse.ArgumentParser(description="数据接缝完整性校验")
    ap.add_argument("--pools", default="top10,core,volume",
                    help="逗号分隔的池名（pairs_<name>.txt），默认 top10,core,volume")
    ap.add_argument("--pairs", default=None, help="额外显式指定品种，逗号分隔")
    ap.add_argument("--timeframes", default="4h,1d", help="默认 4h,1d")
    ap.add_argument("--since", default=None, help="只校验该日期（UTC）之后的区段，如 2026-01-01")
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

    if problems:
        print(f"[x] 接缝校验不通过：{len(problems)} 处问题")
        sys.exit(1)
    print("[ok] 接缝校验全部通过")


if __name__ == "__main__":
    main()
