#!/usr/bin/env bash
# Pillar-C E4：朴素砍量反例（E3 动态臂去掉触发升详）。
# 只新跑 naive 一臂；正例对照复用 E3 动态臂产物。
# 用法：
#   bash scripts/fail-slow/run_pillar_c_e4.sh
#   E3_REF=…/20260726_181423-pillar-c-e3-p3-sw-a-loud bash …
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
W_STAR="${W_STAR:-100}"
E3_REF="${E3_REF:-${FS_HUAWEI_ROOT}/results/ascend-ais/pillar_c_v2/20260726_181423-pillar-c-e3-p3-sw-a-loud}"
CASE_SLUG=$(echo "$CASE_ID" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9-')
PARENT_RUN_ID="${PARENT_RUN_ID:-$(date +%Y%m%d_%H%M%S)-pillar-c-e4-${CASE_SLUG}-${DOSE}}"

export POD_BUNDLE="${POD_BUNDLE:-/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle}"
export POD_RESULTS="/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais"
export LOCAL_RESULT_ROOT_BASE="${LOCAL_RESULT_ROOT_BASE:-${FS_HUAWEI_ROOT}/results/ascend-ais}"
export FS_SHARED_SCRIPTS="${FS_SHARED_SCRIPTS:-/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow}"
export FS_PLATFORM_ASCEND="${FS_PLATFORM_ASCEND:-${FS_SHARED_SCRIPTS}/platform/ascend}"

PARENT_LOCAL="${LOCAL_RESULT_ROOT_BASE}/${OUT_FAMILY}/${PARENT_RUN_ID}"
mkdir -p "$PARENT_LOCAL/logs" "$PARENT_LOCAL/naive_cut" "$PARENT_LOCAL/e3_positive"
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
experiment: E4
case_id: ${CASE_ID}
dose: ${DOSE}
parent_run_id: ${PARENT_RUN_ID}
pod: ${POD}
resident_rate: ${RESIDENT_RATE}
w_star: ${W_STAR}
pillar_c_set_upgrade: 0
e3_positive_ref: ${E3_REF}
out_family: ${OUT_FAMILY}
pod_bundle: ${POD_BUNDLE}
pod_results: ${POD_RESULTS}
cover_target: D4_reuse_B_loud_P3-SW-A
note: "砍量臂=E3动态去触发升详；判分=采集归因掉级；禁止只用cold/禁止训练step_ms"
YAML

cat >"${PARENT_LOCAL}/e3_positive/REUSE.txt" <<EOF
reuse=1
path=${E3_REF}
arm=rate_${RESIDENT_RATE}
note=E3 动态臂正例（rate=0 + SET↑ + RSS ok）；本轮不重跑
EOF

echo "[e4] PARENT=$PARENT_RUN_ID pod=$POD rate=${RESIDENT_RATE} SET↑=0"
echo "[e4] e3_ref=${E3_REF}"
echo "[e4] out=${PARENT_LOCAL}"

idle_out=$(check_idle) || true
echo "$idle_out" | tee "${PARENT_LOCAL}/logs/idle_naive.txt"
if echo "$idle_out" | grep -q OWNER_BUSY; then
  echo "[e4] YIELD — owner training present"
  echo "YIELD naive" >"${PARENT_LOCAL}/YIELD.txt"
  exit 90
fi

log="${PARENT_LOCAL}/logs/arm_naive_cut.log"
set +e
OUT_FAMILY="$OUT_FAMILY" \
PARENT_RUN_ID="$PARENT_RUN_ID" \
ARM=e4_naive \
RESIDENT_RATE="$RESIDENT_RATE" \
ARM_RUN_ID="${PARENT_RUN_ID}-naive_cut" \
CASE_ID="$CASE_ID" \
DOSE="$DOSE" \
POD="$POD" \
NPROC="$NPROC" \
POD_BUNDLE="$POD_BUNDLE" \
POD_RESULTS="$POD_RESULTS" \
bash "${ROOT}/scripts/fail-slow/run_pillar_c_arm.sh" 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}
set -e
echo "$rc" >"${PARENT_LOCAL}/naive_cut/hold_exec.rc"
if [[ "$rc" -ne 0 ]]; then
  echo "[e4] naive FAILED rc=$rc"
  echo "FAIL naive rc=${rc}" >>"${PARENT_LOCAL}/FAILURES.txt"
fi

echo "[e4] score → E4_ABLATION.md"
python3 "${ROOT}/scripts/fail-slow/e4_score_ablation.py" \
  --parent-local "$PARENT_LOCAL" \
  --e3-ref "$E3_REF" \
  --case "$CASE_ID" \
  --resident-rate "$RESIDENT_RATE" \
  --w-star "$W_STAR" \
  --out "${PARENT_LOCAL}/E4_ABLATION.md"

echo "[e4] DONE parent=$PARENT_RUN_ID"
echo "$PARENT_RUN_ID"
