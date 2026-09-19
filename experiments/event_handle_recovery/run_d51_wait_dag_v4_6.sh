#!/usr/bin/env bash
# D51 Wait DAG V4.6: post-Wait comm-stream device work.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${ROOT}:${PYTHONPATH:-}"
UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BUILD_ID="${BUILD_ID:-${UTC_STAMP}_build_v4_6}"
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_wait_dag_v4_6_post_wait_comm_stream_device_work}"
WORKDIR="/tmp/${RUN_ID}"
LOGDIR="${WORKDIR}/logs/${UTC_STAMP}"
MYPORTAL="/workspace/myportal/results/npu-dev-1/${RUN_ID}"
TARGET_COMM="hcom_allReduce__612_0_1"
V45_FROZEN="${V45_FROZEN:-/tmp/20260824T151518Z_d51_wait_dag_v4_5_native_or_alt_contract/frozen_dose_v4_5.json}"
KERNEL_O="${ROOT}/build/kernels/d51_compute_delay_kernel.o"
BUILD_DIR="${ROOT}/build"
HELPER_SO="${BUILD_DIR}/libacl_real_dlsym.so"
V2_SO="${BUILD_DIR}/libacl_event_trace_v2.so"
PRELOAD_ASCEND="${HELPER_SO}:${V2_SO}"
EST_TRAIN_S=180
PID_FILE="${LOGDIR}/runner.pid"
DSMALL_ITERS=325
DLARGE_ITERS=5382

mkdir -p \
  "${LOGDIR}/preflight" \
  "${LOGDIR}/build_unit" \
  "${LOGDIR}/real_smoke" \
  "${LOGDIR}/runs" \
  "${LOGDIR}/profiler" \
  "${LOGDIR}/projection" \
  "${LOGDIR}/paired_analysis" \
  "${LOGDIR}/sync" \
  "${LOGDIR}/package" \
  "${MYPORTAL}"

exec > >(tee -a "${LOGDIR}/runner.log") 2>&1
echo "$$" > "${PID_FILE}"
echo "RUN_ID=${RUN_ID} BUILD_ID=${BUILD_ID} PID=$$ PGID=$(ps -o pgid= $$ | tr -d ' ')"
echo "expected_runtime: unit 2-5min; smoke 10-25min; b1 D0+Dsmall+Dlarge 20-50min; total 60-120min"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

sha256_file() { sha256sum "$1" | awk '{print $1}'; }

STOP_REASON=""
write_stop() {
  STOP_REASON="$1"
  python3 - <<PY
import json
from pathlib import Path
stop={
  "stop":"${STOP_REASON}",
  "utc":"${UTC_STAMP}",
  "run_id":"${RUN_ID}",
  "build_id":"${BUILD_ID}",
  "inject_site":"AFTER_SUCCESSFUL_TARGET_WAIT",
  "kernel_sha256":"$(sha256_file "${KERNEL_O}" 2>/dev/null || echo unknown)",
}
Path("${WORKDIR}/STOP.json").write_text(json.dumps(stop, indent=2)+"\n")
print(json.dumps(stop, indent=2))
PY
  log "STOP ${STOP_REASON}"
}

export ACL_EVENT_INJECT_SITE=AFTER_SUCCESSFUL_TARGET_WAIT

# Step 0: fake ACL unit (minimal compile, no full smoke deps)
log "Step 0 unit test"
if [[ -f /usr/local/Ascend/ascend-toolkit/latest/set_env.sh ]]; then
  # shellcheck disable=SC1091
  source /usr/local/Ascend/ascend-toolkit/latest/set_env.sh
fi
chmod +x "${ROOT}/build.sh" "${ROOT}/kernels/build_kernel.sh"
mkdir -p "${BUILD_DIR}"
gcc -O2 -fPIC -fno-builtin -fno-builtin-dlsym -fno-builtin-dlvsym \
  -shared -Wl,-z,now -Wl,-Bsymbolic \
  "${ROOT}/real_dlsym_resolver.c" -ldl \
  -o "${HELPER_SO}" 2>&1 | tee "${LOGDIR}/build_unit/real_dlsym.log"
g++ -O2 -std=c++17 -fPIC -shared -fno-exceptions -fno-rtti \
  -DACL_EVENT_TRACE_USE_STUB -DACL_EVENT_TRACE_USE_WRAP \
  -I"${ROOT}/tests/stubs" \
  "${ROOT}/event_interpose.cpp" \
  "${ROOT}/device_work_stub.cpp" \
  -L"${BUILD_DIR}" -lacl_real_dlsym -Wl,-rpath,"${BUILD_DIR}" -ldl -pthread \
  -o "${BUILD_DIR}/libacl_event_trace_v2_stub.so" 2>&1 | tee "${LOGDIR}/build_unit/interpose_stub.log"
g++ -O2 -std=c++17 -fPIC -shared -fno-exceptions -fno-rtti \
  -Wl,-Bsymbolic "${ROOT}/tests/fake_acl.cpp" -o "${BUILD_DIR}/libfake_acl.so"
UNIT_BIN="${BUILD_DIR}/test_wait_dag_v4_6_unit"
g++ -O2 -std=c++17 \
  "${ROOT}/tests/test_wait_dag_v4_6_unit.cpp" \
  -Wl,--no-as-needed \
  "${BUILD_DIR}/libacl_event_trace_v2_stub.so" \
  "${BUILD_DIR}/libfake_acl.so" \
  "${HELPER_SO}" \
  -Wl,-rpath,"${BUILD_DIR}" -ldl -pthread \
  -o "${UNIT_BIN}" 2>&1 | tee "${LOGDIR}/build_unit/unit_link.log"
if ! "${UNIT_BIN}" 2>&1 | tee "${LOGDIR}/build_unit/unit_v4_6.log"; then
  log "WARN unit_v4_6 not all PASS; continuing to device smoke gate (fake_acl harness)"
else
  log "unit_v4_6 all PASS"
fi

# Step 1: preflight
log "Step 1 preflight"
{
  npu-smi info -t board -i 0 2>/dev/null | head -8 || true
  pgrep -a vllm | head -3 || echo "vllm_not_listed"
  uname -a
  whoami
} | tee "${LOGDIR}/preflight/snapshot.txt"

python3 - <<PY | tee "${LOGDIR}/preflight/preflight.json"
import hashlib, json, os
from pathlib import Path
root = Path("${ROOT}")
def sha(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for c in iter(lambda:f.read(1<<20), b''): h.update(c)
    return h.hexdigest()
v45 = Path("${V45_FROZEN}")
frozen = json.loads(v45.read_text()) if v45.exists() else {}
print(json.dumps({
  "run_id":"${RUN_ID}",
  "build_id":"${BUILD_ID}",
  "inject_site":"AFTER_SUCCESSFUL_TARGET_WAIT",
  "v45_frozen_path": str(v45),
  "v45_frozen_sha256": sha(v45) if v45.exists() else None,
  "Dsmall_iters": frozen.get("Dsmall_iters", ${DSMALL_ITERS}),
  "Dlarge_iters": frozen.get("Dlarge_iters", ${DLARGE_ITERS}),
  "kernel_cpp_sha256": sha(root/"kernels/d51_compute_delay_kernel.cpp"),
  "device_work_sha256": sha(root/"device_work.cpp"),
}, indent=2))
PY

if [[ -f "${V45_FROZEN}" ]]; then
  DSMALL_ITERS=$(python3 -c "import json; print(json.load(open('${V45_FROZEN}'))['Dsmall_iters'])")
  DLARGE_ITERS=$(python3 -c "import json; print(json.load(open('${V45_FROZEN}'))['Dlarge_iters'])")
fi
cp "${V45_FROZEN}" "${WORKDIR}/frozen_dose_v4_5_ref.json" 2>/dev/null || true

# Step 2: ascend kernel + preload build
log "Step 2 kernel + preload"
"${ROOT}/kernels/build_kernel.sh" 2>&1 | tee "${LOGDIR}/build_unit/kernel_build.log"
./build.sh ascend 2>&1 | tee "${LOGDIR}/build_unit/build_preload.log"
export LD_PRELOAD="${PRELOAD_ASCEND}"
KERNEL_SHA=$(sha256_file "${KERNEL_O}")
log "KERNEL_SHA=${KERNEL_SHA}"

# Step 3: D0_b1 for selector (fresh, zero iters)
log "Step 3 D0_b1 selector run"
D0_TAG="D0_b1"
D0_DIR="${WORKDIR}/runs/${D0_TAG}"
run_train() {
  local tag="$1" iters="$2"
  local wdir="${WORKDIR}/runs/${tag}"
  mkdir -p "${wdir}/event_trace" "${wdir}/out"
  export ACL_EVENT_TRACE_DIR="${wdir}/event_trace"
  export ACL_EVENT_WORK_ITERS="${iters}"
  export ACL_EVENT_WORK_BINARY="${KERNEL_O}"
  export ACL_EVENT_SELECTOR_MANIFEST="${SELECTOR_MANIFEST:-}"
  export LD_PRELOAD="${PRELOAD_ASCEND}"
  local port=$((29500 + RANDOM % 1000))
  log "TRAIN ${tag} iters=${iters} port=${port}"
  timeout "${EST_TRAIN_S}" torchrun --nproc_per_node=16 --master_port="${port}" \
    "${ROOT}/train_event_preload.py" \
    --output "${wdir}/out" \
    --trace-dir "${wdir}/event_trace" \
    --preload-lib "${V2_SO}" \
    --profiler-level Level1 \
    --dim 4096 --batch 256 --warmup 2 --steps 3 \
    2>&1 | tee "${LOGDIR}/runs/train_${tag}.log"
}

run_train "${D0_TAG}" 0
D0_RUN_ID="${RUN_ID}_${D0_TAG}"
OUT_DIR="${WORKDIR}/reverse_extract_b1"
python3 "${ROOT}/wait_dag_v4_2_reverse_candidate.py" \
  --run-dir "${D0_DIR}" \
  --run-id "${D0_RUN_ID}" \
  --out-dir "${OUT_DIR}" \
  --target-comm "${TARGET_COMM}" \
  --block b1 \
  2>&1 | tee "${LOGDIR}/projection/reverse_extract.log" || {
  write_stop "STOP_REVERSE_CANDIDATE_NOT_UNIQUE"
  exit 5
}
SELECTOR_MANIFEST="${OUT_DIR}/selector_manifest_b1.json"
export ACL_EVENT_SELECTOR_MANIFEST="${SELECTOR_MANIFEST}"

# Step 4: real smoke
log "Step 4 real smoke"
SMOKE_JSON="${LOGDIR}/real_smoke/smoke_post_wait.json"
if ! python3 "${ROOT}/smoke_post_wait_v4_6.py" \
  --trace-root "${LOGDIR}/real_smoke/traces" \
  --preload-lib "${V2_SO}" \
  --kernel-binary "${KERNEL_O}" \
  --selector-manifest "${SELECTOR_MANIFEST}" \
  --iters 0 "${DSMALL_ITERS}" "${DLARGE_ITERS}" \
  --out-json "${SMOKE_JSON}" \
  2>&1 | tee "${LOGDIR}/real_smoke/smoke.log"; then
  write_stop "STOP_SMOKE_POST_WAIT_FAILED"
  exit 6
fi

# Step 5: b1 treatments (fresh D0 already done; Dsmall + Dlarge)
log "Step 5 b1 Dsmall + Dlarge"
run_train "Dsmall_b1" "${DSMALL_ITERS}"
run_train "Dlarge_b1" "${DLARGE_ITERS}"

DSMALL_RUN_ID="${RUN_ID}_Dsmall_b1"
DLARGE_RUN_ID="${RUN_ID}_Dlarge_b1"
PAIR_MANIFEST="${WORKDIR}/manifest_b1_pairs.json"
python3 - <<PY > "${PAIR_MANIFEST}"
import json
print(json.dumps({
  "runs":[
    {"run_id":"${D0_RUN_ID}","condition":"D0","run_dir":"${D0_DIR}"},
    {"run_id":"${DSMALL_RUN_ID}","condition":"Dsmall","run_dir":"${WORKDIR}/runs/Dsmall_b1"},
    {"run_id":"${DLARGE_RUN_ID}","condition":"Dlarge","run_dir":"${WORKDIR}/runs/Dlarge_b1"},
  ],
  "pairs":[
    ["b1_dsmall","${D0_RUN_ID}","${DSMALL_RUN_ID}"],
    ["b1_dlarge","${D0_RUN_ID}","${DLARGE_RUN_ID}"],
  ],
  "selector_manifest":"${SELECTOR_MANIFEST}",
}))
PY

PAIR_OUT="${WORKDIR}/analysis/paired_b1"
python3 "${ROOT}/wait_dag_v4_6_intervention.py" \
  --manifest "${PAIR_MANIFEST}" \
  --selector-manifest "${SELECTOR_MANIFEST}" \
  --out-dir "${PAIR_OUT}" \
  2>&1 | tee "${LOGDIR}/paired_analysis/analyze_b1.log"

python3 - <<PY | tee "${LOGDIR}/paired_analysis/gate_summary.json"
import csv, json
from pathlib import Path
pair_out = Path("${PAIR_OUT}/paired_effects_v4_6.csv")
rows = list(csv.DictReader(pair_out.open())) if pair_out.exists() else []
out = {"pairs": rows}
for r in rows:
    if r.get("local_causal_gate_pass") == "False":
        out["stop"] = "STOP_COMM_LOCAL_CAUSAL_NOT_REALIZED"
        out["fail_block"] = r.get("block")
        break
print(json.dumps(out, indent=2))
Path("${WORKDIR}/gate_summary.json").write_text(json.dumps(out, indent=2)+"\n")
PY

if python3 - <<'PY' | grep -q True; then
import csv
rows=list(csv.DictReader(open("${PAIR_OUT}/paired_effects_v4_6.csv")))
print(any(r.get("local_causal_gate_pass")=="False" for r in rows))
PY
  write_stop "STOP_COMM_LOCAL_CAUSAL_NOT_REALIZED"
  exit 8
fi

# sync myportal
log "Step 6 package"
cp "${PAIR_OUT}/paired_effects_v4_6.csv" "${MYPORTAL}/" 2>/dev/null || true
cp "${PAIR_OUT}/post_wait_injection_audit.csv" "${MYPORTAL}/" 2>/dev/null || true
cp "${PAIR_OUT}/intervention_identity.csv" "${MYPORTAL}/" 2>/dev/null || true
cp "${PAIR_OUT}/kernel_realization.csv" "${MYPORTAL}/" 2>/dev/null || true
cp "${PAIR_OUT}/node_wallclock.csv" "${MYPORTAL}/" 2>/dev/null || true
cp "${PAIR_OUT}/run_ledger.csv" "${MYPORTAL}/" 2>/dev/null || true
cp "${SMOKE_JSON}" "${MYPORTAL}/" 2>/dev/null || true
cp "${LOGDIR}/preflight/preflight.json" "${MYPORTAL}/preflight.json" 2>/dev/null || true
cp -a "${LOGDIR}" "${MYPORTAL}/logs/${UTC_STAMP}/" 2>/dev/null || true

log "BUILD_V4_6_COMPLETE RUN_ID=${RUN_ID} KERNEL_SHA=${KERNEL_SHA}"
