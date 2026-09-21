---
name: freqtrade-arb-research
description: 在本 freqtrade 仓库做「套利/市场中性」研究的固定套路：跨品种费率分散、期现资金费、交割基差、跨所费差。含口径纪律、必做的防伪检验、已知结论与坑。当用户要求挖套利机会、做市场中性策略、评估资金费/基差收益、或复核 H 家族（H7/H8/H8c/H10）时使用。
agent_created: true
---

# 套利/市场中性研究套路（freqtrade 仓库）

## 先读，别重复劳动

**动手前必读 `RESEARCH.md` 第十三节（H7/H8/H8c/H10）与第二十一节。**
期现资金费（H7）、交割基差（H8/H8c）、跨所费差（H10）都已做过多轮；直接重跑等于浪费。
新工作应定位为「规则化复核 / 新族 / 反驳旧结论」，并在报告里明说与旧结论的关系。

## 四个研究台（都是独立模拟器，不走 freqtrade 引擎）

| 脚本 | 覆盖 |
|---|---|
| `cross_arb_research.py` | 跨品种：`carry` 横截面费率分散、`pairs` 协整价差回归 |
| `basis_arb_research.py` | 跨工具：`spotperp` 多现货+空永续、`calendar` 多现货+空季度 |
| `funding_spread_scan.py` | 跨所：币安 × Hyperliquid 费差（快照 + 14 天持续性深查） |
| `cm_perp_delivery_research.py` | 币本位（COIN-M）ETHUSD 永续 × 交割：含 funding 覆盖率体检 |
| `arm_stats.py` | 单臂统计质量（逐笔 t 检验、自举 CI、集中度） |

```bash
.venv/Scripts/python.exe user_data/scripts/cross_arb_research.py --mode both --pool top10
.venv/Scripts/python.exe user_data/scripts/basis_arb_research.py --mode both \
    --rule funding_pos --enter-bp 5 --exit-bp -2
.venv/Scripts/python.exe user_data/scripts/basis_arb_research.py --mode calendar --thresh 0 --horizon 30
.venv/Scripts/python.exe user_data/scripts/cm_perp_delivery_research.py          # 币本位永续×交割
```

## 数据底子（本机已有，不必重下）

- `user_data/data/binance/futures/`：79 品种 4h K 线 + 8h 结算 `funding_rate`（2021-01 起）
- `user_data/data/binance/*-4h.feather`：**现货**，8 个币（BTC/ETH/BNB/XRP/SOL/DOGE/TRX/ZEC）
- `user_data/data/binance/quarterly/`：交割合约，BTCUSDT 20 / ETHUSDT 24 / ETHUSD 14 个到期
  （ETHUSD 只到 240628 是**缓存截断**，交易所 240927 之后一直有合约，别当成停发）
- `user_data/data/binance/cm/`：币本位（COIN-M）永续 + funding + 23 个交割合约（210326~261225）
- **funding 一律走 dapi 全历史**（`/dapi/v1/fundingRate`，2020-08 起）；
  vision 的 `cm` 桶 fundingRate **只到 2022-07**，缺口会静默漏算成本（踩过）
- 现货数据比期货旧（最近一次 2026-08-29），跑新时段先确认覆盖，别静默截断

## 口径纪律（照抄，别自创）

- t-1 决策，无前视；费率按**结算事件**逐 bar 计入，不按 bar 平均摊
- 双边 taker：现货 0.10% / 永续 0.05%；建仓+平仓各一次（buy&hold 不滚动）
- 滚动 β / 滚动相关一律 `.shift(1)`，窗口只看过去
- 报两个分母：**每 1x 名义**（gross notional）与**每占用资本**（现货全额 + 永续保证金 1/lev）
- 调参只用 TEST `20220101-20240828`；VAL `20240828-` 只跑一次定版候选
- 逐年汇总别忘乘 100（踩过：打印全是 +0.0%，实际 2.5%）

## 必做的防伪检验（不做就别下结论）

1. **费率错位**：`--fund-shift 1` 把结算整体后移一个 bar。收益若只在 shift=0 成立，说明靠结算瞬间对齐，假的。
2. **池宽符号检验**：同一策略在 top5/top10/core 上跑，看**分项**（费率腿 vs 价格腿）。
   价格腿随池宽翻转符号（+63%/+30%/−51%）= 小池幸存者偏差，不是因子。
3. **事后选择隔离**：配对/单品种的"最优"必须踢掉；只看全等权组合或规则化版本。
   （实测：36 对里最好的 DOGE-XMR +54.7%，等权组合年化 +0.8% → 前者是选择偏差）
4. **阈值扫描**：θ 从宽到严跑一遍。若等阈值达标反而更差（持有期被压缩、固定手续费占比升高），
   说明该阈值是伪优化——交割基差就是这个情况（θ=0/5/10% → 胜率 82/78/65%）。
5. **朴素基准**：先跑"一直持有/完全不择时"。期现套利一直持有会亏（BNB −10%/SOL −11%/TRX −7%），
   熊市费率转负必须离场。
6. **funding 符号核对（血泪，必做）**：正费率 = **多头支付空头**。任何"多永续"的收益式里，
   funding 必须是**负项**（`−Σrate`），且 U 本位线性合约名义固定、**不需要 `P_i/P0` 缩放**。
   单事件若出现"funding 贡献为正且量级接近基差"，几乎一定是符号反了。
   自检：把 U 本位 ETH 永续 TEST 区间 funding 累计跑出来（应为正、年化 ~+6.8%），
   若你的组合在"多永续"腿把它记成收入 → 错。
   另加 **funding 覆盖率体检**：结算条数 vs `区间小时/8`，低于 95% 必须点名（数据缺口会虚增收益）。

## 已确认的结论（别推翻，只可细化）

- 期现资金费（8 币等权 + 费率滞回）：TEST 名义年化 +5.4%、资本 +4.0%、Sharpe 3.65、回撤 −1.4%；
  VAL +2.7%/+2.0%/5.19/−0.2%。**收益衰减明确**（2026 VAL 仅 +0.4%）。
- 交割基差：每 30 天单次净 +0.5% 左右、胜率 82%（θ=0），年化均值 6.0%。受合约窗口限制，属事件型。
- 跨品种费率分散：**只有费率腿可信**（top5/top10/core = +25/+36/+56%，随池宽单调）；
  价格腿不采信。
- 协整价差回归（朴素 z-score）：证伪。
- 跨所费差：稳定组清一色「多HL+空BN」，费率机制底层差 ~5.5pp/年；HL 小币有 OI 与 429 限制。
- **币本位 ETHUSD 永续×交割**：结构在（到期收敛，θ=8% 7 事件 100% 胜），但钱没有——
  单事件 +1.23%（funding −3.73% 吃掉基差 +3.34%），串行年化 **+2.0%/年**；反向全负。
  2026 基差压缩到 ann>8% 占比 0%；在市合约扣费后净 −0.31% → 现在没机会。
- **⚠️ H8c（USDT-M 永续×交割）已勘误作废**：原报"8~13%/年"系 funding 符号错误；
  修正后 ETH **+0.3%/年**、BTC **+1.4%/年**。`h8c_paper.py` 不可启动。详见 RESEARCH 22.5。
- **结构性洞察**：contango 基差与正 funding 是同一件事的两种定价 → 「多永续+空交割」赚基差、
  付 funding，净额天然趋零。永续×交割家族 2024 后基本无利可图，别再投入。

## 坑

- 现货与永续要 `.dropna()` 对齐后再算收益；对齐后仍要检查行数（<100 行直接放弃该币）
- 交割合约文件按 `币_到期日` 命名，**ETHUSDT 与 ETHUSD 是两个产品**，别按 base coin 合并（会出重复行）
- Hyperliquid 深查历史会 429，无重试；跑全量时把 429 的币在报告里点名，别当"无数据"
- 报告类产物写在 `user_data/reports/`（该目录不入 git，属正常）
- vision 的 `cm` 桶 funding 只到 2022-07；dapi 才是全历史（见上）
- 币本位反向合约：USD 盈亏与线性合约**等价**（多头 PnL_usd = 名义×(P_exit/P_entry−1)），
  所以可直接与 U 本位同口径比较；但**币计价收益会有巨大摆动**（ETH 涨 45% 时币计 −29%），
  若面向持币者必须另报，别混为一谈
- **改同一个文件不要并发发多个 Edit**（本次 4 个 Edit 只落地 1 个）；用一次性 python
  替换脚本更稳（幂等 + `assert s.count(old) == 1`）
