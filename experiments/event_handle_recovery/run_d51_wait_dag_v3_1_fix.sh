#!/usr/bin/env bash
# D51 Wait DAG V3.1 FIX — ordinal latch counts first success; inject↔identity fail-closed.
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
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_wait_dag_v3_1_fix}"
WORKDIR="/tmp/${RUN_ID}"
LOGDIR="${WORKDIR}/logs/${UTC_STAMP}"
MYPORTAL="/workspace/myportal/results/npu-dev-1/${RUN_ID}"
ANALYSIS_DIR="${WORKDIR}/analysis"
TARGET_COMM="hcom_allReduce__612_0_1"
EXCLUDED_V3="20260824T074500Z_d51_wait_dag_v3"
EXCLUDED_V3_1="20260824T081200Z_d51_wait_dag_v3_1"

mkdir -p "${LOGDIR}/build_unit" "${LOGDIR}/smoke" "${LOGDIR}/preflight" \
  "${LOGDIR}/runs" "${LOGDIR}/profiler_export" "${LOGDIR}/analysis" \
  "${ANALYSIS_DIR}" "${MYPORTAL}/delay_audit_runs" "${MYPORTAL}"
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

log "RUN_ID=${RUN_ID} WORKDIR=${WORKDIR} (V3.1 FIX)"

{
  echo "RUN_ID=${RUN_ID}"
  echo "STAMP=${UTC_STAMP}"
  echo "TARGET_COMM=${TARGET_COMM}"
  echo "EXCLUDED_V3=${EXCLUDED_V3}"
  echo "EXCLUDED_V3_1=${EXCLUDED_V3_1}"
  npu-smi info 2>&1 | head -25 || true
  python3 -c 'import torch,torch_npu; print(torch.__version__, torch_npu.__version__)'
  nm -D /usr/local/Ascend/ascend-toolkit/latest/lib64/libascendcl.so | grep -i LaunchHostFunc || true
  pgrep -a vllm || true
  sha256sum "${ROOT}/event_interpose.cpp" "${ROOT}/wait_dag_v3_intervention.py" \
    "${ROOT}/tests/event_delay_v3_unit.cpp" 2>/dev/null || true
} | tee "${LOGDIR}/preflight/preflight.log"

PREFLIGHT_JSON="${WORKDIR}/preflight.json"
export RUN_ID
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
  "fix_tag": "v3_1_ordinal_inject_alignment",
  "target_comm": "${TARGET_COMM}",
  "target_record_ordinal": 4,
  "target_wait_ordinal": 4,
  "delay_gradients_us": {"D0": 0, "D2": 2000, "D25": 25000},
  "train_recipe": {"dim": 4096, "batch": 256, "warmup": 2, "steps": 3},
  "excluded_evidence": [
    {"run_id": "${EXCLUDED_V3}", "excluded_from_v3_1_statistics": True},
    {"run_id": "${EXCLUDED_V3_1}", "excluded_from_v3_1_fix_statistics": True},
  ],
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
  "source_hashes": {
    "event_interpose.cpp": sha(root / "event_interpose.cpp"),
    "wait_dag_v3_intervention.py": sha(root / "wait_dag_v3_intervention.py"),
    "event_delay_v3_unit.cpp": sha(root / "tests/event_delay_v3_unit.cpp"),
  },
  "vllm_snapshot": subprocess.getoutput("pgrep -a vllm || true"),
  "experiment_pgid": "${THIS_PGID}",
}
open("${PREFLIGHT_JSON}", "w").write(json.dumps(pref, indent=2) + "\\n")
PY

cd "${ROOT}"
./build.sh ascend 2>&1 | tee "${LOGDIR}/build_unit/build_ascend.log"
./build.sh local 2>&1 | tee "${LOGDIR}/build_unit/build_local.log"
export LD_LIBRARY_PATH="${BUILD_DIR}:${LD_LIBRARY_PATH:-}"
LD_PRELOAD="${PRELOAD_UNIT}" "${BUILD_DIR}/event_delay_v3_unit" 2>&1 | tee "${LOGDIR}/build_unit/unit_delay.log"
LD_PRELOAD="${PRELOAD_UNIT}" "${BUILD_DIR}/event_sequence_smoke" 2>&1 | tee "${LOGDIR}/build_unit/unit_seq.log"

export LD_PRELOAD="${PRELOAD_ASCEND}"
python3 "${ROOT}/smoke_delay_v3.py" \
  --trace-dir "${WORKDIR}/smoke_delay_d0" \
  --preload-lib "${V2_SO}" \
  --delay-us 0 --tag d0 \
  --timeout-s 300 2>&1 | tee "${LOGDIR}/smoke/smoke_d0.log"
python3 "${ROOT}/smoke_delay_v3.py" \
  --trace-dir "${WORKDIR}/smoke_delay_d2" \
  --preload-lib "${V2_SO}" \
  --delay-us 2000 --tag d2 \
  --timeout-s 300 2>&1 | tee "${LOGDIR}/smoke/smoke_d2.log"

run_train() {
  local tag="$1"
  local delay_us="$2"
  local wdir="${WORKDIR}/${tag}"
  local ldir="${LOGDIR}/runs/${tag}"
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
  local db
  db="$(find "${wdir}/out/args_on" -name ascend_pytorch_profiler_0.db | head -1)"
  python3 "${ROOT}/event_preload_v6_analyze.py" \
    --run-id "${RUN_ID}_${tag}" \
    --trace-dir "${wdir}/event_trace" \
    --db "${db}" \
    --analysis-dir "${wdir}/analysis_v6" \
    --log-dir "${ldir}" \
    --profile-window "${wdir}/out/args_on/profile_window.json" \
    2>&1 | tee "${ldir}/v6.log" || true
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
  python3 "${ROOT}/wait_dag_v3_intervention.py" \
    --manifest "${mpath}" \
    --out-dir "${WORKDIR}/per_run/${tag}" 2>&1 | tee "${LOGDIR}/analysis/analyze_${tag}.log"
}

check_d0_structure() {
  local tag="$1"
  local id_csv="${WORKDIR}/per_run/${tag}/intervention_identity.csv"
  python3 - <<'PY' "${id_csv}" "${TARGET_COMM}"
import csv, json, sys
rows = list(csv.DictReader(open(sys.argv[1])))
if not rows:
    raise SystemExit("D0 identity missing")
r = rows[0]
if r.get("status") != "OK":
    raise SystemExit(f"D0 status={r.get('status')}")
if r.get("comm_op") != sys.argv[2]:
    raise SystemExit("wrong comm")
if r.get("generation_closure_status") != "VALID":
    raise SystemExit("generation not valid")
key = json.loads(r["normalized_structure_key"])
if key.get("active_success_ordinal") != 4 or key.get("wait_active_success_ordinal") != 4:
    raise SystemExit("ordinal mismatch in structure key")
print("D0_STRUCTURE_OK", r["normalized_structure_key"][:80])
PY
}

check_d2_inject_align() {
  local tag="$1"
  python3 - <<'PY' "${WORKDIR}/per_run/${tag}"
import csv, sys
from pathlib import Path
root = Path(sys.argv[1])
id_rows = list(csv.DictReader(open(root / "intervention_identity.csv")))
if not id_rows:
    raise SystemExit("D2 identity missing")
r = id_rows[0]
if r.get("status") != "OK":
    raise SystemExit(f"D2 status={r.get('status')}")
inject = int(r.get("inject_preload_cs") or 0)
identity = int(r.get("preload_record_cs") or 0)
if inject != identity:
    raise SystemExit(f"D2 inject_cs={inject} != identity_cs={identity}")
print("D2_INJECT_ALIGN_OK", inject, identity)
PY
}

run_train "control_d0_b1" 0
analyze_one "control_d0_b1" "D0"
check_d0_structure "control_d0_b1"

run_train "small_d2ms" 2000
analyze_one "small_d2ms" "D2"
python3 - <<'PY' "${WORKDIR}/small_d2ms/event_trace" "${WORKDIR}/per_run/small_d2ms"
import csv, glob, json, sys
from pathlib import Path
audit_files = sorted(glob.glob(sys.argv[1] + "/rank_0_pid_*.delay_audit.json"))
if not audit_files:
    raise SystemExit("D2 missing delay audit")
a = json.loads(Path(audit_files[0]).read_text())
if int(a.get("match_count", 0)) != 1:
    raise SystemExit(f"D2 match_count={a.get('match_count')}")
inject = int(a.get("last_inject_preload_cs", 0) or 0)
id_rows = list(csv.DictReader(open(sys.argv[2] + "/intervention_identity.csv")))
if not id_rows or id_rows[0].get("status") != "OK":
    raise SystemExit(f"D2 identity invalid: {id_rows[0].get('status') if id_rows else 'missing'}")
identity = int(id_rows[0].get("preload_record_cs") or 0)
if inject != identity:
    raise SystemExit(f"D2 inject_cs={inject} != identity_cs={identity}")
cb_ns = 0
if a.get("callback_enter_monotonic_ns") and a.get("callback_exit_monotonic_ns"):
    cb_ns = int(a["callback_exit_monotonic_ns"]) - int(a["callback_enter_monotonic_ns"])
cb_ms = cb_ns / 1e6
if not (1.5 <= cb_ms <= 4.0):
    raise SystemExit(f"D2 callback {cb_ms}ms out of [1.5,4.0]")
print("D2_PILOT_OK callback_ms", cb_ms, "inject_cs", inject, "identity_cs", identity)
PY

GATE_OK=0
if python3 - <<'PY' "${WORKDIR}"; then GATE_OK=1; fi
import csv, json, sys
from pathlib import Path
root = Path(sys.argv[1])
d0_id = list(csv.DictReader(open(root / "per_run/control_d0_b1/intervention_identity.csv")))[0]
d2_id = list(csv.DictReader(open(root / "per_run/small_d2ms/intervention_identity.csv")))[0]
if d0_id.get("status") != "OK" or d2_id.get("status") != "OK":
    raise SystemExit("D0/D2 identity not OK")
d2_cb = list(csv.DictReader(open(root / "per_run/small_d2ms/callback_realization.csv")))[0]
cb_ns = int(d2_cb.get("callback_ns") or 0)
if not (1_500_000 <= cb_ns <= 4_000_000):
    raise SystemExit(f"D2 callback_ns {cb_ns} out of range")
nw = list(csv.DictReader(open(root / "per_run/control_d0_b1/node_wallclock.csv")))
rec = next(x for x in nw if x["node"]=="record_task")
fifo = next(x for x in nw if x["node"]=="fifo_successor")
slack_ns = int(fifo["start_offset_from_comm_end_ns"]) - int(rec["end_offset_from_comm_end_ns"])
if 25_000_000 - slack_ns < 5_000_000:
    raise SystemExit(f"D25 gate fail slack_ns={slack_ns}")
open(root / "gate_d25.json", "w").write(json.dumps({
    "slack_record_to_next_ns": slack_ns,
    "d2_callback_ns": cb_ns,
    "d0_structure_key": d0_id.get("normalized_structure_key"),
    "d2_inject_cs": d2_id.get("inject_preload_cs"),
    "d2_identity_cs": d2_id.get("preload_record_cs"),
})+"\n")
print("GATE_D25_PASS slack_ns", slack_ns)
PY

if [[ "${GATE_OK}" != "1" ]]; then
  log "STOP: D25 gate not satisfied"
  mkdir -p "${MYPORTAL}"
  cp -a "${WORKDIR}/per_run/"* "${MYPORTAL}/" 2>/dev/null || true
  cp "${PREFLIGHT_JSON}" "${MYPORTAL}/" 2>/dev/null || true
  cp -a "${LOGDIR}/" "${MYPORTAL}/logs/" 2>/dev/null || true
  exit 4
fi

run_train "effect_d25_b1" 25000
run_train "control_d0_b2" 0
run_train "effect_d25_b2" 25000
run_train "control_d0_b3" 0
run_train "effect_d25_b3" 25000

for spec in "control_d0_b1:D0" "control_d0_b2:D0" "control_d0_b3:D0" "small_d2ms:D2" \
            "effect_d25_b1:D25" "effect_d25_b2:D25" "effect_d25_b3:D25"; do
  tag="${spec%%:*}"
  cond="${spec##*:}"
  analyze_one "${tag}" "${cond}"
done

MANIFEST="${WORKDIR}/manifest.json"
python3 - <<PY
import json
runs = []
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
  --out-dir "${ANALYSIS_DIR}" 2>&1 | tee "${LOGDIR}/analysis/final_analysis.log"

python3 - <<'PY' "${ANALYSIS_DIR}" "${RUN_ID}"
import csv, json, sys
from pathlib import Path
ad = Path(sys.argv[1])
run_id = sys.argv[2]
paired = list(csv.DictReader(open(ad / "paired_effects.csv")))
id_by_run = {r["run_id"]: r for r in csv.DictReader(open(ad / "intervention_identity.csv"))}
claims = [
    "# claims (V3.1 FIX — pending dual Auditor)",
    "",
    f"- RUN_ID: {run_id}",
    "- Old V3 / 081200 V3.1 packages **not** used in statistics.",
    "",
]
for p in paired:
    st = p.get("status", "OK")
    succ = p.get("successor_shift_ns", "")
    claims.append(f"- block {p['block']}: status={st} successor_shift_ns={succ}")
(ad / "claims.md").write_text("\\n".join(claims) + "\\n")

casebook = ["# delay_casebook (V3.1 FIX)", ""]
for row in csv.DictReader(open(ad / "intervention_identity.csv")):
    inj = row.get("inject_preload_cs", "")
    rec_cs = row.get("preload_record_cs", "")
    casebook.append(
        f"## {row['run_id']}\\n"
        f"- status={row.get('status')}\\n"
        f"- inject_preload_cs={inj} preload_record_cs={rec_cs}\\n"
        f"- normalized_structure_key: {row.get('normalized_structure_key')}\\n"
        f"- record_issuing_tid={row.get('record_issuing_tid')} arm_tid={row.get('arm_tid')}\\n"
        f"- tasks record={row['record_task_rowid']} wait={row['wait_task_rowid']} fifo={row['fifo_successor_rowid']}\\n"
    )
for p in paired:
    if p.get("status") != "OK":
        continue
    d25 = id_by_run.get(p["d25_run_id"], {})
    inj = int(d25.get("inject_preload_cs") or 0)
    ident = int(d25.get("preload_record_cs") or 0)
    cb_ns = int(d25.get("callback_ns") or 0)
    rec_shift = int(p.get("record_shift_ns") or 0)
    if inj == ident and cb_ns >= 20_000_000 and abs(rec_shift) < 1_000_000:
        casebook.append(
            f"## SCENARIO_B {p['block']}\\n"
            "- 注入已打到被测链但 TASK 域无位移（inject_cs==identity_cs，callback~25ms，record_shift≈0）。\\n"
            "- 不得写成锥被证伪；不得改 delay/comm。\\n"
        )
(ad / "delay_casebook.md").write_text("\\n".join(casebook) + "\\n")
PY

python3 - <<'PY' "${WORKDIR}" "${MYPORTAL}/hash_manifest.json"
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
out = []
for p in sorted(root.rglob("*")):
    if not p.is_file():
        continue
    if p.suffix in {".db", ".bin"} or "ascend_pytorch_profiler" in p.name:
        rel = str(p.relative_to(root))
        out.append({"path": rel, "size": p.stat().st_size, "sha256": None, "note": "large_remote_only"})
        continue
    if p.stat().st_size > 50_000_000:
        out.append({"path": str(p.relative_to(root)), "size": p.stat().st_size, "sha256": None})
        continue
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    out.append({"path": str(p.relative_to(root)), "size": p.stat().st_size, "sha256": h})
Path(sys.argv[2]).write_text(json.dumps({"files": out}, indent=2) + "\n")
PY

mkdir -p "${MYPORTAL}"
cp -a "${ANALYSIS_DIR}/"* "${MYPORTAL}/"
cp "${PREFLIGHT_JSON}" "${MYPORTAL}/"
cp -a "${LOGDIR}/" "${MYPORTAL}/logs/"
for tag in control_d0_b1 control_d0_b2 control_d0_b3 small_d2ms effect_d25_b1 effect_d25_b2 effect_d25_b3; do
  audit="$(python3 - <<PY
from pathlib import Path
import sys
sys.path.insert(0, "${ROOT}")
from wait_dag_v3_intervention import select_delay_audit_path
p = select_delay_audit_path(Path("${WORKDIR}/${tag}/event_trace"))
print(p or "")
PY
)"
  if [[ -n "${audit}" && -f "${audit}" ]]; then
    cp "${audit}" "${MYPORTAL}/delay_audit_runs/${tag}.json"
  fi
done

trap - EXIT INT TERM
log "DONE RUN_ID=${RUN_ID} MYPORTAL=${MYPORTAL}"
