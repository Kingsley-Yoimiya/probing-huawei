#!/usr/bin/env bash
# D51 Wait DAG V1 Slice A on frozen V2b. Read-only; does not stop vLLM.
set -euo pipefail

UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_wait_dag_v1_slice_a_fix}"
V2B_ROOT="${V2B_ROOT:-/tmp/20260823T145208Z_d51_event_preload_v2b_analyze}"
TRACE_DIR="${TRACE_DIR:-${V2B_ROOT}/event_trace}"
DB_PATH="${DB_PATH:-${V2B_ROOT}/profiler/ascend_pytorch_profiler_0.db}"
OUT_ROOT="${OUT_ROOT:-/tmp/${RUN_ID}}"
ANALYSIS_DIR="${OUT_ROOT}/analysis"
LOG_ROOT="${OUT_ROOT}/logs/${UTC_STAMP}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MYPORTAL_BASE="${MYPORTAL_BASE:-/workspace/myportal/results/npu-dev-1}"
EXPECTED_DB_SHA256="7d57eb2a62ed79ba35706f40e29622ebe723f99697c5c5d524c685c64220c6fa"

mkdir -p "${ANALYSIS_DIR}" "${LOG_ROOT}"

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "${LOG_ROOT}/preflight.log"; }

log "RUN_ID=${RUN_ID}"
log "Slice A expected wall time: 10-25 min (usually <5 min analysis only)"
log "no training, no fresh profiler capture, no delay"
log "vLLM and montyyin_reduce_ws16 must remain running"
log "DB=${DB_PATH}"
log "TRACE_DIR=${TRACE_DIR}"
log "fixed denominators: Record=51 Wait=24 AllReduce=12"
log "rank0 pid=26025 active_window frozen per V6 summary"

if [[ ! -f "${DB_PATH}" ]]; then
  log "FATAL: DB missing at ${DB_PATH}"
  exit 2
fi

sha256sum "${DB_PATH}" | tee "${LOG_ROOT}/hashes.log"
DB_HASH="$(sha256sum "${DB_PATH}" | awk '{print $1}')"
if [[ "${DB_HASH}" != "${EXPECTED_DB_SHA256}" ]]; then
  log "FATAL: DB SHA256 mismatch: ${DB_HASH}"
  exit 2
fi

BIN_N=$(ls "${TRACE_DIR}"/rank_*_pid_*.events.bin 2>/dev/null | wc -l | tr -d ' ')
log "bin_count=${BIN_N} (expect 16, pid 26025-26040)"
ls -la "${TRACE_DIR}"/rank_*_pid_*.events.bin | tee -a "${LOG_ROOT}/hashes.log"

PW=""
if [[ -f "${V2B_ROOT}/profiler/profile_window.json" ]]; then
  PW="--profile-window ${V2B_ROOT}/profiler/profile_window.json"
elif [[ -f "${V2B_ROOT}/profile_window.json" ]]; then
  PW="--profile-window ${V2B_ROOT}/profile_window.json"
fi

log "recording analyzer paths..."
ls -la "${SCRIPT_DIR}"/wait_dag_*.py "${SCRIPT_DIR}"/tests/test_wait_dag_v1.py | tee -a "${LOG_ROOT}/hashes.log"
sha256sum "${SCRIPT_DIR}"/wait_dag_*.py "${SCRIPT_DIR}"/tests/test_wait_dag_v1.py | tee -a "${LOG_ROOT}/hashes.log"

log "unit tests..."
cd "${SCRIPT_DIR}"
python3 tests/test_wait_dag_v1.py -v 2>&1 | tee "${LOG_ROOT}/unit.log"

log "wait DAG analysis..."
python3 wait_dag_build.py \
  --run-id "${RUN_ID}" \
  --trace-dir "${TRACE_DIR}" \
  --db-path "${DB_PATH}" \
  --analysis-dir "${ANALYSIS_DIR}" \
  --log-dir "${LOG_ROOT}" \
  --pid-min 26025 \
  --pid-max 26040 \
  ${PW} \
  2>&1 | tee "${LOG_ROOT}/analysis.log"

ANALYSIS_EXIT=$?

sha256sum "${DB_PATH}" | tee -a "${LOG_ROOT}/hashes.log"
DB_HASH_AFTER="$(sha256sum "${DB_PATH}" | awk '{print $1}')"
if [[ "${DB_HASH_AFTER}" != "${EXPECTED_DB_SHA256}" ]]; then
  log "FATAL: DB SHA256 changed after analysis"
  ANALYSIS_EXIT=2
fi

if [[ -f "${ANALYSIS_DIR}/acceptance.json" ]]; then
  python3 -c "
import json,sys
a=json.load(open('${ANALYSIS_DIR}/acceptance.json'))
print('acceptance_passed=', a.get('passed'))
print('unknown_by_reason=', a.get('unknown_by_reason'))
" | tee "${LOG_ROOT}/acceptance.log"
fi

DEST="${MYPORTAL_BASE}/${RUN_ID}"
mkdir -p "${DEST}/analysis" "${DEST}/logs"
cp -a "${ANALYSIS_DIR}/." "${DEST}/analysis/"
cp -a "${LOG_ROOT}/." "${DEST}/logs/"
log "evidence copied to ${DEST}" | tee "${LOG_ROOT}/transfer.log"

log "done exit=${ANALYSIS_EXIT}"
exit "${ANALYSIS_EXIT}"
