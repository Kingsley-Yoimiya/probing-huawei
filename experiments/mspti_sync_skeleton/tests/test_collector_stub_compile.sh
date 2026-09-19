#!/usr/bin/env bash
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP_DIR=$(mktemp -d)
trap 'rm -rf "${TMP_DIR}"' EXIT

"${CXX:-c++}" -std=c++17 -Wall -Wextra -Wpedantic -O2 -pthread \
  -I"${HERE}/tests/stubs" \
  "${HERE}/collector.cpp" "${HERE}/tests/test_collector_stub_smoke.cpp" \
  -o "${TMP_DIR}/collector_stub_smoke"

RUN_ID=collector-stub-smoke \
  "${TMP_DIR}/collector_stub_smoke" "${TMP_DIR}/rank_0000.skeleton.jsonl"

PYTHONPATH="${HERE}" python3 - "${TMP_DIR}/rank_0000.buffer_audit.json" <<'PY'
import json
import sys
from pathlib import Path

from buffer_audit import validate_rank_buffer_audit

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
errors = validate_rank_buffer_audit(payload, expected_rank=0)
if errors:
    raise SystemExit("\n".join(errors))
PY
