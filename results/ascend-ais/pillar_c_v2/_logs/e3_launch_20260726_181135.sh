#!/usr/bin/env bash
set -euo pipefail
export FS_SHARED_SCRIPTS=/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow
export FS_PLATFORM_ASCEND=${FS_SHARED_SCRIPTS}/platform/ascend
export POD_RESULTS=/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais
export POD_BUNDLE=/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle
export PARENT_RUN_ID='20260726_181135-pillar-c-e3-p3-sw-a-loud'
export CASE_ID=P3-SW-A
export DOSE=loud
export REUSE_FULL=1
export W_STAR=100
export RESIDENT_RATE=0
export POD=grj-megatron-32card-0716-worker-0
cd '/Users/yinjinrun/Codespace/myportal/project/probing-huawei'
source scripts/fail-slow/env.sh
echo "[$(date -Iseconds)] E3 start parent=20260726_181135-pillar-c-e3-p3-sw-a-loud POD_RESULTS=$POD_RESULTS"
bash scripts/fail-slow/run_pillar_c_e3.sh
rc=$?
echo "[$(date -Iseconds)] E3 end rc=$rc"
exit $rc
