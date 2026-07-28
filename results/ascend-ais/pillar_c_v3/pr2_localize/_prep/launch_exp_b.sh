#!/usr/bin/env bash
# PR-2 实验 B：数据量比重算（动态臂复用 A6 localize + 全量臂复用 v2）
set -euo pipefail
ROOT="/Users/yinjinrun/Codespace/myportal/project/probing-huawei"
TS="${RUN_ID_TS:-$(date +%Y%m%d_%H%M%S)}"
export PARENT_RUN_ID="${PARENT_RUN_ID:-${TS}-pillar-c-v3-pr2-e3-b}"

A6_RUN_ID="${A6_RUN_ID:-20260728_102830-pillar-c-v3-pr2-localize-a6}"
A6_ARM_DIR="${A6_ARM_DIR:-upgrade_rate_1.0}"
FULL_REF="${FULL_REF:-/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260725_230350-pillar-c-p3-sw-a-loud/full_fidelity}"
REUSE_FULL="${REUSE_FULL:-1}"
RUN_FULL_ARM="${RUN_FULL_ARM:-0}"
WAIT_IDLE_MAX_S="${WAIT_IDLE_MAX_S:-7200}"
POLL_S="${POLL_S:-30}"

export FS_SHARED_SCRIPTS="/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow"
export LOCAL_RESULT_ROOT_BASE="${ROOT}/results/ascend-ais/pillar_c_v3/pr2_localize"
export OUT_FAMILY=pillar_c_v3
export POD=yysong-worker-0
export CASE_ID=P3-SW-A
export W_STAR=100
export RESIDENT_RATE=0

JUMP_KUBECTL="/root/.cache/volcano/kubectl/kubectl"
JUMP_KUBECONFIG="/tmp/config-vc-a3-241ceshi-songyiyang.yaml"
JUMP_HOST="ais-cf3e61a5"

PARENT_LOCAL="${LOCAL_RESULT_ROOT_BASE}/${PARENT_RUN_ID}"
mkdir -p "${PARENT_LOCAL}/logs" "${PARENT_LOCAL}/full_fidelity"
echo "${PARENT_RUN_ID}" >"${PARENT_LOCAL}/PARENT_RUN_ID.txt"

cat >"${PARENT_LOCAL}/PR2_EXP_B_LAUNCH.md" <<EOF
# PR-2 实验 B · 发射记录

- **parent**：\`${PARENT_RUN_ID}\`
- **动态臂复用**：\`${A6_RUN_ID}/${A6_ARM_DIR}\`（A6 localize PASS · culprit=7）
- **全量臂**：\`${FULL_REF}\`（reuse=${REUSE_FULL}）
- **语义**：编排层 SQL 定位 + \`PILLAR_C_SET_SCOPE=localize\` 仅 culprit 升详
- **发射**：\`_prep/launch_exp_b.sh\`
EOF

check_idle() {
  ssh -o ConnectTimeout=20 "$JUMP_HOST" \
    "export KUBECONFIG=${JUMP_KUBECONFIG}; ${JUMP_KUBECTL} -n default exec ${POD} -- bash -lc '
      busy=\$(ps -eo pid,cmd | awk \"/torchrun|megatron|pretrain_gpt|train_bench_probe/ && !/awk|bash -lc|pgrep|defunct|tar czf/ {print}\")
      if [[ -n \"\$busy\" ]]; then echo OWNER_BUSY; echo \"\$busy\"; exit 90; fi
      echo IDLE
    '"
}

echo "[pr2-b] wait pod IDLE (max ${WAIT_IDLE_MAX_S}s) …"
deadline=$((SECONDS + WAIT_IDLE_MAX_S))
while (( SECONDS < deadline )); do
  idle_out=$(check_idle) || true
  echo "$idle_out" | tee "${PARENT_LOCAL}/logs/idle_poll.txt"
  if ! echo "$idle_out" | grep -q OWNER_BUSY; then
    break
  fi
  sleep "$POLL_S"
done
if echo "$(cat "${PARENT_LOCAL}/logs/idle_poll.txt")" | grep -q OWNER_BUSY; then
  echo "[pr2-b] BLOCKED: pod still busy"
  echo "BLOCKED idle_timeout" >"${PARENT_LOCAL}/BLOCKED.txt"
  exit 90
fi

# ---- 动态臂：从 A6 AFS 拉回 ----
AFS_DYN="/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/${A6_RUN_ID}/${A6_ARM_DIR}"
LOCAL_DYN="${PARENT_LOCAL}/dynamic"
mkdir -p "$LOCAL_DYN"
echo "[pr2-b] pull dynamic ${AFS_DYN}"
ssh -o ConnectTimeout=60 "$JUMP_HOST" \
  "export KUBECONFIG=${JUMP_KUBECONFIG}; ${JUMP_KUBECTL} -n default exec ${POD} -- bash -lc 'cd \"${AFS_DYN}\" && tar -cf - .'" \
  | tar -C "$LOCAL_DYN" -xf -
echo "${AFS_DYN}" >"${LOCAL_DYN}/REUSE_A6.txt"
echo "${A6_RUN_ID}" >"${PARENT_LOCAL}/dynamic_reuse_run.txt"

# 关键日志（case 子树）
CASE_OUT="${AFS_DYN}/P3-SW-A/by_pod/${POD}/round_1/C2_probing"
ssh -o ConnectTimeout=30 "$JUMP_HOST" \
  "export KUBECONFIG=${JUMP_KUBECONFIG}; ${JUMP_KUBECTL} -n default exec ${POD} -- bash -lc '
    for f in set_upgrade.log localize.log probing/dump.log volume_final.txt query_p3sw_rss_window.txt; do
      src=\"${CASE_OUT}/\$f\"
      test -f \"\$src\" && cat \"\$src\"
    done
  '" >"${LOCAL_DYN}/a6_key_logs_merged.txt" 2>/dev/null || true
for f in set_upgrade.log localize.log; do
  ssh -o ConnectTimeout=30 "$JUMP_HOST" \
    "export KUBECONFIG=${JUMP_KUBECONFIG}; ${JUMP_KUBECTL} -n default exec ${POD} -- cat ${CASE_OUT}/${f}" \
    >"${LOCAL_DYN}/${f}" 2>/dev/null || true
done

# ---- 全量臂 ----
if [[ "$RUN_FULL_ARM" == "1" ]]; then
  export ARM=full_fidelity
  export ARM_RUN_ID="${PARENT_RUN_ID}-full_fidelity"
  export ITERS=1800
  export INJECT_START=100
  export INJECT_STOP=300
  cd "${ROOT}"
  bash scripts/fail-slow/run_pillar_c_arm.sh 2>&1 | tee "${PARENT_LOCAL}/logs/arm_full.log"
elif [[ "$REUSE_FULL" == "1" ]]; then
  cat >"${PARENT_LOCAL}/full_fidelity/REUSE.txt" <<EOF
reuse=1
path=${FULL_REF}
case=${CASE_ID}
note=训/注入配方与 Loud P3-SW-A 一致；SAMPLE_MS=50 rate=1.0 作上界锚点
EOF
  full_bytes=$(ssh -o ConnectTimeout=30 "$JUMP_HOST" \
    "export KUBECONFIG=${JUMP_KUBECONFIG}; ${JUMP_KUBECTL} -n default exec ${POD} -- du -sb ${FULL_REF}/probing_data" \
    | awk 'NF>=1{print $1; exit}')
  full_bytes=$(echo "${full_bytes:-}" | tr -cd '0-9')
  if [[ -z "${full_bytes}" ]]; then
    full_bytes=1791975360
    echo "[pr2-b] WARN: du fallback ${full_bytes}" | tee "${PARENT_LOCAL}/logs/full_reuse.txt"
  fi
  echo "${full_bytes}" >"${PARENT_LOCAL}/full_fidelity/total_dump_bytes.txt"
fi

# ---- 判分 ----
python3 "${ROOT}/scripts/fail-slow/pr2_e3_score_ratio.py" \
  --parent-local "$PARENT_LOCAL" \
  --dynamic-arm "$LOCAL_DYN" \
  --case "$CASE_ID" \
  --w-star "$W_STAR" \
  --resident-rate "$RESIDENT_RATE" \
  --full-ref "$FULL_REF" \
  --dynamic-reuse-run "$A6_RUN_ID" \
  --out-json "${PARENT_LOCAL}/PR2_E3_RATIO.json" \
  --out-md "${PARENT_LOCAL}/PR2_E3_RATIO.md"

cp "${PARENT_LOCAL}/PR2_E3_RATIO.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_E3_RATIO.md"
cp "${PARENT_LOCAL}/PR2_E3_RATIO.json" "${LOCAL_RESULT_ROOT_BASE}/PR2_E3_RATIO.json"

echo "[pr2-b] DONE parent=${PARENT_RUN_ID}"
echo "$PARENT_RUN_ID"
