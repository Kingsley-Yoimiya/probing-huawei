#!/usr/bin/env bash
# Pillar-C E2：扫常驻 torch rate，注入 onset 附近 SET↑，判采集能否归因。
# 用法：
#   RATES="0 0.05" bash scripts/fail-slow/run_pillar_c_e2.sh          # 先定边界
#   RATES="0 0.001 0.01 0.05" bash scripts/fail-slow/run_pillar_c_e2.sh # 全扫
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/fail-slow/env.sh"

CASE_ID="${CASE_ID:-P3-SW-A}"
DOSE="${DOSE:-loud}"
POD="${POD:-${FS_HOLD_PODS_C:-grj-megatron-32card-0716-worker-0}}"
NPROC="${NPROC:-16}"
RATES="${RATES:-0 0.05}"   # 默认先边界；全扫改 "0 0.001 0.01 0.05"
OUT_FAMILY="${OUT_FAMILY:-pillar_c_v2}"
PILLAR_C_SET_AT_STEP="${PILLAR_C_SET_AT_STEP:-100}"
CASE_SLUG=$(echo "$CASE_ID" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9-')
PARENT_RUN_ID="${PARENT_RUN_ID:-$(date +%Y%m%d_%H%M%S)-pillar-c-e2-${CASE_SLUG}-${DOSE}}"

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
experiment: E2
case_id: ${CASE_ID}
dose: ${DOSE}
parent_run_id: ${PARENT_RUN_ID}
pod: ${POD}
rates: [${RATES// /, }]
set_at_step: ${PILLAR_C_SET_AT_STEP}
out_family: ${OUT_FAMILY}
pod_bundle: ${POD_BUNDLE}
pod_results: ${POD_RESULTS}
cover_target: D4_reuse_B_loud_P3-SW-A
note: "判分=采集内容归因；主尺可记总落盘；禁止只用 cold / 禁止训练 step_ms 判同 D"
YAML

echo "[e2] PARENT=$PARENT_RUN_ID rates=[$RATES] pod=$POD"
echo "[e2] out=${PARENT_LOCAL}"

for rate in $RATES; do
  echo ""
  echo "======== E2 rate=${rate} ========"
  idle_out=$(check_idle) || true
  echo "$idle_out" | tee -a "${PARENT_LOCAL}/logs/idle_${rate}.txt"
  if echo "$idle_out" | grep -q OWNER_BUSY; then
    echo "[e2] YIELD at rate=${rate} — owner training present"
    echo "YIELD rate=${rate}" >>"${PARENT_LOCAL}/YIELD.txt"
    exit 90
  fi

  log="${PARENT_LOCAL}/logs/arm_rate_${rate}.log"
  set +e
  OUT_FAMILY="$OUT_FAMILY" \
  PARENT_RUN_ID="$PARENT_RUN_ID" \
  ARM=e2_rate \
  RESIDENT_RATE="$rate" \
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
  mkdir -p "${PARENT_LOCAL}/rate_${rate}"
  echo "$rc" >"${PARENT_LOCAL}/rate_${rate}/hold_exec.rc"
  if [[ "$rc" -ne 0 ]]; then
    echo "[e2] arm rate=${rate} FAILED rc=$rc"
    echo "FAIL rate=${rate} rc=${rc}" >>"${PARENT_LOCAL}/FAILURES.txt"
  fi
done

echo "[e2] arms done → score"
python3 "${ROOT}/scripts/fail-slow/e2_score_rate.py" \
  --parent-local "$PARENT_LOCAL" \
  --rates $RATES \
  --case "$CASE_ID" \
  --out "${PARENT_LOCAL}/E2_RATE.md"

echo "[e2] DONE parent=$PARENT_RUN_ID"
echo "$PARENT_RUN_ID"
