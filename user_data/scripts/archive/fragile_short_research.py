"""
fragile_short_research.py — Phase 1 数据观察:脆弱状态下做空是否有效

来源:SyntheticPut 策略卡(user_data/docs/SynthPutV1_strategy_card.md)
在"一多一空、多腿强平"结构被剥掉后,可交易的实质是:
    市场处于脆弱状态(资金费极端正 / 价格乖离趋势过远)时做空,
    用宽止损当"权利金",赌暴跌兑现。
本脚本验证这个核心假设,并与已失败的 short_scan(做空暴涨)对比。

判定标准(STRATEGY_WORKFLOW Phase 1):
    任一信号在 ≥4/5 品种上 胜率>55% 且 均值>0(过盈亏平衡) → 进原型
    资金费信号因数据只覆盖 BTC/ETH/SOL,标准为 3/3。
"""

import os, sys
from pathlib import Path
project_root = Path(__file__).resolve().parent.parent.parent
os.chdir(project_root)
sys.path.insert(0, str(project_root))
# Windows 控制台默认 GBK,强制 UTF-8 输出(否则中文/emoji 会崩)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import pandas as pd
import numpy as np
from freqtrade.configuration import Configuration
from freqtrade.data.history import load_pair_history
from freqtrade.enums import CandleType

config = Configuration.from_files(["user_data/config_perpetual.json"])
dl = Path(config["datadir"])
PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "BNB/USDT:USDT", "SOL/USDT:USDT", "DOGE/USDT:USDT"]
FUNDING_PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]  # 只有这些有 funding 数据
TF = "4h"
H = [8, 12, 24]          # 32h / 48h / 96h 持有期
FUNDING_THRESH = [0.0005, 0.0010]   # 0.05% / 0.10% per 8h (年化≈55% / 110%)
DEVIATION_K = [1.5, 2.0, 2.5, 3.0]  # 乖离倍数(相对 ATR)
PREM_BUDGET = 3.0        # 权利金预算(单腿版止损距离),用于评估"损失封顶"属性


# ═══════════════ 数据加载 ═══════════════

def load_funding(pair):
    """读取 8h 资金费率(Binance 原始 8h 一次,存于 open 列)"""
    name = pair.replace("/", "_").replace(":", "_")
    path = dl / "futures" / f"{name}-1h-funding_rate.feather"
    if not path.exists():
        return None
    f = pd.read_feather(path)[["date", "open"]].rename(columns={"open": "funding"})
    return f.set_index("date")["funding"]


def load_pair(pair):
    df = load_pair_history(datadir=dl, timeframe=TF, pair=pair, data_format="feather",
                           candle_type=CandleType.FUTURES)
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["atr"] = (df["high"] - df["low"]).rolling(14).mean()
    df["dev_atr"] = (df["close"] - df["ema50"]) / df["atr"]   # 相对趋势的乖离(ATR 单位)
    df["ret_4p"] = df["close"].pct_change(periods=4)
    df["volume"] = df["volume"].fillna(0)

    funding = load_funding(pair)
    if funding is not None:
        # funding 是 8h 一次(00/08/16),ffill 到 4h 索引,当前生效值
        idx = df["date"]
        merged = funding.reindex(funding.index.union(idx)).ffill().reindex(idx)
        df["funding"] = merged.values
    return df


# ═══════════════ 信号评估 ═══════════════

def find_clusters(mask):
    """True 段的起点(一段状态的首次出现,避免重复入场)"""
    return mask & (~mask.shift(1).fillna(False))


def event_positions(df, events, h):
    """返回每个信号事件可评估的 (pos, base_price)"""
    out = []
    for idx in df.index[events]:
        pos = df.index.get_loc(idx)
        if pos + h < len(df):
            out.append((pos, df["close"].iloc[pos]))
    return out


def event_stats(df, events, h):
    """做空 H 根K 的收益分布。返回 dict 或 None(样本不足)"""
    rets = []
    for pos, base in event_positions(df, events, h):
        rets.append(-(df["close"].iloc[pos+h] - base) / base * 100)
    if len(rets) < 3:
        return None
    rets = np.array(rets)
    return {"n": len(rets), "wr": (rets > 0).mean() * 100, "mean": rets.mean(),
            "median": np.median(rets)}


def event_excursions(df, events, h):
    """MAE/MFE:做空期间的最大不利/有利偏移,刻画"损失封顶+尾部厚"属性"""
    rets, maes, mfes = [], [], []
    for pos, base in event_positions(df, events, h):
        win = df["close"].iloc[pos+1:pos+h+1]
        up = (win - base) / base * 100        # 价格向上 = 对空不利
        rets.append(-(win.iloc[-1] - base) / base * 100)
        maes.append(up.max())
        mfes.append((-up).max())
    return np.array(rets), np.array(maes), np.array(mfes)


def event_carry(df, events, h):
    """做空 H 根K 期间累计收到的资金费(正 = 收钱)。仅在有 funding 列时有效"""
    if "funding" not in df:
        return None
    carry = []
    for pos, _ in event_positions(df, events, h):
        # 每个 4h 桶持有半个 8h 计息区间,×0.5 折算
        carry.append(df["funding"].iloc[pos+1:pos+h+1].sum() * 0.5)
    return np.array(carry)


def fmt(s):
    if s is None:
        return "  样本不足  "
    return f"{s['n']:>4d}笔 {s['wr']:>4.0f}% {s['mean']:>+6.2f}% med{s['median']:>+6.2f}%"


# ═══════════════ 各假设信号 ═══════════════

def h1_funding(df, f_thresh):
    """H1 资金费极端正 → 做空(入场即收 carry)"""
    return find_clusters(df["funding"] > f_thresh) & (df["volume"] > 0)


def h2_deviation(df, k):
    """H2 价格乖离 EMA50 超过 K·ATR → 做空(与 short_scan 的"首根阴线"无关)"""
    return find_clusters(df["dev_atr"] > k) & (df["volume"] > 0)


def h3_combined(df, f_thresh, k):
    """H3 资金费极端 且 乖离 → 最高置信度"""
    return find_clusters((df["funding"] > f_thresh) & (df["dev_atr"] > k)) & (df["volume"] > 0)


def ref_short_scan(df, thresh=10):
    """对照组:已失败的 short_scan 信号(做空暴涨)"""
    pumped = df["ret_4p"].shift(1) > thresh/100
    bearish = df["close"] < df["open"]
    not_pumped_now = df["ret_4p"] <= thresh/100
    return pumped & bearish & not_pumped_now & (df["volume"] > 0)


# ═══════════════ 主流程 ═══════════════

DATAFRAMES = {pair: load_pair(pair) for pair in PAIRS}
print(f"数据:4h futures 2021-01-01 起,{len(DATAFRAMES['BTC/USDT:USDT'])} 根/品种")
print(f"资金费数据:{', '.join(p.split('/')[0] for p in FUNDING_PAIRS)} (8h 一次,open 列)")
print()

# ── H1 资金费极端正 → 做空 ──
print("=" * 96)
print("H1 资金费极端正 → 做空  (做空期间收 funding,报告含 carry 净值)")
print("=" * 96)
for f_thresh in FUNDING_THRESH:
    ann = f_thresh * 3 * 365 * 100
    print(f"\n资金费阈值 F={f_thresh*100:.2f}%/8h (年化≈{ann:.0f}%)")
    for h in [12, 24]:
        row = f"  H={h}({h*4}h) "
        for pair in FUNDING_PAIRS:
            df = DATAFRAMES[pair]
            events = h1_funding(df, f_thresh)
            s = event_stats(df, events, h)
            carry = event_carry(df, events, h)
            if s is not None and carry is not None:
                net = s["mean"] + carry.mean()   # 空头收正 funding → 加到收益
                row += f"| {pair.split('/')[0]:4s} {fmt(s)} carry{carry.mean():+.2f}% 净{s['mean']+carry.mean():+.2f}% "
            else:
                row += f"| {pair.split('/')[0]:4s}  样本不足  "
        print(row)

# ── H2 乖离 EMA50 → 做空 ──
print("\n" + "=" * 96)
print("H2 价格乖离 EMA50 > K·ATR → 做空  (五品种)")
print("=" * 96)
for k in DEVIATION_K:
    print(f"\n乖离倍数 K={k}")
    for h in [12, 24]:
        row = f"  H={h}({h*4}h) "
        for pair in PAIRS:
            df = DATAFRAMES[pair]
            s = event_stats(df, h2_deviation(df, k), h)
            row += f"| {pair.split('/')[0]:4s} {fmt(s)} "
        print(row)

# ── H2 的 MAE/MFE 明细(K=2.0, 评估"损失封顶+尾部厚") ──
k0 = 2.0
print(f"\nH2 明细 K={k0} H=12:MAE/MFE 分布 (MAE>权利金预算 {PREM_BUDGET}% 占比 = 单腿版会被止损的笔数比例)")
for pair in PAIRS:
    df = DATAFRAMES[pair]
    events = h2_deviation(df, k0)
    rets, maes, mfes = event_excursions(df, events, 12)
    if len(rets) < 3:
        print(f"  {pair.split('/')[0]:5s} 样本不足"); continue
    hit_prem = (maes > PREM_BUDGET).mean() * 100
    print(f"  {pair.split('/')[0]:5s} n={len(rets):>3d}  mean={rets.mean():+5.2f}%  med={np.median(rets):+5.2f}%"
          f"  meanMAE={maes.mean():5.2f}%  MAE>3%占比={hit_prem:4.0f}%  meanMFE={mfes.mean():+5.2f}%")

# ── H3 组合 ──
print("\n" + "=" * 96)
print("H3 资金费极端 且 乖离 → 做空  (组合,仅 BTC/ETH/SOL)")
print("=" * 96)
for f_thresh in [0.0005]:
    for k in [2.0]:
        row = f"  F={f_thresh*100:.2f}% K={k} "
        for h in [12, 24]:
            for pair in FUNDING_PAIRS:
                df = DATAFRAMES[pair]
                s = event_stats(df, h3_combined(df, f_thresh, k), h)
                row += f"| {pair.split('/')[0]:4s} H={h} {fmt(s)} "
        print(row)

# ── 对照组:short_scan(已失败) ──
print("\n" + "=" * 96)
print("对照组 已失败信号 short_scan(涨超10% + 首根阴线做空) H=12")
print("=" * 96)
row = "  "
for pair in PAIRS:
    df = DATAFRAMES[pair]
    s = event_stats(df, ref_short_scan(df), 12)
    row += f"| {pair.split('/')[0]:4s} {fmt(s)} "
print(row)

# ── 判定 ──
print("\n" + "=" * 96)
print("判定(Phase 1 出口):≥4/5 品种 胜率>55% 且 均值>0;资金费信号为 3/3")
print("=" * 96)
best = []
for pair in PAIRS:
    df = DATAFRAMES[pair]
    for k in DEVIATION_K:
        for h in [12, 24]:
            s = event_stats(df, h2_deviation(df, k), h)
            if s and s["wr"] > 55 and s["mean"] > 0:
                best.append((pair, "H2", k, h, s))
for pair in FUNDING_PAIRS:
    df = DATAFRAMES[pair]
    for f_thresh in FUNDING_THRESH:
        for h in [12, 24]:
            events = h1_funding(df, f_thresh)
            s = event_stats(df, events, h)
            carry = event_carry(df, events, h)
            if s and carry is not None and s["wr"] > 55 and (s["mean"] + carry.mean()) > 0:
                best.append((pair, "H1", f_thresh, h, s))

for pair in PAIRS:
    h2_pass = sum(1 for (p, sig, *_, ss) in best if p == pair and sig == "H2" and ss["wr"] > 55 and ss["mean"] > 0)
    print(f"  {pair.split('/')[0]:5s}: H2 通过阈值组合数 = {h2_pass}")
print()
for pair in FUNDING_PAIRS:
    h1_pass = sum(1 for (p, sig, *_, ss) in best if p == pair and sig == "H1")
    print(f"  {pair.split('/')[0]:5s}: H1(含carry) 通过阈值组合数 = {h1_pass}")
print()
n_h2_pairs = len({p for (p, sig, *_) in best if sig == "H2"})
n_h1_pairs = len({p for (p, sig, *_) in best if sig == "H1"})
print(f"H2 至少一档阈值通过的品种数:{n_h2_pairs}/5 (需要 ≥4/5)")
print(f"H1 至少一档阈值通过的品种数:{n_h1_pairs}/3 (需要 3/3,含 carry)")
print("结论:", "PASS 有信号达标 -> 进原型 CrashShortV1" if (n_h2_pairs >= 4 or n_h1_pairs >= 3)
      else "FAIL 无信号达标 -> 记入失败清单,不写策略")
