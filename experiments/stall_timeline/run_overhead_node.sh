#!/usr/bin/env bash
set -euo pipefail

: "${NODE_RANK:?}"
: "${MASTER_ADDR:?}"
: "${MASTER_PORT:?}"
: "${NNODES:?}"
: "${NPROC:?}"
: "${CODE_DIR:?}"
: "${OUT_DIR:?}"

source /usr/local/Ascend/cann-8.5.0/set_env.sh
source /root/miniconda3/etc/profile.d/conda.sh
conda activate llm_test

export PYTHONUNBUFFERED=1
export GLOO_SOCKET_IFNAME="${GLOO_SOCKET_IFNAME:-eth0}"
export HCCL_CONNECT_TIMEOUT="${HCCL_CONNECT_TIMEOUT:-600}"
export HCCL_EXEC_TIMEOUT="${HCCL_EXEC_TIMEOUT:-600}"
export PROBING="${PROBING_MODE:-2}"
export PROBING_DATA_DIR="${OUT_DIR}/probing_data"
export PROBING_EXTTBL_STALL_TIMELINE_MB="${PROBING_EXTTBL_STALL_TIMELINE_MB:-4}"
export PYTHONPATH="/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle/pydeps:${PYTHONPATH:-}"
if [[ "${PROBING_MINIMAL:-0}" == "1" ]]; then
  export PROBING_CPU=off
  export PROBING_GPU=off
  export PROBING_HCCS=off
  export PROBING_SPAN_BACKENDS=none
  export PROBING_TORCHRUN_CLUSTER=0
  export PROBING_CLUSTER_REPORT=0
fi

mkdir -p "${OUT_DIR}"
rm -f "${OUT_DIR}/node_${NODE_RANK}.done" "${OUT_DIR}/node_${NODE_RANK}.fail"

set +e
timeout "${RUN_TIMEOUT_S:-1800}" /root/miniconda3/envs/llm_test/bin/torchrun \
  --nnodes="${NNODES}" \
  --nproc_per_node="${NPROC}" \
  --node_rank="${NODE_RANK}" \
  --master_addr="${MASTER_ADDR}" \
  --master_port="${MASTER_PORT}" \
  "${CODE_DIR}/overhead_probe.py" \
  --out="${OUT_DIR}" \
  --plan="${BLOCK_PLAN}" \
  --block-steps="${BLOCK_STEPS:-30000}" \
  --warmup="${WARMUP:-20}" \
  --matmul-size="${MATMUL_SIZE:-4096}" \
  --ar-bytes="${AR_BYTES:-1048576}" \
  --sample-ranks="${SAMPLE_RANKS:-4}" \
  --hole-ms="${HOLE_MS:-200}" \
  --heartbeat-every="${HEARTBEAT_EVERY:-10000}" \
  --probing-table="${PROBING_TABLE:-auto}" \
  >"${OUT_DIR}/node_${NODE_RANK}.log" 2>&1
rc=$?
set -e

if [[ "$rc" -eq 0 ]]; then
  touch "${OUT_DIR}/node_${NODE_RANK}.done"
else
  echo "$rc" >"${OUT_DIR}/node_${NODE_RANK}.fail"
fi
exit "$rc"
