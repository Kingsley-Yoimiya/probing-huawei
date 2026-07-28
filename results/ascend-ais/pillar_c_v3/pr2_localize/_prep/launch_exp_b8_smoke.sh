#!/usr/bin/env bash
# PR-2 实验 B8 · smoke（≤10min）
#  - 目标：验 (a) localize SQL avg + window=100 生效 (b) no-progress-90s driver kill (c) HCCL_EXEC_TIMEOUT=600
#  - pod: grj-megatron-32card-0716-worker-0（IDLE 让路铁律；主池 yysong-w0 有 rank 15 pre-existing stuck）
#  - ITERS=200；case P3-SW-A；RESIDENT=0；SET rate=1.0；scope=localize；SET_AT_STEP=100；SET_HANG_MAX=180
set -euo pipefail
ROOT="/Users/yinjinrun/Codespace/myportal/project/probing-huawei"
TS="${RUN_ID_TS:-$(date +%Y%m%d_%H%M%S)}"
export PARENT_RUN_ID="${PARENT_RUN_ID:-${TS}-pillar-c-v3-pr2-e3-b8-smoke}"
export ARM_RUN_ID="${PARENT_RUN_ID}-upgrade_rate_1.0"

# B6 gates（B7 code 保留；本轮 python-only 改动叠加）
export PROBING_TORCH_COMM_COLLECTIVE_LAZY="${PROBING_TORCH_COMM_COLLECTIVE_LAZY:-1}"
export PROBING_TORCH_STEP_TIMING_LAZY="${PROBING_TORCH_STEP_TIMING_LAZY:-0}"
export PILLAR_C_PRUNE_EXTRA_PIDS="${PILLAR_C_PRUNE_EXTRA_PIDS:-1}"
export PILLAR_C_PRUNE_DRY_RUN="${PILLAR_C_PRUNE_DRY_RUN:-0}"

# B8 三处新 gate
export PILLAR_C_LOCALIZE_STEP_AGG="${PILLAR_C_LOCALIZE_STEP_AGG:-avg}"
export PILLAR_C_LOCALIZE_STEP_WINDOW="${PILLAR_C_LOCALIZE_STEP_WINDOW:-100}"
export PILLAR_C_NO_PROGRESS_KILL_S="${PILLAR_C_NO_PROGRESS_KILL_S:-90}"
export HCCL_EXEC_TIMEOUT="${HCCL_EXEC_TIMEOUT:-600}"

export FS_SHARED_SCRIPTS="/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow"
export FS_PLATFORM_ASCEND="${FS_SHARED_SCRIPTS}/platform/ascend"
export LOCAL_RESULT_ROOT_BASE="${ROOT}/results/ascend-ais/pillar_c_v3/pr2_localize"
unset OUT_FAMILY
export POD_BUNDLE="/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle"

export CASE_ID=P3-SW-A
export DOSE=loud
export PHASE=pilot
export POD=grj-megatron-32card-0716-worker-0
export NPROC=16
export NNODES=1
export SIDECAR_LOCAL_RANK=7

# 短 smoke：200 步 · inject 窗更早
export ITERS=200
export WARMUP=50
export INJECT_START=100
export INJECT_STOP=180
export DUMP_WAIT_S=60
export DUMP_PROBING_SQL=1

export ARM=e3a_upgrade
export RESIDENT_RATE=0
export PILLAR_C_SET_UPGRADE=1
export PILLAR_C_SET_AT_STEP=100
export PILLAR_C_SET_SCOPE=localize
export PILLAR_C_SET_RATE=1.0
export PILLAR_C_SET_WINDOW_S="${PILLAR_C_SET_WINDOW_S:-15}"
export PILLAR_C_SET_WINDOW_STEPS="${PILLAR_C_SET_WINDOW_STEPS:-0}"
export PILLAR_C_SET_HANG_MAX_S="${PILLAR_C_SET_HANG_MAX_S:-180}"
export JEXEC_POLL_TIMEOUT_S="${JEXEC_POLL_TIMEOUT_S:-25}"
export HOLD_EXEC_SKIP_HEAVY_JSYNC=1
export PILLAR_C_LOCALIZE_MODE=step_ms
# 保留 legacy WINDOW 变量（对 comm_max 生效；step_ms 用 PILLAR_C_LOCALIZE_STEP_WINDOW=100）
export PILLAR_C_LOCALIZE_WINDOW=20
export PILLAR_C_LOCALIZE_TIMEOUT_S=8
export PILLAR_C_LOCALIZE_RETRIES=1
export PILLAR_C_LOCALIZE_TOTAL_BUDGET_S=60
export PILLAR_C_LOCALIZE_PARALLEL=4
export PILLAR_C_ATTACH_READY_WAIT_S=30
export PILLAR_C_SET_BLOCK_TIMEOUT_S=120
export PILLAR_C_LOCALIZE_SECONDARY=1

JUMP_KUBECTL="/root/.cache/volcano/kubectl/kubectl"
JUMP_KUBECONFIG="/tmp/config-vc-a3-241ceshi-songyiyang.yaml"
JUMP_HOST="ais-cf3e61a5"

PARENT_LOCAL="${LOCAL_RESULT_ROOT_BASE}/${PARENT_RUN_ID}"
mkdir -p "${PARENT_LOCAL}/logs" "${PARENT_LOCAL}/full_fidelity"
echo "${PARENT_RUN_ID}" >"${PARENT_LOCAL}/PARENT_RUN_ID.txt"
echo "${ARM_RUN_ID}" >"${PARENT_LOCAL}/ARM_RUN_ID.txt"

cat >"${PARENT_LOCAL}/PR2_EXP_B8_SMOKE_LAUNCH.md" <<EOF
# PR-2 实验 B8 · smoke 发射记录

| 字段 | 值 |
|------|-----|
| parent | \`${PARENT_RUN_ID}\` |
| arm | \`${ARM_RUN_ID}\` |
| pod | \`${POD}\`（grj-w0，主池 yysong-w0 rank15 stuck 让路） |
| case | P3-SW-A · GT culprit rank=7 |
| ITERS | ${ITERS} · inject [${INJECT_START},${INJECT_STOP}] |
| scope | localize + 15s 时基降回 |
| hang_max | **${PILLAR_C_SET_HANG_MAX_S}s** |
| B8 gates | STEP_AGG=**${PILLAR_C_LOCALIZE_STEP_AGG}** STEP_WINDOW=**${PILLAR_C_LOCALIZE_STEP_WINDOW}** NO_PROG_KILL_S=**${PILLAR_C_NO_PROGRESS_KILL_S}** HCCL_EXEC_TIMEOUT=**${HCCL_EXEC_TIMEOUT}** |
| B6 gates | COMM_LAZY=${PROBING_TORCH_COMM_COLLECTIVE_LAZY} · STEP_TIMING_LAZY=${PROBING_TORCH_STEP_TIMING_LAZY} · PRUNE=${PILLAR_C_PRUNE_EXTRA_PIDS} · DRY=${PILLAR_C_PRUNE_DRY_RUN} |

## 发射
\`_prep/launch_exp_b8_smoke.sh\` @ $(date -Iseconds)
EOF
cp "${PARENT_LOCAL}/PR2_EXP_B8_SMOKE_LAUNCH.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_EXP_B8_SMOKE_LAUNCH.md"

echo "[pr2-b8] self-check test_pillar_c_set_window…"
bash "${ROOT}/scripts/fail-slow/test_pillar_c_set_window.sh"

echo "[pr2-b8] localize SQL avg build_sql self-check…"
python3 - <<'PY'
import sys, os, shutil
shutil.copy("/Users/yinjinrun/Codespace/myportal/project/probing-huawei/scripts/fail-slow/pillar_c_localize_culprit.py", "/tmp/_loc_b8.py")
sys.path.insert(0, "/tmp")
os.environ.pop("PILLAR_C_LOCALIZE_STEP_AGG", None)
import _loc_b8 as m
sql = m.build_sql("step_ms", 139, 100)
assert "avg(step_duration_sec)" in sql, sql
assert "local_step >= 39" in sql, sql
assert "local_step <= 139" in sql, sql
print("[pr2-b8] localize.py avg+window=100 OK")
PY

echo "[pr2-b8] hold_exec_run_case.sh HCCL_EXEC_TIMEOUT + no-progress kill self-check…"
grep -q 'HCCL_EXEC_TIMEOUT' "${ROOT}/scripts/fail-slow/hold_exec_run_case.sh"
grep -q 'PILLAR_C_NO_PROGRESS_KILL_S' "${ROOT}/scripts/fail-slow/hold_exec_run_case.sh"
grep -q 'NO_JSONL_PROGRESS_' "${ROOT}/scripts/fail-slow/hold_exec_run_case.sh"
echo "[pr2-b8] hold_exec_run_case.sh gates OK"

jsync_one() {
  local src="$1" dst="$2"
  local bname ddir
  bname=$(basename "$src")
  ddir=$(dirname "$dst")
  set +e
  COPYFILE_DISABLE=1 tar -C "$(dirname "$src")" -cf - "$bname" \
    | ssh -o BatchMode=yes -o ConnectTimeout=30 "${JUMP_HOST}" \
      "export KUBECONFIG='${JUMP_KUBECONFIG}'; K='${JUMP_KUBECTL}'; \$K -n default exec -i '${POD}' -- bash -lc $(printf '%q' "mkdir -p '$ddir' /tmp/yjr_sync && tar -C /tmp/yjr_sync -xf - && install -m 0644 /tmp/yjr_sync/$bname '$dst' && rm -f /tmp/yjr_sync/$bname")"
  local rc=$?
  set -e
  echo "[pr2-b8] jsync $bname -> $dst rc=$rc"
  return 0
}

echo "[pr2-b8] jsync localize.py → bundle (idempotent)…"
jsync_one "${ROOT}/scripts/fail-slow/pillar_c_localize_culprit.py" "${POD_BUNDLE}/pillar_c_localize_culprit.py"
jsync_one "${ROOT}/scripts/fail-slow/pillar_c_localize_culprit.py" "${POD_BUNDLE}/_pillar_c_localize.py"

echo "[pr2-b8] wait grj-w0 IDLE (mandatory - 让路铁律)…"
check_idle() {
  ssh -o ConnectTimeout=20 "$JUMP_HOST" \
    "export KUBECONFIG=${JUMP_KUBECONFIG}; ${JUMP_KUBECTL} -n default exec ${POD} -- bash -lc '
      busy=\$(ps -eo pid,cmd | awk \"/torchrun|megatron|pretrain_gpt|train_bench_probe|tbp_npu/ && !/awk|bash -lc|pgrep|defunct|tar czf/ {print}\")
      if [[ -n \"\$busy\" ]]; then echo OWNER_BUSY; echo \"\$busy\"; exit 90; fi
      echo IDLE
    '"
}
idle_out=$(check_idle) || true
echo "$idle_out" | tee "${PARENT_LOCAL}/logs/idle_check.txt"
if echo "$idle_out" | grep -q OWNER_BUSY; then
  echo "[pr2-b8] BLOCKED: grj-w0 busy (让路)"
  echo "BLOCKED grj_w0_busy $(date -Iseconds)" >"${PARENT_LOCAL}/BLOCKED.txt"
  exit 90
fi

echo "[pr2-b8] fire dynamic arm ${ARM_RUN_ID}…"
cd "${ROOT}"
bash scripts/fail-slow/run_pillar_c_arm.sh 2>&1 | tee "${PARENT_LOCAL}/logs/arm_dynamic.log"
arm_rc=${PIPESTATUS[0]}
echo "$arm_rc" >"${PARENT_LOCAL}/logs/arm_dynamic.rc"

LOCAL_DYN="${PARENT_LOCAL}/upgrade_rate_1.0"
if [[ ! -d "${LOCAL_DYN}" ]]; then
  LOCAL_DYN="${LOCAL_RESULT_ROOT_BASE}/pillar_c/${PARENT_RUN_ID}/upgrade_rate_1.0"
fi
mkdir -p "${PARENT_LOCAL}/dynamic"
if [[ -d "${LOCAL_DYN}" ]]; then
  rsync -a "${LOCAL_DYN}/" "${PARENT_LOCAL}/dynamic/"
fi

echo "[pr2-b8] smoke done rc=${arm_rc}. See PARENT=${PARENT_LOCAL}"
echo "$PARENT_RUN_ID"
