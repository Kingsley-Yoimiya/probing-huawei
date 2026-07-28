#!/usr/bin/env bash
# PR-3 阶段 2 · P1-HW-B 补跑（handbook §3.4 追溯窗第三家族）
#  - 目标：补齐 3/3 case W* 数字（P1-SW-C=200步 · P3-SW-A=60秒 · P1-HW-B=?秒）
#  - 阶段 1 wheel 已装 grj-w0：retain_secs=3600 for gpu.utilization 默认生效
#  - dump 后跑 e3_retention_score.py --case P1-HW-B 出 W_STAR_P1_HW_B.json
#  - inject: INLINE HBM ramp（copies 6→48，mb=512，ramp=1）—— run_pillar_c_arm.sh 的 P1-HW-B 分支
#  - pod: grj-megatron-32card-0716-worker-0（IDLE 让路铁律；主池 yysong-w0 rank 15 stuck 让路）
#  - 三处 B8 gate: STEP_AGG=avg STEP_WINDOW=100 NO_PROGRESS_KILL_S=90 HCCL_EXEC_TIMEOUT=600
set -euo pipefail
ROOT="/Users/yinjinrun/Codespace/myportal/project/probing-huawei"
TS="${RUN_ID_TS:-$(date +%Y%m%d_%H%M%S)}"
export PARENT_RUN_ID="${PARENT_RUN_ID:-${TS}-pillar-c-v3-pr3-p1hwb}"
export ARM_RUN_ID="${PARENT_RUN_ID}-upgrade_rate_1.0"

# B6 gates（B8 保留）
export PROBING_TORCH_COMM_COLLECTIVE_LAZY="${PROBING_TORCH_COMM_COLLECTIVE_LAZY:-1}"
export PROBING_TORCH_STEP_TIMING_LAZY="${PROBING_TORCH_STEP_TIMING_LAZY:-0}"
export PILLAR_C_PRUNE_EXTRA_PIDS="${PILLAR_C_PRUNE_EXTRA_PIDS:-1}"
export PILLAR_C_PRUNE_DRY_RUN="${PILLAR_C_PRUNE_DRY_RUN:-0}"

# B8 三处新 gate（PR-2 已 PASS；PR-3 P1-HW-B 保留）
export PILLAR_C_LOCALIZE_STEP_AGG="${PILLAR_C_LOCALIZE_STEP_AGG:-avg}"
export PILLAR_C_LOCALIZE_STEP_WINDOW="${PILLAR_C_LOCALIZE_STEP_WINDOW:-100}"
export PILLAR_C_NO_PROGRESS_KILL_S="${PILLAR_C_NO_PROGRESS_KILL_S:-90}"
export HCCL_EXEC_TIMEOUT="${HCCL_EXEC_TIMEOUT:-600}"

export FS_SHARED_SCRIPTS="/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow"
export FS_PLATFORM_ASCEND="${FS_SHARED_SCRIPTS}/platform/ascend"
export LOCAL_RESULT_ROOT_BASE="${ROOT}/results/ascend-ais/pillar_c_v3/pr2_localize"
unset OUT_FAMILY
export POD_BUNDLE="/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle"

# P1-HW-B 场景：INLINE HBM 渐衰（1b, ramp copies 6→48）；victim rank=7
export CASE_ID=P1-HW-B
export DOSE=loud
export PHASE=pilot
export POD=grj-megatron-32card-0716-worker-0
export NPROC=16
export NNODES=1
export SIDECAR_LOCAL_RANK=7

# 1000 步 · inject [100, 300]（与 B8/EXP-C 对齐；handbook §3.4 一次跑最大 retain，dump 后离线截窗）
export ITERS=1000
export WARMUP=50
export INJECT_START=100
export INJECT_STOP=300
export DUMP_WAIT_S=90
export DUMP_PROBING_SQL=1

# INLINE 1b 参数（run_pillar_c_arm.sh 的 P1-HW-B 分支会给 hold_exec 加上 *_OVERRIDE）
export INLINE_HBM_MB="${INLINE_HBM_MB:-512}"
export INLINE_HBM_COPIES="${INLINE_HBM_COPIES:-6}"
export INLINE_HBM_COPIES_MAX="${INLINE_HBM_COPIES_MAX:-48}"
export INLINE_HBM_RAMP=1

export ARM=e3a_upgrade
export RESIDENT_RATE=0
export PILLAR_C_SET_UPGRADE=1
export PILLAR_C_SET_AT_STEP=100
export PILLAR_C_SET_SCOPE=localize
export PILLAR_C_SET_RATE=1.0
export PILLAR_C_SET_WINDOW_S="${PILLAR_C_SET_WINDOW_S:-15}"
export PILLAR_C_SET_WINDOW_STEPS="${PILLAR_C_SET_WINDOW_STEPS:-0}"
export PILLAR_C_SET_HANG_MAX_S="${PILLAR_C_SET_HANG_MAX_S:-480}"
export JEXEC_POLL_TIMEOUT_S="${JEXEC_POLL_TIMEOUT_S:-25}"
export HOLD_EXEC_SKIP_HEAVY_JSYNC=1
export PILLAR_C_LOCALIZE_MODE=step_ms
export PILLAR_C_LOCALIZE_WINDOW=20
export PILLAR_C_LOCALIZE_TIMEOUT_S=8
export PILLAR_C_LOCALIZE_RETRIES=1
export PILLAR_C_LOCALIZE_TOTAL_BUDGET_S=60
export PILLAR_C_LOCALIZE_PARALLEL=4
export PILLAR_C_ATTACH_READY_WAIT_S=30
export PILLAR_C_SET_BLOCK_TIMEOUT_S=120
export PILLAR_C_LOCALIZE_SECONDARY=1

# 不 REUSE v2 full_fidelity —— P1-HW-B 判分从 v2 torch_trace.max_allocated 迁到 v3 gpu.utilization.used_bytes
# 因此只跑动态臂即可，判 W* 不依赖对照。
JUMP_KUBECTL="/root/.cache/volcano/kubectl/kubectl"
JUMP_KUBECONFIG="/tmp/config-vc-a3-241ceshi-songyiyang.yaml"
JUMP_HOST="ais-cf3e61a5"

PARENT_LOCAL="${LOCAL_RESULT_ROOT_BASE}/${PARENT_RUN_ID}"
mkdir -p "${PARENT_LOCAL}/logs"
echo "${PARENT_RUN_ID}" >"${PARENT_LOCAL}/PARENT_RUN_ID.txt"
echo "${ARM_RUN_ID}" >"${PARENT_LOCAL}/ARM_RUN_ID.txt"

cat >"${PARENT_LOCAL}/PR2_EXP_P1HWB_LAUNCH.md" <<EOF
# PR-3 P1-HW-B 补跑 · 发射记录

| 字段 | 值 |
|------|-----|
| parent | \`${PARENT_RUN_ID}\` |
| arm | \`${ARM_RUN_ID}\` |
| pod | \`${POD}\`（grj-w0，主池 yysong-w0 rank15 stuck 让路） |
| case | P1-HW-B loud · GT victim rank=7 · inject_kind=1b INLINE HBM ramp (mb=${INLINE_HBM_MB} copies=${INLINE_HBM_COPIES}->${INLINE_HBM_COPIES_MAX} ramp=1) |
| ITERS | ${ITERS} · inject [${INJECT_START},${INJECT_STOP}] |
| scope | localize + **${PILLAR_C_SET_WINDOW_S}s** 时基降回 |
| hang_max | **${PILLAR_C_SET_HANG_MAX_S}s**（8min） |
| 常驻 | \`on,rate=0\` |
| 全量臂 | 不 REUSE（P1-HW-B 判据从 torch_trace.max_allocated 迁到 gpu.utilization.used_bytes） |
| B8 gates | STEP_AGG=**${PILLAR_C_LOCALIZE_STEP_AGG}** STEP_WINDOW=**${PILLAR_C_LOCALIZE_STEP_WINDOW}** NO_PROG_KILL_S=**${PILLAR_C_NO_PROGRESS_KILL_S}** HCCL_EXEC_TIMEOUT=**${HCCL_EXEC_TIMEOUT}** |
| B6 gates | COMM_LAZY=${PROBING_TORCH_COMM_COLLECTIVE_LAZY} · STEP_TIMING_LAZY=${PROBING_TORCH_STEP_TIMING_LAZY} · PRUNE=${PILLAR_C_PRUNE_EXTRA_PIDS} · DRY=${PILLAR_C_PRUNE_DRY_RUN} |
| PR-3 wheel | 阶段 1 已装 \`probing-0.2.6-cp38-abi3-linux_aarch64.whl\` sha \`9416803e...\` |
| 目标（handbook §3.4） | 6 档位（60/300/900/1800/3600/all 秒）首个 enough=Y 即 W\*；判据 gpu.utilization used_bytes rise ≥ 256 MiB |

## 发射
\`_prep/launch_exp_p1hwb.sh\` @ $(date -Iseconds)
EOF
cp "${PARENT_LOCAL}/PR2_EXP_P1HWB_LAUNCH.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_EXP_P1HWB_LAUNCH.md"

echo "[pr3-p1hwb] self-check test_pillar_c_set_window…"
bash "${ROOT}/scripts/fail-slow/test_pillar_c_set_window.sh"

echo "[pr3-p1hwb] localize SQL avg build_sql self-check…"
python3 - <<'PY'
import sys, os, shutil
shutil.copy("/Users/yinjinrun/Codespace/myportal/project/probing-huawei/scripts/fail-slow/pillar_c_localize_culprit.py", "/tmp/_loc_p1hwb.py")
sys.path.insert(0, "/tmp")
os.environ.pop("PILLAR_C_LOCALIZE_STEP_AGG", None)
import _loc_p1hwb as m
sql = m.build_sql("step_ms", 139, 100)
assert "avg(step_duration_sec)" in sql, sql
assert "local_step >= 39" in sql, sql
assert "local_step <= 139" in sql, sql
print("[pr3-p1hwb] localize.py avg+window=100 OK")
PY

echo "[pr3-p1hwb] SET key hygiene check（不能出现旧键 torch.profiling=）…"
if grep -rn '"torch.profiling"' "${ROOT}/scripts/fail-slow/" 2>/dev/null | grep -v probing.torch | grep -v pyc; then
  echo "[pr3-p1hwb] STALE torch.profiling key found — abort!"
  exit 3
fi
if grep -rEn 'SET[[:space:]]+torch\.profiling=' "${ROOT}/scripts/fail-slow/" 2>/dev/null; then
  echo "[pr3-p1hwb] STALE SET torch.profiling= found — abort!"
  exit 3
fi
echo "[pr3-p1hwb] SET key hygiene OK"

echo "[pr3-p1hwb] hold_exec_run_case.sh gates self-check…"
grep -q 'HCCL_EXEC_TIMEOUT' "${ROOT}/scripts/fail-slow/hold_exec_run_case.sh"
grep -q 'PILLAR_C_NO_PROGRESS_KILL_S' "${ROOT}/scripts/fail-slow/hold_exec_run_case.sh"
grep -q 'NO_JSONL_PROGRESS_' "${ROOT}/scripts/fail-slow/hold_exec_run_case.sh"
echo "[pr3-p1hwb] hold_exec_run_case.sh gates OK"

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
  echo "[pr3-p1hwb] jsync $bname -> $dst rc=$rc"
  return 0
}

echo "[pr3-p1hwb] jsync localize.py → bundle (idempotent)…"
jsync_one "${ROOT}/scripts/fail-slow/pillar_c_localize_culprit.py" "${POD_BUNDLE}/pillar_c_localize_culprit.py"
jsync_one "${ROOT}/scripts/fail-slow/pillar_c_localize_culprit.py" "${POD_BUNDLE}/_pillar_c_localize.py"

echo "[pr3-p1hwb] wait grj-w0 IDLE (mandatory - 让路铁律)…"
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
  echo "[pr3-p1hwb] BLOCKED: grj-w0 busy (让路)"
  echo "BLOCKED grj_w0_busy $(date -Iseconds)" >"${PARENT_LOCAL}/BLOCKED.txt"
  exit 90
fi

echo "[pr3-p1hwb] fire dynamic arm ${ARM_RUN_ID}…"
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

# ========== 主判分：e3_retention_score.py --case P1-HW-B ==========
echo "[pr3-p1hwb] score W* windows (e3_retention_score.py, case=P1-HW-B)…"
DUMP_ROOT="${PARENT_LOCAL}/dynamic/probing_data"
if [[ ! -d "${DUMP_ROOT}" ]]; then
  # fallback: dynamic 目录里其他位置
  DUMP_ROOT=$(find "${PARENT_LOCAL}/dynamic" -maxdepth 3 -type d -name probing_data 2>/dev/null | head -1)
fi
echo "[pr3-p1hwb] DUMP_ROOT=${DUMP_ROOT}"

# 从 set_upgrade.log 抓 culprit_pid（B8/EXP-C 都用这个）；备用：worker_pids.txt
CULPRIT_PID=""
if [[ -d "${PARENT_LOCAL}/dynamic" ]]; then
  CULPRIT_PID=$(grep -h 'SET_OK_WORKER pid=' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null | head -1 | sed 's/.*pid=\([0-9]*\).*/\1/')
  if [[ -z "$CULPRIT_PID" ]]; then
    CULPRIT_PID=$(grep -h '^CULPRIT_PID=' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]')
  fi
fi
echo "[pr3-p1hwb] CULPRIT_PID=${CULPRIT_PID:-<none>}"

W_STAR_JSON="${LOCAL_RESULT_ROOT_BASE}/../pr3_retention_scan/W_STAR_P1_HW_B.json"
W_STAR_JSON_DIR="$(dirname "${W_STAR_JSON}")"
mkdir -p "${W_STAR_JSON_DIR}"

if [[ -n "${CULPRIT_PID}" ]] && [[ -d "${DUMP_ROOT}/${CULPRIT_PID}" ]]; then
  python3 "${ROOT}/scripts/fail-slow/e3_retention_score.py" \
    --case "P1-HW-B" \
    --dump-root "${DUMP_ROOT}" \
    --victim-pid "${CULPRIT_PID}" \
    --out "${W_STAR_JSON_DIR}" \
    2>&1 | tee "${PARENT_LOCAL}/logs/e3_retention_p1hwb.log" || \
    echo "[pr3-p1hwb] e3_retention_score failed (non-fatal, check dump)"
else
  echo "[pr3-p1hwb] SKIP e3_retention_score: no CULPRIT_PID or dump missing"
  echo '{"case":"P1-HW-B","status":"BLOCKED","reason":"culprit_pid_or_dump_missing"}' \
    >"${W_STAR_JSON}"
fi

# 抓 W_STAR 数字
W_STAR_SECS=$(python3 -c "import json,os
p='${W_STAR_JSON}'
if os.path.isfile(p):
  d=json.load(open(p))
  print(d.get('w_star_secs') if d.get('status')=='OK' else 'NA')
else:
  print('?')" 2>/dev/null || echo "?")

PRIMARY=$(python3 -c "import json,os
p='${W_STAR_JSON}'
if os.path.isfile(p):
  d=json.load(open(p))
  print(d.get('primary_evidence') or '')
else:
  print('')" 2>/dev/null || echo "")

# 抓 rise 数字（primary_evidence 里 rise_mb=）
RISE_MB=$(echo "$PRIMARY" | sed -n 's/.*rise_mb=\([0-9.]*\).*/\1/p' | head -1)

SET_DG=$(grep -h 'SET_DOWNGRADE_OK' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null | wc -l | tr -d ' ')
DG_REASON=$(grep -h 'SET_DOWNGRADE ts=' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null | sed -n 's/.*reason=\([^ ]*\).*/\1/p' | head -1)
INJECT_OK=$(find "${PARENT_LOCAL}/dynamic" -name "step_${INJECT_STOP}.marker" 2>/dev/null | wc -l | tr -d ' ')
DONE_MARK=$(find "${PARENT_LOCAL}/dynamic" -name "node_0.done" 2>/dev/null | wc -l | tr -d ' ')

VERDICT=PARTIAL
if [[ "${W_STAR_SECS}" != "NA" ]] && [[ "${W_STAR_SECS}" != "?" ]] && [[ -n "${W_STAR_SECS}" ]] && [[ "${INJECT_OK}" -ge 1 ]] && [[ "${SET_DG}" -ge 1 ]]; then
  VERDICT=PASS
elif [[ "${W_STAR_SECS}" == "NA" ]] || [[ -z "${W_STAR_SECS}" ]] || [[ "${W_STAR_SECS}" == "?" ]]; then
  VERDICT=FAIL
elif grep -q 'HANG_DETECTED' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null; then
  VERDICT=BLOCKED
fi

cat >"${PARENT_LOCAL}/PR3_EXP_P1HWB_STATUS.md" <<MD
# PR-3 P1-HW-B 补跑 · ${VERDICT}

**日期**：$(date +%Y-%m-%d)
**parent**：\`${PARENT_RUN_ID}\`
**pod**：\`${POD}\`（grj-w0）
**case**：P1-HW-B loud（HBM 渐衰 INLINE 1b ramp）

## 头条 · W\* 追溯窗（handbook §3.4 P1-HW-B 主判据）

| 项 | 值 | 判据 |
|----|-----|-----|
| **W\*** | **${W_STAR_SECS}** 秒 | 6 档位 {60,300,900,1800,3600,all} 首个 enough=Y |
| primary_evidence | \`${PRIMARY:0:120}\` | \`gpu.utilization_used_bytes:rise_mb=X:dev=D\` |
| rise_mb | **${RISE_MB:-?}** | 阈值 ≥ 256 MiB |

## 五指标

| 项 | 值 | 判据 |
|----|-----|-----|
| culprit_pid | **${CULPRIT_PID:-?}** | localize/SET_OK_WORKER 命中 |
| SET_DOWNGRADE | **${SET_DG}** | reason=${DG_REASON:-?} |
| inject_stop marker | ${INJECT_OK} | ITERS=${ITERS} inject=[${INJECT_START},${INJECT_STOP}] |
| node_0.done | ${DONE_MARK} | 训练完成 |

## B8 三处 gate（保留）

- STEP_AGG=**${PILLAR_C_LOCALIZE_STEP_AGG}** · STEP_WINDOW=**${PILLAR_C_LOCALIZE_STEP_WINDOW}**
- HCCL_EXEC_TIMEOUT=**${HCCL_EXEC_TIMEOUT}**
- NO_PROGRESS_KILL_S=**${PILLAR_C_NO_PROGRESS_KILL_S}**

## B6 gates

- COMM_LAZY=${PROBING_TORCH_COMM_COLLECTIVE_LAZY} · STEP_TIMING_LAZY=${PROBING_TORCH_STEP_TIMING_LAZY}
- PRUNE_EXTRA_PIDS=${PILLAR_C_PRUNE_EXTRA_PIDS} · DRY=${PILLAR_C_PRUNE_DRY_RUN}

- W\* 判分：\`${W_STAR_JSON}\`
- arm rc=${arm_rc}
MD
cp "${PARENT_LOCAL}/PR3_EXP_P1HWB_STATUS.md" "${LOCAL_RESULT_ROOT_BASE}/../pr3_retention_scan/PR3_EXP_P1HWB_STATUS.md"

echo "[pr3-p1hwb] ${VERDICT} W*=${W_STAR_SECS}s rise=${RISE_MB:-?}MiB SET_DG=${SET_DG} inject_ok=${INJECT_OK}"
echo "$PARENT_RUN_ID"
