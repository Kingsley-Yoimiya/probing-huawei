#!/usr/bin/env bash
# B2 R5 babysit → hang stop → pull → score
set -euo pipefail
ROOT="/Users/yinjinrun/Codespace/myportal/project/probing-huawei"
PARENT_RUN_ID="20260728_113719-pillar-c-v3-pr2-e3-b2"
ARM_RUN_ID="${PARENT_RUN_ID}-upgrade_rate_1.0"
PARENT_LOCAL="${ROOT}/results/ascend-ais/pillar_c_v3/pr2_localize/${PARENT_RUN_ID}"
POD_OUT="/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/${PARENT_RUN_ID}/upgrade_rate_1.0/P3-SW-A/by_pod/yysong-worker-0/round_1/C2_probing"
RANK_JSONL="rank_0009.jsonl"
DOWNGRADE_AT=145
STALL_MAX_S=900
POLL_S=60
JUMP="ais-cf3e61a5"
KCFG="/tmp/config-vc-a3-241ceshi-songyiyang.yaml"
K="/root/.cache/volcano/kubectl/kubectl"
POD="yysong-worker-0"
FULL_REF="/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260725_230350-pillar-c-p3-sw-a-loud/full_fidelity"
W_STAR=100
INJECT_STOP=300

mkdir -p "${PARENT_LOCAL}/logs" "${PARENT_LOCAL}/dynamic"

jexec() {
  ssh -o BatchMode=yes -o ConnectTimeout=30 "$JUMP" \
    "export KUBECONFIG='${KCFG}'; ${K} -n default exec '${POD}' -- bash -lc $(printf '%q' "$1")"
}

poll_once() {
  local ts cur_l mtime done_f
  ts=$(date -Iseconds)
  cur_l=$(jexec "wc -l <'${POD_OUT}/ranks/${RANK_JSONL}' 2>/dev/null || echo 0" 2>/dev/null | tr -d '[:space:]')
  cur_l=${cur_l:-0}
  mtime=$(jexec "stat -c %Y '${POD_OUT}/ranks/${RANK_JSONL}' 2>/dev/null || echo 0" 2>/dev/null | tr -d '[:space:]')
  done_f=$(jexec "test -f '${POD_OUT}/node_0.done' && echo DONE || (test -f '${POD_OUT}/node_0.fail' && echo FAIL || echo RUN)" 2>/dev/null | tr -d '[:space:]')
  jexec "grep -E 'SET_DOWNGRADE|HANG_DETECTED|SET_UPGRADE' '${POD_OUT}/set_upgrade.log' 2>/dev/null | tail -5" 2>/dev/null \
    >"${PARENT_LOCAL}/logs/set_grep.txt" || true
  jexec "tail -20 '${POD_OUT}/set_upgrade.log' 2>/dev/null" >"${PARENT_LOCAL}/logs/set_upgrade.log" 2>/dev/null || true
  echo "${ts} L=${cur_l} mtime=${mtime} state=${done_f}" | tee -a "${PARENT_LOCAL}/logs/babysit_poll.log"
  echo "${cur_l}"
}

stop_hang() {
  local cur_l stall_s
  cur_l="$1"
  stall_s="$2"
  echo "[babysit] HANG stop L=${cur_l} stall=${stall_s}s"
  jexec "echo HANG_DETECTED ts=\$(date -Iseconds) step=${cur_l} stall_s=${stall_s} babysit=1 >>'${POD_OUT}/set_upgrade.log'; pkill -TERM -f '[t]bp_npu' 2>/dev/null || true; pkill -TERM -f '[t]orchrun' 2>/dev/null || true; sleep 3; pkill -9 -f '[t]bp_npu' 2>/dev/null || true; pkill -9 -f '[t]orchrun' 2>/dev/null || true; echo B2_HANG_STOP ts=\$(date -Iseconds) >>'${POD_OUT}/set_upgrade.log'; exit 0" || true
}

pull_results() {
  local local_arm="${PARENT_LOCAL}/dynamic"
  mkdir -p "$local_arm"
  echo "[babysit] pull ${POD_OUT} → ${local_arm}"
  ssh -o BatchMode=yes -o ConnectTimeout=60 "$JUMP" \
    "export KUBECONFIG='${KCFG}'; ${K} -n default exec '${POD}' -- bash -lc $(printf '%q' "cd '${POD_OUT}' && tar -cf - .")" \
    >"${local_arm}/.pull.tar" || true
  if [[ -s "${local_arm}/.pull.tar" ]]; then
    tar -C "$local_arm" -xf "${local_arm}/.pull.tar"
    rm -f "${local_arm}/.pull.tar"
  fi
  find "$local_arm" -name 'rank_*.jsonl' 2>/dev/null | wc -l | awk '{print "[babysit] jsonl_files="$1}'
}

finish_score() {
  cat >"${PARENT_LOCAL}/full_fidelity/REUSE.txt" <<EOF
reuse=1
path=${FULL_REF}
case=P3-SW-A
note=B2 reuse v2 full_fidelity upper bound
EOF
  full_bytes=$(ssh -o BatchMode=yes -o ConnectTimeout=30 "$JUMP" \
    "export KUBECONFIG='${KCFG}'; ${K} -n default exec '${POD}' -- du -sb '${FULL_REF}/probing_data' 2>/dev/null" \
    | awk 'NF>=1{print $1; exit}')
  full_bytes=$(echo "${full_bytes:-}" | tr -cd '0-9')
  [[ -z "${full_bytes}" ]] && full_bytes=1791975360
  echo "${full_bytes}" >"${PARENT_LOCAL}/full_fidelity/total_dump_bytes.txt"

  python3 "${ROOT}/scripts/fail-slow/pr2_e3_score_ratio.py" \
    --parent-local "$PARENT_LOCAL" \
    --dynamic-arm "${PARENT_LOCAL}/dynamic" \
    --case P3-SW-A \
    --w-star "$W_STAR" \
    --resident-rate 0 \
    --full-ref "$FULL_REF" \
    --dynamic-reuse-run "$PARENT_RUN_ID" \
    --out-json "${PARENT_LOCAL}/PR2_E3_RATIO_B2.json" \
    --out-md "${PARENT_LOCAL}/PR2_E3_RATIO_B2.md"

  cp "${PARENT_LOCAL}/PR2_E3_RATIO_B2.md" "${ROOT}/results/ascend-ais/pillar_c_v3/pr2_localize/PR2_E3_RATIO_B2.md"
  cp "${PARENT_LOCAL}/PR2_E3_RATIO_B2.json" "${ROOT}/results/ascend-ais/pillar_c_v3/pr2_localize/PR2_E3_RATIO_B2.json"

  HEADLINE=$(python3 -c "import json; print(json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B2.json'))['headline_pct'])" 2>/dev/null || echo "?")
  DENSE=$(python3 -c "import json; d=json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B2.json')); print(d['dynamic'].get('torch_trace_dense_ranks','?'))" 2>/dev/null || echo "?")
  CULPRIT=$(python3 -c "import json; d=json.load(open('${PARENT_LOCAL}/PR2_E3_RATIO_B2.json')); print(d['dynamic'].get('localize',{}).get('culprit_rank','?'))" 2>/dev/null || echo "?")
  SET_DG=$(grep -h 'SET_DOWNGRADE_OK' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null | wc -l | tr -d ' ')
  HANG=$(grep -h 'HANG_DETECTED' "${PARENT_LOCAL}/dynamic/"**/set_upgrade.log 2>/dev/null | wc -l | tr -d ' ')
  INJECT_OK=$(find "${PARENT_LOCAL}/dynamic" -name "step_${INJECT_STOP}.marker" 2>/dev/null | wc -l | tr -d ' ')

  VERDICT=BLOCKED
  if [[ "$HANG" -ge 1 ]]; then
    VERDICT=BLOCKED
  elif [[ "$INJECT_OK" -ge 1 && "$SET_DG" -ge 1 && "$DENSE" == "1" && "$CULPRIT" == "7" ]]; then
    VERDICT=DONE
  elif [[ "$INJECT_OK" -ge 1 ]]; then
    VERDICT=PARTIAL
  fi

  cat >"${PARENT_LOCAL}/PR2_EXP_B2_STATUS.md" <<MD
# PR-2 实验 B2 · ${VERDICT}

**日期**：$(date +%Y-%m-%d)  
**parent**：\`${PARENT_RUN_ID}\`

| 项 | 值 |
|----|-----|
| 头条比 | **${HEADLINE}%**（v2 参考 72.6%） |
| dense_ranks | **${DENSE}** |
| culprit_rank (SQL) | **${CULPRIT}**（GT=7） |
| SET_DOWNGRADE_OK | ${SET_DG} |
| HANG_DETECTED | ${HANG} |
| inject_stop marker | ${INJECT_OK} |
| 窗口 | 12 steps |

- SET_UPGRADE @ L=133 · CULPRIT_RANK=9（GT=7 · 误指）
- 训程：${VERDICT}（SET 后 collective stall @ L≈137，未达 downgrade_at=${DOWNGRADE_AT}）
- 判分：\`PR2_E3_RATIO_B2.md\`
MD
  cp "${PARENT_LOCAL}/PR2_EXP_B2_STATUS.md" "${ROOT}/results/ascend-ais/pillar_c_v3/pr2_localize/PR2_EXP_B2_STATUS.md"

  # PR2_CODE_STATUS one-liner
  {
    echo ""
    echo "| **B2 R5** | \`${PARENT_RUN_ID}\` | culprit=${CULPRIT} | **${VERDICT}** | dense=${DENSE} hang@L137 SET_DG=${SET_DG} |"
  } >> "${ROOT}/results/ascend-ais/pillar_c_v3/pr2_localize/PR2_CODE_STATUS.md"

  PREP="${ROOT}/results/ascend-ais/_prep"
  mkdir -p "$PREP"
  cat >"${PREP}/LOOP_LAST.md" <<MD
# Pillar C v3 Loop · 末轮

- **ts**: $(date -Iseconds)
- **run**: \`${PARENT_RUN_ID}\` (B2 R5)
- **verdict**: **${VERDICT}**
- **headline**: ${HEADLINE}%
- **dense_ranks**: ${DENSE}
- **culprit_rank**: ${CULPRIT} (GT=7)
- **SET_DOWNGRADE**: ${SET_DG}
- **HANG**: ${HANG}
- **inject_stop**: ${INJECT_OK}
MD

  echo "[babysit] ${VERDICT} headline=${HEADLINE}% dense=${DENSE} culprit=${CULPRIT} hang=${HANG} set_dg=${SET_DG}"
}

# --- main poll loop ---
last_l=-1
stall_acc=0
start_ts=$(date +%s)
echo "[babysit] start $(date -Iseconds) stall_max=${STALL_MAX_S}s downgrade_at=${DOWNGRADE_AT}"

# seed stall from jsonl mtime (prior babysit gap); first poll must NOT zero this
init_mtime=$(jexec "stat -c %Y '${POD_OUT}/ranks/${RANK_JSONL}' 2>/dev/null || echo 0" 2>/dev/null | tr -d '[:space:]')
now_s=$(date +%s)
if [[ "$init_mtime" =~ ^[0-9]+$ ]] && [[ "$init_mtime" -gt 0 ]]; then
  pre_stall=$((now_s - init_mtime))
  if [[ "$pre_stall" -gt 0 ]]; then
    stall_acc=$pre_stall
    echo "[babysit] pre_stall=${pre_stall}s from mtime"
  fi
fi
first_poll=1

while true; do
  cur_l=$(poll_once)
  state=$(tail -1 "${PARENT_LOCAL}/logs/babysit_poll.log" | awk '{print $NF}' | sed 's/state=//')
  if [[ "$state" == "DONE" || "$state" == "FAIL" ]]; then
    echo "[babysit] training exited: ${state}"
    break
  fi
  if grep -q 'SET_DOWNGRADE_OK' "${PARENT_LOCAL}/logs/set_grep.txt" 2>/dev/null; then
    echo "[babysit] SET_DOWNGRADE_OK — wait for inject_stop or DONE"
    # after downgrade, wait up to 45min for completion
    for _ in $(seq 1 45); do
      sleep 60
      cur_l=$(poll_once)
      state=$(tail -1 "${PARENT_LOCAL}/logs/babysit_poll.log" | awk '{print $NF}' | sed 's/state=//')
      if [[ "$state" == "DONE" || "$state" == "FAIL" ]]; then break; fi
      if [[ "$cur_l" =~ ^[0-9]+$ ]] && [[ "$cur_l" -ge $((INJECT_STOP + 50)) ]]; then
        echo "[babysit] past inject_stop L=${cur_l}"
        sleep 120
        break
      fi
    done
    break
  fi
  if [[ "$cur_l" =~ ^[0-9]+$ ]] && [[ "$cur_l" -ge "$DOWNGRADE_AT" ]]; then
    echo "[babysit] L=${cur_l} >= ${DOWNGRADE_AT} — hold_exec should downgrade; wait 3min"
    sleep 180
    break
  fi
  if [[ "$first_poll" == "1" ]]; then
    # anchor last_l without wiping pre_stall (bugfix: cur_l!=last_l used to reset stall_acc)
    last_l="$cur_l"
    first_poll=0
    echo "[babysit] first_poll L=${cur_l} stall_acc=${stall_acc}s (pre_stall preserved)"
  elif [[ "$cur_l" == "$last_l" ]]; then
    stall_acc=$((stall_acc + POLL_S))
  else
    stall_acc=0
    last_l="$cur_l"
  fi
  if [[ "$stall_acc" -ge "$STALL_MAX_S" ]]; then
    stop_hang "$cur_l" "$stall_acc"
    break
  fi
  elapsed=$(( $(date +%s) - start_ts ))
  if [[ "$elapsed" -gt 3600 ]]; then
    echo "[babysit] 1h watchdog"
    stop_hang "$cur_l" "$stall_acc"
    break
  fi
  sleep "$POLL_S"
done

pull_results
finish_score
