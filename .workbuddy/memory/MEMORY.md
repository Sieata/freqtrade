# 项目长期记忆（freqtrade 策略研究）

> **本文件只放索引、纪律与易错的元知识**：结论正文一律以 `RESEARCH.md` 为准，这里只写指针，
> 不复制结论（两处各写一份 = 迟早改一忘一）。
> 详细工程坑见 `AGENTS.md` 与 `user_data/docs/ENGINEERING_NOTES.md`；
> 当前方向与可用策略见 `RESEARCH.md` 顶部「当前状态」。最后核对：2026-10-19。
>
> 目录约定：`memory/` 只保留本索引；**按日流水不再入库**（2026-09-22 已删、2026-09-21 已降级
> 为时间线索引）。新结论写进 `RESEARCH.md` 对应章节，这里最多加一行指针。

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
- **2026-10-07 结论（RESEARCH 26.7）：V2 建议按 TOP10 固定仓位上实盘**，不按 paper 冻结的
  8 币满仓复利 1 仓。复利只是把每笔全押（回撤 ~30% vs ~5%），风险调整后无优势；A 的 VAL
  优势 40% 来自 BANK/CYS 小币。paper 冻结实例继续跑，但**其收益数字不作实盘预期**。

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
  用法与结论见 RESEARCH 第十九节。
- **展示口径（0.4 v2，2026-10-07，权威：STRATEGY_WORKFLOW 第〇节 0.4）**：逐年收益率 =
  当年利润 **÷ 钱包（池规模 × 1.2 × $1,000）**，每年重置钱包、跨年不复利。
  **「÷$1,000」已退役**（TOP10 会显示 +333% 这类不对应真实资金的数字）；要表达每份 $1,000
  赚多少就写绝对 $（如 2022 年 +$3,336 = 27.8% × $12,000）。2026-10-07 前的报告与
  RESEARCH 旧节仍是旧口径，**只能当量级参考**（数据切片也不同，见下节）；钱包口径年化
  分母随池规模变，**池间不可横比**。
- 结论：**TOP5 上只有 WeekendReverseV2 与 CrashBuyV2 两段全门禁通过**（年化质量最好）；
  OIFlushV2 可作小注分散腿（Tier B 四门禁全过，但 n≤22）；BigMoveV1 与 FS 在 TOP5 上不成立
  （FS 的 TOP10 VAL 通过靠 ZEC）。工具：`arm_stats.py`（逐笔 t 检验/自举 CI/集中度）。
- 小池固有约束：5 品种下「≥80% 品种盈利」等价于最多 1 个品种能亏，容错极窄。
- **CrashBuyV2 已于 2026-10-07 归档**（观察名单，未过门禁，不再投入）；上文「TOP5 两段全过」
  是其归档前的描述性结果，勿据此复用。

## 单年切片结论（数字以 RESEARCH 26.6 为准）

- 工具 `year_slice.py`（按自然年切片，独立 $1,000/笔）；2026 = 下跌年（BTC −11.7%）。
- **V2 TOP10（0.4 v2 口径 ÷钱包，数据至 2026-10-07）**：
  TEST 2022 +27.8% / 2023 +19.5% / 2024(8 个月) +3.5%；
  VAL 2024(4 个月) +5.9% / 2025 +19.5% / 2026(9 个月) +8.7%。
- 结构结论（2026-09-21 旧数据切片得出，仍成立）：2026 的利润**全部来自中盘**
  （DOGE/ZEC/HYPE/XMR），蓝筹五币贡献接近零 → 这是 top5/top2 上 V2 大幅缩水的根源。
- **旧的「+334% / +234% / +106% / +226% / +89%」是 ÷$1,000 口径且数据切片不同，
  不能简单 ÷12 换算**——要引用就重新跑 `year_slice.py`（现已按 v2 输出）。
- 摩擦：`--fee 0.001`（0.1%/边）相对收益约降 10%；叠加止损滑点，实盘按回测的 ~7 折估。

## 已达成的结论（勿重复试）

- V2 参数空间已闭合：止损轴（收紧单调变差）、止盈轴（4~6% 宽峰但仅 +2.7%）、
  入场端 7 特征（无跨腿稳定变量）。
- 明确优于现行配置的方向：**池内分散**（`max_open_trades` 开到池规模上限），
  质量指标大幅改善；**敞口是线性旋钮不是优化项**。
- 提升收益的唯一干净方向 = 提高 TOP10 内信号密度（新入场逻辑），不是调参或扩池。
- 横截面族已证伪（RESEARCH 24）：F1 买最弱 / F4 买最强 / F2 正向腿全灭，8-9 品种
  池的横截面排名=噪音；时段扫描（F3）无独立 edge。
- **CrashPortfolioV1（BTC 崩盘日无确认接最弱 3 个）已 VAL 关闭**（RESEARCH 25）：
  TEST 全过 +401pp 但 2026 磨损市 -106pp、品种 5/9。确认腿的载重作用坐实——
  同事件源下 CrashBuyV2 靠收阳确认+-8%止损+trailing 2026 只 -20pp。勿再试
  「无确认接刀」变体。

## 纪律

- `WeekendReverseV2.py` **不可改动**（paper forward-test 冻结，SHA 校验，见 `paper/FREEZE_V2.md`）。
  实验一律用副本或临时 config。
  **V1 已于 2026-10-07 退役归档**（`strategies/archive/`），不再有冻结约束，也不必再动它。
- 新研究强制：调参只用 TEST 段（`20220101-20240828`），VAL 只跑一次定版候选。
- 每次修完/做完一个单元主动 commit + push 到 `origin/develop`。

## 本机环境坑

- Bash 工具里 **`mkdir` / `grep` / `tail` / `sed` / `ls` 不在 PATH**；解析一律用
  `./.venv/Scripts/python.exe - <<'PY' ... PY` heredoc，文件查找用 Glob/Grep 工具。
- 嵌套 `bash script.sh` 会被安全策略拦（触发 wsl.exe 黑名单）；直接内联命令。
- binance 需代理：`export https_proxy=http://127.0.0.1:7897 http_proxy=http://127.0.0.1:7897`。
- **本机是 Windows**：venv 解释器是 `.venv/Scripts/python.exe`（无 `.venv/bin/python`）。
  文档/历史笔记里写的 `.venv/bin/python` 在 Git Bash 下也不存在，照抄会失败。
- **归档路径（2026-10-07 收缩）**：一次性研究脚本 → `user_data/scripts/archive/`；
  退役策略 → `user_data/strategies/archive/`（freqtrade 不递归扫描，不会被加载）；
  V1/H8c 的 paper 文档 → `user_data/paper/archive/`。旧笔记里的路径补 `archive/` 即可。
