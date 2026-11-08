# OIFlushV2 — 冻结与 forward-test 文档

> 2026-11-08 | 依据 RESEARCH 第十二节、26.14；TOP10 第一口径，VAL 一次性

## 一、冻结声明

| 项 | 值 |
|---|---|
| 冻结日期 | 2026-11-08 |
| 策略文件 | `user_data/strategies/OIFlushV2.py` |
| 冻结快照 | `user_data/paper/frozen_OIFlushV2.py`（不可变备份） |
| 策略 SHA256 | `d714e0922be084932f2fe477a52a755b0507f9ebb01699830d2e45d154dc5a98` |
| 配置 | `user_data/config_paper_oi.json`（端口 8082 / db `tradesv3.dryrun.oi.sqlite` / 8 并发 × $1,000 / 钱包 $12,000） |
| 启动脚本 | `user_data/scripts/paper_start_oi.sh`（内置 SHA 校验，不匹配拒绝启动） |
| 交易模式 | paper trading（dry_run=true） |

**规则：forward-test 期间若 `OIFlushV2.py` 被任何方式改动（SHA256 变化），本次 forward-test 作废。**

## 二、冻结参数

```
4h K线 | 做多 | TOP10 品种池（BTC ETH BNB XRP SOL TRX HYPE ZEC DOGE XMR）
入场：OI 6根(24h)变化 <= 滚动180d分位 oi_quantile(0.05)
      AND 4h 跌幅 <= -5%(crash_threshold)
      AND 30d 动量 > 0（趋势过滤，close > close[180根4h]）
出场：hold_48h（时间止盈，无信号出场）
风控：-10% 硬止损 | max_open_trades=8 | 固定 $1,000/笔
```

## 三、机制与定位

- **全市场去杠杆共振条件押注**：中期上升趋势中的"急跌 + OI 急缩"= 回调买点；下跌趋势中 = 下跌中继（V1 死因，已被 30d 动量过滤修复）。
- **低频、单年集中**（RESEARCH 26.14）：2024 年 92% 利润来自 3 个"多币同日 OI 齐缩"事件，五年仅出现 3 次，其余年份贡献≈0，2026 转负。
- **参数存疑**（26.12/26.14.1）：`oi_quantile` 0.05→0.15 TEST 多赚 66%、TOP5 蓝筹同向复现，但 OOS 样本不足以下结论。
- **paper 的使命**：正是补样本到统计显著所需（n=62 → 更多 OOS）。判据意义在方向与样本积累，不在首月数字。

## 四、forward-test 判据

| 指标 | 绿灯 |
|---|---|
| 样本量 | ≥ 20 笔平仓（事件臂低频，给足 6 个月） |
| 胜率 | ≥ 45%（VAL 62%，留足波动余量） |
| 最大回撤 | ≤ 10%（VAL 2.4%） |
| 总利润 | > $0 |

**只记录，不改参。至少 3 个月 / 20+ 笔后再下第一次判断。**

## 五、回测基线（TOP10，独立 $1,000/笔口径）

| 段 | 笔数 | 利润 | 胜率 | PF | 钱包回撤 |
|---|---|---|---|---|---|
| TEST 20220101-20240828 | 44 | +$2,497 | 64% | 3.84 | 1.81% |
| VAL 20240828-20261019 | 52 | +$1,293 | 62% | 1.84 | 2.38% |

VAL 独立显著 p=0.096（不显著，见 RESEARCH 26.x 宽度检验），BNB 单品种占比高。这是 paper 需要补样本的核心原因。

## 六、运维要点

- **OI/市值 feather 必须新鲜**：策略 `populate_indicators` 直读 `user_data/data/binance/futures_metrics/<pair>-4h-metrics.feather`，无 OI 数据的品种自动零信号（不进池）。
- **OI 历史靠累积器**：`oi_accumulate.py` 每日跑（openInterestHist 仅 30 天，漏跑即永久缺口，无法回补）。启动前务必 `./refresh_all.sh`。
- 每周 `paper_status.py --strategy OIFlushV2` 记录：笔数、胜率、回撤、累计利润。
