"""paper 数据保鲜计划任务（2026-11-08，schtasks 版）。

为什么需要它：
  两臂 paper bot 已由计划任务常驻运行（paper_restart.py），但 OI 臂依赖的
  user_data/data/binance/futures_metrics/<pair>-4h-metrics.feather 需要每日增量刷新
  （K线/funding + OI 累积 + metrics + 接缝校验）。原设计靠"每次会话手动跑 refresh_all.sh"，
  一旦长时间没有交互会话，OI metrics 会冻结、OI bot 静默零信号（不报错、不交易，监控只在
  当前时点看不出问题）。本任务把刷新交给 Windows 计划任务服务，脱离会话，每日自动跑。

设计：
  - 注册固定名计划任务 paper_refresh（-Force 覆盖，天然防重复）。
  - 动作：用 Git bash 执行仓库根 refresh_all.sh --pool top10，stdout/stderr 追加到
    user_data/logs/paper_refresh_launcher.log。
  - 触发器：每日固定时刻（默认 09:10，本地时间，需在 OI 4h 收盘后）；可 --at HH:MM 覆盖。
  - 只刷新数据，不碰策略源码/参数/冻结值，不读 VAL。

用法（项目根目录）：
  .venv/Scripts/python.exe user_data/scripts/paper_refresh_task.py            # 注册/更新并立即跑一次
  .venv/Scripts/python.exe user_data/scripts/paper_refresh_task.py --at 09:10
  .venv/Scripts/python.exe user_data/scripts/paper_refresh_task.py --dry-run
  .venv/Scripts/python.exe user_data/scripts/paper_refresh_task.py --status
  .venv/Scripts/python.exe user_data/scripts/paper_refresh_task.py --unregister
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOGS = ROOT / "user_data" / "logs"
TASK = "paper_refresh"
POOL = "top10"

# 稳定 Git bash（不要用任何临时 winuxcmd；这里指向系统安装的 Git Bash）
GBASH_CANDIDATES = [
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
]


def _find_bash():
    for c in GBASH_CANDIDATES:
        if Path(c).exists():
            return c
    return None


def _ps_single(s):
    """把字符串转成 PowerShell 单引号字面量（内部单引号翻倍）。"""
    return "'" + str(s).replace("'", "''") + "'"


def _ps_run(script, timeout=60):
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=timeout,
    )
    return r.returncode, (r.stdout or "") + "\n" + (r.stderr or "")


def _status():
    rc, out = _ps_run(
        f"Get-ScheduledTask -TaskName '{TASK}' -ErrorAction SilentlyContinue "
        f"| Select-Object TaskName,State | Format-List; "
        f"(Get-ScheduledTaskInfo -TaskName '{TASK}' -ErrorAction SilentlyContinue) "
        f"| Select-Object LastRunTime,LastTaskResult,NextRunTime | Format-List"
    )
    print(out.strip() or "[!] 查询无结果")


def _unregister():
    rc, out = _ps_run(f"Unregister-ScheduledTask -TaskName '{TASK}' -Confirm:$false -ErrorAction SilentlyContinue")
    print(f"[ok] 已注销任务 {TASK}" if "error" not in out.lower() else out)


def _register(at, dry_run=False):
    bash = _find_bash()
    if bash is None:
        print("[!] 未找到 Git bash（尝试过 " + " / ".join(GBASH_CANDIDATES) + "）")
        return False
    LOGS.mkdir(parents=True, exist_ok=True)
    log = f"user_data/logs/paper_refresh_launcher.log"
    # bash -lc: 登录 shell，cd 到工作目录后执行；工作目录由 -WorkingDirectory 给定。
    # 注意 -lc 下 pwd 默认是 HOME，故命令里显式 cd 到仓库根（用引号包裹 POSIX 路径）。
    root_posix = str(ROOT).replace("\\", "/")
    # -lc 外面用双引号，内部路径改用单引号，避免嵌套双引号把参数截断。
    cmd = f"cd '{root_posix}' && ./refresh_all.sh --pool {POOL}"
    arg = f'-lc "{cmd} >> {log} 2>&1"'

    if dry_run:
        print(f"[dry-run] 任务 {TASK} 将每日 {at} 运行：")
        print(f'    "{bash}" {arg}')
        return True

    ps = f"""
$ErrorActionPreference='Stop'
$arg = {_ps_single(arg)}
$wd = {_ps_single(str(ROOT))}
$exe = {_ps_single(bash)}
$action = New-ScheduledTaskAction -Execute $exe -Argument $arg -WorkingDirectory $wd
$trigger = New-ScheduledTaskTrigger -Daily -At '{at}'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName '{TASK}' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Output 'TASK_REGISTERED {TASK}'
"""
    rc, out = _ps_run(ps, timeout=90)
    ok = "TASK_REGISTERED" in out
    print(out.strip())
    return ok


def _run_once():
    rc, out = _ps_run(f"Start-ScheduledTask -TaskName '{TASK}'; Start-Sleep -Seconds 2; Write-Output 'STARTED'", timeout=30)
    print(out.strip())


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="paper 数据保鲜每日计划任务")
    ap.add_argument("--at", default="09:10", help="每日触发时刻 HH:MM（本地时间，默认 09:10）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将注册的任务，不落地")
    ap.add_argument("--status", action="store_true", help="只查任务状态")
    ap.add_argument("--unregister", action="store_true", help="注销任务")
    ap.add_argument("--no-run", action="store_true", help="注册后不立即执行一次")
    args = ap.parse_args()

    if args.status:
        _status()
        return
    if args.unregister:
        _unregister()
        return
    ok = _register(args.at, dry_run=args.dry_run)
    if ok and not args.dry_run and not args.no_run:
        _run_once()


if __name__ == "__main__":
    main()
