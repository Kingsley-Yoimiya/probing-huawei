#!/usr/bin/env bash
# Continue V3.1 after D0+D2 training — re-analyze with fixed audit picker, then D25 block.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_ID="${RUN_ID:?set RUN_ID}"
WORKDIR="/tmp/${RUN_ID}"
LOGDIR="${WORKDIR}/logs/continue_$(date -u +%Y%m%dT%H%M%SZ)"
MYPORTAL="/workspace/myportal/results/npu-dev-1/${RUN_ID}"
ANALYSIS_DIR="${WORKDIR}/analysis"
BUILD_DIR="${ROOT}/build"
V2_SO="${BUILD_DIR}/libacl_event_trace_v2.so"
HELPER_SO="${BUILD_DIR}/libacl_real_dlsym.so"
PRELOAD_ASCEND="${HELPER_SO}:${V2_SO}"

mkdir -p "${LOGDIR}" "${ANALYSIS_DIR}" "${MYPORTAL}"
exec > >(tee -a "${LOGDIR}/continue.log") 2>&1

if [[ -f /usr/local/Ascend/ascend-toolkit/latest/set_env.sh ]]; then
  # shellcheck disable=SC1091
  source /usr/local/Ascend/ascend-toolkit/latest/set_env.sh
fi

THIS_PGID=""
record_pgid() { THIS_PGID="$(ps -o pgid= -p $$ | tr -d ' ')"; }
kill_experiment() {
  if [[ -n "${THIS_PGID}" ]]; then
    kill -TERM "-${THIS_PGID}" 2>/dev/null || true
    sleep 2
    kill -KILL "-${THIS_PGID}" 2>/dev/null || true
  fi
}
trap kill_experiment EXIT INT TERM
record_pgid

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
    --out-dir "${WORKDIR}/per_run/${tag}"
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

for spec in "control_d0_b1:D0" "small_d2ms:D2"; do
  tag="${spec%%:*}"
  cond="${spec##*:}"
  analyze_one "${tag}" "${cond}"
done

python3 - <<'PY' "${WORKDIR}"
import csv, json, sys
from pathlib import Path
root = Path(sys.argv[1])
d2 = list(csv.DictReader(open(root / "per_run/small_d2ms/intervention_identity.csv")))[0]
if int(d2.get("match_count", 0)) != 1:
    raise SystemExit(f"D2 match_count={d2.get('match_count')}")
cb_ns = int(d2.get("callback_ns", 0))
if not (1_500_000 <= cb_ns <= 4_000_000):
    raise SystemExit(f"D2 callback_ns={cb_ns}")
d0 = list(csv.DictReader(open(root / "per_run/control_d0_b1/intervention_identity.csv")))[0]
if d0["normalized_structure_key"] != d2["normalized_structure_key"]:
    print("WARN D0/D2 structure key differ (expected for different blocks)")
nw = list(csv.DictReader(open(root / "per_run/control_d0_b1/node_wallclock.csv")))
rec = next(x for x in nw if x["node"]=="record_task")
fifo = next(x for x in nw if x["node"]=="fifo_successor")
slack_ns = int(fifo["start_offset_from_comm_end_ns"]) - int(rec["end_offset_from_comm_end_ns"])
if 25_000_000 - slack_ns < 5_000_000:
    raise SystemExit(f"D25 gate fail slack_ns={slack_ns}")
open(root / "gate_d25.json", "w").write(json.dumps({"slack_record_to_next_ns": slack_ns, "d2_callback_ns": cb_ns})+"\n")
print("GATE_OK", cb_ns/1e6, "ms slack", slack_ns)
PY

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
  --out-dir "${ANALYSIS_DIR}"

python3 - <<'PY' "${ANALYSIS_DIR}" "${RUN_ID}"
import csv
from pathlib import Path
ad, run_id = Path(sys.argv[1]), sys.argv[2]
import sys
paired = list(csv.DictReader(open(ad / "paired_effects.csv")))
claims = [f"# claims V3.1 {run_id}", ""]
for p in paired:
    claims.append(f"- block {p['block']}: status={p.get('status')} successor_shift_ns={p.get('successor_shift_ns')}")
(ad / "claims.md").write_text("\\n".join(claims) + "\\n")
casebook = ["# delay_casebook V3.1", ""]
for row in csv.DictReader(open(ad / "intervention_identity.csv")):
    casebook.append(f"## {row['run_id']}\\n- key={row.get('normalized_structure_key')}\\n- match={row.get('match_count')} delay_us={row.get('delay_us')}\\n")
(ad / "delay_casebook.md").write_text("\\n".join(casebook) + "\\n")
PY

cp -a "${ANALYSIS_DIR}/"* "${MYPORTAL}/"
cp -a "${LOGDIR}/" "${MYPORTAL}/logs/continue/"
for tag in control_d0_b1 control_d0_b2 control_d0_b3 small_d2ms effect_d25_b1 effect_d25_b2 effect_d25_b3; do
  audit="$(python3 - <<PY
import json,glob
files=glob.glob("${WORKDIR}/${tag}/event_trace/rank_0_pid_*.delay_audit.json")
best=None;bs=-1
for f in files:
 d=json.load(open(f))
 if int(d.get('rank',-1))!=0: continue
 sc=int(d.get('active_record_success_ord',0))
 if int(d.get('match_count',0)): sc+=1000
 if int(d.get('arm_tid',0)): sc+=100
 if sc>bs: bs=sc; best=f
print(best or '')
PY
)"
  [[ -n "${audit}" ]] && cp "${audit}" "${MYPORTAL}/delay_audit_${tag}.json"
done

trap - EXIT INT TERM
echo "DONE continue ${RUN_ID}"
