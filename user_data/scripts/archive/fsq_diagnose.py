"""FundingSqueezeV1 满池 VAL 宽度失败归因：逐对品质迁移 + funding 体制量化。

背景（2026-08-29 满池重验）：四腿利润全正（CORE×VAL +$13,972/PF1.37），但品种宽度
70-74% < 80% 门禁。问题：输家是谁？funding 体制在 TEST→VAL 间变了什么？

数据源：validate_strategy 满池日志逐对表 + funding feathers（独立口径 $1,000/笔）。
用法: .venv/Scripts/python.exe user_data/scripts/fsq_diagnose.py
"""
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "user_data" / "reports" / "logs" / "validate_fsq_fullpool_20260829.log"
D = ROOT / "user_data" / "data" / "binance" / "futures"


def parse_tables(text):
    """解析日志里 4 腿的逐对表 → {腿: {sym: (total$, n, win%)}}。"""
    legs = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"=== (\w+) × (TEST|VAL)", line)
        if m:
            cur = f"{m.group(1)}×{m.group(2)}"
            legs[cur] = {}
            continue
        if cur is None:
            continue
        toks = line.split()
        if toks and toks[-1].startswith("←"):
            toks = toks[:-1]
        if len(toks) >= 6 and toks[0] not in ("pair", "TOTAL", "---") and not toks[0].startswith("-"):
            try:
                # 行结构: sym y1..yk total n win%
                nums = [t.replace(",", "") for t in toks[1:]]
                total, n, wr = float(nums[-3]), int(nums[-2]), float(nums[-1])
                legs[cur][toks[0]] = (total, n, wr)
            except (ValueError, IndexError):
                pass
    return legs


def funding_regime():
    """按年聚合全池 funding：中位数(每8h, bp)、负结算占比、品种数。"""
    per_year = defaultdict(list)
    for f in sorted(Path(D).glob("*-1h-funding_rate.feather")):
        sym = f.name.split("_")[0]
        df = pd.read_feather(f)
        for y, g in df.groupby(df["date"].dt.year):
            per_year[y].extend(g["open"].tolist())
            n_syms[(y, sym)] = len(g)
    rows = []
    for y in sorted(per_year):
        r = pd.Series(per_year[y]) * 1e4  # bp / 8h
        rows.append((y, len(r), r.median(), (r <= 0).mean() * 100, r.quantile(0.02)))
    return pd.DataFrame(rows, columns=["年", "结算数", "中位bp/8h", "负结算占比%", "p2分位bp"])


n_syms = {}


def main():
    legs = parse_tables(LOG.read_text(encoding="utf-8"))
    print("=== 满池四腿 ===")
    for k, v in legs.items():
        tot = sum(x[0] for x in v.values())
        n = sum(x[1] for x in v.values())
        pos = sum(1 for x in v.values() if x[0] > 0)
        print(f"{k:<14} {n:>5}笔 ${tot:>+9,.0f} 盈利品种 {pos}/{len(v)}")

    # 逐对：TEST vs VAL 并排（core 池）
    ct, cv = legs.get("CORE×TEST", {}), legs.get("CORE×VAL", {})
    rows = []
    for sym in sorted(set(ct) | set(cv)):
        t = ct.get(sym)
        v = cv.get(sym)
        rows.append((sym,
                     t[0] if t else None, t[1] if t else 0,
                     v[0] if v else None, v[1] if v else 0))
    df = pd.DataFrame(rows, columns=["sym", "TEST$", "TESTn", "VAL$", "VALn"])
    df["质量迁移(VAL$/笔 - TEST$/笔)"] = (df["VAL$"] / df["VALn"].clip(lower=1)).round(1) - \
                                        (df["TEST$"] / df["TESTn"].clip(lower=1)).round(1)
    df = df.sort_values("VAL$", na_position="last")
    print("\n=== CORE 逐对 TEST→VAL（按 VAL 利润升序，末 15 = 最差）===")
    print(df.head(15).to_string(index=False))
    flip = df[(df["TEST$"].fillna(0) > 0) & (df["VAL$"].fillna(0) < 0)]
    print(f"\nTEST 盈利→VAL 转亏: {len(flip)} 个: {', '.join(flip['sym'])}")
    born_val = df[(df["TESTn"] == 0) & (df["VALn"] > 0)]
    print(f"仅 VAL 有数据（2024-08 后上市）: {len(born_val)} 个, 其中盈利 "
          f"{(born_val['VAL$'] > 0).sum()}/{len(born_val)}, 合计 ${born_val['VAL$'].sum():+,.0f}")

    print("\n=== funding 体制（全池 61 品种）===")
    print(funding_regime().to_string(index=False,
          formatters={"中位bp/8h": "{:+.2f}".format, "p2分位bp": "{:+.2f}".format,
                      "负结算占比%": "{:.0f}".format}))


if __name__ == "__main__":
    main()
