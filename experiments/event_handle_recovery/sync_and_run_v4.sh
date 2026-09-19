#!/usr/bin/env bash
set -euo pipefail

SRC="/Users/yinjinrun/Codespace/probing-huawei/experiments/event_handle_recovery"
REMOTE_HOST="${REMOTE_HOST:-npu-dev-1}"
CONTAINER="${CONTAINER:-montyyin_reduce_ws16}"
REMOTE_DIR="/root/event_handle_recovery_v4"
UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_wait_dag_v4_device_work}"

echo "Sync -> ${REMOTE_HOST}:${CONTAINER}:${REMOTE_DIR}"
ssh "${REMOTE_HOST}" "sudo docker exec ${CONTAINER} mkdir -p ${REMOTE_DIR}"
tar -C "${SRC}" -cf - \
  build.sh event_interpose.cpp event_trace_format.h device_work.cpp device_work.h device_work_stub.cpp \
  kernels real_dlsym_resolver.c min_create.c \
  run_d51_wait_dag_v4.sh train_event_preload.py smoke_device_work.py \
  wait_dag_v4_intervention.py wait_dag_v4_2_reverse_candidate.py \
  run_d51_wait_dag_v4_2_reanalyze.sh event_preload_v6_analyze.py analyze_event_pairs.py \
  classify_intervening_tasks.py wait_dag_v2_build.py wait_dag_v2_fifo.py \
  wait_dag_v2_casebook.py wait_dag_v2_cone.py \
  a6_predicate_v6.py wait_dag_schema.py wait_dag_v2_schema.py \
  tests/fake_acl.cpp tests/fake_rt.cpp tests/fake_loader.cpp tests/stubs \
  | ssh "${REMOTE_HOST}" "sudo docker exec -i ${CONTAINER} tar -C ${REMOTE_DIR} -xf -"

echo "Launch RUN_ID=${RUN_ID}"
ssh "${REMOTE_HOST}" "sudo docker exec -d ${CONTAINER} bash -lc 'cd ${REMOTE_DIR} && chmod +x run_d51_wait_dag_v4.sh kernels/build_kernel.sh && RUN_ID=${RUN_ID} nohup ./run_d51_wait_dag_v4.sh > /tmp/${RUN_ID}_launcher.log 2>&1 &'"
echo "Monitor: ssh ${REMOTE_HOST} sudo docker exec ${CONTAINER} tail -f /tmp/${RUN_ID}/logs/*/runner.log"
