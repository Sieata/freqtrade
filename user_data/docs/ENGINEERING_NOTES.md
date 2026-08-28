# 工程经验笔记（坑与解法）

> 2026-08-28 首次整理。来源：Mac 从零搭建环境 + 数据补齐 + 前向测试启动过程。
> 策略研究方法论见 `STRATEGY_WORKFLOW.md`；本文件只记工程/运维教训，按"症状 → 根因 → 解法"组织。

---

## 一、环境搭建（macOS，无 brew）

1. **`import freqtrade` 成功 ≠ 环境可用**。CWD 在源码根目录时 import 的就是本地包，`pip3 list` 里可能什么都没有（本机系统 Python 3.9，freqtrade 2026.8 要求 ≥3.11）。判断环境是否真就绪：
   ```bash
   .venv/bin/python -c "import talib, talib.abstract, pandas, ccxt, freqtrade; import freqtrade.optimize.backtesting"
   ```
2. **装 uv（无需 sudo）**：`curl -LsSf https://astral.sh/uv/install.sh | sh` → `~/.local/bin/uv`；`uv venv .venv --python 3.12`。
3. **TA-Lib 在 macOS 12 装不上**：PyPI wheel 标 `macosx_13_0`，pip 会回退源码编译然后因缺 C 库失败。
   解法：下载 cp312 macosx_13_0_x86_64 wheel **直接解包进 site-packages**（纯 C 计算，实测 macOS 12 可用）。
   根治：源码编 TA-Lib C 库（需要 brew 或手动编译）。
4. **cryptography ≥49 没有 x86_64 macOS wheel**（只发 arm64），而 ccxt 4.5.71 要求 `>=49,<51`。
   解法：源码编译 —— 先编 OpenSSL 到用户目录（`./config no-shared --prefix=$HOME/.local/openssl`，约 5 分钟），
   再 `OPENSSL_DIR=$HOME/.local/openssl uv pip install cryptography==50.0.0 --no-binary cryptography`
   （maturin 构建后端会自动准备 Rust，无需预装）。
5. **`technical` 包是依赖陷阱**：它依赖 ta-lib/bottleneck，会把 uv 解析拖崩；freqtrade 核心、本项目策略、
   甚至 technical 自身的 freqtrade 使用场景都不需要它 → 不装。bottleneck 在 freqtrade 源码里零引用，也可跳过。
6. **uv 对 editable 安装（`pip install -e .`）读的是源码 pyproject.toml**，改 dist-info METADATA 没用——
   只要 env 里有 freqtrade 的 editable 记录，uv 解析就会强行拉 ta-lib/bottleneck。
   绕法：全部 `--no-deps` 精确安装 + 用闭包脚本补传递依赖（`user_data/scripts/dep_closure.py`，迭代到输出为空）。
7. **`--no-deps` 的代价**：传递依赖要自己配版本。典型：pydantic-core 必须与 pydantic 严格配对
   （报错会直接给出需要的版本号，照装即可）。
8. requirements.txt 是全量固定清单（freqtrade 官方即如此），配合 `--no-deps` 逐项安装等价于完整环境。

---

## 二、网络与代理（国内网络）

1. **binance API 直连全超时；系统代理不在 shell 环境里**。Clash 类代理通常监听 `127.0.0.1:7897`
   （HTTP/HTTPS/SOCKS 同端口），终端必须显式 `export https_proxy=http://127.0.0.1:7897 http_proxy=...`。
   `scutil --proxy` 可查系统代理配置；`netstat -an | grep LISTEN` 可确认端口。
2. **freqtrade 的两条数据路径代理行为不同**：download-data 走 sync ccxt(requests)，认环境变量即可；
   live/dry-run 机器人走 aiohttp 异步，还需在 config 的 `ccxt_config` 里加 **`"aiohttp_trust_env": true`**
   （config_paper_v2.json 已加）。漏了第二条的表现：机器人能启动但永远拉不到 K 线。
3. **binance WAF 403 规律**：`/fapi/v1/fundingRate` 带 2021 年老 `startTime` 的查询稳定 403，
   近期 `startTime` 正常。所以补历史数据的组合拳是：**历史走 vision 桶 + 增量走 API**。
4. **data.binance.vision 直连可达（不需要代理！）**，月度包齐全：
   `data/futures/um/monthly/{klines,fundingRate,markPriceKlines}/<SYMBOL>/<SYMBOL>-<period>.zip`。
   funding 月度包 CSV 列：`calc_time, funding_interval_hours, last_funding_rate`。
   转换脚本：`user_data/scripts/import_funding_vision.py`（funding feather 结构 = date + open=费率，其余列填 0）。
   注意 vision 没有 funding 的 daily 包（404），当月数据只能走 API。
5. **GitHub SSH 22 端口直连正常**（不需要代理），push 无障碍。
6. K 线/mark 走 API 代理下载没问题，被风控的通常只有 funding 端点——别因为 403 就全量重下。

---

## 三、freqtrade 工程坑

1. **enter_tag 两连坑**（CrashBuyV1 分级仓位从未生效的根因，2026-08-28 修复）：
   - 列名必须是 `enter_tag`（写 `entry_tag` 无效，引擎不认）；
   - **必须在 `populate_entry_trend` 里赋值** —— `advise_entry`（strategy/interface.py）在调用它之前
     会把 `enter_tag` 列清空，写在 `populate_indicators` 里会被无声抹掉。
   症状：回测结果与"分级仓位没生效"完全一致、trade 记录里 tag 全空。修复前后对比见 RESEARCH.md 第九节。
2. **满仓复利口径的逐品种归因不可用于选池**（交易顺序 × 复利路径的假象）；独立口径（每笔固定 $1,000）
   才是品种 edge 的可信度量 → `user_data/scripts/pool_review.py`（品种 × 年度矩阵 + 集中度 + 止损缺口检查）。
3. **回撤口径**：文档里"钱包口径回撤" = `max_relative_drawdown`，不是 `max_drawdown_account`（后者同期只有 20.2%）。
4. **dry-run DB 默认落在 CWD**：`sqlite:///tradesv3.dryrun.sqlite` 是相对路径 → config 显式
   `"db_url": "sqlite:///user_data/tradesv3.dryrun.sqlite"`，且启动脚本里 `cd $ROOT`。
5. **freqtrade 日志不打印策略 SHA**。冻结完整性靠启动前 `shasum -a 256` 与冻结快照比对
   （paper_start.sh 已内置，不匹配拒绝启动）——不要指望事后从日志里 grep SHA。
6. **对账纪律**：复跑一律 `--cache none`；timerange 用显式右端点（如 `20220101-20260828`），
   否则"数据截止到几点"不同会造成数字对不上；数据一律从 2021-01-01 起保证暖机完整（教训见 FREEZE_V2.md 第八节）。
7. **回测不含滑点/盘口深度**；费率可用 `--fee X` 覆盖做摩擦敏感性（2026-08-28 结论：V2 在 0.3%/边下
   4.6 年仍 16 倍，edge 鲁棒）。止损缺口回测只建模"下一根 K 线开盘跳空成交"
   （实测最大穿透 -10.63% vs -10% 止损）。
8. **zsh 不对未加引号的变量/替换输出做分词**：`--pairs $P` 会把整串当一个参数（报 "No pair in whitelist"）。
   用数组 `P=(A B C)` + `--pairs $P`，或 `$=P`。本项目已踩两次。
9. **机器人健康判断**：看 heartbeat（每分钟一条，`state='RUNNING'`）+ `grep -c TemporaryError` 日志；
   uvicorn.error 开头的行是日志器名不是错误。
10. **本地结果 zip 的关键指标**（bt_summary.py 读取）：`total_trades / profit_total_abs / winrate /
    profit_factor / max_relative_drawdown`；zip 内 trades 列表可用于独立口径复核，无需重跑。
11. **离线回测仍要联网**（2026-08-29，validate_strategy.py 踩坑）：backtesting 启动时会
    `reload_markets`（GET api.binance.com exchangeInfo），网络不通直接 exit 2
    `TemporaryError`。且 freqtrade 的市场加载走 aiohttp —— **aiohttp 不认 shell 代理环境变量**，
    必须 config 里 `ccxt_config.aiohttp_trust_env: true`（config_paper_v2 早已加，
    2026-08-29 补进 config_perpetual / config_bigmove）。两个坑叠加时症状是"回测还没开始就 TemporaryError"。
12. **固定每笔本金的池测试两个配置项缺一不可**：`--stake-amount 1000` + `--dry-run-wallet`
    ≥ 本金×最大并发仓×1.2。只设前者会报 "Starting balance (990 USDT) is smaller than stake_amount"
    配置错误（dry_run_wallet 默认 1000 × tradable_balance_ratio 0.99）。

---

## 四、运维备忘

1. **paper 机器人存活依赖**：本机不睡眠（周末持仓 + 无交易所侧止损）+ Clash 代理存活。开盘前看一眼 heartbeat 时间戳。
2. **每周记录**：`.venv/bin/python user_data/scripts/paper_status.py`；forward-test 期间策略文件不可改
   （SHA 变化 = 本次作废，见 paper/FREEZE_V2.md）。
3. **数据已随仓库分发**（`user_data/data/binance/futures/`，见其中 README.md）：新机器 clone 即用，
   `ensure-data.sh` 只做增量。feather 整文件重写 → 每次全量刷新 git 历史约 +23MB，按月刷新即可。
4. **冻结文档的 SHA 字段会过期**：策略文件在冻结后同日又优化过、文档没跟上（FREEZE.md 曾记错 V1 SHA）。
   冻结时以"快照文件的实际 SHA"为准，改动必须同步更新文档并注明。
