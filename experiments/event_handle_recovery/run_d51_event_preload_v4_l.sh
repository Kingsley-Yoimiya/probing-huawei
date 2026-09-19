#!/usr/bin/env bash
# D51 Event Preload V4 — L route paired fresh runs (Level1 vs higher Level).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${ROOT}/build"
HELPER_SO="${BUILD_DIR}/libacl_real_dlsym.so"
V2_SO="${BUILD_DIR}/libacl_event_trace_v2.so"
V2_STUB_SO="${BUILD_DIR}/libacl_event_trace_v2_stub.so"
FAKE_ACL_SO="${BUILD_DIR}/libfake_acl.so"
PRELOAD_ASCEND="${HELPER_SO}:${V2_SO}"
PRELOAD_UNIT="${HELPER_SO}:${V2_STUB_SO}:${FAKE_ACL_SO}"

RUN_ID="${RUN_ID:?RUN_ID required}"
HIGH_LEVEL="${HIGH_LEVEL:-Level2}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORKDIR="/tmp/${RUN_ID}"
PERSIST="${HOME:-/root}/spindle_trace_runs/${RUN_ID}"
LOGDIR="${WORKDIR}/logs/${STAMP}"
mkdir -p "${LOGDIR}" "${PERSIST}" "${WORKDIR}/analysis"

exec > >(tee -a "${LOGDIR}/runner.log") 2>&1
echo "RUN_ID=${RUN_ID} HIGH_LEVEL=${HIGH_LEVEL} LOGDIR=${LOGDIR}"

run_one_train() {
  local tag="$1"
  local level="$2"
  local wdir="${WORKDIR}/${tag}"
  local ldir="${LOGDIR}/${tag}"
  mkdir -p "${wdir}/event_trace" "${wdir}/out" "${ldir}"
  export ACL_EVENT_TRACE_DIR="${wdir}/event_trace"
  export LD_PRELOAD="${PRELOAD_ASCEND}"
  echo "===== TRAIN ${tag} level=${level} ====="
  (
    timeout 1200 torchrun --nproc_per_node=16 --master_port=$((29500 + RANDOM % 1000)) \
      "${ROOT}/train_event_preload.py" \
      --output "${wdir}/out" \
      --trace-dir "${wdir}/event_trace" \
      --preload-lib "${V2_SO}" \
      --profiler-level "${level}"
  ) 2>&1 | tee "${ldir}/torchrun.log"
  echo "===== ANALYSIS ${tag} ====="
  python3 "${ROOT}/analyze_event_pairs.py" \
    --trace-dir "${wdir}/event_trace" \
    --profiler-out "${wdir}/out/args_on" \
    --analysis-dir "${wdir}/analysis" \
    --profile-window "${wdir}/out/args_on/profile_window.json" \
    2>&1 | tee "${ldir}/analysis.log"
  python3 "${ROOT}/classify_intervening_tasks.py" \
    --db "$(find "${wdir}/out/args_on" -name ascend_pytorch_profiler_0.db | head -1)" \
    --analysis-dir "${wdir}/analysis/v4_classify" \
    --profile-window "${wdir}/out/args_on/profile_window.json" \
    2>&1 | tee "${ldir}/classification.log"
  rsync -a "${wdir}/" "${PERSIST}/${tag}/" || cp -a "${wdir}/." "${PERSIST}/${tag}/"
}

{
  echo STAMP="${STAMP}"
  echo RUN_ID="${RUN_ID}"
  npu-smi info 2>&1 | head -20 || true
  python3 -c 'import torch,torch_npu; print(torch.__version__, torch_npu.__version__)'
  df -h /tmp "${HOME:-/root}"
} | tee "${LOGDIR}/preflight.log"

cd "${ROOT}"
./build.sh ascend 2>&1 | tee "${LOGDIR}/build.log"
./build.sh local 2>&1 | tee "${LOGDIR}/unit_build.log"
export LD_LIBRARY_PATH="${ROOT}/build:${LD_LIBRARY_PATH:-}"
LD_PRELOAD="${PRELOAD_UNIT}" "${ROOT}/build/event_sequence_smoke" 2>&1 | tee "${LOGDIR}/unit.log"

export ACL_EVENT_TRACE_DIR="${WORKDIR}/smoke_trace"
mkdir -p "${ACL_EVENT_TRACE_DIR}"
export LD_PRELOAD="${PRELOAD_ASCEND}"
python3 "${ROOT}/smoke_event.py" \
  --trace-dir "${WORKDIR}/smoke_trace" \
  --preload-lib "${V2_SO}" \
  --timeout-s 60 2>&1 | tee "${LOGDIR}/smoke.log"

python3 "${ROOT}/smoke_profiler_level.py" \
  --output "${WORKDIR}/smoke_profiler_${HIGH_LEVEL}" \
  --profiler-level "${HIGH_LEVEL}" \
  --timeout-s 60 2>&1 | tee "${LOGDIR}/smoke_profiler.log"

run_one_train "fresh_level1" "Level1"
run_one_train "fresh_${HIGH_LEVEL,,}" "${HIGH_LEVEL}"

python3 - <<'PY' "${WORKDIR}" "${LOGDIR}" "${HIGH_LEVEL}"
import json, sys
from pathlib import Path
root, logdir, high = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
l1 = json.loads((root / "fresh_level1/analysis/summary.json").read_text())
h = json.loads((root / f"fresh_{high.lower()}/analysis/summary.json").read_text())
keys = [
    "A2_preload_rank0_active_record", "A2_preload_rank0_active_wait",
    "A2_cann_rank0_active_record", "A2_cann_rank0_active_wait", "A5_n_comm",
]
lines = ["# level_comparability", ""]
ok = True
for k in keys:
    v1, vh = l1.get(k), h.get(k)
    match = v1 == vh
    ok = ok and match
    lines.append(f"- {k}: Level1={v1} {high}={vh} {'OK' if match else 'MISMATCH'}")
wait1 = l1.get("event_wait_type_id")
# event wait task count from histogram not in summary - use A6
lines.append(f"- A6_unique_chain: Level1={l1.get('A6_unique_chain')} {high}={h.get('A6_unique_chain')}")
out = logdir / "level_comparability.md"
out.write_text("\n".join(lines) + "\n")
print("COMPARABLE" if ok else "INCOMPARABLE", flush=True)
if not ok:
    raise SystemExit(4)
PY

( cd "${PERSIST}" && find . -type f -print0 | sort -z | xargs -0 sha256sum ) > "${PERSIST}/sha256sums.txt"
echo "DONE RUN_ID=${RUN_ID} PERSIST=${PERSIST}"
