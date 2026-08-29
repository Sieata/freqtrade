#!/bin/bash
# 数据保鲜一键脚本：会话开始/paper 设备 cron 用
# = TOP10+核心 K线/funding 增量 + OI 累积器 + metrics 月度增量
# 可安全重复运行。FT_PROXY 可覆盖代理（默认 http://127.0.0.1:7897）。

set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# Windows(Git Bash) 的 venv 布局是 Scripts/python.exe，Unix 是 bin/python
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$ROOT/.venv/Scripts/python.exe"
[ -x "$PY" ] || { echo "[!] python not found" >&2; exit 1; }

FT_PROXY="${FT_PROXY:-http://127.0.0.1:7897}"
if [ "$FT_PROXY" != "none" ]; then
    export https_proxy="$FT_PROXY" http_proxy="$FT_PROXY"
fi
export PYTHONIOENCODING=utf-8

cd "$ROOT"

echo "── [1/3] K线/funding 增量（TOP10 池）──"
./ensure-data.sh user_data/universe/pairs_top10.txt || echo "[!] ensure-data 失败（继续其他步骤）"

echo
echo "── [2/3] OI 累积器（OIFlush live 前置）──"
"$PY" user_data/scripts/oi_accumulate.py || echo "[!] oi_accumulate 失败"

echo
echo "── [3/3] metrics 月度增量（TOP10 品种）──"
"$PY" user_data/scripts/import_metrics_vision.py BTC ETH BNB XRP SOL TRX HYPE ZEC DOGE XMR | tail -10

echo
echo "保鲜完成。提示：git 里已有全部历史，多设备各自运行互不冲突（feather 随 git 分发）。"
