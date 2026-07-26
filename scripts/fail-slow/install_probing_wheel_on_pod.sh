#!/usr/bin/env bash
# 在 yysong hold-exec pod 内构建并安装 probing-huawei wheel（aarch64 / NPU）。
# 源码：/data/yinjinrun.p-huawei/probing-huawei/src
# 产物：wheels/ + probe-bundle/pydeps + llm_test site-packages
#
# 铁律（docs/fail-slow/agents/BUILD_WHEEL.md）：
#   禁止在 pod 内从公网 rustup/curl 装工具链；缺 rustc → 本机 Clash 摆渡后再跑。
#
#   source scripts/fail-slow/env.sh
#   bash scripts/fail-slow/install_probing_wheel_on_pod.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/fail-slow/env.sh"

POD="${POD:-${FS_HOLD_PODS_CASE:-yysong-master-0}}"
NS="${NS:-default}"
REMOTE_BUILD="/data/yinjinrun.p-huawei/probing-huawei/build_wheel_inner.sh"

jsync_file() {
  local src="$1" dst="$2"
  local bname ddir
  bname=$(basename "$src")
  ddir=$(dirname "$dst")
  tar -C "$(dirname "$src")" -cf - "$bname" \
    | ssh -o BatchMode=yes -o ConnectTimeout=60 "${JUMP_HOST}" \
      "export KUBECONFIG='${JUMP_KUBECONFIG}'; K='${JUMP_KUBECTL}'; \$K -n '${NS}' exec -i '${POD}' -- bash -lc $(printf '%q' "mkdir -p '$ddir' /tmp/yjr_sync && tar -C /tmp/yjr_sync -xf - && install -m 0755 /tmp/yjr_sync/$bname '$dst' && rm -f /tmp/yjr_sync/$bname")"
}

cat >"/tmp/build_wheel_inner.sh" <<'INNER'
#!/usr/bin/env bash
set -euo pipefail
SRC="${SRC:-/data/yinjinrun.p-huawei/probing-huawei/src}"
WHEEL_DIR="${WHEEL_DIR:-/data/yinjinrun.p-huawei/probing-huawei/wheels}"
PYDEPS="${PYDEPS:-/data/yinjinrun.p-huawei/probe-bundle/pydeps}"
PYBIN="${PYBIN:-/root/miniconda3/envs/llm_test/bin}"
# NPU：不必编 gpu-cuda
MATURIN_FEATURES="${MATURIN_FEATURES:-extension-module,gpu,kmsg}"
LOG="${LOG:-/data/yinjinrun.p-huawei/probing-huawei/build_wheel.log}"
CARGO_HOME="${CARGO_HOME:-/data/yinjinrun.p-huawei/probing-huawei/cargo}"
RUSTUP_HOME="${RUSTUP_HOME:-/data/yinjinrun.p-huawei/probing-huawei/rustup}"

mkdir -p "$WHEEL_DIR" "$PYDEPS" "$CARGO_HOME" "$RUSTUP_HOME" "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1
echo "===== build start $(date -Iseconds) ====="
test -f "$SRC/Cargo.toml"
cd "$SRC"
mkdir -p web/dist
[ -f web/dist/index.html ] || echo '<!doctype html><title>probing</title>' > web/dist/index.html

export CARGO_HOME RUSTUP_HOME
export PATH="$CARGO_HOME/bin:${PYBIN}:$PATH"

# shellcheck disable=SC1091
[ -f "$CARGO_HOME/env" ] && source "$CARGO_HOME/env"
export PATH="$CARGO_HOME/bin:$PATH"
if ! command -v rustc >/dev/null 2>&1 || ! rustc --version >/dev/null 2>&1; then
  echo "FATAL: pod 内 rustc 不可用。禁止在此 curl/rustup 重装（集群 egress 极慢）。" >&2
  echo "见 docs/fail-slow/agents/BUILD_WHEEL.md：Mac Clash 摆渡 toolchain 或 linux_aarch64.whl 后再编。" >&2
  exit 91
fi
rustc --version
cargo --version

"${PYBIN}/python" -m pip install -q -U pip maturin build wheel toml

# skills/ is a symlink → python/probing/bundled_skills (do not cp onto itself)
find python -name '._*' -delete 2>/dev/null || true
mkdir -p web/dist
[ -f web/dist/index.html ] || echo '<!doctype html><title>probing</title>' > web/dist/index.html
rm -rf python/probing/bundled_web
mkdir -p python/probing/bundled_web
cp -a web/dist/. python/probing/bundled_web/
if [ -L python/probing/bundled_skills ]; then
  echo "FATAL: bundled_skills is symlink (expected real dir)" >&2
  ls -la python/probing/bundled_skills; exit 2
fi
test -f python/probing/bundled_skills/catalog.yaml
test -f python/probing/__init__.py
test -f python/probing/bundled_web/index.html

mkdir -p dist
echo "[build] maturin features=$MATURIN_FEATURES"
"${PYBIN}/python" -m maturin build --release --features "$MATURIN_FEATURES" --out dist

WH=$(ls -1 dist/probing-*.whl | head -1)
test -n "$WH"
cp -f "$WH" "$WHEEL_DIR/"
echo "[build] wheel=$WH → $WHEEL_DIR/"

"${PYBIN}/python" -m pip install --target="$PYDEPS" --force-reinstall --no-deps "$WH"
"${PYBIN}/python" -m pip install --force-reinstall --no-deps "$WH"

PROBING=0 PYTHONPATH="$PYDEPS" "${PYBIN}/python" -c 'import probing; print("import_ok", probing.__file__)'
PROBING=0 "${PYBIN}/python" -c 'import probing; print("env_import_ok", probing.__file__)'
ls -lh "$PYDEPS/probing/"*_core* 2>/dev/null || ls -lh "$PYDEPS/probing/"*.so 2>/dev/null || true
echo "===== build done $(date -Iseconds) ====="
echo "WHEEL_OK $WH"
INNER

echo "[install-wheel] sync inner → $REMOTE_BUILD on $POD"
jsync_file /tmp/build_wheel_inner.sh "$REMOTE_BUILD"
echo "[install-wheel] building (may take 10–30 min)…"
ssh -o BatchMode=yes -o ConnectTimeout=60 -o ServerAliveInterval=30 "${JUMP_HOST}" \
  "export KUBECONFIG='${JUMP_KUBECONFIG}'; K='${JUMP_KUBECTL}'; \$K -n '${NS}' exec '${POD}' -- bash -lc $(printf '%q' "bash '$REMOTE_BUILD'")"
echo "[install-wheel] DONE"
