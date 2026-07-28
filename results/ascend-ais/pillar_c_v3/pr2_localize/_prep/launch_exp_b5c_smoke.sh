#!/usr/bin/env bash
# PR-2 B5c 短测：验证 ext/torch __file__ + hot-updated + culprit TT rows>0
set -uo pipefail
ROOT="/Users/yinjinrun/Codespace/myportal/project/probing-huawei"
TS="${RUN_ID_TS:-$(date +%Y%m%d_%H%M%S)}"
export PARENT_RUN_ID="${PARENT_RUN_ID:-${TS}-pillar-c-v3-pr2-e3-b5c}"

export FS_SHARED_SCRIPTS="/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow"
export FS_PLATFORM_ASCEND="${FS_SHARED_SCRIPTS}/platform/ascend"
export LOCAL_RESULT_ROOT_BASE="${ROOT}/results/ascend-ais/pillar_c_v3/pr2_localize"
export POD_BUNDLE="/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle"
export POD_RESULTS="/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais"
export CASE_ID=P3-SW-A DOSE=loud POD=yysong-worker-0 NPROC=16 NNODES=1 SIDECAR_LOCAL_RANK=7 OUT_FAMILY=pillar_c

export ITERS=150
export WARMUP=10
export INJECT_START=9999 INJECT_STOP=9999 INJECT_KIND=none
export DUMP_PROBING_SQL=0 DUMP_WAIT_S=0
export ARM=e3a_upgrade RESIDENT_RATE=0 PILLAR_C_SET_RATE=1.0
export PROBING_TORCH_PROFILING='on,rate=0' PILLAR_C_SET_UPGRADE=0
export HOLD_EXEC_SKIP_HEAVY_JSYNC=1 PROBING_SPAN_BACKENDS=none MODE=host_bound

ARM_DIR="upgrade_rate_${PILLAR_C_SET_RATE}"
export ARM_RUN_ID="${PARENT_RUN_ID}-${ARM_DIR}"
POD_OUT="${POD_RESULTS}/${OUT_FAMILY}/${PARENT_RUN_ID}/${ARM_DIR}"
OUT_C2="${POD_OUT}/${CASE_ID}/by_pod/${POD}/round_1/C2_probing"
RANK_JSONL="${OUT_C2}/ranks/rank_0000.jsonl"

JUMP_KUBECTL="/root/.cache/volcano/kubectl/kubectl"
JUMP_KUBECONFIG="/tmp/config-vc-a3-241ceshi-songyiyang.yaml"
JUMP_HOST="ais-cf3e61a5"
PYBIN="/root/miniconda3/envs/llm_test/bin"
POD_PYDEPS="${POD_BUNDLE}/pydeps"

PARENT_LOCAL="${LOCAL_RESULT_ROOT_BASE}/${PARENT_RUN_ID}"
mkdir -p "${PARENT_LOCAL}/logs"
echo "${PARENT_RUN_ID}" >"${PARENT_LOCAL}/PARENT_RUN_ID.txt"

jexec() {
  ssh -o BatchMode=yes -o ConnectTimeout=30 "${JUMP_HOST}" \
    "export KUBECONFIG='${JUMP_KUBECONFIG}'; ${JUMP_KUBECTL} -n default exec '${POD}' -- bash -lc $(printf '%q' "$*")"
}

jsonl_lines() {
  jexec "if [[ -f '${RANK_JSONL}' ]]; then wc -l <'${RANK_JSONL}'; else echo 0; fi" 2>/dev/null | tr -d '[:space:]'
}

jsync_one() {
  local src="$1" dst="$2" bname ddir
  bname=$(basename "$src"); ddir=$(dirname "$dst")
  COPYFILE_DISABLE=1 tar -C "$(dirname "$src")" -cf - "$bname" \
    | ssh -o BatchMode=yes -o ConnectTimeout=30 "${JUMP_HOST}" \
      "export KUBECONFIG='${JUMP_KUBECONFIG}'; K='${JUMP_KUBECTL}'; \$K -n default exec -i '${POD}' -- bash -lc $(printf '%q' "mkdir -p '$ddir' /tmp/yjr_sync && tar -C /tmp/yjr_sync -xf - && install -m 0644 /tmp/yjr_sync/$bname '$dst'")"
  echo "[b5c] jsync $bname"
}

echo "[b5c] jsync…"
jsync_one "${ROOT}/python/probing/ext/torch.py" "${POD_BUNDLE}/pydeps/probing/ext/torch.py"
jsync_one "${ROOT}/python/probing/profiling/torch_probe.py" "${POD_BUNDLE}/pydeps/probing/profiling/torch_probe.py"

BUNDLE_LINES=$(jexec "wc -l <'${POD_BUNDLE}/pydeps/probing/ext/torch.py'" | tr -d '[:space:]')
BUNDLE_SYNC=$(jexec "grep -c _sync_live_tracers '${POD_BUNDLE}/pydeps/probing/ext/torch.py'" | tr -d '[:space:]')
echo "[b5c] bundle ${BUNDLE_LINES}L sync=${BUNDLE_SYNC} OUT=${OUT_C2}"

idle_out=$(jexec 'busy=$(ps -eo pid,cmd | awk "/torchrun|train_bench|tbp_npu/ && !/awk|bash/ {print}"); [[ -n "$busy" ]] && { echo OWNER_BUSY; exit 90; }; echo IDLE' || true)
echo "$idle_out" | tee "${PARENT_LOCAL}/logs/idle_check.txt"
echo "$idle_out" | grep -q OWNER_BUSY && exit 90

cd "${ROOT}"
( export PARENT_RUN_ID ARM_RUN_ID CASE_ID DOSE POD NPROC NNODES OUT_FAMILY ITERS WARMUP
  export INJECT_START INJECT_STOP INJECT_KIND DUMP_PROBING_SQL DUMP_WAIT_S ARM RESIDENT_RATE
  export PILLAR_C_SET_RATE PROBING_TORCH_PROFILING PILLAR_C_SET_UPGRADE HOLD_EXEC_SKIP_HEAVY_JSYNC
  export PROBING_SPAN_BACKENDS FS_SHARED_SCRIPTS FS_PLATFORM_ASCEND LOCAL_RESULT_ROOT_BASE POD_BUNDLE MODE
  bash scripts/fail-slow/run_pillar_c_arm.sh ) >"${PARENT_LOCAL}/logs/hold_exec.log" 2>&1 &
HOLD_PID=$!

# attach early: L>=15
L=0
for e in $(seq 0 2 300); do
  L=$(jsonl_lines); L=${L:-0}
  [[ "$L" =~ ^[0-9]+$ ]] && [ "$L" -ge 15 ] && { echo "[b5c] L=${L} (${e}s)"; break; }
  jexec "test -f '${OUT_C2}/node_0.fail'" 2>/dev/null && { echo FAIL; wait "$HOLD_PID" 2>/dev/null; exit 2; }
  sleep 2
done

find_victim() {
  jexec "export PATH='/usr/bin:/bin:${POD_PYDEPS}/bin:${PYBIN}:\${PATH}'; for pid in \$(ps -eo pid,args | awk '/\\/tmp\\/tbp_npu/ && !/awk|bash|torchrun/ {print \$1}'); do lr=\$(tr '\\0' '\\n' < /proc/\$pid/environ 2>/dev/null | awk -F= '\$1==\"LOCAL_RANK\"{print \$2; exit}'); [[ \"\$lr\" == '${SIDECAR_LOCAL_RANK}' ]] && { echo \$pid; exit 0; }; done" 2>/dev/null | tr -d '[:space:]'
}

VICTIM_PID=$(find_victim)
echo "[b5c] VICTIM_PID=${VICTIM_PID:-none}"

TORCH_FILE=""; SYNC_IN_FILE="False"; FILE_OK=no; HOT_UPDATED=no; N_ROWS=0; DOWNGRADE_OK=no; ROWS_OUT=""; L_BEFORE=$L

if [[ -n "${VICTIM_PID}" ]] && [[ "${VICTIM_PID}" =~ ^[0-9]+$ ]]; then
  # __file__ via direct python (same PYTHONPATH as train)
  jexec "export PATH='/usr/bin:/bin:${POD_PYDEPS}/bin:${PYBIN}:\${PATH}'; export PYTHONPATH='${POD_PYDEPS}:\${PYTHONPATH:-}'; ${PYBIN}/python -c \"import probing.ext.torch as t, pathlib; p=t.__file__; print('FILE', p); print('SYNC', '_sync_live_tracers' in pathlib.Path(p).read_text())\"" \
    2>&1 | tee "${PARENT_LOCAL}/logs/torch_file.txt" || true
  TORCH_FILE=$(grep '^FILE ' "${PARENT_LOCAL}/logs/torch_file.txt" | tail -1 | cut -d' ' -f2-)
  SYNC_IN_FILE=$(grep '^SYNC ' "${PARENT_LOCAL}/logs/torch_file.txt" | tail -1 | awk '{print $2}')
  if echo "$TORCH_FILE" | grep -q 'pydeps/probing/ext/torch.py' && [[ "$SYNC_IN_FILE" == "True" ]]; then FILE_OK=yes; fi

  # attach + SET via probing CLI
  jexec "export PATH='/usr/bin:/bin:${POD_PYDEPS}/bin:${PYBIN}:\${PATH}'; export PYTHONPATH='${POD_PYDEPS}:\${PYTHONPATH:-}'; timeout 15 probing -t ${VICTIM_PID} query 'SHOW TABLES'" \
    2>&1 | tee "${PARENT_LOCAL}/logs/attach_ping.txt" || true

  jexec "export PATH='/usr/bin:/bin:${POD_PYDEPS}/bin:${PYBIN}:\${PATH}'; export PYTHONPATH='${POD_PYDEPS}:\${PYTHONPATH:-}'; timeout 15 probing -t ${VICTIM_PID} config probing.torch.profiling=on,rate=1.0" \
    2>&1 | tee "${PARENT_LOCAL}/logs/set_rate_1.log" || true

  TARGET_L=$((L_BEFORE + 22))
  for e in $(seq 0 2 180); do
    L=$(jsonl_lines); L=${L:-0}
    [[ "$L" -ge "$TARGET_L" ]] && break
    jexec "test -f '${OUT_C2}/node_0.done' -o -f '${OUT_C2}/node_0.fail'" 2>/dev/null && break
    sleep 2
  done
  echo "[b5c] post-SET L=${L}"

  jexec "grep -i 'hot-updated\\|Torch profiling enabled' '${OUT_C2}/node_0.log' 2>/dev/null | tail -15" \
    >"${PARENT_LOCAL}/logs/hot_update_grep.txt" 2>/dev/null || true
  grep -qi 'hot-updated' "${PARENT_LOCAL}/logs/hot_update_grep.txt" 2>/dev/null && HOT_UPDATED=yes

  ROWS_OUT=$(jexec "export PATH='/usr/bin:/bin:${POD_PYDEPS}/bin:${PYBIN}:\${PATH}'; export PYTHONPATH='${POD_PYDEPS}:\${PYTHONPATH:-}'; timeout 20 probing -t ${VICTIM_PID} query 'SELECT COUNT(*) AS n FROM python.torch_trace'" 2>&1 || true)
  echo "$ROWS_OUT" | tee "${PARENT_LOCAL}/logs/torch_trace_count.txt"
  N_ROWS=$(echo "$ROWS_OUT" | grep -oE '[0-9]+' | tail -1); N_ROWS=${N_ROWS:-0}

  jexec "export PATH='/usr/bin:/bin:${POD_PYDEPS}/bin:${PYBIN}:\${PATH}'; export PYTHONPATH='${POD_PYDEPS}:\${PYTHONPATH:-}'; timeout 15 probing -t ${VICTIM_PID} config probing.torch.profiling=on,rate=0" \
    2>&1 | tee "${PARENT_LOCAL}/logs/set_rate_0.log" || true
  sleep 3
  jexec "grep -i 'hot-disabled\\|hot-updated' '${OUT_C2}/node_0.log' 2>/dev/null | tail -8" \
    >"${PARENT_LOCAL}/logs/downgrade_grep.txt" 2>/dev/null || true
  grep -qiE 'hot-disabled|rate=0' "${PARENT_LOCAL}/logs/downgrade_grep.txt" 2>/dev/null && DOWNGRADE_OK=yes
else
  echo "[b5c] no live pid — training too fast or attach miss"
fi

wait "$HOLD_PID" 2>/dev/null || true

VERDICT=FAIL
[[ "$FILE_OK" == "yes" && "$HOT_UPDATED" == "yes" && "${N_ROWS:-0}" -gt 0 ]] && VERDICT=PASS
FAIL_POINT=""
[[ "$FILE_OK" != "yes" ]] && FAIL_POINT="${FAIL_POINT} __file__/sync"
[[ "$HOT_UPDATED" != "yes" ]] && FAIL_POINT="${FAIL_POINT} hot-updated"
[[ "${N_ROWS:-0}" -le 0 ]] && FAIL_POINT="${FAIL_POINT} n_rows=0"
[[ -z "${VICTIM_PID}" ]] && FAIL_POINT="${FAIL_POINT} no_pid"

cat >"${PARENT_LOCAL}/PR2_EXP_B5c_SMOKE.md" <<MD
# PR-2 B5c 短测 · ${VERDICT}

**日期**：$(date +%Y-%m-%d\ %H:%M)  
**run_id**：\`${PARENT_RUN_ID}\`  
**pod**：\`${POD}\` · rank=${SIDECAR_LOCAL_RANK} pid=${VICTIM_PID:-n/a}  
**outdir**：\`${OUT_C2}\`

| 项 | 值 |
|----|-----|
| **verdict** | **${VERDICT}** |
| \`__file__\` | \`${TORCH_FILE:-n/a}\` |
| \`_sync_live_tracers\` | ${SYNC_IN_FILE:-?} |
| hot-updated | **${HOT_UPDATED}** |
| culprit TT rows | **${N_ROWS}** |
| rate=0 downgrade | ${DOWNGRADE_OK} |
| ITERS | ${ITERS} |
| L SET/post | ${L_BEFORE} → ${L:-?} |
| 失败点 | ${FAIL_POINT:-无} |

## hot-updated
\`\`\`
$(head -8 "${PARENT_LOCAL}/logs/hot_update_grep.txt" 2>/dev/null || echo none)
\`\`\`

## COUNT
\`\`\`
${ROWS_OUT:-none}
\`\`\`

MD
cp "${PARENT_LOCAL}/PR2_EXP_B5c_SMOKE.md" "${LOCAL_RESULT_ROOT_BASE}/PR2_EXP_B5c_SMOKE.md"

echo "========== B5c ${VERDICT} =========="
echo "__file__=${TORCH_FILE:-n/a}"
echo "hot-updated=${HOT_UPDATED}"
echo "rows=${N_ROWS}"
echo "$PARENT_RUN_ID"
