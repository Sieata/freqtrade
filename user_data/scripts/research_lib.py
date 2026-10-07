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
def read_result(zip_path):
    """读回测 zip → (stats dict, trades list)。"""
    zip_path = Path(zip_path)
    if not zip_path.is_absolute():
        zip_path = BT_DIR / zip_path
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            if not n.endswith(".json"):
                continue
            d = json.loads(z.read(n))
            if isinstance(d, dict) and "strategy" in d:
                s = d["strategy"][next(iter(d["strategy"]))]
                return s, s.get("trades", [])
    raise SystemExit(f"{zip_path.name}: 无策略结果")


def result_span_years(stats):
    """回测实际覆盖年数（backtest_start→backtest_end），年化分母用它而非首末笔交易跨度。

    低频事件臂首笔可能在区间开头数月之后，按交易跨度年化会系统性虚高。
    """
    a = pd.Timestamp(stats["backtest_start"])
    b = pd.Timestamp(stats["backtest_end"])
    return max((b - a).total_seconds() / 86400 / 365.25, 1e-9)


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
