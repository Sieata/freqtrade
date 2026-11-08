"""每日运维总览（2026-11-08）——一条命令看两臂健康度。

把三处已成熟的独立检查收敛成一个只读入口，服务上线/daily 巡检：
  1) 数据新鲜度与接缝（复用 data_check.collect_pairs + check_metrics 的判定）
  2) 当前高离散度分位（复用 disp_monitor.dispersion_pct，RESEARCH 31/37）
  3) 两臂冻结状态摘要（V2 源码 SHA 是否仍等于冻结值；报告是否齐全）
  4) paper bot 存活（探 V2:8080 / OI:8082 的 /api/v1/ping）

只读，不写任何数据、不改源码、不碰 VAL。退出码：0=全部健康，1=有告警
（数据缺口 / V2 源码 SHA 漂移 / 关键报告缺失 / paper bot 无响应）。

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
METRICS = ROOT / "data" / "binance" / "futures_metrics"
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


def _metrics_fname(pair):
    return METRICS / (pair.replace("/", "_").replace(":", "_") + "-4h-metrics.feather")


def check_oi_freshness(pairs, now=None, max_age_h=24.0):
    """检查 OI 臂输入（futures_metrics 的 oi_usd）是否新鲜且连续。

    这是 OI 臂唯一的数据输入（accumulator 每日采集 openInterestHist，仅留 30 天、
    漏跑不可回补）。K线用另一套端点，K线在更 ≠ OI 在更：accumulator 停跑时
    K线检查仍绿，OI 臂会静默零信号且 daily_ops 全绿 —— 必须独立检查。

    返回 (problems, details)。problems 为 (pair, msg) 列表；
    details 为 {pair: last_oi_ts} 供展示。
    """
    import pandas as pd

    problems, details = [], {}
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    for p in pairs:
        f = _metrics_fname(p)
        if not f.exists():
            problems.append((p, "OI metrics 文件缺失"))
            continue
        try:
            d = pd.read_feather(f)
        except Exception as e:  # 损坏的 feather 不能让巡检崩
            problems.append((p, f"OI metrics 读取失败: {e}"))
            continue
        if "oi_usd" not in d.columns:
            problems.append((p, "无 oi_usd 列"))
            continue
        d["date"] = pd.to_datetime(d["date"], utc=True)
        vo = d[d["oi_usd"].notna()].sort_values("date")
        if vo.empty:
            problems.append((p, "OI 全空（accumulator 从未写入）"))
            continue
        last = vo["date"].max()
        details[p] = last
        age_h = (now - last).total_seconds() / 3600
        if age_h > max_age_h:
            problems.append((p, f"OI 陈旧 {age_h:.1f}h（>{max_age_h:.0f}h，accumulator 可能停跑）"))
    return problems, details


def staleness_hours(newest, now=None):
    """最新 4h bar 距当前的小时数。newest 为 None 返回 None。可用于陈旧度告警。"""
    if newest is None:
        return None
    import pandas as pd

    now = now or pd.Timestamp.now(tz="UTC")
    return (pd.Timestamp(now) - pd.Timestamp(newest)).total_seconds() / 3600


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


PAPER_BOTS = [
    ("V2", 8080),
    ("OI", 8082),
]


def _paper_ping(port, timeout=2.0):
    """对 paper bot 的 /api/v1/ping 做一次 HTTP GET。返回 True/False。

    不依赖 requests（若未装则回退 urllib），不需要 api_server 认证也不会因 ping 失败。
    只探端口是否在服务（pong），不读交易数据。
    """
    url = f"http://127.0.0.1:{port}/api/v1/ping"
    try:
        import requests

        r = requests.get(url, timeout=timeout, auth=("freqtrader", "IUdQZFygM_n6weNX"))
        return r.status_code == 200 and r.json().get("status") == "pong"
    except Exception:
        pass
    try:
        import json
        import urllib.request

        req = urllib.request.Request(url)
        import base64

        token = base64.b64encode(b"freqtrader:IUdQZFygM_n6weNX").decode()
        req.add_header("Authorization", "Basic " + token)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read()).get("status") == "pong"
    except Exception:
        return False


def check_paper_bots():
    """返回 (alive_list, dead_list)，元素为 (name, port)。"""
    alive, dead = [], []
    for name, port in PAPER_BOTS:
        (alive if _paper_ping(port) else dead).append((name, port))
    return alive, dead


def _write_snapshot(snap, alerts):
    """追加一条 UTF-8 JSONL 快照到 user_data/logs/daily_ops.jsonl。

    只追加、不改历史，供事后重建 forward-test 的逐日轨迹
    （数据新鲜度 / 离散度 / 两臂存活 / 信号与漏单计数）。
    """
    import json
    from datetime import datetime, timezone

    rec = {"ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "alerts": alerts}
    rec.update(snap)
    path = ROOT / "logs" / "daily_ops.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"    [record] 已追加快照 → {path.relative_to(ROOT)}")


def main():
    R.enable_utf8_console()
    ap = argparse.ArgumentParser(description="每日运维总览（只读，两臂健康度）")
    ap.add_argument("--pool", default="top10", help="用于数据新鲜度检查的池（默认 top10）")
    ap.add_argument("--days", type=int, default=14, help="离散度回看的宽限天数（默认 14，仅提示用）")
    ap.add_argument(
        "--no-bots",
        action="store_true",
        help="跳过 paper bot 存活检查（默认会探 8080/8082 的 ping）",
    )
    ap.add_argument(
        "--no-recon",
        action="store_true",
        help="跳过 paper 信号对账（默认对 V2 周末窗口 long_entry 与开仓对账）",
    )
    ap.add_argument(
        "--record",
        action="store_true",
        help="追加一条 JSONL 快照到 user_data/logs/daily_ops.jsonl（默认不写，保持只读）",
    )
    args = ap.parse_args()

    snap = {}  # 供 --record 落盘的结构化快照
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
        snap["newest_bar"] = str(newest)[:19] if newest is not None else None
        snap["data_problems"] = len(probs)
        print(f"    最新 4h bar: {str(newest)[:16] if newest is not None else 'N/A'} (UTC)")
        if probs:
            alerts += 1
            for p, msg in probs:
                print(f"    [!] {p:<22} {msg}")
        else:
            print("    [ok] 无缺口、无重复")
        # 陈旧度（age）检查：只看"最新 bar 距今多少小时"。
        # check_freshness 只查接缝内部缺口；若每日刷新任务静默失败数天，尾部仍连续（无内部缺口），
        # 会误报"无缺口"、绿灯；故必须独立按 age 判定，否则 = 静默停更不可见。
        if newest is not None:
            import pandas as pd

            age_h = staleness_hours(newest)
            snap["newest_age_h"] = round(age_h, 1)
            # 4h bar：正常 0~8h（含下一次未收盘）；>12h 视为落下一根以上 → 告警。
            if age_h > 12:
                alerts += 1
                print(f"    [!] 数据陈旧：最新 bar 距今 {age_h:.1f}h（>12h，保鲜任务可能未跑）")
            else:
                print(f"    [ok] 数据新鲜：最新 bar 距今 {age_h:.1f}h")

    # 1b) OI 臂输入新鲜度（futures_metrics 的 oi_usd）—— 独立于 K 线的检查。
    #     OI 臂唯一输入；accumulator 停跑时 K 线仍绿而 OI 臂静默零信号，故必须单独查。
    print("    [1b] OI 输入新鲜度（OI 臂 futures_metrics oi_usd）")
    try:
        import paper_recon as PR

        oi_pairs = PR._bot_whitelist("oi")
        oi_probs, oi_details = check_oi_freshness(oi_pairs)
        snap["oi_pairs"] = len(oi_pairs)
        snap["oi_problems"] = len(oi_probs)
        if oi_details:
            newest_oi = max(oi_details.values())
            snap["oi_newest"] = str(newest_oi)[:19]
            oi_age_h = (pd.Timestamp.now(tz="UTC") - newest_oi).total_seconds() / 3600
            snap["oi_newest_age_h"] = round(oi_age_h, 1)
            print(f"    OI 最新 {str(newest_oi)[:16]} UTC（{len(oi_details)}/{len(oi_pairs)} 品种有数据，距今 {oi_age_h:.1f}h）")
        if oi_probs:
            alerts += 1
            for p, msg in oi_probs:
                print(f"    [!] {p:<22} {msg}")
        else:
            print("    [ok] OI 输入全部新鲜、无缺口")
    except Exception as e:  # 巡检自身不能因此崩
        alerts += 1
        print(f"    [!] OI 新鲜度检查失败: {e}")

    # 2) 高离散度分位
    print(f"\n[2] 高离散度旁路指标（RESEARCH 31/37）")
    try:
        d = current_dispersion()
        if d is None:
            print("    [!] 无可用离散度序列，检查本地 K 线")
            alerts += 1
        else:
            flag = "★高离散" if d["high"] else "非高离散"
            snap["disp_pct"] = round(d["disp"] * 100, 3)
            snap["disp_pctile"] = round(d["pct"], 4)
            snap["disp_high"] = bool(d["high"])
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
    snap["v2_sha_ok"] = err is None
    if err:
        alerts += 1
        print(f"    [!] {err}")
    else:
        print(f"    [ok] SHA 与冻结值一致 ({h[:16]}…)")

    # 4) paper bot 存活（V2:8080 / OI:8082）
    if not args.no_bots:
        print("\n[4] paper bot 存活（V2:8080 / OI:8082）")
        alive, dead = check_paper_bots()
        snap["bots_alive"] = [n for n, _ in alive]
        snap["bots_dead"] = [n for n, _ in dead]
        for name, port in alive:
            print(f"    [ok] {name} :{port} → pong")
        for name, port in dead:
            alerts += 1
            print(f"    [!] {name} :{port} → 无响应（进程可能已退出，用 paper_start*.sh 重拉）")

    # 5) paper 信号对账（揪重启漏单）：V2 周末窗口 long_entry + OI 去杠杆 flush
    if not args.no_recon:
        print("\n[5] paper 信号对账（V2 周末窗口 long_entry / OI flush vs 实际开仓）")
        try:
            import paper_recon as PR

            for arm, label in (("v2", "V2 周末窗口"), ("oi", "OI 去杠杆 flush")):
                r = PR.recon_arm(arm, days=args.days, pool=args.pool)
                if r.get("skipped"):
                    continue
                print(
                    f"    [{arm}] 最近 {args.days} 天：{label} 信号 {r['n_entries']} 个 | "
                    f"DB 实际开仓 {r['n_trades']} 笔 | bot 上线(≈) {r['first_heartbeat_utc']}"
                )
                if r["missed"]:
                    alerts += 1
                    print(f"      [!] {arm} 疑似漏单 {len(r['missed'])} 条（上线后信号、无对应 trade）：")
                    for m in r["missed"]:
                        print(f"          {m['pair']:<18} {m['bar']} close={m['close']}")
                else:
                    print(f"      [ok] {arm} 无上线后漏单")
                snap.setdefault("recon", {})[arm] = {
                    "signals": r["n_entries"],
                    "trades": r["n_trades"],
                    "missed": len(r["missed"]),
                }
        except Exception as e:  # 缺数据/解析失败不致命
            print(f"    [!] 对账失败: {e}")
            alerts += 1

    print("\n" + "=" * 60)
    if args.record:
        _write_snapshot(snap, alerts)
    if alerts:
        print(f"结论：{alerts} 项告警，请排查")
        sys.exit(1)
    print("结论：全部健康")
    sys.exit(0)


if __name__ == "__main__":
    main()
