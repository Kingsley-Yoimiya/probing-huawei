#!/usr/bin/env bash
# Sync V4.3 sources to npu-dev-1 container (no container restart).
set -euo pipefail

SRC="/Users/yinjinrun/Codespace/probing-huawei/experiments/event_handle_recovery"
REMOTE_HOST="${REMOTE_HOST:-npu-dev-1}"
CONTAINER="${CONTAINER:-montyyin_reduce_ws16}"
REMOTE_DIR="/root/event_handle_recovery_v4"

echo "Sync V4.3 -> ${REMOTE_HOST}:${CONTAINER}:${REMOTE_DIR}"
ssh "${REMOTE_HOST}" "sudo docker exec ${CONTAINER} mkdir -p ${REMOTE_DIR}"
tar -C "${SRC}" -cf - \
  build.sh event_interpose.cpp event_trace_format.h device_work.cpp device_work.h device_work_stub.cpp \
  kernels real_dlsym_resolver.c min_create.c \
  run_d51_wait_dag_v4_3.sh run_v4_1_artifact_probe.sh \
  train_event_preload.py smoke_device_work.py smoke_kernel_checksum.py \
  d51_work_unit_reference.py dose_calibrate_v4_3.py dose_calibrate_v4_2.py \
  wait_dag_v4_intervention.py wait_dag_v4_2_reverse_candidate.py \
  run_d51_wait_dag_v4_2_reanalyze.sh event_preload_v6_analyze.py analyze_event_pairs.py \
  classify_intervening_tasks.py wait_dag_v2_build.py wait_dag_v2_fifo.py \
  wait_dag_v2_casebook.py wait_dag_v2_cone.py preload_bindings.py kernel_load_probe.py \
  a6_predicate_v6.py wait_dag_schema.py wait_dag_v2_schema.py \
  tests/fake_acl.cpp tests/fake_rt.cpp tests/fake_loader.cpp tests/stubs \
  tests/test_wait_dag_v4_2.py tests/test_wait_dag_v4_3.py \
  | ssh "${REMOTE_HOST}" "sudo docker exec -i ${CONTAINER} tar -C ${REMOTE_DIR} -xf -"

echo "SYNC_OK ${REMOTE_DIR}"
