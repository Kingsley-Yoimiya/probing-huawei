#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/fail-slow/env.sh"

LOCAL_KUBE="${KUBECONFIG}"
if [[ ! -f "${LOCAL_KUBE}" ]]; then
  echo "missing local kube: ${LOCAL_KUBE}" >&2
  exit 1
fi

python3 -c "from pathlib import Path; print(Path('${LOCAL_KUBE}').expanduser().read_text(), end='')" \
  | ssh -o BatchMode=yes -o ConnectTimeout=20 "${JUMP_HOST}" \
    "cat > '${JUMP_KUBECONFIG}' && chmod 600 '${JUMP_KUBECONFIG}' && echo SYNC_OK:${JUMP_KUBECONFIG}"
