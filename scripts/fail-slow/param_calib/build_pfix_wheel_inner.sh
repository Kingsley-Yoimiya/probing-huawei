#!/usr/bin/env bash
# P-FIX：在 yysong-worker-2 上编 probing wheel（cpu.utilization 环默认 8MiB）
# 前提：RUSTUP_HOME 已有可用 stable（见 docs/fail-slow/agents/BUILD_WHEEL.md）。
# 禁止本脚本内 rustup toolchain install / curl static.rust-lang.org。
set -euo pipefail
SRC=/data/yinjinrun.p-huawei/probing-huawei/src
WHEEL_DIR=/data/yinjinrun.p-huawei/probing-huawei/wheels
PYDEPS=/data/yinjinrun.p-huawei/probe-bundle/pydeps
PYBIN=/root/miniconda3/envs/llm_test/bin
CARGO_HOME=/data/yinjinrun.p-huawei/probing-huawei/cargo
RUSTUP_HOME=/data/yinjinrun.p-huawei/probing-huawei/rustup
LOG=/data/yinjinrun.p-huawei/probing-huawei/build_pfix_wheel.log
MATURIN_FEATURES="${MATURIN_FEATURES:-extension-module,gpu,kmsg}"
mkdir -p "$WHEEL_DIR" "$PYDEPS" "$CARGO_HOME" "$RUSTUP_HOME" "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1
echo "===== pfix build start $(date -Iseconds) ====="
export CARGO_HOME RUSTUP_HOME
export PATH="$CARGO_HOME/bin:$PYBIN:$PATH"
# shellcheck disable=SC1091
[ -f "$CARGO_HOME/env" ] && source "$CARGO_HOME/env"
if ! command -v rustc >/dev/null 2>&1 || ! rustc --version >/dev/null 2>&1; then
  echo "FATAL: rustc missing/broken — ferry toolchain (docs/fail-slow/agents/BUILD_WHEEL.md); do not rustup install in-pod" >&2
  exit 91
fi
rustc --version
cargo --version
cd "$SRC"
mkdir -p web/dist
[ -f web/dist/index.html ] || echo '<!doctype html><title>probing</title>' > web/dist/index.html
find python -name '._*' -delete 2>/dev/null || true
rm -rf python/probing/bundled_web
mkdir -p python/probing/bundled_web
cp -a web/dist/. python/probing/bundled_web/
test -f python/probing/__init__.py
grep -q PROBING_CPU_RING_MB probing/extensions/cc/src/extensions/cpu/collector.rs
"$PYBIN/python" -m pip install -q -U pip maturin build wheel toml
mkdir -p dist
"$PYBIN/python" -m maturin build --release --features "$MATURIN_FEATURES" --out dist
WH=$(ls -1t dist/probing-*.whl | head -1)
test -n "$WH"
cp -f "$WH" "$WHEEL_DIR/"
"$PYBIN/python" -m pip install --target="$PYDEPS" --force-reinstall --no-deps "$WH"
"$PYBIN/python" -m pip install --force-reinstall --no-deps "$WH"
PROBING=0 PYTHONPATH="$PYDEPS" "$PYBIN/python" -c 'import probing; print("import_ok", probing.__file__)'
cp -f "$WH" /tmp/probing_pfix.whl
ls -lh "$WH" /tmp/probing_pfix.whl
echo "WHEEL_OK $WH"
echo "===== pfix build done $(date -Iseconds) ====="
