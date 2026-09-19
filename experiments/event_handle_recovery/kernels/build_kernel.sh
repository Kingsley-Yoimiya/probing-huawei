#!/usr/bin/env bash
# Build d51_compute_delay_kernel: ccec -c (ET_REL) -> ld.lld (ET_EXEC).
# V4.4: production + audit negative control + disasm artifacts.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="${ROOT}/.."
export PYTHONPATH="${EXP_ROOT}:${PYTHONPATH:-}"
OUT_DIR="${EXP_ROOT}/build/kernels"
KERNEL_SRC="${ROOT}/d51_compute_delay_kernel.cpp"
NEG_SRC="${ROOT}/d51_compute_delay_kernel_audit_neg.cpp"
TMP="${OUT_DIR}/d51_compute_delay_kernel_tmp.o"
FINAL="${OUT_DIR}/d51_compute_delay_kernel.o"
NEG_TMP="${OUT_DIR}/d51_compute_delay_kernel_audit_neg_tmp.o"
NEG_FINAL="${OUT_DIR}/d51_compute_delay_kernel_audit_neg.o"
DISASM="${OUT_DIR}/d51_compute_delay_kernel.disasm.txt"
NEG_DISASM="${OUT_DIR}/d51_compute_delay_kernel_audit_neg.disasm.txt"

mkdir -p "${OUT_DIR}"

CANN="${CANN:-/usr/local/Ascend/cann-8.5.1}"
CCEC="${CCEC:-${CANN}/bin/ccec}"
LLD="${LLD:-${CANN}/bin/ld.lld}"

if [[ ! -x "${CCEC}" ]]; then
  CCEC="$(command -v ccec || command -v bisheng || true)"
fi
if [[ ! -x "${LLD}" ]]; then
  LLD="$(command -v ld.lld || true)"
fi
if [[ -z "${CCEC}" || ! -x "${CCEC}" ]]; then
  echo "ccec/bisheng not found" >&2
  exit 1
fi
if [[ -z "${LLD}" || ! -x "${LLD}" ]]; then
  echo "ld.lld not found (CANN_8_5_1_AICORE_LINKER_MISSING)" >&2
  exit 1
fi

if [[ -f /usr/local/Ascend/ascend-toolkit/latest/set_env.sh ]]; then
  # shellcheck disable=SC1091
  source /usr/local/Ascend/ascend-toolkit/latest/set_env.sh
fi

ASCEND_HOME="${ASCEND_HOME:-/usr/local/Ascend/ascend-toolkit/latest}"

INC=(
  "-I${ROOT}"
  "-I${ASCEND_HOME}/include"
  "-I${ASCEND_HOME}/compiler/tikcpp"
  "-I${ASCEND_HOME}/compiler/tikcpp/tikcfw"
  "-I${ASCEND_HOME}/compiler/tikcpp/tikcfw/impl"
  "-I${ASCEND_HOME}/compiler/tikcpp/tikcfw/interface"
)

ARCH="${D51_KERNEL_ARCH:-dav-c220-vec}"
CCEC_FLAGS=(
  -c -x cce -O2 -std=c++17
  --cce-aicore-only
  "--cce-aicore-arch=${ARCH}"
  -mllvm -cce-aicore-stack-size=0x8000
  -mllvm -cce-aicore-function-stack-size=0x8000
)

build_one() {
  local src="$1" tmp="$2" final="$3" disasm="$4"
  echo "[kernel] build ${src} -> ${final}"
  "${CCEC}" "${CCEC_FLAGS[@]}" "${INC[@]}" "${src}" -o "${tmp}" 2>&1 | tee "${OUT_DIR}/$(basename "${src}" .cpp).ccec.log"
  if grep -qiE 'attribute.*ignored|unknown attribute|optnone.*not supported' "${OUT_DIR}/$(basename "${src}" .cpp).ccec.log"; then
    echo "STOP_KERNEL_ANTI_ELIMINATION_UNSUPPORTED: compiler ignored anti-elimination attribute" >&2
    exit 42
  fi
  "${LLD}" -m aicorelinux -Ttext=0 "${tmp}" -static -o "${final}"
  if command -v llvm-objdump >/dev/null 2>&1; then
    llvm-objdump -d "${final}" > "${disasm}" 2>/dev/null || objdump -d "${final}" > "${disasm}" 2>/dev/null || true
  elif command -v objdump >/dev/null 2>&1; then
    objdump -d "${final}" > "${disasm}" 2>/dev/null || true
  fi
}

echo "[kernel] ccec=${CCEC} lld=${LLD} arch=${ARCH}"
build_one "${KERNEL_SRC}" "${TMP}" "${FINAL}" "${DISASM}"
build_one "${NEG_SRC}" "${NEG_TMP}" "${NEG_FINAL}" "${NEG_DISASM}"

echo "[kernel] artifact verification:"
file "${TMP}" "${FINAL}" "${NEG_TMP}" "${NEG_FINAL}"
readelf -h "${TMP}" | grep -E 'Type:|Machine:'
readelf -h "${FINAL}" | grep -E 'Type:|Machine:'
readelf -SW "${FINAL}" | grep -E 'ascend\.meta|\.text' || true
readelf -Ws "${FINAL}" | grep d51_compute_delay_kernel || true
# V4.5: Size column (not Offset); use header-aware parser
TEXT_SIZE=$(python3 -c "from elf_section_parser import read_text_size; print(f'{read_text_size(\"${FINAL}\"):#x}')")
NEG_TEXT_SIZE=$(python3 -c "from elf_section_parser import read_text_size; print(f'{read_text_size(\"${NEG_FINAL}\"):#x}')")
echo "[kernel] production .text=${TEXT_SIZE} neg .text=${NEG_TEXT_SIZE}"
sha256sum "${KERNEL_SRC}" "${NEG_SRC}" "${TMP}" "${FINAL}" "${NEG_TMP}" "${NEG_FINAL}" "${DISASM}" "${NEG_DISASM}" 2>/dev/null || true
ls -la "${TMP}" "${FINAL}" "${NEG_TMP}" "${NEG_FINAL}" "${DISASM}" "${NEG_DISASM}" 2>/dev/null || true
