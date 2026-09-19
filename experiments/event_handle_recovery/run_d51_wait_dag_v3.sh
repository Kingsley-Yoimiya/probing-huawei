#!/usr/bin/env bash
# D51 Wait DAG V3 — HostFunc record_publication_hostfunc_delay causal intervention.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${ROOT}/build"
HELPER_SO="${BUILD_DIR}/libacl_real_dlsym.so"
V2_SO="${BUILD_DIR}/libacl_event_trace_v2.so"
V2_STUB_SO="${BUILD_DIR}/libacl_event_trace_v2_stub.so"
FAKE_ACL_SO="${BUILD_DIR}/libfake_acl.so"
PRELOAD_ASCEND="${HELPER_SO}:${V2_SO}"
PRELOAD_UNIT="${HELPER_SO}:${V2_STUB_SO}:${FAKE_ACL_SO}"

UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_wait_dag_v3}"
WORKDIR="/tmp/${RUN_ID}"
LOGDIR="${WORKDIR}/logs/${UTC_STAMP}"
MYPORTAL="/workspace/myportal/results/npu-dev-1/${RUN_ID}"
ANALYSIS_DIR="${WORKDIR}/analysis"
TARGET_COMM="hcom_allReduce__612_0_1"

mkdir -p "${LOGDIR}" "${ANALYSIS_DIR}" "${MYPORTAL}"
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
trap kill_experiment EXIT INT TERM
record_pgid

log "RUN_ID=${RUN_ID} WORKDIR=${WORKDIR}"

# --- preflight ---
{
  echo "RUN_ID=${RUN_ID}"
  echo "STAMP=${UTC_STAMP}"
  echo "TARGET_COMM=${TARGET_COMM}"
  echo "TARGET_RECORD_ORDINAL=4 TARGET_WAIT_ORDINAL=4"
  npu-smi info 2>&1 | head -20 || true
  python3 -c 'import torch,torch_npu; print(torch.__version__, torch_npu.__version__)'
  nm -D /usr/local/Ascend/ascend-toolkit/latest/lib64/libascendcl.so | grep -i LaunchHostFunc || true
  pgrep -a vllm || true
} | tee "${LOGDIR}/preflight.log"

PREFLIGHT_JSON="${WORKDIR}/preflight.json"
python3 - <<PY
import json, os, subprocess, time
pref = {
  "run_id": os.environ["RUN_ID"],
  "machine": "npu-dev-1",
  "container": "montyyin_reduce_ws16",
  "utc_stamp": "${UTC_STAMP}",
  "target_comm": "${TARGET_COMM}",
  "target_record_ordinal": 4,
  "target_wait_ordinal": 4,
  "delay_gradients_us": {"D0": 0, "D2": 2000, "D25": 25000},
  "train_recipe": {"dim": 4096, "batch": 256, "warmup": 2, "steps": 3},
  "execution_order": [
    "build_unit_smoke",
    "control_d0_b1",
    "small_d2ms",
    "gate_d25",
    "effect_d25_b1",
    "control_d0_b2",
    "effect_d25_b2",
    "control_d0_b3",
    "effect_d25_b3",
  ],
  "vllm_snapshot": subprocess.getoutput("pgrep -a vllm || true"),
}
open("${PREFLIGHT_JSON}", "w").write(json.dumps(pref, indent=2) + "\\n")
PY

# --- build + unit + smoke ---
cd "${ROOT}"
./build.sh ascend 2>&1 | tee "${LOGDIR}/build_ascend.log"
./build.sh local 2>&1 | tee "${LOGDIR}/build_local.log"
export LD_LIBRARY_PATH="${BUILD_DIR}:${LD_LIBRARY_PATH:-}"
LD_PRELOAD="${PRELOAD_UNIT}" "${BUILD_DIR}/event_delay_v3_unit" 2>&1 | tee "${LOGDIR}/unit_delay.log"
LD_PRELOAD="${PRELOAD_UNIT}" "${BUILD_DIR}/event_sequence_smoke" 2>&1 | tee "${LOGDIR}/unit_seq.log"

export LD_PRELOAD="${PRELOAD_ASCEND}"
python3 "${ROOT}/smoke_delay_v3.py" \
  --trace-dir "${WORKDIR}/smoke_delay" \
  --preload-lib "${V2_SO}" \
  --timeout-s 300 2>&1 | tee "${LOGDIR}/smoke_delay.log" || {
  log "WARN: single-card smoke partial fail (PyTorch record thread); 16-card pilot is authoritative"
}

run_train() {
  local tag="$1"
  local delay_us="$2"
  local wdir="${WORKDIR}/${tag}"
  local ldir="${LOGDIR}/${tag}"
  mkdir -p "${wdir}/event_trace" "${wdir}/out" "${ldir}"
  export ACL_EVENT_TRACE_DIR="${wdir}/event_trace"
  export ACL_EVENT_DELAY_US="${delay_us}"
  export LD_PRELOAD="${PRELOAD_ASCEND}"
  log "===== TRAIN ${tag} delay_us=${delay_us} ====="
  local port=$((29500 + RANDOM % 1000))
  timeout 600 torchrun --nproc_per_node=16 --master_port="${port}" \
    "${ROOT}/train_event_preload.py" \
    --output "${wdir}/out" \
    --trace-dir "${wdir}/event_trace" \
    --preload-lib "${V2_SO}" \
    --profiler-level Level1 \
    --dim 4096 --batch 256 --warmup 2 --steps 3 \
    2>&1 | tee "${ldir}/torchrun.log"
  python3 "${ROOT}/event_preload_v6_analyze.py" \
    --run-id "${RUN_ID}_${tag}" \
    --trace-dir "${wdir}/event_trace" \
    --db "$(find "${wdir}/out/args_on" -name ascend_pytorch_profiler_0.db | head -1)" \
    --analysis-dir "${wdir}/analysis_v6" \
    --log-dir "${ldir}" \
    --profile-window "${wdir}/out/args_on/profile_window.json" \
    2>&1 | tee "${ldir}/v6.log" || true
}

analyze_one() {
  local tag="$1"
  local run_id="${RUN_ID}_${tag}"
  local mpath="${WORKDIR}/manifest_${tag}.json"
  python3 - <<PY > "${mpath}"
import json
print(json.dumps({"runs":[{"run_id":"${run_id}","condition":"${tag}","run_dir":"${WORKDIR}/${tag}"}]}))
PY
  python3 "${ROOT}/wait_dag_v3_intervention.py" \
    --manifest "${mpath}" \
    --out-dir "${WORKDIR}/per_run/${tag}" 2>&1 | tee "${LOGDIR}/analyze_${tag}.log"
}

# --- D0 block1 identity chain ---
run_train "control_d0_b1" 0
analyze_one "control_d0_b1"

D0_IDENTITY="${WORKDIR}/per_run/control_d0_b1/intervention_identity.csv"
if [[ ! -f "${D0_IDENTITY}" ]]; then
  log "STOP: D0 identity missing"
  exit 3
fi
python3 - <<'PY' "${D0_IDENTITY}"
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1])))
if not rows:
    raise SystemExit("no identity row")
r = rows[0]
print("D0 record_cs", r.get("preload_record_cs"), "wait_cs", r.get("preload_wait_cs"),
      "streams", r.get("record_stream_id"), "->", r.get("wait_stream_id"))
if r.get("comm_op") != "hcom_allReduce__612_0_1":
    raise SystemExit("wrong comm")
if not r.get("preload_record_cs") or not r.get("preload_wait_cs"):
    raise SystemExit("identity incomplete")
# Fresh run: ordinal-4 chain (cs may differ from V2 frozen 145->176)
print("IDENTITY_OK ordinal4_chain", r.get("preload_record_cs"), "->", r.get("preload_wait_cs"))
PY

# --- D2 pilot ---
run_train "small_d2ms" 2000
analyze_one "small_d2ms"
python3 - <<'PY' "${WORKDIR}/small_d2ms/event_trace"
import glob, json, sys
from pathlib import Path
files = sorted(glob.glob(sys.argv[1] + "/rank_0_pid_*.delay_audit.json"))
if not files:
    raise SystemExit("D2 missing delay audit")
a = json.loads(Path(files[0]).read_text())
if int(a.get("match_count", 0)) != 1:
    raise SystemExit(f"D2 match_count={a.get('match_count')}")
cb = 0
if a.get("callback_exit_monotonic_ns") and a.get("callback_enter_monotonic_ns"):
    cb = (int(a["callback_exit_monotonic_ns"]) - int(a["callback_enter_monotonic_ns"])) / 1e6
if not (1.5 <= cb <= 4.0):
    raise SystemExit(f"D2 callback {cb}ms out of [1.5,4.0]")
print("D2_PILOT_OK callback_ms", cb)
PY

GATE_OK=0
if python3 - <<'PY' "${WORKDIR}"; then GATE_OK=1; fi
import csv, json, sys
from pathlib import Path
root = Path(sys.argv[1])
d2_path = root / "per_run/small_d2ms/callback_realization.csv"
if not d2_path.exists():
    raise SystemExit("D2 callback missing")
d2_rows = list(csv.DictReader(open(d2_path)))
cb = float(d2_rows[0].get("callback_duration_ms") or 0)
if not (1.5 <= cb <= 4.0):
    raise SystemExit(f"D2 callback {cb}ms out of [1.5,4.0]")
nw = list(csv.DictReader(open(root / "per_run/control_d0_b1/node_wallclock.csv")))
rec = next(x for x in nw if x["node"]=="record_task")
fifo = next(x for x in nw if x["node"]=="fifo_successor")
slack_ns = int(fifo["start_ns"]) - int(rec["end_ns"])
if 25000000 - slack_ns < 5000000:
    raise SystemExit(f"D25 gate fail slack_ns={slack_ns}")
open(root / "gate_d25.json", "w").write(json.dumps({"slack_record_to_next_ns": slack_ns, "d2_callback_ms": cb})+"\n")
print("GATE_D25_PASS slack_ns", slack_ns, "d2_cb_ms", cb)
PY

if [[ "${GATE_OK}" != "1" ]]; then
  log "STOP: D25 gate not satisfied — no D25 runs"
  rsync -a "${WORKDIR}/per_run/" "${MYPORTAL}/" 2>/dev/null || true
  rsync -a "${LOGDIR}/" "${MYPORTAL}/logs/" 2>/dev/null || true
  exit 4
fi

run_train "effect_d25_b1" 25000
run_train "control_d0_b2" 0
run_train "effect_d25_b2" 25000
run_train "control_d0_b3" 0
run_train "effect_d25_b3" 25000

for t in control_d0_b1 small_d2ms effect_d25_b1 control_d0_b2 effect_d25_b2 control_d0_b3 effect_d25_b3; do
  analyze_one "${t}"
done

MANIFEST="${WORKDIR}/manifest.json"
python3 - <<PY
import json
runs = []
pairs = []
for cond, tag in [
    ("D0","control_d0_b1"),("D2","small_d2ms"),
    ("D25","effect_d25_b1"),("D0","control_d0_b2"),("D25","effect_d25_b2"),
    ("D0","control_d0_b3"),("D25","effect_d25_b3"),
]:
    runs.append({"run_id": f"${RUN_ID}_{tag}", "condition": cond, "run_dir": f"${WORKDIR}/{tag}"})
pairs = [
  ["b1", f"${RUN_ID}_control_d0_b1", f"${RUN_ID}_effect_d25_b1"],
  ["b2", f"${RUN_ID}_control_d0_b2", f"${RUN_ID}_effect_d25_b2"],
  ["b3", f"${RUN_ID}_control_d0_b3", f"${RUN_ID}_effect_d25_b3"],
]
open("${MANIFEST}", "w").write(json.dumps({"runs": runs, "pairs": pairs}, indent=2) + "\\n")
PY

python3 "${ROOT}/wait_dag_v3_intervention.py" \
  --manifest "${MANIFEST}" \
  --out-dir "${ANALYSIS_DIR}" 2>&1 | tee "${LOGDIR}/final_analysis.log"

python3 - <<'PY' "${ANALYSIS_DIR}"
import csv, json, sys
from pathlib import Path
ad = Path(sys.argv[1])
paired = list(csv.DictReader(open(ad / "paired_effects.csv")))
claims = ["# claims (pending dual Auditor)", ""]
for p in paired:
    claims.append(f"- block {p['block']}: successor_shift_ns={p.get('successor_shift_ns')}")
(ad / "claims.md").write_text("\\n".join(claims) + "\\n")
casebook = ["# delay_casebook", ""]
for row in csv.DictReader(open(ad / "intervention_identity.csv")):
    casebook.append(
        f"## {row['run_id']}\\n"
        f"- record cs={row['preload_record_cs']} wait cs={row['preload_wait_cs']}\\n"
        f"- tasks record={row['record_task_rowid']} wait={row['wait_task_rowid']} fifo={row['fifo_successor_rowid']}\\n"
    )
(ad / "delay_casebook.md").write_text("\\n".join(casebook) + "\\n")
PY

rsync -a "${ANALYSIS_DIR}/" "${MYPORTAL}/"
rsync -a "${LOGDIR}/" "${MYPORTAL}/logs/"
cp "${PREFLIGHT_JSON}" "${MYPORTAL}/"
trap - EXIT INT TERM
log "DONE RUN_ID=${RUN_ID} MYPORTAL=${MYPORTAL}"
