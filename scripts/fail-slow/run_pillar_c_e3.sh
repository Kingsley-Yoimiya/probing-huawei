#!/usr/bin/env bash
# Pillar-C E3：动态臂（rate=0 + SET↑ + W*）vs 全量臂（rate=1.0）总落盘比。
# 用法：
#   bash scripts/fail-slow/run_pillar_c_e3.sh
#   REUSE_FULL=0 bash …   # 强制新跑全量臂
#   SKIP_DYNAMIC=1 …      # 只复用已有动态臂目录
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
PILLAR_C_SET_AT_STEP="${PILLAR_C_SET_AT_STEP:-100}"
REUSE_FULL="${REUSE_FULL:-1}"
SKIP_DYNAMIC="${SKIP_DYNAMIC:-0}"
# 旧 P3-SW-A loud full_fidelity（训/注入配方一致时可复用总字节作上界）
FULL_REF="${FULL_REF:-/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260725_230350-pillar-c-p3-sw-a-loud/full_fidelity}"
CASE_SLUG=$(echo "$CASE_ID" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9-')
PARENT_RUN_ID="${PARENT_RUN_ID:-$(date +%Y%m%d_%H%M%S)-pillar-c-e3-${CASE_SLUG}-${DOSE}}"

export POD_BUNDLE="${POD_BUNDLE:-/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle}"
export POD_RESULTS="/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais"
export LOCAL_RESULT_ROOT_BASE="${LOCAL_RESULT_ROOT_BASE:-${FS_HUAWEI_ROOT}/results/ascend-ais}"
export FS_SHARED_SCRIPTS="${FS_SHARED_SCRIPTS:-/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow}"
export FS_PLATFORM_ASCEND="${FS_PLATFORM_ASCEND:-${FS_SHARED_SCRIPTS}/platform/ascend}"

PARENT_LOCAL="${LOCAL_RESULT_ROOT_BASE}/${OUT_FAMILY}/${PARENT_RUN_ID}"
mkdir -p "$PARENT_LOCAL/logs" "$PARENT_LOCAL/dynamic" "$PARENT_LOCAL/full_fidelity"
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
experiment: E3
case_id: ${CASE_ID}
dose: ${DOSE}
parent_run_id: ${PARENT_RUN_ID}
pod: ${POD}
resident_rate: ${RESIDENT_RATE}
w_star: ${W_STAR}
set_at_step: ${PILLAR_C_SET_AT_STEP}
set_key: probing.torch.profiling=on,rate=1.0
out_family: ${OUT_FAMILY}
pod_bundle: ${POD_BUNDLE}
pod_results: ${POD_RESULTS}
reuse_full: ${REUSE_FULL}
full_ref: ${FULL_REF}
cover_target: D4_reuse_B_loud_P3-SW-A
note: "主尺=总落盘动态/全量；判分=采集归因；禁止只用 cold / 禁止训练 step_ms 并比"
YAML

echo "[e3] PARENT=$PARENT_RUN_ID pod=$POD rate=${RESIDENT_RATE} W*=${W_STAR}"
echo "[e3] out=${PARENT_LOCAL}"

# ---- 动态臂 ----
if [[ "$SKIP_DYNAMIC" != "1" ]]; then
  idle_out=$(check_idle) || true
  echo "$idle_out" | tee "${PARENT_LOCAL}/logs/idle_dynamic.txt"
  if echo "$idle_out" | grep -q OWNER_BUSY; then
    echo "[e3] YIELD — owner training present"
    echo "YIELD dynamic" >"${PARENT_LOCAL}/YIELD.txt"
    exit 90
  fi

  log="${PARENT_LOCAL}/logs/arm_dynamic.log"
  set +e
  OUT_FAMILY="$OUT_FAMILY" \
  PARENT_RUN_ID="$PARENT_RUN_ID" \
  ARM=e2_rate \
  RESIDENT_RATE="$RESIDENT_RATE" \
  ARM_RUN_ID="${PARENT_RUN_ID}-dynamic" \
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
  # run_pillar_c_arm 写到 rate_${RESIDENT_RATE}/；同步到 dynamic/
  SRC_ARM="${PARENT_LOCAL}/rate_${RESIDENT_RATE}"
  if [[ -d "$SRC_ARM" ]]; then
    # 保留 rate_* 目录，并做 dynamic 软链/标记
    ln -sfn "rate_${RESIDENT_RATE}" "${PARENT_LOCAL}/dynamic_link" 2>/dev/null || true
    echo "rate_${RESIDENT_RATE}" >"${PARENT_LOCAL}/dynamic/ARM_DIR.txt"
    echo "$rc" >"${PARENT_LOCAL}/dynamic/hold_exec.rc"
    cp -a "${SRC_ARM}/arm_manifest.yaml" "${PARENT_LOCAL}/dynamic/" 2>/dev/null || true
  else
    echo "$rc" >"${PARENT_LOCAL}/dynamic/hold_exec.rc"
  fi
  if [[ "$rc" -ne 0 ]]; then
    echo "[e3] dynamic FAILED rc=$rc"
    echo "FAIL dynamic rc=${rc}" >>"${PARENT_LOCAL}/FAILURES.txt"
  fi
else
  echo "[e3] SKIP_DYNAMIC=1"
fi

# ---- 全量臂 ----
if [[ "$REUSE_FULL" == "1" ]]; then
  echo "[e3] reuse full_fidelity bytes from ${FULL_REF}"
  cat >"${PARENT_LOCAL}/full_fidelity/REUSE.txt" <<EOF
reuse=1
path=${FULL_REF}
case=${CASE_ID}
note=训/注入配方与 Loud P3-SW-A 一致；SAMPLE_MS=50 rate=1.0 作上界锚点
EOF
  # 从跳板读 du（本机未必挂 AFS）
  set +e
  full_bytes=$(ssh -o ConnectTimeout=30 "$JUMP_HOST" \
    "export KUBECONFIG=${JUMP_KUBECONFIG}; ${JUMP_KUBECTL} -n default exec ${POD} -- du -sb ${FULL_REF}/probing_data" \
    | awk 'NF>=1{print $1; exit}')
  set -e
  full_bytes=$(echo "${full_bytes:-}" | tr -cd '0-9')
  if [[ -z "${full_bytes}" ]]; then
    # 已知 P3-SW-A loud full_fidelity 本轮已测 du（配方一致兜底）
    full_bytes=1791975360
    echo "[e3] WARN: du failed; using cached full_dump_bytes=${full_bytes}" | tee -a "${PARENT_LOCAL}/logs/full_reuse.txt"
  fi
  echo "${full_bytes}" >"${PARENT_LOCAL}/full_fidelity/total_dump_bytes.txt"
  echo "full_dump_bytes=${full_bytes}" | tee -a "${PARENT_LOCAL}/logs/full_reuse.txt"
else
  idle_out=$(check_idle) || true
  echo "$idle_out" | tee "${PARENT_LOCAL}/logs/idle_full.txt"
  if echo "$idle_out" | grep -q OWNER_BUSY; then
    echo "[e3] YIELD before full"
    echo "YIELD full" >"${PARENT_LOCAL}/YIELD.txt"
    exit 90
  fi
  log="${PARENT_LOCAL}/logs/arm_full.log"
  set +e
  OUT_FAMILY="$OUT_FAMILY" \
  PARENT_RUN_ID="$PARENT_RUN_ID" \
  ARM=full_fidelity \
  CASE_ID="$CASE_ID" \
  DOSE="$DOSE" \
  POD="$POD" \
  NPROC="$NPROC" \
  POD_BUNDLE="$POD_BUNDLE" \
  POD_RESULTS="$POD_RESULTS" \
  bash "${ROOT}/scripts/fail-slow/run_pillar_c_arm.sh" 2>&1 | tee "$log"
  rc=${PIPESTATUS[0]}
  set -e
  echo "$rc" >"${PARENT_LOCAL}/full_fidelity/hold_exec.rc"
  if [[ "$rc" -ne 0 ]]; then
    echo "[e3] full FAILED rc=$rc"
    echo "FAIL full rc=${rc}" >>"${PARENT_LOCAL}/FAILURES.txt"
  fi
fi

echo "[e3] score → E3_RATIO.md"
python3 "${ROOT}/scripts/fail-slow/e3_score_ratio.py" \
  --parent-local "$PARENT_LOCAL" \
  --case "$CASE_ID" \
  --w-star "$W_STAR" \
  --resident-rate "$RESIDENT_RATE" \
  --full-ref "$FULL_REF" \
  --out "${PARENT_LOCAL}/E3_RATIO.md"

echo "[e3] DONE parent=$PARENT_RUN_ID"
echo "$PARENT_RUN_ID"
