#!/usr/bin/env bash
# D51 V5 wait-gap reanalysis on frozen V2b DB + bins. Read-only; does not stop vLLM.
set -euo pipefail

UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_event_preload_v5_waitgap}"
V2B_ROOT="${V2B_ROOT:-/tmp/20260823T145208Z_d51_event_preload_v2b_analyze}"
TRACE_DIR="${TRACE_DIR:-${V2B_ROOT}/event_trace}"
DB_PATH="${DB_PATH:-${V2B_ROOT}/profiler/ascend_pytorch_profiler_0.db}"
OUT_ROOT="${OUT_ROOT:-/tmp/${RUN_ID}}"
ANALYSIS_DIR="${OUT_ROOT}/analysis"
LOG_ROOT="${OUT_ROOT}/logs/${UTC_STAMP}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "${ANALYSIS_DIR}" "${LOG_ROOT}"

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "${LOG_ROOT}/preflight.log"; }

log "RUN_ID=${RUN_ID}"
log "DB=${DB_PATH}"
log "TRACE_DIR=${TRACE_DIR}"

if [[ ! -f "${DB_PATH}" ]]; then
  log "DB missing; searching..."
  DB_PATH="$(find /tmp -name 'ascend_pytorch_profiler_0.db' 2>/dev/null | head -1)"
  log "found DB=${DB_PATH}"
fi

sha256sum "${DB_PATH}" | tee "${LOG_ROOT}/hashes.log"
ls -la "${TRACE_DIR}"/rank_*_pid_*.events.bin | tee -a "${LOG_ROOT}/hashes.log"
BIN_N=$(ls "${TRACE_DIR}"/rank_*_pid_*.events.bin 2>/dev/null | wc -l)
log "bin_count=${BIN_N}"

PW=""
if [[ -f "${V2B_ROOT}/profiler/profile_window.json" ]]; then
  PW="--profile-window ${V2B_ROOT}/profiler/profile_window.json"
elif [[ -f "${V2B_ROOT}/profile_window.json" ]]; then
  PW="--profile-window ${V2B_ROOT}/profile_window.json"
fi

log "unit tests..."
cd "${SCRIPT_DIR}"
python3 tests/test_wait_gap_v5.py -v 2>&1 | tee "${LOG_ROOT}/unit.log"

log "analysis (expected 5-15 min wall, usually <2 min)..."
python3 wait_gap_analyze.py \
  --run-id "${RUN_ID}" \
  --trace-dir "${TRACE_DIR}" \
  --db-path "${DB_PATH}" \
  --analysis-dir "${ANALYSIS_DIR}" \
  --log-dir "${LOG_ROOT}" \
  --pid-min 26025 \
  --pid-max 26040 \
  ${PW} \
  2>&1 | tee "${LOG_ROOT}/analysis.log"

sha256sum "${DB_PATH}" | tee -a "${LOG_ROOT}/hashes.log"
log "done exit=$?"
