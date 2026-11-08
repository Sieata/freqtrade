"""paper 两臂（V2:8080 / OI:8082）分离式重启工具（2026-11-08）。

为什么需要它（轮 10 实操教训）：
  - 直接用 `bash paper_start*.sh &` 或作为本会话后台 job 启动，进程会被绑在
    调用者的进程树上，会话/作业结束就被 SIGHUP 连带杀掉 → paper 静默停跑。
  - 反复用普通方式拉起会残留孤儿进程，多个实例抢同一个 db/端口（实测出现 6+ 个
    freqtrade trade 进程）。
  本工具用 Windows DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP 起一个全新的、
  与会话无关的进程组，再调用已加固的 paper_start*.sh（内置 SHA 校验）。
  启动前可选先清理既有 freqtrade trade 进程，保证每个端口只有一份。

用法（项目根目录）：
  .venv/Scripts/python.exe user_data/scripts/paper_restart.py            # 两臂都重启
  .venv/Scripts/python.exe user_data/scripts/paper_restart.py --only v2
  .venv/Scripts/python.exe user_data/scripts/paper_restart.py --only oi
  .venv/Scripts/python.exe user_data/scripts/paper_restart.py --no-kill  # 不清理旧的
  .venv/Scripts/python.exe user_data/scripts/paper_restart.py --dry-run  # 只打印将做什么

只做进程管理，不碰策略源码/数据/交易；不读 VAL。
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = ROOT / "user_data" / "scripts"
LOGS = ROOT / "user_data" / "logs"

# DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP（Windows）
_CREATE_FLAGS = 0x00000008 | 0x00000200

BOTS = {
    "v2": SCRIPTS / "paper_start.sh",
    "oi": SCRIPTS / "paper_start_oi.sh",
}

PINGS = {
    "v2": ("http://127.0.0.1:8080/api/v1/ping", 8080),
    "oi": ("http://127.0.0.1:8082/api/v1/ping", 8082),
}


def _kill_existing(only=None):
    """杀掉 freqtrade ... trade 进程（含其 fork 子进程树）。

    only=None 时杀掉所有臂；only='v2'/'oi' 时只杀对应臂（按 config 文件名匹配，
    v2→config_paper_v2.json, oi→config_paper_oi.json），避免重启一个臂时误杀另一个。
    返回杀掉的 PID 列表。
    """
    try:
        import psutil
    except Exception:
        print("[!] 未安装 psutil，跳过清理（可能残留旧进程）")
        return []
    needle = None
    if only:
        needle = f"config_paper_{only}.json"
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


def _ping(name, timeout=2.0):
    url, _ = PINGS[name]
    try:
        import requests

        r = requests.get(url, timeout=timeout, auth=("freqtrader", "IUdQZFygM_n6weNX"))
        return r.status_code == 200 and r.json().get("status") == "pong"
    except Exception:
        return False


def _launch(name, dry_run=False):
    """分离式启动一个臂。返回 launcher 的 PID（或 None）。"""
    script = BOTS[name]
    if not script.exists():
        print(f"[!] launcher 不存在: {script}")
        return None
    bash = shutil.which("bash")
    if not bash:
        print("[!] PATH 中找不到 bash，无法启动")
        return None
    LOGS.mkdir(parents=True, exist_ok=True)
    logpath = LOGS / f"paper_{name}_launcher.log"
    if dry_run:
        print(f"[dry-run] 将分离启动 {name}: {bash} -c 'cd {ROOT} && bash {script}' >> {logpath}")
        return None
    log = open(logpath, "wb")
    # 关键坑（轮 10 实测，winuxcmd/MSYS bash）：若把 launcher 用绝对 /c/... 路径传给
    # `bash <script>`，bash 会把该参数再转成 Windows 反斜杠 'C:\...'，使 launcher 里
    # 的 case */* 判断失配 → ROOT 解析错（丢到 /c/Users/fs2023）。因此改用它自己的
    # cwd 相对路径（与手动 `bash user_data/scripts/paper_start.sh` 完全一致）。
    rel_script = script.relative_to(ROOT).as_posix()
    inner = f"bash '{rel_script}'"
    p = subprocess.Popen(
        [bash, "-c", inner],
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        creationflags=_CREATE_FLAGS,
        cwd=str(ROOT),
    )
    return p.pid


def main():
    ap = argparse.ArgumentParser(description="paper 两臂分离式重启（V2/OI）")
    ap.add_argument("--only", choices=["v2", "oi"], help="只重启某一个臂（默认两臂都重启）")
    ap.add_argument("--no-kill", action="store_true", help="不清理既有 freqtrade trade 进程")
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的动作")
    ap.add_argument("--wait", type=int, default=12, help="启动后等待秒数（默认 12）")
    args = ap.parse_args()

    names = [args.only] if args.only else list(BOTS.keys())

    print("=" * 60)
    print("paper 两臂分离式重启")
    print("=" * 60)

    if not args.no_kill:
        killed = _kill_existing(only=args.only)
        scope = args.only or "全部"
        print(f"[kill] 清理既有 freqtrade trade 进程（{scope}）: {killed if killed else '无'}")
        if killed and not args.dry_run:
            time.sleep(3)
    else:
        print("[kill] 跳过清理（--no-kill）")

    for n in names:
        pid = _launch(n, dry_run=args.dry_run)
        if pid:
            print(f"[start] {n} launcher 已分离启动 (PID {pid})，日志 user_data/logs/paper_{n}_launcher.log")
        elif not args.dry_run:
            print(f"[!] {n} 启动失败")

    if args.dry_run:
        return

    print(f"[wait] 等待 {args.wait}s 后探活…")
    time.sleep(args.wait)
    ok = 0
    for n in names:
        alive = _ping(n)
        print(f"    {n:>3} :{PINGS[n][1]} → {'pong ✓' if alive else '无响应 ✗'}")
        ok += 1 if alive else 0
    print("-" * 60)
    if ok == len(names):
        print(f"结论：{len(names)} 个臂全部存活")
        sys.exit(0)
    print(f"结论：{len(names) - ok} 个臂未存活，检查 user_data/logs/paper_*_launcher.log")
    sys.exit(1)


if __name__ == "__main__":
    main()
