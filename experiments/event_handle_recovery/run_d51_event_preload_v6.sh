#!/usr/bin/env bash
# D51 V6 A6 recompute on frozen V2b DB + bins. Read-only; does not stop vLLM.
set -euo pipefail

UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_event_preload_v6_a6_recompute}"
V2B_ROOT="${V2B_ROOT:-/tmp/20260823T145208Z_d51_event_preload_v2b_analyze}"
TRACE_DIR="${TRACE_DIR:-${V2B_ROOT}/event_trace}"
DB_PATH="${DB_PATH:-${V2B_ROOT}/profiler/ascend_pytorch_profiler_0.db}"
OUT_ROOT="${OUT_ROOT:-/tmp/${RUN_ID}}"
ANALYSIS_DIR="${OUT_ROOT}/analysis"
LOG_ROOT="${OUT_ROOT}/logs/${UTC_STAMP}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MYPORTAL_BASE="${MYPORTAL_BASE:-/workspace/myportal/results/npu-dev-1}"

mkdir -p "${ANALYSIS_DIR}" "${LOG_ROOT}"

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "${LOG_ROOT}/preflight.log"; }

log "RUN_ID=${RUN_ID}"
log "expected wall time: 5-15 min (usually <3 min for recompute only)"
log "DB=${DB_PATH}"
log "TRACE_DIR=${TRACE_DIR}"

if [[ ! -f "${DB_PATH}" ]]; then
  log "FATAL: DB missing at ${DB_PATH}"
  exit 2
fi

sha256sum "${DB_PATH}" | tee "${LOG_ROOT}/hashes.log"
BIN_N=$(ls "${TRACE_DIR}"/rank_*_pid_*.events.bin 2>/dev/null | wc -l | tr -d ' ')
log "bin_count=${BIN_N} (expect 16)"
if [[ "${BIN_N}" -lt 16 ]]; then
  log "FATAL: expected 16 pid bins 26025-26040"
  exit 2
fi
ls -la "${TRACE_DIR}"/rank_*_pid_*.events.bin | tee -a "${LOG_ROOT}/hashes.log"

PW=""
if [[ -f "${V2B_ROOT}/profiler/profile_window.json" ]]; then
  PW="--profile-window ${V2B_ROOT}/profiler/profile_window.json"
elif [[ -f "${V2B_ROOT}/profile_window.json" ]]; then
  PW="--profile-window ${V2B_ROOT}/profile_window.json"
fi

log "unit tests..."
cd "${SCRIPT_DIR}"
python3 tests/test_a6_v6.py -v 2>&1 | tee "${LOG_ROOT}/unit.log"

log "V6 analysis..."
python3 event_preload_v6_analyze.py \
  --run-id "${RUN_ID}" \
  --trace-dir "${TRACE_DIR}" \
  --db-path "${DB_PATH}" \
  --analysis-dir "${ANALYSIS_DIR}" \
  --log-dir "${LOG_ROOT}" \
  --pid-min 26025 \
  --pid-max 26040 \
  ${PW} \
  2>&1 | tee -a "${LOG_ROOT}/analysis.log"

ANALYSIS_EXIT=$?
sha256sum "${DB_PATH}" | tee -a "${LOG_ROOT}/hashes.log"

DEST="${MYPORTAL_BASE}/${RUN_ID}"
mkdir -p "${DEST}/analysis" "${DEST}/logs"
cp -a "${ANALYSIS_DIR}/." "${DEST}/analysis/"
cp -a "${LOG_ROOT}/." "${DEST}/logs/"
log "evidence copied to ${DEST}" | tee "${LOG_ROOT}/transfer.log"

log "done exit=${ANALYSIS_EXIT}"
exit "${ANALYSIS_EXIT}"
