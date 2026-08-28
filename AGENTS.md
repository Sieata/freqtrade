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

# 标准化验证（2026-08-28 起新研究强制，口径详见 STRATEGY_WORKFLOW.md 第〇节）
.venv/bin/python user_data/scripts/time_splits.py                    # 打印冻结的 TEST/VAL timerange
.venv/bin/python user_data/scripts/make_universe.py                  # 重生成币池快照 core50/volume30（需代理）
.venv/bin/python user_data/scripts/validate_strategy.py --strategy X # 一键 TEST+VAL × core+volume + 门禁 + 报告
./ensure-data.sh user_data/universe/pairs_volume.txt                 # 按币池快照补数据（新品种 funding 老数据走 import_funding_vision.py）

# paper forward-test（V2，进行中）
./user_data/scripts/paper_start.sh        # 启动（内置 SHA 校验，不匹配拒绝启动）
.venv/bin/python user_data/scripts/paper_status.py   # 周记录
```

## 研究纪律（2026-08-28 起新研究强制）

- **时间切分冻结**（`user_data/universe/splits.json`）：调参/滚动只准用 TEST `20220101-20240828`；
  VAL `20240828-` 定版候选只跑一次，跑过又改参 = 作废重来；2021 数据只作暖机；重切要改文件+RESEARCH.md 记录。
- **币池分层**（`user_data/universe/pairs_*.txt`）：实盘/paper 只允许 CORE（市值 Top50）；
  VOLUME（24h 量 Top30）只做泛化测试、禁实盘；双池同参都过才算普适。池文件是生成日快照。
- 泛化验证一律独立口径（固定 $1,000/笔，validate_strategy.py 内置）；复利口径只用于定版后单池回测。
- VAL 报告必看单年集中度，防新币单年 pump 假 edge（ZEC/BANK 教训）。

## 禁改 / 高危

- **`user_data/strategies/WeekendReverseV1.py` 与 `WeekendReverseV2.py` 不可改动**：SHA 已冻结，
  paper forward-test 进行中，改动 = 测试作废（paper/FREEZE_V2.md）。改参数做实验用副本或策略参数文件。
- forward-test 期间只记录不改参数；判据与周期见 `user_data/paper/FREEZE_V2.md`。

## Git 提交纪律（用户已明确授权，2026-08-28）

- **每完成一个可独立描述的改动单元（bug 修复 / 新脚本工具 / 文档更新 / 配置变更 / 数据更新），主动 commit + push，不要等用户提醒。**
- 粒度：逻辑相关的改动一个提交，不同主题拆开（例：数据入库与研究修复分开提交）。
- 信息：中文为主，首行说清"做了什么 + 为什么"，与仓库现有提交风格一致。
- 推送目标：`origin/develop`（默认分支）。SSH 直连可用，无需代理。
- 边界：不提交未完成/破坏状态的中间态；提交前 `git status` 过一眼，确认没有把密钥、sqlite、日志、
  来源不明的改动卷进去（遇到不明改动先问，不要顺手提交）。
- 授权范围：仅本仓库（Sieata/freqtrade develop 分支）。

## 高频坑速记

- `enter_tag` 列名必须准确，且只能在 `populate_entry_trend` 里赋值（`advise_entry` 会先清空该列）。
- zsh 不分词：`--pairs $P` 用数组；`$(cat file)` 可用，`$VAR` 整串不可用。
- 满仓复利的逐品种归因是路径假象，选池/评估用独立口径（每笔固定 $1,000，见 pool_review.py）。
- 文档"钱包口径回撤" = `max_relative_drawdown`。
- dry-run DB 路径要显式配置（`db_url`），默认落在 CWD。
- 回测不含滑点；摩擦测试用 `--fee` 覆盖。
- 回测启动也要访问 binance（reload_markets）：aiohttp 不认 shell 代理变量，回测 config 必须
  `ccxt_config.aiohttp_trust_env: true`（config_perpetual/bigmove 已加），且 shell 带 https_proxy。
- 固定每笔本金的池测试要 `--stake-amount` + `--dry-run-wallet`（≥ 本金×并发仓×1.2），否则
  "Starting balance smaller than stake_amount" 配置错误。
- freqtrade 日志不打印策略 SHA，冻结校验靠 `shasum -a 256` 与 `user_data/paper/frozen_*.py` 比对。
