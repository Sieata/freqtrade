"""paper 实际仓位配置（1 槽 + unlimited stake = 99% 满仓复利）在 paper8 池的摩擦阶梯。

不同于 paper8_fee_ladder.py 的独立口径（8 槽固定 $1000/笔），本脚本复刻 forward-test
真正跑的那套仓位（config_paper_v2.json: max_open_trades=1, stake_amount=unlimited,
tradable_balance_ratio=0.99），在 VAL 段 across 费率阶梯，看复利路径下的 CAGR / 钱包回撤。

口径：sizing_sweep.make_ratio_config(0.99) 生成 99% 复利配置；run_backtest 单槽、不传
--stake-amount（= unlimited）；钱包 $10,000（满仓复利基准，与 sizing_sweep 默认一致）。
仅分析 paper 池特征，不改 paper 真配置；摩擦 --fee 不记 VAL 台账。
"""
import sys
import pathlib
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import sizing_sweep as S

STRATEGY = "WeekendReverseV2"
POOL = "paper8"
TR = "20240828-20261108"  # VAL
FEES = [0.0005, 0.0010, 0.0020, 0.0030, 0.0040, 0.0050]
WALLET = 10000.0


def main():
    pairs = S.pairs_from_pool(POOL)
    logdir = pathlib.Path(tempfile.mkdtemp(prefix="paper8_compound_"))
    cfg = S.make_ratio_config(0.99, logdir)
    print(f"paper8 池 ({len(pairs)} 品种) 1槽 unlimited(99%复利) VAL 费率阶梯 钱包 ${WALLET:,.0f}")
    print(f"{'往返':>6} {'笔数':>5} {'绝对利润$':>11} {'CAGR%':>7} {'最大回撤%':>9} {'PF':>6} {'Sharpe':>7} {'p值':>12}")
    base = None
    for fee in FEES:
        tag = f"fee{int(fee*10000)}"
        log = S.run_backtest(STRATEGY, TR, pairs, WALLET,
                             ["--max-open-trades", "1", "--fee", str(fee)],
                             cfg, logdir, tag)
        d = S.parse_summary(log)
        if d is None:
            print(f"{fee*200:>5.2f}%  FAILED (见 {log})")
            continue
        prof = S.num(d["绝对利润"])
        if base is None:
            base = prof
        print(f"{fee*200:>5.2f}% {d['笔数'].split('/')[0].strip():>5} {prof:>11,.0f} "
              f"{S.num(d['CAGR%']):>7.1f} {S.num(d['最大回撤%']):>8.2f}% "
              f"{S.num(d['PF']):>6.2f} {S.num(d['Sharpe']):>7.2f} {d['p值']:>12}")
    print(f"\n日志目录: {logdir}")


if __name__ == "__main__":
    main()
