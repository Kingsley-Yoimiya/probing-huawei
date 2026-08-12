#!/usr/bin/env bash
# One-shot B fixture debug on GRJ master.
set -euo pipefail
JUMP="${JUMP:-afs-cpu}"
KUBE="${KUBE:-/root/.kube/config-vc-a3-241ceshi-songyiyang.yaml}"
KUBECTL="${KUBECTL:-/root/bin/kubectl}"
POD="${MASTER_POD:-grj-megatron-32card-0716-master-0}"
CODE="/afs-a3-weight-share/yinjinrun.p-huawei/probing-huawei/experiments/mspti_sync_skeleton-yield-fp"

ssh -o ConnectTimeout=30 "${JUMP}" "export KUBECONFIG='${KUBE}'; K='${KUBECTL}'; \$K exec -n default '${POD}' -- bash --noprofile --norc -s" <<'EOS'
set -euo pipefail
CODE=/afs-a3-weight-share/yinjinrun.p-huawei/probing-huawei/experiments/mspti_sync_skeleton-yield-fp
OUT=/tmp/yield_b_debug_$$
mkdir -p "$OUT/fixture_b"
printf 'import time\ntime.sleep(60)\n' > "$OUT/fixture_b/pretrain_gpt.py"
python3 "$OUT/fixture_b/pretrain_gpt.py" &
FPID=$!
echo "FPID=$FPID"
sleep 0.5
ps -eo pid,pgid,state,args | awk -v p="$FPID" '$1==p {print}'
echo 999999001 > "$OUT/node_0.pgid"
set +e
python3 "$CODE/opponent_check.py" --mode yield --out-dir "$OUT" --run-marker MSPTI_YIELD_FP_DEBUG_MARKER01 --node-id 0
echo "RC=$?"
set -e
kill "$FPID" 2>/dev/null || true
wait "$FPID" 2>/dev/null || true
EOS
