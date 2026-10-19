"""研究框架公共层：路径 / 时间切分 / 币池 / 回测结果读取 / 验证报告定位。

动机（2026-10-07 框架审计）：validate_strategy / tier_b_eval / arm_stats / portfolio_* 各自复制了
一份"读池 + 解析报告 + 读 zip"，已出现三类哑失败——
  1. 调用方忘传 pool → 静默混池（tier_b_eval 修了函数签名，但 main/eval_arm 仍没传）；
  2. 相对路径依赖 CWD = 仓库根，换目录跑就 FileNotFound 或读到别处；
  3. Windows 默认编码 cp936：池文件中文注释读不出、报告 ✅ 写不进（本机 validate 直接崩）。
新脚本一律从这里取，不再各自实现。

验证结果的权威来源是 validate_strategy 写出的 JSON 旁车（reports/validate_*.json），
markdown 报告只给人看；旧报告没有旁车时回退正则解析 markdown。
"""
import json
import re
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
USER_DATA = ROOT / "user_data"
UNIVERSE = USER_DATA / "universe"
STRATEGIES = USER_DATA / "strategies"
BT_DIR = USER_DATA / "backtest_results"
REPORT_DIR = USER_DATA / "reports"
STAKE = 1000.0


def enable_utf8_console():
    """把 stdout/stderr 切到 UTF-8。

    Windows 控制台默认 cp936：门禁脚本打印 ✅/❌ 会直接 UnicodeEncodeError 崩掉
    （tier_b_eval / portfolio_4arm 实测），不是"显示乱码"而是中途异常退出、判定丢失。
    只影响本进程输出，不改变文件读写（文件本来就用显式 encoding="utf-8"）。
    """
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # 被重定向到不支持 reconfigure 的对象时忽略
            pass


# ---------------------------------------------------------------- 时间切分
def load_splits():
    return json.loads((UNIVERSE / "splits.json").read_text(encoding="utf-8"))


def split_ts():
    """TEST/VAL 分界（UTC 时间戳）。"""
    return pd.Timestamp(load_splits()["split_date"], tz="UTC")


# ---------------------------------------------------------------- 币池
def load_pool(name):
    """读币池文件 → [(pair, 注释dict)]，'#' 起注释，行内 'k=v' 解析进 dict。"""
    out = []
    for line in (UNIVERSE / f"pairs_{name}.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split("#", 1)
        pair = parts[0].strip()
        if not pair:
            continue
        meta = {}
        if len(parts) > 1:
            for tok in parts[1].split():
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    meta[k] = v
        out.append((pair, meta))
    return out


def pool_bases(name):
    """币池品种基名集合（BTC/ETH/...）。空池直接报错，防止"空集过滤掉全部交易"的哑失败。"""
    bases = {p.split("/")[0] for p, _ in load_pool(name)}
    if not bases:
        raise SystemExit(f"币池 {name} 为空：{UNIVERSE / f'pairs_{name}.txt'}")
    return bases


def pool_wallet(name):
    """钱包口径 = 池内品种数 × 1.2 × $1,000（与 validate 的 --dry-run-wallet 一致）。"""
    return len(load_pool(name)) * 1.2 * STAKE


# ---------------------------------------------------------------- 回测结果
def _resolve_zip(zip_path):
    zip_path = Path(zip_path)
    if not zip_path.is_absolute() and not zip_path.exists():
        zip_path = BT_DIR / zip_path.name
    return zip_path


def iter_results(zip_path):
    """逐个策略产出 (策略名, stats, trades, 顶层 meta)。

    一个 zip 通常只含一个策略；bt_summary 需遍历全部策略，其余脚本用 read_result 取首个。
    """
    p = _resolve_zip(zip_path)
    with zipfile.ZipFile(p) as z:
        for n in z.namelist():
            if not n.endswith(".json"):
                continue
            d = json.loads(z.read(n))
            if isinstance(d, dict) and "strategy" in d:
                for name, s in d["strategy"].items():
                    yield name, s, s.get("trades", []), d
                return
    raise SystemExit(f"{p.name}: 无策略结果")


def read_result(zip_path):
    """读回测 zip → (stats dict, trades list)（取 zip 内首个策略）。

    路径解析：绝对路径 / 相对当前目录存在的路径原样用；否则按裸文件名在 BT_DIR 下找。
    """
    for _, s, trades, _ in iter_results(zip_path):
        return s, trades
    raise SystemExit(f"{_resolve_zip(zip_path).name}: 无策略结果")


def result_span_years(stats):
    """回测实际覆盖年数（backtest_start→backtest_end），年化分母用它而非首末笔交易跨度。

    低频事件臂首笔可能在区间开头数月之后，按交易跨度年化会系统性虚高。
    """
    a = pd.Timestamp(stats["backtest_start"])
    b = pd.Timestamp(stats["backtest_end"])
    return max((b - a).total_seconds() / 86400 / 365.25, 1e-9)


def result_wallet(stats):
    """回测钱包（逐年收益率分母，STRATEGY_WORKFLOW 0.4 v2）。

    run_backtest 跑的独立口径回测 starting_balance = 池规模×1.2×$1,000；其他 zip 取其启动余额。
    """
    return float(stats.get("starting_balance") or stats.get("dry_run_wallet") or STAKE)


def trades_frame(trades, seg=None):
    df = pd.DataFrame(trades)
    if df.empty:
        return df
    df["open_dt"] = pd.to_datetime(df["open_date"], utc=True)
    df["close_dt"] = pd.to_datetime(df["close_date"], utc=True)
    df["profit$"] = df["profit_ratio"] * STAKE
    df["pair_base"] = df["pair"].str.split("/").str[0]
    if seg:
        df["seg"] = seg
    return df


# ---------------------------------------------------------------- 跑回测
def run_backtest(strategy, config, timerange, pairs, fee=None, max_open_trades=None, echo=True):
    """独立口径回测（每笔固定 $1,000，--cache none），返回 BT_DIR 下的结果 zip 路径。

    失败抛 RuntimeError（附 freqtrade 输出尾部）。每次运行导出到独立临时目录再移回 BT_DIR，
    并行跑多个回测不会互相"认领"对方的最新 zip（旧实现按 mtime 取最新，会串）。
    回测虽用本地数据，freqtrade 启动仍要 reload_markets（走 API），默认带代理（FT_PROXY 覆盖，none 直连）。
    """
    import os
    import shutil
    import subprocess
    import sys
    import uuid

    mot = max_open_trades or len(pairs)
    tmp = BT_DIR / f".run_{uuid.uuid4().hex[:8]}"
    tmp.mkdir(parents=True)
    cmd = [sys.executable, "-m", "freqtrade", "backtesting",
           "--config", str(config), "--strategy", strategy,
           "--timerange", timerange, "--pairs", *pairs,
           "--cache", "none", "--export", "trades", "--export-directory", str(tmp),
           "--max-open-trades", str(mot),
           # 启动余额 ≥ 每笔本金 × 最大并发仓 × 1.2，否则 "Starting balance smaller than stake_amount"
           "--dry-run-wallet", str(int(STAKE * mot * 1.2)),
           "--stake-amount", str(int(STAKE))]
    if fee is not None:
        cmd += ["--fee", str(fee)]
    env = os.environ.copy()
    proxy = os.environ.get("FT_PROXY", "http://127.0.0.1:7897")
    if proxy != "none":
        env.setdefault("https_proxy", proxy)
        env.setdefault("http_proxy", proxy)
    if echo:
        print(f"\n$ freqtrade backtesting --strategy {strategy} --timerange {timerange} "
              f"({len(pairs)} pairs{f', fee {fee}' if fee is not None else ''})", flush=True)
    try:
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, env=env,
                              encoding="utf-8", errors="replace")
        zips = sorted(tmp.glob("backtest-result-*.zip"))
        if proc.returncode != 0 or not zips:
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-25:])
            raise RuntimeError(f"freqtrade backtesting 失败（{strategy} {timerange}，exit {proc.returncode}）:\n{tail}")
        src = zips[-1]
        dst = BT_DIR / src.name
        if dst.exists():  # 同秒并发完成：加后缀避免覆盖
            dst = BT_DIR / f"{src.stem}_{tmp.name[5:]}{src.suffix}"
        shutil.move(str(src), dst)
        meta = src.with_suffix(".meta.json")
        if meta.exists():
            shutil.move(str(meta), dst.with_suffix(".meta.json"))
        return dst
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 逐笔统计
def one_sided_t_p(d):
    """单边 t 检验 p 值（H0: 每笔均值 ≤ 0）。假设交易独立——同事件多品种交易相关时偏乐观。"""
    n = len(d)
    if n < 3:
        return float("nan")
    sd = d.std(ddof=1)
    if sd == 0:
        return 0.0 if d.mean() > 0 else 1.0
    t = d.mean() / (sd / n ** 0.5)
    try:
        from scipy import stats
        return float(stats.t.sf(t, df=n - 1))
    except Exception:
        import math
        return float(0.5 * math.erfc(t / 2 ** 0.5))  # 正态近似（n 大时足够）


def boot_ci(d, iters=4000, seed=7):
    """每笔均值的自举 95% CI（逐笔独立重抽）。"""
    import random
    rnd = random.Random(seed)
    vals = list(d)
    n = len(vals)
    if n < 3:
        return (float("nan"), float("nan"))
    means = sorted(sum(rnd.choices(vals, k=n)) / n for _ in range(iters))
    return (means[int(0.025 * iters)], means[int(0.975 * iters)])


def cluster_boot_ci(df, iters=4000, seed=7):
    """按平仓月聚类自举每笔均值的 95% CI：同月交易整块重抽，保留月内相关性。

    df 需含 profit$ / close_dt。与 boot_ci 差距大 = 有效样本远少于笔数。
    """
    import random
    rnd = random.Random(seed)
    blocks = [(b["profit$"].sum(), len(b)) for _, b in df.groupby(df["close_dt"].dt.strftime("%Y-%m"))]
    k = len(blocks)
    if k < 3:
        return (float("nan"), float("nan"))
    means = []
    for _ in range(iters):
        pick = rnd.choices(blocks, k=k)
        means.append(sum(s for s, _ in pick) / max(sum(n for _, n in pick), 1))
    means.sort()
    return (means[int(0.025 * iters)], means[int(0.975 * iters)])


# ---------------------------------------------------------------- 月度序列
def monthly_series(df, lo, hi):
    """段内全日历月度 P&L（按平仓月；无交易月补 0）。

    不补 0 的后果：低频臂的 Sharpe 被空月虚抬、相关系数只在"双方都有交易的月"上算，
    portfolio_4arm 旧版 BM–OI 相关 TEST 0.68 / VAL 0.03 就是这么来的。
    """
    idx = pd.period_range(lo.tz_convert(None).to_period("M"), hi.tz_convert(None).to_period("M"), freq="M")
    if df.empty:
        return pd.Series(0.0, index=idx)
    m = df.groupby(df["close_dt"].dt.tz_convert(None).dt.to_period("M"))["profit$"].sum()
    return m.reindex(idx, fill_value=0.0)


def sharpe_m(m):
    """月度 P&L 的年化 Sharpe（无风险利率记 0；与资金规模无关，可跨臂比较）。"""
    sd = m.std(ddof=1)
    return float(m.mean() / sd * 12 ** 0.5) if sd > 0 else float("nan")


def max_dd(m):
    """月度累计 P&L 的最大回撤（$，负数）。"""
    eq = m.cumsum()
    return float(min((eq - eq.cummax()).min(), 0.0))


# ---------------------------------------------------------------- 逐笔汇总
def trade_pf(p, inf_if_no_loss=True):
    """利润因子 = 毛利 ÷ 毛亏。p 为逐笔 $ 序列。

    空序列返回 0.0（没有交易 ≠ 无亏损的完美策略）。
    无亏损笔时返回 inf（inf_if_no_loss=True）——报告里写作 ∞，与 freqtrade 一致。
    """
    if len(p) == 0:
        return 0.0
    gw = p[p > 0].sum()
    gl = -p[p < 0].sum()
    if gl > 0:
        return float(gw / gl)
    return float("inf") if inf_if_no_loss else 0.0


def trade_pf_from_trades(trades, stake=STAKE):
    """从逐笔交易算 PF，不依赖 freqtrade 的 stats["profit_factor"]。

    用 profit_abs（已实现净额，含手续费/funding）而非 profit_ratio × $1,000——
    后者假设每笔本金恰为 $1,000，而固定仓位回测里 stake 会在余额约束下浮动
    （实测 V2 VAL 最低 916.9），两者能差出 0.002 的 PF。profit_abs 与 freqtrade
    自己的 PF 逐位一致，且对复利 zip 仍然只是"该笔真实赚了多少"。

    trades 缺 profit_abs 时回退 profit_ratio × stake。
    """
    if not trades:
        return 0.0
    if all("profit_abs" in t for t in trades):
        p = pd.Series([float(t["profit_abs"]) for t in trades])
    else:
        p = pd.Series([float(t["profit_ratio"]) * stake for t in trades])
    return trade_pf(p)


def concentration(df, denom="profit"):
    """集中度三件套：(最大品种, 其利润占比, 其利润最好一年占该品种利润比)。

    df 需含 pair_base / close_dt / profit$。总利润 ≤ 0 时返回 None
    （占比无意义，调用方按"无利润"处理，不要拿负数分母硬算）。
    """
    tot = df["profit$"].sum()
    if df.empty or tot <= 0:
        return None
    by_pair = df.groupby("pair_base")["profit$"].sum()
    top = by_pair.idxmax()
    t = df[df["pair_base"] == top]
    yr = t.groupby(t["close_dt"].dt.year)["profit$"].sum()
    return top, float(by_pair[top] / tot), float((yr.max() / by_pair[top]) if by_pair[top] > 0 else 0.0)


def yearly_pnl(df, wallet=None):
    """按平仓年汇总利润：{year(str): $}；给 wallet 时另给 {year: %}，每年重置钱包口径。"""
    yr = df.groupby(df["close_dt"].dt.year)["profit$"].sum()
    if wallet is None:
        return {str(k): float(v) for k, v in yr.items()}
    return {str(k): (float(v), float(v / wallet)) for k, v in yr.items()}


# ---------------------------------------------------------------- 验证报告定位
def _legs_from_sidecar(path):
    d = json.loads(path.read_text(encoding="utf-8"))
    return {(r["pool"], r["split"]): r for r in d["runs"]}


def _legs_from_markdown(path):
    """旧报告（无 JSON 旁车）：按 `## POOL × SPLIT（tr）` + 「结果: `<zip>`」行解析。"""
    txt = path.read_text(encoding="utf-8")
    legs = {}
    for m in re.finditer(r"^## (\w+) × (TEST|VAL)（([^）]*)）", txt, re.M):
        body = txt[m.end():].split("\n## ", 1)[0]
        z = re.search(r"结果: `(backtest-result-[0-9_-]+\.zip)`", body)
        if z:
            legs[(m.group(1).lower(), m.group(2))] = {
                "pool": m.group(1).lower(), "split": m.group(2),
                "timerange": m.group(3), "zip": z.group(1)}
    return legs


def find_validation(strategy, pool):
    """返回该策略在指定池上最新一份**同时含 TEST 与 VAL 两腿**的验证记录。

    pool 必填——不提供"取最新一份"的回退，那正是静默混池的来源。
    返回 dict: {report, TEST: leg, VAL: leg}，leg 含 zip/timerange 等。
    """
    if not pool:
        raise ValueError("find_validation 必须指定 pool（防静默混池）")
    pool = pool.lower()
    cands = sorted(REPORT_DIR.glob(f"validate_{strategy}_*.md"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for md in cands:
        side = md.with_suffix(".json")
        legs = _legs_from_sidecar(side) if side.exists() else _legs_from_markdown(md)
        if (pool, "TEST") in legs and (pool, "VAL") in legs:
            return {"report": md.name, "TEST": legs[(pool, "TEST")], "VAL": legs[(pool, "VAL")]}
    have = sorted({k[0] for md in cands[:10]
                   for k in (_legs_from_sidecar(md.with_suffix(".json")) if md.with_suffix(".json").exists()
                             else _legs_from_markdown(md))})
    raise SystemExit(f"{strategy}: 找不到 {pool.upper()} 池的 TEST+VAL 验证报告（{REPORT_DIR}）。"
                     f"已有池: {have or '无'}；先跑 validate_strategy.py --strategy {strategy} --pool {pool}")


def load_arm(strategy, pool):
    """读某策略在指定池上的 TEST+VAL 交易，过滤到池内品种。返回 (df, 报告名)。

    df 列: pair/pair_base/open_dt/close_dt/profit$/profit_ratio/seg。seg 取自交易所属的回测腿，
    而不是按 close_dt 重切——TEST 腿末尾被 force_exit 的交易不会被错算进 VAL。
    """
    v = find_validation(strategy, pool)
    frames = []
    for seg in ("TEST", "VAL"):
        _, trades = read_result(v[seg]["zip"])
        frames.append(trades_frame(trades, seg))
    df = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(
        not f.empty for f in frames) else pd.DataFrame(
        columns=["pair", "pair_base", "open_dt", "close_dt", "profit$", "profit_ratio", "seg"])
    if not df.empty:
        df = df[df["pair_base"].isin(pool_bases(pool))].reset_index(drop=True)
    return df, v["report"]


def leg_span_years(strategy, pool):
    """该验证记录两腿各自的回测覆盖年数 {TEST: y, VAL: y}（年化分母，替代硬编码 2.657/2.002）。"""
    v = find_validation(strategy, pool)
    return {seg: result_span_years(read_result(v[seg]["zip"])[0]) for seg in ("TEST", "VAL")}


def leg_window(strategy, pool, seg):
    """该策略验证腿的实际回测窗口 (start, end)，UTC。

    组合脚本拿它求"各臂共同窗口"——VAL 右端开放、各臂验证日期不同，
    不截齐就会拿不同长度的段算年化/相关，增量 pp 不可比。
    """
    stats, _ = read_result(find_validation(strategy, pool)[seg]["zip"])
    return pd.Timestamp(stats["backtest_start"], tz="UTC"), pd.Timestamp(stats["backtest_end"], tz="UTC")
