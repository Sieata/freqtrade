#!/bin/bash
# 数据保鲜一键入口：K线/funding 增量 + OI 累积 + metrics 月度增量 + 接缝校验
#
# 用法（一条命令，可安全重复运行，增量耗时约 1-3 分钟）：
#   ./refresh_all.sh                                  # 默认 TOP10 池，自动探测本地代理
#   ./refresh_all.sh --pool core                      # 换池：top10 / top5 / top2 / core / volume
#   FT_PROXY=http://127.0.0.1:7897 ./refresh_all.sh   # 显式指定代理
#   FT_PROXY=none ./refresh_all.sh                    # 强制直连
#
# 说明：
#   - --pool 只作用于 [1/4] K线/funding 与 [4/4] 接缝校验（校验按该池）；
#   - [2/4] OI 累积器固定按自己的默认 13 品种；[3/4] metrics 固定 TOP10
#     （core/volume 全量补 metrics 要拉数千个日包，评估用不到，故不随池变化）；
#   - 非 TOP10 池的接缝校验带 --no-metrics，只看 K线，避免 metrics 滞后误报。
#
# Windows PowerShell 等价入口：.\refresh.ps1 [--pool core]
# 校验规则见 user_data/scripts/data_check.py；数据分发方式见 user_data/data/README.md。

set -uo pipefail

# ── 参数 ────────────────────────────────────────────────────────────────
POOL="top10"
while [ $# -gt 0 ]; do
    case "$1" in
        --pool)   POOL="${2:-}"; shift 2 || shift ;;
        --pool=*) POOL="${1#*=}"; shift ;;
        -h|--help)
            echo "用法: $0 [--pool top10|top5|top2|core|volume]"
            echo "  --pool  要保鲜的币池（默认 top10），对应 user_data/universe/pairs_<pool>.txt"
            exit 0 ;;
        *) echo "[!] 未知参数: $1（用 --help 查看）" >&2; exit 2 ;;
    esac
done

POOL_FILE="user_data/universe/pairs_${POOL}.txt"
if [ -z "$POOL" ] || [ ! -f "$POOL_FILE" ]; then
    echo "[!] 池文件不存在: $POOL_FILE" >&2
    echo "    可用池: $(ls user_data/universe/pairs_*.txt 2>/dev/null | sed 's|.*pairs_||;s|\.txt||' | tr '\n' ' ')" >&2
    exit 2
fi

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

echo "池: $POOL（$POOL_FILE）"
echo
echo "── [1/4] K线/funding 增量（$POOL）──"
./ensure-data.sh "$POOL_FILE" || { echo "[!] ensure-data 失败（继续其他步骤）" >&2; FAIL=1; }

echo
echo "── [2/4] OI 累积器（OIFlush live 前置，漏跑不可回补；固定 13 品种）──"
"$PY" user_data/scripts/oi_accumulate.py || { echo "[!] oi_accumulate 失败" >&2; FAIL=1; }

echo
echo "── [3/4] metrics 月度增量（固定 TOP10 品种）──"
"$PY" user_data/scripts/import_metrics_vision.py BTC ETH BNB XRP SOL TRX HYPE ZEC DOGE XMR \
    | "$PY" -c "import sys; ls = sys.stdin.readlines(); print(''.join(ls[-10:]), end='')" \
    || { echo "[!] metrics 导入失败" >&2; FAIL=1; }

echo
echo "── [4/4] 接缝校验 + 最新数据日期（$POOL）──"
CHECK_ARGS=(--pools "$POOL")
[ "$POOL" = "top10" ] || CHECK_ARGS+=(--no-metrics)
if ! "$PY" user_data/scripts/data_check.py "${CHECK_ARGS[@]}"; then
    echo "[!] data_check 报出缺口/重复（见上方输出）" >&2
    FAIL=1
fi

"$PY" - "$POOL_FILE" <<'PYEOF'
import sys
import warnings
warnings.filterwarnings("ignore")
from datetime import datetime, timezone

import pyarrow.feather as f

pool_file = sys.argv[1]
pairs = [line.split("#")[0].strip()
         for line in open(pool_file, encoding="utf-8")
         if line.split("#")[0].strip()]

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
