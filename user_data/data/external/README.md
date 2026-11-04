# 另类数据快照（2026-11-04）

由 `user_data/scripts/fetch_altdata.py`（另类数据）与
`user_data/scripts/fetch_quality_data.py`（COT/premium/CME）抓取，供 TEST-only 研究复现。
全部为公开、免费、无需 API key 的序列；CSV 均为 UTF-8，`date` 为 UTC 日期。

| 文件 | 来源 | 覆盖 | 字段 |
|---|---|---|---|
| `fear_greed.csv` | alternative.me Fear & Greed | 2018-02-01 → 2026-11-04 | value(0–100), classification |
| `stablecoins.csv` | DefiLlama stablecoincharts | 2017-11-29 → 2026-11-04 | total / usdt / usdc（USD 流通量） |
| `dvol_btc.csv` / `dvol_eth.csv` | Deribit DVOL（1D） | 2021-03-24 → 2026-11-04 | open/high/low/close |
| `macro.csv` | Yahoo Finance | 2020-01-02 → 2026-11-04 | dxy, spx, ndx, gold, vix, us10y |
| `btc_onchain.csv` | blockchain.info charts | 2009-01-03 → 2026-10-28 | n_tx, fees_btc, hash_rate, unique_addr |
| `cftc_cot.csv` | CFTC TFF（Socrata `gpe5-46if`） | BTC 2018-04-10 → 2026-10-27（447 周）；ETH 2021-04-06 → 2026-10-27（291 周） | date/asset/oi/am_long_pct/am_short_pct/lev_long_pct/lev_short_pct/am_net_contracts/lev_net_contracts/oi_chg/avail_date |
| `premium_index_btc.csv` / `premium_index_eth.csv` | Binance USDⓈ-M premiumIndexKlines（1d） | 2021-06-01 → 2024-08-31（各 1,181 行） | date/open/high/low/close |
| `cme_futures.csv` | Yahoo Finance `BTC=F` / `ETH=F` | 2020-01-02 → 2026-11-04 | date/btc_cme/eth_cme（ETH 早期有 276 个空值） |

## 使用口径（重要）

- 分析侧一律用 `merge_asof(..., allow_exact_matches=False)` 取**严格早于信号日**的最近值，
  再用当日 00:00 UTC 开盘交易，避免「当天收盘价当天用」的前视。
- 稳定币供给可能被来源方后续修订；这里是 2026-11-04 的快照，不是当时的实时值。
- DVOL 2021-03-24 才开始，2021 年只作暖机；TEST 从 2022-01-01 起。
- FNG 是主观合成指标；宏观日线按交易日，周末/节假日用最近一个交易日值。
- 链上数据只有 BTC，不能做横截面；手续费单位 BTC，算力单位以 blockchain.info 为准。
- COT 报告日为周二，官方周五 15:30 ET 发布；`avail_date` 取报告日 +4 天（周六）作为
  保守可用日，分析侧再从 `avail_date` 取严格早于信号日的最近值。`am_*`/`lev_*`
  为占 OI 的百分比，多空两侧各自计算，不是净值的百分比。
- premium index 日线收盘对应 UTC 当日结束；分析侧取前一日值，次日 00:00 开盘交易。
- CME 为 Yahoo 连续主力合约（无展期调整），只适合方向性研究；基差用 CME 收盘对比
  Binance 同日 20:00 UTC 收盘，不是同一结算价，不能当精确套利价差。
- 这些数据只用于研究，不构成实盘建议。
