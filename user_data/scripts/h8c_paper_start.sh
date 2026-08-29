#!/usr/bin/env bash
# H8c 双腿基差套利 paper 模拟器启动器：SHA 校验 + 跑一轮 + cron 提示（FREEZE_H8C.md）。
# 模拟器为一次性进程（非常驻），paper 设备由 cron 每小时调用；本机手动跑只做抽查。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$ROOT/.venv/Scripts/python.exe"
SCRIPT="$ROOT/user_data/scripts/h8c_paper.py"
FROZEN_SHA="eda7a9784c1d7a382040ce7987c7df46b301f5899382e31c8ae16c660247eb35"

# 0. preflight
[ -x "$PY" ] || { echo "python 不存在: $PY"; exit 1; }
[ -f "$SCRIPT" ] || { echo "脚本不存在: $SCRIPT"; exit 1; }

# 1. SHA 校验（FREEZE_H8C.md 冻结，不匹配拒绝运行）
CUR_SHA=$(shasum -a 256 "$SCRIPT" | cut -d' ' -f1)
if [ "$CUR_SHA" != "$FROZEN_SHA" ]; then
    echo "SHA 不匹配，拒绝运行：脚本与 user_data/paper/FREEZE_H8C.md 冻结版本不一致。"
    echo "  期望 $FROZEN_SHA"
    echo "  实际 $CUR_SHA"
    exit 1
fi

# 2. 代理（fapi 需代理；脚本内 setdefault 兜底，这里显式传入）
FT_PROXY="${FT_PROXY:-http://127.0.0.1:7897}"
if [ "${FT_PROXY:-}" != "none" ]; then
    export https_proxy="$FT_PROXY" http_proxy="$FT_PROXY"
fi

# 3. 跑一轮（幂等：重复运行不重复记账）
cd "$ROOT"
mkdir -p user_data/logs
echo "[h8c_paper] $(date '+%F %T') 运行一轮（模拟交易，FREEZE_H8C.md）"
"$PY" user_data/scripts/h8c_paper.py

# 4. cron 提示（本机不装计划任务——2026-08-29 用户指令；paper 设备用）
cat <<'EOF'

[cron 提示] 常驻监控请装在 paper 设备（24h 在线）：
  17 * * * * cd <repo> && .venv/bin/python user_data/scripts/h8c_paper.py >> user_data/logs/h8c_paper.log 2>&1
EOF
