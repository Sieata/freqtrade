"""paper 两臂（V2:8080 / OI:8082）分离式重启工具（2026-11-08，schtasks 版）。

为什么需要它（轮 10 实操教训，逐层踩坑后定位根因）：
  直接 `bash paper_start*.sh &`、或作为本会话后台 job、甚至 Popen(DETACHED_PROCESS)，
  拉起的进程**仍属本会话 ops 执行器的 Windows Job Object**。该调用一结束，Job 的
  KILL_ON_JOB_CLOSE 会把整棵子进程树（含"已分离"的孙子进程）一起杀掉 → paper 静默停跑
  （实测：两臂在两次调用之间双双消失、进程数归零）。反复普通拉起还会残留多实例抢同一 db。

  唯一可靠的办法：让 **Windows 计划任务（Task Scheduler 服务）** 承载 bot 进程。任务动作
  直接是"venv python -m freqtrade trade …"（长驻），任务状态保持 Running；它不属于任何
  交互式会话的 Job Object，本会话结束/关终端都不影响。

设计：
  - SHA 校验在本工具里做（读 strategies/<S>.py 计算 SHA256 与冻结值比对），不匹配拒绝启动。
    冻结值：V2 见 FREEZE_V2.md，OI 见 FREEZE_OI.md（下方 FROZEN 字典）。
  - 每个臂注册一个计划任务 paper_v2 / paper_oi（固定名，重启用 -Force 覆盖，天然防重复）。
  - bot 直接写自己的 logfile；stdout 不便于持久化，故任务动作里重定向到 *_launcher.log。
  - --only v2|oi 按 config 文件名 scoped 清理，重启一臂不误杀另一臂。

用法（项目根目录）：
  .venv/Scripts/python.exe user_data/scripts/paper_restart.py                # 两臂都重启
  .venv/Scripts/python.exe user_data/scripts/paper_restart.py --only v2
  .venv/Scripts/python.exe user_data/scripts/paper_restart.py --no-kill
  .venv/Scripts/python.exe user_data/scripts/paper_restart.py --dry-run
  .venv/Scripts/python.exe user_data/scripts/paper_restart.py --status        # 只看状态

只做进程管理 + SHA 完整性校验，不碰策略源码/数据/交易；不读 VAL。
"""
import argparse
import hashlib
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
STRAT_DIR = ROOT / "user_data" / "strategies"
SCRIPTS = ROOT / "user_data" / "scripts"
LOGS = ROOT / "user_data" / "logs"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"

# 每臂定义：策略类名、config 文件、端口、db、冻结 SHA（=源码 SHA256）
ARMS = {
    "v2": {
        "strategy": "WeekendReverseV2",
        "config": "user_data/config_paper_v2.json",
        "port": 8080,
        "db": "user_data/tradesv3.dryrun.sqlite",
        "frozen": "1b90c90bb7873500a30e8f821713935f497a018d28736596fdaa6f97743b46d1",
    },
    "oi": {
        "strategy": "OIFlushV2",
        "config": "user_data/config_paper_oi.json",
        "port": 8082,
        "db": "user_data/tradesv3.dryrun.oi.sqlite",
        "frozen": "d714e0922be084932f2fe477a52a755b0507f9ebb01699830d2e45d154dc5a98",
    },
}

USER = "freqtrader"
PASS = "IUdQZFygM_n6weNX"


def _src_sha(strategy):
    p = STRAT_DIR / f"{strategy}.py"
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _ping(arm, timeout=2.5):
    url = f"http://127.0.0.1:{ARMS[arm]['port']}/api/v1/ping"
    try:
        import requests

        r = requests.get(url, timeout=timeout, auth=(USER, PASS))
        return r.status_code == 200 and r.json().get("status") == "pong"
    except Exception:
        return False


def _kill_existing(only=None):
    """杀 freqtrade trade 进程；only 时按 config 文件名 scoped。返回 PID 列表。"""
    try:
        import psutil
    except Exception:
        print("[!] 未安装 psutil，跳过清理")
        return []
    needle = f"config_paper_{only}.json" if only else None
    killed = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        cl = p.info.get("cmdline") or []
        s = " ".join(cl)
        if "freqtrade" not in s or "trade" not in cl:
            continue
        if needle and needle not in s:
            continue
        try:
            p.kill()
            killed.append(p.info["pid"])
        except Exception:
            pass
    return killed


def _task_name(arm):
    return f"paper_{arm}"


def _ps_run(script, timeout=90):
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return r.returncode, (r.stdout or "") + "\n" + (r.stderr or "")


def _launch(arm, dry_run=False):
    """注册并启动计划任务（动作=直接跑 freqtrade）。返回 task 名或 None。"""
    info = ARMS[arm]
    # 1) SHA 完整性校验（forward-test 期间源码不得改动）
    cur = _src_sha(info["strategy"])
    if cur is None:
        print(f"[!] {arm}: 策略源文件缺失 {info['strategy']}.py")
        return None
    if cur != info["frozen"]:
        print(f"[!] {arm}: {info['strategy']}.py SHA 漂移，拒绝启动（forward-test 作废）")
        print(f"    expected {info['frozen']}")
        print(f"    current  {cur}")
        return None

    LOGS.mkdir(parents=True, exist_ok=True)
    task = _task_name(arm)
    logfile = f"user_data/logs/paper_{arm}_forwardtest.log"
    stdout = f"user_data/logs/paper_{arm}_launcher.log"
    py = str(VENV_PY)
    cfg = info["config"]
    strat = info["strategy"]

    if dry_run:
        print(f"[dry-run] {arm}: 计划任务 {task} 将运行")
        print(f"    {py} -m freqtrade trade --config {cfg} --strategy {strat} --logfile {logfile}")
        return None

    # --- 关键设计（2026-11-08 重构，见 AGENTS.md 教训）---
    # ① 非管理员 fs2023 下 `Register-ScheduledTask -Force` 修改/新建任务会 Access denied，
    #    且 /SC ONSTART 特权触发同样需管理员。唯一无管理员可靠路径 = schtasks.exe /Create /F
    #    配 /SC MINUTE /MO 30（用户级、每 30 分钟重复；bot 崩溃后下一次重复自动拉起 = 自愈；
    #    schtasks 默认单实例：任务仍 Running 时重复触发被拒，不会双开同一 db）。
    # ② schtasks 内联 /TR 含 `cmd /c ""...""` 嵌套引号 + >> 重定向会 Last Result=1 失败，
    #    故写一个 .bat 包装文件（cd /d 仓库根 + venv python + 重定向），/TR 只指向 .bat 绝对路径。
    bat = SCRIPTS / f"_run_paper_{arm}.bat"
    bat.write_text(
        "@echo off\r\n"
        f'cd /d "{ROOT}"\r\n'
        f'"{py}" -m freqtrade trade --config {cfg} --strategy {strat} '
        f"--logfile {logfile} >> {stdout} 2>&1\r\n",
        encoding="utf-8",
    )

    def _sch(args, timeout=60):
        r = subprocess.run(["schtasks.exe"] + args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    _sch(["/Delete", "/TN", task, "/F"])  # 容忍不存在
    rc, out = _sch(["/Create", "/TN", task, "/TR", str(bat), "/SC", "MINUTE", "/MO", "30", "/F"])
    if rc != 0 or "SUCCESS" not in out.upper():
        print(f"[!] {arm}: 计划任务创建失败 rc={rc}\n{out.strip()[:400]}")
        return None
    rc, out = _sch(["/Run", "/TN", task])
    if rc != 0 or "SUCCESS" not in out.upper():
        print(f"[!] {arm}: 计划任务启动失败 rc={rc}\n{out.strip()[:400]}")
        return None
    print(f"[task] {arm}: 已注册 {task}（/SC MINUTE /MO 30, .bat 包装）并启动")
    return task


def _status(arm):
    rc, out = _ps_run(
        f"(Get-ScheduledTask -TaskName 'paper_{arm}' -ErrorAction SilentlyContinue).State"
    )
    state = out.strip().splitlines()[0] if out.strip() else "None"
    return state, _ping(arm)


def main():
    ap = argparse.ArgumentParser(description="paper 两臂分离式重启（schtasks 承载，V2/OI）")
    ap.add_argument("--only", choices=["v2", "oi"], help="只重启某一个臂")
    ap.add_argument("--no-kill", action="store_true", help="不清理既有 freqtrade trade 进程")
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的动作")
    ap.add_argument("--status", action="store_true", help="只查看状态，不重启")
    ap.add_argument("--wait", type=int, default=60, help="启动后最长等待秒数（默认 60，轮询探活）")
    args = ap.parse_args()

    names = [args.only] if args.only else list(ARMS.keys())

    print("=" * 60)
    print("paper 两臂分离式重启（Task Scheduler 承载）")
    print("=" * 60)

    if args.status:
        for n in names:
            state, alive = _status(n)
            print(f"    {n:>3} :{ARMS[n]['port']}  任务状态={state}  存活={'pong ✓' if alive else '无响应 ✗'}")
        return

    if args.dry_run:
        print("[dry-run] 不清理进程、不注册任务，仅打印动作")
    elif not args.no_kill:
        killed = _kill_existing(only=args.only)
        print(f"[kill] 清理既有 freqtrade trade 进程（{args.only or '全部'}）: {killed if killed else '无'}")
        if killed:
            time.sleep(3)
    else:
        print("[kill] 跳过清理（--no-kill）")

    for n in names:
        task = _launch(n, dry_run=args.dry_run)
        if task:
            print(f"[start] {n} 计划任务已注册并启动 ({task})，见 user_data/logs/paper_{n}_forwardtest.log")
        elif not args.dry_run:
            print(f"[!] {n} 启动失败")

    if args.dry_run:
        return
    deadline = time.time() + args.wait
    status = {n: False for n in names}
    while time.time() < deadline:
        for n in names:
            if not status[n] and _ping(n):
                status[n] = True
        if all(status.values()):
            break
        time.sleep(3)

    ok = 0
    for n in names:
        alive = status[n]
        print(f"    {n:>3} :{ARMS[n]['port']} → {'pong ✓' if alive else '无响应 ✗'}")
        ok += 1 if alive else 0
    print("-" * 60)
    if ok == len(names):
        print(f"结论：{len(names)} 个臂全部存活")
        sys.exit(0)
    print(f"结论：{len(names) - ok} 个臂未存活，检查计划任务与 user_data/logs/paper_*_launcher.log")
    sys.exit(1)


if __name__ == "__main__":
    main()
