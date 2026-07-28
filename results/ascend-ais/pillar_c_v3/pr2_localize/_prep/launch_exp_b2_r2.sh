#!/usr/bin/env bash
# PR-2 B2 重发（跳过重复 heavy jsync；111524 已废弃）
set -euo pipefail
ROOT="/Users/yinjinrun/Codespace/myportal/project/probing-huawei"
TS="${RUN_ID_TS:-$(date +%Y%m%d_%H%M%S)}"
export PARENT_RUN_ID="${PARENT_RUN_ID:-${TS}-pillar-c-v3-pr2-e3-b2}"
export ARM_RUN_ID="${PARENT_RUN_ID}-upgrade_rate_1.0"

export FS_SHARED_SCRIPTS="/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow"
export FS_PLATFORM_ASCEND="${FS_SHARED_SCRIPTS}/platform/ascend"
export LOCAL_RESULT_ROOT_BASE="${ROOT}/results/ascend-ais/pillar_c_v3/pr2_localize"
unset OUT_FAMILY
export POD_BUNDLE="/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle"
export HOLD_EXEC_SKIP_HEAVY_JSYNC=1

export CASE_ID=P3-SW-A DOSE=loud PHASE=pilot POD=yysong-worker-0
export NPROC=16 NNODES=1 SIDECAR_LOCAL_RANK=7
export ITERS=1800 WARMUP=50 INJECT_START=100 INJECT_STOP=300
export DUMP_WAIT_S=90 DUMP_PROBING_SQL=1
export ARM=e3a_upgrade RESIDENT_RATE=0
export PILLAR_C_SET_UPGRADE=1 PILLAR_C_SET_AT_STEP=100
export PILLAR_C_SET_SCOPE=localize PILLAR_C_SET_RATE=1.0
export PILLAR_C_SET_WINDOW_STEPS="${PILLAR_C_SET_WINDOW_STEPS:-12}"
export PILLAR_C_SET_HANG_MAX_S="${PILLAR_C_SET_HANG_MAX_S:-900}"
export PILLAR_C_LOCALIZE_MODE=step_ms PILLAR_C_LOCALIZE_WINDOW=20
export PILLAR_C_LOCALIZE_TIMEOUT_S=8 PILLAR_C_LOCALIZE_RETRIES=1
export PILLAR_C_LOCALIZE_TOTAL_BUDGET_S=60 PILLAR_C_LOCALIZE_PARALLEL=4
export PILLAR_C_ATTACH_READY_WAIT_S=30 PILLAR_C_SET_BLOCK_TIMEOUT_S=120
export PILLAR_C_LOCALIZE_SECONDARY=1

PARENT_LOCAL="${LOCAL_RESULT_ROOT_BASE}/${PARENT_RUN_ID}"
mkdir -p "${PARENT_LOCAL}/logs"
echo "${PARENT_RUN_ID}" >"${PARENT_LOCAL}/PARENT_RUN_ID.txt"
echo "${ARM_RUN_ID}" >"${PARENT_LOCAL}/ARM_RUN_ID.txt"

cat >"${PARENT_LOCAL}/PR2_EXP_B2_LAUNCH.md" <<EOF
# PR-2 实验 B2 · 发射记录（R2）

| 字段 | 值 |
|------|-----|
| parent | \`${PARENT_RUN_ID}\` |
| arm | \`${ARM_RUN_ID}\` |
| pod | \`${POD}\` |
| 废弃 | \`20260728_111524-pillar-c-v3-pr2-e3-b2\`（jsync 卡死） |
| B2 窗口 | \`${PILLAR_C_SET_WINDOW_STEPS}\` step 后 SET_DOWNGRADE rate=0 |
| jsync | \`HOLD_EXEC_SKIP_HEAVY_JSYNC=1\`（bundle 已有 train/sidecar） |
| 全量臂 | REUSE v2 \`20260725_230350-…/full_fidelity\` |
| ETA | ~35–55 min（过 inject_stop=300） |

发射：\`_prep/launch_exp_b2_r2.sh\` @ $(date -Iseconds)
EOF
cp "${PARENT_LOCAL}/PR2_EXP_B2_LAUNCH.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_EXP_B2_LAUNCH.md"

LOG="${PARENT_LOCAL}/logs/arm_dynamic.log"
echo "[b2-r2] fire ${ARM_RUN_ID} skip_heavy_jsync=1"
cd "${ROOT}"
bash scripts/fail-slow/run_pillar_c_arm.sh 2>&1 | tee "${LOG}"
arm_rc=${PIPESTATUS[0]}
echo "$arm_rc" >"${PARENT_LOCAL}/logs/arm_dynamic.rc"

# post-run: score + status (parent loop may also poll)
if [[ "$arm_rc" -eq 0 ]]; then
  LOCAL_DYN="${LOCAL_RESULT_ROOT_BASE}/pillar_c/${PARENT_RUN_ID}/upgrade_rate_1.0"
  mkdir -p "${PARENT_LOCAL}/dynamic" "${PARENT_LOCAL}/full_fidelity"
  rsync -a "${LOCAL_DYN}/" "${PARENT_LOCAL}/dynamic/" 2>/dev/null || true
  echo "reuse=1" >"${PARENT_LOCAL}/full_fidelity/REUSE.txt"
  echo "1791975360" >"${PARENT_LOCAL}/full_fidelity/total_dump_bytes.txt"
  python3 "${ROOT}/scripts/fail-slow/pr2_e3_score_ratio.py" \
    --parent-local "$PARENT_LOCAL" \
    --dynamic-arm "${PARENT_LOCAL}/dynamic" \
    --case P3-SW-A --w-star 100 --resident-rate 0 \
    --full-ref "/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260725_230350-pillar-c-p3-sw-a-loud/full_fidelity" \
    --dynamic-reuse-run "$PARENT_RUN_ID" \
    --out-json "${PARENT_LOCAL}/PR2_E3_RATIO_B2.json" \
    --out-md "${PARENT_LOCAL}/PR2_E3_RATIO_B2.md" || true
  cp "${PARENT_LOCAL}/PR2_E3_RATIO_B2.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_E3_RATIO_B2.md" 2>/dev/null || true
fi
echo "[b2-r2] exit rc=$arm_rc parent=${PARENT_RUN_ID}"
exit "$arm_rc"
