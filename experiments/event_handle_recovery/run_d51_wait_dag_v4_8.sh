#!/usr/bin/env bash
# D51 Wait DAG V4.8: STRUCTURAL_NA + anchor-relative controls.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${ROOT}:${PYTHONPATH:-}"
UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BUILD_ID="${BUILD_ID:-${UTC_STAMP}_build_v4_8}"
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_wait_dag_v4_8_structural_na_anchor_controls}"
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
EXPECTED_KERNEL_SHA="578a088f5d06e96977f780ed0f2da31704633b7bef4d067fd440231853d7a703"

mkdir -p \
  "${LOGDIR}/preflight" \
  "${LOGDIR}/build_unit" \
  "${LOGDIR}/real_smoke" \
  "${LOGDIR}/runs" \
  "${LOGDIR}/profiler" \
  "${LOGDIR}/identity" \
  "${LOGDIR}/capture_classification" \
  "${LOGDIR}/projection" \
  "${LOGDIR}/analysis" \
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

atomic_write_stop() {
  local reason="$1"
  python3 - <<PY
import json, os
from pathlib import Path
stop={
  "stop":"${reason}",
  "utc":"${UTC_STAMP}",
  "run_id":"${RUN_ID}",
  "build_id":"${BUILD_ID}",
  "inject_site":"AFTER_SUCCESSFUL_TARGET_WAIT",
  "kernel_sha256":"$(sha256_file "${KERNEL_O}" 2>/dev/null || echo unknown)",
}
workdir = Path("${WORKDIR}")
tmp = workdir / "STOP.json.tmp"
auth = workdir / "STOP.json"
tmp.write_text(json.dumps(stop, indent=2)+"\n")
fd = os.open(str(tmp), os.O_RDONLY)
os.fsync(fd)
os.close(fd)
os.replace(str(tmp), str(auth))
print(json.dumps(stop, indent=2))
PY
  log "STOP ${reason}"
}

write_stop_null() {
  python3 - <<PY
import json, os
from pathlib import Path
stop={"stop": None, "utc":"${UTC_STAMP}", "run_id":"${RUN_ID}", "build_id":"${BUILD_ID}"}
workdir = Path("${WORKDIR}")
tmp = workdir / "STOP.json.tmp"
auth = workdir / "STOP.json"
tmp.write_text(json.dumps(stop, indent=2)+"\n")
fd = os.open(str(tmp), os.O_RDONLY)
os.fsync(fd)
os.close(fd)
os.replace(str(tmp), str(auth))
PY
}

export ACL_EVENT_INJECT_SITE=AFTER_SUCCESSFUL_TARGET_WAIT

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
if ! "${UNIT_BIN}" 2>&1 | tee "${LOGDIR}/build_unit/unit_v4_7.log"; then
  atomic_write_stop "STOP_UNIT_WAIT_THEN_LAUNCH_FAILED"
  exit 3
fi
if ! python3 "${ROOT}/tests/test_wait_dag_v4_7.py" 2>&1 | tee "${LOGDIR}/build_unit/pytest_v4_7.log"; then
  atomic_write_stop "STOP_UNIT_PYTEST_V47_FAILED"
  exit 3
fi
if ! python3 "${ROOT}/tests/test_wait_dag_v4_8.py" 2>&1 | tee "${LOGDIR}/build_unit/pytest_v4_8.log"; then
  atomic_write_stop "STOP_UNIT_PYTEST_V48_FAILED"
  exit 3
fi

log "Step 1 preflight"
{
  npu-smi info -t board -i 0 2>/dev/null | head -8 || true
  pgrep -a vllm | head -3 || echo "vllm_not_listed"
  hostname
  cat /proc/self/cgroup 2>/dev/null | head -3 || true
  uname -a
  whoami
} | tee "${LOGDIR}/preflight/snapshot.txt"

python3 - <<PY | tee "${LOGDIR}/preflight/preflight.json"
import hashlib, json, os, subprocess
from pathlib import Path
from wait_dag_v4_8_intervention import canonical_capture_contract, capture_contract_sha
root = Path("${ROOT}")
def sha(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for c in iter(lambda:f.read(1<<20), b''): h.update(c)
    return h.hexdigest()
v46 = Path("${V46_FROZEN}")
frozen = json.loads(v46.read_text()) if v46.exists() else {}
cc = canonical_capture_contract()
cc_sha = capture_contract_sha(cc)
(Path("${LOGDIR}/preflight/capture_contract.json")).write_text(json.dumps(cc, indent=2)+"\n")
cann = ""
try:
    cann = subprocess.check_output(["bash","-lc","source /usr/local/Ascend/ascend-toolkit/latest/set_env.sh 2>/dev/null; cat /usr/local/Ascend/ascend-toolkit/latest/version.cfg 2>/dev/null | head -1"], text=True).strip()
except Exception:
    cann = "unknown"
print(json.dumps({
  "run_id":"${RUN_ID}",
  "build_id":"${BUILD_ID}",
  "inject_site":"AFTER_SUCCESSFUL_TARGET_WAIT",
  "v46_frozen_path": str(v46),
  "Dsmall_iters": frozen.get("Dsmall_iters", ${DSMALL_ITERS}),
  "Dlarge_iters": frozen.get("Dlarge_iters", ${DLARGE_ITERS}),
  "kernel_cpp_sha256": sha(root/"kernels/d51_compute_delay_kernel.cpp"),
  "device_work_sha256": sha(root/"device_work.cpp"),
  "wait_dag_v4_8_sha256": sha(root/"wait_dag_v4_8_intervention.py"),
  "expected_kernel_object_sha256": "${EXPECTED_KERNEL_SHA}",
  "capture_contract": cc,
  "capture_contract_sha256": cc_sha,
  "cann_version": cann,
}, indent=2))
PY
CAPTURE_SHA=$(python3 -c "import json; print(json.load(open('${LOGDIR}/preflight/preflight.json'))['capture_contract_sha256'])")
cp "${LOGDIR}/preflight/preflight.json" "${MYPORTAL}/preflight.json"
cp "${LOGDIR}/preflight/capture_contract.json" "${MYPORTAL}/capture_contract.json"

if [[ -f "${V46_FROZEN}" ]]; then
  DSMALL_ITERS=$(python3 -c "import json; print(json.load(open('${V46_FROZEN}'))['Dsmall_iters'])")
  DLARGE_ITERS=$(python3 -c "import json; print(json.load(open('${V46_FROZEN}'))['Dlarge_iters'])")
fi
cp "${V46_FROZEN}" "${WORKDIR}/frozen_dose_v4_5_ref.json" 2>/dev/null || true

log "Step 2 kernel + preload"
"${ROOT}/kernels/build_kernel.sh" 2>&1 | tee "${LOGDIR}/build_unit/kernel_build.log"
./build.sh ascend 2>&1 | tee "${LOGDIR}/build_unit/build_preload.log"
export LD_PRELOAD="${PRELOAD_ASCEND}"
KERNEL_SHA=$(sha256_file "${KERNEL_O}")
log "KERNEL_SHA=${KERNEL_SHA}"
if [[ "${KERNEL_SHA}" != "${EXPECTED_KERNEL_SHA}" ]]; then
  atomic_write_stop "STOP_KERNEL_SHA_MISMATCH"
  exit 4
fi

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

log "Step 3 D0_b1"
D0_TAG="D0_b1"
D0_DIR="${WORKDIR}/runs/${D0_TAG}"
run_train "${D0_TAG}" 0
D0_RUN_ID="${RUN_ID}_${D0_TAG}"
OUT_DIR="${WORKDIR}/reverse_extract_b1"
if ! python3 "${ROOT}/wait_dag_v4_2_reverse_candidate.py" \
  --run-dir "${D0_DIR}" \
  --run-id "${D0_RUN_ID}" \
  --out-dir "${OUT_DIR}" \
  --target-comm "${TARGET_COMM}" \
  --block b1 \
  2>&1 | tee "${LOGDIR}/projection/reverse_extract.log"; then
  atomic_write_stop "STOP_REVERSE_CANDIDATE_NOT_UNIQUE"
  exit 5
fi
SELECTOR_MANIFEST="${OUT_DIR}/selector_manifest_b1.json"

log "Step 4 real smoke"
SMOKE_JSON="${LOGDIR}/real_smoke/smoke_post_wait.json"
if ! python3 "${ROOT}/smoke_post_wait_v4_7.py" \
  --trace-root "${LOGDIR}/real_smoke/traces" \
  --preload-lib "${V2_SO}" \
  --kernel-binary "${KERNEL_O}" \
  --selector-manifest "${SELECTOR_MANIFEST}" \
  --iters 0 "${DSMALL_ITERS}" "${DLARGE_ITERS}" \
  --out-json "${SMOKE_JSON}" \
  2>&1 | tee "${LOGDIR}/real_smoke/smoke.log"; then
  atomic_write_stop "STOP_SMOKE_POST_WAIT_FAILED"
  exit 6
fi
cp "${SMOKE_JSON}" "${MYPORTAL}/smoke_post_wait.json"

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
if ! python3 "${ROOT}/wait_dag_v4_8_intervention.py" \
  --manifest "${PAIR_MANIFEST}" \
  --selector-manifest "${SELECTOR_MANIFEST}" \
  --out-dir "${PAIR_OUT}" \
  --capture-contract-sha "${CAPTURE_SHA}" \
  --preflight-capture-sha "${CAPTURE_SHA}" \
  2>&1 | tee "${LOGDIR}/analysis/analyze_b1.log"; then
  atomic_write_stop "STOP_PAIRED_ANALYSIS_FAILED"
  GATE_RC=7
else
  cp "${PAIR_OUT}/capture_classification.csv" "${LOGDIR}/capture_classification/" 2>/dev/null || true
  set +e
  PAIR_OUT="${PAIR_OUT}" KERNEL_SHA="${KERNEL_SHA}" WORKDIR="${WORKDIR}" \
  RUN_ID="${RUN_ID}" BUILD_ID="${BUILD_ID}" UTC_STAMP="${UTC_STAMP}" \
  CAPTURE_SHA="${CAPTURE_SHA}" \
  python3 - <<'PY' | tee "${LOGDIR}/analysis/gate_eval.json"
import csv, json, os, sys
from pathlib import Path
pair_out = Path(os.environ["PAIR_OUT"])
rows = list(csv.DictReader((pair_out / "paired_effects_v4_8.csv").open())) if (pair_out / "paired_effects_v4_8.csv").exists() else []
cls_rows = list(csv.DictReader((pair_out / "capture_classification.csv").open())) if (pair_out / "capture_classification.csv").exists() else []
gate = {
  "unit_pass": True,
  "smoke_pass": True,
  "kernel_sha256": os.environ["KERNEL_SHA"],
  "capture_contract_sha256": os.environ["CAPTURE_SHA"],
  "pairs": rows,
  "classification": cls_rows,
  "b1_protocol_pass": False,
  "stop": None,
}
for cr in cls_rows:
    if cr.get("pre_wait_p_status") != "STRUCTURAL_NA":
        gate["stop"] = cr.get("pre_wait_p_status") or "STOP_PRE_WAIT_P_CLASSIFICATION_FAILED"
        gate["fail_run"] = cr.get("run_id")
        break
else:
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
        gate["control_reason"] = r.get("control_reason")
        break
    if block == "b1_dsmall":
        if r.get("causal_eligibility") == "STRUCTURE_ONLY_NOT_CAUSAL":
            gate["dsmall_structure_only"] = True
        elif r.get("local_causal_gate_pass") not in ("", None):
            if r.get("local_causal_gate_pass") != "True":
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
        "fail_run": gate.get("fail_run"),
        "kernel_sha256": os.environ["KERNEL_SHA"],
    }
    tmp = workdir / "STOP.json.tmp"
    auth = workdir / "STOP.json"
    tmp.write_text(json.dumps(stop, indent=2) + "\n")
    import os as _os
    fd = _os.open(str(tmp), _os.O_RDONLY)
    _os.fsync(fd)
    _os.close(fd)
    _os.replace(str(tmp), str(auth))
    sys.exit(8)
PY
  GATE_RC=${PIPESTATUS[0]}
  set -e
fi

package_myportal() {
  log "Step 6 package (always)"
  local pkg_log="${LOGDIR}/package/sync.log"
  mkdir -p "${MYPORTAL}/logs/${UTC_STAMP}"
  for f in paired_effects_v4_8.csv post_wait_injection_audit.csv intervention_identity.csv \
    kernel_realization.csv comm_entry_projection.csv node_wallclock.csv \
    control_effects_v4_8.csv capture_classification.csv run_ledger.csv claims.md v4_8_summary.json; do
    cp "${PAIR_OUT}/${f}" "${MYPORTAL}/" 2>/dev/null || true
  done
  cp "${WORKDIR}/gate_summary.json" "${MYPORTAL}/" 2>/dev/null || true
  cp "${WORKDIR}/STOP.json" "${MYPORTAL}/" 2>/dev/null || true
  cp "${LOGDIR}/preflight/capture_contract.json" "${MYPORTAL}/" 2>/dev/null || true
  cp -a "${LOGDIR}/." "${MYPORTAL}/logs/${UTC_STAMP}/" 2>/dev/null || true
  python3 - <<PY | tee "${pkg_log}"
import hashlib, json, datetime
from pathlib import Path
myportal = Path("${MYPORTAL}")
manifest = {"run_id": "${RUN_ID}", "build_id": "${BUILD_ID}", "files": []}
for p in sorted(myportal.rglob("*")):
    if not p.is_file() or p.name == "hash_manifest.json":
        continue
    rel = str(p.relative_to(myportal))
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    manifest["files"].append({"path": rel, "sha256": h.hexdigest(), "bytes": p.stat().st_size})
manifest["file_count"] = len(manifest["files"])
manifest_path = myportal / "hash_manifest.json"
manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
receipt = {
    "manifest_sha256": manifest_sha,
    "generated_utc": datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"),
    "file_count": manifest["file_count"],
}
(myportal / "package_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps({"hash_manifest": str(manifest_path), "file_count": manifest["file_count"]}, indent=2))
PY
}

if [[ "${GATE_RC}" -ne 0 ]]; then
  if [[ ! -f "${WORKDIR}/STOP.json" ]]; then
    atomic_write_stop "STOP_PAIRED_ANALYSIS_OR_GATE_FAILED"
  fi
  package_myportal
  exit "${GATE_RC}"
fi
write_stop_null
package_myportal

log "BUILD_V4_8_COMPLETE RUN_ID=${RUN_ID} KERNEL_SHA=${KERNEL_SHA}"
