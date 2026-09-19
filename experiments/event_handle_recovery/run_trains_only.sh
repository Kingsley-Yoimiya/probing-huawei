#!/usr/bin/env bash
set -euo pipefail
ROOT="/root/event_handle_recovery_v4"
BUILD_DIR="${ROOT}/build"
PRELOAD="${BUILD_DIR}/libacl_real_dlsym.so:${BUILD_DIR}/libacl_event_trace_v2.so"
V2_SO="${BUILD_DIR}/libacl_event_trace_v2.so"
RUN_ID="${RUN_ID:-20260823T161200Z_d51_event_preload_v4_l}"
WORKDIR="/tmp/${RUN_ID}"
LOGDIR="${WORKDIR}/logs/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "${LOGDIR}"

run_one() {
  local tag="$1" level="$2"
  local wdir="${WORKDIR}/${tag}"
  local ldir="${LOGDIR}/${tag}"
  mkdir -p "${wdir}/event_trace" "${wdir}/out" "${ldir}"
  export ACL_EVENT_TRACE_DIR="${wdir}/event_trace"
  export LD_PRELOAD="${PRELOAD}"
  echo "===== TRAIN ${tag} ${level} ====="
  timeout 1200 torchrun --nproc_per_node=16 --master_port=$((29600 + RANDOM % 500)) \
    "${ROOT}/train_event_preload.py" \
    --output "${wdir}/out" \
    --trace-dir "${wdir}/event_trace" \
    --preload-lib "${V2_SO}" \
    --profiler-level "${level}" 2>&1 | tee "${ldir}/torchrun.log"
  python3 "${ROOT}/analyze_event_pairs.py" \
    --trace-dir "${wdir}/event_trace" \
    --profiler-out "${wdir}/out/args_on" \
    --analysis-dir "${wdir}/analysis" \
    --profile-window "${wdir}/out/args_on/profile_window.json" \
    2>&1 | tee "${ldir}/analysis.log"
  DB="$(find "${wdir}/out/args_on" -name 'ascend_pytorch_profiler*.db' ! -name 'analysis.db' | head -1)"
  python3 "${ROOT}/classify_intervening_tasks.py" \
    --db "${DB}" \
    --analysis-dir "${wdir}/analysis/v4_classify" \
    --profile-window "${wdir}/out/args_on/profile_window.json" \
    2>&1 | tee "${ldir}/classification.log"
}

run_one fresh_level1 Level1
run_one fresh_level2 Level2
echo ALL_DONE
