#!/usr/bin/env bash
# Param-Calib ②-A：单 case full_fidelity（大 cpu 环）→ 供离线截窗定 W*
# 用法：CASE_ID=P3-SW-A bash run_2a_full_fidelity.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/fail-slow/env.sh"

CASE_ID="${CASE_ID:?need CASE_ID}"
POD="${POD:-grj-megatron-32card-0716-worker-0}"
# 注入窗 ~60–110s；全训 ~150–220s → 64MiB 足够盖住 inject→dump（8MiB≈36s 不够）
export PROBING_CPU_RING_MB="${PROBING_CPU_RING_MB:-64}"
export INLINE_2C_FALLBACK_S="${INLINE_2C_FALLBACK_S:-0.6}"
# 强制本机落点（env.sh 默认 probing-huawei/results；此处覆盖为 myportal）
export LOCAL_RESULT_ROOT_BASE="/Users/yinjinrun/Codespace/myportal/results/ascend-ais"
export OUT_FAMILY="${OUT_FAMILY:-param_calib/2A_trace_window}"
export POD_RESULTS="${POD_RESULTS:-/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais}"
export POD_BUNDLE="${POD_BUNDLE:-/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle}"
export FS_SHARED_SCRIPTS="${FS_SHARED_SCRIPTS:-/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow}"
export FS_PLATFORM_ASCEND="${FS_SHARED_SCRIPTS}/platform/ascend"

TS=$(date +%Y%m%d_%H%M%S)
CASE_SLUG=$(echo "$CASE_ID" | tr 'A-Z' 'a-z')
PARENT_RUN_ID="${PARENT_RUN_ID:-${TS}-2a-${CASE_SLUG}-loud}"

echo "[2a-ff] CASE=$CASE_ID PARENT=$PARENT_RUN_ID POD=$POD RING_MB=$PROBING_CPU_RING_MB"

# 发射前确认无真训练
ssh -o ConnectTimeout=20 "${JUMP_HOST}" "export KUBECONFIG=${JUMP_KUBECONFIG}; K=${JUMP_KUBECTL}; \$K exec -n default ${POD} -- bash -lc 'pgrep -af \"torchrun|megatron|pretrain_gpt|train_bench\" 2>/dev/null | grep -vE \"bash|pgrep\" || echo NONE'" \
  | tee /tmp/2a_idle_check.txt
if ! grep -q NONE /tmp/2a_idle_check.txt; then
  echo "[2a-ff] YIELD: owner busy" >&2
  exit 90
fi

ARM=full_fidelity \
CASE_ID="$CASE_ID" \
DOSE=loud \
POD="$POD" \
PARENT_RUN_ID="$PARENT_RUN_ID" \
OUT_FAMILY="$OUT_FAMILY" \
PROBING_CPU_RING_MB="$PROBING_CPU_RING_MB" \
LOCAL_RESULT_ROOT_BASE="$LOCAL_RESULT_ROOT_BASE" \
bash "${ROOT}/scripts/fail-slow/run_pillar_c_arm.sh"

echo "$PARENT_RUN_ID" >"${LOCAL_RESULT_ROOT_BASE}/${OUT_FAMILY}/LAST_PARENT_${CASE_SLUG}.txt"
echo "[2a-ff] done PARENT=$PARENT_RUN_ID"
