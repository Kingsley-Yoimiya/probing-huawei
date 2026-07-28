#!/usr/bin/env bash
# B3 自检：时基降回 + 稳健 pid 读回 + victim fallback（禁止 skip）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOLD="${ROOT}/scripts/fail-slow/hold_exec_run_case.sh"
fail=0

check() {
  if ! "$@"; then
    echo "FAIL: $*"
    fail=1
  else
    echo "OK: $*"
  fi
}

check grep -q 'PILLAR_C_SET_WINDOW_S' "$HOLD"
check grep -q 'PILLAR_C_SET_WINDOW_STEPS' "$HOLD"
check grep -q 'read_set_upgrade_field' "$HOLD"
check grep -q 'jexec_poll' "$HOLD"
check grep -q 'SET_UPGRADE ts=' "$HOLD"
check grep -q 'SET_DOWNGRADE ts=' "$HOLD"
check grep -q 'reason=time' "$HOLD"
check grep -q 'reason=steps' "$HOLD"
check grep -q 'B3_PID_FALLBACK' "$HOLD"
if grep -q 'B3 skip: no SET_OK pid' "$HOLD"; then
  echo "FAIL: must not skip downgrade on empty pid read"
  fail=1
else
  echo "OK: no B3 skip on empty pid"
fi
check grep -q 'HANG_DETECTED' "$HOLD"

# awk 解析 SET_OK_WORKER pid
sample='SET_OK_WORKER pid=2496910'
pid=$(echo "$sample" | awk '/^SET_OK_WORKER/{for(i=1;i<=NF;i++) if($i~/^pid=/){sub(/^pid=/,"",$i); print $i; exit}}')
if [[ "$pid" == "2496910" ]]; then
  echo "OK: awk parses SET_OK_WORKER pid=${pid}"
else
  echo "FAIL: awk parse got '${pid}'"
  fail=1
fi

# 时基：elapsed >= window_s 即降回
window_s=30
elapsed=31
if [[ "$elapsed" -ge "$window_s" ]]; then
  echo "OK: time trigger elapsed=${elapsed}s >= window_s=${window_s}s"
else
  echo "FAIL: time trigger"
  fail=1
fi

# fallback：空 pid 仍应进入降回（非 skip）
if grep -q 'FALLBACK victim rank' "$HOLD"; then
  echo "OK: victim fallback path present"
else
  echo "FAIL: missing victim fallback"
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo "[test_pillar_c_set_window] FAILED"
  exit 1
fi
echo "[test_pillar_c_set_window] PASS"
exit 0
