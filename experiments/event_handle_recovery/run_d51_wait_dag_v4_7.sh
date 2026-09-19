#!/usr/bin/env bash
# D51 Wait DAG V4.7: layered identity + unmasked comm-stream occupancy.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${ROOT}:${PYTHONPATH:-}"
UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BUILD_ID="${BUILD_ID:-${UTC_STAMP}_build_v4_7}"
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_wait_dag_v4_7_layered_identity_unmasked_occupancy}"
WORKDIR="/tmp/${RUN_ID}"
LOGDIR="${WORKDIR}/logs/${UTC_STAMP}"
MYPORTAL="/workspace/myportal/results/npu-dev-1/${RUN_ID}"
TARGET_COMM="hcom_allReduce__612_0_1"
V46_FROZEN="${V46_FROZEN:-/tmp/20260824T165749Z_d51_wait_dag_v4_6_post_wait_comm_stream_device_work/frozen_dose_v4_5_ref.json}"
KERNEL_O="${ROOT}/build/kernels/d51_compute_delay_kernel.o"
BUILD_DIR="${ROOT}/build"
HELPER_SO="${BUILD_DIR}/libacl_real_dlsym.so"
V2_SO="${BUILD_DIR}/libacl_event_trace_v2.so"
PRELOAD_ASCEND="${HELPER_SO}:${V2_SO}"
EST_TRAIN_S=180
PID_FILE="${LOGDIR}/runner.pid"
DSMALL_ITERS=325
DLARGE_ITERS=5382
STOP_REASON=""

mkdir -p \
  "${LOGDIR}/preflight" \
  "${LOGDIR}/build_unit" \
  "${LOGDIR}/real_smoke" \
  "${LOGDIR}/runs" \
  "${LOGDIR}/profiler" \
  "${LOGDIR}/identity" \
  "${LOGDIR}/projection" \
  "${LOGDIR}/paired_analysis" \
  "${LOGDIR}/controls" \
  "${LOGDIR}/sync" \
  "${LOGDIR}/package" \
  "${MYPORTAL}"

exec > >(tee -a "${LOGDIR}/runner.log") 2>&1
echo "$$" > "${PID_FILE}"
echo "RUN_ID=${RUN_ID} BUILD_ID=${BUILD_ID} PID=$$ PGID=$(ps -o pgid= $$ | tr -d ' ')"
echo "expected_runtime: unit+smoke 10-25min; b1 D0+Dsmall+Dlarge 20-50min"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

sha256_file() { sha256sum "$1" | awk '{print $1}'; }

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

write_stop_null() {
  python3 - <<PY
import json
from pathlib import Path
stop={"stop": None, "utc":"${UTC_STAMP}", "run_id":"${RUN_ID}", "build_id":"${BUILD_ID}"}
Path("${WORKDIR}/STOP.json").write_text(json.dumps(stop, indent=2)+"\n")
PY
}

export ACL_EVENT_INJECT_SITE=AFTER_SUCCESSFUL_TARGET_WAIT
UNIT_PASS=false
SMOKE_PASS=false

# Step 0: unit (must PASS)
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
UNIT_BIN="${BUILD_DIR}/test_wait_dag_v4_7_unit"
g++ -O2 -std=c++17 \
  "${ROOT}/tests/test_wait_dag_v4_7_unit.cpp" \
  -Wl,--no-as-needed \
  "${BUILD_DIR}/libacl_event_trace_v2_stub.so" \
  "${BUILD_DIR}/libfake_acl.so" \
  "${HELPER_SO}" \
  -Wl,-rpath,"${BUILD_DIR}" -ldl -pthread \
  -o "${UNIT_BIN}" 2>&1 | tee "${LOGDIR}/build_unit/unit_link.log"
if "${UNIT_BIN}" 2>&1 | tee "${LOGDIR}/build_unit/unit_v4_7.log"; then
  UNIT_PASS=true
  log "unit_v4_7 all PASS"
else
  write_stop "STOP_UNIT_WAIT_THEN_LAUNCH_FAILED"
  exit 3
fi

if ! python3 "${ROOT}/tests/test_wait_dag_v4_7.py" 2>&1 | tee "${LOGDIR}/build_unit/pytest_v4_7.log"; then
  write_stop "STOP_UNIT_PYTEST_FAILED"
  exit 3
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
v46 = Path("${V46_FROZEN}")
frozen = json.loads(v46.read_text()) if v46.exists() else {}
print(json.dumps({
  "run_id":"${RUN_ID}",
  "build_id":"${BUILD_ID}",
  "inject_site":"AFTER_SUCCESSFUL_TARGET_WAIT",
  "v46_frozen_path": str(v46),
  "Dsmall_iters": frozen.get("Dsmall_iters", ${DSMALL_ITERS}),
  "Dlarge_iters": frozen.get("Dlarge_iters", ${DLARGE_ITERS}),
  "kernel_cpp_sha256": sha(root/"kernels/d51_compute_delay_kernel.cpp"),
  "device_work_sha256": sha(root/"device_work.cpp"),
  "wait_dag_v4_7_sha256": sha(root/"wait_dag_v4_7_intervention.py"),
}, indent=2))
PY
cp "${LOGDIR}/preflight/preflight.json" "${MYPORTAL}/preflight.json"

if [[ -f "${V46_FROZEN}" ]]; then
  DSMALL_ITERS=$(python3 -c "import json; print(json.load(open('${V46_FROZEN}'))['Dsmall_iters'])")
  DLARGE_ITERS=$(python3 -c "import json; print(json.load(open('${V46_FROZEN}'))['Dlarge_iters'])")
fi
cp "${V46_FROZEN}" "${WORKDIR}/frozen_dose_v4_5_ref.json" 2>/dev/null || true

# Step 2: kernel + preload
log "Step 2 kernel + preload"
"${ROOT}/kernels/build_kernel.sh" 2>&1 | tee "${LOGDIR}/build_unit/kernel_build.log"
./build.sh ascend 2>&1 | tee "${LOGDIR}/build_unit/build_preload.log"
export LD_PRELOAD="${PRELOAD_ASCEND}"
KERNEL_SHA=$(sha256_file "${KERNEL_O}")
log "KERNEL_SHA=${KERNEL_SHA}"

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

# Step 3: D0_b1 for selector manifest (enumeration input only)
log "Step 3 D0_b1"
D0_TAG="D0_b1"
D0_DIR="${WORKDIR}/runs/${D0_TAG}"
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

# Step 4: smoke
log "Step 4 real smoke"
SMOKE_JSON="${LOGDIR}/real_smoke/smoke_post_wait.json"
if python3 "${ROOT}/smoke_post_wait_v4_7.py" \
  --trace-root "${LOGDIR}/real_smoke/traces" \
  --preload-lib "${V2_SO}" \
  --kernel-binary "${KERNEL_O}" \
  --selector-manifest "${SELECTOR_MANIFEST}" \
  --iters 0 "${DSMALL_ITERS}" "${DLARGE_ITERS}" \
  --out-json "${SMOKE_JSON}" \
  2>&1 | tee "${LOGDIR}/real_smoke/smoke.log"; then
  SMOKE_PASS=true
else
  write_stop "STOP_SMOKE_POST_WAIT_FAILED"
  exit 6
fi
cp "${SMOKE_JSON}" "${MYPORTAL}/smoke_post_wait.json"

# Step 5: b1 Dsmall + Dlarge (D0 done)
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
GATE_RC=0
if ! python3 "${ROOT}/wait_dag_v4_7_intervention.py" \
  --manifest "${PAIR_MANIFEST}" \
  --selector-manifest "${SELECTOR_MANIFEST}" \
  --out-dir "${PAIR_OUT}" \
  2>&1 | tee "${LOGDIR}/paired_analysis/analyze_b1.log"; then
  write_stop "STOP_PAIRED_ANALYSIS_FAILED"
  GATE_RC=7
else
  set +e
  PAIR_OUT="${PAIR_OUT}" KERNEL_SHA="${KERNEL_SHA}" WORKDIR="${WORKDIR}" \
  RUN_ID="${RUN_ID}" BUILD_ID="${BUILD_ID}" UTC_STAMP="${UTC_STAMP}" \
  python3 - <<PY | tee "${LOGDIR}/paired_analysis/gate_eval.json"
import csv, json, os, sys
from pathlib import Path
pair_out = Path(os.environ["PAIR_OUT"] + "/paired_effects_v4_7.csv")
rows = list(csv.DictReader(pair_out.open())) if pair_out.exists() else []
gate = {
  "unit_pass": True,
  "smoke_pass": True,
  "kernel_sha256": os.environ["KERNEL_SHA"],
  "pairs": rows,
  "b1_protocol_pass": False,
  "stop": None,
}
for r in rows:
    block = r.get("block")
    if r.get("structure_gate_pass") != "True":
        gate["stop"] = "STOP_STRUCTURE_GATE_FAILED"
        gate["fail_block"] = block
        break
    if r.get("dose_gate_pass") != "True":
        gate["stop"] = "STOP_DOSE_GATE_FAILED"
        gate["fail_block"] = block
        break
    if r.get("control_gate_pass") == "False":
        gate["stop"] = "STOP_CONTROL_GATE_FAILED"
        gate["fail_block"] = block
        break
    if block == "b1_dsmall":
        if r.get("causal_eligibility") == "STRUCTURE_ONLY_NOT_CAUSAL":
            gate["dsmall_structure_only"] = True
        elif r.get("local_causal_gate_pass") != "True":
            gate["stop"] = "STOP_DSMALL_CAUSAL_FAILED"
            gate["fail_block"] = block
            break
    if block == "b1_dlarge":
        if r.get("local_causal_gate_pass") != "True":
            gate["stop"] = "STOP_COMM_LOCAL_CAUSAL_NOT_REALIZED"
            gate["fail_block"] = block
            break
else:
    if not gate.get("stop"):
        gate["b1_protocol_pass"] = True

workdir = Path(os.environ["WORKDIR"])
workdir.joinpath("gate_summary.json").write_text(json.dumps(gate, indent=2) + "\n")
print(json.dumps(gate, indent=2))
if gate.get("stop"):
    stop = {
        "stop": gate["stop"],
        "utc": os.environ["UTC_STAMP"],
        "run_id": os.environ["RUN_ID"],
        "build_id": os.environ["BUILD_ID"],
        "fail_block": gate.get("fail_block"),
        "kernel_sha256": os.environ["KERNEL_SHA"],
    }
    workdir.joinpath("STOP.json").write_text(json.dumps(stop, indent=2) + "\n")
    sys.exit(8)
PY
  GATE_RC=${PIPESTATUS[0]}
  set -e
fi

package_myportal() {
  log "Step 6 package (always)"
  local pkg_log="${LOGDIR}/package/sync.log"
  mkdir -p "${MYPORTAL}/logs/${UTC_STAMP}"
  for f in paired_effects_v4_7.csv post_wait_injection_audit.csv intervention_identity.csv \
    kernel_realization.csv comm_entry_projection.csv node_wallclock.csv \
    control_effects.csv run_ledger.csv claims.md v4_7_summary.json; do
    cp "${PAIR_OUT}/${f}" "${MYPORTAL}/" 2>/dev/null || true
  done
  cp "${WORKDIR}/gate_summary.json" "${MYPORTAL}/" 2>/dev/null || true
  cp "${WORKDIR}/STOP.json" "${MYPORTAL}/" 2>/dev/null || true
  cp -a "${LOGDIR}/." "${MYPORTAL}/logs/${UTC_STAMP}/" 2>/dev/null || true
  python3 - <<PY | tee "${pkg_log}"
import hashlib, json, os
from pathlib import Path
myportal = Path("${MYPORTAL}")
manifest = {"run_id": "${RUN_ID}", "build_id": "${BUILD_ID}", "files": []}
for p in sorted(myportal.rglob("*")):
    if not p.is_file():
        continue
    rel = str(p.relative_to(myportal))
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    manifest["files"].append({"path": rel, "sha256": h.hexdigest(), "bytes": p.stat().st_size})
manifest["file_count"] = len(manifest["files"])
out = myportal / "hash_manifest.json"
out.write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps({"hash_manifest": str(out), "file_count": manifest["file_count"]}, indent=2))
PY
}

if [[ "${GATE_RC}" -ne 0 ]]; then
  if [[ ! -f "${WORKDIR}/STOP.json" ]]; then
    write_stop "STOP_PAIRED_ANALYSIS_OR_GATE_FAILED"
  fi
  package_myportal
  exit "${GATE_RC}"
fi
write_stop_null
package_myportal

log "BUILD_V4_7_COMPLETE RUN_ID=${RUN_ID} KERNEL_SHA=${KERNEL_SHA}"
