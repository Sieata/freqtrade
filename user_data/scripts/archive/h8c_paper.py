"""H8c 双腿基差套利 paper 模拟器（多永续+空交割 及反向，BTC+ETH，1x，RESEARCH 13.5）。

freqtrade 引擎不支持交割合约（market_is_future 硬过滤 type=="swap"），双腿结构按仓库
既有 paper 纪律以独立模拟器落地：模拟交易（不实盘、无需 API key），每轮拉 fapi 公开
行情 → 触发判断 → sqlite 记账（建仓/funding/到期结算）→ 打印状态报告。

预注册参数（FREEZE_H8C.md 冻结，改动 = 作废重来）：
  触发: |b|/days_left×365 ≥ 10%（b = 交割/永续 − 1）且剩余 ≥14 天；b>0 正向
        （多永续+空交割）、b<0 反向（空永续+多交割）；每合约每方向至多一次
  规模: 每腿名义 $1,000（项目独立口径），1x，无复利
  费用: taker 0.05%/腿，建仓 2 腿 + 到期平永续 1 腿；交割腿到期交割免手续费
  funding: 多永续收正费/付负费，反向相反；现金流 = Σ rate×固定名义（价格修正项
        量级 << 费用缓冲，从简）
  PnL: 两腿按真实成交/结算价计算（非近似 b_entry），交割腿交割价 = 到期后合约
        最后一根 K 线 close（≈结算价），永续腿按到期后最新价平仓
幂等: 持仓 (sym, dir) 判重 + funding_log 主键，重复运行不重复记账。
用法: .venv/Scripts/python.exe user_data/scripts/h8c_paper.py [--status | --selftest]
cron（paper 设备）: 17 * * * * cd <repo> && .venv/bin/python user_data/scripts/h8c_paper.py \
    >> user_data/logs/h8c_paper.log 2>&1
"""
import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

os.environ.setdefault("https_proxy", "http://127.0.0.1:7897")
os.environ.setdefault("http_proxy", "http://127.0.0.1:7897")

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "user_data/h8c_paper/state.sqlite"
SNAP_F = ROOT / "user_data/data/binance/h8c_paper/basis_snapshots.feather"

FAPI = "https://fapi.binance.com"
ASSETS = ["BTC", "ETH"]          # 预注册标的
STAKE = 1000.0                   # 每腿名义 USDT（1x，无复利）
THETA = 0.10                     # 年化基差触发门槛
MIN_DAYS_LEFT = 14.0
TAKER = 0.0005
SETTLE_H = 8                     # 交割 08:00 UTC（funding 00/08/16 同刻）
LOOKBACK_FUND_DAYS = 10          # funding 增量回看窗口（幂等靠主键兜底）


def with_retries(fn, tries=4, wait=3):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if i == tries - 1:
                raise
            print(f"  [retry {i+1}] {type(e).__name__}: {e}", flush=True)
            time.sleep(wait)


def quarter_last_fridays(start_year=2025, end_year=2027):
    out = []
    for y in range(start_year, end_year + 1):
        for m in (3, 6, 9, 12):
            d = datetime(y + (m == 12), (m % 12) + 1, 1, tzinfo=timezone.utc) - timedelta(days=1)
            while d.weekday() != 4:
                d -= timedelta(days=1)
            out.append(d)
    return sorted(out)


def live_contracts(now):
    """在市合约 [(sym, asset, expiry_ts)]：只看当季+次季（200 天内，远季未上市）。"""
    out = []
    for e in quarter_last_fridays():
        ets = e.replace(hour=SETTLE_H, tzinfo=timezone.utc)
        if now < ets <= now + timedelta(days=200):
            for a in ASSETS:
                out.append((f"{a}USDT_{e.strftime('%y%m%d')}", a, ets))
    return out


def get_json(url, params=None):
    return with_retries(lambda: requests.get(url, params=params, timeout=30).json())


def fetch_prices(syms):
    """fapi 最新价（永续与交割同域）。返回 {sym: price}，拉不到的略过。"""
    out = {}
    for s in syms:
        try:
            r = get_json(f"{FAPI}/fapi/v1/ticker/price", {"symbol": s})
            out[s] = float(r["price"])
        except Exception as e:
            print(f"  [price 失败] {s}: {type(e).__name__}: {e}", flush=True)
    return out


def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS positions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      sym TEXT, asset TEXT, dir INTEGER,
      entry_ts TEXT, expiry_ts TEXT,
      entry_perp REAL, entry_del REAL, entry_b REAL, days_left REAL,
      notional REAL, qty REAL, fee_open REAL,
      status TEXT DEFAULT 'OPEN',
      exit_ts TEXT, exit_perp REAL, exit_del REAL, fee_close REAL,
      funding_total REAL, ret REAL);
    CREATE TABLE IF NOT EXISTS funding_log(
      sym TEXT, settle_ts TEXT, rate REAL, cash REAL,
      PRIMARY KEY(sym, settle_ts));
    """)
    con.commit()
    return con


def snap_basis(rows):
    SNAP_F.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    if SNAP_F.exists():
        df = pd.concat([pd.read_feather(SNAP_F), df])
    df = (df.sort_values("ts")
            .drop_duplicates(["ts", "sym"], keep="last")
            .reset_index(drop=True))
    df.to_feather(SNAP_F)


def open_position(con, sym, asset, d, perp_px, del_px, b, now, expiry_ts):
    """等币数建仓：永续腿 $STAKE 名义，交割腿同币数（名义 STAKE×(1+b)）→ 严格 delta 中性。"""
    days_left = (expiry_ts - now).total_seconds() / 86400
    qty = STAKE / perp_px
    fee = (STAKE + qty * del_px) * TAKER
    con.execute("INSERT INTO positions(sym,asset,dir,entry_ts,expiry_ts,entry_perp,entry_del,"
                "entry_b,days_left,notional,qty,fee_open) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (sym, asset, d, now.isoformat(), expiry_ts.isoformat(),
                 perp_px, del_px, b, days_left, STAKE, qty, fee))
    con.commit()
    print(f"  >>> [开仓] {sym} {'正向(多永续+空交割)' if d == 1 else '反向(空永续+多交割)'} "
          f"b={b*100:+.2f}% 锁定APR {abs(b)/days_left*365*100:+.1f}% "
          f"剩{days_left:.0f}天 名义 ${STAKE:.0f}+${qty*del_px:.0f} 费 ${fee:.2f}", flush=True)


def settle_position(con, pos, now, perp_exit, del_exit):
    d = pos["dir"]
    qty = pos["qty"]
    pnl_perp = d * qty * (perp_exit - pos["entry_perp"])
    pnl_del = -d * qty * (del_exit - pos["entry_del"])
    fee_close = qty * perp_exit * TAKER  # 永续平仓 1 腿 taker；交割腿交割免费
    fund = con.execute("SELECT COALESCE(SUM(cash),0) FROM funding_log WHERE sym=?",
                       (pos["sym"],)).fetchone()[0]
    ret = (pnl_perp + pnl_del + fund - pos["fee_open"] - fee_close) / STAKE
    con.execute("UPDATE positions SET status='CLOSED', exit_ts=?, exit_perp=?, exit_del=?, "
                "fee_close=?, funding_total=?, ret=? WHERE id=?",
                (now.isoformat(), perp_exit, del_exit, fee_close, fund, ret, pos["id"]))
    con.commit()
    print(f"  >>> [交割结算] {pos['sym']} 实现 {ret*100:+.2f}% "
          f"(腿盈 {pnl_perp*100:+.2f}/{pnl_del*100:+.2f}$ funding {fund*100:+.2f}$)", flush=True)


def update_funding(con, pos, now):
    """补记持仓期新增 funding 结算（多永续收正费：dir=+1 → cash=+rate×名义）。"""
    since = now - timedelta(days=LOOKBACK_FUND_DAYS)
    rows = get_json(f"{FAPI}/fapi/v1/fundingRate",
                    {"symbol": f"{pos['asset']}USDT",
                     "startTime": int(since.timestamp() * 1000), "limit": 1000})
    for r in rows:
        ts = datetime.fromtimestamp(r["fundingTime"] / 1000, tz=timezone.utc)
        if ts <= datetime.fromisoformat(pos["entry_ts"]) or ts > now:
            continue
        rate = float(r["fundingRate"])
        cash = pos["dir"] * rate * pos["notional"]
        cur = con.execute("INSERT OR IGNORE INTO funding_log VALUES(?,?,?,?)",
                          (pos["sym"], ts.isoformat(), rate, cash))
        if cur.rowcount:
            con.commit()


def close_expired(con, now, prices):
    for pos in con.execute("SELECT * FROM positions WHERE status='OPEN'").fetchall():
        expiry_ts = datetime.fromisoformat(pos["expiry_ts"])
        if now < expiry_ts:
            continue
        kl = get_json(f"{FAPI}/fapi/v1/klines",
                      {"symbol": pos["sym"], "interval": "4h", "limit": 3})
        del_exit = float(kl[-1][4]) if kl else prices.get(pos["sym"])
        perp_exit = prices.get(f"{pos['asset']}USDT")
        if not del_exit or not perp_exit:
            print(f"  [结算缺价] {pos['sym']} 延后一轮", flush=True)
            continue
        settle_position(con, pos, now, perp_exit, del_exit)


def run_cycle(con, now, allow_new=True):
    contracts = live_contracts(now)
    syms = [s for s, _, _ in contracts] + [f"{a}USDT" for a in ASSETS]
    prices = fetch_prices(syms)
    snaps = []
    for sym, asset, ets in contracts:
        p_del, p_perp = prices.get(sym), prices.get(f"{asset}USDT")
        if not p_del or not p_perp:
            continue
        days_left = (ets - now).total_seconds() / 86400
        b = p_del / p_perp - 1
        ann = b / days_left * 365
        snaps.append({"ts": now, "sym": sym, "b": b, "perp": p_perp, "del": p_del,
                      "days_left": days_left, "ann": ann})
        if not allow_new or days_left < MIN_DAYS_LEFT:
            continue
        d = 1 if b > 0 else -1
        if con.execute("SELECT 1 FROM positions WHERE sym=? AND dir=?", (sym, d)).fetchone():
            continue
        if b > 0 and ann >= THETA:
            open_position(con, sym, asset, 1, p_perp, p_del, b, now, ets)
        elif b < 0 and ann <= -THETA:
            open_position(con, sym, asset, -1, p_perp, p_del, b, now, ets)
    if snaps:
        snap_basis(snaps)
    for pos in con.execute("SELECT * FROM positions WHERE status='OPEN'").fetchall():
        update_funding(con, pos, now)
    close_expired(con, now, prices)


def report(con, now):
    print(f"\n===== H8c paper 状态 @ {now:%Y-%m-%d %H:%M} UTC =====")
    snaps = pd.read_feather(SNAP_F) if SNAP_F.exists() else pd.DataFrame()
    if len(snaps):
        last = snaps[snaps["ts"] == snaps["ts"].max()]
    else:
        last = snaps
    print("-- 监控面板（当季+次季，门槛 |ann|≥10% 且剩≥14天）--")
    for _, r in last.iterrows():
        gate = "★可触发" if abs(r["ann"]) >= THETA and r["days_left"] >= MIN_DAYS_LEFT else ""
        print(f"  {r['sym']}: b={r['b']*100:+.2f}%  锁定APR {r['ann']*100:+.1f}%  "
              f"剩 {r['days_left']:.0f}天  {gate}")
    opens = con.execute("SELECT * FROM positions WHERE status='OPEN'").fetchall()
    print(f"-- 持仓中 {len(opens)} --")
    for p in opens:
        fund = con.execute("SELECT COALESCE(SUM(cash),0) FROM funding_log WHERE sym=?",
                           (p["sym"],)).fetchone()[0]
        s = snaps[snaps["sym"] == p["sym"]]
        cur_b = s.iloc[-1]["b"] if len(s) else p["entry_b"]
        print(f"  {p['sym']} [{'正' if p['dir']==1 else '反'}] 入场 b={p['entry_b']*100:+.2f}% "
              f"现 b={cur_b*100:+.2f}%  浮动 {(p['dir']*(cur_b-p['entry_b']))*100:+.2f}%  "
              f"funding {fund*100:+.2f}%  到期 {p['expiry_ts'][:10]}")
    closed = con.execute("SELECT * FROM positions WHERE status='CLOSED'").fetchall()
    print(f"-- 已实现 {len(closed)} --")
    for p in closed:
        print(f"  {p['sym']} [{'正' if p['dir']==1 else '反'}] {p['entry_ts'][:10]}→{p['exit_ts'][:10]} "
              f"b={p['entry_b']*100:+.2f}% → 实现 {p['ret']*100:+.2f}%")
    if closed:
        tot = sum(p["ret"] for p in closed)
        t0 = min(datetime.fromisoformat(p["entry_ts"]) for p in closed)
        yrs = max((now - t0).total_seconds() / 86400 / 365, 1e-9)
        print(f"  合计 {tot*100:+.2f}% / {yrs:.2f} 年 ≈ {tot/yrs*100:+.1f}%/年 "
              f"(另含持仓中浮动与 funding 未计入合计)")
    print()


def selftest():
    """逻辑自检：等币数 PnL 闭合（D_exit=P_exit 时 PnL 精确 = STAKE×b_entry，与路径无关）。"""
    print("== H8c 模拟器自检 ==")
    ok = True
    for d, p_entry, d_entry in ((+1, 100.0, 103.0), (-1, 100.0, 98.0)):
        p_exit, d_exit = 110.0, 110.0  # 到期基差归零（价格路径任意取）
        b_entry = d_entry / p_entry - 1
        qty = STAKE / p_entry
        pnl_perp = d * qty * (p_exit - p_entry)
        pnl_del = -d * qty * (d_exit - d_entry)
        fee_open = (STAKE + qty * d_entry) * TAKER
        fee_close = qty * p_exit * TAKER
        ret = (pnl_perp + pnl_del - fee_open - fee_close) / STAKE
        expect = abs(b_entry) - (fee_open + fee_close) / STAKE
        good = abs(ret - expect) < 1e-9
        ok &= good
        print(f"  {'正向' if d == 1 else '反向'}闭合: ret={ret*100:+.4f}% "
              f"期望(|b_entry|−费)={expect*100:+.4f}% {'PASS' if good else 'FAIL'}")
    cash_pos, cash_neg = (+1) * 0.0001 * STAKE, (-1) * (-0.0001) * STAKE
    good = cash_pos > 0 and cash_neg > 0
    ok &= good
    print(f"  funding 方向: 正向收 {cash_pos:+.3f} 反向收 {cash_neg:+.3f} "
          f"{'PASS' if good else 'FAIL'}")
    print("== 自检结束 ==")
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="只看报告，不开新仓")
    ap.add_argument("--selftest", action="store_true", help="内置逻辑自检")
    args = ap.parse_args()
    if args.selftest:
        selftest()
    now = datetime.now(timezone.utc)
    con = init_db()
    run_cycle(con, now, allow_new=not args.status)
    report(con, now)


if __name__ == "__main__":
    main()
