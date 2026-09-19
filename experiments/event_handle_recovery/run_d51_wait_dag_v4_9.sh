#!/usr/bin/env bash
# D51 Wait DAG V4.9: path-external C3 DID + C5 diagnostic-only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${ROOT}:${PYTHONPATH:-}"
UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BUILD_ID="${BUILD_ID:-${UTC_STAMP}_build_v4_9}"
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_wait_dag_v4_9_path_external_c3_c5_diagnostic}"
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
echo "expected_runtime: unit+smoke 10-25min; b1+b2+b3 40-90min"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

sha256_file() { sha256sum "$1" | awk '{print $1}'; }

SKIPPED_TS="$(date -u +%Y%m%dT%H%M%SZ)"
echo "SKIPPED:awaiting_identity_extraction UTC=${SKIPPED_TS}" > "${LOGDIR}/identity/README.txt"
echo "SKIPPED:awaiting_control_analysis UTC=${SKIPPED_TS}" > "${LOGDIR}/controls/README.txt"

ensure_nonempty_log() {
  local path="$1"
  local reason="$2"
  if [[ ! -s "${path}" ]]; then
    echo "SKIPPED:${reason} UTC=$(date -u +%Y%m%dT%H%M%SZ)" > "${path}"
  fi
}

atomic_write_stop() {
  local reason="$1"
  local fail_block="${2:-}"
  python3 - <<PY
import json, os
from pathlib import Path
workdir = Path("${WORKDIR}")
gate = {
  "stop": "${reason}",
  "utc": "${UTC_STAMP}",
  "run_id": "${RUN_ID}",
  "build_id": "${BUILD_ID}",
  "fail_block": "${fail_block}" if "${fail_block}" else None,
  "b1_protocol_pass": False,
  "kernel_sha256": "${KERNEL_SHA:-unknown}",
}
workdir.joinpath("gate_summary.json").write_text(json.dumps(gate, indent=2) + "\n")
stop = {
  "stop": "${reason}",
  "utc": "${UTC_STAMP}",
  "run_id": "${RUN_ID}",
  "build_id": "${BUILD_ID}",
  "inject_site": "AFTER_SUCCESSFUL_TARGET_WAIT",
  "kernel_sha256": "${KERNEL_SHA:-unknown}",
}
if "${fail_block}":
    stop["fail_block"] = "${fail_block}"
tmp = workdir / "STOP.json.tmp"
auth = workdir / "STOP.json"
tmp.write_text(json.dumps(stop, indent=2) + "\n")
fd = os.open(str(tmp), os.O_RDONLY)
os.fsync(fd)
os.close(fd)
os.replace(str(tmp), str(auth))
print(json.dumps(stop, indent=2))
PY
  log "STOP ${reason}"
}

promote_package_artifacts() {
  local src="${1:-${WORKDIR}/analysis/paired_all}"
  if [[ ! -d "${src}" ]]; then
    return 0
  fi
  for f in paired_effects_v4_9.csv c3_preintervention_rank_pairs.csv c3_noise_envelope.csv \
    c5_path_diagnostics.csv control_effects_v4_9.csv capture_classification.csv run_ledger.csv \
    v4_9_summary.json path_external_proofs.json path_external_proofs.csv; do
    if [[ -f "${src}/${f}" && -s "${src}/${f}" ]]; then
      cp "${src}/${f}" "${WORKDIR}/${f}"
    fi
  done
}

write_stop_null() {
  python3 - <<PY
import json, os
from pathlib import Path
workdir = Path("${WORKDIR}")
gate_path = workdir / "gate_summary.json"
if gate_path.exists():
    gate = json.loads(gate_path.read_text())
    gate["stop"] = None
else:
    gate = {
        "stop": None,
        "utc": "${UTC_STAMP}",
        "run_id": "${RUN_ID}",
        "build_id": "${BUILD_ID}",
    }
gate_path.write_text(json.dumps(gate, indent=2) + "\n")
stop = {"stop": None, "utc": "${UTC_STAMP}", "run_id": "${RUN_ID}", "build_id": "${BUILD_ID}"}
tmp = workdir / "STOP.json.tmp"
auth = workdir / "STOP.json"
tmp.write_text(json.dumps(stop, indent=2) + "\n")
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
ensure_nonempty_log "${LOGDIR}/build_unit/real_dlsym.log" "gcc_helper_so_no_stderr"
ensure_nonempty_log "${LOGDIR}/build_unit/unit_link.log" "g++_unit_link_no_stderr"
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
if ! python3 "${ROOT}/tests/test_wait_dag_v4_9.py" 2>&1 | tee "${LOGDIR}/build_unit/pytest_v4_9.log"; then
  atomic_write_stop "STOP_UNIT_PYTEST_V49_FAILED"
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
from wait_dag_v4_9_intervention import C3_CONTRACT, C5_DIAG, c3_contract_sha
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
  "wait_dag_v4_9_sha256": sha(root/"wait_dag_v4_9_intervention.py"),
  "expected_kernel_object_sha256": "${EXPECTED_KERNEL_SHA}",
  "capture_contract": cc,
  "capture_contract_sha256": cc_sha,
  "c3_contract": C3_CONTRACT,
  "c3_contract_sha256": c3_contract_sha(),
  "c5_gate_boolean": C5_DIAG["C5_gate_boolean"],
  "C5_status": C5_DIAG["C5_status"],
  "C5_in_control_denominator": C5_DIAG["C5_in_control_denominator"],
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
if ! python3 "${ROOT}/wait_dag_v4_9_intervention.py" \
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
  cp "${PAIR_OUT}/path_external_proofs.json" "${LOGDIR}/controls/path_external_b1.json" 2>/dev/null || true
  set +e
  PAIR_OUT="${PAIR_OUT}" KERNEL_SHA="${KERNEL_SHA}" WORKDIR="${WORKDIR}" \
  RUN_ID="${RUN_ID}" BUILD_ID="${BUILD_ID}" UTC_STAMP="${UTC_STAMP}" \
  CAPTURE_SHA="${CAPTURE_SHA}" \
  python3 - <<'PY' | tee "${LOGDIR}/analysis/gate_eval.json"
import csv, json, os, sys
from pathlib import Path
pair_out = Path(os.environ["PAIR_OUT"])
rows = list(csv.DictReader((pair_out / "paired_effects_v4_9.csv").open())) if (pair_out / "paired_effects_v4_9.csv").exists() else []
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
  local pair_src="${PAIR_OUT:-${WORKDIR}/analysis/paired_all}"
  promote_package_artifacts "${pair_src}"
  mkdir -p "${MYPORTAL}/logs/${UTC_STAMP}"
  for f in paired_effects_v4_9.csv c3_preintervention_rank_pairs.csv c3_noise_envelope.csv \
    c5_path_diagnostics.csv control_effects_v4_9.csv capture_classification.csv run_ledger.csv \
    v4_9_summary.json path_external_proofs.json path_external_proofs.csv; do
    if [[ -f "${WORKDIR}/${f}" && -s "${WORKDIR}/${f}" ]]; then
      cp "${WORKDIR}/${f}" "${MYPORTAL}/"
    else
      echo "SKIPPED:missing_or_empty_${f} UTC=$(date -u +%Y%m%dT%H%M%SZ)" > "${MYPORTAL}/${f}.SKIPPED"
    fi
  done
  cp "${WORKDIR}/gate_summary.json" "${MYPORTAL}/" 2>/dev/null || true
  cp "${WORKDIR}/STOP.json" "${MYPORTAL}/" 2>/dev/null || true
  cp "${LOGDIR}/preflight/capture_contract.json" "${MYPORTAL}/" 2>/dev/null || true
  cp -a "${LOGDIR}/." "${MYPORTAL}/logs/${UTC_STAMP}/" 2>/dev/null || true
  python3 - <<PY | tee "${pkg_log}"
import hashlib, json, datetime
from pathlib import Path
myportal = Path("${MYPORTAL}")
workdir = Path("${WORKDIR}")
manifest = {"run_id": "${RUN_ID}", "build_id": "${BUILD_ID}", "files": []}
for base in (myportal, workdir):
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.name == "hash_manifest.json":
            continue
        rel = str(p.relative_to(base))
        if base == workdir and rel.startswith("runs/"):
            continue
        entry_path = f"workdir/{rel}" if base == workdir else rel
        if any(e["path"] == entry_path for e in manifest["files"]):
            continue
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        manifest["files"].append({"path": entry_path, "sha256": h.hexdigest(), "bytes": p.stat().st_size})
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
  for f in paired_effects_v4_9.csv c3_noise_envelope.csv control_effects_v4_9.csv \
    path_external_proofs.json path_external_proofs.csv; do
    if [[ -f "${WORKDIR}/${f}" && -s "${WORKDIR}/${f}" ]]; then
      cp "${WORKDIR}/${f}" "${MYPORTAL}/"
    fi
  done
  cp "${MYPORTAL}/hash_manifest.json" "${WORKDIR}/hash_manifest.json" 2>/dev/null || true
  cp "${MYPORTAL}/package_receipt.json" "${WORKDIR}/package_receipt.json" 2>/dev/null || true
}

if [[ "${GATE_RC}" -ne 0 ]]; then
  if [[ ! -f "${WORKDIR}/STOP.json" ]]; then
    atomic_write_stop "STOP_PAIRED_ANALYSIS_OR_GATE_FAILED"
  fi
  package_myportal
  exit "${GATE_RC}"
fi

run_block_pair() {
  local block="$1"
  local d0_tag="$2"
  local dl_tag="$3"
  local d0_dir="${WORKDIR}/runs/${d0_tag}"
  local dl_dir="${WORKDIR}/runs/${dl_tag}"
  local d0_run_id="${RUN_ID}_${d0_tag}"
  local dl_run_id="${RUN_ID}_${dl_tag}"
  local out_dir="${WORKDIR}/analysis/paired_${block}"
  local manifest="${WORKDIR}/manifest_${block}_pairs.json"
  local selector="${WORKDIR}/reverse_extract_${block}/selector_manifest_${block}.json"

  log "Block ${block}: D0=${d0_tag} Dlarge=${dl_tag}"
  run_train "${d0_tag}" 0
  mkdir -p "${WORKDIR}/reverse_extract_${block}"
  if ! python3 "${ROOT}/wait_dag_v4_2_reverse_candidate.py" \
    --run-dir "${d0_dir}" \
    --run-id "${d0_run_id}" \
    --out-dir "${WORKDIR}/reverse_extract_${block}" \
    --target-comm "${TARGET_COMM}" \
    --block "${block}" \
    2>&1 | tee "${LOGDIR}/projection/reverse_extract_${block}.log"; then
    atomic_write_stop "STOP_REVERSE_CANDIDATE_NOT_UNIQUE"
    return 9
  fi
  export ACL_EVENT_SELECTOR_MANIFEST="${selector}"
  run_train "${dl_tag}" "${DLARGE_ITERS}"

  python3 - <<PY > "${manifest}"
import json
print(json.dumps({
  "runs":[
    {"run_id":"${d0_run_id}","condition":"D0","run_dir":"${d0_dir}"},
    {"run_id":"${dl_run_id}","condition":"Dlarge","run_dir":"${dl_dir}"},
  ],
  "pairs":[["${block}_dlarge","${d0_run_id}","${dl_run_id}"]],
  "selector_manifest":"${selector}",
}))
PY

  if ! python3 "${ROOT}/wait_dag_v4_9_intervention.py" \
    --manifest "${manifest}" \
    --selector-manifest "${selector}" \
    --out-dir "${out_dir}" \
    --capture-contract-sha "${CAPTURE_SHA}" \
    --preflight-capture-sha "${CAPTURE_SHA}" \
    2>&1 | tee "${LOGDIR}/analysis/analyze_${block}.log"; then
    atomic_write_stop "STOP_PAIRED_ANALYSIS_FAILED"
    return 7
  fi

  cp "${out_dir}/capture_classification.csv" "${LOGDIR}/capture_classification/" 2>/dev/null || true
  cp "${out_dir}/path_external_proofs.json" "${LOGDIR}/controls/path_external_${block}.json" 2>/dev/null || true
  cp "${out_dir}/intervention_identity.csv" "${LOGDIR}/identity/" 2>/dev/null || true

  set +e
  PAIR_OUT="${out_dir}" BLOCK="${block}" WORKDIR="${WORKDIR}" \
  RUN_ID="${RUN_ID}" BUILD_ID="${BUILD_ID}" UTC_STAMP="${UTC_STAMP}" \
  KERNEL_SHA="${KERNEL_SHA}" \
  python3 - <<'PY'
import csv, json, os, sys
from pathlib import Path
pair_out = Path(os.environ["PAIR_OUT"])
block = os.environ["BLOCK"]
rows = list(csv.DictReader((pair_out / "paired_effects_v4_9.csv").open()))
cls_rows = list(csv.DictReader((pair_out / "capture_classification.csv").open()))
gate = {"block": block, "stop": None, "dlarge_pass": False}
for cr in cls_rows:
    if cr.get("pre_wait_p_status") != "STRUCTURAL_NA":
        gate["stop"] = cr.get("pre_wait_p_status") or "STOP_PRE_WAIT_P_CLASSIFICATION_FAILED"
        gate["fail_run"] = cr.get("run_id")
        break
else:
    for r in rows:
        if r.get("block") != f"{block}_dlarge":
            continue
        if r.get("structure_gate_pass") != "True":
            gate["stop"] = "STOP_STRUCTURE_GATE_FAILED"
            break
        if r.get("dose_gate_pass") != "True":
            gate["stop"] = "STOP_DOSE_GATE_FAILED"
            break
        if r.get("control_gate_pass") == "False":
            gate["stop"] = "STOP_CONTROL_GATE_FAILED"
            gate["control_reason"] = r.get("control_reason")
            break
        if r.get("local_causal_gate_pass") != "True":
            gate["stop"] = "STOP_COMM_LOCAL_CAUSAL_NOT_REALIZED"
            break
        gate["dlarge_pass"] = True
        break
    else:
        gate["stop"] = "STOP_PAIRED_ANALYSIS_OR_GATE_FAILED"

print(json.dumps(gate, indent=2))
workdir = Path(os.environ["WORKDIR"])
workdir.joinpath("gate_summary.json").write_text(json.dumps(gate, indent=2) + "\n")
if gate.get("stop"):
    stop = {
        "stop": gate["stop"],
        "utc": os.environ["UTC_STAMP"],
        "run_id": os.environ["RUN_ID"],
        "build_id": os.environ["BUILD_ID"],
        "fail_block": block,
        "kernel_sha256": os.environ["KERNEL_SHA"],
    }
    if gate.get("control_reason"):
        stop["control_reason"] = gate.get("control_reason")
    if gate.get("fail_run"):
        stop["fail_run"] = gate.get("fail_run")
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
  local rc=${PIPESTATUS[0]}
  set -e
  PAIR_OUT="${out_dir}"
  return "${rc}"
}

log "Step 6 b2/b3 (after b1 pass)"
GATE_RC=0
run_block_pair "b2" "D0_b2" "Dlarge_b2" || GATE_RC=$?
if [[ "${GATE_RC}" -ne 0 ]]; then
  if [[ ! -f "${WORKDIR}/STOP.json" ]]; then
    atomic_write_stop "STOP_PAIRED_ANALYSIS_OR_GATE_FAILED"
  fi
  package_myportal
  exit "${GATE_RC}"
fi
run_block_pair "b3" "D0_b3" "Dlarge_b3" || GATE_RC=$?
if [[ "${GATE_RC}" -ne 0 ]]; then
  if [[ ! -f "${WORKDIR}/STOP.json" ]]; then
    atomic_write_stop "STOP_PAIRED_ANALYSIS_OR_GATE_FAILED"
  fi
  package_myportal
  exit "${GATE_RC}"
fi

log "Step 7 dlarge 3/3 gate"
MANIFEST_ALL="${WORKDIR}/manifest_all_pairs.json"
python3 - <<PY > "${MANIFEST_ALL}"
import json
runs = [
  {"run_id":"${D0_RUN_ID}","condition":"D0","run_dir":"${D0_DIR}"},
  {"run_id":"${DSMALL_RUN_ID}","condition":"Dsmall","run_dir":"${WORKDIR}/runs/Dsmall_b1"},
  {"run_id":"${DLARGE_RUN_ID}","condition":"Dlarge","run_dir":"${WORKDIR}/runs/Dlarge_b1"},
  {"run_id":"${RUN_ID}_D0_b2","condition":"D0","run_dir":"${WORKDIR}/runs/D0_b2"},
  {"run_id":"${RUN_ID}_Dlarge_b2","condition":"Dlarge","run_dir":"${WORKDIR}/runs/Dlarge_b2"},
  {"run_id":"${RUN_ID}_D0_b3","condition":"D0","run_dir":"${WORKDIR}/runs/D0_b3"},
  {"run_id":"${RUN_ID}_Dlarge_b3","condition":"Dlarge","run_dir":"${WORKDIR}/runs/Dlarge_b3"},
]
pairs = [
  ["b1_dsmall","${D0_RUN_ID}","${DSMALL_RUN_ID}"],
  ["b1_dlarge","${D0_RUN_ID}","${DLARGE_RUN_ID}"],
  ["b2_dlarge","${RUN_ID}_D0_b2","${RUN_ID}_Dlarge_b2"],
  ["b3_dlarge","${RUN_ID}_D0_b3","${RUN_ID}_Dlarge_b3"],
]
print(json.dumps({"runs": runs, "pairs": pairs, "selector_manifest":"${SELECTOR_MANIFEST}"}))
PY
PAIR_OUT="${WORKDIR}/analysis/paired_all"
if ! python3 "${ROOT}/wait_dag_v4_9_intervention.py" \
  --manifest "${MANIFEST_ALL}" \
  --selector-manifest "${SELECTOR_MANIFEST}" \
  --out-dir "${PAIR_OUT}" \
  --capture-contract-sha "${CAPTURE_SHA}" \
  --preflight-capture-sha "${CAPTURE_SHA}" \
  2>&1 | tee "${LOGDIR}/analysis/analyze_all.log"; then
  atomic_write_stop "STOP_PAIRED_ANALYSIS_FAILED"
  package_myportal
  exit 7
fi
cp "${PAIR_OUT}/path_external_proofs.json" "${LOGDIR}/controls/path_external_all.json" 2>/dev/null || true
cp "${PAIR_OUT}/capture_classification.csv" "${LOGDIR}/capture_classification/" 2>/dev/null || true

set +e
PAIR_OUT="${PAIR_OUT}" KERNEL_SHA="${KERNEL_SHA}" WORKDIR="${WORKDIR}" \
RUN_ID="${RUN_ID}" BUILD_ID="${BUILD_ID}" UTC_STAMP="${UTC_STAMP}" \
CAPTURE_SHA="${CAPTURE_SHA}" \
python3 - <<'PY' | tee "${LOGDIR}/analysis/gate_eval_final.json"
import csv, json, os, sys
from pathlib import Path

pair_out = Path(os.environ["PAIR_OUT"])
rows = list(csv.DictReader((pair_out / "paired_effects_v4_9.csv").open()))
cls_rows = list(csv.DictReader((pair_out / "capture_classification.csv").open()))
gate = {
    "unit_pass": True,
    "smoke_pass": True,
    "kernel_sha256": os.environ["KERNEL_SHA"],
    "capture_contract_sha256": os.environ["CAPTURE_SHA"],
    "pairs": rows,
    "classification": cls_rows,
    "b1_protocol_pass": False,
    "b2_dlarge_pass": False,
    "b3_dlarge_pass": False,
    "dlarge_3_of_3_pass": False,
    "stop": None,
}
for cr in cls_rows:
    if cr.get("pre_wait_p_status") != "STRUCTURAL_NA":
        gate["stop"] = cr.get("pre_wait_p_status") or "STOP_PRE_WAIT_P_CLASSIFICATION_FAILED"
        gate["fail_run"] = cr.get("run_id")
        break
else:
    dlarge_blocks = {}
    b1_dsmall_ok = True
    for r in rows:
        block = r.get("block")
        if block == "b1_dsmall":
            if r.get("structure_gate_pass") != "True":
                gate["stop"] = "STOP_STRUCTURE_GATE_FAILED"
                gate["fail_block"] = block
                b1_dsmall_ok = False
                break
            if r.get("dose_gate_pass") != "True":
                gate["stop"] = "STOP_DOSE_GATE_FAILED"
                gate["fail_block"] = block
                b1_dsmall_ok = False
                break
            if r.get("control_gate_pass") == "False":
                gate["stop"] = "STOP_CONTROL_GATE_FAILED"
                gate["fail_block"] = block
                b1_dsmall_ok = False
                break
            if r.get("causal_eligibility") == "STRUCTURE_ONLY_NOT_CAUSAL":
                gate["dsmall_structure_only"] = True
    if not gate.get("stop"):
        for r in rows:
            block = r.get("block")
            if block in ("b1_dlarge", "b2_dlarge", "b3_dlarge"):
                ok = (
                    r.get("structure_gate_pass") == "True"
                    and r.get("dose_gate_pass") == "True"
                    and r.get("control_gate_pass") != "False"
                    and r.get("local_causal_gate_pass") == "True"
                )
                dlarge_blocks[block] = ok
                if not ok:
                    gate["stop"] = "STOP_COMM_LOCAL_CAUSAL_NOT_REALIZED"
                    gate["fail_block"] = block
                    break
    if not gate.get("stop"):
        gate["b2_dlarge_pass"] = dlarge_blocks.get("b2_dlarge", False)
        gate["b3_dlarge_pass"] = dlarge_blocks.get("b3_dlarge", False)
        if all(dlarge_blocks.get(f"b{i}_dlarge") for i in (1, 2, 3)) and b1_dsmall_ok:
            gate["dlarge_3_of_3_pass"] = True
            gate["b1_protocol_pass"] = True
        else:
            gate["stop"] = "STOP_DLARGE_CAUSAL_NOT_PROPAGATED"
            gate["fail_block"] = "dlarge_3_of_3"

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

if [[ "${GATE_RC}" -ne 0 ]]; then
  if [[ ! -f "${WORKDIR}/STOP.json" ]]; then
    atomic_write_stop "STOP_PAIRED_ANALYSIS_OR_GATE_FAILED"
  fi
  package_myportal
  exit "${GATE_RC}"
fi
write_stop_null
package_myportal

log "BUILD_V4_9_COMPLETE RUN_ID=${RUN_ID} KERNEL_SHA=${KERNEL_SHA}"
