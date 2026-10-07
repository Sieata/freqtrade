"""
WeekendReverseV2 批量变体扫描

生成 N 个独立策略文件(参数内嵌), 用 freqtrade --strategy-list 一次进程回测,
解析结果到 CSV。真实回测口径, 不含模拟器偏差。

用法:
  .venv\\Scripts\\python.exe user_data/scripts/wrv2_batch.py
"""

import csv
import glob
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)

BATCH_DIR = os.path.join(ROOT, "user_data", "backtest_results", "_dev", "strat_batch")
RESULT_DIR = os.path.join(ROOT, "user_data", "backtest_results", "_dev")
PAIRS = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT",
    "ZEC/USDT:USDT", "BANK/USDT:USDT", "CYS/USDT:USDT", "HYPE/USDT:USDT",
]

TEMPLATE = '''"""
Auto-generated variant {name} - do not edit
"""
from datetime import timedelta

import talib.abstract as ta
from pandas import DataFrame
from freqtrade.strategy import IStrategy, PairLocks


class {name}(IStrategy):
    INTERFACE_VERSION = 3
    can_short: bool = False
    timeframe = "4h"
    startup_candle_count: int = 250

    stoploss = {stoploss}
    trailing_stop = True
    trailing_stop_positive = {step}
    trailing_stop_positive_offset = {offset}
    trailing_only_offset_is_reached = True
    minimal_roi = {roi}

    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    max_open_trades = 1
    process_only_new_candles = True
    order_types = {{
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }}

    DROP = {drop}
    DROP2 = {drop2}
    BODY_MIN = {body_min}
    VOL_MULT = {vol_mult}
    RSI_BELOW = {rsi_below}
    CLOSE_ABOVE_PREV = {close_above_prev}
    WINDOW = "{window}"
    COOLDOWN_H = {cooldown_h}

    def populate_indicators(self, dataframe, metadata):
        dataframe["ret_1p"] = dataframe["close"].pct_change(periods=1)
        dataframe["ret_2p"] = dataframe["close"].pct_change(periods=2)
        dataframe["body_pct"] = abs(dataframe["close"] - dataframe["open"]) / dataframe["open"]
        dataframe["vol_ma20"] = dataframe["volume"].rolling(20).mean()
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        tss = dataframe["date"]
        bj_h = (tss.dt.hour + 8) % 24
        dow = tss.dt.dayofweek
        mode = self.WINDOW
        if mode == "fri_mon":
            window = (dow >= 5) | ((dow == 0) & (bj_h <= 21))
        elif mode == "sat_mon":
            window = (dow >= 6) | ((dow == 0) & (bj_h <= 21))
        elif mode == "fri_sun":
            window = dow >= 5
        elif mode == "sat_sun":
            window = dow >= 6
        elif mode == "fri_mon_full":
            window = dow >= 5
        else:
            window = (dow >= 5) | ((dow == 0) & (bj_h <= 21))

        entry = (
            (dataframe["ret_1p"].shift(1) < -self.DROP)
            & (dataframe["close"] > dataframe["open"])
            & (dataframe["ret_1p"] >= -self.DROP)
            & (dataframe["volume"] > 0)
            & window
        )
        if self.DROP2 > 0:
            entry &= (dataframe["ret_2p"].shift(1) < -self.DROP2)
        if self.BODY_MIN > 0:
            entry &= (dataframe["body_pct"] > self.BODY_MIN)
        if self.VOL_MULT > 0:
            entry &= (dataframe["volume"].shift(1) > self.VOL_MULT * dataframe["vol_ma20"].shift(1))
        if self.RSI_BELOW > 0:
            entry &= (dataframe["rsi"].shift(1) < self.RSI_BELOW)
        if self.CLOSE_ABOVE_PREV:
            entry &= (dataframe["close"] > dataframe["close"].shift(1))

        dataframe["long_entry"] = entry
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe.loc[dataframe["long_entry"], "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        return dataframe

    def confirm_trade_exit(
        self, pair, trade, order_type, amount, rate, time_in_force, exit_reason, current_time, **kwargs
    ) -> bool:
        if self.COOLDOWN_H > 0 and "stop_loss" in exit_reason:
            PairLocks.lock_pair(
                pair, current_time + timedelta(hours=self.COOLDOWN_H), reason="cooldown_after_loss"
            )
        return True
'''


def roi_str(roi):
    return "{}" if roi is None else '{"0": %s}' % float(roi)


def gen_variant(idx, p):
    name = f"V2_{idx:03d}"
    src = TEMPLATE.format(
        name=name,
        stoploss=float(p["stoploss"]),
        step=float(p["trail_step"]),
        offset=float(p["trail_offset"]),
        roi=roi_str(p["roi"]),
        drop=float(p["drop"]),
        drop2=float(p.get("drop2", 0.0)),
        body_min=float(p.get("body_min", 0.0)),
        vol_mult=float(p.get("vol_mult", 0.0)),
        rsi_below=float(p.get("rsi_below", 0.0)),
        close_above_prev="True" if p.get("close_above_prev") else "False",
        window=p.get("window", "fri_mon"),
        cooldown_h=int(p.get("cooldown_h", 0)),
    )
    with open(os.path.join(BATCH_DIR, f"{name}.py"), "w", encoding="utf-8") as f:
        f.write(src)
    return name


def parse_result(zip_path):
    import zipfile
    with zipfile.ZipFile(zip_path) as zf:
        js = [n for n in zf.namelist() if n.endswith(".json") and "_config" not in n][0]
        data = json.loads(zf.read(js))
    rows = []
    for sname, s in data.get("strategy", {}).items():
        exit_reasons = {}
        for e in s.get("exit_reason_summary", []):
            if isinstance(e, dict):
                reason = e.get("exit_reason") or e.get("reason") or "?"
                exit_reasons[str(reason)] = [e.get("trade_count", 0), round(e.get("profit_mean", 0) * 100, 2)]
        rows.append({
            "strategy": sname,
            "trades": s.get("total_trades", 0),
            "profit_pct": round(s.get("profit_total", 0) * 100, 2),
            "profit_abs": round(s.get("profit_total_abs", 0), 2),
            "winrate": round(s.get("winrate", 0) * 100, 2),
            "pf": s.get("profit_factor"),
            "sharpe": s.get("sharpe"),
            "cagr": s.get("cagr"),
            "mdd": round(s.get("max_relative_drawdown", 0) * 100, 2),
            "mdd_abs": round(s.get("max_drawdown_abs", 0), 2),
            "avg_hold_h": None,
            "wins": s.get("wins"),
            "losses": s.get("losses"),
            "exit_reasons": exit_reasons,
        })
    return rows


def build_combos(batch: int):
    combos = []
    base = {"drop": 0.02, "window": "fri_mon", "stoploss": -0.10,
            "trail_offset": 0.015, "trail_step": 0.003, "roi": 0.08}
    combos.append(("base", dict(base)))

    def add(tag, **kw):
        p = dict(base)
        p.update(kw)
        combos.append((tag, p))

    if batch == 1:
        for v in [0.015, 0.025, 0.03, 0.035, 0.04]:
            add(f"drop{v:.3f}", drop=v)
        for w in ["sat_mon", "fri_sun", "sat_sun", "fri_mon_full"]:
            add(f"win_{w}", window=w)
        for v in [-0.08, -0.12, -0.15]:
            add(f"sl{v:.2f}", stoploss=v)
        for v in [0.01, 0.02, 0.025, 0.03]:
            add(f"off{v:.3f}", trail_offset=v)
        for v in [0.002, 0.005, 0.008]:
            add(f"st{v:.3f}", trail_step=v)
        for v in [0.06, 0.10, 0.12]:
            add(f"roi{v:.2f}", roi=v)
        add("roi_none", roi=None)
        for v in [0.005, 0.01]:
            add(f"body{v:.3f}", body_min=v)
        for v in [1.5, 2.0, 3.0]:
            add(f"vol{v:.1f}", vol_mult=v)
        for v in [25, 30, 35]:
            add(f"rsi{v}", rsi_below=v)
        for v in [0.03, 0.04, 0.05]:
            add(f"drop2{v:.2f}", drop2=v)
        for v in [24, 48, 72]:
            add(f"cool{v}", cooldown_h=v)
        add("close_prev", close_above_prev=True)
    elif batch == 2:
        for v in [0.0015, 0.002, 0.0025, 0.003]:
            add(f"st{v:.4f}", trail_step=v)
        for v in [-0.11, -0.12, -0.13, -0.15]:
            add(f"sl{v:.2f}", stoploss=v)
        for v in [0.05, 0.06, 0.07, 0.08]:
            add(f"roi{v:.2f}", roi=v)
        for v in [0.012, 0.015, 0.018]:
            add(f"st2_off{v:.3f}", trail_step=0.002, trail_offset=v)
        for v in [-0.10, -0.12, -0.15]:
            add(f"st2_sl{v:.2f}", trail_step=0.002, stoploss=v)
        for v in [0.06, 0.08]:
            add(f"st2_roi{v:.2f}", trail_step=0.002, roi=v)
        add("st2_sl12_roi6", trail_step=0.002, stoploss=-0.12, roi=0.06)
        add("st2_sl15_roi6", trail_step=0.002, stoploss=-0.15, roi=0.06)
        add("st2_sl12_roi6_off18", trail_step=0.002, stoploss=-0.12, roi=0.06, trail_offset=0.018)
        add("st2_sl12_roi6_off12", trail_step=0.002, stoploss=-0.12, roi=0.06, trail_offset=0.012)
        add("st2_sl15_roi5", trail_step=0.002, stoploss=-0.15, roi=0.05)
        add("st2_sl12_roi7", trail_step=0.002, stoploss=-0.12, roi=0.07)
        add("st2_sl12_roi10", trail_step=0.002, stoploss=-0.12, roi=0.10)
    return combos


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--timerange", default="20220101-20260805")
    ap.add_argument("--batch", type=int, default=1, choices=[1, 2])
    args = ap.parse_args()

    combos = build_combos(args.batch)

    os.makedirs(BATCH_DIR, exist_ok=True)
    for f in glob.glob(os.path.join(BATCH_DIR, "*.py")):
        os.remove(f)

    names = []
    for idx, (tag, p) in enumerate(combos, 1):
        names.append(gen_variant(idx, p))
    print(f"generated {len(names)} variants")

    cmd = [
        sys.executable, "-m", "freqtrade", "backtesting",
        "--config", "user_data/config_perpetual.json",
        "--strategy-path", BATCH_DIR,
        "--strategy-list", *names,
        "--timerange", args.timerange,
        "--pairs", *PAIRS,
        "--backtest-directory", RESULT_DIR,
    ]
    print("running freqtrade ...")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout[-4000:])
        print(r.stderr[-4000:])
        sys.exit(1)

    zips = sorted(glob.glob(os.path.join(RESULT_DIR, "backtest-result-*.zip")), key=os.path.getmtime)
    if not zips:
        print(r.stdout[-4000:])
        sys.exit(1)
    rows = parse_result(zips[-1])
    by_name = {r["strategy"]: r for r in rows}
    out = []
    for idx, (tag, p) in enumerate(combos, 1):
        r_ = by_name.get(names[idx - 1])
        if r_:
            r_ = dict(r_)
            r_["tag"] = tag
            out.append(r_)

    tag = f"batch{args.batch}_{args.timerange}"
    out_csv = os.path.join(RESULT_DIR, f"wrv2_{tag}.csv")
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["tag", "strategy", "trades", "profit_pct", "profit_abs",
                                          "winrate", "pf", "sharpe", "cagr", "mdd", "wins", "losses"])
        w.writeheader()
        for r_ in out:
            w.writerow({k: r_.get(k) for k in w.fieldnames})

    print(f"\n{'tag':<12} {'trades':>6} {'profit%':>10} {'profit$':>12} {'win%':>6} {'PF':>6} {'MDD%':>6} {'Sharpe':>6}")
    for r_ in sorted(out, key=lambda x: -x["profit_abs"]):
        print(f"{r_['tag']:<12} {r_['trades']:>6} {r_['profit_pct']:>10,.1f} {r_['profit_abs']:>12,.0f} "
              f"{r_['winrate']:>5.1f}% {r_['pf']:>6.2f} {r_['mdd']:>5.1f}% {r_['sharpe']:>6.2f}")
    print("\nCSV:", out_csv)


if __name__ == "__main__":
    main()
