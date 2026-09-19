#!/usr/bin/env bash
# D51 Wait DAG V4 — compute-stream device-work delay (fresh evidence).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${ROOT}/build"
HELPER_SO="${BUILD_DIR}/libacl_real_dlsym.so"
V2_SO="${BUILD_DIR}/libacl_event_trace_v2.so"
KERNEL_O="${BUILD_DIR}/kernels/d51_compute_delay_kernel.o"
PRELOAD_ASCEND="${HELPER_SO}:${V2_SO}"

UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_wait_dag_v4_device_work}"
export RUN_ID
WORKDIR="/tmp/${RUN_ID}"
LOGDIR="${WORKDIR}/logs/${UTC_STAMP}"
MYPORTAL="/workspace/myportal/results/npu-dev-1/${RUN_ID}"
ANALYSIS_DIR="${WORKDIR}/analysis"
TARGET_COMM="hcom_allReduce__612_0_1"
EXCLUDED_V31="20260824T090000Z_d51_wait_dag_v3_1_fix_ctrlfix"

mkdir -p "${LOGDIR}/toolchain" "${LOGDIR}/build_unit" "${LOGDIR}/kernel_smoke" \
  "${LOGDIR}/dose_calibration" "${LOGDIR}/preflight" "${LOGDIR}/runs" \
  "${LOGDIR}/profiler_export" "${LOGDIR}/analysis" "${LOGDIR}/package" \
  "${ANALYSIS_DIR}" "${MYPORTAL}"
exec > >(tee -a "${LOGDIR}/runner.log") 2>&1

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

if [[ -f /usr/local/Ascend/ascend-toolkit/latest/set_env.sh ]]; then
  # shellcheck disable=SC1091
  source /usr/local/Ascend/ascend-toolkit/latest/set_env.sh
fi

THIS_PGID=""
record_pgid() { THIS_PGID="$(ps -o pgid= -p $$ | tr -d ' ')"; }
kill_experiment() {
  if [[ -n "${THIS_PGID}" ]]; then
    log "KILL experiment PGID=${THIS_PGID}"
    kill -TERM "-${THIS_PGID}" 2>/dev/null || true
    sleep 2
    kill -KILL "-${THIS_PGID}" 2>/dev/null || true
  fi
}
trap kill_experiment INT TERM
record_pgid

log "RUN_ID=${RUN_ID} WORKDIR=${WORKDIR}"

# --- toolchain / mechanism probe ---
{
  echo "RUN_ID=${RUN_ID}"
  nm -D /usr/local/Ascend/ascend-toolkit/latest/lib64/libascendcl.so | grep -E "aclrtBinary|aclrtLaunchKernel" | head -20
  grep -E "aclrtBinaryLoadFromFile|aclrtLaunchKernelWithConfig" \
    /usr/local/Ascend/ascend-toolkit/latest/include/acl/acl_rt.h | head -5
  which ccec bisheng
  npu-smi info 2>&1 | head -8 || true
  pgrep -a vllm || true
} | tee "${LOGDIR}/toolchain/toolchain_symbols.txt" || true

MECH="${WORKDIR}/mechanism_decision.md"
cat > "${MECH}" <<EOF
# mechanism_decision

**decision:** GO_CUSTOM_DEVICE_KERNEL

| 能力 | 状态 |
|------|------|
| ccec/bisheng | $(command -v ccec) |
| aclrtBinaryLoadFromFile | header + libascendcl symbol |
| aclrtBinaryGetFunction | header + libascendcl symbol |
| aclrtLaunchKernelWithConfig | header + libascendcl symbol |
| SoC | 910B2C (npu-smi) |

证据: \`${LOGDIR}/toolchain/toolchain_symbols.txt\`
EOF

PREFLIGHT_JSON="${WORKDIR}/preflight.json"
python3 - <<PY
import json, os, subprocess, hashlib
from pathlib import Path
root = Path("${ROOT}")
def sha(p):
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()
pref = {
  "run_id": os.environ["RUN_ID"],
  "machine": "npu-dev-1",
  "container": "montyyin_reduce_ws16",
  "utc_stamp": "${UTC_STAMP}",
  "target_comm": "${TARGET_COMM}",
  "target_record_ordinal": 3,
  "target_wait_ordinal": 0,
  "excluded_from_v4_statistics": [
    {"run_id": "${EXCLUDED_V31}", "reason": "v3.1 seal"},
  ],
  "execution_order": [
    "D0_b1", "Dsmall", "Dlarge_b1", "D0_b2", "Dlarge_b2", "D0_b3", "Dlarge_b3",
  ],
  "train_recipe": {"dim": 4096, "batch": 256, "warmup": 2, "steps": 3},
  "experiment_pgid": "${THIS_PGID}",
  "vllm_snapshot": subprocess.getoutput("pgrep -a vllm || true"),
}
open("${PREFLIGHT_JSON}", "w").write(json.dumps(pref, indent=2) + "\\n")
PY

# --- build ---
cd "${ROOT}"
chmod +x kernels/build_kernel.sh
./kernels/build_kernel.sh 2>&1 | tee "${LOGDIR}/build_unit/build_kernel.log"
./build.sh ascend 2>&1 | tee "${LOGDIR}/build_unit/build_ascend.log"
export LD_PRELOAD="${PRELOAD_ASCEND}"
export ACL_EVENT_WORK_BINARY="${KERNEL_O}"

python3 "${ROOT}/smoke_device_work.py" \
  --trace-dir "${WORKDIR}/kernel_smoke" \
  --preload-lib "${V2_SO}" \
  --kernel-binary "${KERNEL_O}" \
  --iters 0 50 200 1000 \
  2>&1 | tee "${LOGDIR}/kernel_smoke/smoke.log" || true

run_train() {
  local tag="$1"
  local iters="$2"
  local wdir="${WORKDIR}/${tag}"
  local ldir="${LOGDIR}/runs/${tag}"
  mkdir -p "${wdir}/event_trace" "${wdir}/out" "${ldir}"
  export ACL_EVENT_TRACE_DIR="${wdir}/event_trace"
  export ACL_EVENT_WORK_ITERS="${iters}"
  export ACL_EVENT_WORK_BINARY="${KERNEL_O}"
  export LD_PRELOAD="${PRELOAD_ASCEND}"
  log "===== TRAIN ${tag} iters=${iters} ====="
  local port=$((29500 + RANDOM % 1000))
  timeout 600 torchrun --nproc_per_node=16 --master_port="${port}" \
    "${ROOT}/train_event_preload.py" \
    --output "${wdir}/out" \
    --trace-dir "${wdir}/event_trace" \
    --preload-lib "${V2_SO}" \
    --profiler-level Level1 \
    --dim 4096 --batch 256 --warmup 2 --steps 3 \
    2>&1 | tee "${ldir}/torchrun.log"
}

analyze_one() {
  local tag="$1"
  local cond="$2"
  local run_id="${RUN_ID}_${tag}"
  local mpath="${WORKDIR}/manifest_${tag}.json"
  python3 - <<PY > "${mpath}"
import json
print(json.dumps({"runs":[{"run_id":"${run_id}","condition":"${cond}","run_dir":"${WORKDIR}/${tag}"}]}))
PY
  python3 "${ROOT}/wait_dag_v4_intervention.py" \
    --manifest "${mpath}" \
    --out-dir "${WORKDIR}/per_run/${tag}" 2>&1 | tee "${LOGDIR}/analysis/analyze_${tag}.log"
}

check_d0_structure() {
  local tag="$1"
  python3 - <<'PY' "${WORKDIR}/per_run/${tag}/intervention_identity.csv"
import csv, json, sys
rows = list(csv.DictReader(open(sys.argv[1])))
r = rows[0]
key = json.loads(r["normalized_structure_key"])
if key.get("active_success_ordinal") != 3:
    raise SystemExit(f"record ordinal {key.get('active_success_ordinal')} != 3")
if key.get("wait_active_success_ordinal") != 0:
    raise SystemExit(f"wait ordinal {key.get('wait_active_success_ordinal')} != 0")
print("D0_STRUCTURE_OK", r["comm_op"], key)
PY
}

pick_dose_iters() {
  python3 - <<'PY' "${WORKDIR}/per_run/control_d0_b1" "${WORKDIR}/kernel_smoke/smoke_summary.json" \
    "${WORKDIR}/dose_iters.json"
import csv, json, sys
from pathlib import Path
root = Path(sys.argv[1])
slack = 0
nw = root / "node_wallclock.csv"
if nw.exists():
    rows = list(csv.DictReader(open(nw)))
    rec = next((x for x in rows if x["node"] == "record_task"), None)
    ce = next((x for x in rows if x["node"] == "comm_entry"), None)
    if rec and ce:
        slack = int(ce["start_offset_from_upstream_kernel_end_ns"]) - int(
            rec["end_offset_from_upstream_kernel_end_ns"]
        )
smoke = json.loads(Path(sys.argv[2]).read_text())
curve = []
for r in smoke.get("results", []):
    iters = int(r.get("iters", 0))
    if iters <= 0:
        continue
    curve.append((iters, float(r.get("elapsed_s", 0)) * 1e9 / max(iters, 1)))
S = max(slack, 500_000)
small_lo, small_hi = 0.15 * S, 0.40 * S
large_target = S + max(1_000_000, 0.5 * S)
large_lo = S + 1_000_000
large_hi = S + max(4_000_000, 0.75 * S)

def pick(lo, hi, prefer_above=None):
    for iters, ns_per in sorted(curve):
        est = iters * ns_per
        if lo <= est <= hi:
            if prefer_above is None or est >= prefer_above:
                return iters
    return 2000 if prefer_above else 200

dsmall = pick(small_lo, small_hi)
dlarge = pick(large_lo, large_hi, large_target)
out = {"S_record_to_comm_ns": int(S), "Dsmall_iters": dsmall, "Dlarge_iters": dlarge}
Path(sys.argv[3]).write_text(json.dumps(out, indent=2) + "\n")
print(json.dumps(out))
PY
}

# --- D0_b1 ---
run_train "control_d0_b1" 0
analyze_one "control_d0_b1" "D0"
check_d0_structure "control_d0_b1"
pick_dose_iters | tee "${LOGDIR}/dose_calibration/pick_dose.log"
DSMALL=$(python3 -c "import json; print(json.load(open('${WORKDIR}/dose_iters.json'))['Dsmall_iters'])")
DLARGE=$(python3 -c "import json; print(json.load(open('${WORKDIR}/dose_iters.json'))['Dlarge_iters'])")
log "FROZEN_DOSE Dsmall=${DSMALL} Dlarge=${DLARGE}"

run_train "small_dsmall" "${DSMALL}"
analyze_one "small_dsmall" "Dsmall"
run_train "effect_dlarge_b1" "${DLARGE}"
run_train "control_d0_b2" 0
run_train "effect_dlarge_b2" "${DLARGE}"
run_train "control_d0_b3" 0
run_train "effect_dlarge_b3" "${DLARGE}"

for spec in "control_d0_b1:D0" "small_dsmall:Dsmall" "effect_dlarge_b1:Dlarge" \
            "control_d0_b2:D0" "effect_dlarge_b2:Dlarge" "control_d0_b3:D0" "effect_dlarge_b3:Dlarge"; do
  tag="${spec%%:*}"
  cond="${spec##*:}"
  analyze_one "${tag}" "${cond}"
done

MANIFEST="${WORKDIR}/manifest.json"
python3 - <<PY
import json
runs = []
for cond, tag in [
    ("D0","control_d0_b1"),("Dsmall","small_dsmall"),("Dlarge","effect_dlarge_b1"),
    ("D0","control_d0_b2"),("Dlarge","effect_dlarge_b2"),
    ("D0","control_d0_b3"),("Dlarge","effect_dlarge_b3"),
]:
    runs.append({"run_id": f"${RUN_ID}_{tag}", "condition": cond, "run_dir": f"${WORKDIR}/{tag}"})
pairs = [
  ["b1", f"${RUN_ID}_control_d0_b1", f"${RUN_ID}_effect_dlarge_b1"],
  ["b2", f"${RUN_ID}_control_d0_b2", f"${RUN_ID}_effect_dlarge_b2"],
  ["b3", f"${RUN_ID}_control_d0_b3", f"${RUN_ID}_effect_dlarge_b3"],
]
open("${MANIFEST}", "w").write(json.dumps({"runs": runs, "pairs": pairs}, indent=2) + "\\n")
PY

python3 "${ROOT}/wait_dag_v4_intervention.py" \
  --manifest "${MANIFEST}" \
  --out-dir "${ANALYSIS_DIR}" 2>&1 | tee "${LOGDIR}/analysis/final_analysis.log"

mkdir -p "${MYPORTAL}"
cp -a "${ANALYSIS_DIR}/"* "${MYPORTAL}/" 2>/dev/null || true
cp "${PREFLIGHT_JSON}" "${MECH}" "${MYPORTAL}/"
cp "${WORKDIR}/dose_iters.json" "${MYPORTAL}/" 2>/dev/null || true
cp -a "${LOGDIR}/" "${MYPORTAL}/logs/"

trap - EXIT INT TERM
log "DONE RUN_ID=${RUN_ID} MYPORTAL=${MYPORTAL}"
