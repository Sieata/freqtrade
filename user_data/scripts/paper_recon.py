# -*- coding: utf-8 -*-
"""paper 信号对账（2026-11-08，轮13）。

目的：V2 只在周末窗口入场（buy_window_mode="fri_mon"），而 freqtrade
process_only_new_candles=True 只对「启动后新收盘」的 bar 触发入场 —— 若在周末窗口内
重启 bot，重启前已收盘的周末入场信号会被静默跳过（RESEARCH 2026-11-08 轮11 记录）。
本模块离线用 V2 入场逻辑重放指定臂在最近 N 天的 long_entry，与 DB 实际开仓对账，
把「窗口内 long_entry=True 但无对应 trade 且在 bot 已运行之后」的 bar 标记为疑似漏单。

只读：读本地 feather K 线 + sqlite trades 表，不写任何东西，不改源码，不碰 VAL。

用法（项目根目录）：
  .venv/Scripts/python.exe user_data/scripts/paper_recon.py --arm v2 --days 14
  .venv/Scripts/python.exe user_data/scripts/paper_recon.py --arm v2 --days 14 --json
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "user_data" / "data" / "binance" / "futures"
METRICS = ROOT / "user_data" / "data" / "binance" / "futures_metrics"

# 各臂的 db 与 config（与 paper_restart.ARMS 对齐）
ARM_DB = {
    "v2": ROOT / "user_data" / "tradesv3.dryrun.sqlite",
    "oi": ROOT / "user_data" / "tradesv3.dryrun.oi.sqlite",
}
# 各臂实际 config（用于从 bot 实际白名单取对账品种；pool 文件是静态快照，可能与 live 白名单不符）
ARM_CONFIG = {
    "v2": ROOT / "user_data" / "config_paper_v2.json",
    "oi": ROOT / "user_data" / "config_paper_oi.json",
}
# V2 入场参数默认值（WeekendReverseV2.py ClassVar，冻结不可改，这里复刻用于离线重放）
V2_BUY_DROP = 0.02          # buy_drop 默认（WeekendReverseV2.py:69 DecimalParameter default=0.02）
V2_BUY_WINDOW_MODE = "fri_mon"
# OI 入场参数默认值（OIFlushV2.py 冻结不可改，这里复刻用于离线重放）
OI_QUANTILE = 0.05
OI_WINDOW = 1080
OI_MIN_PERIODS = 360
OI_CRASH_THRESHOLD = -0.05
OI_MOM_WINDOW = 180
OI_SHIFT = 6


def _kline_fname(pair, tf="4h"):
    return DATA / (pair.replace("/", "_").replace(":", "_") + f"-{tf}-futures.feather")


# 各臂 live bot 端口（缺本地 feather 时从 bot 的 /pair_candles 取运行时 K 线，
# 覆盖 BANK/CYS 等不在 TOP10 保鲜池、本地无 feather 但 whitelist 内实盘在用品种）。
ARM_PORT = {"v2": 8080, "oi": 8082}
_API_AUTH = ("freqtrader", "IUdQZFygM_n6weNX")


def _fetch_kline_api(pair, arm, tf="4h", limit=1080):
    """从 live bot 的 /api/v1/pair_candles 拉运行时 OHLCV（含策略指标，但只用 OHLCV 重放）。
    失败返回 None（调用方按缺数据跳过）。"""
    port = ARM_PORT.get(arm)
    if port is None:
        return None
    try:
        import requests
        import pandas as pd
    except Exception:
        return None
    try:
        r = requests.get(
            f"http://127.0.0.1:{port}/api/v1/pair_candles",
            params={"pair": pair, "timeframe": tf, "limit": limit},
            auth=_API_AUTH, timeout=15,
        )
        j = r.json()
        cols = j.get("columns") or []
        data = j.get("data") or []
        if not cols or not data:
            return None
        df = pd.DataFrame(data, columns=cols)
        keep = [c for c in ("date", "open", "high", "low", "close", "volume") if c in df.columns]
        df = df[keep].copy()
        df["date"] = pd.to_datetime(df["date"], utc=True)
        return df.sort_values("date").reset_index(drop=True)
    except Exception:
        return None


def _load_pool(name):
    f = ROOT / "user_data" / "universe" / f"pairs_{name}.txt"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.append(line)
    return out


def _bot_whitelist(arm):
    """取 live bot 实际交易的品种（/whitelist），失败回退 config 的 pair_whitelist。
    recon 必须按 bot **实际** 白名单对账：TOP10 静态池与 V2 live 白名单不一致
    （实测 V2 实跑 8 品种含 BANK/CYS，而 TOP10 池是 BNB/TRX/DOGE/XMR —— 用池文件会
    漏查真实品种 + 空查未交易品种）。"""
    port = ARM_PORT.get(arm)
    if port is not None:
        try:
            import requests
            r = requests.get(f"http://127.0.0.1:{port}/api/v1/whitelist",
                             auth=_API_AUTH, timeout=10)
            wl = r.json().get("whitelist") or []
            if wl:
                return wl
        except Exception:
            pass
    # 回退读 config
    cfg = ARM_CONFIG.get(arm)
    if cfg and cfg.exists():
        try:
            import json as _json
            c = _json.loads(cfg.read_text(encoding="utf-8"))
            wl = (c.get("exchange") or {}).get("pair_whitelist")
            if wl:
                return wl
        except Exception:
            pass
    return []


def v2_window_mask(dates):
    """复刻 WeekendReverseV2.py:91-106 的窗口判定（默认 fri_mon）。返回布尔 Series。"""
    import pandas as pd

    tss = pd.to_datetime(pd.Series(dates), utc=True)
    bj_h = (tss.dt.hour + 8) % 24
    dow = tss.dt.dayofweek
    mode = V2_BUY_WINDOW_MODE
    if mode == "fri_mon":
        window = (dow >= 5) | ((dow == 0) & (bj_h <= 21))
    elif mode == "sat_mon":
        window = (dow >= 6) | ((dow == 0) & (bj_h <= 21))
    elif mode == "fri_sun":
        window = dow >= 5
    elif mode == "sat_sun":
        window = dow >= 6
    elif mode == "fri_mon_full":
        window = (dow >= 5) | (dow == 0)
    else:
        window = (dow >= 5) | ((dow == 0) & (bj_h <= 21))
    return window.reset_index(drop=True)


def v2_long_entry(df, buy_drop=V2_BUY_DROP):
    """复刻 WeekendReverseV2.py:108-127 的入场条件（仅用默认参数，不含可选加严项）。

    返回与原 df 对齐的布尔 Series（long_entry）。
    """
    import pandas as pd

    close = df["close"]
    open_ = df["open"]
    vol = df["volume"]
    ret_1p = close.pct_change(periods=1)
    window = pd.Series(v2_window_mask(df["date"]).values, index=df.index)
    entry = (
        (ret_1p.shift(1) < -buy_drop)
        & (close > open_)
        & (ret_1p >= -buy_drop)
        & (vol > 0)
        & window
    )
    return entry


def oi_flush_entry(df, metrics_df):
    """复刻 OIFlushV2.py populate_indicators 的 flush 入场条件（默认参数）。

    df: 4h K 线（含 open/high/low/close/volume/date）。
    metrics_df: metrics feather（含 date/oi_usd）。按 date 左连接；与策略一致。
    返回与 df 对齐的布尔 Series（flush）。
    """
    import pandas as pd

    if metrics_df is None or metrics_df.empty:
        return pd.Series(False, index=df.index)
    m = metrics_df.copy()
    m["oi_chg"] = m["oi_usd"] / m["oi_usd"].shift(OI_SHIFT) - 1
    m["oi_q"] = m["oi_chg"].rolling(OI_WINDOW, min_periods=OI_MIN_PERIODS).quantile(OI_QUANTILE)
    d = df.merge(m[["date", "oi_chg", "oi_q"]], on="date", how="left")
    d["ret4"] = d["close"] / d["close"].shift(1) - 1
    d["mom30"] = d["close"] / d["close"].shift(OI_MOM_WINDOW) - 1
    flush = (
        (d["oi_chg"] <= d["oi_q"])
        & (d["ret4"] <= OI_CRASH_THRESHOLD)
        & (d["mom30"] > 0)
    )
    return pd.Series(flush.fillna(False).values, index=df.index)


def _trades_open_dates(db_path):
    """返回 [(pair, open_date_str), ...]；db 或表缺失返回 []。"""
    if not db_path.exists():
        return []
    try:
        c = sqlite3.connect(str(db_path))
        rows = c.execute("SELECT pair, open_date FROM trades").fetchall()
        c.close()
        return [(str(p), str(d)) for p, d in rows]
    except Exception:
        return []


def _first_heartbeat_ts(arm):
    """从 logfile 里取最早 heartbeat 时间（=bot 大致上线时刻）；读不到返回 None。"""
    logf = ROOT / "user_data" / "logs" / f"paper_{arm}_forwardtest.log"
    if not logf.exists():
        return None
    import re as _re

    ts = None
    for line in logf.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if m and ("heartbeat" in line or "RUNNING" in line):
            ts = m.group(1)
            break
    return ts


def recon_arm(arm, days=14, pool="top10"):
    """对账一个臂。返回 dict：
    {arm, days, since, pairs, entry_bars:[{pair,bar,reason*}], n_entries, n_trades, missed:[...]}
    missed = 窗口内 long_entry=True、bar 收盘在 bot 上线之后、且无对应 open_date 的条目。
    """
    import pandas as pd

    if arm not in ARM_DB:
        return {
            "arm": arm,
            "skipped": True,
            "reason": f"未支持的臂 {arm}",
        }

    db = ARM_DB[arm]
    # 优先用 live bot 实际白名单；取不到才退回静态 pool 文件
    pairs = _bot_whitelist(arm)
    pool_source = "bot_whitelist"
    if not pairs:
        pairs = _load_pool(pool)
        pool_source = f"pool:{pool}"
    now = pd.Timestamp.now(tz="UTC")
    since = now - pd.Timedelta(days=days)
    trades = _trades_open_dates(db)
    # 交易开仓时间归一到 4h bar 左边界（向下取整）
    traded_bars = set()
    for pair, d in trades:
        try:
            ts = pd.Timestamp(d, tz="UTC").floor("4h")
            traded_bars.add((pair, ts))
        except Exception:
            pass

    hb = _first_heartbeat_ts(arm)
    hb_ts = pd.Timestamp(hb, tz="Asia/Shanghai").tz_convert("UTC") if hb else None
    if hb_ts is not None:
        hb_ts = hb_ts.floor("4h")

    entries = []
    no_data = []  # 既无 feather 又取不到 API 的品种（显式记录，不再静默跳过）
    api_pairs = []  # 靠 API 兜底参与对账的品种
    stale_pairs = []  # feather 过期、改走 API 的品种
    for p in pairs:
        f = _kline_fname(p, "4h")
        df = None
        if f.exists():
            df = pd.read_feather(f).sort_values("date").reset_index(drop=True)
            df["date"] = pd.to_datetime(df["date"], utc=True)
            # feather 新鲜度检查：若末日早于窗口起点（陈旧，如 BANK/CYS 不在保鲜池会停在数周前），
            # 直接用它会把近窗口当作"无信号/无数据"，对账形同虚设 → 改从 live bot API 取实时 K 线。
            if df.empty or df["date"].iloc[-1] < since:
                apidf = _fetch_kline_api(p, arm, "4h")
                if apidf is not None and not apidf.empty:
                    stale_pairs.append(p)
                    df = apidf
                    df = df[df["date"] >= since - pd.Timedelta(days=60)].reset_index(drop=True)
        if df is None or df.empty:
            # 本地无 feather 或已空 → 从 live bot API 取运行时 K 线
            df = _fetch_kline_api(p, arm, "4h")
            if df is None or df.empty:
                no_data.append(p)
                continue
            api_pairs.append(p)
        # OI 的 mom30 需要历史 180 根，先保留全量算指标，再过滤窗口
        full = df.reset_index(drop=True)
        if arm == "v2":
            ent = v2_long_entry(full)
        else:  # oi
            mf = METRICS / (p.replace("/", "_").replace(":", "_") + "-4h-metrics.feather")
            mdf = pd.read_feather(mf) if mf.exists() else None
            if mdf is not None:
                mdf["date"] = pd.to_datetime(mdf["date"], utc=True)
            ent = oi_flush_entry(full, mdf)
        mask = full["date"] >= since
        for i in range(len(full)):
            if not bool(mask.iloc[i]):
                continue
            if not bool(ent.iloc[i]):
                continue
            bar = full["date"].iloc[i]
            entries.append({"pair": p, "bar": bar, "close": float(full["close"].iloc[i])})

    entry_bars = [{"pair": e["pair"], "bar": str(e["bar"])[:16], "close": round(e["close"], 6)} for e in entries]
    missed = []
    for e in entries:
        bar = e["bar"]
        key = (e["pair"], bar)
        if key in traded_bars:
            continue
        # 只有 bar 收盘在 bot 上线之后、才可能真的该成交（否则属重启漏单/冷启动）
        if hb_ts is not None and bar < hb_ts:
            continue
        missed.append({"pair": e["pair"], "bar": str(bar)[:16], "close": round(e["close"], 6)})

    return {
        "arm": arm,
        "days": days,
        "since": str(since)[:19],
        "pairs": len(pairs),
        "pool_source": pool_source,
        "via_api": api_pairs,
        "stale_api": stale_pairs,
        "no_data": no_data,
        "first_heartbeat_utc": str(hb_ts)[:19] if hb_ts is not None else None,
        "n_entries": len(entry_bars),
        "n_trades": len(trades),
        "entry_bars": entry_bars,
        "missed": missed,
    }


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    ap = argparse.ArgumentParser(description="paper 信号对账（V2 周末窗口 long_entry vs 实际开仓，只读）")
    ap.add_argument("--arm", choices=["v2", "oi"], default="v2")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--pool", default="top10")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    r = recon_arm(args.arm, args.days, args.pool)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return
    if r.get("skipped"):
        print(f"[{args.arm}] 对账跳过：{r['reason']}")
        return
    print(f"paper 信号对账  arm={r['arm']}  days={r['days']}  since={r['since']} UTC")
    print(f"  品种 {r['pairs']} | 窗口内 long_entry {r['n_entries']} 个 | DB 实际开仓 {r['n_trades']} 笔")
    print(f"  bot 首次心跳(≈上线) UTC: {r['first_heartbeat_utc']}")
    if not r["entry_bars"]:
        print("  → 最近窗口内无入场信号（正常，V2 低频）")
    else:
        print("  窗口内入场信号明细：")
        for e in r["entry_bars"]:
            print(f"     {e['pair']:<18} {e['bar']} close={e['close']}")
    if r["missed"]:
        print(f"  ⚠️ 疑似漏单（窗口内信号、bot 上线后、无对应 trade）：{len(r['missed'])} 条")
        for m in r["missed"]:
            print(f"     ! {m['pair']:<18} {m['bar']} close={m['close']}")
        sys.exit(1)
    print("  [ok] 无上线后漏单")


if __name__ == "__main__":
    main()
