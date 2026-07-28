#!/usr/bin/env bash
# PR-2 A2 发射 wrapper：nohup 保活 hold_exec（macOS 无 setsid）
set -euo pipefail
ROOT="/Users/yinjinrun/Codespace/myportal/project/probing-huawei"
TS="${RUN_ID_TS:-$(date +%Y%m%d_%H%M%S)}"
PARENT="${PARENT_RUN_ID:-${TS}-pillar-c-v3-pr2-localize-a2}"
LOGDIR="${ROOT}/results/ascend-ais/pillar_c_v3/pr2_localize/_prep/logs"
mkdir -p "$LOGDIR"
LOG="${LOGDIR}/launch_a2_${TS}.log"
PIDFILE="${LOGDIR}/hold_exec_a2_${TS}.pid"

export RUN_ID_TS="$TS"
export PARENT_RUN_ID="$PARENT"

nohup env RUN_ID_TS="$TS" PARENT_RUN_ID="$PARENT" \
  bash "${ROOT}/results/ascend-ais/pillar_c_v3/pr2_localize/_prep/launch_exp_a2.sh" \
  >>"$LOG" 2>&1 &
HPID=$!
disown "$HPID" 2>/dev/null || true
echo "$HPID" >"$PIDFILE"
echo "PARENT=$PARENT HPID=$HPID LOG=$LOG"
