# 数据目录（随仓库分发）

> 用途：避免每次换机器/被风控后重新拉全量数据。clone 本仓库即可离线回测。

## 内容

`binance/futures/`（约 23MB，56 个 feather 文件）：

- `*_USDT_USDT-4h-futures.feather` / `*-1d-futures.feather`：14 品种 K 线（2021-01-01 起）
- `*-1h-mark.feather`：标记价格（futures 回测强平/资金费计算用）
- `*-1h-funding_rate.feather`：资金费率历史（8h 间隔；HOME/BANK/CYS/HYPE 为其真实间隔）

品种：BTC ETH SOL XRP BNB ZEC HOME BANK CYS HYPE DOGE ADA AVAX DOT

## 数据来源

均为币安 USDT 永续官方数据：

- K 线 / mark：`fapi.binance.com` API（`ensure-data.sh`，需代理）
- funding：API 被 WAF 403 拦截的部分从 `data.binance.vision` 月度包重建
  （`user_data/scripts/import_funding_vision.py`），8 月增量走 API 近期时间戳（403 只拦 2021 老起点）

## 使用

```bash
# 新机器：clone 后直接回测，无需先下载数据
# 更新到最新（增量，可重复运行）：
./ensure-data.sh          # FT_PROXY=http://127.0.0.1:7897 可改代理；FT_PROXY=none 直连
```

## 注意

- freqtrade 更新数据会重写整个 feather 文件，git 历史随刷新次数线性增长（当前 23MB/次全量上限），
  建议按月增量刷新即可，回测/前向测试对小时级新鲜度不敏感。
- 数据为公开行情，不含任何账户/密钥信息。
