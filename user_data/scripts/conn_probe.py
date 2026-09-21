"""连通性探针：研究台用到的全部数据端点（走代理 + 不走代理各测一次）。"""
import json
import time
from urllib import request as rq

ENDPOINTS = [
    ("vision 历史包", "https://data.binance.vision/data/futures/cm/monthly/klines/ETHUSD_PERP/4h/ETHUSD_PERP-4h-2026-08.zip"),
    ("dapi 币本位", "https://dapi.binance.com/dapi/v1/fundingRate?symbol=ETHUSD_PERP&limit=1"),
    ("fapi U本位", "https://fapi.binance.com/fapi/v1/fundingRate?symbol=ETHUSDT&limit=1"),
    ("spot 现货", "https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=4h&limit=1"),
    ("Hyperliquid", "https://api.hyperliquid.xyz/info"),
]


def probe(url, proxy, method="GET", body=None):
    op = rq.build_opener(rq.ProxyHandler({"https": proxy, "http": proxy})) if proxy else \
         rq.build_opener(rq.ProxyHandler({}))
    hdrs = {"User-Agent": "probe/0.1"}
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    req = rq.Request(url, data=body, headers=hdrs, method=method)
    t0 = time.time()
    try:
        with op.open(req, timeout=15) as r:
            data = r.read()
            return f"{r.status}  {time.time() - t0:.2f}s  {len(data):,}B"
    except Exception as e:
        return f"FAIL {type(e).__name__}: {str(e)[:80]}"


PROXY = "http://127.0.0.1:7897"
hl_body = json.dumps({"type": "meta"}).encode()

print(f"{'端点':<14}{'直连':<34}{'代理':<34}")
for name, url in ENDPOINTS:
    body = hl_body if "hyperliquid" in url else None
    method = "POST" if body else "GET"
    direct = probe(url, None, method, body)
    via = probe(url, PROXY, method, body)
    print(f"{name:<14}{direct:<34}{via:<34}")
