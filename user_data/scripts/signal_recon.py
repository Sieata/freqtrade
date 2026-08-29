"""信号一致性对账（FREEZE_FS 判据：月度实际信号 vs 回测同期预期，偏差 ≤±30%）。

回测侧：策略最新 TOP10 验证报告的交易集 → 每月每对入场数预期表。
live 侧：paper db 的 trades 表 → 实际入场数。
偏差 =（live − 预期）/ 预期，|偏差| > 30% 的月份标 FLAG——信号断供/体制漂移探测器。

用法:
  .venv/bin/python user_data/scripts/signal_recon.py --strategy FundingSqueezeV1L \
      --db user_data/tradesv3.dryrun.fs.sqlite
  .venv/bin/python user_data/scripts/signal_recon.py --strategy FundingSqueezeV1L --selftest
"""
import argparse
import json
import os
import re
import sqlite3
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
BT = ROOT / "user_data" / "backtest_results"
REPORTS = ROOT / "user_data" / "reports"
UNIVERSE = ROOT / "user_data" / "universe" / "pairs_top10.txt"

POOL = {line.split("/")[0].strip() for line in UNIVERSE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#") and "/" in line}


def expected_monthly(strategy):
    """回测预期：TOP10 验证 TEST+VAL 两腿合并的每月入场数。"""
    r = sorted(REPORTS.glob(f"validate_{strategy}_*.md"), key=lambda p: p.stat().st_mtime)[-1]
    txt = r.read_text(encoding="utf-8")
    rows = []
    for m in re.finditer(r"## \w+ × (TEST|VAL)（[^）]+）\n结果: `(backtest-result-[0-9_-]+\.zip)`", txt):
        with zipfile.ZipFile(BT / m.group(2)) as z:
            for n in z.namelist():
                if not n.endswith(".json"):
                    continue
                d = json.loads(z.read(n))
                if isinstance(d, dict) and "strategy" in d:
                    rows.extend(d["strategy"][list(d["strategy"])[0]].get("trades", []))
    df = pd.DataFrame(rows)
    df["pair_base"] = df["pair"].str.split("/").str[0]
    df = df[df["pair_base"].isin(POOL)]
    df["month"] = pd.to_datetime(df["open_date"], utc=True).dt.strftime("%Y-%m")
    return df.groupby("month").size().sort_index()


def live_monthly(db):
    con = sqlite3.connect(db)
    rows = con.execute("SELECT pair, open_date FROM trades WHERE is_open IN (0, 1)").fetchall()
    con.close()
    df = pd.DataFrame(rows, columns=["pair", "open_date"])
    df["pair_base"] = df["pair"].str.split("/").str[0]
    df = df[df["pair_base"].isin(POOL)]
    df["month"] = pd.to_datetime(df["open_date"], utc=True).dt.strftime("%Y-%m")
    return df.groupby("month").size().sort_index()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="FundingSqueezeV1L")
    ap.add_argument("--db", default=None)
    ap.add_argument("--selftest", action="store_true", help="用回测交易充当 live 验证管道（偏差应为 0）")
    args = ap.parse_args()

    exp = expected_monthly(args.strategy)
    print(f"回测预期（{args.strategy} TOP10，{len(exp)} 个月，合计 {exp.sum()} 信号）:")
    for k, v in exp.items():
        print(f"  {k}: {v}")

    if args.selftest:
        live = exp  # 管道自检: live=预期 → 全部 0% 偏差
        print("\n[selftest] live 侧 = 回测交易（管道验证）")
    else:
        db = args.db or str(ROOT / "user_data" / "tradesv3.dryrun.fs.sqlite")
        if not os.path.exists(db):
            print(f"\n[live] db 不存在: {db}（paper 未启动或不在本机）——仅打印预期表")
            return
        live = live_monthly(db)

    print("\n对账（|偏差|>30% = FLAG）:")
    flags = 0
    for k in sorted(set(exp.index) | set(live.index)):
        e, l = int(exp.get(k, 0)), int(live.get(k, 0))
        if e == 0:
            dev = 0.0 if l == 0 else float("inf")
        else:
            dev = (l - e) / e * 100
        mark = ""
        if e > 0 and abs(dev) > 30:
            mark = "  ← FLAG"
            flags += 1
        print(f"  {k}: 预期 {e:>3}  实际 {l:>3}  偏差 {dev:+7.0f}%{mark}")
    print(f"\nFLAG 月份: {flags} 个" + ("（正常）" if flags == 0 else "（检查 funding 数据链路/体制漂移）"))


if __name__ == "__main__":
    main()
