"""OI 数据累积器：为 OIFlushV2 的 live/paper 提前积累 OI 历史。

背景：币安 openInterestHist 端点只能查最近 ~30 天，而策略的 180d 分位窗需要长期历史。
本脚本每天（或手动）滚动拉取各品种的 4h OI 序列并入本地 feather，时间越长历史越厚，
是 OIFlush 系策略 live/paper 化的前置数据件。

用法:
  .venv/Scripts/python.exe user_data/scripts/oi_accumulate.py          # 按默认池增量拉取
  .venv/Scripts/python.exe user_data/scripts/oi_accumulate.py --pairs BTC,ETH
**在 paper 设备（24h 在线）每日 cron 运行**，本机不搞计划任务（2026-08-29 用户指令）：
  5 9 * * * cd <repo> && .venv/bin/python user_data/scripts/oi_accumulate.py >> user_data/logs/oi_accumulate.log 2>&1
注意：API 仅 30 天窗口，漏跑的日期无法回补——gap 会永久缺失。
"""
import argparse
import os
from pathlib import Path

import pandas as pd
import requests

# 计划任务环境无 shell 代理变量：FT_PROXY 优先，否则自动探测 7897（不通则直连）
_proxy = os.environ.get("FT_PROXY")
if _proxy is None:
    import socket
    try:
        socket.create_connection(("127.0.0.1", 7897), 0.3).close()
        _proxy = "http://127.0.0.1:7897"
    except OSError:
        _proxy = "none"
if _proxy != "none":
    os.environ.setdefault("https_proxy", _proxy)
    os.environ.setdefault("http_proxy", _proxy)

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "user_data" / "data" / "binance" / "futures_metrics"
DEFAULT_PAIRS = ["BTC", "ETH", "BNB", "XRP", "SOL", "ZEC", "DOGE", "ADA", "AVAX", "DOT",
                 "TRX", "HYPE", "XMR"]
URL = "https://fapi.binance.com/futures/data/openInterestHist"


def fetch_oi(sym_usdt):
    """拉最近 ~83 天的 4h OI 序列（API 上限 30 天窗口按 limit=500 覆盖）。"""
    r = requests.get(URL, params={"symbol": sym_usdt, "period": "4h", "limit": 500}, timeout=30)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return None
    df = pd.DataFrame({
        "date": pd.to_datetime([x["timestamp"] for x in rows], unit="ms", utc=True),
        "oi_usd": [float(x["sumOpenInterestValue"]) for x in rows],
    }).sort_values("date").drop_duplicates("date")
    return df


def merge_old_new(old, new):
    """合并本地与 API 数据 → (df, 新增行数, 接缝缺口天数或 None)。

    缺口检测是必需的：本文件是"漏跑即永久缺口"的数据件，只报"本次 +N"会把
    "补上了 38 行但中间断了 9 天"说成一切正常。4h 粒度下正常接缝 ≤2 天
    （cutoff 回看 2 天），超过即说明期间有漏跑。
    """
    if old is None or old.empty:
        return new, len(new), None
    cutoff = old["date"].max() - pd.Timedelta(days=2)
    new = new[new["date"] > cutoff]
    if new.empty:
        return old, 0, None
    gap_days = (new["date"].min() - old["date"].max()).total_seconds() / 86400
    gap = gap_days if gap_days > 2.5 else None
    df = pd.concat([old, new]).sort_values("date").drop_duplicates("date")
    # 净新增 = 合并后总行数 − 原行数。cutoff 回看 2 天必然重取到已有数据，
    # 用 len(new) 报"本次 +N"会把重叠行算成新增（实测 12 行重叠全被误报）。
    return df, len(df) - len(old), gap


def atomic_to_feather(df, path):
    """先写同目录临时文件再 os.replace：进程中断/断电不会留下半截 feather。

    非原子写入的代价是不对称的——本文件无法从 API 回补 30 天以前的历史，
    一次坏写等于永久丢数据。
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        df.to_feather(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def accumulate(pair):
    sym = f"{pair}USDT"
    path = OUT / f"{pair}_USDT_USDT-4h-oi_live.feather"
    old = pd.read_feather(path) if path.exists() else None
    new = fetch_oi(sym)
    if new is None or new.empty:
        return f"[skip] {pair}: API 无数据"
    df, added, gap = merge_old_new(old, new)
    if added == 0:
        return f"[ok] {pair}: 无新增"
    atomic_to_feather(df, path)
    msg = (f"[ok] {pair}: {len(df)} 行 {str(df['date'].min())[:10]} -> {str(df['date'].max())[:10]}"
           f"（本次 +{added}）")
    if gap is not None:
        msg += f"  [!] 接缝缺口 {gap:.1f} 天，API 已过 30 天窗口的部分无法回补"
    return msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default=",".join(DEFAULT_PAIRS))
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for p in [x.strip() for x in args.pairs.split(",") if x.strip()]:
        try:
            print(accumulate(p), flush=True)
        except Exception as e:
            print(f"[FAIL] {p}: {e}", flush=True)


if __name__ == "__main__":
    main()
