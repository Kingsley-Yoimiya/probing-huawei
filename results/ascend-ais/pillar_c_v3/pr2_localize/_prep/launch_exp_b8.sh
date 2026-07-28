#!/usr/bin/env bash
# PR-2 实验 B8 · 长跑（ITERS=1000）
#  - 上游 smoke run_id 20260728_203149-pillar-c-v3-pr2-e3-b8-smoke 判定 PASS
#  - 目标 headline<100%(理想 <60%) · culprit_rank=7 稳定 · dense_ranks=1 · LOCALIZE_FALLBACK=0
#  - pod: grj-megatron-32card-0716-worker-0（IDLE 让路铁律；主池 yysong-w0 rank 15 pre-existing stuck 未修）
#  - 三处 B8 gate: STEP_AGG=avg STEP_WINDOW=100 NO_PROGRESS_KILL_S=90 HCCL_EXEC_TIMEOUT=600
set -euo pipefail
ROOT="/Users/yinjinrun/Codespace/myportal/project/probing-huawei"
TS="${RUN_ID_TS:-$(date +%Y%m%d_%H%M%S)}"
export PARENT_RUN_ID="${PARENT_RUN_ID:-${TS}-pillar-c-v3-pr2-e3-b8}"
export ARM_RUN_ID="${PARENT_RUN_ID}-upgrade_rate_1.0"

# B6 gates（B7/smoke 保留;必须传到 hold_exec_run_case → torchrun env）
export PROBING_TORCH_COMM_COLLECTIVE_LAZY="${PROBING_TORCH_COMM_COLLECTIVE_LAZY:-1}"
export PROBING_TORCH_STEP_TIMING_LAZY="${PROBING_TORCH_STEP_TIMING_LAZY:-0}"
export PILLAR_C_PRUNE_EXTRA_PIDS="${PILLAR_C_PRUNE_EXTRA_PIDS:-1}"
export PILLAR_C_PRUNE_DRY_RUN="${PILLAR_C_PRUNE_DRY_RUN:-0}"

# B8 三处新 gate（smoke 已验；长跑保持一致）
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

# 长跑：1000 步 · inject [100, 300]（不倒退 B7 参数）
export ITERS=1000
export WARMUP=50
export INJECT_START=100
export INJECT_STOP=300
export DUMP_WAIT_S=90
export DUMP_PROBING_SQL=1

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

FULL_REF="/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260725_230350-pillar-c-p3-sw-a-loud/full_fidelity"
W_STAR=100

JUMP_KUBECTL="/root/.cache/volcano/kubectl/kubectl"
JUMP_KUBECONFIG="/tmp/config-vc-a3-241ceshi-songyiyang.yaml"
JUMP_HOST="ais-cf3e61a5"

PARENT_LOCAL="${LOCAL_RESULT_ROOT_BASE}/${PARENT_RUN_ID}"
mkdir -p "${PARENT_LOCAL}/logs" "${PARENT_LOCAL}/full_fidelity"
echo "${PARENT_RUN_ID}" >"${PARENT_LOCAL}/PARENT_RUN_ID.txt"
echo "${ARM_RUN_ID}" >"${PARENT_LOCAL}/ARM_RUN_ID.txt"

cat >"${PARENT_LOCAL}/PR2_EXP_B8_LAUNCH.md" <<EOF
# PR-2 实验 B8 · 长跑发射记录

| 字段 | 值 |
|------|-----|
| parent | \`${PARENT_RUN_ID}\` |
| arm | \`${ARM_RUN_ID}\` |
| pod | \`${POD}\`（grj-w0，主池 yysong-w0 rank15 stuck 让路） |
| case | P3-SW-A · GT culprit rank=7 |
| ITERS | ${ITERS} · inject [${INJECT_START},${INJECT_STOP}] |
| scope | localize + **15s** 时基降回 |
| hang_max | **${PILLAR_C_SET_HANG_MAX_S}s**（8min） |
| 常驻 | \`on,rate=0\` |
| 全量臂 | REUSE v2 \`${FULL_REF}\` |
| B8 gates | STEP_AGG=**${PILLAR_C_LOCALIZE_STEP_AGG}** STEP_WINDOW=**${PILLAR_C_LOCALIZE_STEP_WINDOW}** NO_PROG_KILL_S=**${PILLAR_C_NO_PROGRESS_KILL_S}** HCCL_EXEC_TIMEOUT=**${HCCL_EXEC_TIMEOUT}** |
| B6 gates | COMM_LAZY=${PROBING_TORCH_COMM_COLLECTIVE_LAZY} · STEP_TIMING_LAZY=${PROBING_TORCH_STEP_TIMING_LAZY} · PRUNE=${PILLAR_C_PRUNE_EXTRA_PIDS} · DRY=${PILLAR_C_PRUNE_DRY_RUN} |
| 前置 smoke | \`20260728_203149-pillar-c-v3-pr2-e3-b8-smoke\` PASS ✓ |
| 目标 | headline<100% · culprit=7 · dense=1 · LOCALIZE_FALLBACK=0 · SET_OK+DG=Y |

## 发射
\`_prep/launch_exp_b8.sh\` @ $(date -Iseconds)
EOF
cp "${PARENT_LOCAL}/PR2_EXP_B8_LAUNCH.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_EXP_B8_LAUNCH.md"

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

echo "[pr2-b8] hold_exec_run_case.sh gates self-check…"
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

# 全量臂 REUSE v2
cat >"${PARENT_LOCAL}/full_fidelity/REUSE.txt" <<EOF
reuse=1
path=${FULL_REF}
case=${CASE_ID}
note=B8 reuse v2 full_fidelity upper bound
EOF
full_bytes=$(ssh -o ConnectTimeout=30 "$JUMP_HOST" \
  "export KUBECONFIG=${JUMP_KUBECONFIG}; ${JUMP_KUBECTL} -n default exec ${POD} -- du -sb ${FULL_REF}/probing_data" \
  | awk 'NF>=1{print $1; exit}')
full_bytes=$(echo "${full_bytes:-}" | tr -cd '0-9')
if [[ -z "${full_bytes}" ]]; then
  full_bytes=1791975360
fi
echo "${full_bytes}" >"${PARENT_LOCAL}/full_fidelity/total_dump_bytes.txt"

echo "[pr2-b8] score…"
python3 "${ROOT}/scripts/fail-slow/pr2_e3_score_ratio.py" \
  --parent-local "$PARENT_LOCAL" \
  --dynamic-arm "${PARENT_LOCAL}/dynamic" \
  --case "$CASE_ID" \
  --w-star "$W_STAR" \
  --resident-rate "$RESIDENT_RATE" \
  --full-ref "$FULL_REF" \
  --dynamic-reuse-run "$PARENT_RUN_ID" \
  --out-json "${PARENT_LOCAL}/PR2_E3_RATIO_B8.json" \
  --out-md "${PARENT_LOCAL}/PR2_E3_RATIO_B8.md" || echo "[pr2-b8] score failed (non-fatal)"

if [[ -f "${PARENT_LOCAL}/PR2_E3_RATIO_B8.md" ]]; then
  cp "${PARENT_LOCAL}/PR2_E3_RATIO_B8.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_E3_RATIO_B8.md"
  cp "${PARENT_LOCAL}/PR2_E3_RATIO_B8.json" "${LOCAL_RESULT_ROOT_BASE}/PR2_E3_RATIO_B8.json"
fi

HEADLINE=$(python3 -c "import json; print(json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B8.json'))['headline_pct'])" 2>/dev/null || echo "?")
DENSE=$(python3 -c "import json; d=json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B8.json')); print(d['dynamic'].get('torch_trace_dense_ranks','?'))" 2>/dev/null || echo "?")
CULPRIT=$(python3 -c "import json; d=json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B8.json')); print(d['dynamic'].get('localize',{}).get('culprit_rank','?'))" 2>/dev/null || echo "?")
LOC_FALLBACK=$(python3 -c "import json; d=json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B8.json')); print(d['dynamic'].get('localize',{}).get('localize_fallback','?'))" 2>/dev/null || echo "?")
CULPRIT_ROWS=$(python3 -c "
import json
d=json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B8.json'))
loc=d['dynamic'].get('localize',{})
cpid=str(loc.get('culprit_pid',''))
rows=[r for r in d['dynamic'].get('torch_trace_ranks',[]) if str(r.get('pid'))==cpid]
print(rows[0]['n_rows'] if rows else 0)
" 2>/dev/null || echo "?")
NON_CULPRIT_MAX=$(python3 -c "
import json
d=json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B8.json'))
loc=d['dynamic'].get('localize',{})
cpid=str(loc.get('culprit_pid',''))
mx=max((int(r.get('n_rows') or 0) for r in d['dynamic'].get('torch_trace_ranks',[]) if str(r.get('pid'))!=cpid), default=0)
print(mx)
" 2>/dev/null || echo "?")
SET_DG=$(grep -h 'SET_DOWNGRADE_OK' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null | wc -l | tr -d ' ')
DG_REASON=$(grep -h 'SET_DOWNGRADE ts=' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null | sed -n 's/.*reason=\([^ ]*\).*/\1/p' | head -1)
INJECT_OK=$(find "${PARENT_LOCAL}/dynamic" -name "step_${INJECT_STOP}.marker" 2>/dev/null | wc -l | tr -d ' ')

NATIVE_OK=no
if [[ "$SET_DG" -ge 1 ]]; then
  NATIVE_OK=yes
fi

VERDICT=PARTIAL
HEADLINE_OK=0
if python3 -c "import json; h=float(json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B8.json'))['headline_pct']); exit(0 if h<100 else 1)" 2>/dev/null; then
  HEADLINE_OK=1
fi

if [[ "$INJECT_OK" -ge 1 ]] && [[ "$SET_DG" -ge 1 ]] && [[ "$DENSE" == "1" ]] && [[ "$CULPRIT" == "7" ]] \
   && [[ "$NATIVE_OK" == "yes" ]] && [[ "$HEADLINE_OK" -eq 1 ]] \
   && [[ "${CULPRIT_ROWS:-0}" -gt 0 ]] && [[ "${NON_CULPRIT_MAX:-99}" -eq 0 ]] \
   && [[ "$LOC_FALLBACK" == "0" ]]; then
  VERDICT=PASS
elif [[ "$SET_DG" -ge 1 ]] && [[ "$INJECT_OK" -ge 1 ]] && [[ "$CULPRIT" == "7" ]]; then
  VERDICT=PARTIAL
elif grep -q 'HANG_DETECTED' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null; then
  VERDICT=BLOCKED
fi

cat >"${PARENT_LOCAL}/PR2_EXP_B8_STATUS.md" <<MD
# PR-2 实验 B8 · ${VERDICT}

**日期**：$(date +%Y-%m-%d)
**parent**：\`${PARENT_RUN_ID}\`
**pod**：\`${POD}\`（grj-w0）

## 头条 · 五指标

| 项 | 值 | 判据 |
|----|-----|-----|
| 头条比 | **${HEADLINE}%** | 目标 <100%（理想 <60%） |
| dense_ranks | **${DENSE}** | 目标 =1 |
| culprit_rank | **${CULPRIT}** | GT=7 |
| LOCALIZE_FALLBACK | **${LOC_FALLBACK}** | 目标 =0（SQL 命中） |
| SET_OK / SET_DOWNGRADE | Y / **${SET_DG}** | reason=${DG_REASON:-?} |
| culprit TT rows | **${CULPRIT_ROWS}** | 目标 >0 |
| 非 culprit max rows | **${NON_CULPRIT_MAX}** | 目标 =0 |
| inject_stop marker | ${INJECT_OK} | ITERS=${ITERS} inject=[${INJECT_START},${INJECT_STOP}] |
| WINDOW_S | ${PILLAR_C_SET_WINDOW_S} | 时基降回 |
| hang_max | ${PILLAR_C_SET_HANG_MAX_S}s | 8min |

## B8 三处 gate

- STEP_AGG=**${PILLAR_C_LOCALIZE_STEP_AGG}** · STEP_WINDOW=**${PILLAR_C_LOCALIZE_STEP_WINDOW}**（B7 max/20 mis-localize=5 → B8 avg/100 预期命中 7）
- HCCL_EXEC_TIMEOUT=**${HCCL_EXEC_TIMEOUT}**（B7 默认 1800s 太长 → 600s 让 driver 兜底能触发）
- NO_PROGRESS_KILL_S=**${PILLAR_C_NO_PROGRESS_KILL_S}**（B7 stall 30min 死等 → 90s 主动 kill）

## B6 gates

- COMM_LAZY=${PROBING_TORCH_COMM_COLLECTIVE_LAZY} · STEP_TIMING_LAZY=${PROBING_TORCH_STEP_TIMING_LAZY}
- PRUNE_EXTRA_PIDS=${PILLAR_C_PRUNE_EXTRA_PIDS} · DRY=${PILLAR_C_PRUNE_DRY_RUN}

- 判分：\`PR2_E3_RATIO_B8.md\`
- arm rc=${arm_rc}
MD
cp "${PARENT_LOCAL}/PR2_EXP_B8_STATUS.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_EXP_B8_STATUS.md"

CODE_STATUS="${LOCAL_RESULT_ROOT_BASE}/PR2_CODE_STATUS.md"
if [[ -f "$CODE_STATUS" ]]; then
  if ! grep -q "实验 B8" "$CODE_STATUS"; then
    cat >>"$CODE_STATUS" <<MD

## 实验 B8 状态（B7 code + AVG SQL/window=100 + no-progress kill + HCCL 600s）

| 轮次 | run_id | localize | E3 头条比 | dense | culprit_rows | 备注 |
|------|--------|----------|-----------|-------|--------------|------|
| **B8** | \`${PARENT_RUN_ID}\` | culprit=**${CULPRIT}** | **${HEADLINE}%** | **${DENSE}** | **${CULPRIT_ROWS}** | WINDOW=15s hang=480s SET_DG=${SET_DG} B8-gates(avg/100/90s/600s) |
MD
  fi
fi

cat >"${LOCAL_RESULT_ROOT_BASE}/PR2_VOLUME.md" <<MD
# PR-2 数据量比（E3 头条）

| 轮次 | run_id | headline | dense | culprit | 备注 |
|------|--------|----------|-------|---------|------|
| v2 ref | 20260726_181423 | 72.6% | 1 | victim | grj baseline |
| B5d | (prev) | 115.05% | 1 | 7 | eager comm/step,no prune |
| B7 | 20260728_185909 | 47.67% | 0 | 5(mis) | B6 code lazy+prune, max/20 mis-localize, crash@146 |
| **B8** | ${PARENT_RUN_ID} | **${HEADLINE}%** | **${DENSE}** | **${CULPRIT}** | B7 code + avg/100/90s/600s gates on grj-w0 |
MD

echo "[pr2-b8] ${VERDICT} headline=${HEADLINE}% dense=${DENSE} culprit_rows=${CULPRIT_ROWS} culprit=${CULPRIT} SET_DG=${SET_DG}"
echo "$PARENT_RUN_ID"
