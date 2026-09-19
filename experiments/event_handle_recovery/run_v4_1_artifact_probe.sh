#!/usr/bin/env bash
# V4.1 kernel artifact probe: structure gate + runtime load + smoke.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTC_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="${RUN_ID:-${UTC_STAMP}_d51_wait_dag_v4_1_kernel_artifact}"
LOG_ROOT="/tmp/${RUN_ID}/logs/${UTC_STAMP}/artifact"
mkdir -p "${LOG_ROOT}"

exec > >(tee -a "${LOG_ROOT}/artifact_probe.log") 2>&1

echo "RUN_ID=${RUN_ID}"
echo "LOG_ROOT=${LOG_ROOT}"
echo "expected_runtime=5-15min"

CANN=/usr/local/Ascend/cann-8.5.1
CCEC="$CANN/bin/ccec"
LLD="$CANN/bin/ld.lld"

{
  echo "=== toolchain ==="
  test -x "$CCEC"
  test -x "$LLD"
  readlink -f "$CCEC"
  readlink -f "$LLD"
  "$CCEC" --version || true
  "$LLD" --version || true
  npu-smi info -t board -i 0 2>/dev/null | head -5 || true
} | tee "${LOG_ROOT}/toolchain.txt"

cd "${ROOT}"
chmod +x kernels/build_kernel.sh build.sh
./kernels/build_kernel.sh 2>&1 | tee "${LOG_ROOT}/build_kernel.log"
./build.sh ascend 2>&1 | tee "${LOG_ROOT}/build_preload.log"

SRC="${ROOT}/kernels/d51_compute_delay_kernel.cpp"
OUT="${ROOT}/build/kernels"
TMP="${OUT}/d51_compute_delay_kernel_tmp.o"
FINAL="${OUT}/d51_compute_delay_kernel.o"

{
  echo "=== file/readelf gate ==="
  file "$TMP" "$FINAL"
  od -An -tx1 -N4 "$TMP"
  od -An -tx1 -N4 "$FINAL"
  readelf -h "$TMP"
  readelf -h "$FINAL"
  readelf -SW "$FINAL"
  readelf -Ws "$FINAL"
  sha256sum "$SRC" "$TMP" "$FINAL"
} | tee "${LOG_ROOT}/elf_inspect.txt"

# Hard assertions
file "$TMP" | grep -q "relocatable" || { echo "STOP: TMP not relocatable"; exit 2; }
readelf -h "$TMP" | grep -q "Type:.*REL" || { echo "STOP: TMP not ET_REL"; exit 2; }
file "$FINAL" | grep -q "executable" || { echo "STOP: FINAL not executable"; exit 2; }
readelf -h "$FINAL" | grep -q "Type:.*EXEC" || { echo "STOP: FINAL not ET_EXEC"; exit 2; }
readelf -SW "$FINAL" | grep -q "ascend.meta.d51_compute_delay_kernel" || { echo "STOP: missing metadata section"; exit 2; }
readelf -Ws "$FINAL" | grep -q "d51_compute_delay_kernel" || { echo "STOP: missing kernel symbol"; exit 2; }

if command -v msobjdump >/dev/null 2>&1; then
  msobjdump --dump-elf "$FINAL" | tee "${LOG_ROOT}/msobjdump.txt" || true
fi

# Independent load probe (FINAL vs TMP negative)
python3 - "${FINAL}" "${TMP}" "${LOG_ROOT}" <<'PY'
import ctypes, json, os, sys
from pathlib import Path

final_path = os.path.realpath(sys.argv[1])
tmp_path = os.path.realpath(sys.argv[2])
log_root = Path(sys.argv[3])

def try_load(path: str) -> dict:
    acl = ctypes.CDLL("libascendcl.so")
    acl.aclInit.restype = ctypes.c_int
    acl.aclInit.argtypes = [ctypes.c_char_p]
    acl.aclrtSetDevice.restype = ctypes.c_int
    acl.aclrtSetDevice.argtypes = [ctypes.c_int]
    acl.aclrtCreateContext.restype = ctypes.c_int
    acl.aclrtCreateContext.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]
    acl.aclrtBinaryLoadFromFile.restype = ctypes.c_int
    acl.aclrtBinaryLoadFromFile.argtypes = [
        ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
    ]
    acl.aclrtBinaryGetFunction.restype = ctypes.c_int
    acl.aclrtBinaryGetFunction.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)
    ]

    rc_init = acl.aclInit(None)
    rc_dev = acl.aclrtSetDevice(0)
    ctx = ctypes.c_void_p()
    rc_ctx = acl.aclrtCreateContext(ctypes.byref(ctx), 0)

    bin_handle = ctypes.c_void_p()
    load_rc = acl.aclrtBinaryLoadFromFile(path.encode(), None, ctypes.byref(bin_handle))
    func_handle = ctypes.c_void_p()
    get_rc = -1
    if load_rc == 0:
        get_rc = acl.aclrtBinaryGetFunction(
            bin_handle, b"d51_compute_delay_kernel", ctypes.byref(func_handle)
        )
    return {
        "path": path,
        "aclInit": rc_init,
        "aclrtSetDevice": rc_dev,
        "aclrtCreateContext": rc_ctx,
        "load_rc": load_rc,
        "get_function_rc": get_rc,
    }

out = {"FINAL": try_load(final_path), "TMP_negative": try_load(tmp_path)}
(log_root / "load_probe.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
if out["FINAL"]["load_rc"] != 0 or out["FINAL"]["get_function_rc"] != 0:
    sys.exit(3)
if out["TMP_negative"]["load_rc"] == 0:
    sys.exit(4)
PY
tee "${LOG_ROOT}/load_probe_stdout.txt"

# Smoke: non-zero iters, launch_count=1
SMOKE_DIR="${LOG_ROOT}/smoke"
mkdir -p "${SMOKE_DIR}"
export ACL_EVENT_WORK_BINARY="${FINAL}"
python3 "${ROOT}/smoke_device_work.py" \
  --trace-dir "${SMOKE_DIR}" \
  --preload-lib "${ROOT}/build/libacl_event_trace_v2.so" \
  --kernel-binary "${FINAL}" \
  --iters 0 50 \
  --timeout-s 120 \
  2>&1 | tee "${LOG_ROOT}/smoke.log"

# Summarize
python3 - <<PY
import json, glob
from pathlib import Path
log = Path("${LOG_ROOT}")
smoke = log / "smoke.log"
load = json.loads((log / "load_probe.json").read_text())
summary = {
    "run_id": "${RUN_ID}",
    "final_path": "${FINAL}",
    "load_rc": load["FINAL"]["load_rc"],
    "get_function_rc": load["FINAL"]["get_function_rc"],
    "tmp_load_rc": load["TMP_negative"]["load_rc"],
}
for p in sorted(glob.glob(str(Path("${SMOKE_DIR}") / "**" / "rank_*_pid_*.device_work_audit.json"), recursive=True)):
    audit = json.loads(Path(p).read_text())
    summary.setdefault("smoke_audits", []).append(audit)
(log / "kernel_artifact_manifest.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
PY

echo "ARTIFACT_PROBE_PASS"
