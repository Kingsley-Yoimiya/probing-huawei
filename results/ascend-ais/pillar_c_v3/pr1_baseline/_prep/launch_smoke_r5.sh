#!/usr/bin/env bash
set -euo pipefail
ROOT="/Users/yinjinrun/Codespace/myportal/project/probing-huawei"
export FS_SHARED_SCRIPTS="/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow"
export RUN_ID="${RUN_ID:-20260727_204500-yjr-as-b-pr1-health-smoke-r5b}"
export CASE_ID=P3-SW-A
export DOSE=health
export PHASE=smoke
export INJECT_KIND=none
export ABC_CONFIGS=C2_probing
export POD=yysong-worker-0
export ITERS=3000
export WARMUP=20
export DUMP_WAIT_S=240
export DUMP_PROBING_SQL=1
export PROBING_TORCH_PROFILING='on,rate=0'
export PROBING_TORCH_MIN_STEP_INTERVAL=100
export PROBING_GPU_SAMPLE_MS=500
export PROBING_CPU_SAMPLE_MS=500
export PROBING_SPAN_BACKENDS=none
export PILLAR_C_SET_UPGRADE=0
export POD_OUT="/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c_v3/pr1_baseline/${RUN_ID}"
export LOCAL_RESULT_ROOT="${ROOT}/results/ascend-ais/pillar_c_v3/pr1_baseline/${RUN_ID}"
mkdir -p "${LOCAL_RESULT_ROOT}/logs"
cd "${ROOT}"
exec bash scripts/fail-slow/hold_exec_run_case.sh
