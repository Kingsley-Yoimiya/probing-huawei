#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/fail-slow/env.sh"

ssh -o BatchMode=yes -o ConnectTimeout=20 "${JUMP_HOST}" bash -s <<EOF
set -euo pipefail
export KUBECONFIG='${JUMP_KUBECONFIG}'
K='${JUMP_KUBECTL}'
echo "kubectl=\$K"
echo "user=\$(\$K config view --minify -o jsonpath='{.contexts[0].context.user}' 2>/dev/null || true)"
\$K auth can-i get pods
\$K auth can-i create pods
\$K get nodes -o json | python3 -c '
import json,sys
d=json.load(sys.stdin)
tot=0
keys=set()
for n in d.get("items",[]):
  for k,v in n.get("status",{}).get("allocatable",{}).items():
    if "Ascend" in k or "910" in k:
      keys.add(k)
      try: tot+=int(v)
      except: pass
print("allocatable_keys", sorted(keys))
print("TOTAL_ASCEND", tot)
'
echo "WARN: hold-exec on yysong-* (our 64 via SYY); never touch a3-megatron / grj-megatron"
echo "=== yysong pods (idle = no live torchrun) ==="
for p in yysong-master-0 yysong-worker-0 yysong-worker-1 yysong-worker-2; do
  live=\$(\$K exec "\$p" -- bash -lc "pgrep -af 'torchrun|megatron|tbp.py' 2>/dev/null | grep -v defunct | grep -v 'bash -lc' | head -3" 2>/dev/null || true)
  if [[ -z "\$live" ]]; then echo "\$p IDLE"; else echo "\$p BUSY"; echo "\$live"; fi
done
\$K get pods -n default --no-headers 2>/dev/null | awk '/yysong|a3-megatron|grj-megatron/{print}' || true
EOF
