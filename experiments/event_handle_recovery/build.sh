#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${ROOT}/build"
mkdir -p "${BUILD_DIR}"

MODE="${1:-local}"

WRAP_FLAGS=()
if [[ "$(uname -s)" == "Linux" ]]; then
  WRAP_FLAGS=(-DACL_EVENT_TRACE_USE_WRAP)
fi

if [[ "${MODE}" == "local" ]]; then
  echo "[build] local fake_acl + interpose unit test (v2)"
  gcc -O2 -fPIC -fno-builtin -fno-builtin-dlsym -fno-builtin-dlvsym \
    -shared -Wl,-z,now -Wl,-Bsymbolic \
    "${ROOT}/real_dlsym_resolver.c" -ldl \
    -o "${BUILD_DIR}/libacl_real_dlsym.so"
  g++ -O2 -std=c++17 -fPIC -shared -fno-exceptions -fno-rtti \
    -DACL_EVENT_TRACE_USE_STUB \
    -DACL_EVENT_TRACE_USE_WRAP \
    -I"${ROOT}/tests/stubs" \
    "${ROOT}/event_interpose.cpp" \
    "${ROOT}/device_work_stub.cpp" \
    -L"${BUILD_DIR}" -lacl_real_dlsym \
    -Wl,-rpath,"${BUILD_DIR}" \
    -ldl -pthread \
    -o "${BUILD_DIR}/libacl_event_trace_v2_stub.so"
  g++ -O2 -std=c++17 -fPIC -shared -fno-exceptions -fno-rtti \
    -Wl,-Bsymbolic \
    "${ROOT}/tests/fake_acl.cpp" \
    -o "${BUILD_DIR}/libfake_acl.so"
  g++ -O2 -std=c++17 -fPIC -shared -fno-exceptions -fno-rtti \
    "${ROOT}/tests/fake_rt.cpp" \
    -o "${BUILD_DIR}/libfake_rt.so"
  g++ -O2 -std=c++17 -fPIC -shared -fno-exceptions -fno-rtti \
    "${ROOT}/tests/fake_loader.cpp" \
    "${BUILD_DIR}/libfake_acl.so" \
    -ldl \
    -o "${BUILD_DIR}/libfake_loader.so"
  SMOKE_LDFLAGS=(-Wl,-rpath,"${BUILD_DIR}")
  g++ -O2 -std=c++17 \
    "${ROOT}/tests/event_sequence_smoke.cpp" \
    "${BUILD_DIR}/libacl_event_trace_v2_stub.so" \
    "${BUILD_DIR}/libfake_acl.so" \
    "${BUILD_DIR}/libacl_real_dlsym.so" \
    "${SMOKE_LDFLAGS[@]}" \
    -ldl -pthread \
    -o "${BUILD_DIR}/event_sequence_smoke"
  g++ -O2 -std=c++17 \
    "${ROOT}/tests/event_delay_v3_unit.cpp" \
    "${BUILD_DIR}/libacl_event_trace_v2_stub.so" \
    "${BUILD_DIR}/libfake_acl.so" \
    "${BUILD_DIR}/libacl_real_dlsym.so" \
    "${SMOKE_LDFLAGS[@]}" \
    -ldl -pthread \
    -o "${BUILD_DIR}/event_delay_v3_unit"
  g++ -O2 -std=c++17 \
    "${ROOT}/tests/test_wait_dag_v4_6_unit.cpp" \
    "${BUILD_DIR}/libacl_event_trace_v2_stub.so" \
    "${BUILD_DIR}/libfake_acl.so" \
    "${BUILD_DIR}/libacl_real_dlsym.so" \
    "${SMOKE_LDFLAGS[@]}" \
    -ldl -pthread \
    -o "${BUILD_DIR}/test_wait_dag_v4_6_unit"
  gcc -O2 "${ROOT}/min_create.c" -ldl \
    -o "${BUILD_DIR}/min_create"
  echo "[build] done -> ${BUILD_DIR}"
  exit 0
fi

if [[ "${MODE}" == "ascend" ]]; then
  ASCEND_HOME="${ASCEND_HOME:-/usr/local/Ascend/ascend-toolkit/latest}"
  ASCEND_INC="${ASCEND_HOME}/include"
  RT_INC="$(find /usr/local/Ascend -path '*/pkg_inc' -type d 2>/dev/null | head -1)"
  if [[ -z "${RT_INC}" ]]; then
    RT_INC="${ASCEND_HOME}/../cann-8.5.1/x86_64-linux/pkg_inc"
  fi
  echo "[build] ascend preload v2 include=${ASCEND_INC} rt_inc=${RT_INC}"
  gcc -O2 -fPIC -fno-builtin -fno-builtin-dlsym -fno-builtin-dlvsym \
    -shared -Wl,-z,now -Wl,-Bsymbolic \
    "${ROOT}/real_dlsym_resolver.c" -ldl \
    -o "${BUILD_DIR}/libacl_real_dlsym.so"
  g++ -O2 -std=c++17 -fPIC -shared -fno-exceptions -fno-rtti \
    -DACL_EVENT_TRACE_RT_ABI \
    -DACL_EVENT_TRACE_USE_WRAP \
    -I"${ASCEND_INC}" \
    -I"${RT_INC}" \
    -I"${RT_INC}/profiling" \
    -I"${ROOT}" \
    "${ROOT}/event_interpose.cpp" \
    "${ROOT}/device_work.cpp" \
    -L"${BUILD_DIR}" -lacl_real_dlsym \
    -Wl,-rpath,"${BUILD_DIR}" \
    -ldl -pthread \
    -o "${BUILD_DIR}/libacl_event_trace_v2.so"
  gcc -O2 "${ROOT}/min_create.c" -ldl \
    -o "${BUILD_DIR}/min_create"
  echo "[build] done -> ${BUILD_DIR}/libacl_event_trace_v2.so"
  exit 0
fi

echo "usage: $0 [local|ascend]" >&2
exit 1
