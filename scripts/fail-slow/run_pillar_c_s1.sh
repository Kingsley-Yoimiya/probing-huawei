#!/usr/bin/env bash
# Pillar-C S1：中途接入回溯（outline 场景一）。
# 起训不挂 probing → step>=ATTACH_AT 才 site_hook；attach 在 inject onset 之后。
# 用法：
#   bash scripts/fail-slow/run_pillar_c_s1.sh
#   PROBING_ATTACH_AT_STEP=150 bash …
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/fail-slow/env.sh"

CASE_ID="${CASE_ID:-P3-SW-A}"
DOSE="${DOSE:-loud}"
POD="${POD:-${FS_HOLD_PODS_C:-grj-megatron-32card-0716-worker-0}}"
NPROC="${NPROC:-16}"
OUT_FAMILY="${OUT_FAMILY:-pillar_c_v2}"
RESIDENT_RATE="${RESIDENT_RATE:-0}"
ATTACH_AT="${PROBING_ATTACH_AT_STEP:-150}"
INJECT_START="${INJECT_START:-100}"
INJECT_STOP="${INJECT_STOP:-300}"
W_RING_STEPS="${W_RING_STEPS:-546}"  # E1-off 标定 20MB≈546 步
CASE_SLUG=$(echo "$CASE_ID" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9-')
PARENT_RUN_ID="${PARENT_RUN_ID:-$(date +%Y%m%d_%H%M%S)-pillar-c-s1-${CASE_SLUG}-${DOSE}}"

export POD_BUNDLE="${POD_BUNDLE:-/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle}"
export POD_RESULTS="/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais"
export LOCAL_RESULT_ROOT_BASE="${LOCAL_RESULT_ROOT_BASE:-${FS_HUAWEI_ROOT}/results/ascend-ais}"
export FS_SHARED_SCRIPTS="${FS_SHARED_SCRIPTS:-/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow}"
export FS_PLATFORM_ASCEND="${FS_PLATFORM_ASCEND:-${FS_SHARED_SCRIPTS}/platform/ascend}"

PARENT_LOCAL="${LOCAL_RESULT_ROOT_BASE}/${OUT_FAMILY}/${PARENT_RUN_ID}"
mkdir -p "$PARENT_LOCAL/logs" "$PARENT_LOCAL/mid_attach"
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
experiment: S1
case_id: ${CASE_ID}
dose: ${DOSE}
parent_run_id: ${PARENT_RUN_ID}
pod: ${POD}
resident_rate: ${RESIDENT_RATE}
attach_at_step: ${ATTACH_AT}
inject_window: [${INJECT_START}, ${INJECT_STOP}]
ring_calibrated_steps: ${W_RING_STEPS}
pillar_c_set_upgrade: 1
out_family: ${OUT_FAMILY}
pod_bundle: ${POD_BUNDLE}
pod_results: ${POD_RESULTS}
cover_target: D4_reuse_B_loud_P3-SW-A
note: "中途接入回溯；尺=RSS/冷段时间覆盖；禁止只报cold/禁止训练step_ms；Ascend用延迟site_hook（无libprobing.so）"
YAML

echo "[s1] PARENT=$PARENT_RUN_ID pod=$POD attach_at=${ATTACH_AT} inject=[${INJECT_START},${INJECT_STOP}]"
echo "[s1] out=${PARENT_LOCAL}"

idle_out=$(check_idle) || true
echo "$idle_out" | tee "${PARENT_LOCAL}/logs/idle.txt"
if echo "$idle_out" | grep -q OWNER_BUSY; then
  echo "[s1] YIELD — owner training present"
  echo "YIELD" >"${PARENT_LOCAL}/YIELD.txt"
  exit 90
fi

log="${PARENT_LOCAL}/logs/arm_mid_attach.log"
set +e
OUT_FAMILY="$OUT_FAMILY" \
PARENT_RUN_ID="$PARENT_RUN_ID" \
ARM=s1_mid_attach \
RESIDENT_RATE="$RESIDENT_RATE" \
PROBING_ATTACH_AT_STEP="$ATTACH_AT" \
ARM_RUN_ID="${PARENT_RUN_ID}-mid_attach" \
CASE_ID="$CASE_ID" \
DOSE="$DOSE" \
POD="$POD" \
NPROC="$NPROC" \
POD_BUNDLE="$POD_BUNDLE" \
POD_RESULTS="$POD_RESULTS" \
bash "${ROOT}/scripts/fail-slow/run_pillar_c_arm.sh" 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}
set -e
echo "$rc" >"${PARENT_LOCAL}/mid_attach/hold_exec.rc"
if [[ "$rc" -ne 0 ]]; then
  echo "[s1] mid_attach FAILED rc=$rc"
  echo "FAIL mid_attach rc=${rc}" >>"${PARENT_LOCAL}/FAILURES.txt"
fi

echo "[s1] score → S1_MID_ATTACH.md"
python3 "${ROOT}/scripts/fail-slow/s1_score_mid_attach.py" \
  --parent-local "$PARENT_LOCAL" \
  --case "$CASE_ID" \
  --attach-at "$ATTACH_AT" \
  --inject-start "$INJECT_START" \
  --inject-stop "$INJECT_STOP" \
  --ring-steps "$W_RING_STEPS" \
  --out "${PARENT_LOCAL}/S1_MID_ATTACH.md"

echo "[s1] DONE parent=$PARENT_RUN_ID"
echo "$PARENT_RUN_ID"
