#!/usr/bin/env python
"""sizing_sweep.py —— 仓位 / 复利 / 并发槽位 扫描器

动机（2026-09-21）：
    V2 实际仓位设计是 max_open_trades=1 + stake_amount=unlimited + tradable_balance_ratio=0.99
    （单仓 99% 钱包复利）。用 `--max-open-trades 10` 跑回测会**替换掉仓位设计**，
    从而完全测不到仓位轴——必须显式做仓位扫描。

本脚本一次跑完三组曲线并打印对照表（含统计质量指标）：
    A. 仓位大小曲线：tradable_balance_ratio ∈ {0.99, 0.50, 0.25, 0.10} + 固定 $ 本金
    B. 并发槽位曲线：max_open_trades ∈ {1, 3, 5, 10}（固定本金）
    C. 分散度曲线：固定 ratio（=总敞口），扫 max_open_trades —— "集中 vs 分散"

**关键公式（freqtrade/wallets.py `_calculate_unlimited_stake_amount`）**：
    stake = (available + tied_up) / max_open_trades，上限 = available
于是 `tradable_balance_ratio` 决定**总敞口**，`max_open_trades` 只决定**分散度**——
同一 ratio 下把槽位从 1 开到 10，是把同样的敞口摊到 10 个品种，而不是加仓。
A/B 两组用的是同一 `ratio`（config 默认 0.99），B 用固定本金绕开了这一点，故 C 单独补齐。

用法：
    ./.venv/Scripts/python.exe user_data/scripts/sizing_sweep.py \
        --strategy WeekendReverseV2 --timerange 20220101-20240828 --pool top10

    # 只跑一组 / 自定义档位
    ... --only dispersion --ratio 0.99 --slots 1,2,3,5,10
    ... --only sizing --at-slots 10 --ratios 0.99,0.5,0.25,0.1
    ... --only slots --slots 1,3,5

注意：
    - 比例仓位通过临时 config（只改 tradable_balance_ratio）实现，
      因为 CLI 没有 --tradable-balance-ratio 参数。
    - 固定本金档用 --stake-amount；wallet 需 ≥ 本金 × 并发槽 × 1.2，否则 freqtrade 报
      "Starting balance smaller than stake_amount"。
    - CAGR / 最大回撤 是**钱包口径**；比较不同仓位时不要单看 CAGR/最大回撤——
      复利下该比值系统性偏袒大仓位（CAGR 是增长倍数的凹函数、回撤近似线性于仓位）。
      请与 PF / SQN / Sharpe / p 值 并列判断。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
PY = sys.executable
DEFAULT_CFG = REPO / "user_data" / "config_perpetual.json"

RATIOS = [0.99, 0.50, 0.25, 0.10]
SLOTS = [1, 3, 5, 10]

# 从 freqtrade 摘要表里抓取的指标（左列名 → 别名）
METRICS = {
    "绝对利润": "Absolute profit",
    "总收益%": "Total profit %",
    "CAGR%": "CAGR %",
    "最大回撤%": "Max % of account underwater",
    "PF": "Profit factor",
    "SQN": "SQN",
    "Sharpe": "Sharpe (closed trades)",
    "p值": "Mean profit p-value",
    "笔数": "Total/Daily Avg Trades",
    "拒单": "Rejected Entry signals",
    "最差日": "Worst day",
    "最好日": "Best day",
    "均仓": "Avg. stake amount",
    "连亏": "Max Consecutive Wins / Loss",
}


def pairs_from_pool(pool: str) -> list[str]:
    path = REPO / "user_data" / "universe" / f"pairs_{pool}.txt"
    if not path.exists():
        raise SystemExit(f"币池文件不存在: {path}")
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.split("#")[0].strip()
        if code:
            out.append(code)
    return out


def num(value: str) -> float:
    try:
        return float(str(value).split()[0].replace("%", "").replace(",", ""))
    except Exception:
        return float("nan")


def parse_summary(log: pathlib.Path) -> dict | None:
    text = log.read_text(encoding="utf-8", errors="replace")
    if "unrecognized arguments" in text or "Traceback" in text:
        return None
    table: dict[str, str] = {}
    for line in text.splitlines():
        cells = line.split("│")
        if len(cells) < 3:
            continue
        key = cells[1].strip()
        if key:
            table[key] = cells[2].strip()
    if "Absolute profit" not in table:
        return None
    return {alias: table.get(src, "-") for alias, src in METRICS.items()}


def run_backtest(strategy, timerange, pairs, wallet, extra, cfg, logdir, tag):
    cmd = [
        PY, "-m", "freqtrade", "backtesting",
        "--config", str(cfg),
        "--strategy", strategy,
        "--timerange", timerange,
        "--pairs", *pairs,
        "--cache", "none",
        "--dry-run-wallet", str(wallet),
        *extra,
    ]
    log = logdir / f"{tag}.log"
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        subprocess.run(cmd, cwd=REPO, stdout=fh, stderr=subprocess.STDOUT)
    return log


def make_ratio_config(ratio: float, logdir: pathlib.Path) -> pathlib.Path:
    cfg = json.loads(DEFAULT_CFG.read_text(encoding="utf-8"))
    cfg["tradable_balance_ratio"] = ratio
    out = logdir / f"cfg_ratio{ratio}.json"
    out.write_text(json.dumps(cfg, indent=1, ensure_ascii=False), encoding="utf-8")
    return out


def print_table(title, rows, note=""):
    print(f"\n### {title}")
    if note:
        print(f"    {note}")
    cols = ["配置", "笔数", "拒单", "绝对利润", "CAGR%", "最大回撤%", "PF", "SQN", "Sharpe", "p值"]
    widths = [18, 12, 6, 13, 8, 10, 6, 6, 7, 11]
    head = "".join(c.ljust(w) for c, w in zip(cols, widths))
    print(head)
    print("-" * len(head))
    for name, d in rows:
        if d is None:
            print(f"{name:<18}{'FAILED (见日志)':<40}")
            continue
        vals = [name,
                d["笔数"].split("/")[0].strip(),
                d["拒单"],
                f"{num(d['绝对利润']):,.0f}",
                f"{num(d['CAGR%']):.1f}",
                f"{num(d['最大回撤%']):.2f}",
                f"{num(d['PF']):.2f}",
                f"{num(d['SQN']):.2f}",
                f"{num(d['Sharpe']):.2f}",
                d["p值"]]
        print("".join(str(v).ljust(w) for v, w in zip(vals, widths)))


def main():
    ap = argparse.ArgumentParser(description="仓位/复利/并发槽位扫描")
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--timerange", required=True)
    ap.add_argument("--pool", default="top10", help="币池名（user_data/universe/pairs_<pool>.txt）")
    ap.add_argument("--only", choices=["all", "sizing", "slots", "dispersion"], default="all")
    ap.add_argument("--ratios", default="", help="覆盖仓位比例档，逗号分隔，如 0.99,0.5")
    ap.add_argument("--slots", default="", help="覆盖并发槽位档，逗号分隔，如 1,3,5")
    ap.add_argument("--ratio", type=float, default=0.99, help="dispersion 模式的固定总敞口")
    ap.add_argument("--at-slots", type=int, default=1, help="sizing 模式固定的槽位数")
    ap.add_argument("--wallet", type=float, default=0, help="钱包本金；0=按档位自动（本金×槽位×1.2）")
    ap.add_argument("--fixed-stake", type=float, default=1000.0, help="固定本金档的单笔金额")
    ap.add_argument("--keep-logs", default="", help="日志目录；默认临时目录")
    args = ap.parse_args()

    pairs = pairs_from_pool(args.pool)
    ratios = [float(x) for x in args.ratios.split(",") if x.strip()] or RATIOS
    slots = [int(x) for x in args.slots.split(",") if x.strip()] or SLOTS

    logdir = pathlib.Path(args.keep_logs) if args.keep_logs else pathlib.Path(tempfile.mkdtemp(prefix="sizing_sweep_"))
    logdir.mkdir(parents=True, exist_ok=True)
    print(f"策略={args.strategy}  区间={args.timerange}  池={args.pool}({len(pairs)} 品种)  日志={logdir}")

    if args.only in ("all", "sizing"):
        wallet = args.wallet or 10_000.0
        rows = []
        for ratio in ratios:
            cfg = DEFAULT_CFG if ratio >= 0.99 else make_ratio_config(ratio, logdir)
            tag = f"ratio{ratio}_slot{args.at_slots}"
            label = f"{ratio:.0%} 复利" + ("（现行默认）" if ratio >= 0.99 else "")
            log = run_backtest(args.strategy, args.timerange, pairs, wallet,
                               ["--max-open-trades", str(args.at_slots)], cfg, logdir, tag)
            rows.append((label, parse_summary(log)))
        log = run_backtest(args.strategy, args.timerange, pairs, wallet,
                           ["--max-open-trades", str(args.at_slots),
                            "--stake-amount", str(args.fixed_stake)],
                           DEFAULT_CFG, logdir, f"fixed{args.at_slots}")
        rows.append((f"固定 ${args.fixed_stake:,.0f}", parse_summary(log)))
        print_table(f"A. 仓位大小曲线（{args.at_slots} 槽，钱包 {wallet:,.0f} 口径）", rows,
                    "注意：CAGR/最大回撤 复利下偏袒大仓位，须与 PF/SQN/p 值 并列判断")

    if args.only in ("all", "slots"):
        # 关键：全档共用同一钱包，否则利润与回撤百分比的分母不同、不可比。
        # 钱包 ≥ 最大槽位 × 本金 × 1.2，才能让每档都跑满槽位。
        wallet = args.wallet or args.fixed_stake * max(slots) * 1.2
        rows = []
        for n in slots:
            log = run_backtest(args.strategy, args.timerange, pairs, wallet,
                               ["--max-open-trades", str(n), "--stake-amount", str(args.fixed_stake)],
                               DEFAULT_CFG, logdir, f"slot{n}")
            rows.append((f"{n} 槽", parse_summary(log)))
        print_table(f"B. 并发槽位曲线（固定 ${args.fixed_stake:,.0f}/笔，钱包 {wallet:,.0f} 全档共用）", rows,
                    "拒单数 = 因槽位满而放弃的信号数；边际收益通常在 3~5 槽后衰减")

    if args.only in ("all", "dispersion"):
        # 固定 ratio = 固定总敞口，只改分散度。这才是"集中 vs 分散"的干净对照：
        # n 槽时每笔 = 钱包×ratio/n，n 笔满仓时总敞口仍 = ratio。
        ratio = args.ratio
        cfg = DEFAULT_CFG if ratio >= 0.99 else make_ratio_config(ratio, logdir)
        wallet = args.wallet or 10_000.0
        rows = []
        for n in slots:
            log = run_backtest(args.strategy, args.timerange, pairs, wallet,
                               ["--max-open-trades", str(n)], cfg, logdir, f"disp{n}")
            rows.append((f"{n} 槽 × {ratio / n:.1%}/笔", parse_summary(log)))
        print_table(f"C. 分散度曲线（固定总敞口 {ratio:.0%}，钱包 {wallet:,.0f}）", rows,
                    "同敞口下集中 vs 分散；实际敞口 < 名义敞口（信号不足时槽位不填满）")

    print(f"\n日志目录：{logdir}")


if __name__ == "__main__":
    main()
