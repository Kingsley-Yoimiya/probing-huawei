#!/usr/bin/env bash
set -euo pipefail
ROOT="/Users/yinjinrun/Codespace/myportal/project/probing-huawei"
TS="${RUN_ID_TS:-$(date +%Y%m%d_%H%M%S)}"
PARENT="${PARENT_RUN_ID:-${TS}-pillar-c-v3-pr2-localize-a4}"
LOGDIR="${ROOT}/results/ascend-ais/pillar_c_v3/pr2_localize/_prep/logs"
mkdir -p "$LOGDIR"
LOG="${LOGDIR}/launch_a4_${TS}.log"
export RUN_ID_TS="$TS"
export PARENT_RUN_ID="$PARENT"
echo "PARENT=$PARENT LOG=$LOG"
bash "${ROOT}/results/ascend-ais/pillar_c_v3/pr2_localize/_prep/launch_exp_a4.sh" 2>&1 | tee "$LOG"
