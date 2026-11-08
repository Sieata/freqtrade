#!/usr/bin/env python3
"""sync_oi_to_metrics.py — 把 live OI 累积器的尾巴并入回测源 metrics feather。

背景（2026-11-08，启动 OI paper 时发现的数据管道缺口）：
  - OIFlushV2 策略 populate_indicators 直读 `-4h-metrics.feather`（vision 日包降采样，
    含 oi_usd + 多空比等列）；该源因 vision 日包发布滞后 1–2 天而停在 T-2。
  - `oi_accumulate.py` 每日从 API 拉 OI 写入 `-4h-oi_live.feather`（只有 date/oi_usd），
    可到 T（当日）。
  - 结果：paper 实盘 bot 读到的是 T-2 的 OI，分位计算滞后 2 天 —— live 信号失真。

本工具只做「追加尾巴」：把 oi_live 中 **晚于 metrics 最后一行日期** 的行（日期不在 metrics 里）
按 oi_usd 追加进 metrics feather，其余列（多空比等）留 NaN。**绝不改写历史重叠行**，
保证回测历史逐字节可复现（与 vision 纯口径一致），只把 live 尾段补新鲜。

策略只用 oi_usd（不读 top_ls_*），故尾部多空比为 NaN 不影响信号。

用法:
  .venv/bin/python user_data/scripts/sync_oi_to_metrics.py [PAIR...]   # 缺省 TOP10
  .venv/bin/python user_data/scripts/sync_oi_to_metrics.py --dry-run
每日 OI 累积器之后跑一次即可（refresh_all.sh 已集成）。幂等：无新数据时报 skipped。
"""
import argparse
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
METRICS = os.path.join(ROOT, "user_data", "data", "binance", "futures_metrics")

TOP10 = ["BTC", "ETH", "BNB", "XRP", "SOL", "TRX", "HYPE", "ZEC", "DOGE", "XMR"]


def metrics_path(sym):
    return os.path.join(METRICS, f"{sym}_USDT_USDT-4h-metrics.feather")


def oi_live_path(sym):
    return os.path.join(METRICS, f"{sym}_USDT_USDT-4h-oi_live.feather")


def atomic_to_feather(df, path):
    tmp = path + ".tmp"
    df.to_feather(tmp)
    os.replace(tmp, path)


def sync_one(sym, dry_run=False):
    mp, op = metrics_path(sym), oi_live_path(sym)
    if not os.path.exists(mp):
        return f"[skip] {sym}: no metrics feather"
    if not os.path.exists(op):
        return f"[skip] {sym}: no oi_live feather"
    m = pd.read_feather(mp)
    o = pd.read_feather(op)
    if "date" not in m.columns or "date" not in o.columns or "oi_usd" not in o.columns:
        return f"[skip] {sym}: unexpected columns"
    m_last = m["date"].max()
    o_last = o["date"].max()
    # 只在 oi_live 晚于 metrics 时补尾（严格晚于，不重叠历史）
    tail = o[o["date"] > m_last].copy()
    if tail.empty:
        return f"[ok] {sym}: 无需补（metrics 已到 {m_last}，oi_live 到 {o_last}）"
    # 对齐列：metrics 缺的列（如多空比）留 NaN
    for col in m.columns:
        if col not in tail.columns:
            tail[col] = pd.NA
    # 类型对齐：让 pd.concat 不因 dtype 报错
    tail = tail[m.columns]
    merged = pd.concat([m, tail], ignore_index=True).sort_values("date").reset_index(drop=True)
    if dry_run:
        return f"[dry] {sym}: 将追加 {len(tail)} 行 {tail['date'].min()} -> {tail['date'].max()}"
    atomic_to_feather(merged, mp)
    return f"[ok] {sym}: +{len(tail)} 行 {tail['date'].min()} -> {tail['date'].max()}（metrics 现到 {merged['date'].max()}）"


def main():
    ap = argparse.ArgumentParser(description="把 live OI 尾巴并入回测源 metrics feather")
    ap.add_argument("pairs", nargs="*", default=None, help="品种符号（BTC 等），缺省 TOP10")
    ap.add_argument("--dry-run", action="store_true", help="只报告不写入")
    args = ap.parse_args()
    syms = args.pairs or TOP10
    if not os.path.isdir(METRICS):
        print(f"[!] metrics dir not found: {METRICS}", file=sys.stderr)
        sys.exit(1)
    changed = 0
    for sym in syms:
        r = sync_one(sym, dry_run=args.dry_run)
        print(r)
        if r.startswith("[ok]") and "+" in r:
            changed += 1
    print(f"\n完成：{changed} 品种补尾" + ("（dry-run，未写盘）" if args.dry_run else ""))


if __name__ == "__main__":
    main()
