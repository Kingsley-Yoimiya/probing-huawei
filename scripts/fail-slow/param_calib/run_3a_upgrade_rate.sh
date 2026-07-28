#!/usr/bin/env bash
# Param-Calib ③-A：触发后升采样率 rate 扫 → D-level vs rate；定够 D4 的最小 rate*
# 自变量：PILLAR_C_SET_RATE ∈ {0.001,0.05,0.5,1.0}；rate≈0 端点挂 E4（不重跑）
# 控制：P3-SW-A loud、resident rate=0、SET@L>=100、窗[100,300]、victim=7、SET 键 probing.torch.profiling=
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/fail-slow/env.sh"

CASE_ID="${CASE_ID:-P3-SW-A}"
DOSE="${DOSE:-loud}"
POD="${POD:-yysong-worker-0}"
NPROC="${NPROC:-16}"
RATES="${RATES:-0.001 0.05 0.5 1.0}"
RESIDENT_RATE="${RESIDENT_RATE:-0}"
PILLAR_C_SET_AT_STEP="${PILLAR_C_SET_AT_STEP:-100}"
export PILLAR_C_SET_SCOPE="${PILLAR_C_SET_SCOPE:-victim}"
export PROBING_CPU_RING_MB="${PROBING_CPU_RING_MB:-64}"
export INLINE_2C_FALLBACK_S="${INLINE_2C_FALLBACK_S:-0.6}"
export LOCAL_RESULT_ROOT_BASE="/Users/yinjinrun/Codespace/myportal/results/ascend-ais"
export OUT_FAMILY="${OUT_FAMILY:-param_calib/3A_upgrade_rate}"
export POD_RESULTS="${POD_RESULTS:-/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais}"
export POD_BUNDLE="${POD_BUNDLE:-/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle}"
export FS_SHARED_SCRIPTS="${FS_SHARED_SCRIPTS:-/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow}"
export FS_PLATFORM_ASCEND="${FS_PLATFORM_ASCEND:-${FS_SHARED_SCRIPTS}/platform/ascend}"
E4_REF="${E4_REF:-/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c_v2/20260726_182630-pillar-c-e4-p3-sw-a-loud}"
E3_REF="${E3_REF:-/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c_v2/20260726_181423-pillar-c-e3-p3-sw-a-loud}"
E4_JSON="${E4_JSON:-${LOCAL_RESULT_ROOT_BASE}/${OUT_FAMILY}/_e4_hung/E4_ABLATION.json}"

TS=$(date +%Y%m%d_%H%M%S)
CASE_SLUG=$(echo "$CASE_ID" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9-')
PARENT_RUN_ID="${PARENT_RUN_ID:-${TS}-3a-${CASE_SLUG}-${DOSE}}"
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
experiment: 3A_upgrade_rate
case_id: ${CASE_ID}
dose: ${DOSE}
parent_run_id: ${PARENT_RUN_ID}
pod: ${POD}
resident_rate: ${RESIDENT_RATE}
set_at_step: ${PILLAR_C_SET_AT_STEP}
set_key: probing.torch.profiling=
swept_upgrade_rates: [${RATES// /, }]
e4_rate0_anchor: ${E4_REF}
e3_rate1_ref: ${E3_REF}
w_star_design: 100
note: "自变量=触发后升到的 rate；禁止 step_ms；只报 cold 冒充；rate=0 挂 E4"
YAML

echo "$PARENT_RUN_ID" >"${PARENT_LOCAL}/PARENT_RUN_ID.txt"
echo "[3a] PARENT=$PARENT_RUN_ID rates=[$RATES] resident=$RESIDENT_RATE SET_AT=$PILLAR_C_SET_AT_STEP"
echo "[3a] out=${PARENT_LOCAL}"

for rate in $RATES; do
  idle_out=$(check_idle) || true
  echo "$idle_out" | tee "${PARENT_LOCAL}/logs/idle_before_rate_${rate}.txt"
  if echo "$idle_out" | grep -q OWNER_BUSY; then
    echo "[3a] YIELD — owner training present before rate=$rate"
    echo "YIELD rate=${rate}" >"${PARENT_LOCAL}/YIELD.txt"
    exit 90
  fi

  log="${PARENT_LOCAL}/logs/arm_upgrade_rate_${rate}.log"
  echo "[3a] >>> upgrade_rate=${rate}"
  set +e
  OUT_FAMILY="$OUT_FAMILY" \
  PARENT_RUN_ID="$PARENT_RUN_ID" \
  ARM=e3a_upgrade \
  RESIDENT_RATE="$RESIDENT_RATE" \
  PILLAR_C_SET_RATE="$rate" \
  PILLAR_C_SET_AT_STEP="$PILLAR_C_SET_AT_STEP" \
  PILLAR_C_SET_SCOPE="$PILLAR_C_SET_SCOPE" \
  ARM_RUN_ID="${PARENT_RUN_ID}-upgrade_rate_${rate}" \
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
  echo "$rc" >"${PARENT_LOCAL}/upgrade_rate_${rate}/hold_exec.rc"
  if [[ "$rc" -ne 0 ]]; then
    echo "[3a] FAIL rate=${rate} rc=${rc}" | tee -a "${PARENT_LOCAL}/FAILURES.txt"
  fi

  # 臂间再确认无对方训练
  idle_out=$(check_idle) || true
  echo "$idle_out" | tee "${PARENT_LOCAL}/logs/idle_after_rate_${rate}.txt"
  if echo "$idle_out" | grep -q OWNER_BUSY; then
    echo "[3a] YIELD after rate=$rate — stop remaining"
    echo "YIELD after rate=${rate}" >"${PARENT_LOCAL}/YIELD.txt"
    exit 90
  fi
done

echo "[3a] score → PARAM.md"
python3 "${ROOT}/scripts/fail-slow/param_calib/3a_upgrade_rate.py" \
  --parent-local "$PARENT_LOCAL" \
  --rates $RATES \
  --case "$CASE_ID" \
  --e4-ref "$E4_REF" \
  --e3-ref "$E3_REF" \
  --e4-json "$E4_JSON" \
  --out-dir "${LOCAL_RESULT_ROOT_BASE}/${OUT_FAMILY}"

echo "[3a] DONE parent=$PARENT_RUN_ID"
echo "$PARENT_RUN_ID"
