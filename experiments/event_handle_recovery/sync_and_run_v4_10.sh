#!/usr/bin/env bash
# Sync V4.10 sources to npu-dev-1, run fresh RUN, pull full myportal publish tree (even on STOP).
set -euo pipefail

SRC="/Users/yinjinrun/Codespace/probing-huawei/experiments/event_handle_recovery"
REMOTE_HOST="${REMOTE_HOST:-npu-dev-1}"
CONTAINER="${CONTAINER:-montyyin_reduce_ws16}"
REMOTE_DIR="/root/event_handle_recovery_v410"
UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_wait_dag_v4_10_c3_ordinal_studentized}"
BUILD_ID="${BUILD_ID:-${UTC_STAMP}_build_v4_10}"
LOCAL_MYPORTAL="/Users/yinjinrun/Codespace/myportal/results/npu-dev-1/${RUN_ID}"
REMOTE_WORKDIR="/tmp/${RUN_ID}"
REMOTE_MYPORTAL="/workspace/myportal/results/npu-dev-1/${RUN_ID}"
SSH_QUIET=(-o LogLevel=ERROR -o RequestTTY=no)
LAUNCHER_LOG="/tmp/${RUN_ID}_launcher.log"

echo "Sync V4.10 -> ${REMOTE_HOST}:${CONTAINER}:${REMOTE_DIR}"
ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" "sudo docker exec ${CONTAINER} mkdir -p ${REMOTE_DIR}"
tar -C "${SRC}" -cf - \
  build.sh event_interpose.cpp event_trace_format.h device_work.cpp device_work.h device_work_stub.cpp \
  kernels real_dlsym_resolver.c min_create.c \
  elf_section_parser.py kernel_disasm_audit_v4_5.py native_disasm_tool_probe_v4_5.py \
  train_event_preload.py smoke_post_wait_v4_7.py \
  smoke_device_work.py smoke_reachability_v4_4.py \
  kernel_disasm_audit_v4_4.py task_projection_v4_4.py \
  d51_work_unit_reference.py dose_calibrate_v4_4.py \
  wait_dag_v4_intervention.py wait_dag_v4_6_intervention.py wait_dag_v4_7_intervention.py \
  wait_dag_v4_8_intervention.py wait_dag_v4_9_intervention.py wait_dag_v4_10_intervention.py \
  wait_dag_v4_2_reverse_candidate.py \
  analyze_event_pairs.py classify_intervening_tasks.py event_preload_v6_analyze.py \
  wait_dag_v2_build.py wait_dag_v2_fifo.py wait_dag_v2_casebook.py wait_dag_v2_cone.py \
  wait_dag_v2_schema.py a6_predicate_v6.py wait_dag_schema.py \
  preload_bindings.py kernel_load_probe.py \
  run_d51_wait_dag_v4_10.sh \
  tests/test_wait_dag_v4_7_unit.cpp tests/test_wait_dag_v4_7.py tests/test_wait_dag_v4_8.py \
  tests/test_wait_dag_v4_9.py tests/test_wait_dag_v4_10.py \
  tests/test_wait_dag_v4_6_unit.cpp tests/test_wait_dag_v4_5_parser.py \
  tests/fake_acl.cpp tests/fake_rt.cpp tests/fake_loader.cpp tests/stubs \
  | ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" "sudo docker exec -i ${CONTAINER} tar -C ${REMOTE_DIR} -xf -"

echo "RUN_ID=${RUN_ID} BUILD_ID=${BUILD_ID}"
echo "Expected: unit+smoke 10-25min; b1 20-50min; b2/b3 after b1 pass"
set +e
ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" \
  "sudo docker exec ${CONTAINER} bash -lc 'cd ${REMOTE_DIR} && chmod +x run_d51_wait_dag_v4_10.sh kernels/build_kernel.sh build.sh && RUN_ID=${RUN_ID} BUILD_ID=${BUILD_ID} ./run_d51_wait_dag_v4_10.sh'" \
  2>&1 | tee "${LAUNCHER_LOG}"
REMOTE_RC=${PIPESTATUS[0]}
set -e
if [[ "${REMOTE_RC}" -ne 0 ]]; then
  echo "REMOTE_RUN_STOP_OR_FAIL rc=${REMOTE_RC} (pulling package anyway)"
fi

mkdir -p "${LOCAL_MYPORTAL}"
echo "Pull full publish tree from container myportal"
if ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" \
  "sudo docker exec ${CONTAINER} test -d ${REMOTE_MYPORTAL}" 2>/dev/null; then
  ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" \
    "sudo docker exec ${CONTAINER} tar -C ${REMOTE_MYPORTAL} -cf - ." \
    | tar -C "${LOCAL_MYPORTAL}" -xf -
  echo "PULLED myportal tree"
else
  echo "WARN: container myportal missing, fallback WORKDIR root files"
  pull_file() {
    local rel="$1"
    local dst="${LOCAL_MYPORTAL}/$(basename "${rel}")"
    if ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" \
      "sudo docker exec ${CONTAINER} test -f ${REMOTE_WORKDIR}/${rel}" 2>/dev/null; then
      ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" \
        "sudo docker exec ${CONTAINER} cat ${REMOTE_WORKDIR}/${rel}" > "${dst}"
      echo "PULLED ${rel}"
    fi
  }
  for f in STOP.json gate_summary.json hash_manifest.json package_receipt.json; do
    pull_file "${f}"
  done
fi

echo "Verify three-tree hash consistency"
VERIFY_LOG="/tmp/${RUN_ID}_verify.log"
REMOTE_MYPORTAL="${REMOTE_MYPORTAL}" REMOTE_WORKDIR="${REMOTE_WORKDIR}" LOCAL_MYPORTAL="${LOCAL_MYPORTAL}" \
python3 - <<'PY' | tee "${VERIFY_LOG}"
import hashlib, json, os, subprocess, sys
from pathlib import Path

remote_host = os.environ.get("REMOTE_HOST", "npu-dev-1")
container = os.environ.get("CONTAINER", "montyyin_reduce_ws16")
local = Path(os.environ["LOCAL_MYPORTAL"])
manifest_path = local / "hash_manifest.json"
if not manifest_path.exists():
    print(json.dumps({"error": "local hash_manifest missing"}))
    sys.exit(1)
manifest = json.loads(manifest_path.read_text())
ssh = ["ssh", "-o", "LogLevel=ERROR", "-o", "RequestTTY=no", remote_host]

def remote_sha(tree_base, rel):
    cmd = ssh + [f"sudo docker exec {container} bash -lc 'python3 - <<\"INNER\"\nimport hashlib\nfrom pathlib import Path\np=Path(\"{tree_base}\")/\"{rel}\"\nif not p.is_file():\n    print(\"MISSING\")\nelse:\n    h=hashlib.sha256()\n    with open(p,\"rb\") as f:\n        for c in iter(lambda:f.read(1<<20), b\"\"): h.update(c)\n    print(h.hexdigest())\nINNER'"]
    out = subprocess.check_output(cmd, text=True).strip()
    return out

def local_sha(rel):
    p = local / rel
    if not p.is_file():
        return "MISSING"
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()

workdir_miss = myportal_miss = local_miss = sha_mismatch = 0
for e in manifest["files"]:
    rel = e["path"]
    ls = local_sha(rel)
    if ls == "MISSING":
        local_miss += 1
        continue
    if ls != e["sha256"]:
        sha_mismatch += 1
    try:
        ws = remote_sha(os.environ["REMOTE_WORKDIR"], rel)
        if ws == "MISSING":
            workdir_miss += 1
        elif ws != e["sha256"]:
            sha_mismatch += 1
        ms = remote_sha(os.environ["REMOTE_MYPORTAL"], rel)
        if ms == "MISSING":
            myportal_miss += 1
        elif ms != e["sha256"]:
            sha_mismatch += 1
    except Exception as ex:
        print(json.dumps({"verify_error": str(ex)}))
        sys.exit(2)

report = {
    "manifest_files": manifest["file_count"],
    "local_present": manifest["file_count"] - local_miss,
    "workdir_miss": workdir_miss,
    "myportal_miss": myportal_miss,
    "sha_mismatch": sha_mismatch,
    "three_tree_closed": workdir_miss == 0 and myportal_miss == 0 and local_miss == 0 and sha_mismatch == 0,
}
print(json.dumps(report, indent=2))
if not report["three_tree_closed"]:
    sys.exit(3)
PY

echo "SYNC_RUN_DONE RUN_ID=${RUN_ID} REMOTE_RC=${REMOTE_RC} LOCAL_MYPORTAL=${LOCAL_MYPORTAL}"
exit "${REMOTE_RC}"
