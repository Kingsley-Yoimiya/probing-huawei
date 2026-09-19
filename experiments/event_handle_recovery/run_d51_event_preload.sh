#!/usr/bin/env bash
# D51 event preload V2 end-to-end runner (container-internal).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${ROOT}/build"
HELPER_SO="${BUILD_DIR}/libacl_real_dlsym.so"
V2_SO="${BUILD_DIR}/libacl_event_trace_v2.so"
V2_STUB_SO="${BUILD_DIR}/libacl_event_trace_v2_stub.so"
FAKE_ACL_SO="${BUILD_DIR}/libfake_acl.so"
PRELOAD_ASCEND="${HELPER_SO}:${V2_SO}"
PRELOAD_UNIT="${HELPER_SO}:${V2_STUB_SO}:${FAKE_ACL_SO}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)_d51_event_preload_v2}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORKDIR="/tmp/${RUN_ID}"
PERSIST="${HOME}/spindle_trace_runs/${RUN_ID}"
LOGDIR="${WORKDIR}/logs/${STAMP}"
mkdir -p "${LOGDIR}" "${PERSIST}" "${WORKDIR}/nm" "${WORKDIR}/analysis"

exec > >(tee -a "${LOGDIR}/runner.log") 2>&1
echo "RUN_ID=${RUN_ID} LOGDIR=${LOGDIR} WORKDIR=${WORKDIR}"

run_stage() {
  local name="$1"
  shift
  echo "===== STAGE ${name} ====="
  "$@"
}

run_stage preflight bash -c "
set -euo pipefail
{
  echo STAMP=${STAMP}
  echo RUN_ID=${RUN_ID}
  echo HOST=\$(hostname)
  echo USER=\$(whoami)
  echo HOME=${HOME}
  echo LD_PRELOAD=\${LD_PRELOAD:-}
  npu-smi info 2>&1 | head -40 || true
  python3 -c 'import torch,torch_npu; print(torch.__version__, torch_npu.__version__)'
  df -h /tmp ${HOME}
  npu-smi info -l | grep -c Ascend || true
  docker top montyyin_reduce_ws16 2>/dev/null | head -5 || true
} | tee ${LOGDIR}/preflight.log
"

if [[ -n "${LD_PRELOAD:-}" ]]; then
  echo "ERROR: foreign LD_PRELOAD=${LD_PRELOAD}" | tee -a "${LOGDIR}/preflight.log"
  exit 2
fi

TORCH_NPU_SO="$(python3 -c 'import torch_npu, pathlib; print(pathlib.Path(torch_npu.__file__).parent / "lib/libtorch_npu.so")')"
ASCEND_CL_SO="/usr/local/Ascend/ascend-toolkit/latest/lib64/libacl_rt.so"
ASCEND_IMPL_SO="/usr/local/Ascend/ascend-toolkit/latest/lib64/libacl_rt_impl.so"
ASCEND_INC="/usr/local/Ascend/ascend-toolkit/latest/include"
{
  for sym in aclrtRecordEvent aclrtStreamWaitEvent aclrtCreateEvent aclrtCreateEventWithFlag aclrtCreateEventExWithFlag; do
    echo "=== torch_npu U ${sym} ==="
    nm -D --undefined-only "${TORCH_NPU_SO}" 2>/dev/null | grep -w "${sym}" || echo "absent"
    echo "=== libacl_rt T/U ${sym} ==="
    nm -D "${ASCEND_CL_SO}" 2>/dev/null | grep -w "${sym}" || echo "absent"
  done
  echo "=== libacl_rt_impl rtEventCreate* ==="
  nm -D "${ASCEND_IMPL_SO}" 2>/dev/null | grep rtEventCreate || true
  readelf -Ws /lib64/libc.so.6 2>/dev/null | grep -E ' dlsym$| dlvsym$' | head -10 || true
} | tee "${WORKDIR}/nm/symbols.log"

run_stage build bash -c "
set -euo pipefail
cd '${ROOT}'
ASCEND_HOME=/usr/local/Ascend/ascend-toolkit/latest ./build.sh ascend 2>&1 | tee '${LOGDIR}/build.log'
nm -D '${ROOT}/build/libacl_real_dlsym.so' | grep ' dlsym' && exit 11 || true
nm -D '${ROOT}/build/libacl_real_dlsym.so' | grep ' dlvsym' && exit 12 || true
echo 'HELPER_SO_EXPORT_CHECK=PASS' | tee -a '${LOGDIR}/build.log'
"

run_stage min_create bash -c "
set -euo pipefail
export LD_PRELOAD='${PRELOAD_ASCEND}'
timeout 2 '${ROOT}/build/min_create' 2>&1 | tee '${LOGDIR}/min_create.log'
"

run_stage unit bash -c "
set -euo pipefail
cd '${ROOT}'
./build.sh local 2>&1 | tee '${LOGDIR}/unit_build.log'
export LD_LIBRARY_PATH='${ROOT}/build':\${LD_LIBRARY_PATH:-}
LD_PRELOAD='${PRELOAD_UNIT}' \
  '${ROOT}/build/event_sequence_smoke' 2>&1 | tee '${LOGDIR}/unit.log'
"

SMOKE_PASS=0
run_stage acl_smoke bash -c "
set -euo pipefail
export ACL_EVENT_TRACE_DIR='${WORKDIR}/smoke_trace'
mkdir -p '${WORKDIR}/smoke_trace'
export LD_PRELOAD='${PRELOAD_ASCEND}'
export LD_DEBUG=bindings
python3 '${ROOT}/smoke_event.py' \
  --trace-dir '${WORKDIR}/smoke_trace' \
  --preload-lib '${V2_SO}' \
  --timeout-s 60 2>&1 | tee '${LOGDIR}/smoke.log' | tee '${LOGDIR}/ld_debug.log'
" && SMOKE_PASS=1 || SMOKE_PASS=0

if [[ "${SMOKE_PASS}" != "1" ]]; then
  echo "SMOKE_GATE_FAIL: skip 16-rank train" | tee -a "${LOGDIR}/runner.log"
  exit 3
fi

run_stage train bash -c "
set -euo pipefail
export ACL_EVENT_TRACE_DIR='${WORKDIR}/event_trace'
export LD_PRELOAD='${PRELOAD_ASCEND}'
mkdir -p '${WORKDIR}/event_trace' '${WORKDIR}/out'
cat > '${WORKDIR}/run.json' <<JSON
{\"run_id\":\"${RUN_ID}\",\"expected_train_s\":\"25-180\",\"world_size\":16,\"smoke_s\":\"<60\",\"total_s\":\"60-100min\"}
JSON
(
  timeout 1200 torchrun --nproc_per_node=16 --master_port=29541 \
    '${ROOT}/train_event_preload.py' \
    --output '${WORKDIR}/out' \
    --trace-dir '${WORKDIR}/event_trace' \
    --preload-lib '${V2_SO}' \
) 2>&1 | tee '${LOGDIR}/torchrun.log'
"

run_stage analysis bash -c "
set -euo pipefail
python3 '${ROOT}/analyze_event_pairs.py' \
  --trace-dir '${WORKDIR}/event_trace' \
  --profiler-out '${WORKDIR}/out/args_on' \
  --analysis-dir '${WORKDIR}/analysis' \
  --profile-window '${WORKDIR}/out/args_on/profile_window.json' \
  2>&1 | tee '${LOGDIR}/analysis.log'
"

run_stage transfer bash -c "
set -euo pipefail
mkdir -p '${PERSIST}/logs/${STAMP}'
if command -v rsync >/dev/null 2>&1; then
  rsync -a '${WORKDIR}/' '${PERSIST}/'
else
  cp -a '${WORKDIR}/.' '${PERSIST}/'
fi
( cd '${PERSIST}' && find . -type f -print0 | sort -z | xargs -0 sha256sum ) > '${PERSIST}/sha256sums.txt'
cp '${LOGDIR}/runner.log' '${PERSIST}/logs/${STAMP}/runner.log' || true
echo PERSIST=${PERSIST} | tee '${LOGDIR}/transfer.log'
"

echo "DONE RUN_ID=${RUN_ID}"
