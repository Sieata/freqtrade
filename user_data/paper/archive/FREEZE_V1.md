# WeekendReverseV1 — Forward-test 冻结文档

> 这是验证链条里**唯一不可作弊的样本外**：参数冻结后，用真实市场逐笔走势验证。
> 历史回测做得再干净，也回答不了"未来还行不行"——只有这一段能回答。

## 一、冻结声明

| 项 | 值 |
|---|---|
| 冻结日期 | 2026-08-13 |
| 策略文件 | `user_data/strategies/WeekendReverseV1.py` |
| 冻结快照 | `user_data/paper/frozen_WeekendReverseV1.py`（不可变备份） |
| 策略 SHA256 | `119407ff70a85a1f09dfb4aab830673bf3e7328d1b1b033e3379671a4062d707` |
| 交易模式 | paper trading（dry_run=true，模拟下单，不动真钱） |
| 起始资金 | $1,000（`dry_run_wallet`，与回测同口径） |

> 勘误（2026-08-28）：本表原记 SHA `77cb3784…7df4` 为 08-13 当天最终优化（尾随激活 1.2%→1.5%、去 EMA 离场）**之前**的旧版本指纹；实际冻结快照与现行策略文件一致，SHA 为 `119407ff…2d707`。forward-test 此前从未实际启动（本机无日志/无交易记录），不受影响。正式前向测试已改由 **WeekendReverseV2 单策略**承担（避免近双胞胎策略双开分裂样本），见 `RESEARCH.md` 第九节。

**规则：forward-test 期间，若 `WeekendReverseV1.py` 被任何方式改动（SHA256 变化），本次 forward-test 作废，必须重来。** 启动时核对日志开头的策略 SHA 与上表一致。

## 二、冻结参数

```
4h K线 | 做多 | 八品种（BTC ETH SOL XRP ZEC BANK CYS HYPE）
窗口：周六日 + 周一美股盘前（北京时间21:00前）
入场：单根跌 >2% + 阳线确认
风控：-10% 硬止损 | 尾随 1.5%激活 / 0.3%步长 | 8% 止盈 | 无 EMA 离场
仓位：stake_amount=unlimited, max_open_trades=1, tradable_balance_ratio=0.99（满仓复利）
```

## 三、回测基线（用于对比，不是"目标"）

| 指标 | 历史回测（2022-2026） |
|---|---|
| 全期利润 | $206,386（$1,000 复利到 $207,386） |
| CAGR | 229% |
| 胜率 | 91.4% |
| 最大回撤 | 20.5% |
| 交易笔数 | 478 |
| 盈利品种 | 8/8 |

逐年（每年 $1,000 独立起跑口径，非复利）：

| 年份 | 利润 | 胜率 | 回撤 |
|---|---|---|---|
| 2022 | $1,679 | 91.7% | 25.0% |
| 2023 | $2,136 | 95.4% | 10.0% |
| 2024 | $1,043 | 92.5% | 14.7% |
| 2025 | $3,304 | 90.3% | 15.1% |
| 2026 | $1,799 | 89.0% | 20.5% |

## 四、forward-test 判据（预先定义，红灯/绿灯）

> 这些阈值在开始前定死，避免事后找借口。小样本下波动大是正常的，但方向必须对。

| 指标 | 绿灯 | 说明 |
|---|---|---|
| 样本量 | ≥ 20 笔平仓 | 少于 20 笔统计无意义，只观察不判断 |
| 胜率 | ≥ 70% | 历史 91.4%，留足小样本余量 |
| 最大回撤 | ≤ 30% | 历史 20.5% 的 1.5 倍 |
| 总利润 | > $0 | 至少不亏 |

**判读原则**：三项全绿 → forward-test 通过，策略可信度显著上升；回撤或胜率破线 → 存在回测未捕捉的实盘摩擦（滑点/资金费率/流动性/周末缺口），需回炉；样本不足 → 继续跑，勿下结论。

## 五、启动步骤

1. **申请 Binance API key**（二选一）：
   - **主网**：binance.com → API 管理 → 创建 API → 勾选 `Enable Futures`，权限**只读**即可（dry-run 不下单）。⚠ 需网络可访问 binance。
   - **测试网**：testnet.binancefuture.com 申请，同时把 `config_paper.json` 里 `ccxt_config` 加 `"urls": {"api": "https://testnet.binancefuture.com"}`。
2. **填入 key**：编辑 `user_data/config_paper.json`，把 `YOUR_BINANCE_API_KEY` / `YOUR_BINANCE_API_SECRET` 换成真实值。
3. **启动**（项目根目录）：
   ```powershell
   .\user_data\scripts\paper_start.ps1
   ```
4. **核对**：日志 `user_data/logs/paper_forwardtest.log` 开头的策略 SHA 必须等于冻结指纹。

## 六、日常评估

```powershell
.venv\Scripts\python.exe user_data\scripts\paper_status.py
```

每周跑一次，记录：笔数、胜率、回撤、累计利润。**只记录，不改参数。** 建议至少跑 3 个月 / 20+ 笔平仓后再做第一次判断。

## 七、停止

```powershell
Stop-Process -Id <PID>   # PID 在启动时打印
```

## 八、收尾

forward-test 结束后，把实际结果（逐笔、逐品种、胜率/回撤/利润）与第三节基线对照，写回 `RESEARCH.md` 的 WeekendReverseV1 章节，形成"回测 → forward-test → 结论"闭环。若通过，可考虑小资金实盘；若失败，回炉研究实盘摩擦来源。
