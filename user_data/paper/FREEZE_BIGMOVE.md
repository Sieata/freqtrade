# BigMoveV1 — 冻结与 forward-test 文档（Tier B 事件臂）

> 状态：paper forward-test 候选（Tier B 组合臂定位，门禁分层提案 GATE_TIERING_PROPOSAL.md）。
> 关联：RESEARCH 10.3（标准验证）、12.6（Tier B 评估）。

## 一、冻结声明

- 冻结对象：`user_data/strategies/BigMoveV1.py`
- 冻结 SHA256：`8d337d365356c87db9e7e0f831cbc479eb444ef1a11958a3cf16aecdbf917380`
- 冻结日期：2026-08-29
- 配置：`user_data/config_paper_bigmove.json`（TOP10 静态池，max_open_trades 3，$1,000/笔，
  独立 db `tradesv3.dryrun.bigmove.sqlite`，UI 端口 8082——可与 V2/FS paper 同机并行）。

## 二、冻结参数

3 根动量 >12% + 收盘>SMA200(4h) + BTC>SMA200(4h) 入场；收盘<SMA200 离场；stoploss -18%；
无尾随无 ROI；timeframe 4h。低频肥尾（TEST+VAL 合计 67 笔/4.6 年）。

## 三、Tier B 评估快照（2026-08-29，tier_b_eval.py，TOP10）

| 门禁 | TEST | VAL |
|---|---|---|
| 4 重叠（vs V2） | 0% ✅ | 0% ✅ |
| 5 组合增量 | +5.4pp ✅ | **+15.6pp** ✅ |
| 6 最差月归一 | 0.50x ✅ | 0.50x ✅ |
| 7 VAL 负年 | — | 0 个 ✅ |

臂年化（TOP10 钱包口径）：TEST +5.8% / **VAL +17.0%**——三臂中 VAL 增量最高、零负年。
组合年化参考：V2 单独 +16.6% → 加 BigMove +32.2%。

## 四、forward-test 判据（预注册，启动前写定）

| 指标 | 绿灯 | 说明 |
|---|---|---|
| 评审周期 | 6 个月（3 个月中途检查） | 低频：预期 6~10 笔 |
| 样本量 | ≥ 6 笔平仓 | 低频策略，按笔不按月 |
| 总利润 | > $0 | — |
| PF | ≥ 1.2 | 回测 2.33–3.21，容忍衰减 |
| 钱包最大回撤 | ≤ 5% | 回测 2.2–2.6% |
| Tier B 联动 | 每月与 V2 组合增量追踪 | 增量转负连续 2 个月 = 预警 |

- 中止条件：累计亏损 > 8%（回测同口径 dd 2.2–2.6% 的 ~3 倍）→ 停止复盘。
- 定位：**组合臂**——不单独承担收益，价值 = 与 V2 的低相关（-0.01）+ 牛市肥尾。
- 已知集中度风险：DOGE(2024)/ZEC(2025) 单年贡献 68–69%——paper 期逐季检查贡献分布。

## 五、启动方式

paper 设备：`git pull` → `./user_data/scripts/paper_start_bigmove.sh`
（与 V2/FS paper 三实例并行，端口 8080/8081/8082，db 各自独立。）
