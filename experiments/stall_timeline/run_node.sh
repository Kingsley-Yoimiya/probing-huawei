#!/usr/bin/env bash
set -euo pipefail

: "${RUN_ID:?}"
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
export PROBING=2
export PROBING_DATA_DIR="${OUT_DIR}/probing_data"
export PROBING_EXTTBL_STALL_TIMELINE_MB="${PROBING_EXTTBL_STALL_TIMELINE_MB:-4}"
export PYTHONPATH="/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle/pydeps:${PYTHONPATH:-}"

mkdir -p "${OUT_DIR}"
rm -f "${OUT_DIR}/node_${NODE_RANK}.done" "${OUT_DIR}/node_${NODE_RANK}.fail"

set +e
timeout "${RUN_TIMEOUT_S:-900}" /root/miniconda3/envs/llm_test/bin/torchrun \
  --nnodes="${NNODES}" \
  --nproc_per_node="${NPROC}" \
  --node_rank="${NODE_RANK}" \
  --master_addr="${MASTER_ADDR}" \
  --master_port="${MASTER_PORT}" \
  "${CODE_DIR}/probe.py" \
  --out="${OUT_DIR}" \
  --scheme="${SCHEME:-sentinel}" \
  --iters="${ITERS:-120}" \
  --duration-s="${DURATION_S:-0}" \
  --stop-check-every="${STOP_CHECK_EVERY:-10000}" \
  --warmup="${WARMUP:-20}" \
  --matmul-size="${MATMUL_SIZE:-1024}" \
  --ar-bytes="${AR_BYTES:-1048576}" \
  --sample-rate="${SAMPLE_RATE:-0.1}" \
  --sample-ranks="${SAMPLE_RANKS:-4}" \
  --inject-kind="${INJECT_KIND:-device}" \
  --inject-rank="${INJECT_RANK:-7}" \
  --inject-start="${INJECT_START:-20}" \
  --inject-stop="${INJECT_STOP:-100}" \
  --inject-every="${INJECT_EVERY:-20}" \
  --inject-ms="${INJECT_MS:-500}" \
  --inject-matmul-size="${INJECT_MATMUL_SIZE:-2048}" \
  --hole-ms="${HOLE_MS:-200}" \
  --record-mode="${RECORD_MODE:-full}" \
  --heartbeat-every="${HEARTBEAT_EVERY:-1000}" \
  --flush-every="${FLUSH_EVERY:-100}" \
  >"${OUT_DIR}/node_${NODE_RANK}.log" 2>&1
rc=$?
set -e

if [[ "$rc" -eq 0 ]]; then
  touch "${OUT_DIR}/node_${NODE_RANK}.done"
else
  echo "$rc" >"${OUT_DIR}/node_${NODE_RANK}.fail"
fi
exit "$rc"
