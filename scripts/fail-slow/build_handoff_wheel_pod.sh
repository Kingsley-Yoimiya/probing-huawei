#!/usr/bin/env bash
# handoff probing wheel：在 Ascend hold pod 内编译（正确 ABI / CANN 环境）
# 铁律：project/probing-huawei/docs/fail-slow/agents/BUILD_WHEEL.md
#       .cursor/rules/probing-wheel-build.mdc
#
# 用法（须显式 POD；优先自升 16 卡 yjr-*，勿默认 yysong）:
#   source project/probing-huawei/scripts/fail-slow/env.sh
#   POD=<your-yjr-16-or-IDLE-grj> bash project/probing-huawei/scripts/fail-slow/build_handoff_wheel_pod.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/fail-slow/env.sh"

# yysong 已不可用；GRJ 不一定空闲。必须显式 POD=（自升 16 或确认 IDLE 的 grj）。
if [[ -z "${POD:-}" ]]; then
  echo "[build-handoff] ERROR: set POD= to a self-raised 16-card pod or IDLE grj hold." >&2
  echo "  example: POD=grj-megatron-32card-0716-master-0 (only if pgrep IDLE)" >&2
  echo "  prefer: raise yjr-*-16 first for small agent builds" >&2
  exit 2
fi
if [[ "${POD}" == yysong-* ]]; then
  echo "[build-handoff] ERROR: yysong hold is unavailable; do not use ${POD}" >&2
  exit 2
fi
NS="${NS:-default}"
COMMIT="${HANDOFF_COMMIT:-bed9ee1}"
HANDOFF_REV="${HANDOFF_REV:-2}"   # PEP 427 local version: +handoff.bed9ee1.<rev> (.2 = lazy-hook fixed)
SRC="${AFS_PROBING}/handoff-src-${COMMIT}"
TARGET_DIR="${AFS_PROBING}/build-handoff-${COMMIT}/target"
WHEEL_DIR="${AFS_PROBING}/wheels"
WHEEL_OUT="${WHEEL_DIR}/probing-0.2.6+handoff.${COMMIT}.${HANDOFF_REV}-cp38-abi3-linux_aarch64.whl"
LOG="${AFS_PROBING}/build_handoff_${COMMIT}.log"
CARGO_AFS="${AFS_HOME}/toolchains/rust/cargo"
RUST_ENV="${AFS_HOME}/toolchains/rust-env.sh"
MATURIN_FEATURES="${MATURIN_FEATURES:-extension-module,gpu,kmsg}"
PYBIN="/root/miniconda3/envs/llm_test/bin"

remote() {
  ssh -o BatchMode=yes -o ConnectTimeout=60 "${JUMP_HOST}" \
    "export KUBECONFIG='${JUMP_KUBECONFIG}'; K='${JUMP_KUBECTL}'; \$K -n '${NS}' exec '${POD}' -- bash -lc $(printf '%q' "$1")"
}

echo "[build-handoff] pod=$POD commit=$COMMIT → ${WHEEL_OUT}"
case "${POD}" in
  *grj*|*geruijun*)
    echo "[build-handoff] GRJ pod detected — idle gate required"
    idle="$(remote "ps -eo pid,args | awk '/torchrun|pretrain_gpt.py|megatron/ && !/awk/ {print}' || true")"
    if [[ -n "${idle}" ]]; then
      echo "FATAL: GRJ pod not idle; refusing build:" >&2
      echo "${idle}" >&2
      exit 3
    fi
    echo "[build-handoff] GRJ idle OK"
    ;;
esac
remote "test -f '${SRC}/Cargo.toml'" || {
  echo "FATAL: 缺少源码 ${SRC}；本机 clone → tar → SSH 管道灌 AFS。" >&2
  exit 2
}

remote "
set -euo pipefail
exec >>'${LOG}' 2>&1
echo '===== pod build \$(date -Iseconds) pod=${POD} ====='
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
source /usr/local/Ascend/ascend-toolkit/set_env.sh 2>/dev/null || true
source /root/miniconda3/etc/profile.d/conda.sh && conda activate llm_test
source '${RUST_ENV}'
export CARGO_HOME='${CARGO_AFS}'
export CARGO_TARGET_DIR='${TARGET_DIR}'
export PATH='${PYBIN}':\"\$PATH\"
mkdir -p \"\$CARGO_HOME\" \"\$CARGO_TARGET_DIR\"
cat > \"\$CARGO_HOME/config.toml\" <<'CFG'
[source.crates-io]
replace-with = \"tuna\"
[source.tuna]
registry = \"sparse+https://mirrors.tuna.tsinghua.edu.cn/crates.io-index/\"
CFG
rustc -V
cargo -V
'${PYBIN}/python' -m maturin --version
cd '${SRC}'
export BUILD_START_EPOCH=\$(date +%s)
# 隔离本轮 dist，避免 head 选中旧 wheel
rm -rf dist
mkdir -p python/probing/bundled_web/public web/dist dist
echo '<!doctype html><title>probing</title>' > python/probing/bundled_web/public/index.html
cp python/probing/bundled_web/public/index.html web/dist/index.html
rm -rf python/probing/bundled_web && mkdir -p python/probing/bundled_web
cp -a web/dist/. python/probing/bundled_web/
cargo build -p probing-hccl-profapi --release
mkdir -p python/probing/shim/hccl
cp -f \"\$CARGO_TARGET_DIR/release/libprofapi.so\" python/probing/shim/hccl/
'${PYBIN}/python' -m maturin build --release --features '${MATURIN_FEATURES}' --out dist
# 仅接受本轮新建 wheel（mtime >= BUILD_START）；优先精确名匹配
WH=''
WANT_NAME=\"probing-0.2.6+handoff.${COMMIT}.${HANDOFF_REV}-cp38-abi3-linux_aarch64.whl\"
if [[ -f \"dist/\${WANT_NAME}\" ]]; then
  WH=\"dist/\${WANT_NAME}\"
else
  WH=\$(BUILD_START_EPOCH=\"\$BUILD_START_EPOCH\" '${PYBIN}/python' - <<'PY'
import os, glob
start = int(os.environ.get("BUILD_START_EPOCH", "0") or "0")
cands = []
for p in glob.glob("dist/probing-*.whl"):
    st = os.stat(p)
    if st.st_mtime >= start - 2:
        cands.append((st.st_mtime, p))
if not cands:
    raise SystemExit("no fresh wheel in dist/")
print(sorted(cands)[-1][1])
PY
)
fi
[[ -n \"\$WH\" && -f \"\$WH\" ]] || { echo FATAL_NO_FRESH_WHEEL; exit 4; }
cp -f \"\$WH\" '${WHEEL_OUT}'
'${PYBIN}/python' -m pip install -q --force-reinstall --no-deps '${WHEEL_OUT}'
PROBING=0 '${PYBIN}/python' -c 'import probing; print(\"import_ok\", probing.VERSION, probing.__file__)'
ls -lh '${WHEEL_OUT}'
echo WHEEL_OK '${WHEEL_OUT}' \"from=\$WH\"
"

echo "[build-handoff] DONE → ${WHEEL_OUT}"
echo "[build-handoff] 若 caseb-512 在 pjlab-new 集群，需另管道同步 wheel（见 probing-wheel-build 规则）"
