"""每日运维总览（2026-11-08）——一条命令看两臂健康度。

把三处已成熟的独立检查收敛成一个只读入口，服务上线/daily 巡检：
  1) 数据新鲜度与接缝（复用 data_check.collect_pairs + check_metrics 的判定）
  2) 当前高离散度分位（复用 disp_monitor.dispersion_pct，RESEARCH 31/37）
  3) 两臂冻结状态摘要（V2 源码 SHA 是否仍等于冻结值；报告是否齐全）

只读，不写任何数据、不改源码、不碰 VAL。退出码：0=全部健康，1=有告警
（数据缺口 / V2 源码 SHA 漂移 / 关键报告缺失）。

用法:
  .venv/Scripts/python.exe user_data/scripts/daily_ops.py
  .venv/Scripts/python.exe user_data/scripts/daily_ops.py --pool top10 --days 14
"""
import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_lib as R  # noqa: E402

ROOT = Path(R.__file__).resolve().parent.parent
DATA = ROOT / "data" / "binance" / "futures"
UNIVERSE = ROOT / "universe"
STRAT_DIR = ROOT / "strategies"
REPORTS = ROOT / "backtest_results"

# 冻结指纹（paper/FREEZE_V2.md 记录值；源码 SHA 不可变）
FROZEN_V2_SHA256 = (
    "1b90c90bb7873500a30e8f821713935f497a018d28736596fdaa6f97743b46d1"
)


def _load_pool(name):
    f = UNIVERSE / f"pairs_{name}.txt"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.append(line)
    return out


def _kline_fname(pair, tf="4h"):
    return DATA / (pair.replace("/", "_").replace(":", "_") + f"-{tf}-futures.feather")


def check_freshness(pairs):
    """返回 (problems, latest_ts)。problems 为 (pair, msg) 列表。"""
    import pandas as pd

    problems, latest = [], {}
    for p in pairs:
        f = _kline_fname(p, "4h")
        if not f.exists():
            problems.append((p, "K线文件缺失"))
            continue
        t = pd.to_datetime(pd.read_feather(f)["date"], utc=True).sort_values()
        if t.empty:
            problems.append((p, "K线为空"))
            continue
        latest[p] = t.max()
        gaps = int((t.diff().dropna() > pd.Timedelta(hours=4) * 1.5).sum())
        dups = int(t.duplicated().sum())
        if gaps or dups:
            problems.append((p, f"{gaps} 处缺口 / {dups} 处重复"))
    newest = max(latest.values()) if latest else None
    return problems, newest


def check_v2_sha():
    p = STRAT_DIR / "WeekendReverseV2.py"
    if not p.exists():
        return None, "源码文件缺失"
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    if h == FROZEN_V2_SHA256:
        return h, None
    return h, f"SHA 漂移（冻结 {FROZEN_V2_SHA256[:16]}…）"


def current_dispersion():
    import disp_monitor as D

    disp, pct = D.dispersion_pct()
    import pandas as pd

    df = pd.DataFrame({"disp": disp, "pct": pct}).dropna()
    if df.empty:
        return None
    last = df.index.max()
    row = df.loc[last]
    return {
        "bar": str(last)[:16],
        "disp": float(row["disp"]),
        "pct": float(row["pct"]),
        "high": bool(row["pct"] >= D.HI_Q),
    }


def main():
    R.enable_utf8_console()
    ap = argparse.ArgumentParser(description="每日运维总览（只读，两臂健康度）")
    ap.add_argument("--pool", default="top10", help="用于数据新鲜度检查的池（默认 top10）")
    ap.add_argument("--days", type=int, default=14, help="离散度回看的宽限天数（默认 14，仅提示用）")
    args = ap.parse_args()

    alerts = 0
    print("=" * 60)
    print("每日运维总览  daily_ops")
    print("=" * 60)

    # 1) 数据新鲜度
    pairs = _load_pool(args.pool)
    print(f"\n[1] 数据新鲜度（池 {args.pool}，{len(pairs)} 品种，4h）")
    if not pairs:
        print(f"    [!] 池文件缺失: pairs_{args.pool}.txt")
        alerts += 1
    else:
        probs, newest = check_freshness(pairs)
        print(f"    最新 4h bar: {str(newest)[:16] if newest is not None else 'N/A'} (UTC)")
        if probs:
            alerts += 1
            for p, msg in probs:
                print(f"    [!] {p:<22} {msg}")
        else:
            print("    [ok] 无缺口、无重复")

    # 2) 高离散度分位
    print(f"\n[2] 高离散度旁路指标（RESEARCH 31/37）")
    try:
        d = current_dispersion()
        if d is None:
            print("    [!] 无可用离散度序列，检查本地 K 线")
            alerts += 1
        else:
            flag = "★高离散" if d["high"] else "非高离散"
            print(
                f"    最新 bar {d['bar']} UTC | disp={d['disp'] * 100:.2f}% "
                f"pct={d['pct']:.3f} → {flag}"
            )
            print("    解读：仅作环境参考，不单独决定仓位（37 节 OOS 优势已衰减大半）")
    except Exception as e:  # 缺 K 线等情况不致命
        print(f"    [!] 计算离散度失败: {e}")
        alerts += 1

    # 3) V2 冻结 SHA
    print("\n[3] V2 源码冻结状态")
    h, err = check_v2_sha()
    if err:
        alerts += 1
        print(f"    [!] {err}")
    else:
        print(f"    [ok] SHA 与冻结值一致 ({h[:16]}…)")

    print("\n" + "=" * 60)
    if alerts:
        print(f"结论：{alerts} 项告警，请排查")
        sys.exit(1)
    print("结论：全部健康")
    sys.exit(0)


if __name__ == "__main__":
    main()
