#!/bin/bash
# BigMoveV1 forward-test launcher (paper trading / dry-run) — macOS/Linux 版
# 用法（项目根目录）: ./user_data/scripts/paper_start_fs.sh
# 前提: 数据已就绪（./ensure-data.sh user_data/universe/pairs_core.txt）；与 V2 paper
#       可同机并行（独立 config/db/端口 8082），判据见 user_data/paper/FREEZE_FS.md

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
CONFIG="$ROOT/user_data/config_paper_bigmove.json"
STRATEGY="BigMoveV1"
LOGDIR="$ROOT/user_data/logs"
LOGFILE="$LOGDIR/paper_bigmove_forwardtest.log"
FROZEN_SHA="8d337d365356c87db9e7e0f831cbc479eb444ef1a11958a3cf16aecdbf917380"

# 0. preflight
[ -x "$PY" ] || { echo "[!] python not found: $PY" >&2; exit 1; }
[ -f "$CONFIG" ] || { echo "[!] config not found: $CONFIG" >&2; exit 1; }

# 1. 策略完整性：当前文件必须等于冻结快照（防止 forward-test 期间被改动）
CUR_SHA=$(shasum -a 256 "$ROOT/user_data/strategies/$STRATEGY.py" | cut -d' ' -f1)
if [ "$CUR_SHA" != "$FROZEN_SHA" ]; then
    echo "[!] $STRATEGY.py SHA256 mismatch — forward-test 作废 (paper/FREEZE_FS.md)" >&2
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
    --logfile "$LOGFILE" > "$LOGDIR/paper_bigmove_stdout.log" 2>&1 &
PID=$!
disown

echo
echo "[OK] BigMove forward-test started (PID $PID)"
echo "    log:      $LOGFILE"
echo "    db:       user_data/tradesv3.dryrun.bigmove.sqlite"
echo "    api/ui:   http://127.0.0.1:8082  (账号见 config_paper_bigmove.json)"
echo "    stop:     kill $PID"
echo
echo "note: 策略 SHA 已在启动前与冻结快照比对(见上方 [ok]); 期间改动策略文件会使本次 forward-test 作废。"
echo "note: funding 端点遇 WAF 403 时 bot 以缓存降级运行(对 4h/结算级信号可容忍), 持续断供再排查。"
