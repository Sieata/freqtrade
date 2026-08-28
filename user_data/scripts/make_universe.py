"""生成两份币池快照文件（user_data/universe/）——币池口径的唯一权威来源。

  pairs_core.txt    CORE 实盘池   CoinGecko 市值 Top N 中在币安 USDT-M 有永续者（稳定币/封装币剔除）
  pairs_volume.txt  VOLUME 泛化池  币安 USDT-M 24h 成交量 Top N（稳定币/封装币剔除）

纪律：
  - 实盘 / paper 只允许跑 CORE 内品种；VOLUME 池仅用于泛化能力测试。
  - 这两份文件是"生成日快照"：重新生成 = 币池变更，需在 RESEARCH.md 记录一次。
  - 今日成交量榜带幸存者偏差（今天热门 ≠ 历史一直热门），泛化结论要结合
    validate_strategy.py 输出的单年集中度看，防"新币单年 pump"假 edge。

网络：走系统代理（FT_PROXY 环境变量可覆盖，FT_PROXY=none 直连）。
代理链路偶发连接重置（Clash），网络调用统一带重试。

用法:
  .venv/bin/python user_data/scripts/make_universe.py                    # 生成两份
  .venv/bin/python user_data/scripts/make_universe.py --core-top 50 --volume-top 30
  .venv/bin/python user_data/scripts/make_universe.py --volume-only      # 只重生成 volume 榜
"""
import argparse
import datetime as dt
import time
from pathlib import Path

import requests

UNIVERSE_DIR = Path(__file__).resolve().parent.parent / "universe"

# 稳定币 / 封装币 / 商品代币（显式清单，不用模糊正则防误伤 JUP 这类）
EXCLUDE = {
    # 稳定币
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "USD1", "USDE",
    "USDS", "USDTB", "PYUSD", "FRAX", "SUSD", "GUSD", "HUSD", "XUSD", "USDF",
    "USR", "BFUSD", "AEUR", "EUR", "EURI", "BSC-USD", "USDG", "USDX",
    "USTC", "DOLA", "CRVUSD", "MIM", "LUSD",
    # 封装 / 质押衍生品
    "WBTC", "WETH", "WBNB", "WSOL", "STETH", "WSTETH", "RETH", "WEETH",
    "EZETH", "PUFETH", "METH", "CBBTC", "CBXETH", "LBTC", "SOLVBTC", "JITOSOL",
    "JUPSOL", "MSOL", "BNSOL", "WBETH", "TBTC", "RENBTC", "HBTC",
    # 商品代币
    "XAUT", "PAXG",
}


def proxy_env():
    """返回 (env_dict, requests_proxies_dict)；FT_PROXY 可覆盖，FT_PROXY=none 直连。"""
    import os

    p = os.environ.get("FT_PROXY", "http://127.0.0.1:7897")
    if p == "none":
        return {}, {}
    return (
        {"https_proxy": p, "http_proxy": p},
        {"https": p, "http": p},
    )


def with_retries(fn, *args, tries=4, wait=3, **kwargs):
    """网络调用统一重试：代理链路偶发 Connection reset。"""
    last = None
    for i in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 —— 网络/限流异常都可能，统一重试
            last = e
            if i < tries - 1:
                print(f"  [!] 网络失败({i + 1}/{tries}): {type(e).__name__}，{wait}s 后重试", flush=True)
                time.sleep(wait)
    raise last


def coin_perp_markets(ex):
    """活跃 USDT 本位 + 加密原生（underlyingType=COIN）的合约列表。"""
    return [
        m for m in ex.markets.values()
        if m.get("swap") and m.get("quote") == "USDT" and m.get("active")
        and (m.get("info") or {}).get("underlyingType") == "COIN"
    ]


def load_perp_markets():
    """返回 (ccxt实例, {base: pair}, {base: onboard_date})，仅加密原生 USDT 永续。"""
    import ccxt

    _, proxies = proxy_env()
    ex = ccxt.binanceusdm()
    ex.session.proxies = proxies or None
    with_retries(ex.load_markets)
    pairs, onboard = {}, {}
    for m in coin_perp_markets(ex):
        base = m["base"]
        if base not in pairs or m["symbol"].endswith(":USDT"):
            pairs[base] = m["symbol"]
        info = m.get("info") or {}
        if info.get("onboardDate"):
            onboard[base] = dt.datetime.fromtimestamp(
                int(info["onboardDate"]) / 1000, dt.UTC
            ).strftime("%Y-%m-%d")
    return ex, pairs, onboard


def fmt_usd(x):
    x = float(x)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if x >= div:
            return f"${x / div:.2f}{suf}"
    return f"${x:,.0f}"


def build_core(ex, top_n, perp, onboard, today):
    """CoinGecko 市值榜（一次取 250 名），顺序取前 top_n 个有币安永续的币。"""
    url = (
        "https://api.coingecko.com/api/v3/coins/markets"
        "?vs_currency=usd&order=market_cap_desc&per_page=250&page=1"
    )
    _, proxies = proxy_env()
    rows = with_retries(
        requests.get, url, proxies=proxies or None, timeout=30, tries=3
    ).json()

    picked = []
    for row in rows:
        sym = (row.get("symbol") or "").upper()
        if sym in EXCLUDE or sym not in perp:
            continue
        picked.append(row)
        if len(picked) >= top_n:
            break

    lines = [
        f"# CORE 实盘池 —— 币安 USDT-M 永续 · 市值 Top{len(picked)}",
        f"# 生成: {today} | 数据源: CoinGecko 市值榜 + ccxt binanceusdm 合约表",
        f"# 规则: CoinGecko 市值前 250 名中在币安有 USDT 永续者，按市值降序取前 {top_n}；剔除稳定币/封装币/商品代币",
        "# 用途: 实盘/paper 只允许跑此池内品种。重生成: make_universe.py --core-only（重生成需在 RESEARCH.md 记录）",
        "#",
    ]
    if len(picked) < top_n:
        print(f"[!] 警告: 市值前 250 名中只凑到 {len(picked)}/{top_n} 个有永续的品种", flush=True)
    for rank, row in enumerate(picked, 1):
        sym = row["symbol"].upper()
        ob = onboard.get(sym, "?")
        lines.append(f"{perp[sym]} # rank={rank} mcap={fmt_usd(row.get('market_cap') or 0)} onboard={ob}")
    return "\n".join(lines) + "\n"


def build_volume(ex, top_n, perp, onboard, today):
    """币安 USDT-M 24h 成交量榜（复用已 load_markets 的实例）。"""
    tickers = with_retries(ex.fetch_tickers)

    scored = []
    for m in coin_perp_markets(ex):
        base = m["base"]
        if base in EXCLUDE:
            continue
        t = tickers.get(m["symbol"]) or {}
        qv = t.get("quoteVolume") or 0
        if qv > 0:
            scored.append((base, qv))
    scored.sort(key=lambda kv: kv[1], reverse=True)
    picked = scored[:top_n]

    lines = [
        f"# VOLUME 泛化测试池 —— 币安 USDT-M 永续 · 24h 成交量 Top{len(picked)}",
        f"# 生成: {today} | 数据源: ccxt binanceusdm 24h ticker (quoteVolume)",
        f"# 规则: 按 24h USDT 成交量降序取前 {top_n}；剔除稳定币/封装币/商品代币",
        "# 用途: 仅用于泛化能力测试，禁止直接实盘。今日热门榜有幸存者偏差——单年集中度必看（validate_strategy.py 报告）",
        "#",
    ]
    for rank, (base, qv) in enumerate(picked, 1):
        ob = onboard.get(base, "?")
        lines.append(f"{perp[base]} # rank={rank} vol24h={fmt_usd(qv)} onboard={ob}")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="生成 CORE / VOLUME 币池快照")
    ap.add_argument("--core-top", type=int, default=50)
    ap.add_argument("--volume-top", type=int, default=30)
    ap.add_argument("--core-only", action="store_true")
    ap.add_argument("--volume-only", action="store_true")
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    print("拉取币安 USDT-M 合约表 ...", flush=True)
    ex, perp, onboard = load_perp_markets()
    print(f"  活跃 USDT 永续: {len(perp)} 个", flush=True)

    UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    if not args.volume_only:
        print("生成 CORE 池（CoinGecko 市值榜）...", flush=True)
        core = build_core(ex, args.core_top, perp, onboard, today)
        (UNIVERSE_DIR / "pairs_core.txt").write_text(core)
        n = len([l for l in core.splitlines() if l and not l.startswith("#")])
        print(f"  → user_data/universe/pairs_core.txt ({n} 个)")
    if not args.core_only:
        print("生成 VOLUME 池（24h 成交量榜）...", flush=True)
        vol = build_volume(ex, args.volume_top, perp, onboard, today)
        (UNIVERSE_DIR / "pairs_volume.txt").write_text(vol)
        n = len([l for l in vol.splitlines() if l and not l.startswith("#")])
        print(f"  → user_data/universe/pairs_volume.txt ({n} 个)")


if __name__ == "__main__":
    main()
