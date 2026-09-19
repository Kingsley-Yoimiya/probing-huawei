#!/usr/bin/env bash
# D51 Wait DAG V4.4: GM reachability -> TASK projection -> scaling -> dose -> b1.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BUILD_ID="${BUILD_ID:-${UTC_STAMP}_build_v4_4}"
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_wait_dag_v4_4_reachable_kernel}"
WORKDIR="/tmp/${RUN_ID}"
LOGDIR="${WORKDIR}/logs/${UTC_STAMP}"
MYPORTAL="/workspace/myportal/results/npu-dev-1/${RUN_ID}"
TARGET_COMM="hcom_allReduce__612_0_1"
D0_V41_RUN_ID="20260824T112527Z_d51_wait_dag_v4_1_device_work"
D0_V41_DIR="/tmp/${D0_V41_RUN_ID}/control_d0_b1"
KERNEL_SRC="${ROOT}/kernels/d51_compute_delay_kernel.cpp"
KERNEL_O="${ROOT}/build/kernels/d51_compute_delay_kernel.o"
KERNEL_TMP="${ROOT}/build/kernels/d51_compute_delay_kernel_tmp.o"
NEG_O="${ROOT}/build/kernels/d51_compute_delay_kernel_audit_neg.o"
DISASM="${ROOT}/build/kernels/d51_compute_delay_kernel.disasm.txt"
NEG_DISASM="${ROOT}/build/kernels/d51_compute_delay_kernel_audit_neg.disasm.txt"
BUILD_DIR="${ROOT}/build"
HELPER_SO="${BUILD_DIR}/libacl_real_dlsym.so"
V2_SO="${BUILD_DIR}/libacl_event_trace_v2.so"
PRELOAD_ASCEND="${HELPER_SO}:${V2_SO}"
EST_TRAIN_S=180
PID_FILE="${LOGDIR}/runner.pid"
SEAL_JSON="${WORKDIR}/binary_seal.json"

mkdir -p \
  "${LOGDIR}/preflight" \
  "${LOGDIR}/kernel_build" \
  "${LOGDIR}/reachability" \
  "${LOGDIR}/task_projection" \
  "${LOGDIR}/profiler_scaling" \
  "${LOGDIR}/dose_freeze" \
  "${LOGDIR}/runs" \
  "${LOGDIR}/analysis" \
  "${LOGDIR}/sync" \
  "${LOGDIR}/package" \
  "${MYPORTAL}"

exec > >(tee -a "${LOGDIR}/runner.log") 2>&1
echo "$$" > "${PID_FILE}"
echo "RUN_ID=${RUN_ID} BUILD_ID=${BUILD_ID} PID=$$ PGID=$(ps -o pgid= $$ | tr -d ' ')"
echo "expected_runtime: build+reachability 15-30min; scaling+freeze 20-45min; b1 train 25-180s each"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

sha256_file() { sha256sum "$1" | awk '{print $1}'; }

STOP_REASON=""
write_stop() {
  STOP_REASON="$1"
  python3 - <<PY
import json, hashlib
from pathlib import Path

def sha(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for c in iter(lambda:f.read(1<<20), b''): h.update(c)
    return h.hexdigest()

seal_path=Path("${SEAL_JSON}")
seal=json.loads(seal_path.read_text()) if seal_path.exists() else {}
stop={
  "stop":"${STOP_REASON}",
  "utc":"${UTC_STAMP}",
  "run_id":"${RUN_ID}",
  "build_id":"${BUILD_ID}",
  "binary_seal": seal,
  "final_sha256": seal.get("final",{}).get("sha256"),
  "source_sha256": seal.get("source",{}).get("sha256"),
  "text_bytes": seal.get("final",{}).get("text_bytes"),
  "disasm_sha256": seal.get("disasm",{}).get("sha256"),
}
Path("${WORKDIR}/STOP.json").write_text(json.dumps(stop, indent=2)+"\n")
print(json.dumps(stop, indent=2))
PY
  log "STOP ${STOP_REASON}"
}

# --- Step 1: preflight ---
log "Step 1 preflight"
{
  echo "=== container preflight ==="
  npu-smi info -t board -i 0 2>/dev/null | head -8 || true
  pgrep -a vllm | head -3 || echo "vllm_not_listed"
  uname -a
  whoami
} | tee "${LOGDIR}/preflight/snapshot.txt"

python3 - <<PY | tee "${LOGDIR}/preflight/hash_manifest.json"
import hashlib, json, os
from pathlib import Path
root = Path("${ROOT}")
def sha(p):
    if not p.exists(): return None
    h=hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1<<20), b""): h.update(c)
    return h.hexdigest()
print(json.dumps({
  "run_id":"${RUN_ID}",
  "build_id":"${BUILD_ID}",
  "utc":"${UTC_STAMP}",
  "pid": os.getpid(),
  "sources": {
    "kernel_cpp": sha(root/"kernels/d51_compute_delay_kernel.cpp"),
    "device_work": sha(root/"device_work.cpp"),
    "reference_py": sha(root/"d51_work_unit_reference.py"),
    "run_script": sha(root/"run_d51_wait_dag_v4_4.sh"),
  },
}, indent=2))
PY

# --- Step 2-3: build kernel + binary seal ---
log "Step 2-3 kernel build + disasm gate"
if [[ -f /usr/local/Ascend/ascend-toolkit/latest/set_env.sh ]]; then
  # shellcheck disable=SC1091
  source /usr/local/Ascend/ascend-toolkit/latest/set_env.sh
fi
chmod +x "${ROOT}/kernels/build_kernel.sh" "${ROOT}/build.sh"
if ! "${ROOT}/kernels/build_kernel.sh" 2>&1 | tee "${LOGDIR}/kernel_build/build_kernel.log"; then
  if grep -q "STOP_KERNEL_ANTI_ELIMINATION_UNSUPPORTED" "${LOGDIR}/kernel_build/build_kernel.log"; then
    write_stop "STOP_KERNEL_ANTI_ELIMINATION_UNSUPPORTED"
  else
    write_stop "STOP_KERNEL_BUILD_FAILED"
  fi
  exit 3
fi

TEXT_SIZE=$(readelf -SW "${KERNEL_O}" | awk '/\.text/ {print $6; exit}')
python3 - <<PY > "${SEAL_JSON}"
import hashlib, json, os, subprocess
from pathlib import Path

def sha(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for c in iter(lambda:f.read(1<<20), b''): h.update(c)
    return h.hexdigest()

def stat_entry(p):
    p=Path(p)
    return {"path": str(p.resolve()), "bytes": p.stat().st_size, "mtime": p.stat().st_mtime, "sha256": sha(p)}

seal={
  "build_id": "${BUILD_ID}",
  "run_id": "${RUN_ID}",
  "utc": "${UTC_STAMP}",
  "source": stat_entry("${KERNEL_SRC}"),
  "build_script": stat_entry("${ROOT}/kernels/build_kernel.sh"),
  "tmp": stat_entry("${KERNEL_TMP}"),
  "final": {**stat_entry("${KERNEL_O}"), "text_bytes": int("${TEXT_SIZE}", 16)},
  "neg_final": stat_entry("${NEG_O}"),
  "disasm": stat_entry("${DISASM}"),
  "neg_disasm": stat_entry("${NEG_DISASM}"),
  "compile_command": "ccec -O2 --cce-aicore-only + ld.lld -m aicorelinux",
  "entry_symbol": "d51_compute_delay_kernel",
}
print(json.dumps(seal, indent=2))
PY

python3 "${ROOT}/kernel_disasm_audit_v4_4.py" \
  --source "${KERNEL_SRC}" \
  --final "${KERNEL_O}" \
  --neg-final "${NEG_O}" \
  --disasm "${DISASM}" \
  --neg-disasm "${NEG_DISASM}" \
  --out-json "${LOGDIR}/kernel_build/disasm_audit.json" \
  2>&1 | tee "${LOGDIR}/kernel_build/disasm_audit.log" || {
  STOP=$(python3 -c "import json; print(json.load(open('${LOGDIR}/kernel_build/disasm_audit.json')).get('stop','STOP_KERNEL_BODY_NOT_PRESENT'))")
  write_stop "${STOP}"
  exit 3
}

{
  echo "=== readelf gate ==="
  file "${KERNEL_TMP}" "${KERNEL_O}"
  readelf -h "${KERNEL_TMP}" | grep -E 'Type:'
  readelf -h "${KERNEL_O}" | grep -E 'Type:'
  readelf -SW "${KERNEL_O}" | grep -E 'ascend\.meta'
  readelf -Ws "${KERNEL_O}" | grep d51_compute_delay_kernel
} | tee "${LOGDIR}/kernel_build/elf_inspect.txt"
file "${KERNEL_TMP}" | grep -q "relocatable" || { write_stop "ARTIFACT_TMP_NOT_ET_REL"; exit 3; }
readelf -h "${KERNEL_TMP}" | grep -q "Type:.*REL" || { write_stop "ARTIFACT_TMP_NOT_ET_REL"; exit 3; }
readelf -h "${KERNEL_O}" | grep -q "Type:.*EXEC" || { write_stop "ARTIFACT_FINAL_NOT_ET_EXEC"; exit 3; }
readelf -SW "${KERNEL_O}" | grep -q "ascend.meta.d51_compute_delay_kernel" || { write_stop "ARTIFACT_META_MISSING"; exit 3; }

python3 - "${KERNEL_O}" "${LOGDIR}/kernel_build/load_probe.json" <<'PY' | tee "${LOGDIR}/kernel_build/load_probe.log"
import ctypes, json, sys
from pathlib import Path
final_path = sys.argv[1]
out_path = Path(sys.argv[2])
acl = ctypes.CDLL("libascendcl.so")
acl.aclInit(None)
acl.aclrtSetDevice(0)
ctx = ctypes.c_void_p()
acl.aclrtCreateContext(ctypes.byref(ctx), 0)
handle = ctypes.c_void_p()
load_rc = acl.aclrtBinaryLoadFromFile(final_path.encode(), None, ctypes.byref(handle))
func = ctypes.c_void_p()
get_rc = -1
if load_rc == 0:
    get_rc = acl.aclrtBinaryGetFunction(handle, b"d51_compute_delay_kernel", ctypes.byref(func))
out = {"FINAL": {"path": final_path, "load_rc": int(load_rc), "get_function_rc": int(get_rc)}}
out_path.write_text(json.dumps(out, indent=2) + "\n")
print(json.dumps(out, indent=2))
if load_rc != 0 or get_rc != 0:
    sys.exit(3)
PY

LOADED_SHA=$(sha256_file "${KERNEL_O}")
SEAL_SHA=$(python3 -c "import json; print(json.load(open('${SEAL_JSON}'))['final']['sha256'])")
if [[ "${LOADED_SHA}" != "${SEAL_SHA}" ]]; then
  write_stop "STOP_BINARY_SEAL_MISMATCH"
  exit 3
fi
log "KERNEL_SHA=${LOADED_SHA} text=${TEXT_SIZE}"

# --- D0 selector ---
log "D0_b1 reverse witness"
if [[ ! -d "${D0_V41_DIR}" ]]; then
  write_stop "D0_B1_MISSING"
  exit 4
fi
OUT_DIR="${WORKDIR}/reverse_extract_b1"
python3 "${ROOT}/wait_dag_v4_2_reverse_candidate.py" \
  --run-dir "${D0_V41_DIR}" \
  --run-id "${D0_V41_RUN_ID}_control_d0_b1" \
  --out-dir "${OUT_DIR}" \
  --target-comm "${TARGET_COMM}" \
  --block b1 \
  2>&1 | tee "${LOGDIR}/analysis/reverse_extract.log" || {
  write_stop "STOP_REVERSE_CANDIDATE_NOT_UNIQUE"
  exit 5
}
SELECTOR_MANIFEST="${OUT_DIR}/selector_manifest_b1.json"

# --- build preload ---
./build.sh ascend 2>&1 | tee "${LOGDIR}/kernel_build/build_preload.log"
export LD_PRELOAD="${PRELOAD_ASCEND}"

# --- Step 4: GM reachability ---
log "Step 4 GM reachability"
REACH_DIR="${LOGDIR}/reachability"
mkdir -p "${REACH_DIR}/trace"
if ! python3 "${ROOT}/smoke_reachability_v4_4.py" \
  --trace-dir "${REACH_DIR}/trace" \
  --preload-lib "${V2_SO}" \
  --kernel-binary "${KERNEL_O}" \
  --selector-manifest "${SELECTOR_MANIFEST}" \
  --out-json "${REACH_DIR}/reachability_proofs.json" \
  --iters 1 2 17 257 \
  2>&1 | tee "${REACH_DIR}/reachability.log"; then
  write_stop "STOP_KERNEL_GM_REACHABILITY_FAILED"
  exit 3
fi
cp "${REACH_DIR}/reachability_proofs.json" "${WORKDIR}/reachability_proofs.json"

# --- Step 5: TASK projection ---
log "Step 5 TASK projection"
PROJ_DIR="${LOGDIR}/task_projection/probe_0"
if ! python3 "${ROOT}/task_projection_v4_4.py" \
  --probe-dir "${PROJ_DIR}" \
  --preload-lib "${V2_SO}" \
  --kernel-binary "${KERNEL_O}" \
  --selector-manifest "${SELECTOR_MANIFEST}" \
  --out-json "${LOGDIR}/task_projection/task_projection.json" \
  --timeout-s 240 \
  2>&1 | tee "${LOGDIR}/task_projection/task_projection.log"; then
  write_stop "STOP_KERNEL_TASK_PROJECTION_NOT_UNIQUE"
  exit 3
fi
python3 - <<PY > "${WORKDIR}/reachability_gate.json"
import json
from pathlib import Path
proof=json.load(open("${WORKDIR}/reachability_proofs.json"))
proj=json.load(open("${LOGDIR}/task_projection/task_projection.json"))
out={
  "reachability_gate_pass": bool(proof.get("pass")) and bool(proj.get("pass")),
  "gm_proof_pass": proof.get("pass"),
  "task_projection_pass": proj.get("pass"),
  "build_id":"${BUILD_ID}",
  "final_sha256":"${LOADED_SHA}",
}
Path("${WORKDIR}/reachability_gate.json").write_text(json.dumps(out, indent=2)+"\n")
print(json.dumps(out, indent=2))
PY

# --- D0 S for dose ---
D0_MANIFEST="${WORKDIR}/manifest_d0_b1.json"
python3 - <<PY > "${D0_MANIFEST}"
import json
print(json.dumps({
  "runs":[{"run_id":"${D0_V41_RUN_ID}_control_d0_b1","condition":"D0","run_dir":"${D0_V41_DIR}"}],
  "pairs":[],
  "selector_manifest":"${SELECTOR_MANIFEST}",
}))
PY
python3 "${ROOT}/wait_dag_v4_intervention.py" \
  --manifest "${D0_MANIFEST}" \
  --selector-manifest "${SELECTOR_MANIFEST}" \
  --out-dir "${WORKDIR}/analysis/d0_b1" \
  2>&1 | tee "${LOGDIR}/analysis/analyze_d0.log"
D0_NWC="${WORKDIR}/analysis/d0_b1/node_wallclock.csv"
S_NS=$(python3 - <<PY
import csv
rows=list(csv.DictReader(open("${D0_NWC}")))
rec=next(x for x in rows if x["node"]=="record_task")
ce=next(x for x in rows if x["node"]=="comm_entry")
print(int(ce["start_offset_from_upstream_kernel_end_ns"]) - int(rec["end_offset_from_upstream_kernel_end_ns"]))
PY
)
log "D0_b1 S_record_to_comm_ns=${S_NS}"

# --- Step 6-7: scaling + dose freeze ---
log "Step 6-7 profiler scaling + dose freeze"
DOSE_PROBE="${WORKDIR}/dose_probe_${UTC_STAMP}"
DOSE_JSON="${WORKDIR}/dose_iters_v4_4.json"
if ! python3 "${ROOT}/dose_calibrate_v4_4.py" \
  --d0-node-wallclock "${D0_NWC}" \
  --out-json "${DOSE_JSON}" \
  --probe-root "${DOSE_PROBE}" \
  --preload-lib "${V2_SO}" \
  --kernel-binary "${KERNEL_O}" \
  --selector-manifest "${SELECTOR_MANIFEST}" \
  --kernel-binary-sha256 "${LOADED_SHA}" \
  --timeout-s 300 \
  --mode full \
  2>&1 | tee "${LOGDIR}/profiler_scaling/dose_calibrate.log"; then
  STATUS=$(python3 -c "import json; print(json.load(open('${DOSE_JSON}')).get('status','STOP_DOSE_KERNEL_SCALING_UNAVAILABLE'))" 2>/dev/null || echo STOP_DOSE_KERNEL_SCALING_UNAVAILABLE)
  write_stop "${STATUS}"
  cp "${DOSE_JSON}" "${MYPORTAL}/" 2>/dev/null || true
  exit 6
fi

python3 - <<PY > "${WORKDIR}/frozen_dose_v4_4.json"
import json
from pathlib import Path
d=json.load(open("${DOSE_JSON}"))
seal=json.load(open("${SEAL_JSON}"))
gate=json.load(open("${WORKDIR}/reachability_gate.json"))
frozen={
  **d,
  "frozen_utc":"${UTC_STAMP}",
  "build_id":"${BUILD_ID}",
  "kernel_binary_sha256":"${LOADED_SHA}",
  "source_sha256": seal["source"]["sha256"],
  "disasm_sha256": seal["disasm"]["sha256"],
  "reachability_gate": gate,
  "block_dim":1,
  "scratch_bytes":1024,
}
Path("${WORKDIR}/frozen_dose_v4_4.json").write_text(json.dumps(frozen, indent=2)+"\n")
print(json.dumps({"Dsmall_iters": frozen.get("Dsmall_iters"), "Dlarge_iters": frozen.get("Dlarge_iters")}, indent=2))
PY
DSMALL=$(python3 -c "import json; print(json.load(open('${WORKDIR}/frozen_dose_v4_4.json'))['Dsmall_iters'])")
DLARGE=$(python3 -c "import json; print(json.load(open('${WORKDIR}/frozen_dose_v4_4.json'))['Dlarge_iters'])")
log "FROZEN Dsmall=${DSMALL} Dlarge=${DLARGE}"

# --- Step 8: fresh b1 ---
run_train() {
  local tag="$1" iters="$2"
  local wdir="${WORKDIR}/runs/${tag}"
  mkdir -p "${wdir}/event_trace" "${wdir}/out"
  export ACL_EVENT_TRACE_DIR="${wdir}/event_trace"
  export ACL_EVENT_WORK_ITERS="${iters}"
  export ACL_EVENT_WORK_BINARY="${KERNEL_O}"
  export ACL_EVENT_SELECTOR_MANIFEST="${SELECTOR_MANIFEST}"
  export LD_PRELOAD="${PRELOAD_ASCEND}"
  local port=$((29500 + RANDOM % 1000))
  log "TRAIN ${tag} iters=${iters} port=${port} pid=$$"
  timeout "${EST_TRAIN_S}" torchrun --nproc_per_node=16 --master_port="${port}" \
    "${ROOT}/train_event_preload.py" \
    --output "${wdir}/out" \
    --trace-dir "${wdir}/event_trace" \
    --preload-lib "${V2_SO}" \
    --profiler-level Level1 \
    --dim 4096 --batch 256 --warmup 2 --steps 3 \
    2>&1 | tee "${LOGDIR}/runs/train_${tag}.log"
}

SMALL_TAG="Dsmall_v44_b1"
DLARGE_TAG="Dlarge_v44_b1"
log "Step 8 b1 treatments"
run_train "${SMALL_TAG}" "${DSMALL}"
run_train "${DLARGE_TAG}" "${DLARGE}"

write_manifest() { python3 - <<PY > "$1"
import json; print(json.dumps($2))
PY
}
SMALL_RUN_ID="${RUN_ID}_${SMALL_TAG}"
DLARGE_RUN_ID="${RUN_ID}_${DLARGE_TAG}"
PAIR_MANIFEST="${WORKDIR}/manifest_b1_pairs.json"
write_manifest "${PAIR_MANIFEST}" "{
  \"runs\":[
    {\"run_id\":\"${D0_V41_RUN_ID}_control_d0_b1\",\"condition\":\"D0\",\"run_dir\":\"${D0_V41_DIR}\"},
    {\"run_id\":\"${SMALL_RUN_ID}\",\"condition\":\"Dsmall\",\"run_dir\":\"${WORKDIR}/runs/${SMALL_TAG}\"},
    {\"run_id\":\"${DLARGE_RUN_ID}\",\"condition\":\"Dlarge\",\"run_dir\":\"${WORKDIR}/runs/${DLARGE_TAG}\"}
  ],
  \"pairs\":[
    [\"b1_dsmall\",\"${D0_V41_RUN_ID}_control_d0_b1\",\"${SMALL_RUN_ID}\"],
    [\"b1_dlarge\",\"${D0_V41_RUN_ID}_control_d0_b1\",\"${DLARGE_RUN_ID}\"]
  ],
  \"selector_manifest\":\"${SELECTOR_MANIFEST}\"
}"
PAIR_OUT="${WORKDIR}/analysis/paired_b1"
python3 "${ROOT}/wait_dag_v4_intervention.py" \
  --manifest "${PAIR_MANIFEST}" \
  --selector-manifest "${SELECTOR_MANIFEST}" \
  --out-dir "${PAIR_OUT}" \
  2>&1 | tee "${LOGDIR}/analysis/analyze_b1_pairs.log"

B1_CAUSAL_PASS=$(python3 - <<PY
import csv
rows=list(csv.DictReader(open("${PAIR_OUT}/paired_effects.csv")))
causal=[r for r in rows if r.get("legacy_illegal_dose","False").lower()!="true"]
ok=all(r.get("causal_gate_pass")=="True" for r in causal if r.get("dose_gate_pass")=="True")
print("1" if ok and len(causal)==2 else "0")
for r in rows:
    print("PAIR", r["block"], "struct", r.get("structure_gate_pass"), "dose", r.get("dose_gate_pass"), "causal", r.get("causal_gate_pass"))
PY
)
log "b1_causal_all_pass=${B1_CAUSAL_PASS}"
if [[ "${B1_CAUSAL_PASS}" != "1" ]]; then
  write_stop "B1_CAUSAL_GATE_FAIL"
fi

# package
cp "${SEAL_JSON}" "${WORKDIR}/" 
cp "${DOSE_JSON}" "${MYPORTAL}/" 2>/dev/null || true
cp "${WORKDIR}/frozen_dose_v4_4.json" "${MYPORTAL}/" 2>/dev/null || true
cp "${WORKDIR}/reachability_proofs.json" "${MYPORTAL}/" 2>/dev/null || true
cp "${LOGDIR}/task_projection/task_projection.json" "${MYPORTAL}/" 2>/dev/null || true
cp -a "${LOGDIR}" "${MYPORTAL}/logs/${UTC_STAMP}/" 2>/dev/null || true
log "DONE V4.4 MYPORTAL=${MYPORTAL} STOP=${STOP_REASON:-none}"
