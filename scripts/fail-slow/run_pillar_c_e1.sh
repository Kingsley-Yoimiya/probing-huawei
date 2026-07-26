#!/usr/bin/env bash
# Pillar-C E1 正式：极稀常驻 + onset SET↑，dump 后按步截窗判归因（offline truncate）。
# 用法：
#   CASE_ID=P1-SW-C POD=grj-megatron-32card-0716-master-0 \
#     bash scripts/fail-slow/run_pillar_c_e1.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/fail-slow/env.sh"

CASE_ID="${CASE_ID:-P1-SW-C}"
DOSE="${DOSE:-loud}"
POD="${POD:-grj-megatron-32card-0716-master-0}"
NPROC="${NPROC:-16}"
RESIDENT_RATE="${RESIDENT_RATE:-0}"
OUT_FAMILY="${OUT_FAMILY:-pillar_c_v2}"
PILLAR_C_SET_AT_STEP="${PILLAR_C_SET_AT_STEP:-100}"
WINDOWS="${WINDOWS:-50 100 200}"
CASE_SLUG=$(echo "$CASE_ID" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9-')
PARENT_RUN_ID="${PARENT_RUN_ID:-$(date +%Y%m%d_%H%M%S)-pillar-c-e1-${CASE_SLUG}-${DOSE}}"

export POD_BUNDLE="${POD_BUNDLE:-/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle}"
export POD_RESULTS="/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais"
export LOCAL_RESULT_ROOT_BASE="${LOCAL_RESULT_ROOT_BASE:-${FS_HUAWEI_ROOT}/results/ascend-ais}"
export FS_SHARED_SCRIPTS="${FS_SHARED_SCRIPTS:-/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow}"
export FS_PLATFORM_ASCEND="${FS_PLATFORM_ASCEND:-${FS_SHARED_SCRIPTS}/platform/ascend}"

PARENT_LOCAL="${LOCAL_RESULT_ROOT_BASE}/${OUT_FAMILY}/${PARENT_RUN_ID}"
mkdir -p "$PARENT_LOCAL/logs"
echo "$PARENT_RUN_ID" >"${PARENT_LOCAL}/PARENT_RUN_ID.txt"

JUMP_KUBECTL="${JUMP_KUBECTL:-/root/.cache/volcano/kubectl/kubectl}"
JUMP_KUBECONFIG="${JUMP_KUBECONFIG:-/tmp/config-vc-a3-241ceshi-songyiyang.yaml}"
JUMP_HOST="${JUMP_HOST:-ais-cf3e61a5}"

check_idle() {
  ssh -o ConnectTimeout=20 "$JUMP_HOST" \
    "export KUBECONFIG=${JUMP_KUBECONFIG}; ${JUMP_KUBECTL} -n default exec ${POD} -- bash -lc '
      busy=\$(ps -eo pid,cmd | awk \"/torchrun|megatron|pretrain_gpt|train_bench_probe/ && !/awk|bash -lc|pgrep|defunct|tar czf/ {print}\")
      if [[ -n \"\$busy\" ]]; then echo OWNER_BUSY; echo \"\$busy\"; exit 90; fi
      echo IDLE
    '"
}

cat >"${PARENT_LOCAL}/manifest.yaml" <<YAML
experiment: E1
case_id: ${CASE_ID}
dose: ${DOSE}
parent_run_id: ${PARENT_RUN_ID}
pod: ${POD}
resident_rate: ${RESIDENT_RATE}
set_at_step: ${PILLAR_C_SET_AT_STEP}
windows: [${WINDOWS// /, }]
window_mode: offline_truncate
out_family: ${OUT_FAMILY}
pod_bundle: ${POD_BUNDLE}
pod_results: ${POD_RESULTS}
note: "在线极稀常驻+SET↑；尚无 online retention API → dump 后按步截窗（与 E1-off 同尺）；判分=duration 尖刺；禁 cold/step_ms"
YAML

echo "[e1] PARENT=$PARENT_RUN_ID case=$CASE_ID rate=${RESIDENT_RATE} pod=$POD"
echo "[e1] out=${PARENT_LOCAL}"

idle_out=$(check_idle) || true
echo "$idle_out" | tee "${PARENT_LOCAL}/logs/idle.txt"
if echo "$idle_out" | grep -q OWNER_BUSY; then
  echo "[e1] YIELD — owner training present on $POD"
  echo "YIELD" >"${PARENT_LOCAL}/YIELD.txt"
  exit 90
fi

log="${PARENT_LOCAL}/logs/arm_rate_${RESIDENT_RATE}.log"
set +e
OUT_FAMILY="$OUT_FAMILY" \
PARENT_RUN_ID="$PARENT_RUN_ID" \
ARM=e2_rate \
RESIDENT_RATE="$RESIDENT_RATE" \
CASE_ID="$CASE_ID" \
DOSE="$DOSE" \
POD="$POD" \
NPROC="$NPROC" \
PILLAR_C_SET_AT_STEP="$PILLAR_C_SET_AT_STEP" \
POD_BUNDLE="$POD_BUNDLE" \
POD_RESULTS="$POD_RESULTS" \
bash "${ROOT}/scripts/fail-slow/run_pillar_c_arm.sh" 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}
set -e
mkdir -p "${PARENT_LOCAL}/rate_${RESIDENT_RATE}"
echo "$rc" >"${PARENT_LOCAL}/rate_${RESIDENT_RATE}/hold_exec.rc"
if [[ "$rc" -ne 0 ]]; then
  echo "[e1] arm FAILED rc=$rc"
  echo "FAIL rc=${rc}" >>"${PARENT_LOCAL}/FAILURES.txt"
  exit "$rc"
fi

echo "[e1] arm done → score windows [$WINDOWS]"
python3 "${ROOT}/scripts/fail-slow/e1_score_window.py" \
  --parent-local "$PARENT_LOCAL" \
  --arm-dir "rate_${RESIDENT_RATE}" \
  --case "$CASE_ID" \
  --windows $WINDOWS \
  --out "${PARENT_LOCAL}/E1_WINDOW.md"

echo "[e1] DONE parent=$PARENT_RUN_ID"
echo "$PARENT_RUN_ID"
