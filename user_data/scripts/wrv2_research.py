"""
WeekendReverseV1 改进研究 - 快速模拟器（用于参数网格筛选）

复刻 freqtrade 核心语义：
  - 4h K线, 信号在收盘后产生, 下一根 K 线开盘价入场
  - 满仓复利: stake = balance * tradable_balance_ratio(0.99)
  - 手续费 0.05%/边
  - 退出: ROI(止盈) > 尾随止损 > 硬止损 (用 K 线 high/low 触发)
  - max_open_trades = 1, 跨品种按时间顺序扫描

用途: 快速筛选参数组合; 最终候选用 freqtrade backtesting 复核。
注意: 本模拟器不含资金费率, 用于排序比较, 绝对数字以 freqtrade 为准。
"""

import argparse
import glob
import os
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

FEATHER = os.path.join(ROOT, "user_data", "data", "binance", "futures")
FEE = 0.0005
TRADABLE = 0.99


def load_pair(pair: str) -> pd.DataFrame:
    path = os.path.join(FEATHER, f"{pair}_USDT_USDT-4h-futures.feather")
    if not os.path.exists(path):
        return None
    df = pd.read_feather(path)
    df = df[["date", "open", "high", "low", "close", "volume"]].copy()
    df["pair"] = pair
    return df


def bj_hour(ts: pd.Series) -> pd.Series:
    return (ts.dt.hour + 8) % 24


def add_signals(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy()
    thr = cfg.get("drop", 0.02)
    df["ret1"] = df["close"].pct_change()
    ts = pd.to_datetime(df["date"])
    bj_h = bj_hour(ts)
    dow = ts.dt.dayofweek

    win_mode = cfg.get("window", "fri_mon")
    if win_mode == "fri_mon":          # 周五+周六+周日+周一盘前(21:00前)
        window = (dow >= 5) | ((dow == 0) & (bj_h <= 21))
    elif win_mode == "sat_mon":        # 周六+周日+周一盘前
        window = (dow >= 6) | ((dow == 0) & (bj_h <= 21))
    elif win_mode == "fri_sun":        # 周五+周六+周日
        window = dow >= 5
    elif win_mode == "sat_sun":        # 仅周六+周日
        window = dow >= 6
    elif win_mode == "all":            # 全时段(对照)
        window = pd.Series(True, index=df.index)
    else:
        raise ValueError(win_mode)

    entry = (
        (df["ret1"].shift(1) < -thr)
        & (df["close"] > df["open"])
        & (df["ret1"] >= -thr)
        & (df["volume"] > 0)
        & window
    )

    # ── 可选过滤器 ──────────────────────────────
    if cfg.get("body_min", 0) > 0:
        entry &= ((df["close"] - df["open"]) / df["open"] > cfg["body_min"])
    if cfg.get("vol_mult", 0) > 0:
        vol_ma = df["volume"].rolling(20).mean()
        entry &= (df["volume"].shift(1) > cfg["vol_mult"] * vol_ma.shift(1))
    if cfg.get("rsi_below", 0) > 0:
        rsi = 100 - 100 / (1 + ta_rsi(df["close"], 14))
        entry &= (rsi.shift(1) < cfg["rsi_below"])
    if cfg.get("close_above_prev", False):
        entry &= (df["close"] > df["close"].shift(1))
    if cfg.get("drop2", 0) > 0:        # 两根K线累计跌幅
        ret2 = df["close"].pct_change(2)
        entry &= (ret2.shift(1) < -cfg["drop2"])
    if cfg.get("atr_mult", 0) > 0:     # 跌幅用 ATR 单位
        atr = ta_atr(df, 14)
        entry &= ((df["high"].shift(1) - df["close"].shift(1)) > cfg["atr_mult"] * atr.shift(1))

    df["entry"] = entry
    return df


def ta_rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ru = up.ewm(alpha=1 / n, adjust=False).mean()
    rd = down.ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + ru / rd.replace(0, np.nan))


def ta_atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


@dataclass
class Trade:
    pair: str
    entry_date: pd.Timestamp
    entry: float
    exit_date: pd.Timestamp
    exit: float
    stake: float
    reason: str
    profit_ratio: float = 0.0
    profit_abs: float = 0.0

    def compute(self) -> None:
        qty = self.stake / self.entry
        buy_cost = self.stake * (1 + FEE)
        sell_value = qty * self.exit * (1 - FEE)
        self.profit_abs = sell_value - buy_cost
        self.profit_ratio = self.profit_abs / self.stake


def run_backtest(pairs: list, cfg: dict) -> tuple[list[Trade], float, float]:
    frames = []
    for p in pairs:
        df = load_pair(p)
        if df is None or len(df) < 300:
            continue
        frames.append(add_signals(df, cfg))
    if not frames:
        return [], 1000.0, 0.0
    merged = pd.concat(frames).sort_values("date").reset_index(drop=True)

    balance = 1000.0
    open_trade = None
    pending = {}
    trades = []
    # 记录每对最近止损时间, 用于 cooldown
    last_loss = {p: pd.Timestamp.min for p in pairs}

    cooldown_h = cfg.get("cooldown_h", 0)
    sl = cfg.get("stoploss", -0.10)
    off = cfg.get("trail_offset", 0.015)
    step = cfg.get("trail_step", 0.003)
    roi = cfg.get("roi", 0.08)

    for _, row in merged.iterrows():
        t = row["date"]
        p = row["pair"]
        if open_trade is None:
            if pending.get(p, False):
                # 上一根K线产生的信号, 在本根K线开盘处理(freqtrade语义)
                pending[p] = False
                if not (cooldown_h and (t - last_loss.get(p, pd.Timestamp.min)).total_seconds() < cooldown_h * 3600):
                    stake = balance * TRADABLE
                    if stake > 0:
                        open_trade = Trade(
                            pair=p, entry_date=t, entry=row["open"],
                            exit_date=t, exit=0.0, stake=stake, reason="",
                        )
                        open_trade.highest = row["open"]
                        open_trade.stop = open_trade.entry * (1 + sl)
                        open_trade.trailing_on = False
        else:
            if pending.get(p, False):
                # 槽位被占, 该信号作废(freqtrade语义: 不延迟重试)
                pending[p] = False
        if row["entry"]:
            # 本根K线的信号留给下一根K线处理
            pending[p] = True
        if open_trade is not None:
            tr = open_trade
            if tr.pair != row["pair"]:
                continue
            o, h, l = row["open"], row["high"], row["low"]
            dur_candles = int((t - tr.entry_date).total_seconds() // 14400)
            initial_stop = tr.entry * (1 + sl)
            # 尾随止损更新
            if not tr.trailing_on and h >= tr.entry * (1 + off):
                tr.trailing_on = True
            if tr.trailing_on:
                tr.trail_stop = max(initial_stop, h * (1 - step))
            else:
                tr.trail_stop = initial_stop
            # 退出优先级(freqtrade): 硬止损 > ROI > 尾随止损
            # 1) 硬止损
            if tr.reason == "" and l <= initial_stop:
                tr.exit, tr.reason = initial_stop, "stop_loss"
            # 2) ROI 止盈
            if tr.reason == "" and roi is not None:
                target = tr.entry * (1 + roi)
                if h >= target:
                    tr.exit, tr.reason = min(max(target, l), h), "roi"
            # 3) 尾随止损
            if tr.reason == "" and tr.trailing_on and l <= tr.trail_stop:
                if dur_candles == 0:
                    stop_rate = tr.entry * (1 + off - step)
                    tr.exit, tr.reason = max(l, stop_rate), "trailing_stop_loss"
                else:
                    tr.exit, tr.reason = tr.trail_stop, "trailing_stop_loss"
            if tr.reason:
                tr.exit_date = t
                tr.compute()
                trades.append(tr)
                balance += tr.profit_abs
                if tr.profit_abs < 0:
                    last_loss[tr.pair] = t
                open_trade = None

    return trades, balance, balance - 1000.0


def yearly_pnl(trades: list, balance: float) -> dict:
    """按自然年报告利润(复利口径)。"""
    out = {}
    year0 = None
    prev = 1000.0
    cur = 1000.0
    by_year = {}
    for tr in trades:
        y = tr.exit_date.year
        by_year.setdefault(y, []).append(tr)
    for y in sorted(by_year):
        pnl = sum(t.profit_abs for t in by_year[y])
        out[y] = pnl
    return out


def summarize(trades: list, balance: float) -> dict:
    if not trades:
        return {"trades": 0, "profit": 0.0, "winrate": 0.0, "pf": 0.0, "mdd": 0.0,
                "avg_profit": 0.0, "years": {}}
    wins = [t for t in trades if t.profit_abs > 0]
    losses = [t for t in trades if t.profit_abs < 0]
    gw = sum(t.profit_abs for t in wins)
    gl = abs(sum(t.profit_abs for t in losses))
    # 最大回撤(按交易后余额曲线)
    bal = 1000.0
    peak = 1000.0
    mdd = 0.0
    for t in trades:
        bal += t.profit_abs
        peak = max(peak, bal)
        mdd = max(mdd, (peak - bal) / peak)
    return {
        "trades": len(trades),
        "profit": balance - 1000.0,
        "winrate": len(wins) / len(trades),
        "pf": gw / gl if gl > 0 else float("inf"),
        "mdd": mdd,
        "avg_profit": np.mean([t.profit_ratio for t in trades]),
        "years": yearly_pnl(trades, balance),
    }


BASE_PAIRS = ["BTC", "ETH", "SOL", "XRP", "ZEC", "BANK", "CYS", "HYPE"]
EXTRA_PAIRS = ["SUI", "WLD", "1000PEPE", "ADA", "AVAX", "DOT", "LTC", "DOGE", "LINK", "COTI"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default="base", choices=["base", "all", "main", "extras", "base+new", "base+main"])
    args = ap.parse_args()

    if args.pairs == "base":
        pairs = BASE_PAIRS
    elif args.pairs == "all":
        pairs = BASE_PAIRS + EXTRA_PAIRS
    elif args.pairs == "main":
        pairs = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "DOT", "LINK", "LTC", "ZEC", "COTI"]
    elif args.pairs == "extras":
        pairs = EXTRA_PAIRS
    elif args.pairs == "base+new":
        pairs = BASE_PAIRS + ["SUI", "WLD", "1000PEPE"]
    elif args.pairs == "base+main":
        pairs = BASE_PAIRS + ["DOGE", "ADA", "AVAX", "DOT", "LTC", "LINK", "COTI"]
    else:
        pairs = BASE_PAIRS

    # 基线(复刻 WeekendReverseV1)
    baseline_cfg = {
        "drop": 0.02, "window": "fri_mon", "stoploss": -0.10,
        "trail_offset": 0.015, "trail_step": 0.003, "roi": 0.08,
    }
    trades, bal, pnl = run_backtest(pairs, baseline_cfg)
    s = summarize(trades, bal)
    print(f"== 基线: {len(trades)} 笔, 利润 ${pnl:,.0f}, 胜率 {s['winrate']*100:.1f}%, "
          f"PF {s['pf']:.2f}, MDD {s['mdd']*100:.1f}%")
    print("   逐年:", {k: round(v) for k, v in s["years"].items()})

    # 参数网格
    grid = []
    for drop in [0.015, 0.02, 0.025, 0.03, 0.04]:
        grid.append({"drop": drop, "label": f"drop={drop:.0%}"})
    for w in ["fri_mon", "sat_mon", "fri_sun", "sat_sun"]:
        grid.append({"window": w, "label": f"window={w}"})
    for vol in [1.5, 2.0, 3.0]:
        grid.append({"vol_mult": vol, "label": f"volx{vol}"})
    for rsi in [25, 30, 35]:
        grid.append({"rsi_below": rsi, "label": f"rsi<{rsi}"})
    for drop2 in [0.03, 0.04, 0.05]:
        grid.append({"drop2": drop2, "label": f"drop2>{drop2:.0%}"})
    for cooldown in [12, 24, 48, 72]:
        grid.append({"cooldown_h": cooldown, "label": f"cooldown{cooldown}h"})
    for body in [0.005, 0.01]:
        grid.append({"body_min": body, "label": f"body>{body:.1%}"})
    for sl in [-0.08, -0.12, -0.15]:
        grid.append({"stoploss": sl, "label": f"sl={sl:.0%}"})
    for off, st in [(0.01, 0.003), (0.02, 0.003), (0.03, 0.003), (0.015, 0.005), (0.015, 0.008), (0.02, 0.005)]:
        grid.append({"trail_offset": off, "trail_step": st, "label": f"off{off:.1%}/st{st:.1%}"})
    for roi_v in [0.06, 0.10, 0.12, None]:
        grid.append({"roi": roi_v, "label": f"roi={roi_v if roi_v else 'none'}"})

    print(f"\n== 单参数网格 (品种池: {args.pairs}, n={len(pairs)}) ==")
    print(f"{'variant':<22} {'trades':>6} {'profit$':>10} {'win%':>6} {'PF':>6} {'MDD%':>6} {'avg%':>6}")
    results = []
    for g in grid:
        cfg = dict(baseline_cfg)
        cfg.update({k: v for k, v in g.items() if k != "label"})
        tr, bal2, pnl2 = run_backtest(pairs, cfg)
        s2 = summarize(tr, bal2)
        results.append((g["label"], s2))
        print(f"{g['label']:<22} {s2['trades']:>6} {pnl2:>10,.0f} {s2['winrate']*100:>5.1f}% "
              f"{s2['pf']:>6.2f} {s2['mdd']*100:>5.1f}% {s2['avg_profit']*100:>5.2f}%")
    print("\n-- top10 by profit --")
    for label, s2 in sorted(results, key=lambda x: -x[1]["profit"])[:10]:
        print(f"{label:<22} {s2['trades']:>6} {s2['profit']:>10,.0f} {s2['winrate']*100:>5.1f}% "
              f"{s2['pf']:>6.2f} {s2['mdd']*100:>5.1f}%")


if __name__ == "__main__":
    main()
