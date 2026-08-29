# 策略库总览（测量结果整理）

> 整理日期：2026-08-16
> 数据来源：`user_data/backtest_results`（644 次回测 zip + meta）、`user_data/strategies/` 源码、`RESEARCH.md`、`STRATEGY_WORKFLOW.md`
> 全量明细：`user_data/backtest_results/_all_runs_summary.csv`（644 行，含每次回测的策略/周期/笔数/胜率/利润/回撤/夏普等）

---

## 一、当前策略库（权威状态）

**现势状态以 `user_data/docs/STATUS_20260829.md` 为唯一权威**（每日收敛快照）。当前构成：
V2 引擎（TOP10 全门禁，paper 中）+ 三 Tier B 事件臂（BigMove/FS paper 待启动、OIFlushV2 12 月）
+ CrashBuy（观察名单）+ 归档（V1/OIFlushV1/carry 族）。TOP10 口径的逐年收益与门禁明细见
RESEARCH 十~十二与各 FREEZE 文档。下表为 2026-08-16 的历史快照，**数字已过时勿引用**：

## 二、过拟合对照（源码已删，仅存测量记录）

| 策略 | 定位 | 测量结果 | 结论 |
|---|---|---|---|
| `ETHShortV1_overfit` | 单品种过拟合反面教材 | ETH 全期 +47.3%（107 笔/胜率 86%/夏普 0.45），换品种 SOL +26%、BTC -23%、BNB -37%、DOGE -46% | 单标优秀 ≠ 策略有效；2026-08-28 删除源码，结论保留 |
| `DOGEDualV1` | 过拟合对照（双向暴跌多/暴涨空） | 仅 DOGE +185%；ETH +9%、SOL -7% | 参数按 DOGE 波动率定制，换品种失效；2026-08-28 删除源码 |

---

## 三、曾测量、已废弃的策略（源码已删，仅剩回测记录）

### 按信号族归纳（中位数口径，来自 644 次回测）

| 信号族 | 策略 | 测量结论 |
|---|---|---|
| 均值回归 | EmaDevV1-V6 | 全负：胜率 53-63%，利润 -29% ~ -57%，直接淘汰 |
| 趋势/配对 | TrendFollowV1、EthBtcPairV1 | -20% / -30%，淘汰 |
| 做空/双向 | ETHShortV4、ETHDualV1、SOLDualV1、SOLShortV1、DOGEDualV2 | 多数亏损（-22% ~ -67%）；个别正收益依赖单一品种，过拟合 |
| DOGE 专属 | DOGELongV1-V5（含 V4A/B/C） | 中位 +10% ~ +71%，但 edge 只对 DOGE 成立，换品种崩 |
| 短线趋势 | ShortTrendV1-V5 / F1 / AI | 胜率 86-93% 但利润≈0（-1% ~ +74% 中位），摩擦吃掉全部 edge；FreqAI 学不到模式 |
| 凌晨闪崩 | NightCrashV1/V2 | 统计优势存在但利润太薄（+3% / -13%），扛不住手续费 |
| 周末衍生 | WeekendCrashV1、BTCWeekendV1/V2 | +12% / +364% / +46%，但依赖单品种（BTC），普适性不足 |
| 暴跌抄底变体 | CrashBuy1H、CrashBuyV2 | 1h 版 -75%（周期错误）；4h 版 +255%（并入 CrashBuyV1 演进） |
| 波动率 | SOLVolV1 | +51%，单品种依赖，未进入正式池 |
| 合成期权 | SynthPutV1 系列（put/call 镜像、择时、杠杆、跨周期） | **穷尽证伪**：所有信号全期利润均为 2021 疯牛贡献，样本外 2022-2026 全负；真期权同价更优；唯一未测信息源为 OI/多空比（历史数据不可得） |

---

## 四、测量库概况

- 回测总数：644 次（zip + meta 成对保存）
  - 2026-08-04：361 次（EmaDev / ShortTrend / DOGE / ETHShort 等早期扫描）
  - 2026-08-05：136 次（NightCrash / Weekend 系列 / CrashBuy）
  - 2026-08-13：147 次（WeekendReverseV1 集中验证 + 2 份 HTML 报告）
- 策略数量：39 个曾在回测中出现（源码现存 4 个正式 + sample）
- 汇总表：`user_data/backtest_results/_all_runs_summary.csv`

---

## 五、当前状态

| 项目 | 状态 |
|---|---|
| WeekendReverseV1 | 已冻结，paper forward-test 进行中（`user_data/paper/FREEZE.md` 记录判据：胜率 ≥70%、回撤 ≤30%、≥20 笔、利润 >0） |
| WeekendReverseV2 | 已冻结（2026-08-16） |
| 数据刷新复跑（2026-08-28） | 数据更新至 08-28：V2 全期 553 笔 / +$525,857（PF 2.10），冻结后新窗口 16 笔 +$150,527 为正；冻结基线差异系暖机口径（详见 `paper/FREEZE_V2.md` 第八节） |
| BigMoveV1 / CrashBuyV1 | 已定版，未实盘 |
| 研究文档 | `RESEARCH.md`（结论+失败记录）、`STRATEGY_WORKFLOW.md`（研发流程） |
