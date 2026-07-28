#!/usr/bin/env bash
# PR-2 实验 C · 追溯窗复现（P1-SW-C GPU 编译尖刺）
#  - handbook §2.4 实验 C：验证 SET 键名修好后正式跑能复现离线 W*=100
#  - 前置：B8 长跑 PASS（三 gate 生效，culprit=7 稳定命中；SET 键名统一到 probing.torch.profiling）
#  - pod: grj-megatron-32card-0716-worker-0（IDLE 让路铁律；主池 yysong-w0 rank 15 pre-existing stuck 未修）
#  - 三处 B8 gate: STEP_AGG=avg STEP_WINDOW=100 NO_PROGRESS_KILL_S=90 HCCL_EXEC_TIMEOUT=600
#  - 判分主线：e1_score_window.py（P1-SW-C duration_spike @ W=100）
set -euo pipefail
ROOT="/Users/yinjinrun/Codespace/myportal/project/probing-huawei"
TS="${RUN_ID_TS:-$(date +%Y%m%d_%H%M%S)}"
export PARENT_RUN_ID="${PARENT_RUN_ID:-${TS}-pillar-c-v3-pr2-exp-c-p1swc}"
export ARM_RUN_ID="${PARENT_RUN_ID}-upgrade_rate_1.0"

# B6 gates（B8 保留）
export PROBING_TORCH_COMM_COLLECTIVE_LAZY="${PROBING_TORCH_COMM_COLLECTIVE_LAZY:-1}"
export PROBING_TORCH_STEP_TIMING_LAZY="${PROBING_TORCH_STEP_TIMING_LAZY:-0}"
export PILLAR_C_PRUNE_EXTRA_PIDS="${PILLAR_C_PRUNE_EXTRA_PIDS:-1}"
export PILLAR_C_PRUNE_DRY_RUN="${PILLAR_C_PRUNE_DRY_RUN:-0}"

# B8 三处新 gate（B8 长跑已 PASS 目标；实验 C 保留）
export PILLAR_C_LOCALIZE_STEP_AGG="${PILLAR_C_LOCALIZE_STEP_AGG:-avg}"
export PILLAR_C_LOCALIZE_STEP_WINDOW="${PILLAR_C_LOCALIZE_STEP_WINDOW:-100}"
export PILLAR_C_NO_PROGRESS_KILL_S="${PILLAR_C_NO_PROGRESS_KILL_S:-90}"
export HCCL_EXEC_TIMEOUT="${HCCL_EXEC_TIMEOUT:-600}"

export FS_SHARED_SCRIPTS="/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow"
export FS_PLATFORM_ASCEND="${FS_SHARED_SCRIPTS}/platform/ascend"
export LOCAL_RESULT_ROOT_BASE="${ROOT}/results/ascend-ais/pillar_c_v3/pr2_localize"
unset OUT_FAMILY
export POD_BUNDLE="/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle"

# 实验 C 场景：GPU 编译尖刺（P1-SW-C loud）；victim rank=7（dose_recipes.yaml defaults）
export CASE_ID=P1-SW-C
export DOSE=loud
export PHASE=pilot
export POD=grj-megatron-32card-0716-worker-0
export NPROC=16
export NNODES=1
export SIDECAR_LOCAL_RANK=7

# 1000 步 · inject [100, 300]（与 B8 对齐；handbook §2.4 实验 C 无特别推荐）
export ITERS=1000
export WARMUP=50
export INJECT_START=100
export INJECT_STOP=300
export DUMP_WAIT_S=90
export DUMP_PROBING_SQL=1

# inline 2c 参数：使用 dose_recipes loud（n=1024,every=1,fallback_s=0.25）
# 注：run_pillar_c_arm.sh 里 P1-SW-C 默认 fallback_s=0.6；handbook 判据 dur_s>=0.5 → 显式给 0.6 更稳
export INLINE_2C_N=1024
export INLINE_2C_EVERY=1
export INLINE_2C_FALLBACK_S=0.6

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
# 保留 legacy WINDOW 变量（对 comm_max 生效；step_ms 用 PILLAR_C_LOCALIZE_STEP_WINDOW=100）
export PILLAR_C_LOCALIZE_WINDOW=20
export PILLAR_C_LOCALIZE_TIMEOUT_S=8
export PILLAR_C_LOCALIZE_RETRIES=1
export PILLAR_C_LOCALIZE_TOTAL_BUDGET_S=60
export PILLAR_C_LOCALIZE_PARALLEL=4
export PILLAR_C_ATTACH_READY_WAIT_S=30
export PILLAR_C_SET_BLOCK_TIMEOUT_S=120
export PILLAR_C_LOCALIZE_SECONDARY=1

# v2 P1-SW-C 全量臂 REUSE 参考（同 case，用作数据量比对照上界）
FULL_REF="/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260726_012627-pillar-c-p1-sw-c-loud/full_fidelity"
W_STAR=100

JUMP_KUBECTL="/root/.cache/volcano/kubectl/kubectl"
JUMP_KUBECONFIG="/tmp/config-vc-a3-241ceshi-songyiyang.yaml"
JUMP_HOST="ais-cf3e61a5"

PARENT_LOCAL="${LOCAL_RESULT_ROOT_BASE}/${PARENT_RUN_ID}"
mkdir -p "${PARENT_LOCAL}/logs" "${PARENT_LOCAL}/full_fidelity"
echo "${PARENT_RUN_ID}" >"${PARENT_LOCAL}/PARENT_RUN_ID.txt"
echo "${ARM_RUN_ID}" >"${PARENT_LOCAL}/ARM_RUN_ID.txt"

cat >"${PARENT_LOCAL}/PR2_EXP_C_LAUNCH.md" <<EOF
# PR-2 实验 C · 追溯窗复现（P1-SW-C）发射记录

| 字段 | 值 |
|------|-----|
| parent | \`${PARENT_RUN_ID}\` |
| arm | \`${ARM_RUN_ID}\` |
| pod | \`${POD}\`（grj-w0，主池 yysong-w0 rank15 stuck 让路） |
| case | P1-SW-C loud · GT victim rank=7 · inject_kind=2c (n=${INLINE_2C_N} every=${INLINE_2C_EVERY} fallback_s=${INLINE_2C_FALLBACK_S}) |
| ITERS | ${ITERS} · inject [${INJECT_START},${INJECT_STOP}] |
| scope | localize + **${PILLAR_C_SET_WINDOW_S}s** 时基降回 |
| hang_max | **${PILLAR_C_SET_HANG_MAX_S}s**（8min） |
| 常驻 | \`on,rate=0\` |
| 全量臂 | REUSE v2 P1-SW-C \`${FULL_REF}\` |
| B8 gates | STEP_AGG=**${PILLAR_C_LOCALIZE_STEP_AGG}** STEP_WINDOW=**${PILLAR_C_LOCALIZE_STEP_WINDOW}** NO_PROG_KILL_S=**${PILLAR_C_NO_PROGRESS_KILL_S}** HCCL_EXEC_TIMEOUT=**${HCCL_EXEC_TIMEOUT}** |
| B6 gates | COMM_LAZY=${PROBING_TORCH_COMM_COLLECTIVE_LAZY} · STEP_TIMING_LAZY=${PROBING_TORCH_STEP_TIMING_LAZY} · PRUNE=${PILLAR_C_PRUNE_EXTRA_PIDS} · DRY=${PILLAR_C_PRUNE_DRY_RUN} |
| 目标（handbook §2.4） | W* first enough=Y @ W=100（不迟于 200）· duration_spike step=X dur_s>=0.5 module=Y |
| 参考 | v2 E1_off P1-SW-C W*=**100** (spike@238 AdamW dur≈0.71s) |
| v2 正式跑失败 | \`20260726_173830-pillar-c-e1-p1-sw-c-loud\` (SET 键名错 → 未升详 → NO_W_STAR) |

## 发射
\`_prep/launch_exp_c.sh\` @ $(date -Iseconds)
EOF
cp "${PARENT_LOCAL}/PR2_EXP_C_LAUNCH.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_EXP_C_LAUNCH.md"

echo "[pr2-exp-c] self-check test_pillar_c_set_window…"
bash "${ROOT}/scripts/fail-slow/test_pillar_c_set_window.sh"

echo "[pr2-exp-c] localize SQL avg build_sql self-check…"
python3 - <<'PY'
import sys, os, shutil
shutil.copy("/Users/yinjinrun/Codespace/myportal/project/probing-huawei/scripts/fail-slow/pillar_c_localize_culprit.py", "/tmp/_loc_expc.py")
sys.path.insert(0, "/tmp")
os.environ.pop("PILLAR_C_LOCALIZE_STEP_AGG", None)
import _loc_expc as m
sql = m.build_sql("step_ms", 139, 100)
assert "avg(step_duration_sec)" in sql, sql
assert "local_step >= 39" in sql, sql
assert "local_step <= 139" in sql, sql
print("[pr2-exp-c] localize.py avg+window=100 OK")
PY

echo "[pr2-exp-c] SET key hygiene check（不能出现旧键 torch.profiling=）…"
if grep -rn '"torch.profiling"' "${ROOT}/scripts/fail-slow/" 2>/dev/null | grep -v probing.torch | grep -v pyc; then
  echo "[pr2-exp-c] STALE torch.profiling key found — abort!"
  exit 3
fi
if grep -rEn 'SET[[:space:]]+torch\.profiling=' "${ROOT}/scripts/fail-slow/" 2>/dev/null; then
  echo "[pr2-exp-c] STALE SET torch.profiling= found — abort!"
  exit 3
fi
echo "[pr2-exp-c] SET key hygiene OK"

echo "[pr2-exp-c] hold_exec_run_case.sh gates self-check…"
grep -q 'HCCL_EXEC_TIMEOUT' "${ROOT}/scripts/fail-slow/hold_exec_run_case.sh"
grep -q 'PILLAR_C_NO_PROGRESS_KILL_S' "${ROOT}/scripts/fail-slow/hold_exec_run_case.sh"
grep -q 'NO_JSONL_PROGRESS_' "${ROOT}/scripts/fail-slow/hold_exec_run_case.sh"
echo "[pr2-exp-c] hold_exec_run_case.sh gates OK"

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
  echo "[pr2-exp-c] jsync $bname -> $dst rc=$rc"
  return 0
}

echo "[pr2-exp-c] jsync localize.py → bundle (idempotent)…"
jsync_one "${ROOT}/scripts/fail-slow/pillar_c_localize_culprit.py" "${POD_BUNDLE}/pillar_c_localize_culprit.py"
jsync_one "${ROOT}/scripts/fail-slow/pillar_c_localize_culprit.py" "${POD_BUNDLE}/_pillar_c_localize.py"

echo "[pr2-exp-c] wait grj-w0 IDLE (mandatory - 让路铁律)…"
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
  echo "[pr2-exp-c] BLOCKED: grj-w0 busy (让路)"
  echo "BLOCKED grj_w0_busy $(date -Iseconds)" >"${PARENT_LOCAL}/BLOCKED.txt"
  exit 90
fi

echo "[pr2-exp-c] fire dynamic arm ${ARM_RUN_ID}…"
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

# 全量臂 REUSE v2 P1-SW-C（用于 pr2_e3_score_ratio.py 数据量比对照）
cat >"${PARENT_LOCAL}/full_fidelity/REUSE.txt" <<EOF
reuse=1
path=${FULL_REF}
case=${CASE_ID}
note=exp-C reuse v2 P1-SW-C full_fidelity upper bound
EOF
full_bytes=$(ssh -o ConnectTimeout=30 "$JUMP_HOST" \
  "export KUBECONFIG=${JUMP_KUBECONFIG}; ${JUMP_KUBECTL} -n default exec ${POD} -- du -sb ${FULL_REF}/probing_data" \
  | awk 'NF>=1{print $1; exit}')
full_bytes=$(echo "${full_bytes:-}" | tr -cd '0-9')
if [[ -z "${full_bytes}" ]]; then
  full_bytes=0
fi
echo "${full_bytes}" >"${PARENT_LOCAL}/full_fidelity/total_dump_bytes.txt"

# ========== 主判分：e1_score_window.py（W* 追溯窗）==========
# 注意：e1_score_window.py 期待 parent_local/arm_dir 结构；我们的动态臂在 dynamic/ 下
# 用符号链接把 dynamic 映射为 rate_0 让脚本能找到；或直接指 parent_local=. arm_dir=dynamic
echo "[pr2-exp-c] score W* windows (e1_score_window.py, case=P1-SW-C)…"
python3 "${ROOT}/scripts/fail-slow/e1_score_window.py" \
  --parent-local "$PARENT_LOCAL" \
  --arm-dir "dynamic" \
  --case "P1-SW-C" \
  --windows 50 100 200 full \
  --out "${PARENT_LOCAL}/PR2_TRACEWINDOW_P1SWC.md" || echo "[pr2-exp-c] e1_score failed (non-fatal, check dynamic/ probing_data)"

# JSON is written next to MD as E1_WINDOW.json → 复制为 PR2_TRACEWINDOW_P1SWC.json
if [[ -f "${PARENT_LOCAL}/E1_WINDOW.json" ]]; then
  cp "${PARENT_LOCAL}/E1_WINDOW.json" "${PARENT_LOCAL}/PR2_TRACEWINDOW_P1SWC.json"
fi
if [[ -f "${PARENT_LOCAL}/PR2_TRACEWINDOW_P1SWC.md" ]]; then
  cp "${PARENT_LOCAL}/PR2_TRACEWINDOW_P1SWC.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_TRACEWINDOW_P1SWC.md"
  cp "${PARENT_LOCAL}/PR2_TRACEWINDOW_P1SWC.json" "${LOCAL_RESULT_ROOT_BASE}/PR2_TRACEWINDOW_P1SWC.json" 2>/dev/null || true
fi

# ========== 附加：数据量比（复用 B8 判分脚本，输入同 case）==========
echo "[pr2-exp-c] score data-volume ratio (pr2_e3_score_ratio.py)…"
python3 "${ROOT}/scripts/fail-slow/pr2_e3_score_ratio.py" \
  --parent-local "$PARENT_LOCAL" \
  --dynamic-arm "${PARENT_LOCAL}/dynamic" \
  --case "$CASE_ID" \
  --w-star "$W_STAR" \
  --resident-rate "$RESIDENT_RATE" \
  --full-ref "$FULL_REF" \
  --dynamic-reuse-run "$PARENT_RUN_ID" \
  --out-json "${PARENT_LOCAL}/PR2_EXP_C_RATIO.json" \
  --out-md "${PARENT_LOCAL}/PR2_EXP_C_RATIO.md" || echo "[pr2-exp-c] ratio score failed (non-fatal)"

# ========== 五指标 + 判定 ==========
# W* 来自 PR2_TRACEWINDOW_P1SWC.json
W_STAR_FOUND=$(python3 -c "import json,os
p='${PARENT_LOCAL}/PR2_TRACEWINDOW_P1SWC.json'
print(json.load(open(p)).get('W_star','?') if os.path.isfile(p) else '?')" 2>/dev/null || echo "?")
W_EVID_100=$(python3 -c "import json,os
p='${PARENT_LOCAL}/PR2_TRACEWINDOW_P1SWC.json'
if not os.path.isfile(p):
  print('?')
else:
  d=json.load(open(p))
  wins=d.get('windows') or []
  rec=next((w for w in wins if str(w.get('W'))=='100'), None)
  print(rec.get('evidence') if rec else '?')" 2>/dev/null || echo "?")

# 从 ratio JSON 抽 dense / culprit / fallback
HEADLINE=$(python3 -c "import json,os
p='${PARENT_LOCAL}/PR2_EXP_C_RATIO.json'
print(json.load(open(p))['headline_pct'] if os.path.isfile(p) else '?')" 2>/dev/null || echo "?")
DENSE=$(python3 -c "import json,os
p='${PARENT_LOCAL}/PR2_EXP_C_RATIO.json'
d=json.load(open(p)) if os.path.isfile(p) else {}
print(d.get('dynamic',{}).get('torch_trace_dense_ranks','?'))" 2>/dev/null || echo "?")
CULPRIT=$(python3 -c "import json,os
p='${PARENT_LOCAL}/PR2_EXP_C_RATIO.json'
d=json.load(open(p)) if os.path.isfile(p) else {}
print(d.get('dynamic',{}).get('localize',{}).get('culprit_rank','?'))" 2>/dev/null || echo "?")
LOC_FALLBACK=$(python3 -c "import json,os
p='${PARENT_LOCAL}/PR2_EXP_C_RATIO.json'
d=json.load(open(p)) if os.path.isfile(p) else {}
print(d.get('dynamic',{}).get('localize',{}).get('localize_fallback','?'))" 2>/dev/null || echo "?")
SET_DG=$(grep -h 'SET_DOWNGRADE_OK' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null | wc -l | tr -d ' ')
DG_REASON=$(grep -h 'SET_DOWNGRADE ts=' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null | sed -n 's/.*reason=\([^ ]*\).*/\1/p' | head -1)
INJECT_OK=$(find "${PARENT_LOCAL}/dynamic" -name "step_${INJECT_STOP}.marker" 2>/dev/null | wc -l | tr -d ' ')

VERDICT=PARTIAL
# 主判：W_STAR_FOUND ∈ {50,100,200}（不迟于 200 即 PASS 达成 handbook §2.4 判据）
W_STAR_OK=0
case "$W_STAR_FOUND" in
  100|50) W_STAR_OK=1 ;;
  200) W_STAR_OK=1 ;;
  *) W_STAR_OK=0 ;;
esac

# 完整 PASS：W_STAR<=200 且 SET 触发 downgrade 且 inject 完 markers 齐 且 culprit=7
if [[ "$W_STAR_OK" -eq 1 ]] && [[ "$INJECT_OK" -ge 1 ]] && [[ "$SET_DG" -ge 1 ]] \
   && [[ "$CULPRIT" == "7" ]] && [[ "$LOC_FALLBACK" == "0" ]]; then
  VERDICT=PASS
elif [[ "$W_STAR_OK" -eq 1 ]]; then
  VERDICT=PARTIAL
elif grep -q 'HANG_DETECTED' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null; then
  VERDICT=BLOCKED
fi

cat >"${PARENT_LOCAL}/PR2_EXP_C_STATUS.md" <<MD
# PR-2 实验 C · ${VERDICT}

**日期**：$(date +%Y-%m-%d)
**parent**：\`${PARENT_RUN_ID}\`
**pod**：\`${POD}\`（grj-w0）
**case**：P1-SW-C loud（GPU 编译尖刺 inline_2c）

## 头条 · W* 追溯窗（handbook §2.4 实验 C 主判据）

| 项 | 值 | 判据 |
|----|-----|-----|
| **W\*** | **${W_STAR_FOUND}** | 目标 =100（不迟于 200） |
| W=100 evidence | \`${W_EVID_100}\` | 期望 \`torch_trace.duration_spike:step=X:dur_s=…:module=…\` |
| v2 E1-off 参考 | W*=100 spike@238 AdamW dur≈0.71s | — |

## 五指标

| 项 | 值 | 判据 |
|----|-----|-----|
| 头条比（数据量比） | **${HEADLINE}%** | 目标 <100% |
| dense_ranks | **${DENSE}** | 目标 =1（此 case 未必强要，主看 W\*） |
| culprit_rank | **${CULPRIT}** | GT=7 |
| LOCALIZE_FALLBACK | **${LOC_FALLBACK}** | 目标 =0（SQL 命中） |
| SET_DOWNGRADE | **${SET_DG}** | reason=${DG_REASON:-?} |
| inject_stop marker | ${INJECT_OK} | ITERS=${ITERS} inject=[${INJECT_START},${INJECT_STOP}] |

## B8 三处 gate（保留）

- STEP_AGG=**${PILLAR_C_LOCALIZE_STEP_AGG}** · STEP_WINDOW=**${PILLAR_C_LOCALIZE_STEP_WINDOW}**
- HCCL_EXEC_TIMEOUT=**${HCCL_EXEC_TIMEOUT}**
- NO_PROGRESS_KILL_S=**${PILLAR_C_NO_PROGRESS_KILL_S}**

## B6 gates

- COMM_LAZY=${PROBING_TORCH_COMM_COLLECTIVE_LAZY} · STEP_TIMING_LAZY=${PROBING_TORCH_STEP_TIMING_LAZY}
- PRUNE_EXTRA_PIDS=${PILLAR_C_PRUNE_EXTRA_PIDS} · DRY=${PILLAR_C_PRUNE_DRY_RUN}

- W* 判分：\`PR2_TRACEWINDOW_P1SWC.md\`
- 数据量比：\`PR2_EXP_C_RATIO.md\`
- arm rc=${arm_rc}
MD
cp "${PARENT_LOCAL}/PR2_EXP_C_STATUS.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_EXP_C_STATUS.md"

echo "[pr2-exp-c] ${VERDICT} W*=${W_STAR_FOUND} headline=${HEADLINE}% dense=${DENSE} culprit=${CULPRIT} SET_DG=${SET_DG}"
echo "$PARENT_RUN_ID"
