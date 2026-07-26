#!/usr/bin/env bash
set -euo pipefail
source /root/miniconda3/etc/profile.d/conda.sh
conda activate llm_test
export PYTHONUNBUFFERED=1
export PATH=/root/miniconda3/envs/llm_test/bin:${PATH}
export PYTHONPATH=/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle/pydeps:${PYTHONPATH:-}
export GLOO_SOCKET_IFNAME=${GLOO_SOCKET_IFNAME:-eth0}
export HCCL_CONNECT_TIMEOUT=${HCCL_CONNECT_TIMEOUT:-1800}
export HOST_BOUND_MATMUL=768
export CKPT_DIR=/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle/ckpt
export PROBING=2; export PROBING_GPU=on; export PROBING_GPU_BACKEND=npu; export PROBING_NPU_SOURCE=auto; export PROBING_GPU_SAMPLE_MS=1000; export PROBING_CPU=on; export PROBING_CPU_SAMPLE_MS=1000; export PYTHONPATH=/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle/pydeps:${PYTHONPATH:-}; export PATH=/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle/pydeps/bin:/root/miniconda3/envs/llm_test/bin:${PATH}; unset PROBING_TORCH_PROFILING; unset PROBING_COLD_MAX_TOTAL_MB 2>/dev/null || true; unset PROBING_COLD 2>/dev/null || true; export INLINE_INJECT=2c; export INLINE_VICTIM_LOCAL_RANK=7; export INLINE_INJECT_START=100; export INLINE_INJECT_STOP=300; export INLINE_2C_N=768; export INLINE_2C_EVERY=4; export INLINE_2C_FALLBACK_S=0.1;
OUT='/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/20260726_021606-yjr-as-c-p1-sw-c-quiet/P1-SW-C/by_pod/grj-megatron-32card-0716-master-0/round_1/C2_probing'
rm -f "$OUT/node_0.done" "$OUT/node_0.fail"
rm -rf "$OUT/ranks"
mkdir -p "$OUT/ranks"
/root/miniconda3/envs/llm_test/bin/torchrun --nnodes=1 --nproc_per_node=16 --node_rank=0 \
  --master_addr=10.119.0.183 --master_port=30200 \
  /tmp/tbp_npu.py --iters=500 --warmup=50 --seed=42 --mode=gpu_bound --model=gpt2 --seq=1024 --batch=8 \
  --flush-every=5 --ckpt-every=100 \
  --io-payload='' --io-read-kb=0 \
  --run-id=20260726_021606-yjr-as-c-p1-sw-c-quiet --group=2 --config='C2_probing' --round=1 \
  --out-dir="$OUT/ranks" > "$OUT/node_0.log" 2>&1
rc=$?
if [[ $rc -eq 0 ]]; then touch "$OUT/node_0.done"; else echo $rc > "$OUT/node_0.fail"; fi
