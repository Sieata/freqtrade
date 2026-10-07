"""
synth_put_backtest.py — 合成看跌期权(一多一空)路径回测

freqtrade 单仓模式无法模拟"同合约双腿",这里用独立模拟器按"一多一空"的形式化跑路径:

  对冲态(两腿在):   净值≈0,只付手续费
                     ↓ 市场漂移 > ROLL_GAP → 滚动行权价(净≈0,付 4 单费)
                     ↓ 跌到多腿强平线 → 多腿爆仓(权利金沉没) → 裸空窗口
  裸空窗口:          跌到目标 → 兑现暴跌利润;弹回行权价 → 回收部分权利金
                     时间到 → 按市价平;闪涨到空腿强平线 → 结构被动解除
  再对冲:            按当前价重开两腿,进入下一周期

诚实的简化(均为偏向"好看"的假设,真实会差):
  - 多腿爆仓按标记价触发,实亏 = 保证金 + 滑点(真实还有保险基金/ADL/穿仓)
  - 裸空平仓按目标价/行权价限价成交,无滑点;真实缺口/跳空会打折扣
  - 对冲态资金费净值 = 0(名义相等方向相反);裸空窗口按真实 funding 计
  - 未建模:爆仓瞬间订单深度、强平手续费对保证金的挤占

用法:python user_data/scripts/synth_put_backtest.py
"""

import os, sys
from pathlib import Path
project_root = Path(__file__).resolve().parent.parent.parent
os.chdir(project_root)
sys.path.insert(0, str(project_root))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import pandas as pd
import numpy as np
from freqtrade.configuration import Configuration
from freqtrade.data.history import load_pair_history
from freqtrade.enums import CandleType

# ── 参数(与策略卡第 6 节对应) ──
N = 10_000.0          # 单腿名义本金(USDT),双腿各 N
L_L = 20.0            # 多腿杠杆(权利金腿)
L_S = 2.0             # 空腿杠杆(幸存者腿)
TP_DROP = 0.10        # 裸空止盈:跌破行权价再跌 10% 平仓兑现
MAX_HOLD = 48         # 裸空时间止损(根 4h K,= 8 天)
ROLL_GAP = 0.10       # 行权价滚动阈值:价格漂移 >10% 重开两腿跟踪市场
FEE = 0.0004          # taker 费率(每单)
LIQ_SLIPPAGE = 0.005  # 多腿爆仓滑点(名义的 %,额外成本)
MAINT = 0.004         # 维持保证金率(强平比理论杠杆位早 ~0.4%)

CAPITAL = N / L_L + N / L_S          # 结构占用保证金(权利金腿 + 幸存者腿)
PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "BNB/USDT:USDT", "SOL/USDT:USDT", "DOGE/USDT:USDT"]
TF = "4h"


# ═══════════════ 数据加载 ═══════════════

config = Configuration.from_files(["user_data/config_perpetual.json"])


def load_funding(pair):
    name = pair.replace("/", "_").replace(":", "_")
    path = Path(config["datadir"]) / "futures" / f"{name}-1h-funding_rate.feather"
    if not path.exists():
        return None
    f = pd.read_feather(path)[["date", "open"]].rename(columns={"open": "funding"})
    return f.set_index("date")["funding"]


def load_pair(pair):
    df = load_pair_history(datadir=config["datadir"], timeframe=TF, pair=pair,
                           data_format="feather", candle_type=CandleType.FUTURES)
    df = df.reset_index(drop=True)
    funding = load_funding(pair)
    if funding is not None:
        merged = funding.reindex(funding.index.union(df["date"])).ffill().reindex(df["date"])
        df["funding"] = merged.values
    else:
        df["funding"] = 0.0
    return df


# ═══════════════ 模拟器 ═══════════════

def simulate(pair, df, use_funding=True, verbose_events=False):
    n = len(df)
    dates = df["date"].values
    highs, lows, closes = df["high"].values, df["low"].values, df["close"].values
    fr = df["funding"].values if use_funding else np.zeros(n)

    liq_move_long = 1 / L_L - MAINT       # 多腿强平位移(≈4.6%)
    liq_move_short = 1 / L_S - MAINT      # 空腿强平位移(≈49.6%)

    realized = 0.0
    entry_p = 0.0
    state = "FLAT"
    naked_start = 0
    cycle_start = 0.0
    events = []

    # 分解记账(各分项之和 = realized)
    c_longliq = 0.0     # 多腿爆仓:-(权利金 + 滑点)
    c_short = 0.0       # 空腿平仓盈亏(兑现/弹回/时间/尾段)
    c_roll = 0.0        # 滚动净额(长腿+空腿同时平,≈0)
    c_fee = 0.0         # 全部手续费
    c_funding = 0.0     # 裸空窗口资金费
    n_cycles = 0
    n_crash = n_bounce = n_time = n_shortliq = n_roll = 0
    cycle_nets = []     # (类型, 单周期净额)

    def pay_fee(orders):
        nonlocal realized, c_fee
        f = FEE * N * orders
        realized -= f
        c_fee += f

    def open_legs(price):
        nonlocal entry_p, state
        entry_p = price
        state = "HEDGED"
        pay_fee(2)

    equity = []
    zero_date = None
    for i in range(n):
        h, l, c = highs[i], lows[i], closes[i]

        if state == "FLAT":
            if i == 0:
                open_legs(c)
                events.append(("open", dates[i], c, 0.0))

        elif state == "HEDGED":
            liq_p = entry_p * (1 - liq_move_long)
            short_liq_p = entry_p * (1 + liq_move_short)
            if l <= liq_p:
                # 多腿爆仓 → 权利金沉没,进入裸空窗口
                cycle_start = realized        # 爆仓前(含权利金前的完整周期净额)
                prem = N / L_L
                slip = LIQ_SLIPPAGE * N
                realized -= prem + slip
                c_longliq += -(prem + slip)
                pay_fee(1)
                events.append(("long_liq", dates[i], liq_p, -(prem + slip + FEE * N)))
                n_cycles += 1
                naked_start = i
                state = "NAKED"
            elif h >= short_liq_p:
                # 闪涨把空腿打爆(结构被动解除,净≈多腿浮盈-空腿保证金)
                short_loss = N / L_S
                long_gain = N * (c - entry_p) / entry_p
                net = long_gain - short_loss
                realized += net
                c_short += net
                pay_fee(1)
                events.append(("short_liq_hedged", dates[i], short_liq_p, net - FEE * N))
                n_shortliq += 1
                open_legs(c)
            elif abs(c / entry_p - 1) > ROLL_GAP:
                # 价格漂移超阈值 → 滚动行权价跟踪市场(净≈0,付 4 单费)
                unreal = N * (c - entry_p) / entry_p + N * (entry_p - c) / entry_p
                realized += unreal
                c_roll += unreal
                pay_fee(2)          # 平多 + 平空
                open_legs(c)        # 开多 + 开空
                events.append(("roll", dates[i], c, unreal - FEE * N * 4))
                n_roll += 1

        elif state == "NAKED":
            f = fr[i] * 0.5 * N
            realized += f
            c_funding += f

            strike_p = entry_p * (1 - liq_move_long)
            target_p = strike_p * (1 - TP_DROP)
            short_liq_p = entry_p * (1 + liq_move_short)

            if l <= target_p:
                # 兑现暴跌
                pnl = N * (entry_p - target_p) / entry_p
                realized += pnl
                c_short += pnl
                pay_fee(1)
                events.append(("crash", dates[i], target_p, pnl - FEE * N))
                n_crash += 1
                open_legs(target_p)
                cycle_nets.append(("crash", realized - cycle_start))
            elif h >= short_liq_p:
                loss = N / L_S
                realized -= loss
                c_short -= loss
                events.append(("short_liq", dates[i], short_liq_p, -loss))
                n_shortliq += 1
                cycle_nets.append(("short_liq", realized - cycle_start))
                state = "BLOWN"
                break
            elif h >= strike_p:
                # 弹回行权价(回收约 4.6% 名义,权利金差维持保证金)
                pnl = N * (entry_p - strike_p) / entry_p
                realized += pnl
                c_short += pnl
                pay_fee(1)
                events.append(("bounce", dates[i], strike_p, pnl - FEE * N))
                n_bounce += 1
                open_legs(strike_p)
                cycle_nets.append(("bounce", realized - cycle_start))
            elif i - naked_start >= MAX_HOLD:
                pnl = N * (entry_p - c) / entry_p
                realized += pnl
                c_short += pnl
                pay_fee(1)
                events.append(("time", dates[i], c, pnl - FEE * N))
                n_time += 1
                open_legs(c)
                cycle_nets.append(("time", realized - cycle_start))

        if state == "HEDGED":
            unreal = N * (c - entry_p) / entry_p + N * (entry_p - c) / entry_p
        elif state == "NAKED":
            unreal = N * (entry_p - c) / entry_p
        else:
            unreal = 0.0
        eq_val = CAPITAL + realized + unreal
        equity.append(eq_val)
        if zero_date is None and eq_val <= 0:
            zero_date = str(dates[i])[:10]

    if state == "NAKED":
        pnl = N * (entry_p - closes[-1]) / entry_p
        realized += pnl
        c_short += pnl
        pay_fee(1)
        events.append(("end_close", dates[-1], closes[-1], pnl - FEE * N))

    eq = pd.Series(equity)
    mdd = float((eq / eq.cummax() - 1).min() * 100)
    ret = realized / CAPITAL * 100

    # 分项自检:必须等于 realized
    assert abs(c_longliq + c_short + c_roll - c_fee + c_funding - realized) < 0.01, "分项和≠realized"

    def avg_net(tag):
        nets = [net for t, net in cycle_nets if t == tag]
        return (len(nets), np.mean(nets) if nets else 0.0, sum(nets))

    return {
        "pair": pair, "start": str(dates[0])[:10], "end": str(dates[-1])[:10],
        "n_candles": n, "state_end": state,
        "realized": realized, "ret": ret, "mdd": mdd,
        "n_cycles": n_cycles, "n_roll": n_roll,
        "n_crash": n_crash, "n_bounce": n_bounce, "n_time": n_time, "n_shortliq": n_shortliq,
        "c_longliq": c_longliq, "c_short": c_short, "c_roll": c_roll,
        "c_fee": c_fee, "c_funding": c_funding,
        "avg_bounce": avg_net("bounce"), "avg_crash": avg_net("crash"),
        "zero_date": zero_date,
        "events": events,
        "equity": equity,
    }


# ═══════════════ 输出 ═══════════════

def fmt(x, signed=True):
    return f"{x:+,.0f}" if signed else f"{x:,.0f}"


def report(r, bh_ret):
    print(f"\n{'─'*96}")
    print(f"  {r['pair'].split('/')[0]:5s} | {r['start']} → {r['end']} | {r['n_candles']} 根 4h | 结束态:{r['state_end']}")
    print(f"{'─'*96}")
    if r["n_shortliq"] > 0:
        print(f"  ⚠ 空腿被强平 {r['n_shortliq']} 次(含对冲态闪涨)→ 结构被动解除过")
    nb, ab, tb = r["avg_bounce"]
    nc, ac, tc = r["avg_crash"]
    print(f"  周期:爆仓 {r['n_cycles']} | 滚动 {r['n_roll']} | 兑现 {r['n_crash']} / 弹回 {r['n_bounce']} / 时间平 {r['n_time']}")
    print(f"  弹回 {nb} 次:平均净 {ab:+.0f}U/次,合计 {tb:+.0f}U  |  兑现 {nc} 次:平均净 {ac:+.0f}U/次,合计 {tc:+.0f}U")
    print(f"  占用资金 {CAPITAL:,.0f} → 净盈亏 {fmt(r['realized'])} ({r['ret']:+.1f}%)   最大回撤 {r['mdd']:.1f}%")
    if r["zero_date"]:
        print(f"  → 账户归零日: {r['zero_date']} (若持续补保证金,终值如上行)")
    print(f"  分项: 多腿爆仓 {fmt(r['c_longliq'])} | 空腿平仓 {fmt(r['c_short'])} | 滚动 {fmt(r['c_roll'])} | 手续费 {fmt(-r['c_fee'], signed=False)} | 资金费 {fmt(r['c_funding'])}")
    print(f"  同期 B&H(同额 {CAPITAL:,.0f}U): {bh_ret:+,.0f} ({bh_ret/CAPITAL*100:+.1f}%)")
    if verbose_events:
        for ev in r["events"]:
            print(f"    {ev[0]:>16s} {str(ev[1])[:10]}  @{ev[2]:>10,.1f}  {fmt(ev[3])}")


verbose_events = False


if __name__ == "__main__":
    for pair in PAIRS:
        df = load_pair(pair)
        has_funding = bool((df["funding"] != 0).any())
        r = simulate(pair, df, use_funding=has_funding)
        bh = CAPITAL * (df["close"].iloc[-1] / df["close"].iloc[0] - 1)
        report(r, bh)
        if not has_funding:
            print("  ⚠ 无 funding 数据,裸空资金费按 0 计(结果偏乐观)")

    print(f"\n参数:N={N:.0f} | 多腿 {L_L:.0f}x(强平 -{1/L_L*100:.1f}%) | 空腿 {L_S:.0f}x | 裸空止盈 -{TP_DROP*100:.0f}% | 时间止损 {MAX_HOLD} 根 | 滚动 {ROLL_GAP*100:.0f}% | fee {FEE*100:.2f}% | 爆仓滑点 {LIQ_SLIPPAGE*100:.1f}%")
    print("说明:裸空按限价成交(无滑点,乐观);维持保证金差 {:.1f}% 计入强平价。".format(MAINT * 100))
