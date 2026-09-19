#!/usr/bin/env bash
# One foreground node transaction for a multi-node legacy MSPTI subset run.
# The underlying Megatron runner writes only inside this node's private subtree;
# this wrapper atomically publishes the root-level node receipt last.
set -euo pipefail

: "${ATTEMPT_DIR:?}"
: "${RUN_ID:?}"
: "${NODE_RANK:?}"
: "${MASTER_ADDR:?}"
: "${MASTER_PORT:?}"
: "${NNODES:?}"
: "${NPROC:?}"
: "${CODE_DIR:?}"
: "${SEALED_SO_SHA256:?}"
: "${SOURCE_CODE_HASH:?}"

NODE_TAG="$(printf '%02d' "${NODE_RANK}")"
NODE_OUT="${ATTEMPT_DIR}/node_runs/node_${NODE_TAG}"
RECEIPT="${ATTEMPT_DIR}/node_${NODE_TAG}.receipt.json"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "${ATTEMPT_DIR}/node_runs" "${NODE_OUT}"
if [[ -e "${RECEIPT}" ]]; then
  echo "FATAL: receipt already exists: ${RECEIPT}" >&2
  exit 65
fi
if find "${NODE_OUT}" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
  echo "FATAL: node output subtree is not empty: ${NODE_OUT}" >&2
  exit 65
fi

set +e
OUT_DIR="${NODE_OUT}" bash "${CODE_DIR}/run_megatron_node.sh"
RUN_RC=$?
set -e

ENDED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 - "${RECEIPT}.tmp" "${RUN_RC}" "${STARTED_AT}" "${ENDED_AT}" \
  "${NODE_RANK}" "${NNODES}" "${NPROC}" "${RUN_ID}" "${NODE_OUT}" \
  "${SEALED_SO_SHA256}" "${SOURCE_CODE_HASH}" "${MASTER_ADDR}" \
  "${MASTER_PORT}" <<'PY'
import hashlib
import json
import os
import pathlib
import socket
import sys

path = pathlib.Path(sys.argv[1])
run_rc = int(sys.argv[2])
node = int(sys.argv[5])
nnodes = int(sys.argv[6])
nproc = int(sys.argv[7])
node_out = pathlib.Path(sys.argv[9])
rank_start = node * nproc
rank_end = rank_start + nproc - 1

trace_ranks = []
meta_ranks = []
audit_ranks = []
for rank in range(rank_start, rank_end + 1):
    if (node_out / f"rank_{rank:04d}.skeleton.jsonl").is_file():
        trace_ranks.append(rank)
    if (node_out / f"rank_{rank:04d}.mspti_meta.json").is_file():
        meta_ranks.append(rank)
    if (node_out / f"rank_{rank:04d}.buffer_audit.json").is_file():
        audit_ranks.append(rank)

launch_path = node_out / f"node_{node}.launch.json"
launch_sha = None
collector_loaded_sha = None
if launch_path.is_file():
    launch_bytes = launch_path.read_bytes()
    launch_sha = hashlib.sha256(launch_bytes).hexdigest()
    try:
        collector_loaded_sha = json.loads(launch_bytes).get(
            "collector_so_sha256_loaded"
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass

payload = {
    "schema_version": 1,
    "attempt_id": sys.argv[8],
    "node_index": node,
    "nnodes": nnodes,
    "nproc_per_node": nproc,
    "world_size": nnodes * nproc,
    "master_addr": sys.argv[12],
    "master_port": int(sys.argv[13]),
    "pod_hostname": socket.gethostname(),
    "expected_global_rank_start": rank_start,
    "expected_global_rank_end": rank_end,
    "trace_count": len(trace_ranks),
    "trace_ranks": trace_ranks,
    "meta_count": len(meta_ranks),
    "meta_ranks": meta_ranks,
    "buffer_audit_count": len(audit_ranks),
    "buffer_audit_ranks": audit_ranks,
    "run_rc": run_rc,
    "done_exists": (node_out / f"node_{node}.done").exists(),
    "fail_exists": (node_out / f"node_{node}.fail").exists(),
    "node_output_dir": str(node_out),
    "node_launch_sha256": launch_sha,
    "collector_so_sha256": sys.argv[10],
    "collector_so_sha256_loaded": collector_loaded_sha,
    "source_code_hash": sys.argv[11],
    "started_at_utc": sys.argv[3],
    "ended_at_utc": sys.argv[4],
}
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
with path.open("rb") as handle:
    os.fsync(handle.fileno())
os.replace(path, path.with_suffix(""))
directory_fd = os.open(path.parent, os.O_DIRECTORY)
try:
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
PY

exit "${RUN_RC}"
