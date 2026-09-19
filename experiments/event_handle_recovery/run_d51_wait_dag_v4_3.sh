#!/usr/bin/env bash
# D51 Wait DAG V4.3: scalable kernel + profiler curve + dose freeze + b1 gate.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_wait_dag_v4_3_scalable_device_work}"
WORKDIR="/tmp/${RUN_ID}"
LOGDIR="${WORKDIR}/logs/${UTC_STAMP}"
MYPORTAL="/workspace/myportal/results/npu-dev-1/${RUN_ID}"
TARGET_COMM="hcom_allReduce__612_0_1"
D0_V42_RUN_ID="20260824T112527Z_d51_wait_dag_v4_1_device_work"
D0_V42_DIR="/tmp/${D0_V42_RUN_ID}/control_d0_b1"
KERNEL_O="${ROOT}/build/kernels/d51_compute_delay_kernel.o"
BUILD_DIR="${ROOT}/build"
HELPER_SO="${BUILD_DIR}/libacl_real_dlsym.so"
V2_SO="${BUILD_DIR}/libacl_event_trace_v2.so"
PRELOAD_ASCEND="${HELPER_SO}:${V2_SO}"
EST_TRAIN_S=180
PID_FILE="${LOGDIR}/runner.pid"

mkdir -p \
  "${LOGDIR}/preflight" \
  "${LOGDIR}/kernel_build" \
  "${LOGDIR}/kernel_unit" \
  "${LOGDIR}/profiler_curve" \
  "${LOGDIR}/dose_freeze" \
  "${LOGDIR}/runs" \
  "${LOGDIR}/analysis" \
  "${LOGDIR}/sync" \
  "${LOGDIR}/package" \
  "${MYPORTAL}"

exec > >(tee -a "${LOGDIR}/runner.log") 2>&1
echo "$$" > "${PID_FILE}"
echo "RUN_ID=${RUN_ID} PID=$$ PGID=$(ps -o pgid= $$ | tr -d ' ')"
echo "expected_runtime: kernel_build/unit 10-20min; curve+freeze 15-35min; b1 train 25-180s each"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

sha256_file() {
  sha256sum "$1" | awk '{print $1}'
}

STOP_REASON=""
write_stop() {
  STOP_REASON="$1"
  echo "{\"stop\":\"${STOP_REASON}\",\"utc\":\"${UTC_STAMP}\",\"run_id\":\"${RUN_ID}\"}" > "${WORKDIR}/STOP.json"
  log "STOP ${STOP_REASON}"
}

# --- Step 1: preflight hash/PID snapshot ---
log "Step 1 preflight"
{
  echo "=== container preflight ==="
  npu-smi info -t board -i 0 2>/dev/null | head -8 || true
  pgrep -a vllm | head -3 || echo "vllm_not_listed"
  sha256sum "${ROOT}/kernels/d51_compute_delay_kernel.cpp" || true
  ls -la "${KERNEL_O}" 2>/dev/null || echo "no_prior_kernel"
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
out={
  "run_id":"${RUN_ID}",
  "utc":"${UTC_STAMP}",
  "pid": os.getpid(),
  "sources":{
    "kernel_cpp": sha(root/"kernels/d51_compute_delay_kernel.cpp"),
    "device_work": sha(root/"device_work.cpp"),
    "intervention": sha(root/"wait_dag_v4_intervention.py"),
    "dose_calibrate_v43": sha(root/"dose_calibrate_v4_3.py"),
  },
  "prior_v42_kernel": sha(Path("${KERNEL_O}")) if Path("${KERNEL_O}").exists() else None,
}
print(json.dumps(out, indent=2))
PY

# --- Step 3+4: build kernel + Plan Step 4 artifact gate (FINAL only) ---
log "Step 3-4 kernel build + V4.1 artifact"
if [[ -f /usr/local/Ascend/ascend-toolkit/latest/set_env.sh ]]; then
  # shellcheck disable=SC1091
  source /usr/local/Ascend/ascend-toolkit/latest/set_env.sh
fi
chmod +x "${ROOT}/kernels/build_kernel.sh" "${ROOT}/build.sh"
"${ROOT}/kernels/build_kernel.sh" 2>&1 | tee "${LOGDIR}/kernel_build/build_kernel.log"
TMP="${ROOT}/build/kernels/d51_compute_delay_kernel_tmp.o"
{
  echo "=== readelf gate ==="
  file "${TMP}" "${KERNEL_O}"
  readelf -h "${TMP}" | grep -E 'Type:'
  readelf -h "${KERNEL_O}" | grep -E 'Type:'
  readelf -SW "${KERNEL_O}" | grep -E 'ascend\.meta'
  readelf -Ws "${KERNEL_O}" | grep d51_compute_delay_kernel
  sha256sum "${ROOT}/kernels/d51_compute_delay_kernel.cpp" "${TMP}" "${KERNEL_O}"
} | tee "${LOGDIR}/kernel_build/elf_inspect.txt"
file "${TMP}" | grep -q "relocatable" || { write_stop "ARTIFACT_TMP_NOT_ET_REL"; exit 3; }
readelf -h "${TMP}" | grep -q "Type:.*REL" || { write_stop "ARTIFACT_TMP_NOT_ET_REL"; exit 3; }
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
KERNEL_SHA=$(sha256_file "${KERNEL_O}")
log "V4.3 KERNEL_SHA=${KERNEL_SHA}"

# --- D0 selector manifest (needed for chain-out checksum + dose) ---
log "Step 6 prep: D0_b1 reverse witness from V4.2"
if [[ ! -d "${D0_V42_DIR}" ]]; then
  write_stop "D0_B1_MISSING"
  exit 4
fi
OUT_DIR="${WORKDIR}/reverse_extract_b1"
python3 "${ROOT}/wait_dag_v4_2_reverse_candidate.py" \
  --run-dir "${D0_V42_DIR}" \
  --run-id "${D0_V42_RUN_ID}_control_d0_b1" \
  --out-dir "${OUT_DIR}" \
  --target-comm "${TARGET_COMM}" \
  --block b1 \
  2>&1 | tee "${LOGDIR}/analysis/reverse_extract.log" || {
  write_stop "REVERSE_CANDIDATE_NOT_UNIQUE"
  exit 5
}
SELECTOR_MANIFEST="${OUT_DIR}/selector_manifest_b1.json"

# --- checksum unit smoke (device_work chain-out path) ---
log "Step 3 checksum unit"
./build.sh ascend 2>&1 | tee "${LOGDIR}/kernel_build/build_preload.log"
export LD_PRELOAD="${PRELOAD_ASCEND}"
CHECKSUM_DIR="${LOGDIR}/kernel_unit"
mkdir -p "${CHECKSUM_DIR}/trace"
python3 "${ROOT}/smoke_kernel_checksum.py" \
  --trace-dir "${CHECKSUM_DIR}/trace" \
  --preload-lib "${V2_SO}" \
  --kernel-binary "${KERNEL_O}" \
  --selector-manifest "${SELECTOR_MANIFEST}" \
  --out-json "${CHECKSUM_DIR}/checksum_smoke.json" \
  --iters 1 2 17 257 \
  2>&1 | tee "${CHECKSUM_DIR}/checksum_smoke.log" || {
  write_stop "DOSE_KERNEL_DEPENDENCY_INVALID"
  exit 3
}

# --- Step 2 partial: D0 analyze for S ---
D0_MANIFEST="${WORKDIR}/manifest_d0_b1.json"
python3 - <<PY > "${D0_MANIFEST}"
import json
print(json.dumps({
  "runs":[{"run_id":"${D0_V42_RUN_ID}_control_d0_b1","condition":"D0","run_dir":"${D0_V42_DIR}"}],
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

# --- Step 5-6: profiler curve + dose freeze ---
log "Step 5-6 profiler curve + dose freeze"
export LD_PRELOAD="${PRELOAD_ASCEND}"
DOSE_PROBE="${WORKDIR}/dose_probe_${UTC_STAMP}"
DOSE_JSON="${WORKDIR}/dose_iters.json"
if ! python3 "${ROOT}/dose_calibrate_v4_3.py" \
  --d0-node-wallclock "${D0_NWC}" \
  --out-json "${DOSE_JSON}" \
  --probe-root "${DOSE_PROBE}" \
  --preload-lib "${V2_SO}" \
  --kernel-binary "${KERNEL_O}" \
  --selector-manifest "${SELECTOR_MANIFEST}" \
  --kernel-binary-sha256 "${KERNEL_SHA}" \
  --timeout-s 240 \
  --mode full \
  2>&1 | tee "${LOGDIR}/profiler_curve/dose_calibrate.log"; then
  STATUS=$(python3 -c "import json; print(json.load(open('${DOSE_JSON}')).get('status','UNKNOWN'))" 2>/dev/null || echo UNKNOWN)
  write_stop "${STATUS}"
  cp "${DOSE_JSON}" "${MYPORTAL}/"
  cp -a "${LOGDIR}" "${MYPORTAL}/logs/${UTC_STAMP}/" 2>/dev/null || true
  exit 6
fi

DSMALL=$(python3 -c "import json; print(json.load(open('${DOSE_JSON}'))['Dsmall_iters'])")
DLARGE=$(python3 -c "import json; print(json.load(open('${DOSE_JSON}'))['Dlarge_iters'])")
python3 - <<PY > "${WORKDIR}/frozen_dose_v4_3.json"
import json
d=json.load(open("${DOSE_JSON}"))
d["frozen_utc"]="${UTC_STAMP}"
d["kernel_binary_sha256"]="${KERNEL_SHA}"
d["block_dim"]=1
d["scratch_bytes"]=1024
print(json.dumps(d, indent=2))
PY
log "FROZEN Dsmall=${DSMALL} Dlarge=${DLARGE}"

# --- Step 7: fresh b1 treatments ---
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

SMALL_TAG="Dsmall_v43_b1"
DLARGE_TAG="Dlarge_v43_b1"
SMALL_RUN_ID="${RUN_ID}_${SMALL_TAG}"
DLARGE_RUN_ID="${RUN_ID}_${DLARGE_TAG}"

log "Step 7 b1 treatments"
run_train "${SMALL_TAG}" "${DSMALL}"
run_train "${DLARGE_TAG}" "${DLARGE}"

write_manifest() {
  python3 - <<PY > "$1"
import json
print(json.dumps($2))
PY
}

SMALL_MANIFEST="${WORKDIR}/manifest_${SMALL_TAG}.json"
write_manifest "${SMALL_MANIFEST}" "{
  \"runs\":[{\"run_id\":\"${SMALL_RUN_ID}\",\"condition\":\"Dsmall\",\"run_dir\":\"${WORKDIR}/runs/${SMALL_TAG}\"}],
  \"pairs\":[],
  \"selector_manifest\":\"${SELECTOR_MANIFEST}\"
}"
DLARGE_MANIFEST="${WORKDIR}/manifest_${DLARGE_TAG}.json"
write_manifest "${DLARGE_MANIFEST}" "{
  \"runs\":[{\"run_id\":\"${DLARGE_RUN_ID}\",\"condition\":\"Dlarge\",\"run_dir\":\"${WORKDIR}/runs/${DLARGE_TAG}\"}],
  \"pairs\":[],
  \"selector_manifest\":\"${SELECTOR_MANIFEST}\"
}"
PAIR_MANIFEST="${WORKDIR}/manifest_b1_pairs.json"
write_manifest "${PAIR_MANIFEST}" "{
  \"runs\":[
    {\"run_id\":\"${D0_V42_RUN_ID}_control_d0_b1\",\"condition\":\"D0\",\"run_dir\":\"${D0_V42_DIR}\"},
    {\"run_id\":\"${SMALL_RUN_ID}\",\"condition\":\"Dsmall\",\"run_dir\":\"${WORKDIR}/runs/${SMALL_TAG}\"},
    {\"run_id\":\"${DLARGE_RUN_ID}\",\"condition\":\"Dlarge\",\"run_dir\":\"${WORKDIR}/runs/${DLARGE_TAG}\"}
  ],
  \"pairs\":[
    [\"b1_dsmall\",\"${D0_V42_RUN_ID}_control_d0_b1\",\"${SMALL_RUN_ID}\"],
    [\"b1_dlarge\",\"${D0_V42_RUN_ID}_control_d0_b1\",\"${DLARGE_RUN_ID}\"]
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
import csv, json
rows=list(csv.DictReader(open("${PAIR_OUT}/paired_effects.csv")))
causal=[r for r in rows if r.get("legacy_illegal_dose","False").lower()!="true"]
ok=all(r.get("causal_gate_pass")=="True" for r in causal if r.get("dose_gate_pass")=="True")
print("1" if ok and len(causal)==2 else "0")
for r in rows:
    print("PAIR", r["block"], "struct", r.get("structure_gate_pass"), "dose", r.get("dose_gate_pass"), "causal", r.get("causal_gate_pass"), "legacy", r.get("legacy_illegal_dose"))
PY
)
log "b1_causal_all_pass=${B1_CAUSAL_PASS}"

if [[ "${B1_CAUSAL_PASS}" != "1" ]]; then
  write_stop "B1_CAUSAL_GATE_FAIL"
else
  log "Step 8: b1 causal PASS — b2/b3 would run here (not auto-expanded in first pass)"
fi

# --- package ---
python3 - <<'PY' "${PAIR_OUT}/paired_effects.csv" "${WORKDIR}/claims.md" "${RUN_ID}"
import csv, sys
from pathlib import Path
rows = list(csv.DictReader(open(sys.argv[1])))
claims = ["# D51 Wait DAG V4.3 claims", "", f"RUN_ID={sys.argv[3]}", ""]
causal_pass = [r for r in rows if r.get("causal_gate_pass") == "True" and r.get("legacy_illegal_dose","False").lower() != "true"]
if causal_pass:
    claims.append("## Causal claims (causal_gate_pass only)")
    for r in causal_pass:
        claims.append(f"- {r['block']}: causal_gate_pass=true realized_work_ns={r.get('realized_work_ns')}")
else:
    claims.append("## No causal claims — causal_gate_pass not true for all legal pairs")
claims.append("")
claims.append("## Excluded")
claims.append("- V4.2 legacy 200/2000 treatments: legacy_illegal_dose, not in denominator")
Path(sys.argv[2]).write_text("\n".join(claims) + "\n")
PY

cp "${DOSE_JSON}" "${MYPORTAL}/"
cp "${WORKDIR}/frozen_dose_v4_3.json" "${MYPORTAL}/" 2>/dev/null || true
cp "${WORKDIR}/claims.md" "${MYPORTAL}/"
cp -a "${OUT_DIR}/"* "${MYPORTAL}/" 2>/dev/null || true
mkdir -p "${MYPORTAL}/paired_b1" "${MYPORTAL}/analysis"
cp -a "${PAIR_OUT}/"* "${MYPORTAL}/paired_b1/"
cp -a "${LOGDIR}" "${MYPORTAL}/logs/${UTC_STAMP}/"
log "DONE V4.3 MYPORTAL=${MYPORTAL} STOP=${STOP_REASON:-none}"
