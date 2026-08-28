#!/bin/bash
# WeekendReverseV2 forward-test launcher (paper trading / dry-run) — macOS/Linux 版
# 用法（项目根目录）: ./user_data/scripts/paper_start.sh
# 前提: 数据已就绪（./ensure-data.sh）；本机直连 binance 需走代理（脚本内已设置）

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
CONFIG="$ROOT/user_data/config_paper_v2.json"
STRATEGY="WeekendReverseV2"
LOGDIR="$ROOT/user_data/logs"
LOGFILE="$LOGDIR/paper_v2_forwardtest.log"
FROZEN_SHA="1b90c90bb7873500a30e8f821713935f497a018d28736596fdaa6f97743b46d1"

# 0. preflight
[ -x "$PY" ] || { echo "[!] python not found: $PY" >&2; exit 1; }
[ -f "$CONFIG" ] || { echo "[!] config not found: $CONFIG" >&2; exit 1; }

# 1. 策略完整性：当前文件必须等于冻结快照（防止 forward-test 期间被改动）
CUR_SHA=$(shasum -a 256 "$ROOT/user_data/strategies/$STRATEGY.py" | cut -d' ' -f1)
if [ "$CUR_SHA" != "$FROZEN_SHA" ]; then
    echo "[!] $STRATEGY.py SHA256 mismatch — forward-test 作废 (paper/FREEZE_V2.md)" >&2
    echo "    expected: $FROZEN_SHA" >&2
    echo "    current:  $CUR_SHA" >&2
    exit 1
fi
echo "[ok] strategy SHA256 matches frozen snapshot"

# 2. 代理（binance API 需代理，与 ensure-data.sh 相同约定）
FT_PROXY="${FT_PROXY:-http://127.0.0.1:7897}"
if [ "$FT_PROXY" != "none" ]; then
    export https_proxy="$FT_PROXY" http_proxy="$FT_PROXY"
fi

mkdir -p "$LOGDIR"
cd "$ROOT"   # 保证相对路径(db_url/logs)统一解析到项目根

# 3. 后台启动 dry-run
nohup "$PY" -m freqtrade trade --config "$CONFIG" --strategy "$STRATEGY" \
    --logfile "$LOGFILE" > "$LOGDIR/paper_v2_stdout.log" 2>&1 &
PID=$!
disown

echo
echo "[OK] forward-test started (PID $PID)"
echo "    log:      $LOGFILE"
echo "    status:   .venv/bin/python user_data/scripts/paper_status.py"
echo "    api/ui:   http://127.0.0.1:8080  (user: freqtrader, pass in config_paper_v2.json)"
echo "    stop:     kill $PID"
echo
echo "note: 策略 SHA 已在启动前与冻结快照比对(见上方 [ok]); 期间改动策略文件会使本次 forward-test 作废。"
