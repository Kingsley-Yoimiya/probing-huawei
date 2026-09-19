#!/usr/bin/env bash
# Sync FIX sources to npu-dev-1 container and launch fresh collection.
set -euo pipefail

SRC="/Users/yinjinrun/Codespace/probing-huawei/experiments/event_handle_recovery"
REMOTE_HOST="${REMOTE_HOST:-npu-dev-1}"
CONTAINER="${CONTAINER:-montyyin_reduce_ws16}"
REMOTE_DIR="/root/event_handle_recovery_v3_1_fix"
UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_wait_dag_v3_1_fix}"

echo "Sync ${SRC} -> ${REMOTE_HOST}:${CONTAINER}:${REMOTE_DIR}"
ssh "${REMOTE_HOST}" "sudo docker exec ${CONTAINER} mkdir -p ${REMOTE_DIR}"
tar -C "${SRC}" -cf - \
  build.sh event_interpose.cpp event_trace_format.h real_dlsym_resolver.c min_create.c \
  run_d51_wait_dag_v3_1_fix.sh train_event_preload.py smoke_delay_v3.py \
  wait_dag_v3_intervention.py event_preload_v6_analyze.py analyze_event_pairs.py \
  classify_intervening_tasks.py wait_dag_v2_build.py wait_dag_v2_fifo.py \
  a6_predicate_v6.py wait_dag_schema.py wait_dag_v2_schema.py \
  tests/event_delay_v3_unit.cpp tests/fake_acl.cpp tests/fake_rt.cpp \
  tests/fake_loader.cpp tests/event_sequence_smoke.cpp tests/stubs \
  | ssh "${REMOTE_HOST}" "sudo docker exec -i ${CONTAINER} tar -C ${REMOTE_DIR} -xf -"

echo "Launch RUN_ID=${RUN_ID} (background in container)"
ssh "${REMOTE_HOST}" "sudo docker exec -d ${CONTAINER} bash -lc 'cd ${REMOTE_DIR} && chmod +x run_d51_wait_dag_v3_1_fix.sh && RUN_ID=${RUN_ID} nohup ./run_d51_wait_dag_v3_1_fix.sh > /tmp/${RUN_ID}_launcher.log 2>&1 &'"
echo "RUN_ID=${RUN_ID}"
echo "Monitor: ssh ${REMOTE_HOST} sudo docker exec ${CONTAINER} tail -f /tmp/${RUN_ID}/logs/*/runner.log"
