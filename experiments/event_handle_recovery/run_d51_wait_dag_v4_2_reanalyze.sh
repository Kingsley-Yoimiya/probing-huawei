#!/usr/bin/env bash
# D51 Wait DAG V4.2 FIX: reverse reanalysis + profiler dose + b1 treatment rerun.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_ID="${RUN_ID:-20260824T112527Z_d51_wait_dag_v4_1_device_work}"
WORKDIR="/tmp/${RUN_ID}"
TARGET_COMM="hcom_allReduce__612_0_1"
UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ANALYSIS_ROOT="${WORKDIR}/analysis_v4_2"
LOGDIR="${ANALYSIS_ROOT}/logs/${UTC_STAMP}"
MYPORTAL="/workspace/myportal/results/npu-dev-1/${RUN_ID}/analysis_v4_2"
KERNEL_O="${ROOT}/build/kernels/d51_compute_delay_kernel.o"
BUILD_DIR="${ROOT}/build"
HELPER_SO="${BUILD_DIR}/libacl_real_dlsym.so"
V2_SO="${BUILD_DIR}/libacl_event_trace_v2.so"
PRELOAD_ASCEND="${HELPER_SO}:${V2_SO}"
D0_TAG="control_d0_b1"
D0_RUN_ID="${RUN_ID}_${D0_TAG}"
SMALL_TAG="small_dsmall"
SMALL_RUN_ID="${RUN_ID}_${SMALL_TAG}"
DLARGE_TAG="effect_dlarge_b1"
DLARGE_RUN_ID="${RUN_ID}_${DLARGE_TAG}"
EST_TRAIN_S=600

mkdir -p "${LOGDIR}" "${MYPORTAL}/logs/${UTC_STAMP}"
exec > >(tee -a "${LOGDIR}/reanalyze.log") 2>&1

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

log "V4.2 FIX reanalyze RUN_ID=${RUN_ID} UTC=${UTC_STAMP} est_train_s=${EST_TRAIN_S}"

hash_manifest() {
  UTC_STAMP="${UTC_STAMP}" ROOT="${ROOT}" WORKDIR="${WORKDIR}" \
  python3 - <<'PY'
import hashlib, json, os
from pathlib import Path

def sha(p: Path):
    if not p.exists():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()

run_dir = Path(os.environ["WORKDIR"]) / "control_d0_b1"
prof = run_dir / "out/args_on"
db = next(prof.glob("**/ascend_pytorch_profiler_0.db"), None)
pw = prof / "profile_window.json"
root = Path(os.environ["ROOT"])
out = {
  "utc_stamp": os.environ["UTC_STAMP"],
  "db_sha256": sha(db) if db else None,
  "profile_window_sha256": sha(pw) if pw.exists() else None,
  "extractor": sha(root / "wait_dag_v4_2_reverse_candidate.py"),
  "intervention": sha(root / "wait_dag_v4_intervention.py"),
  "device_work": sha(root / "device_work.cpp"),
  "dose_calibrate": sha(root / "dose_calibrate_v4_2.py"),
}
path = Path(os.environ.get("HASH_OUT", "/dev/stdout"))
path.write_text(json.dumps(out, indent=2) + "\n")
print(json.dumps(out, indent=2))
PY
}

HASH_OUT="${LOGDIR}/hash_before.json" hash_manifest | tee "${LOGDIR}/hash_before.json"

OUT_DIR="${ANALYSIS_ROOT}/reverse_extract_b1"
python3 "${ROOT}/wait_dag_v4_2_reverse_candidate.py" \
  --run-dir "${WORKDIR}/${D0_TAG}" \
  --run-id "${D0_RUN_ID}" \
  --out-dir "${OUT_DIR}" \
  --target-comm "${TARGET_COMM}" \
  --block b1 \
  2>&1 | tee "${LOGDIR}/reverse_extract.log" || RC=$?

RC="${RC:-0}"
if [[ "${RC}" -ne 0 ]]; then
  log "STOP: REVERSE_CANDIDATE_NOT_UNIQUE rc=${RC}"
  cp -a "${OUT_DIR}/"* "${MYPORTAL}/" 2>/dev/null || true
  cp -a "${LOGDIR}" "${MYPORTAL}/logs/"
  exit "${RC}"
fi

SELECTOR_MANIFEST="${OUT_DIR}/selector_manifest_b1.json"
log "Reverse candidate unique; manifest=${SELECTOR_MANIFEST}"

analyze_one() {
  local tag="$1" cond="$2" mpath="$3"
  python3 "${ROOT}/wait_dag_v4_intervention.py" \
    --manifest "${mpath}" \
    --selector-manifest "${SELECTOR_MANIFEST}" \
    --out-dir "${ANALYSIS_ROOT}/per_run/${tag}" \
    2>&1 | tee "${LOGDIR}/analyze_${tag}.log"
}

write_manifest() {
  local mpath="$1"
  shift
  python3 - <<PY > "${mpath}"
import json
print(json.dumps($1))
PY
}

# --- D0 identity analyze (single run) ---
D0_MANIFEST="${WORKDIR}/manifest_${D0_TAG}.json"
write_manifest "${D0_MANIFEST}" "{
  \"runs\":[{\"run_id\":\"${D0_RUN_ID}\",\"condition\":\"D0\",\"run_dir\":\"${WORKDIR}/${D0_TAG}\"}],
  \"pairs\":[],
  \"selector_manifest\":\"${SELECTOR_MANIFEST}\"
}"
analyze_one "${D0_TAG}" "D0" "${D0_MANIFEST}"

D0_SUMMARY="${ANALYSIS_ROOT}/per_run/${D0_TAG}/v3_1_summary.json"
if ! python3 - <<PY
import json, sys
s=json.load(open("${D0_SUMMARY}"))
if int(s.get("identity_ok",0)) < 1:
    raise SystemExit("identity_ok<1")
print("D0_IDENTITY_OK", s["identity_ok"])
PY
then
  log "STOP: D0 analyze identity_ok mismatch"
  exit 4
fi

# --- mechanism smoke (audit only) ---
if [[ -f /usr/local/Ascend/ascend-toolkit/latest/set_env.sh ]]; then
  # shellcheck disable=SC1091
  source /usr/local/Ascend/ascend-toolkit/latest/set_env.sh
fi
export LD_PRELOAD="${PRELOAD_ASCEND}"
export ACL_EVENT_WORK_BINARY="${KERNEL_O}"
export ACL_EVENT_SELECTOR_MANIFEST="${SELECTOR_MANIFEST}"
SMOKE_DIR="${ANALYSIS_ROOT}/kernel_smoke_${UTC_STAMP}"
mkdir -p "${SMOKE_DIR}"
python3 "${ROOT}/smoke_device_work.py" \
  --trace-dir "${SMOKE_DIR}" \
  --preload-lib "${V2_SO}" \
  --kernel-binary "${KERNEL_O}" \
  --selector-manifest "${SELECTOR_MANIFEST}" \
  --iters 0 50 200 1000 \
  2>&1 | tee "${LOGDIR}/smoke.log"

# --- profiler dose calibration (fail-closed) ---
DOSE_PROBE="${ANALYSIS_ROOT}/dose_probe_${UTC_STAMP}"
S_NS=$(python3 - <<PY
import csv
rows=list(csv.DictReader(open("${ANALYSIS_ROOT}/per_run/${D0_TAG}/node_wallclock.csv")))
rec=next(x for x in rows if x["node"]=="record_task")
ce=next(x for x in rows if x["node"]=="comm_entry")
S=int(ce["start_offset_from_upstream_kernel_end_ns"]) - int(rec["end_offset_from_upstream_kernel_end_ns"])
print(S)
PY
)
log "D0 S_record_to_comm_ns=${S_NS}"
ITER_CANDIDATES=$(python3 - <<PY
S=${S_NS}
# scale candidates from S; up to 6 probes
base = max(1000, int(S / 500))
cands = sorted({base, base*5, base*20, base*100, base*500, base*2000})
print(" ".join(str(min(c, 5000000)) for c in cands[:6]))
PY
)
log "DOSE_PROBE candidates: ${ITER_CANDIDATES}"
DOSE_OK=0
if python3 "${ROOT}/dose_calibrate_v4_2.py" \
  --d0-node-wallclock "${ANALYSIS_ROOT}/per_run/${D0_TAG}/node_wallclock.csv" \
  --out-json "${ANALYSIS_ROOT}/dose_iters.json" \
  --probe-root "${DOSE_PROBE}" \
  --preload-lib "${V2_SO}" \
  --kernel-binary "${KERNEL_O}" \
  --selector-manifest "${SELECTOR_MANIFEST}" \
  --iter-candidates ${ITER_CANDIDATES} \
  2>&1 | tee "${LOGDIR}/dose_calibrate.log"; then
  DOSE_OK=1
  DSMALL=$(python3 -c "import json; print(json.load(open('${ANALYSIS_ROOT}/dose_iters.json'))['Dsmall_iters'])")
  DLARGE=$(python3 -c "import json; print(json.load(open('${ANALYSIS_ROOT}/dose_iters.json'))['Dlarge_iters'])")
  log "FROZEN_DOSE profiler-calibrated Dsmall=${DSMALL} Dlarge=${DLARGE}"
else
  log "STOP_DOSE: calibration failed — skip fresh treatment; re-analyze existing runs only"
  echo '{"status":"STOP_DOSE_CALIBRATION_FAILED"}' > "${ANALYSIS_ROOT}/dose_iters.json"
fi

cd "${ROOT}"
./build.sh ascend 2>&1 | tee "${LOGDIR}/rebuild_ascend.log"

if [[ "${DOSE_OK}" -eq 1 ]]; then
  run_train() {
    local tag="$1" iters="$2"
    local wdir="${WORKDIR}/${tag}"
    mkdir -p "${wdir}/event_trace" "${wdir}/out"
    export ACL_EVENT_TRACE_DIR="${wdir}/event_trace"
    export ACL_EVENT_WORK_ITERS="${iters}"
    export ACL_EVENT_WORK_BINARY="${KERNEL_O}"
    export ACL_EVENT_SELECTOR_MANIFEST="${SELECTOR_MANIFEST}"
    export LD_PRELOAD="${PRELOAD_ASCEND}"
    log "===== TRAIN ${tag} iters=${iters} pid=$$ ====="
    local port=$((29500 + RANDOM % 1000))
    timeout "${EST_TRAIN_S}" torchrun --nproc_per_node=16 --master_port="${port}" \
      "${ROOT}/train_event_preload.py" \
      --output "${wdir}/out" \
      --trace-dir "${wdir}/event_trace" \
      --preload-lib "${V2_SO}" \
      --profiler-level Level1 \
      --dim 4096 --batch 256 --warmup 2 --steps 3 \
      2>&1 | tee "${LOGDIR}/train_${tag}.log"
  }
  run_train "${SMALL_TAG}" "${DSMALL}"
  run_train "${DLARGE_TAG}" "${DLARGE}"
else
  log "Reusing existing treatment dirs: ${SMALL_TAG} ${DLARGE_TAG}"
fi
SMALL_MANIFEST="${WORKDIR}/manifest_${SMALL_TAG}.json"
write_manifest "${SMALL_MANIFEST}" "{
  \"runs\":[{\"run_id\":\"${SMALL_RUN_ID}\",\"condition\":\"Dsmall\",\"run_dir\":\"${WORKDIR}/${SMALL_TAG}\"}],
  \"pairs\":[],
  \"selector_manifest\":\"${SELECTOR_MANIFEST}\"
}"
analyze_one "${SMALL_TAG}" "Dsmall" "${SMALL_MANIFEST}"

DLARGE_MANIFEST="${WORKDIR}/manifest_${DLARGE_TAG}.json"
write_manifest "${DLARGE_MANIFEST}" "{
  \"runs\":[{\"run_id\":\"${DLARGE_RUN_ID}\",\"condition\":\"Dlarge\",\"run_dir\":\"${WORKDIR}/${DLARGE_TAG}\"}],
  \"pairs\":[],
  \"selector_manifest\":\"${SELECTOR_MANIFEST}\"
}"
analyze_one "${DLARGE_TAG}" "Dlarge" "${DLARGE_MANIFEST}"

# --- paired analyze (single manifest, log=CSV) ---
PAIR_MANIFEST="${WORKDIR}/manifest_b1_pairs.json"
write_manifest "${PAIR_MANIFEST}" "{
  \"runs\":[
    {\"run_id\":\"${D0_RUN_ID}\",\"condition\":\"D0\",\"run_dir\":\"${WORKDIR}/${D0_TAG}\"},
    {\"run_id\":\"${SMALL_RUN_ID}\",\"condition\":\"Dsmall\",\"run_dir\":\"${WORKDIR}/${SMALL_TAG}\"},
    {\"run_id\":\"${DLARGE_RUN_ID}\",\"condition\":\"Dlarge\",\"run_dir\":\"${WORKDIR}/${DLARGE_TAG}\"}
  ],
  \"pairs\":[
    [\"b1_dsmall\",\"${D0_RUN_ID}\",\"${SMALL_RUN_ID}\"],
    [\"b1_dlarge\",\"${D0_RUN_ID}\",\"${DLARGE_RUN_ID}\"]
  ],
  \"selector_manifest\":\"${SELECTOR_MANIFEST}\"
}"
PAIR_OUT="${ANALYSIS_ROOT}/paired_b1"
python3 "${ROOT}/wait_dag_v4_intervention.py" \
  --manifest "${PAIR_MANIFEST}" \
  --selector-manifest "${SELECTOR_MANIFEST}" \
  --out-dir "${PAIR_OUT}" \
  2>&1 | tee "${LOGDIR}/analyze_b1_pairs.log"

python3 - <<PY | tee "${LOGDIR}/consistency_check.log"
import csv, json, sys
from pathlib import Path

pair_out = Path("${PAIR_OUT}")
log_dir = Path("${LOGDIR}")
summary = json.loads((pair_out / "v3_1_summary.json").read_text())
log_line = (log_dir / "analyze_b1_pairs.log").read_text().strip().splitlines()[-1]
log_summary = json.loads(log_line)
for key in ("identity_ok", "pair_ok", "realized_work_ok"):
    if summary.get(key) != log_summary.get(key):
        raise SystemExit(f"INCONSISTENT {key}: csv={summary.get(key)} log={log_summary.get(key)}")
paired = list(csv.DictReader(open(pair_out / "paired_effects.csv")))
if not paired:
    raise SystemExit("paired_effects.csv empty")
print("CONSISTENCY_OK", json.dumps({k: summary[k] for k in ("identity_ok","pair_ok","realized_work_ok")}))
print("PAIRED_ROWS", len(paired))
for r in paired:
    print("PAIR", r.get("block"), r.get("status"), "realized_work_ns", r.get("realized_work_ns"))
PY

python3 - <<'PY' "${PAIR_OUT}/paired_effects.csv" "${ANALYSIS_ROOT}/claims.md"
import csv, json, sys
from pathlib import Path
rows = list(csv.DictReader(open(sys.argv[1])))
allowed = [
    "D0_b1 reverse candidate_count=1 for hcom_allReduce__612_0_1; A6 4/4 excluded.",
    "Selector ordinal from paired D0 manifest (run-local); no source constant fallback.",
    "Treatment launch_count=1 with inject preload cs aligned to target Record cs (audit-only).",
]
forbidden = []
for r in rows:
    if r.get("status") != "OK":
        forbidden.append(f"pair {r.get('block')} status={r.get('status')}")
    else:
        rw = int(r.get("realized_work_ns") or 0)
        if rw <= 0:
            forbidden.append(f"pair {r.get('block')} realized_work_ns=0")
claims = "\n".join([
    "# D51 Wait DAG V4.2 claims (audit-limited)",
    "",
    "## Allowed (weak)",
] + [f"- {c}" for c in allowed] + ["", "## Forbidden / not claimed"] + [f"- {f}" for f in forbidden] + [
    "- No Close; causal §7 not asserted unless pair rows OK with realized_work>0.",
    "- b2/b3 not run in this FIX pass.",
])
Path(sys.argv[2]).write_text(claims + "\n")
PY

HASH_OUT="${LOGDIR}/hash_after.json" hash_manifest | tee "${LOGDIR}/hash_after.json"
cp -a "${OUT_DIR}/"* "${MYPORTAL}/" 2>/dev/null || true
cp -a "${ANALYSIS_ROOT}/per_run/"* "${MYPORTAL}/per_run/" 2>/dev/null || true
cp -a "${PAIR_OUT}/"* "${MYPORTAL}/paired_b1/" 2>/dev/null || mkdir -p "${MYPORTAL}/paired_b1" && cp -a "${PAIR_OUT}/"* "${MYPORTAL}/paired_b1/"
cp "${ANALYSIS_ROOT}/dose_iters.json" "${MYPORTAL}/" 2>/dev/null || true
cp "${ANALYSIS_ROOT}/claims.md" "${MYPORTAL}/" 2>/dev/null || true
cp -a "${LOGDIR}" "${MYPORTAL}/logs/"
log "DONE V4.2 FIX MYPORTAL=${MYPORTAL}"
