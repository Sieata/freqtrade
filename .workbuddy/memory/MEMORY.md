# 项目长期记忆（freqtrade 策略研究）

> 详细工程坑见 `AGENTS.md` 与 `user_data/docs/ENGINEERING_NOTES.md`；研究结论见 `RESEARCH.md`。

## 仓位模型（2026-09-21 确立，易错）

- 公式：`stake = (available_amount + val_tied_up) / max_open_trades`（`freqtrade/wallets.py`）。
  故 **`tradable_balance_ratio` 决定总敞口、`max_open_trades` 只决定分散度，两者正交**。
- V2 实际配置是「`max_open_trades=1` + `stake_amount=unlimited` + `ratio 0.99`」= 单仓 99% 满仓复利。
  **用 `--max-open-trades 10` 跑回测会把仓位设计整个替换掉**，得不到 V2 的真实风险形态。
- CLI **没有** `--tradable-balance-ratio`；比例仓位只能靠临时 config 文件。
- 并发/分散度曲线**全档必须共用同一钱包**，否则利润与回撤百分比分母不同、不可比。
- `CAGR/最大回撤` 在复利场景**不可作决策指标**（CAGR 是增长倍数的凹函数、回撤近似线性于仓位
  → 系统性偏袒大仓位）。报告须并列 PF / SQN / Sharpe / p 值。
- 工具：`user_data/scripts/sizing_sweep.py`（sizing / slots / dispersion 三模式）。

## 币池快照的覆盖限制（重要方法论警告）

- `user_data/universe/pairs_*.txt` 是 **2026-08-29 快照**。用今天的市值榜回测 2022 年
  存在**幸存者偏差 + 成分漂移**，宽池回测的绝对收益数字不可采信。
- 实测全程覆盖（2022-01-01 起有数据）：**TOP10 仅 9 个（HYPE 缺）；core50 仅 23 个
  （27 个中途上线、16 个完全无数据）；volume30 仅 12 个**。
- 实证结论：扩池的**增量信号 PF 只有 1.06~1.16**（core50/volume30），扣摩擦后约等于零
  → 「TOP10 市值池优先」纪律有独立数据支持；垃圾币是**负贡献**而非仅噪音。

## 币池口径（2026-09-21 扩充）

- 评估池：`top10`（第一口径）／**`top5`**（纯蓝筹 BTC/ETH/BNB/XRP/SOL，剥离中盘贡献）／
  `core`(50) ／ `volume`(30)。`validate_strategy.py --pool top5`、`tier_b_eval.py --pool top5`。
- **`top2`**（只 BTC/ETH）= 分散度极限池，**非实盘建议**。实测 V2 收益缩到 TOP10 的 1/10
  （TEST +$6,098→$634）、TEST 段 p 从 1.1e-8 掉到 0.081（不显著）、最大品种占比 27%→57%。
  用法与结论见 RESEARCH 第十九节。跨池比收益一律用"每年重置 $1,000"逐年口径，
  钱包口径年化分母随池规模变、不可横比。
- 结论：**TOP5 上只有 WeekendReverseV2 与 CrashBuyV2 两段全门禁通过**（年化质量最好）；
  OIFlushV2 可作小注分散腿（Tier B 四门禁全过，但 n≤22）；BigMoveV1 与 FS 在 TOP5 上不成立
  （FS 的 TOP10 VAL 通过靠 ZEC）。工具：`arm_stats.py`（逐笔 t 检验/自举 CI/集中度）。
- 小池固有约束：5 品种下「≥80% 品种盈利」等价于最多 1 个品种能亏，容错极窄。

## 单年切片结论（2026-09-21）

- 工具 `year_slice.py`（按自然年切片，独立 $1,000/笔）；2026 = 下跌年（BTC −11.7%），V2 TOP10 +89%、
  TOP5 −1% → **利润全部来自中盘（DOGE/ZEC/HYPE/XMR +895，蓝筹五币 −8）**；2026 段 p=0.036 不显著。
- 历年 V2 TOP10：2022 +334% / 2023 +234% / 2024 +106% / 2025 +226% / 2026 +89%(8mo)。
- 摩擦敏感：--fee 0.001（0.1%/边）→ 2026 +89%→+79%；叠加止损滑点 → 实盘约 +68~70%。

## 已达成的结论（勿重复试）

- V2 参数空间已闭合：止损轴（收紧单调变差）、止盈轴（4~6% 宽峰但仅 +2.7%）、
  入场端 7 特征（无跨腿稳定变量）。
- 明确优于现行配置的方向：**池内分散**（`max_open_trades` 开到池规模上限），
  质量指标大幅改善；**敞口是线性旋钮不是优化项**。
- 提升收益的唯一干净方向 = 提高 TOP10 内信号密度（新入场逻辑），不是调参或扩池。

## 纪律

- `WeekendReverseV2.py` / `WeekendReverseV1.py` **不可改动**（paper forward-test 冻结，SHA 校验）。
  实验一律用副本（如 `WeekendReverseV2Lab.py`）或临时 config。
- 新研究强制：调参只用 TEST 段（`20220101-20240828`），VAL 只跑一次定版候选。
- 每次修完/做完一个单元主动 commit + push 到 `origin/develop`。

## 本机环境坑

- Bash 工具里 **`mkdir` / `grep` / `tail` / `sed` / `ls` 不在 PATH**；解析一律用
  `./.venv/Scripts/python.exe - <<'PY' ... PY` heredoc，文件查找用 Glob/Grep 工具。
- 嵌套 `bash script.sh` 会被安全策略拦（触发 wsl.exe 黑名单）；直接内联命令。
- binance 需代理：`export https_proxy=http://127.0.0.1:7897 http_proxy=http://127.0.0.1:7897`。
