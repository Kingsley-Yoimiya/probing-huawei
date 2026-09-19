#!/usr/bin/env bash
# Sync V4.5 sources to npu-dev-1, run fresh RUN, pull clean myportal evidence.
set -euo pipefail

SRC="/Users/yinjinrun/Codespace/probing-huawei/experiments/event_handle_recovery"
REMOTE_HOST="${REMOTE_HOST:-npu-dev-1}"
CONTAINER="${CONTAINER:-montyyin_reduce_ws16}"
REMOTE_DIR="/root/event_handle_recovery_v45"
UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_wait_dag_v4_5_native_or_alt_contract}"
BUILD_ID="${BUILD_ID:-${UTC_STAMP}_build_v4_5}"
LOCAL_MYPORTAL="/Users/yinjinrun/Codespace/myportal/results/npu-dev-1/${RUN_ID}"
REMOTE_WORKDIR="/tmp/${RUN_ID}"
SSH_QUIET=(-o LogLevel=ERROR -o RequestTTY=no)

echo "Sync V4.5 -> ${REMOTE_HOST}:${CONTAINER}:${REMOTE_DIR}"
ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" "sudo docker exec ${CONTAINER} mkdir -p ${REMOTE_DIR}"
tar -C "${SRC}" -cf - \
  build.sh event_interpose.cpp event_trace_format.h device_work.cpp device_work.h device_work_stub.cpp \
  kernels real_dlsym_resolver.c min_create.c \
  elf_section_parser.py kernel_disasm_audit_v4_5.py native_disasm_tool_probe_v4_5.py \
  run_d51_wait_dag_v4_5.sh run_d51_wait_dag_v4_4.sh \
  train_event_preload.py smoke_device_work.py smoke_kernel_checksum.py smoke_reachability_v4_4.py \
  kernel_disasm_audit_v4_4.py task_projection_v4_4.py \
  d51_work_unit_reference.py dose_calibrate_v4_4.py dose_calibrate_v4_3.py dose_calibrate_v4_2.py \
  wait_dag_v4_intervention.py wait_dag_v4_2_reverse_candidate.py \
  analyze_event_pairs.py classify_intervening_tasks.py event_preload_v6_analyze.py \
  wait_dag_v2_build.py wait_dag_v2_fifo.py wait_dag_v2_casebook.py wait_dag_v2_cone.py \
  wait_dag_v2_schema.py a6_predicate_v6.py wait_dag_schema.py \
  preload_bindings.py kernel_load_probe.py \
  tests/test_wait_dag_v4_5_parser.py tests/test_wait_dag_v4_4.py tests/test_wait_dag_v4_3.py \
  tests/fake_acl.cpp tests/fake_rt.cpp tests/fake_loader.cpp tests/stubs \
  | ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" "sudo docker exec -i ${CONTAINER} tar -C ${REMOTE_DIR} -xf -"

echo "RUN_ID=${RUN_ID} BUILD_ID=${BUILD_ID}"
echo "Launch runner in container (expected GM gate ~15-30 min if pass continues; STOP ~20s if GM fail)"
ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" \
  "sudo docker exec ${CONTAINER} bash -lc 'cd ${REMOTE_DIR} && chmod +x run_d51_wait_dag_v4_5.sh kernels/build_kernel.sh build.sh && RUN_ID=${RUN_ID} BUILD_ID=${BUILD_ID} ./run_d51_wait_dag_v4_5.sh'" \
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
    idx=raw.find("[")
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
      echo "SKIP invalid JSON ${rel}" >&2
    fi
  fi
}

for f in binary_seal.json structure_evidence.json reachability_proofs.json \
  profiler_scaling.json reachability_gate.json frozen_dose_v4_5.json \
  dose_iters_v4_5.json native_disasm_tool_probe.json STOP.json; do
  pull_file "${f}"
done

# task_projection lives under logs; pull latest copy to myportal root
TASK_JSON=$(ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" \
  "sudo docker exec ${CONTAINER} bash -lc 'ls -1 ${REMOTE_WORKDIR}/logs/*/task_projection/task_projection.json 2>/dev/null | tail -1'" || true)
if [[ -n "${TASK_JSON}" ]]; then
  ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" \
    "sudo docker exec ${CONTAINER} cat ${TASK_JSON}" > "${LOCAL_MYPORTAL}/task_projection.json.tmp"
  if python3 -c "import json; json.load(open('${LOCAL_MYPORTAL}/task_projection.json.tmp'))"; then
    mv "${LOCAL_MYPORTAL}/task_projection.json.tmp" "${LOCAL_MYPORTAL}/task_projection.json"
    echo "PULLED task_projection.json"
  else
    rm -f "${LOCAL_MYPORTAL}/task_projection.json.tmp"
  fi
fi

# parser summary (text, not JSON)
mkdir -p "${LOCAL_MYPORTAL}"
LOG_GLOB=$(ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" \
  "sudo docker exec ${CONTAINER} bash -lc 'ls -1d ${REMOTE_WORKDIR}/logs/*/parser_tests/summary.txt 2>/dev/null | head -1'" || true)
if [[ -n "${LOG_GLOB}" ]]; then
  ssh "${SSH_QUIET[@]}" "${REMOTE_HOST}" \
    "sudo docker exec ${CONTAINER} cat ${LOG_GLOB}" > "${LOCAL_MYPORTAL}/parser_tests_summary.txt"
fi

echo "SYNC_RUN_OK RUN_ID=${RUN_ID} LOCAL_MYPORTAL=${LOCAL_MYPORTAL}"
