#!/usr/bin/env bash
# Synthetic workload 单节点 runner：setsid + 本 pod 自身 node_${id}.pgid。
# arm_kind=synthetic：只加载 hash 命名、只读 sealed SO（禁止可变 build/lib 路径）。
set -euo pipefail

: "${RUN_ID:?}"
: "${NODE_RANK:?}"
: "${MASTER_ADDR:?}"
: "${MASTER_PORT:?}"
: "${NNODES:?}"
: "${NPROC:?}"
: "${CODE_DIR:?}"
: "${OUT_DIR:?}"
: "${CAPTURE_RANKS:?}"

export OUT_DIR CODE_DIR RUN_ID NODE_RANK

source /usr/local/Ascend/cann-8.5.0/set_env.sh
source /root/miniconda3/etc/profile.d/conda.sh
conda activate llm_test

export PYTHONUNBUFFERED=1
export GLOO_SOCKET_IFNAME="${GLOO_SOCKET_IFNAME:-eth0}"
export HCCL_CONNECT_TIMEOUT="${HCCL_CONNECT_TIMEOUT:-300}"
export HCCL_EXEC_TIMEOUT="${HCCL_EXEC_TIMEOUT:-300}"
export LD_LIBRARY_PATH="/usr/local/Ascend/cann-8.5.0/lib64:${LD_LIBRARY_PATH:-}"
PRELOADS="/usr/local/Ascend/cann-8.5.0/lib64/libmspti.so"
if [[ "${ENABLE_ACL_INTERPOSE:-0}" == "1" ]]; then
  PRELOADS="${CODE_DIR}/build/libmspti_sync_interpose.so:${PRELOADS}"
fi
export LD_PRELOAD="${PRELOADS}${LD_PRELOAD:+:${LD_PRELOAD}}"

mkdir -p "${OUT_DIR}"
rm -f "${OUT_DIR}/node_${NODE_RANK}.done" "${OUT_DIR}/node_${NODE_RANK}.fail" \
  "${OUT_DIR}/node_${NODE_RANK}.pgid" "${OUT_DIR}/node_${NODE_RANK}.pids"

RUN_MARKER="${RUN_MARKER:-MSPTI_SYNTH_${RUN_ID}}"
export RUN_MARKER
ARM_KIND="${ARM_KIND:-synthetic}"

# Resolve immutable sealed SO only (hash-named 0444). Never load mutable build/*.so.
SEALED_SO=""
EXPECTED_SO_HASH=""
if [[ -f "${OUT_DIR}/provenance_build.json" ]]; then
  SEALED_REL="$(python3 - <<'PY'
import json, os
from pathlib import Path
obj = json.loads((Path(os.environ["OUT_DIR"]) / "provenance_build.json").read_text(encoding="utf-8"))
print(obj.get("collector_so_sealed_relpath") or "")
PY
)"
  SEALED_LOAD="$(python3 - <<'PY'
import json, os
from pathlib import Path
obj = json.loads((Path(os.environ["OUT_DIR"]) / "provenance_build.json").read_text(encoding="utf-8"))
print(obj.get("collector_so_load_path") or obj.get("collector_so_sealed_path") or "")
PY
)"
  EXPECTED_SO_HASH="$(python3 - <<'PY'
import json, os
from pathlib import Path
obj = json.loads((Path(os.environ["OUT_DIR"]) / "provenance_build.json").read_text(encoding="utf-8"))
print(obj.get("collector_so_sha256") or "")
PY
)"
  if [[ -n "${SEALED_REL}" && -f "${OUT_DIR}/${SEALED_REL}" ]]; then
    SEALED_SO="${OUT_DIR}/${SEALED_REL}"
  elif [[ -n "${SEALED_LOAD}" && -f "${SEALED_LOAD}" ]]; then
    SEALED_SO="${SEALED_LOAD}"
  fi
fi
if [[ -z "${SEALED_SO}" && -d "${CODE_DIR}/build/sealed" ]]; then
  SEALED_SO="$(ls -1 "${CODE_DIR}/build/sealed"/libmspti_sync_skeleton.so.* 2>/dev/null | head -1 || true)"
fi
if [[ -z "${SEALED_SO}" || ! -f "${SEALED_SO}" ]]; then
  echo "FATAL: sealed collector SO missing; refuse mutable build/lib path" >&2
  exit 13
fi
case "$(basename "${SEALED_SO}")" in
  libmspti_sync_skeleton.so.[0-9a-f]*)
    ;;
  *)
    echo "FATAL: SO not hash-named sealed: ${SEALED_SO}" >&2
    exit 13
    ;;
esac
# Refuse obvious mutable build path without hash suffix.
if [[ "${SEALED_SO}" == */build/libmspti_sync_skeleton.so ]]; then
  echo "FATAL: mutable build SO path forbidden: ${SEALED_SO}" >&2
  exit 13
fi

SO_HASH_LOADED="$(python3 - <<PY
from hashlib import sha256
from pathlib import Path
p = Path("${SEALED_SO}")
# Symlink refuse
if p.is_symlink():
    raise SystemExit("sealed SO is symlink")
h = sha256()
with p.open("rb") as fh:
    while True:
        b = fh.read(1024 * 1024)
        if not b:
            break
        h.update(b)
print(h.hexdigest())
PY
)"
if [[ -n "${EXPECTED_SO_HASH}" && "${SO_HASH_LOADED}" != "${EXPECTED_SO_HASH}" ]]; then
  echo "FATAL: sealed SO rehash ${SO_HASH_LOADED} != provenance ${EXPECTED_SO_HASH}" >&2
  exit 13
fi
# Filename suffix must match content hash.
SO_BASE="$(basename "${SEALED_SO}")"
SO_SUFFIX="${SO_BASE#libmspti_sync_skeleton.so.}"
if [[ "${SO_SUFFIX}" != "${SO_HASH_LOADED}" ]]; then
  echo "FATAL: sealed SO name hash ${SO_SUFFIX} != content ${SO_HASH_LOADED}" >&2
  exit 13
fi

mkdir -p "${OUT_DIR}/sealed_bins"
SEALED_NAME="$(basename "${SEALED_SO}")"
if [[ ! -f "${OUT_DIR}/sealed_bins/${SEALED_NAME}" ]]; then
  cp -f "${SEALED_SO}" "${OUT_DIR}/sealed_bins/${SEALED_NAME}"
fi
chmod a-w "${OUT_DIR}/sealed_bins/${SEALED_NAME}" || true
# Prefer loading the attempt-local sealed copy.
SEALED_SO="${OUT_DIR}/sealed_bins/${SEALED_NAME}"

python3 - <<PY
import json, os, stat
from pathlib import Path
out = Path("${OUT_DIR}")
launch = out / f"node_${NODE_RANK}.launch.json"
mode = out.joinpath("sealed_bins", "${SEALED_NAME}").stat().st_mode & 0o777
payload = {
    "node_rank": int("${NODE_RANK}"),
    "run_id": os.environ.get("RUN_ID"),
    "run_marker": os.environ.get("RUN_MARKER"),
    "code_dir": os.environ.get("CODE_DIR"),
    "out_dir": os.environ.get("OUT_DIR"),
    "raw_exit_code_pending": True,
    "workload_kind": "synthetic",
    "arm_kind": "${ARM_KIND}",
    "collector_so_loaded_path": "${SEALED_SO}",
    "collector_so_sha256_loaded": "${SO_HASH_LOADED}",
    "collector_so_mode": oct(mode),
}
launch.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
print("node_provenance_so", "${SO_HASH_LOADED}"[:16], oct(mode))
PY

set +e
E2E_T0_NS="$(python3 -c 'import time; print(time.monotonic_ns())')"
setsid env RUN_MARKER="${RUN_MARKER}" OUT_DIR="${OUT_DIR}" RUN_ID="${RUN_ID}" \
  MSPTI_COLLECTOR_LIB="${SEALED_SO}" \
  MSPTI_COLLECTOR_SO_SHA256="${SO_HASH_LOADED}" \
  timeout "${RUN_TIMEOUT_S:-300}" /root/miniconda3/envs/llm_test/bin/torchrun \
  --nnodes="${NNODES}" \
  --nproc_per_node="${NPROC}" \
  --node_rank="${NODE_RANK}" \
  --master_addr="${MASTER_ADDR}" \
  --master_port="${MASTER_PORT}" \
  "${CODE_DIR}/workload.py" \
  --out="${OUT_DIR}" \
  --collector-lib="${SEALED_SO}" \
  --collector="${COLLECTOR:-on}" \
  --capture-ranks="${CAPTURE_RANKS}" \
  --warmup="${WARMUP:-3}" \
  --steps="${STEPS:-2}" \
  --matmul-size="${MATMUL_SIZE:-2048}" \
  --allreduce-bytes="${ALLREDUCE_BYTES:-4194304}" \
  --gap-us="${GAP_US:-50}" \
  --reorder-us="${REORDER_US:-1000}" \
  >"${OUT_DIR}/node_${NODE_RANK}.log" 2>&1 &
TPID=$!
echo "${TPID}" >"${OUT_DIR}/node_${NODE_RANK}.pgid"
echo "${TPID}" >"${OUT_DIR}/node_${NODE_RANK}.pids"
python3 - <<PY
from pathlib import Path
p = Path("${OUT_DIR}") / f"node_${NODE_RANK}.pgid"
got = int(p.read_text().strip())
assert got == int("${TPID}"), (got, "${TPID}")
print("pgid_ok", got)
PY
for _ in 1 2 3; do
  sleep 1
  ps -eo pid,pgid,args | awk -v pgid="${TPID}" '$2==pgid {print $1}' \
    >"${OUT_DIR}/node_${NODE_RANK}.pids" || true
done
wait "${TPID}"
rc=$?
E2E_T1_NS="$(python3 -c 'import time; print(time.monotonic_ns())')"
E2E_WALL_MS="$(python3 -c 'import sys; a=int(sys.argv[1]); b=int(sys.argv[2]); print("%.3f" % ((b-a)/1e6))' "${E2E_T0_NS}" "${E2E_T1_NS}")"
set -e

# End-of-run: rehash same sealed SO; record real raw exit + monotonic e2e_wall_ms.
python3 - <<PY
import json
from hashlib import sha256
from pathlib import Path
so = Path("${SEALED_SO}")
h = sha256()
with so.open("rb") as fh:
    while True:
        b = fh.read(1024 * 1024)
        if not b:
            break
        h.update(b)
got = h.hexdigest()
expected = "${SO_HASH_LOADED}"
if got != expected:
    raise SystemExit(f"end-of-run sealed SO hash drift {got} != {expected}")
p = Path("${OUT_DIR}") / f"node_${NODE_RANK}.launch.json"
data = json.loads(p.read_text())
data["raw_exit_code"] = int(${rc})
data["raw_exit_code_pending"] = False
data["collector_so_sha256_end"] = got
data["collector_so_sha256_loaded"] = expected
data["collector_so_loaded_path"] = "${SEALED_SO}"
e2e = float("${E2E_WALL_MS}")
if e2e <= 0:
    raise SystemExit(f"non-positive e2e_wall_ms={e2e}")
data["e2e_wall_ms"] = e2e
data["e2e_t0_monotonic_ns"] = int("${E2E_T0_NS}")
data["e2e_t1_monotonic_ns"] = int("${E2E_T1_NS}")
data["e2e_wall_definition"] = (
    "time.monotonic_ns() from torchrun/workload argv start through process exit "
    "(command→export complete); independent of UTC timestamps"
)
p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
print("so_end_hash_ok", got[:16], "e2e_wall_ms", e2e, "raw_exit", int(${rc}))
PY

if [[ "${rc}" -eq 0 ]]; then
  touch "${OUT_DIR}/node_${NODE_RANK}.done"
else
  printf '%s\n' "${rc}" >"${OUT_DIR}/node_${NODE_RANK}.fail"
fi
exit "${rc}"
