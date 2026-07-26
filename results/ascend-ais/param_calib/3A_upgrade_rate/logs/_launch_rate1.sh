#!/usr/bin/env bash
set -euo pipefail
ROOT=/Users/yinjinrun/Codespace/myportal/project/probing-huawei
export OUT_FAMILY=param_calib/3A_upgrade_rate
export PARENT_RUN_ID=20260727_014151-3a-p3-sw-a-loud
export ARM=e3a_upgrade
export RESIDENT_RATE=0
export PILLAR_C_SET_RATE=1.0
export PILLAR_C_SET_AT_STEP=100
export PILLAR_C_SET_SCOPE=victim
export ARM_RUN_ID=${PARENT_RUN_ID}-upgrade_rate_1.0
export CASE_ID=P3-SW-A
export DOSE=loud
export POD=grj-megatron-32card-0716-worker-0
export NPROC=16
export POD_BUNDLE=/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle
export POD_RESULTS=/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais
export PROBING_CPU_RING_MB=64
export LOCAL_RESULT_ROOT_BASE=/Users/yinjinrun/Codespace/myportal/results/ascend-ais
export FS_SHARED_SCRIPTS=/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow
export FS_PLATFORM_ASCEND=${FS_SHARED_SCRIPTS}/platform/ascend
export INLINE_2C_FALLBACK_S=0.6
PARENT_LOCAL=${LOCAL_RESULT_ROOT_BASE}/${OUT_FAMILY}/${PARENT_RUN_ID}
mkdir -p "$PARENT_LOCAL/logs" "$PARENT_LOCAL/upgrade_rate_1.0"
echo "[3a-resume] >>> upgrade_rate=1.0 scope=victim $(date -Iseconds)"
set +e
bash "$ROOT/scripts/fail-slow/run_pillar_c_arm.sh"
rc=$?
set -e
echo "$rc" > "$PARENT_LOCAL/upgrade_rate_1.0/hold_exec.rc"
echo "[3a-resume] rate=1.0 rc=$rc $(date -Iseconds)"
exit $rc
