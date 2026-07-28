#!/usr/bin/env bash
# PR-2 实验 B7：B6 code(lazy comm/table + prune_extra_pids) 长跑 · WINDOW_S=15 · hang_max=480s
set -euo pipefail
ROOT="/Users/yinjinrun/Codespace/myportal/project/probing-huawei"
TS="${RUN_ID_TS:-$(date +%Y%m%d_%H%M%S)}"
export PARENT_RUN_ID="${PARENT_RUN_ID:-${TS}-pillar-c-v3-pr2-e3-b7}"
export ARM_RUN_ID="${PARENT_RUN_ID}-upgrade_rate_1.0"

# B6 gates（显式设默认;必须传到 hold_exec_run_case → torchrun env）
export PROBING_TORCH_COMM_COLLECTIVE_LAZY="${PROBING_TORCH_COMM_COLLECTIVE_LAZY:-1}"
export PROBING_TORCH_STEP_TIMING_LAZY="${PROBING_TORCH_STEP_TIMING_LAZY:-0}"
export PILLAR_C_PRUNE_EXTRA_PIDS="${PILLAR_C_PRUNE_EXTRA_PIDS:-1}"
export PILLAR_C_PRUNE_DRY_RUN="${PILLAR_C_PRUNE_DRY_RUN:-0}"

export FS_SHARED_SCRIPTS="/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow"
export FS_PLATFORM_ASCEND="${FS_SHARED_SCRIPTS}/platform/ascend"
export LOCAL_RESULT_ROOT_BASE="${ROOT}/results/ascend-ais/pillar_c_v3/pr2_localize"
unset OUT_FAMILY
export POD_BUNDLE="/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle"

export CASE_ID=P3-SW-A
export DOSE=loud
export PHASE=pilot
export POD=yysong-worker-0
export NPROC=16
export NNODES=1
export SIDECAR_LOCAL_RANK=7

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

cat >"${PARENT_LOCAL}/PR2_EXP_B7_LAUNCH.md" <<EOF
# PR-2 实验 B7 · 发射记录

| 字段 | 值 |
|------|-----|
| parent | \`${PARENT_RUN_ID}\` |
| arm | \`${ARM_RUN_ID}\` |
| pod | \`${POD}\` |
| case | P3-SW-A · GT culprit rank=7 |
| 前置 | B6 code(lazy comm/table + prune) 已部署 pod;B5d 头条=115.05%,预期 B7 ~40-50% |
| scope | localize + **15s** 时基降回 |
| hang_max | **480s**（SET 后 step ≥8min 不动 → stop_hang） |
| 常驻 | \`on,rate=0\` |
| ITERS | ${ITERS} · inject [${INJECT_START},${INJECT_STOP}] |
| 全量臂 | REUSE v2 \`${FULL_REF}\` |
| B6 gates | COMM_LAZY=${PROBING_TORCH_COMM_COLLECTIVE_LAZY} · STEP_TIMING_LAZY=${PROBING_TORCH_STEP_TIMING_LAZY} · PRUNE_EXTRA_PIDS=${PILLAR_C_PRUNE_EXTRA_PIDS} · DRY=${PILLAR_C_PRUNE_DRY_RUN} |

## 发射
\`_prep/launch_exp_b7.sh\` @ $(date -Iseconds)
EOF
cp "${PARENT_LOCAL}/PR2_EXP_B7_LAUNCH.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_EXP_B7_LAUNCH.md"

echo "[pr2-b7] self-check test_pillar_c_set_window…"
bash "${ROOT}/scripts/fail-slow/test_pillar_c_set_window.sh"

echo "[pr2-b7] ext/torch.py C0 hot-sync self-check…"
grep -q '_sync_live_tracers' "${ROOT}/python/probing/ext/torch.py"
grep -q '_last_spec' "${ROOT}/python/probing/ext/torch.py"
grep -q 'tracer.rate = config.rate' "${ROOT}/python/probing/ext/torch.py"
TORCH_LINES=$(wc -l <"${ROOT}/python/probing/ext/torch.py" | tr -d ' ')
if [[ "${TORCH_LINES}" -lt 150 ]]; then
  echo "[pr2-b7] BLOCKED: ext/torch.py too short (${TORCH_LINES} lines)"
  exit 91
fi
echo "ext/torch.py C0 OK (${TORCH_LINES} lines)"

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
  echo "[pr2-b7] jsync $bname -> $dst rc=$rc"
  return 0
}

echo "[pr2-b7] jsync scripts + torch_probe + ext/torch → bundle…"
jsync_one "${ROOT}/scripts/fail-slow/hold_exec_run_case.sh" "${POD_BUNDLE}/hold_exec_run_case.sh"
jsync_one "${ROOT}/scripts/fail-slow/pillar_c_localize_culprit.py" "${POD_BUNDLE}/pillar_c_localize_culprit.py"
jsync_one "${ROOT}/python/probing/profiling/torch_probe.py" "${POD_BUNDLE}/pydeps/probing/profiling/torch_probe.py"
jsync_one "${ROOT}/python/probing/ext/torch.py" "${POD_BUNDLE}/pydeps/probing/ext/torch.py"

echo "[pr2-b7] verify bundle ext/torch.py on pod…"
ssh -o BatchMode=yes -o ConnectTimeout=30 "${JUMP_HOST}" \
  "export KUBECONFIG='${JUMP_KUBECONFIG}'; ${JUMP_KUBECTL} -n default exec '${POD}' -- bash -lc $(printf '%q' "grep -c _sync_live_tracers ${POD_BUNDLE}/pydeps/probing/ext/torch.py && wc -l < ${POD_BUNDLE}/pydeps/probing/ext/torch.py")" \
  | tee "${PARENT_LOCAL}/logs/bundle_torch_verify.txt"

echo "[pr2-b7] wait pod IDLE…"
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
  echo "[pr2-b7] BLOCKED: pod busy"
  echo "BLOCKED pod_busy" >"${PARENT_LOCAL}/BLOCKED.txt"
  exit 90
fi

echo "[pr2-b7] fire dynamic arm ${ARM_RUN_ID}…"
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

native_dg=0
backfill=0
for slog in "${PARENT_LOCAL}"/dynamic/**/set_upgrade.log; do
  [[ -f "$slog" ]] || continue
  if grep -q 'SET_DOWNGRADE_OK' "$slog"; then
    native_dg=$((native_dg + 1))
  fi
  if grep -q 'mac_triggered_backfill' "$slog"; then
    backfill=$((backfill + 1))
  fi
done

if grep -q 'HANG_DETECTED' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null \
   && ! grep -q 'SET_DOWNGRADE_OK' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null; then
  cat >"${PARENT_LOCAL}/PR2_EXP_B7_STATUS.md" <<MD
# PR-2 实验 B7 · **BLOCKED**

- parent: \`${PARENT_RUN_ID}\`
- 现象: SET 后 stall 且未 SET_DOWNGRADE
MD
  cp "${PARENT_LOCAL}/PR2_EXP_B7_STATUS.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_EXP_B7_STATUS.md"
  exit 2
fi

cat >"${PARENT_LOCAL}/full_fidelity/REUSE.txt" <<EOF
reuse=1
path=${FULL_REF}
case=${CASE_ID}
note=B7 reuse v2 full_fidelity upper bound
EOF
full_bytes=$(ssh -o ConnectTimeout=30 "$JUMP_HOST" \
  "export KUBECONFIG=${JUMP_KUBECONFIG}; ${JUMP_KUBECTL} -n default exec ${POD} -- du -sb ${FULL_REF}/probing_data" \
  | awk 'NF>=1{print $1; exit}')
full_bytes=$(echo "${full_bytes:-}" | tr -cd '0-9')
if [[ -z "${full_bytes}" ]]; then
  full_bytes=1791975360
fi
echo "${full_bytes}" >"${PARENT_LOCAL}/full_fidelity/total_dump_bytes.txt"

echo "[pr2-b7] score…"
python3 "${ROOT}/scripts/fail-slow/pr2_e3_score_ratio.py" \
  --parent-local "$PARENT_LOCAL" \
  --dynamic-arm "${PARENT_LOCAL}/dynamic" \
  --case "$CASE_ID" \
  --w-star "$W_STAR" \
  --resident-rate "$RESIDENT_RATE" \
  --full-ref "$FULL_REF" \
  --dynamic-reuse-run "$PARENT_RUN_ID" \
  --out-json "${PARENT_LOCAL}/PR2_E3_RATIO_B7.json" \
  --out-md "${PARENT_LOCAL}/PR2_E3_RATIO_B7.md"

cp "${PARENT_LOCAL}/PR2_E3_RATIO_B7.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_E3_RATIO_B7.md"
cp "${PARENT_LOCAL}/PR2_E3_RATIO_B7.json" "${LOCAL_RESULT_ROOT_BASE}/PR2_E3_RATIO_B7.json"

HEADLINE=$(python3 -c "import json; print(json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B7.json'))['headline_pct'])" 2>/dev/null || echo "?")
DENSE=$(python3 -c "import json; d=json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B7.json')); print(d['dynamic'].get('torch_trace_dense_ranks','?'))" 2>/dev/null || echo "?")
CULPRIT=$(python3 -c "import json; d=json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B7.json')); print(d['dynamic'].get('localize',{}).get('culprit_rank','?'))" 2>/dev/null || echo "?")
CULPRIT_ROWS=$(python3 -c "
import json
d=json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B7.json'))
loc=d['dynamic'].get('localize',{})
cpid=str(loc.get('culprit_pid',''))
rows=[r for r in d['dynamic'].get('torch_trace_ranks',[]) if str(r.get('pid'))==cpid]
print(rows[0]['n_rows'] if rows else 0)
" 2>/dev/null || echo "?")
NON_CULPRIT_MAX=$(python3 -c "
import json
d=json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B7.json'))
loc=d['dynamic'].get('localize',{})
cpid=str(loc.get('culprit_pid',''))
mx=max((int(r.get('n_rows') or 0) for r in d['dynamic'].get('torch_trace_ranks',[]) if str(r.get('pid'))!=cpid), default=0)
print(mx)
" 2>/dev/null || echo "?")
SET_DG=$(grep -h 'SET_DOWNGRADE_OK' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null | wc -l | tr -d ' ')
DG_REASON=$(grep -h 'SET_DOWNGRADE ts=' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null | sed -n 's/.*reason=\([^ ]*\).*/\1/p' | head -1)
INJECT_OK=$(find "${PARENT_LOCAL}/dynamic" -name "step_${INJECT_STOP}.marker" 2>/dev/null | wc -l | tr -d ' ')

NATIVE_OK=no
if [[ "$SET_DG" -ge 1 ]] && [[ "$backfill" -eq 0 ]]; then
  NATIVE_OK=yes
fi

VERDICT=PARTIAL
HEADLINE_OK=0
if python3 -c "import json; h=float(json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B7.json'))['headline_pct']); exit(0 if h<100 else 1)" 2>/dev/null; then
  HEADLINE_OK=1
fi

if [[ "$INJECT_OK" -ge 1 ]] && [[ "$SET_DG" -ge 1 ]] && [[ "$DENSE" == "1" ]] && [[ "$CULPRIT" == "7" ]] \
   && [[ "$NATIVE_OK" == "yes" ]] && [[ "$HEADLINE_OK" -eq 1 ]] \
   && [[ "${CULPRIT_ROWS:-0}" -gt 0 ]] && [[ "${NON_CULPRIT_MAX:-99}" -eq 0 ]]; then
  VERDICT=DONE
elif [[ "$SET_DG" -ge 1 ]] && [[ "$INJECT_OK" -ge 1 ]] && [[ "$CULPRIT" == "7" ]]; then
  VERDICT=PARTIAL
elif grep -q 'HANG_DETECTED' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null; then
  VERDICT=BLOCKED
fi

cat >"${PARENT_LOCAL}/PR2_EXP_B7_STATUS.md" <<MD
# PR-2 实验 B7 · ${VERDICT}

**日期**：$(date +%Y-%m-%d)  
**parent**：\`${PARENT_RUN_ID}\`

| 项 | 值 |
|----|-----|
| 头条比 | **${HEADLINE}%**（v2 参考 72.6%；目标 <100%） |
| dense_ranks | **${DENSE}** |
| culprit_rank | **${CULPRIT}**（GT=7） |
| culprit TT rows | **${CULPRIT_ROWS}** |
| 非 culprit max rows | **${NON_CULPRIT_MAX}** |
| SET_DOWNGRADE_OK | ${SET_DG} |
| downgrade reason | ${DG_REASON:-?} |
| 原生（非 backfill） | **${NATIVE_OK}** |
| inject_stop marker | ${INJECT_OK} |
| WINDOW_S | ${PILLAR_C_SET_WINDOW_S} |
| hang_max | ${PILLAR_C_SET_HANG_MAX_S}s |
| B7 改动 | B6 code(lazy comm/table + prune_extra_pids) 全 gate 生效 · 15s 窗 · 8min hang |

- 判分：\`PR2_E3_RATIO_B7.md\`
MD
cp "${PARENT_LOCAL}/PR2_EXP_B7_STATUS.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_EXP_B7_STATUS.md"

CODE_STATUS="${LOCAL_RESULT_ROOT_BASE}/PR2_CODE_STATUS.md"
if [[ -f "$CODE_STATUS" ]]; then
  if ! grep -q "实验 B7" "$CODE_STATUS"; then
    cat >>"$CODE_STATUS" <<MD

## 实验 B7 状态（B6 code 长跑）

| 轮次 | run_id | localize | E3 头条比 | dense | culprit_rows | 备注 |
|------|--------|----------|-----------|-------|--------------|------|
| **B7** | \`${PARENT_RUN_ID}\` | culprit=**${CULPRIT}** | **${HEADLINE}%** | **${DENSE}** | **${CULPRIT_ROWS}** | WINDOW=15s hang=480s SET_DG=${SET_DG} B6-gates |
MD
  fi
fi

# NOTE: B7 sub-agent skips LOOP_LAST write; parent Loop handles it.

cat >"${LOCAL_RESULT_ROOT_BASE}/PR2_VOLUME.md" <<MD
# PR-2 数据量比（E3 头条）

| 轮次 | run_id | headline | dense | culprit | 备注 |
|------|--------|----------|-------|---------|------|
| v2 ref | 20260726_181423 | 72.6% | 1 | victim | grj baseline |
| B5d | (prev) | 115.05% | 1 | 7 | eager comm/step,no prune |
| **B7** | ${PARENT_RUN_ID} | **${HEADLINE}%** | **${DENSE}** | **${CULPRIT}** | B6 code lazy+prune |
MD

echo "[pr2-b7] ${VERDICT} headline=${HEADLINE}% dense=${DENSE} culprit_rows=${CULPRIT_ROWS} culprit=${CULPRIT} SET_DG=${SET_DG}"
echo "$PARENT_RUN_ID"
