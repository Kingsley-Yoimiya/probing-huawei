#!/usr/bin/env bash
# Param-Calib ③-B：SET→live tracer 生效延迟（step）+ 升完到够归因；对照对手重启≈150
# 自变量：无（测响应时间）；清晰信号 rate=1.0（不扫 rate）
# 控制：P3-SW-A loud、resident=0、SET@L>=100、窗[100,300]、victim=7、SET 键 probing.torch.profiling=、SET_SCOPE=victim
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/fail-slow/env.sh"

CASE_ID="${CASE_ID:-P3-SW-A}"
DOSE="${DOSE:-loud}"
POD="${POD:-grj-megatron-32card-0716-worker-0}"
NPROC="${NPROC:-16}"
# 清晰信号：升到 1.0（③-A rate*=0.001 已证够 D4；本格测延迟不扫 rate）
SET_RATE="${SET_RATE:-1.0}"
RESIDENT_RATE="${RESIDENT_RATE:-0}"
PILLAR_C_SET_AT_STEP="${PILLAR_C_SET_AT_STEP:-100}"
export PILLAR_C_SET_SCOPE="${PILLAR_C_SET_SCOPE:-victim}"
export PILLAR_C_LATENCY_PROBE=1
export PILLAR_C_W_STAR="${PILLAR_C_W_STAR:-100}"
export PILLAR_C_TT_FLOOR="${PILLAR_C_TT_FLOOR:-800}"
export PILLAR_C_LATENCY_PROBE_MAX_S="${PILLAR_C_LATENCY_PROBE_MAX_S:-600}"
export PROBING_CPU_RING_MB="${PROBING_CPU_RING_MB:-64}"
export INLINE_2C_FALLBACK_S="${INLINE_2C_FALLBACK_S:-0.6}"
export LOCAL_RESULT_ROOT_BASE="/Users/yinjinrun/Codespace/myportal/results/ascend-ais"
export OUT_FAMILY="${OUT_FAMILY:-param_calib/3B_upgrade_latency}"
export POD_RESULTS="${POD_RESULTS:-/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais}"
export POD_BUNDLE="${POD_BUNDLE:-/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle}"
export FS_SHARED_SCRIPTS="${FS_SHARED_SCRIPTS:-/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow}"
export FS_PLATFORM_ASCEND="${FS_PLATFORM_ASCEND:-${FS_SHARED_SCRIPTS}/platform/ascend}"
S1_REF="${S1_REF:-/Users/yinjinrun/Codespace/myportal/project/probing-huawei/results/ascend-ais/pillar_c_v2/S1_MID_ATTACH.md}"
OPPONENT_RESTART_STEPS="${OPPONENT_RESTART_STEPS:-150}"

TS=$(date +%Y%m%d_%H%M%S)
CASE_SLUG=$(echo "$CASE_ID" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9-')
PARENT_RUN_ID="${PARENT_RUN_ID:-${TS}-3b-${CASE_SLUG}-${DOSE}}"
PARENT_LOCAL="${LOCAL_RESULT_ROOT_BASE}/${OUT_FAMILY}/${PARENT_RUN_ID}"
mkdir -p "$PARENT_LOCAL/logs"

JUMP_HOST="${JUMP_HOST:-ais-cf3e61a5}"
JUMP_KUBECTL="${JUMP_KUBECTL:-/root/.cache/volcano/kubectl/kubectl}"
JUMP_KUBECONFIG="${JUMP_KUBECONFIG:-/tmp/config-vc-a3-241ceshi-songyiyang.yaml}"

check_idle() {
  ssh -o ConnectTimeout=20 "$JUMP_HOST" \
    "export KUBECONFIG=${JUMP_KUBECONFIG}; ${JUMP_KUBECTL} -n default exec ${POD} -- bash -lc '
      busy=\$(ps -eo pid,cmd | awk \"/torchrun|megatron|pretrain_gpt|train_bench_probe/ && !/awk|bash -lc|pgrep|defunct|tar czf/ {print}\")
      if [[ -n \"\$busy\" ]]; then echo OWNER_BUSY; echo \"\$busy\"; exit 90; fi
      echo IDLE
    '"
}

cat >"${PARENT_LOCAL}/manifest.yaml" <<YAML
experiment: 3B_upgrade_latency
case_id: ${CASE_ID}
dose: ${DOSE}
parent_run_id: ${PARENT_RUN_ID}
pod: ${POD}
resident_rate: ${RESIDENT_RATE}
set_rate: ${SET_RATE}
set_at_step: ${PILLAR_C_SET_AT_STEP}
set_key: probing.torch.profiling=
set_scope: ${PILLAR_C_SET_SCOPE}
latency_probe: 1
w_star: ${PILLAR_C_W_STAR}
tt_floor: ${PILLAR_C_TT_FLOOR}
opponent_restart_steps: ${OPPONENT_RESTART_STEPS}
s1_ref: ${S1_REF}
note: "自变量=无（测 SET→live / live→够归因 step）；清晰信号 rate=${SET_RATE}；禁止 step_ms / 只报 cold"
YAML

echo "$PARENT_RUN_ID" >"${PARENT_LOCAL}/PARENT_RUN_ID.txt"
echo "[3b] PARENT=$PARENT_RUN_ID rate=${SET_RATE} SET_AT=$PILLAR_C_SET_AT_STEP probe=1"
echo "[3b] out=${PARENT_LOCAL}"

idle_out=$(check_idle) || true
echo "$idle_out" | tee "${PARENT_LOCAL}/logs/idle_before.txt"
if echo "$idle_out" | grep -q OWNER_BUSY; then
  echo "[3b] YIELD — owner training present"
  echo "YIELD" >"${PARENT_LOCAL}/YIELD.txt"
  exit 90
fi

log="${PARENT_LOCAL}/logs/arm_upgrade_latency.log"
set +e
OUT_FAMILY="$OUT_FAMILY" \
PARENT_RUN_ID="$PARENT_RUN_ID" \
ARM=e3a_upgrade \
RESIDENT_RATE="$RESIDENT_RATE" \
PILLAR_C_SET_RATE="$SET_RATE" \
PILLAR_C_SET_AT_STEP="$PILLAR_C_SET_AT_STEP" \
PILLAR_C_SET_SCOPE="$PILLAR_C_SET_SCOPE" \
PILLAR_C_LATENCY_PROBE=1 \
PILLAR_C_W_STAR="$PILLAR_C_W_STAR" \
PILLAR_C_TT_FLOOR="$PILLAR_C_TT_FLOOR" \
PILLAR_C_LATENCY_PROBE_MAX_S="$PILLAR_C_LATENCY_PROBE_MAX_S" \
ARM_RUN_ID="${PARENT_RUN_ID}-upgrade_rate_${SET_RATE}" \
CASE_ID="$CASE_ID" \
DOSE="$DOSE" \
POD="$POD" \
NPROC="$NPROC" \
POD_BUNDLE="$POD_BUNDLE" \
POD_RESULTS="$POD_RESULTS" \
PROBING_CPU_RING_MB="$PROBING_CPU_RING_MB" \
LOCAL_RESULT_ROOT_BASE="$LOCAL_RESULT_ROOT_BASE" \
bash "${ROOT}/scripts/fail-slow/run_pillar_c_arm.sh" 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}
set -e
mkdir -p "${PARENT_LOCAL}/upgrade_rate_${SET_RATE}"
echo "$rc" >"${PARENT_LOCAL}/upgrade_rate_${SET_RATE}/hold_exec.rc"

idle_out=$(check_idle) || true
echo "$idle_out" | tee "${PARENT_LOCAL}/logs/idle_after.txt"
if echo "$idle_out" | grep -q OWNER_BUSY; then
  echo "[3b] YIELD after run — owner returned"
  echo "YIELD after" >"${PARENT_LOCAL}/YIELD.txt"
fi

echo "[3b] score → PARAM.md"
python3 "${ROOT}/scripts/fail-slow/param_calib/3b_upgrade_latency.py" \
  --parent-local "$PARENT_LOCAL" \
  --set-rate "$SET_RATE" \
  --case "$CASE_ID" \
  --w-star "$PILLAR_C_W_STAR" \
  --tt-floor "$PILLAR_C_TT_FLOOR" \
  --opponent-restart-steps "$OPPONENT_RESTART_STEPS" \
  --s1-ref "$S1_REF" \
  --out-dir "${LOCAL_RESULT_ROOT_BASE}/${OUT_FAMILY}"

echo "[3b] DONE parent=$PARENT_RUN_ID rc=$rc"
echo "$PARENT_RUN_ID"
exit "$rc"
