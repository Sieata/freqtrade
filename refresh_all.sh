#!/bin/bash
# 数据保鲜一键入口：K线/funding 增量 + OI 累积 + metrics 月度增量 + 接缝校验
#
# 用法（一条命令，可安全重复运行，增量耗时约 1-3 分钟）：
#   ./refresh_all.sh                                  # 自动探测本地代理，探测不到则直连
#   FT_PROXY=http://127.0.0.1:7897 ./refresh_all.sh   # 显式指定代理
#   FT_PROXY=none ./refresh_all.sh                    # 强制直连
#
# Windows PowerShell 等价入口：.\refresh.ps1
# 校验规则见 user_data/scripts/data_check.py；数据分发方式见 user_data/data/README.md。

set -uo pipefail

# 纯 bash 定位脚本目录（不依赖 dirname，受限 shell 也可运行；2026-09-21 加固）
SELF="${BASH_SOURCE[0]}"
case "$SELF" in */*) ;; *) SELF="./$SELF" ;; esac
ROOT="$(cd "${SELF%/*}" && pwd)"
# Windows(Git Bash) 的 venv 布局是 Scripts/python.exe，Unix 是 bin/python
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$ROOT/.venv/Scripts/python.exe"
[ -x "$PY" ] || { echo "[!] python not found" >&2; exit 1; }

# ── 代理：未显式指定则自动探测（本机 Clash 常见端口 7897）────────────────
if [ -z "${FT_PROXY:-}" ]; then
    if "$PY" -c "import socket; socket.create_connection(('127.0.0.1', 7897), 0.3).close()" 2>/dev/null; then
        FT_PROXY="http://127.0.0.1:7897"
        echo "[i] 检测到本地代理 127.0.0.1:7897，走代理"
    else
        FT_PROXY="none"
        echo "[i] 未检测到本地代理，直连 binance"
    fi
fi
if [ "$FT_PROXY" != "none" ]; then
    export https_proxy="$FT_PROXY" http_proxy="$FT_PROXY"
fi
# 子进程（oi_accumulate.py 等）自带 7897 默认代理，必须把探测结果传下去，
# 否则探测到"直连"时 OI 步骤仍会去连不存在的代理。
export FT_PROXY
export PYTHONIOENCODING=utf-8

cd "$ROOT"
FAIL=0

echo "── [1/4] K线/funding 增量（TOP10 池）──"
./ensure-data.sh user_data/universe/pairs_top10.txt || { echo "[!] ensure-data 失败（继续其他步骤）" >&2; FAIL=1; }

echo
echo "── [2/4] OI 累积器（OIFlush live 前置，漏跑不可回补）──"
"$PY" user_data/scripts/oi_accumulate.py || { echo "[!] oi_accumulate 失败" >&2; FAIL=1; }

echo
echo "── [3/4] metrics 月度增量（TOP10 品种）──"
"$PY" user_data/scripts/import_metrics_vision.py BTC ETH BNB XRP SOL TRX HYPE ZEC DOGE XMR \
    | "$PY" -c "import sys; ls = sys.stdin.readlines(); print(''.join(ls[-10:]), end='')" \
    || { echo "[!] metrics 导入失败" >&2; FAIL=1; }

echo
echo "── [4/4] 接缝校验 + 最新数据日期 ──"
if ! "$PY" user_data/scripts/data_check.py --pools top10; then
    echo "[!] data_check 报出缺口/重复（见上方输出）" >&2
    FAIL=1
fi

"$PY" - <<'PYEOF'
import warnings
warnings.filterwarnings("ignore")
from datetime import datetime, timezone

import pyarrow.feather as f

pairs = []
for line in open("user_data/universe/pairs_top10.txt", encoding="utf-8"):
    line = line.split("#")[0].strip()
    if line:
        pairs.append(line)

now = datetime.now(timezone.utc)
print(f"\n{'pair':<16s} {'最新 4h K 线 (UTC)':<20s} {'落后':>7s}")
for p in pairs:
    fn = ("user_data/data/binance/futures/"
          + p.replace("/", "_").replace(":", "_") + "-4h-futures.feather")
    try:
        last = f.read_table(fn).column("date")[-1].as_py()
        lag_h = (now - last).total_seconds() / 3600
        print(f"{p:<16s} {last:%Y-%m-%d %H:%M}     {lag_h:6.1f}h")
    except Exception as e:  # 缺文件/空表不阻断整体流程
        print(f"{p:<16s} ERR {e}")
PYEOF

echo
if [ "$FAIL" -ne 0 ]; then
    echo "[!] 保鲜流程有步骤失败，请查看上方输出" >&2
    exit 1
fi
echo "[ok] 保鲜完成：K线/funding、OI、metrics 已增量更新，接缝校验通过。"
echo "     提示：feather 随 git 分发；如产生改动，按仓库纪律单独提交数据更新。"
