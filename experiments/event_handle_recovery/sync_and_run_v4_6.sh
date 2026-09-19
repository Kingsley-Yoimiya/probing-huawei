#!/usr/bin/env bash
# Sync V4.6 sources to npu-dev-1, run fresh RUN, pull myportal evidence.
set -euo pipefail

SRC="/Users/yinjinrun/Codespace/probing-huawei/experiments/event_handle_recovery"
REMOTE_HOST="${REMOTE_HOST:-npu-dev-1}"
CONTAINER="${CONTAINER:-montyyin_reduce_ws16}"
REMOTE_DIR="/root/event_handle_recovery_v46"
UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_wait_dag_v4_6_post_wait_comm_stream_device_work}"
BUILD_ID="${BUILD_ID:-${UTC_STAMP}_build_v4_6}"
LOCAL_MYPORTAL="/Users/yinjinrun/Codespace/myportal/results/npu-dev-1/${RUN_ID}"
REMOTE_WORKDIR="/tmp/${RUN_ID}"
SSH_QUIET=(-o LogLevel=ERROR -o RequestTTY=no)

echo "Sync V4.6 -> ${REMOTE_HOST}:${CONTAINER}:${REMOTE_DIR}"
ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" "sudo docker exec ${CONTAINER} mkdir -p ${REMOTE_DIR}"
tar -C "${SRC}" -cf - \
  build.sh event_interpose.cpp event_trace_format.h device_work.cpp device_work.h device_work_stub.cpp \
  kernels real_dlsym_resolver.c min_create.c \
  elf_section_parser.py kernel_disasm_audit_v4_5.py native_disasm_tool_probe_v4_5.py \
  run_d51_wait_dag_v4_6.sh smoke_post_wait_v4_6.py \
  train_event_preload.py smoke_device_work.py smoke_reachability_v4_4.py \
  kernel_disasm_audit_v4_4.py task_projection_v4_4.py \
  d51_work_unit_reference.py dose_calibrate_v4_4.py \
  wait_dag_v4_intervention.py wait_dag_v4_6_intervention.py wait_dag_v4_2_reverse_candidate.py \
  analyze_event_pairs.py classify_intervening_tasks.py event_preload_v6_analyze.py \
  wait_dag_v2_build.py wait_dag_v2_fifo.py wait_dag_v2_casebook.py wait_dag_v2_cone.py \
  wait_dag_v2_schema.py a6_predicate_v6.py wait_dag_schema.py \
  preload_bindings.py kernel_load_probe.py \
  tests/test_wait_dag_v4_6_unit.cpp tests/test_wait_dag_v4_5_parser.py \
  tests/fake_acl.cpp tests/fake_rt.cpp tests/fake_loader.cpp tests/stubs \
  | ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" "sudo docker exec -i ${CONTAINER} tar -C ${REMOTE_DIR} -xf -"

echo "RUN_ID=${RUN_ID} BUILD_ID=${BUILD_ID}"
echo "Expected: unit 2-5min; smoke 10-25min; b1 20-50min"
ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" \
  "sudo docker exec ${CONTAINER} bash -lc 'cd ${REMOTE_DIR} && chmod +x run_d51_wait_dag_v4_6.sh kernels/build_kernel.sh build.sh && RUN_ID=${RUN_ID} BUILD_ID=${BUILD_ID} ./run_d51_wait_dag_v4_6.sh'" \
  2>&1 | tee "/tmp/${RUN_ID}_launcher.log"

pull_file() {
  local rel="$1"
  local dst="${LOCAL_MYPORTAL}/$(basename "${rel}")"
  mkdir -p "${LOCAL_MYPORTAL}"
  if ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" \
    "sudo docker exec ${CONTAINER} test -f ${REMOTE_WORKDIR}/${rel}" 2>/dev/null; then
    ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" \
      "sudo docker exec ${CONTAINER} cat ${REMOTE_WORKDIR}/${rel}" > "${dst}.tmp"
    if python3 - <<PY
import json, sys
from pathlib import Path
p=Path("${dst}.tmp")
raw=p.read_text(errors="replace")
idx=raw.find("{")
if idx < 0:
    sys.exit(1)
body=raw[idx:]
json.loads(body)
Path("${dst}.tmp").write_text(body)
PY
    then
      mv "${dst}.tmp" "${dst}"
      echo "PULLED ${rel}"
    else
      rm -f "${dst}.tmp"
    fi
  fi
}

for f in STOP.json gate_summary.json frozen_dose_v4_5_ref.json paired_effects_v4_6.csv \
  post_wait_injection_audit.csv intervention_identity.csv kernel_realization.csv \
  node_wallclock.csv run_ledger.csv preflight.json smoke_post_wait.json; do
  pull_file "${f}"
done

LOG_GLOB=$(ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" \
  "sudo docker exec ${CONTAINER} bash -lc 'ls -1d ${REMOTE_WORKDIR}/logs/*/build_unit/unit_v4_6.log 2>/dev/null | head -1'" || true)
if [[ -n "${LOG_GLOB}" ]]; then
  mkdir -p "${LOCAL_MYPORTAL}/logs"
  ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" \
    "sudo docker exec ${CONTAINER} cat ${LOG_GLOB}" > "${LOCAL_MYPORTAL}/logs/unit_v4_6.log"
fi

echo "SYNC_RUN_OK RUN_ID=${RUN_ID} LOCAL_MYPORTAL=${LOCAL_MYPORTAL}"
