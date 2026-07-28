#!/usr/bin/env bash
# PR-2 实验 A：P3-SW-A GT=rank7，localize scope SET
set -euo pipefail
ROOT="/Users/yinjinrun/Codespace/myportal/project/probing-huawei"
TS="${RUN_ID_TS:-$(date +%Y%m%d_%H%M%S)}"
export PARENT_RUN_ID="${PARENT_RUN_ID:-${TS}-pillar-c-v3-pr2-localize-a}"
export ARM_RUN_ID="${PARENT_RUN_ID}-upgrade_rate_1.0"

export FS_SHARED_SCRIPTS="/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow"
export FS_PLATFORM_ASCEND="${FS_SHARED_SCRIPTS}/platform/ascend"
export LOCAL_RESULT_ROOT_BASE="${ROOT}/results/ascend-ais/pillar_c_v3/pr2_localize"
# 勿用空串：run_pillar_c_arm 的 ${OUT_FAMILY:-pillar_c} 会把空串当 unset
export OUT_FAMILY="pr2_localize"
export POD_RESULTS="/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c_v3/pr2_localize"
export POD_BUNDLE="/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle"

export CASE_ID=P3-SW-A
export DOSE=loud
export PHASE=pilot
export POD=yysong-worker-0
export NPROC=16
export NNODES=1
export SIDECAR_LOCAL_RANK=7

# 短 pilot：覆盖 SET@100 + dump；DUMP_WAIT < 训程
export ITERS=2000
export WARMUP=50
export DUMP_WAIT_S=180
export DUMP_PROBING_SQL=1

export ARM=e3a_upgrade
export RESIDENT_RATE=0
export PILLAR_C_SET_UPGRADE=1
export PILLAR_C_SET_AT_STEP=100
export PILLAR_C_SET_SCOPE=localize
export PILLAR_C_SET_RATE=1.0
export PILLAR_C_LOCALIZE_MODE=auto
export PILLAR_C_LOCALIZE_WINDOW=20
export PILLAR_C_LOCALIZE_TIMEOUT_S=15

PARENT_LOCAL="${LOCAL_RESULT_ROOT_BASE}/${PARENT_RUN_ID}"
mkdir -p "${PARENT_LOCAL}/logs"
echo "${PARENT_RUN_ID}" >"${PARENT_LOCAL}/PARENT_RUN_ID.txt"
echo "${ARM_RUN_ID}" >"${PARENT_LOCAL}/ARM_RUN_ID.txt"

cd "${ROOT}"
exec env ARM="${ARM}" \
  PARENT_RUN_ID="${PARENT_RUN_ID}" \
  ARM_RUN_ID="${ARM_RUN_ID}" \
  bash scripts/fail-slow/run_pillar_c_arm.sh
