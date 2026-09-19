#!/usr/bin/env bash
# Re-analyze existing V3.1 FIX profiler DBs with corrected pre_comm_same_stream selector.
# Does not retrain; does not stop vLLM.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_RUN_ID="${SOURCE_RUN_ID:-20260824T090000Z_d51_wait_dag_v3_1_fix}"
RUN_ID="${RUN_ID:-${SOURCE_RUN_ID}_ctrlfix}"
SOURCE_WORKDIR="/tmp/${SOURCE_RUN_ID}"
WORKDIR="/tmp/${RUN_ID}"
UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOGDIR="${WORKDIR}/logs/${UTC_STAMP}"
ANALYSIS_DIR="${WORKDIR}/analysis"
MYPORTAL="/workspace/myportal/results/npu-dev-1/${RUN_ID}"
LOCAL_MYPORTAL_SRC="/Users/yinjinrun/Codespace/myportal/results/npu-dev-1/${SOURCE_RUN_ID}"

mkdir -p "${LOGDIR}/analysis" "${ANALYSIS_DIR}" "${MYPORTAL}/delay_audit_runs"
exec > >(tee -a "${LOGDIR}/reanalyze.log") 2>&1

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

if [[ ! -d "${SOURCE_WORKDIR}/control_d0_b1" ]]; then
  log "STOP: source WORKDIR missing: ${SOURCE_WORKDIR}"
  exit 2
fi

log "CTRLFIX reanalyze SOURCE=${SOURCE_RUN_ID} OUT=${RUN_ID}"

cp -a "${SOURCE_WORKDIR}/preflight.json" "${WORKDIR}/" 2>/dev/null || true
python3 - <<PY
import json
from pathlib import Path
p = Path("${WORKDIR}/preflight.json")
if p.exists():
    d = json.loads(p.read_text())
else:
    d = {"run_id": "${SOURCE_RUN_ID}"}
d["ctrlfix_reanalyze"] = {
    "source_run_id": "${SOURCE_RUN_ID}",
    "output_run_id": "${RUN_ID}",
    "fix": "pre_comm_same_stream_before_comm_entry",
    "utc_stamp": "${UTC_STAMP}",
}
d["delay_audit_authoritative_source"] = "delay_audit.csv (rank_0 sidecar via select_delay_audit_path)"
p.write_text(json.dumps(d, indent=2) + "\\n")
PY

MANIFEST="${WORKDIR}/manifest.json"
python3 - <<PY
import json
runs = []
for cond, tag in [
    ("D0","control_d0_b1"),("D2","small_d2ms"),
    ("D25","effect_d25_b1"),("D0","control_d0_b2"),("D25","effect_d25_b2"),
    ("D0","control_d0_b3"),("D25","effect_d25_b3"),
]:
    runs.append({
        "run_id": f"${SOURCE_RUN_ID}_{tag}",
        "condition": cond,
        "run_dir": f"${SOURCE_WORKDIR}/{tag}",
    })
pairs = [
  ["b1", f"${SOURCE_RUN_ID}_control_d0_b1", f"${SOURCE_RUN_ID}_effect_d25_b1"],
  ["b2", f"${SOURCE_RUN_ID}_control_d0_b2", f"${SOURCE_RUN_ID}_effect_d25_b2"],
  ["b3", f"${SOURCE_RUN_ID}_control_d0_b3", f"${SOURCE_RUN_ID}_effect_d25_b3"],
]
open("${MANIFEST}", "w").write(json.dumps({"runs": runs, "pairs": pairs}, indent=2) + "\\n")
PY

python3 "${ROOT}/wait_dag_v3_intervention.py" \
  --manifest "${MANIFEST}" \
  --out-dir "${ANALYSIS_DIR}" 2>&1 | tee "${LOGDIR}/analysis/final_analysis.log"

# Preserve pre-ctrlfix paired_effects for Auditor cross-check.
if [[ -f "${LOCAL_MYPORTAL_SRC}/paired_effects.csv" ]]; then
  cp "${LOCAL_MYPORTAL_SRC}/paired_effects.csv" "${ANALYSIS_DIR}/paired_effects_pre_ctrlfix.csv"
elif [[ -f "/workspace/myportal/results/npu-dev-1/${SOURCE_RUN_ID}/paired_effects.csv" ]]; then
  cp "/workspace/myportal/results/npu-dev-1/${SOURCE_RUN_ID}/paired_effects.csv" \
    "${ANALYSIS_DIR}/paired_effects_pre_ctrlfix.csv"
fi

python3 - <<'PY' "${ANALYSIS_DIR}" "${RUN_ID}" "${SOURCE_RUN_ID}"
import csv, json, sys
from pathlib import Path
ad, run_id, source_id = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
paired = list(csv.DictReader(open(ad / "paired_effects.csv")))
controls = list(csv.DictReader(open(ad / "control_nodes.csv")))
id_by_run = {r["run_id"]: r for r in csv.DictReader(open(ad / "intervention_identity.csv"))}

def ctrl_shift(block: str, role: str) -> int | None:
    d0_id = f"{source_id}_control_d0_{block}"
    d25_id = f"{source_id}_effect_d25_{block}"
    d0 = next((r for r in controls if r["run_id"] == d0_id and r["control_role"] == role), None)
    d25 = next((r for r in controls if r["run_id"] == d25_id and r["control_role"] == role), None)
    if not d0 or not d25:
        return None
    return int(d25["end_offset_from_comm_end_ns"]) - int(d0["end_offset_from_comm_end_ns"])

shifts = {}
for block in ("b1", "b2", "b3"):
    shifts[block] = {
        "pre_wait_compute_shift_ns": ctrl_shift(block, "pre_wait_compute"),
        "pre_comm_same_stream_shift_ns": ctrl_shift(block, "pre_comm_same_stream"),
    }

succ_vals = [int(p["successor_shift_ns"]) for p in paired if p.get("status") == "OK"]
med_succ = sorted(succ_vals)[len(succ_vals) // 2] if succ_vals else 0
tol_ns = max(1_000_000, int(0.25 * med_succ))

claims = [
    "# claims (V3.1 FIX ctrlfix — pending dual Auditor)",
    "",
    f"- RUN_ID: {run_id} (reanalyze of {source_id})",
    f"- pre_comm selector: comm-entry FIFO predecessor on record stream (not Record-1)",
    f"- delay_audit authoritative: delay_audit.csv / rank_0 sidecar",
    "",
]
for block, s in shifts.items():
    pc = s["pre_comm_same_stream_shift_ns"]
    pw = s["pre_wait_compute_shift_ns"]
    claims.append(
        f"- block {block}: pre_comm_shift_ns={pc} pre_wait_shift_ns={pw} "
        f"(tol={tol_ns} ns)"
    )
(ad / "claims.md").write_text("\\n".join(claims) + "\\n")

casebook = [
    "# delay_casebook (V3.1 FIX ctrlfix)",
    "",
    f"- source_run: {source_id}",
    f"- ctrlfix_run: {run_id}",
    "- pre_comm_same_stream: first comm TASK entry on record stream, FIFO predecessor before comm.start",
    "- paired_effects_pre_ctrlfix.csv: Auditor cross-check vs pre-fix selector",
    "",
]
for row in csv.DictReader(open(ad / "control_nodes.csv")):
    if row["control_role"] != "pre_comm_same_stream":
        continue
    casebook.append(
        f"## {row['run_id']} pre_comm\\n"
        f"- rule={row['selection_rule']} rowid={row['task_rowid']} stream={row['stream_id']}\\n"
        f"- end_offset_from_comm_end_ns={row['end_offset_from_comm_end_ns']}\\n"
    )
(ad / "delay_casebook.md").write_text("\\n".join(casebook) + "\\n")

summary = json.loads((ad / "v3_1_summary.json").read_text())
summary["ctrlfix"] = {
    "source_run_id": source_id,
    "control_shifts_ns": shifts,
    "median_successor_shift_ns": med_succ,
    "control_tol_ns": tol_ns,
}
(ad / "v3_1_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps({"control_shifts": shifts, "tol_ns": tol_ns}, indent=2))
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

cp -a "${ANALYSIS_DIR}/"* "${MYPORTAL}/"
cp "${WORKDIR}/preflight.json" "${MYPORTAL}/"
cp -a "${LOGDIR}/" "${MYPORTAL}/logs/"

for tag in control_d0_b1 control_d0_b2 control_d0_b3 small_d2ms effect_d25_b1 effect_d25_b2 effect_d25_b3; do
  audit="$(python3 - <<PY
from pathlib import Path
import sys
sys.path.insert(0, "${ROOT}")
from wait_dag_v3_intervention import select_delay_audit_path
p = select_delay_audit_path(Path("${SOURCE_WORKDIR}/${tag}/event_trace"))
print(p or "")
PY
)"
  if [[ -n "${audit}" && -f "${audit}" ]]; then
    cp "${audit}" "${MYPORTAL}/delay_audit_runs/${tag}.json"
  else
    log "WARN: no rank0 delay_audit for ${tag}"
  fi
done

log "DONE CTRLFIX RUN_ID=${RUN_ID} MYPORTAL=${MYPORTAL}"
