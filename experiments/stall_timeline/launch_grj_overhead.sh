#!/usr/bin/env bash
# Launch the bounded 32-card in-process observer overhead benchmark on idle GRJ pods.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXP_LOCAL="${ROOT}/experiments/stall_timeline"
JUMP="${JUMP:-ais-cf3e61a5}"
KUBE="${KUBE:-/tmp/config-vc-a3-241ceshi-songyiyang.yaml}"
KUBECTL="${KUBECTL:-/root/.cache/volcano/kubectl/kubectl}"
NS="${NS:-default}"
MASTER_POD="${MASTER_POD:-grj-megatron-32card-0716-master-0}"
WORKER_POD="${WORKER_POD:-grj-megatron-32card-0716-worker-0}"
NNODES=2
NPROC=16
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)-stall-observer-overhead}"
MASTER_PORT="${MASTER_PORT:-29873}"
AFS_ROOT="/afs-a3-weight-share/yinjinrun.p-huawei"
CODE_DIR="${AFS_ROOT}/probing-huawei/experiments/stall_timeline"
OUT_DIR="${AFS_ROOT}/results/stall-timeline/${RUN_ID}"
BLOCK_PLAN="${BLOCK_PLAN:-control,host,control,rotate,control,full,control,rotate,control,full,control,host,control,full,control,host,control,rotate,control}"

jump() {
  ssh "$JUMP" "export KUBECONFIG='$KUBE'; K='$KUBECTL'; $*"
}

pod_exec() {
  local pod="$1" cmd="$2"
  jump "\$K exec -n '$NS' '$pod' -- bash --noprofile --norc -lc $(printf '%q' "$cmd")"
}

check_idle() {
  local pod="$1" active
  active="$(pod_exec "$pod" "pgrep -af 'torchrun|pretrain|megatron|stall_timeline/.*probe.py' 2>/dev/null | grep -v defunct | grep -v 'bash --noprofile' | head -10 || true")"
  if [[ -n "$active" ]]; then
    echo "FATAL: $pod is not idle:" >&2
    echo "$active" >&2
    exit 3
  fi
}

check_idle "$MASTER_POD"
check_idle "$WORKER_POD"

COPYFILE_DISABLE=1 tar -C "$EXP_LOCAL" -cf - \
  probe.py overhead_probe.py analyze_overhead.py run_overhead_node.sh \
  | ssh "$JUMP" "export KUBECONFIG='$KUBE'; K='$KUBECTL'; \$K exec -i -n '$NS' '$MASTER_POD' -- bash --noprofile --norc -lc 'mkdir -p $CODE_DIR && tar -C $CODE_DIR -xf - && chmod +x $CODE_DIR/*.sh $CODE_DIR/*.py'"

MASTER_ADDR="$(jump "\$K get pod -n '$NS' '$MASTER_POD' -o jsonpath='{.status.podIP}'")"
COMMON_ENV="MASTER_ADDR='$MASTER_ADDR' MASTER_PORT='$MASTER_PORT' NNODES='$NNODES' NPROC='$NPROC' CODE_DIR='$CODE_DIR' OUT_DIR='$OUT_DIR' BLOCK_PLAN='$BLOCK_PLAN' BLOCK_STEPS='${BLOCK_STEPS:-30000}' WARMUP='${WARMUP:-20}' MATMUL_SIZE='${MATMUL_SIZE:-4096}' AR_BYTES='${AR_BYTES:-1048576}' SAMPLE_RANKS='${SAMPLE_RANKS:-4}' HOLE_MS='${HOLE_MS:-200}' HEARTBEAT_EVERY='${HEARTBEAT_EVERY:-10000}' PROBING_MODE='${PROBING_MODE:-2}' PROBING_TABLE='${PROBING_TABLE:-auto}' PROBING_MINIMAL='${PROBING_MINIMAL:-0}' RUN_TIMEOUT_S='${RUN_TIMEOUT_S:-1800}'"

pod_exec "$MASTER_POD" "mkdir -p '$OUT_DIR'; env $COMMON_ENV NODE_RANK=0 setsid nohup '$CODE_DIR/run_overhead_node.sh' </dev/null >'$OUT_DIR/launch_0.log' 2>&1 &"
pod_exec "$WORKER_POD" "mkdir -p '$OUT_DIR'; env $COMMON_ENV NODE_RANK=1 setsid nohup '$CODE_DIR/run_overhead_node.sh' </dev/null >'$OUT_DIR/launch_1.log' 2>&1 &"

echo "[stall-overhead] RUN_ID=$RUN_ID out=$OUT_DIR plan=$BLOCK_PLAN"
deadline=$((SECONDS + ${WAIT_TIMEOUT_S:-1600}))
while (( SECONDS < deadline )); do
  done_count=0
  fail_count=0
  for node in 0 1; do
    if pod_exec "$MASTER_POD" "test -f '$OUT_DIR/node_${node}.done'" >/dev/null 2>&1; then
      done_count=$((done_count + 1))
    elif pod_exec "$MASTER_POD" "test -f '$OUT_DIR/node_${node}.fail'" >/dev/null 2>&1; then
      fail_count=$((fail_count + 1))
    fi
  done
  if (( fail_count > 0 )); then
    pod_exec "$MASTER_POD" "tail -n 120 '$OUT_DIR'/node_*.log 2>/dev/null || true"
    exit 5
  fi
  if (( done_count == 2 )); then
    break
  fi
  sleep 5
done

if (( SECONDS >= deadline )); then
  echo "timeout waiting for $RUN_ID" >&2
  pod_exec "$MASTER_POD" "tail -n 120 '$OUT_DIR'/node_*.log 2>/dev/null || true"
  exit 6
fi

pod_exec "$MASTER_POD" "python3 '$CODE_DIR/analyze_overhead.py' '$OUT_DIR' >'$OUT_DIR/analyze_overhead.log' 2>&1"
pod_exec "$MASTER_POD" "cat '$OUT_DIR/OVERHEAD_SUMMARY.md'; echo RUN_COMPLETE='$RUN_ID'"
