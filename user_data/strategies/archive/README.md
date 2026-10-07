# 已归档策略（2026-10-07 项目收缩）

freqtrade 默认不递归扫描子目录，本目录策略不会被加载。复用时复制回上一级并重新走
STRATEGY_WORKFLOW 全流程（VAL 窥视台账按策略名记录，改名不豁免）。

| 策略 | 归档原因 | 结论出处 |
|---|---|---|
| WeekendReverseV1 | V2 全覆盖，退役（用户 2026-10-07 批准）；paper 配置/冻结文档在 `paper/archive/` | RESEARCH 前段、paper/archive/FREEZE_V1.md |
| CrashBuyV1 | 2026 年 −24.9%，退出实盘候选 | STATUS_20260829（已归档）、RESEARCH |
| CrashBuyV2 | 观察名单，未过门禁，不再投入 | RESEARCH 24–25 |
| CrashPortfolioV1 | VAL 失败关闭 | RESEARCH 25 |
| FundingSqueezeV1 | 被 V1L（live 安全变体）替代 | RESEARCH 十三~十五 |
| OIFlushV1 | 被 V2（30 天动量过滤）替代 | RESEARCH 十二 |
| WeekendReverseV2Lab | V2 实验副本 | RESEARCH |
| sample_strategy | freqtrade 示例 | — |

仍在用的 4 个（上一级目录）：WeekendReverseV2（引擎，paper 中）、OIFlushV2（唯一过 Tier B 的臂，
12 月初 paper）、FundingSqueezeV1L / BigMoveV1（paper 跑完各自 FREEZE 评审，不再研究；Tier B 未过）。
