# 策略研究文档

> 2026-08-05 | 永续合约 | Freqtrade 2026.8-dev

---

## 核心结论

**WeekendReverseV1 是当前最强普适策略。** 统一参数跑币安 Top10 USDT 永续，9/10 盈利，样本外验证通过。CrashBuyV1 作为互补策略。

---

## 一、WeekendReverseV1 — 周末低流动性反转 ★

### 参数

```
4h K线 | 做多 | 十品种统一参数
窗口：周六日 + 周一美股盘前（北京时间21:00前）
入场：单根跌 >2% + 阳线
止损：-10% | 尾随：1.2%激活/0.3%步长 | 止盈：8% | 离场：EMA20
```

### 币安 Top10 USDT 永续表现

| 品种 | 笔数 | 胜率 | 利润 |
|------|------|------|------|
| BANK | 65 | 87.7% | +$42,608 |
| CYS | 22 | 95.5% | +$14,656 |
| ETH | 65 | 83.1% | +$5,602 |
| XRP | 59 | 86.4% | +$5,045 |
| SOL | 112 | 91.1% | +$4,195 |
| ZEC | 114 | 86.0% | +$2,712 |
| BNB | 24 | 83.3% | +$1,671 |
| BTC | 47 | 87.2% | +$1,566 |
| HOME | 39 | 79.5% | +$101 |
| HYPE | 17 | 76.5% | -$4,053 |
| **合计** | **564** | **86.5%** | **+$74,102** |

CAGR 162% | 夏普 0.81 | 利润因子 1.79 | 9/10 盈利

### 样本外验证

参数用 2022-2024 数据调优，在 2025-2026 上测试：10/10 盈利，夏普 1.23。不同尾随参数（0.2%-0.5%）在测试集上表现一致，策略驱动力来自市场结构而非参数精调。

### 为什么有效

周末 + 周一盘前是加密货币流动性最低的连续窗口。下跌多为缺乏承接的被动抛售，一旦买方回归即反弹。BTC/ETH/SOL/XRP/BNB/ZEC/BANK/CYS/HOME 全部遵循此规律，HYPE 例外可能因上线时间太短（2025年5月）。

---

## 二、CrashBuyV1 — 全时段暴跌抄底

### 参数

```
4h K线 | 做多 | 五品种统一参数
入场：16h跌>9% + 阳线实体>0.5%（跌9-12%半仓，>12%满仓）
止损：-12% | 尾随：5%激活/2%步长 | 止盈：25% | 离场：EMA20
```

### 表现

| 品种 | 笔数 | 胜率 | 利润 |
|------|------|------|------|
| DOGE | 24 | 83.3% | +$1,138 |
| ETH | 16 | 81.2% | +$524 |
| BNB | 8 | 75.0% | +$340 |
| BTC | 8 | 87.5% | +$278 |
| SOL | 32 | 78.1% | +$267 |
| **合计** | **88** | **80.7%** | **+$2,548** |

CAGR 32.7% | 夏普 1.30 | 利润因子 1.86

> 在 Top10 新币上 7/10 盈利（BANK/CYS 无效），整体不如 WeekendReverseV1。

---

## 三、两策略对比

| | WeekendReverseV1 | CrashBuyV1 |
|---|-----------------|-----------|
| 笔数 | 564 | 88 |
| 利润 | +$74,102 | +$2,548 |
| 胜率 | 86.5% | 80.7% |
| 夏普 | 0.81 | 1.30 |
| 利润因子 | 1.79 | 1.86 |
| Top10通过率 | 9/10 | 7/10 |
| 信号频率 | 高频 | 低频 |
| 互补性 | 周末窗口 | 全时段大周期 |

---

## 四、失败记录

- **单品种策略（ETHShortV1_overfit/DOGEDualV1）**：单标的优秀，换品种全崩 → 过拟合。ETHShortV1_overfit 保留作为对照案例
- **凌晨闪崩策略（NightCrash）**：统计优势存在但利润太薄，无法对抗交易摩擦
- **FreqAI 机器学习**：训练数据不足，学不到有效模式
- **做空暴涨**：加密市场涨了继续涨，做空暴涨没有普适性

---

## 五、运行命令

```bash
# WeekendReverseV1（十品种）
freqtrade backtesting --config user_data/config_perpetual.json \
  --strategy WeekendReverseV1 --timerange 20220101- \
  --pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT BNB/USDT:USDT \
         ZEC/USDT:USDT HOME/USDT:USDT BANK/USDT:USDT CYS/USDT:USDT HYPE/USDT:USDT

# CrashBuyV1（五品种）
freqtrade backtesting --config user_data/config_perpetual.json \
  --strategy CrashBuyV1 --timerange 20220101- \
  --pairs BTC/USDT:USDT ETH/USDT:USDT BNB/USDT:USDT SOL/USDT:USDT DOGE/USDT:USDT
```
