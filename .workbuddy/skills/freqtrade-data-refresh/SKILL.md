---
name: freqtrade-data-refresh
description: 增量刷新 freqtrade 仓库的 binance futures 行情数据（4h/1d/mark/funding）、做接缝完整性校验、策略复跑对账并提交入库。适用于用户说「把数据拉一下最新的」「更新数据」「补数据」「数据刷新」，或需要在新数据上复跑策略、排查数据停在旧日期、确认回撤/收益口径的场景。
agent_created: true
---

# freqtrade 数据增量刷新与复跑对账

本仓库（`Sieata/freqtrade`）的行情数据随 git 分发，clone 后离线即可回测。
本 skill 负责把数据续到最新，并用「复跑对账」证明旧数据段没被改写。

## 触发场景

- 用户要求拉最新数据 / 更新数据 / 补数据
- 发现 `.feather` 最新时间戳落后于当天
- 在新增数据上跑策略前，需要确认接缝干净
- 需要核对回测回撤口径（见「关键坑 2」）

## 前置事实

| 项 | 值 |
|---|---|
| 数据目录 | `user_data/data/binance/futures/` |
| 文件名 | `<PAIR>_<QUOTE>_<SETTLE>-<tf>-futures.feather`（`BTC/USDT:USDT` → `BTC_USDT_USDT`） |
| 代理 | `http://127.0.0.1:7897`（Clash），国内直连 binance 不通 |
| Python | `.venv/Scripts/python.exe`（Windows 布局；Unix 为 `.venv/bin/python`） |
| 起始日期 | **一律从 2021-01-01 起**（4h 策略需 250 根暖机，见 FREEZE_V2 第八节） |

## 步骤

### 1. 看现状：哪些品种停在哪天

```bash
cd <repo> && ".venv/Scripts/python.exe" - <<'PY'
import os, glob, pandas as pd
rows=[]
for f in glob.glob("user_data/data/binance/futures/*-4h-futures.feather"):
    df = pd.read_feather(f)
    rows.append((os.path.basename(f), len(df), str(pd.to_datetime(df["date"]).max())[:16]))
rows.sort(key=lambda r: r[2], reverse=True)
for r in rows[:15]: print(f"{r[0]:<44}{r[1]:>7}  {r[2]}")
print("... 共", len(rows), "个 4h 文件")
PY
```

### 2. 测通路（代理 + binance 可达）

```bash
".venv/Scripts/python.exe" - <<'PY'
import socket, os, urllib.request
s=socket.socket(); s.settimeout(3)
try: s.connect(("127.0.0.1",7897)); print("代理 7897: 通")
except Exception as e: print("代理 7897 不通:", e)
s.close()
os.environ["https_proxy"]=os.environ["http_proxy"]="http://127.0.0.1:7897"
print(urllib.request.urlopen("https://fapi.binance.com/fapi/v1/time", timeout=15).read().decode()[:60])
PY
```

之前那条 `date` 输出是 binance 服务器时间（毫秒），用来确认本机时钟没跑偏。

### 3. 下载（`ensure-data.sh` 已加固，直接跑）

```bash
cd <repo> && ./ensure-data.sh                        # 默认 14 个既有品种
./ensure-data.sh user_data/universe/pairs_top10.txt  # 按币池快照补数据
```

`ensure-data.sh` 自 2026-09-21 起为**纯 bash 内建 + venv python** 实现，不依赖
dirname/sed/tr/cut/shasum/mkdir/nohup/cat/date，受限 shell 可直接运行。
若确需手动调 freqtrade（罕见）：代理用 `os.environ` 注入 7897 后 subprocess 调
`download-data --trading-mode futures --timeframes 4h 1d --timerange 20210101-`。
**先单品种试水**（`--pairs BTC/USDT:USDT --timerange 20260901-`）再全量。

### 4. 接缝校验（必做）

```bash
.venv/bin/python user_data/scripts/data_check.py --pools top10,core,volume
# 可选：--pairs BTC/USDT:USDT,... 显式指定；--since 2026-01-01 只看尾段；
# 退出码 0=干净 / 1=有问题（可直接作 cron 告警条件）
```

判读：4h 应统一收在**最后一根已收盘 K 线**（UTC 整点，如 04:00 UTC = 12:00 北京时间；
当前 12:45 时最新为 08:00 北京时间的 00:00 UTC 那根）。若某品种单独落后，通常是
该品种上新 / 停牌 / 改名，属正常，但要在报告里点名。

### 5. 复跑对账（证明旧段没被改写）

```bash
export https_proxy=http://127.0.0.1:7897 http_proxy=http://127.0.0.1:7897
".venv/Scripts/python.exe" -m freqtrade backtesting --config user_data/config_perpetual.json \
  --strategy WeekendReverseV2 --timerange 20220101-<今天> \
  --pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT \
          ZEC/USDT:USDT BANK/USDT:USDT CYS/USDT:USDT HYPE/USDT:USDT \
  --cache none --export trades
".venv/Scripts/python.exe" user_data/scripts/bt_summary.py user_data/backtest_results/<最新zip>
```

**对账方法**：把本期利润与上期（记录在 `user_data/paper/FREEZE_V2.md` 最新一节）相减，
再从 zip 里取「上期截止日之后平仓」的交易求和。**两个数必须相等**——相等即历史段
逐笔未变、接缝干净。2026-09-21 那次的实例：559 vs 553 笔，利润差 −$6,650，
新增 6 笔净额正好 −$6,650 ✅。

```bash
# 取新增交易明细
".venv/Scripts/python.exe" - <<'PY'
import zipfile, json, pandas as pd
z = "<最新zip>"
d = json.loads(zipfile.ZipFile(z).read([n for n in zipfile.ZipFile(z).namelist()
                                        if n.endswith(".json") and "config" not in n][0]))
tr = pd.DataFrame(d["strategy"]["WeekendReverseV2"]["trades"])
tr["close"] = pd.to_datetime(tr["close_date"], utc=True)
blk = tr[tr["close"] >= "<上期截止日>"].sort_values("close")
print(blk[["pair","close","profit_abs","profit_ratio","exit_reason","stake_amount"]].to_string(index=False))
print("小计", round(blk["profit_abs"].sum()))
PY
```

### 6. 提交入库

```bash
git add user_data/data/binance/futures && git commit -m "数据：增量更新至 <日期>（N 品种，4h/1d + mark/funding）"
git push origin develop
```

提交信息里写清：上次停在哪天、本次品种范围、新增行数、接缝校验结论。
再往 `user_data/paper/FREEZE_V2.md` 追加一节「数据刷新复跑」，格式照抄第八/九节。
`user_data/logs/` 已被 gitignore，日志不必手动排除。

## 关键坑

1. **`ensure-data.sh` 已加固（2026-09-21），可直接跑**；但它只保证脚本自身可移植——
   **临时拼的命令行**仍会踩受限 shell 的 PATH 缺失（ls/grep/tail/cat 等），过滤输出用
   `python -c`。另外 `for` 循环等复合 bash 结构会触发沙箱 wsl.exe 黑名单，一律内联展开。
2. **回撤两个口径，差 12pp，极易误读。**
   - `ddW` = `max_relative_drawdown`（**钱包口径**，文档一律引用这个）
   - `ddA` = `max_drawdown_account`（账户口径，数值明显偏小）
   `bt_summary.py` 自 2026-09-21 起并列输出两者；只看 `ddA` 会把 32.1% 的回撤读成 20.2%。
3. **funding 端点会 WAF 限流**：批量拉取时 funding 整体临时封锁几分钟（连近期增量也 403）。
   等几分钟小批量重试即可。老 startTime 查询必 403，历史要走
   `user_data/scripts/import_funding_vision.py`（直连 data.binance.vision，无需代理）。
4. **`--timerange 20210101-` 是增量语义**，安全可重跑，不重不漏。
5. **回测启动也要访问 binance**（reload_markets）：config 需
   `ccxt_config.aiohttp_trust_env: true`，且 shell 带 `https_proxy`。
6. **本机不是 paper 机**：`tradesv3.dryrun*.sqlite` 在本机不存在属正常，
   paper 与 OI 累积器跑在专用 paper 设备上——不能据本机 db 推断 forward-test 运行态。
