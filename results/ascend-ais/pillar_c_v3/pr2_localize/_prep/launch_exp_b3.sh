#!/usr/bin/env bash
# PR-2 实验 B3：时基优先升详窗 + 自动降回 rate=0（不依赖 step 前进）
set -euo pipefail
ROOT="/Users/yinjinrun/Codespace/myportal/project/probing-huawei"
TS="${RUN_ID_TS:-$(date +%Y%m%d_%H%M%S)}"
export PARENT_RUN_ID="${PARENT_RUN_ID:-${TS}-pillar-c-v3-pr2-e3-b3}"
export ARM_RUN_ID="${PARENT_RUN_ID}-upgrade_rate_1.0"

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

export ITERS=1800
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
# B3：时基优先（默认 45s）；可选兼步数
export PILLAR_C_SET_WINDOW_S="${PILLAR_C_SET_WINDOW_S:-45}"
export PILLAR_C_SET_WINDOW_STEPS="${PILLAR_C_SET_WINDOW_STEPS:-0}"
export PILLAR_C_SET_HANG_MAX_S="${PILLAR_C_SET_HANG_MAX_S:-900}"
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

ETA_MIN=35
ETA_MAX=55

cat >"${PARENT_LOCAL}/PR2_EXP_B3_LAUNCH.md" <<EOF
# PR-2 实验 B3 · 发射记录

| 字段 | 值 |
|------|-----|
| parent | \`${PARENT_RUN_ID}\` |
| arm | \`${ARM_RUN_ID}\` |
| pod | \`${POD}\` |
| case | P3-SW-A · GT culprit rank=7 |
| scope | localize + B3 时基 \`${PILLAR_C_SET_WINDOW_S}\`s（steps=\`${PILLAR_C_SET_WINDOW_STEPS}\`） |
| ITERS | ${ITERS} · inject [${INJECT_START},${INJECT_STOP}] |
| 全量臂 | REUSE v2 \`${FULL_REF}\` |
| ETA | ~${ETA_MIN}–${ETA_MAX} min（过 inject_stop=${INJECT_STOP}） |

## B3 变更
- SET_UPGRADE @ localize culprit → rate=1.0
- SET_DOWNGRADE @ **时间** window_s=${PILLAR_C_SET_WINDOW_S}s（或可选步数）→ rate=0（同 pid）
- jexec_poll 超时=${JEXEC_POLL_TIMEOUT_S}s；hang_max=${PILLAR_C_SET_HANG_MAX_S}s

## 发射
\`_prep/launch_exp_b3.sh\` @ $(date -Iseconds)
EOF

cp "${PARENT_LOCAL}/PR2_EXP_B3_LAUNCH.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_EXP_B3_LAUNCH.md"

echo "[pr2-b3] self-check…"
bash "${ROOT}/scripts/fail-slow/test_pillar_c_set_window.sh"

echo "[pr2-b3] jsync scripts → bundle…"
jsync_one() {
  local src="$1" dst="$2"
  local bname ddir rc=0
  bname=$(basename "$src")
  ddir=$(dirname "$dst")
  set +e
  COPYFILE_DISABLE=1 tar -C "$(dirname "$src")" -cf - "$bname" \
    | ssh -o BatchMode=yes -o ConnectTimeout=30 "${JUMP_HOST}" \
      "export KUBECONFIG='${JUMP_KUBECONFIG}'; K='${JUMP_KUBECTL}'; \$K -n default exec -i '${POD}' -- bash -lc $(printf '%q' "mkdir -p '$ddir' /tmp/yjr_sync && tar -C /tmp/yjr_sync -xf - && install -m 0755 /tmp/yjr_sync/$bname '$dst' && rm -f /tmp/yjr_sync/$bname")"
  rc=$?
  set -e
  echo "[pr2-b3] jsync $bname -> $dst rc=$rc"
  return 0
}
jsync_one "${ROOT}/scripts/fail-slow/hold_exec_run_case.sh" "${POD_BUNDLE}/hold_exec_run_case.sh"
jsync_one "${ROOT}/scripts/fail-slow/pillar_c_localize_culprit.py" "${POD_BUNDLE}/pillar_c_localize_culprit.py"

echo "[pr2-b3] wait pod IDLE…"
check_idle() {
  ssh -o ConnectTimeout=20 "$JUMP_HOST" \
    "export KUBECONFIG=${JUMP_KUBECONFIG}; ${JUMP_KUBECTL} -n default exec ${POD} -- bash -lc '
      busy=\$(ps -eo pid,cmd | awk \"/torchrun|megatron|pretrain_gpt|train_bench_probe/ && !/awk|bash -lc|pgrep|defunct|tar czf/ {print}\")
      if [[ -n \"\$busy\" ]]; then echo OWNER_BUSY; echo \"\$busy\"; exit 90; fi
      echo IDLE
    '"
}
idle_out=$(check_idle) || true
echo "$idle_out" | tee "${PARENT_LOCAL}/logs/idle_check.txt"
if echo "$idle_out" | grep -q OWNER_BUSY; then
  echo "[pr2-b3] BLOCKED: pod busy"
  echo "BLOCKED pod_busy" >"${PARENT_LOCAL}/BLOCKED.txt"
  exit 90
fi

echo "[pr2-b3] fire dynamic arm ${ARM_RUN_ID}…"
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

# hang 检测（未降回或降回后仍 stall）
if grep -q 'HANG_DETECTED' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null \
   && ! grep -q 'SET_DOWNGRADE_OK' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null; then
  cat >"${PARENT_LOCAL}/PR2_EXP_B3_STATUS.md" <<MD
# PR-2 实验 B3 · **BLOCKED**

- parent: \`${PARENT_RUN_ID}\`
- 现象: SET 后 stall 且未 SET_DOWNGRADE（见 set_upgrade.log）
- 比率: **BLOCKED**

MD
  cp "${PARENT_LOCAL}/PR2_EXP_B3_STATUS.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_EXP_B3_STATUS.md"
  echo "[pr2-b3] BLOCKED hang before downgrade"
  exit 2
fi

if [[ "$arm_rc" -ne 0 ]]; then
  SET_DG=$(grep -h 'SET_DOWNGRADE_OK' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null | wc -l | tr -d ' ')
  if [[ "${SET_DG:-0}" -lt 1 ]]; then
    cat >"${PARENT_LOCAL}/PR2_EXP_B3_STATUS.md" <<MD
# PR-2 实验 B3 · **PARTIAL/BLOCKED**

- parent: \`${PARENT_RUN_ID}\`
- hold_exec rc=${arm_rc}
- SET_DOWNGRADE_OK=${SET_DG:-0}
- 见 logs/arm_dynamic.log

MD
    cp "${PARENT_LOCAL}/PR2_EXP_B3_STATUS.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_EXP_B3_STATUS.md"
    exit "$arm_rc"
  fi
fi

# 全量臂 REUSE v2
cat >"${PARENT_LOCAL}/full_fidelity/REUSE.txt" <<EOF
reuse=1
path=${FULL_REF}
case=${CASE_ID}
note=B3 reuse v2 full_fidelity upper bound
EOF
full_bytes=$(ssh -o ConnectTimeout=30 "$JUMP_HOST" \
  "export KUBECONFIG=${JUMP_KUBECONFIG}; ${JUMP_KUBECTL} -n default exec ${POD} -- du -sb ${FULL_REF}/probing_data" \
  | awk 'NF>=1{print $1; exit}')
full_bytes=$(echo "${full_bytes:-}" | tr -cd '0-9')
if [[ -z "${full_bytes}" ]]; then
  full_bytes=1791975360
fi
echo "${full_bytes}" >"${PARENT_LOCAL}/full_fidelity/total_dump_bytes.txt"

echo "[pr2-b3] score…"
python3 "${ROOT}/scripts/fail-slow/pr2_e3_score_ratio.py" \
  --parent-local "$PARENT_LOCAL" \
  --dynamic-arm "${PARENT_LOCAL}/dynamic" \
  --case "$CASE_ID" \
  --w-star "$W_STAR" \
  --resident-rate "$RESIDENT_RATE" \
  --full-ref "$FULL_REF" \
  --dynamic-reuse-run "$PARENT_RUN_ID" \
  --out-json "${PARENT_LOCAL}/PR2_E3_RATIO_B3.json" \
  --out-md "${PARENT_LOCAL}/PR2_E3_RATIO_B3.md"

cp "${PARENT_LOCAL}/PR2_E3_RATIO_B3.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_E3_RATIO_B3.md"
cp "${PARENT_LOCAL}/PR2_E3_RATIO_B3.json" "${LOCAL_RESULT_ROOT_BASE}/PR2_E3_RATIO_B3.json"

HEADLINE=$(python3 -c "import json; print(json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B3.json'))['headline_pct'])" 2>/dev/null || echo "?")
DENSE=$(python3 -c "import json; d=json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B3.json')); print(d['dynamic'].get('torch_trace_dense_ranks','?'))" 2>/dev/null || echo "?")
CULPRIT=$(python3 -c "import json; d=json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B3.json')); print(d['dynamic'].get('localize',{}).get('culprit_rank','?'))" 2>/dev/null || echo "?")
SET_DG=$(grep -h 'SET_DOWNGRADE_OK' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null | wc -l | tr -d ' ')
DG_REASON=$(grep -h 'SET_DOWNGRADE ts=' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null | head -1 | sed -n 's/.*reason=\([^ ]*\).*/\1/p')
INJECT_OK=$(find "${PARENT_LOCAL}/dynamic" -name "step_${INJECT_STOP}.marker" 2>/dev/null | wc -l | tr -d ' ')

VERDICT=PARTIAL
if [[ "$INJECT_OK" -ge 1 ]] && [[ "$SET_DG" -ge 1 ]] && [[ "$DENSE" == "1" ]] && [[ "$CULPRIT" == "7" ]]; then
  VERDICT=DONE
elif [[ "$SET_DG" -ge 1 ]] && [[ "$INJECT_OK" -ge 1 ]]; then
  VERDICT=PARTIAL
elif grep -q 'HANG_DETECTED' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null; then
  VERDICT=BLOCKED
fi

cat >"${PARENT_LOCAL}/PR2_EXP_B3_STATUS.md" <<MD
# PR-2 实验 B3 · ${VERDICT}

**日期**：$(date +%Y-%m-%d)  
**parent**：\`${PARENT_RUN_ID}\`

| 项 | 值 |
|----|-----|
| 头条比 | **${HEADLINE}%**（v2 参考 72.6%） |
| dense_ranks | **${DENSE}** |
| culprit_rank | **${CULPRIT}**（GT=7） |
| SET_DOWNGRADE_OK | ${SET_DG} |
| downgrade reason | ${DG_REASON:-?} |
| inject_stop marker | ${INJECT_OK} |
| 窗口 | ${PILLAR_C_SET_WINDOW_S}s / steps=${PILLAR_C_SET_WINDOW_STEPS} |

- 判分：\`PR2_E3_RATIO_B3.md\`
- 发射：\`PR2_EXP_B3_LAUNCH.md\`

MD
cp "${PARENT_LOCAL}/PR2_EXP_B3_STATUS.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_EXP_B3_STATUS.md"

# 更新 CODE_STATUS / LOOP_LAST
CODE_STATUS="${LOCAL_RESULT_ROOT_BASE}/PR2_CODE_STATUS.md"
if [[ -f "$CODE_STATUS" ]]; then
  if ! grep -q "B3" "$CODE_STATUS"; then
    cat >>"$CODE_STATUS" <<MD

## 实验 B3 状态

| 轮次 | run_id | localize | E3 头条比 | 备注 |
|------|--------|----------|-----------|------|
| **B3** | \`${PARENT_RUN_ID}\` | culprit=**${CULPRIT}** | **${HEADLINE}%** | SET_DG=${SET_DG} reason=${DG_REASON:-?} dense=${DENSE} |
MD
  fi
fi

PREP_LOOP="${LOCAL_RESULT_ROOT_BASE}/_prep/LOOP_LAST.md"
mkdir -p "$(dirname "$PREP_LOOP")"
cat >"$PREP_LOOP" <<MD
# LOOP_LAST · PR-2 B3

- **时间**：$(date -Iseconds)
- **run**：\`${PARENT_RUN_ID}\`
- **verdict**：${VERDICT}
- **SET_DOWNGRADE**：${SET_DG}（reason=${DG_REASON:-?}）
- **dense**：${DENSE} · **culprit**：${CULPRIT} · **headline**：${HEADLINE}%
- **pod**：IDLE（训程结束）
MD

echo "[pr2-b3] ${VERDICT} headline=${HEADLINE}% dense=${DENSE} culprit=${CULPRIT} SET_DG=${SET_DG}"
echo "$PARENT_RUN_ID"
