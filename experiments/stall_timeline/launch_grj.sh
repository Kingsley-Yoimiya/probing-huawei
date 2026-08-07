#!/usr/bin/env bash
# Launch one bounded 16/32-card run inside the explicitly authorized idle GRJ hold pods.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXP_LOCAL="${ROOT}/experiments/stall_timeline"
JUMP="${JUMP:-ais-cf3e61a5}"
KUBE="${KUBE:-/tmp/config-vc-a3-241ceshi-songyiyang.yaml}"
KUBECTL="${KUBECTL:-/root/.cache/volcano/kubectl/kubectl}"
NS="${NS:-default}"
MASTER_POD="${MASTER_POD:-grj-megatron-32card-0716-master-0}"
WORKER_POD="${WORKER_POD:-grj-megatron-32card-0716-worker-0}"
NNODES="${NNODES:-1}"
NPROC="${NPROC:-16}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)-stall-timeline-${NNODES}x${NPROC}-${SCHEME:-sentinel}}"
MASTER_PORT="${MASTER_PORT:-29861}"
AFS_ROOT="/afs-a3-weight-share/yinjinrun.p-huawei"
CODE_DIR="${AFS_ROOT}/probing-huawei/experiments/stall_timeline"
OUT_DIR="${AFS_ROOT}/results/stall-timeline/${RUN_ID}"

if [[ "$NNODES" != "1" && "$NNODES" != "2" ]]; then
  echo "NNODES must be 1 or 2" >&2
  exit 2
fi

jump() {
  ssh "$JUMP" "export KUBECONFIG='$KUBE'; K='$KUBECTL'; $*"
}

pod_exec() {
  local pod="$1" cmd="$2"
  jump "\$K exec -n '$NS' '$pod' -- bash --noprofile --norc -lc $(printf '%q' "$cmd")"
}

check_idle() {
  local pod="$1"
  local active
  active="$(pod_exec "$pod" "pgrep -af 'torchrun|pretrain|megatron|stall_timeline/probe.py' 2>/dev/null | grep -v defunct | grep -v 'bash --noprofile' | head -10 || true")"
  if [[ -n "$active" ]]; then
    echo "FATAL: $pod is not idle:" >&2
    echo "$active" >&2
    exit 3
  fi
}

check_idle "$MASTER_POD"
if [[ "$NNODES" == "2" ]]; then
  check_idle "$WORKER_POD"
fi

echo "[stall-timeline] sync code to owned AFS path $CODE_DIR"
COPYFILE_DISABLE=1 tar -C "$EXP_LOCAL" -cf - README.md probe.py analyze.py run_node.sh \
  | ssh "$JUMP" "export KUBECONFIG='$KUBE'; K='$KUBECTL'; \$K exec -i -n '$NS' '$MASTER_POD' -- bash --noprofile --norc -lc 'mkdir -p $CODE_DIR && tar -C $CODE_DIR -xf - && chmod +x $CODE_DIR/*.sh $CODE_DIR/*.py'"

MASTER_ADDR="$(jump "\$K get pod -n '$NS' '$MASTER_POD' -o jsonpath='{.status.podIP}'")"
[[ -n "$MASTER_ADDR" ]] || { echo "no master pod IP" >&2; exit 4; }

COMMON_ENV="RUN_ID='$RUN_ID' MASTER_ADDR='$MASTER_ADDR' MASTER_PORT='$MASTER_PORT' NNODES='$NNODES' NPROC='$NPROC' CODE_DIR='$CODE_DIR' OUT_DIR='$OUT_DIR' SCHEME='${SCHEME:-sentinel}' ITERS='${ITERS:-120}' DURATION_S='${DURATION_S:-0}' STOP_CHECK_EVERY='${STOP_CHECK_EVERY:-10000}' WARMUP='${WARMUP:-20}' MATMUL_SIZE='${MATMUL_SIZE:-1024}' AR_BYTES='${AR_BYTES:-1048576}' SAMPLE_RATE='${SAMPLE_RATE:-0.1}' SAMPLE_RANKS='${SAMPLE_RANKS:-4}' INJECT_KIND='${INJECT_KIND:-device}' INJECT_RANK='${INJECT_RANK:-7}' INJECT_START='${INJECT_START:-20}' INJECT_STOP='${INJECT_STOP:-100}' INJECT_EVERY='${INJECT_EVERY:-20}' INJECT_MS='${INJECT_MS:-500}' INJECT_MATMUL_SIZE='${INJECT_MATMUL_SIZE:-2048}' HOLE_MS='${HOLE_MS:-200}' RECORD_MODE='${RECORD_MODE:-full}' HEARTBEAT_EVERY='${HEARTBEAT_EVERY:-1000}' FLUSH_EVERY='${FLUSH_EVERY:-100}' RUN_TIMEOUT_S='${RUN_TIMEOUT_S:-2400}'"

pod_exec "$MASTER_POD" "mkdir -p '$OUT_DIR'; env $COMMON_ENV NODE_RANK=0 setsid nohup '$CODE_DIR/run_node.sh' </dev/null >'$OUT_DIR/launch_0.log' 2>&1 &"
if [[ "$NNODES" == "2" ]]; then
  pod_exec "$WORKER_POD" "mkdir -p '$OUT_DIR'; env $COMMON_ENV NODE_RANK=1 setsid nohup '$CODE_DIR/run_node.sh' </dev/null >'$OUT_DIR/launch_1.log' 2>&1 &"
fi

echo "[stall-timeline] RUN_ID=$RUN_ID world=$((NNODES * NPROC)) out=$OUT_DIR"
deadline=$((SECONDS + ${WAIT_TIMEOUT_S:-1000}))
while (( SECONDS < deadline )); do
  done_count=0
  fail_count=0
  for node in $(seq 0 $((NNODES - 1))); do
    if pod_exec "$MASTER_POD" "test -f '$OUT_DIR/node_${node}.done'" >/dev/null 2>&1; then
      done_count=$((done_count + 1))
    elif pod_exec "$MASTER_POD" "test -f '$OUT_DIR/node_${node}.fail'" >/dev/null 2>&1; then
      fail_count=$((fail_count + 1))
    fi
  done
  if (( fail_count > 0 )); then
    pod_exec "$MASTER_POD" "tail -n 100 '$OUT_DIR'/node_*.log 2>/dev/null || true"
    exit 5
  fi
  if (( done_count == NNODES )); then
    break
  fi
  sleep 5
done

if (( SECONDS >= deadline )); then
  echo "timeout waiting for $RUN_ID" >&2
  pod_exec "$MASTER_POD" "tail -n 100 '$OUT_DIR'/node_*.log 2>/dev/null || true"
  exit 6
fi

pod_exec "$MASTER_POD" "python3 '$CODE_DIR/analyze.py' '$OUT_DIR' --hole-ms='${HOLE_MS:-200}' >'$OUT_DIR/analyze.log' 2>&1"
pod_exec "$MASTER_POD" "cat '$OUT_DIR/SUMMARY.md'; echo RUN_COMPLETE='$RUN_ID'"
