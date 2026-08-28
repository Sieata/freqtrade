# AGENTS.md — 本仓库工作常识（给 AI 会话 / 新会话速查）

> 详细版（症状→根因→解法）：`user_data/docs/ENGINEERING_NOTES.md`。
> 策略研究纪律：`STRATEGY_WORKFLOW.md`；研究结论与失败记录：`RESEARCH.md`。

## 环境

- Python 一律用 `.venv/bin/python`（uv 管理，Python 3.12；系统 python3 是 3.9 且无依赖，别用）。
- 验证环境：`.venv/bin/python -c "import talib, talib.abstract, pandas, ccxt, freqtrade; import freqtrade.optimize.backtesting"`
- 本仓库已入 git：K 线/funding/mark 数据在 `user_data/data/binance/futures/`，clone 后离线可回测，**不要重复下载全量数据**。

## 网络（国内网络，Clash 代理）

- binance API 需代理：`export https_proxy=http://127.0.0.1:7897 http_proxy=http://127.0.0.1:7897`（shell 环境变量默认没有）。
- WAF 403 只拦 funding 的老 startTime 查询：历史从 data.binance.vision 补（`user_data/scripts/import_funding_vision.py`，直连无需代理），近期增量走 API。
- GitHub SSH 22 直连正常，push 不需要代理。
- 数据更新：`./ensure-data.sh`（增量；FT_PROXY 环境变量可覆盖代理）。

## 常用命令

```bash
# 回测（--cache none 对账纪律；pairs 必须用数组传参，zsh 不分词）
P8=(BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT ZEC/USDT:USDT BANK/USDT:USDT CYS/USDT:USDT HYPE/USDT:USDT)
.venv/bin/python -m freqtrade backtesting --config user_data/config_perpetual.json \
  --strategy WeekendReverseV2 --timerange 20220101-20260828 --pairs $P8 --cache none --export trades

# 结果速览 / 币池独立口径复核
.venv/bin/python user_data/scripts/bt_summary.py <result.zip>
.venv/bin/python user_data/scripts/pool_review.py <result.zip> --worst 10

# paper forward-test（V2，进行中）
./user_data/scripts/paper_start.sh        # 启动（内置 SHA 校验，不匹配拒绝启动）
.venv/bin/python user_data/scripts/paper_status.py   # 周记录
```

## 禁改 / 高危

- **`user_data/strategies/WeekendReverseV1.py` 与 `WeekendReverseV2.py` 不可改动**：SHA 已冻结，
  paper forward-test 进行中，改动 = 测试作废（paper/FREEZE_V2.md）。改参数做实验用副本或策略参数文件。
- forward-test 期间只记录不改参数；判据与周期见 `user_data/paper/FREEZE_V2.md`。

## 高频坑速记

- `enter_tag` 列名必须准确，且只能在 `populate_entry_trend` 里赋值（`advise_entry` 会先清空该列）。
- zsh 不分词：`--pairs $P` 用数组；`$(cat file)` 可用，`$VAR` 整串不可用。
- 满仓复利的逐品种归因是路径假象，选池/评估用独立口径（每笔固定 $1,000，见 pool_review.py）。
- 文档"钱包口径回撤" = `max_relative_drawdown`。
- dry-run DB 路径要显式配置（`db_url`），默认落在 CWD。
- 回测不含滑点；摩擦测试用 `--fee` 覆盖。
- freqtrade 日志不打印策略 SHA，冻结校验靠 `shasum -a 256` 与 `user_data/paper/frozen_*.py` 比对。
