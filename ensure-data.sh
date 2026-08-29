#!/bin/bash
# 增量更新 K 线数据（从缓存末尾续传到最新）— macOS/Linux 版（对应 ensure-data.ps1）
# 用法: ./ensure-data.sh [pairs文件]
#   ./ensure-data.sh                                    # 默认 14 个既有品种
#   ./ensure-data.sh user_data/universe/pairs_core.txt   # 按币池快照补数据（支持行内 # 注释）
# 可安全重复运行 — 已有数据只补新增部分，不重不漏。
# 数据已随仓库分发（user_data/data/README.md）：新机器 clone 后无需先下载，本脚本只做增量更新。
# 首次运行从 2021-01-01 全量下载：4h 策略需 250 根暖机、1d 策略需 MA200 暖机，
# 暖机口径的教训见 user_data/paper/FREEZE_V2.md 第八节。
#
# 注意：新品种的 funding 老数据会被 WAF 403 拦，需另行用
# user_data/scripts/import_funding_vision.py 从 data.binance.vision 补。
#
# 网络说明：本机直连 binance API 不通，走系统代理 127.0.0.1:7897（Clash）。
# 若代理端口不同，用 FT_PROXY 环境变量覆盖；代理关闭时用 FT_PROXY=none 直连。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# Windows(Git Bash) 的 venv 布局是 Scripts/python.exe，Unix 是 bin/python
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$ROOT/.venv/Scripts/python.exe"
FT_PROXY="${FT_PROXY:-http://127.0.0.1:7897}"

if [ ! -x "$PY" ]; then
    echo "[!] python not found: $PY (先运行 uv venv .venv --python 3.12)" >&2
    exit 1
fi

if [ "$FT_PROXY" != "none" ]; then
    export https_proxy="$FT_PROXY" http_proxy="$FT_PROXY"
fi

PAIRS=(
    "BTC/USDT:USDT" "ETH/USDT:USDT" "SOL/USDT:USDT"
    "XRP/USDT:USDT" "BNB/USDT:USDT" "ZEC/USDT:USDT"
    "HOME/USDT:USDT" "BANK/USDT:USDT" "CYS/USDT:USDT"
    "HYPE/USDT:USDT" "DOGE/USDT:USDT" "ADA/USDT:USDT"
    "AVAX/USDT:USDT" "DOT/USDT:USDT"
)

if [ $# -ge 1 ]; then
    PAIRS_FILE="$1"
    if [ ! -f "$PAIRS_FILE" ]; then
        echo "[!] 找不到 pairs 文件: $PAIRS_FILE" >&2
        exit 1
    fi
    PAIRS=()
    while IFS= read -r raw; do
        line="$(printf '%s' "$raw" | sed 's/#.*//' | tr -d ' \t\r')"
        if [ -n "$line" ]; then
            PAIRS+=("$line")
        fi
    done < "$PAIRS_FILE"
    echo "从 $PAIRS_FILE 读取 ${#PAIRS[@]} 个品种"
fi

echo "增量更新 K 线（4h + 1d，futures 模式自动补 funding/mark）..."
echo "品种 (${#PAIRS[@]}): ${PAIRS[*]}"
echo

"$PY" -m freqtrade download-data \
    --exchange binance \
    --trading-mode futures \
    --timeframes 4h 1d \
    --timerange 20210101- \
    --pairs "${PAIRS[@]}"

echo
echo "K 线数据已是最新！"
