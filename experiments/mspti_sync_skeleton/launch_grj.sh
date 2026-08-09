#!/usr/bin/env bash
# 在明确授权且空闲的 GRJ hold pod 上，执行有界 1/2 节点 MSPTI 采集。
# setsid + 每 pod 自身 node_${id}.pgid；失败/timeout 仅杀本 attempt PID 树并立即回拉。
# 内容寻址 CODE_DIR，避免持久 AFS 旧代码。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXP_LOCAL="${ROOT}/experiments/mspti_sync_skeleton"
JUMP="${JUMP:-ais-cf3e61a5}"
KUBE="${KUBE:-/tmp/config-vc-a3-241ceshi-songyiyang.yaml}"
KUBECTL="${KUBECTL:-/root/.cache/volcano/kubectl/kubectl}"
NS="${NS:-default}"
MASTER_POD="${MASTER_POD:-grj-megatron-32card-0716-master-0}"
WORKER_POD="${WORKER_POD:-grj-megatron-32card-0716-worker-0}"
NNODES="${NNODES:-1}"
NPROC="${NPROC:-1}"
CAPTURE_RANKS="${CAPTURE_RANKS:-0}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)-mspti-sync-${NNODES}x${NPROC}-${CAPTURE_RANKS//,/x}}"
MASTER_PORT="${MASTER_PORT:-29931}"
AFS_ROOT="/afs-a3-weight-share/yinjinrun.p-huawei"
OUT_DIR="${AFS_ROOT}/results/mspti-sync-skeleton/${RUN_ID}"
BACKUP_PARENT="${BACKUP_PARENT:-/Users/yinjinrun/Codespace/myportal/results/huawei-a3-32/mspti-sync-skeleton}"
BACKUP_ROOT="${BACKUP_ROOT:-${BACKUP_PARENT}/${RUN_ID}}"
CLAIM_PARENT="${CLAIM_PARENT:-${BACKUP_PARENT}/.group_claims}"
LOG_ROOT="${LOG_ROOT:-/Users/yinjinrun/Codespace/myportal/logs}"
LOG_DIR="${LOG_DIR:-${LOG_ROOT}/mspti-grj-${RUN_ID}}"
RUN_MARKER="MSPTI_GRJ_${RUN_ID}"
LOCAL_GROUP_CLAIMED=0
GROUP_ID="${GROUP_ID:-${RUN_ID}}"

# 内容寻址 CODE_DIR
CODE_HASH="$(
  cd "${EXP_LOCAL}" && python3 - <<'PY'
import hashlib
from pathlib import Path
h = hashlib.sha256()
for p in sorted(Path('.').glob('*')):
    if p.is_file() and p.suffix in {'.cpp','.hpp','.h','.py','.sh','.md'} or p.name=='CMakeLists.txt':
        h.update(p.name.encode()); h.update(p.read_bytes())
print(h.hexdigest()[:16])
PY
)"
CODE_DIR="${AFS_ROOT}/probing-huawei/experiments/mspti_sync_skeleton-${CODE_HASH}"

if [[ "${NNODES}" != "1" && "${NNODES}" != "2" ]]; then
  echo "NNODES 必须是 1 或 2" >&2
  exit 2
fi
if [[ "${NNODES}" == "2" && "${NPROC}" != "16" ]]; then
  echo "双节点正式运行要求 NPROC=16" >&2
  exit 2
fi

# 本机 run/log 原子认领：O_CREAT|O_EXCL → 单层 mkdir（无 -p）。
claim_local_group_dirs() {
  python3 "${EXP_LOCAL}/local_group_guard.py" claim \
    --group-id "${GROUP_ID}" \
    --claim-parent "${CLAIM_PARENT}" \
    --backup-root "${BACKUP_ROOT}" \
    --log-dir "${LOG_DIR}"
}

jump() {
  ssh "${JUMP}" "export KUBECONFIG='${KUBE}'; K='${KUBECTL}'; $*"
}

pod_exec() {
  local pod="$1" cmd="$2"
  jump "\$K exec -n '${NS}' '${pod}' -- bash --noprofile --norc -lc $(printf '%q' "${cmd}")"
}

active_processes() {
  local pod="$1"
  pod_exec "${pod}" "python3 '${CODE_DIR}/opponent_check.py' --mode idle"
}

check_idle() {
  # Fail-closed：检查本身失败 ≠ 空闲。CLEAR=0 OPPONENT=10 CHECK_FAILED=20。
  local pod="$1" active rc
  set +e
  active="$(pod_exec "${pod}" "python3 '${CODE_DIR}/opponent_check.py' --mode idle")"
  rc=$?
  set -e
  if (( rc != 0 && rc != 10 && rc != 20 )); then
    echo "FATAL: idle check FAILED (SSH/kubectl) pod=${pod} rc=${rc}" >&2
    echo "YIELD_CHECK_FAILED stage=pre_start pod=${pod}" >&2
    return 20
  fi
  if (( rc == 20 )); then
    echo "FATAL: idle CHECK_FAILED pod=${pod} out=${active}" >&2
    return 20
  fi
  if (( rc == 10 )); then
    echo "FATAL: ${pod} 非空闲（OPPONENT）：" >&2
    echo "${active}" >&2
    return 10
  fi
  local last
  last="$(printf '%s\n' "${active}" | awk 'NF{p=$0} END{print p}')"
  if [[ -z "${last}" || ( "${last}" != "CLEAR" && "${last}" != "OK" ) ]]; then
    echo "FATAL: idle empty/unknown stdout → CHECK_FAILED pod=${pod} out=${active}" >&2
    return 20
  fi
  return 0
}

require_idle_or_invalid() {
  local pod="$1" stage="${2:-pre_start}"
  local rc
  set +e
  check_idle "${pod}"
  rc=$?
  set -e
  if (( rc == 0 )); then
    return 0
  fi
  local reason="yield_opponent"
  if (( rc == 20 )); then
    reason="YIELD_CHECK_FAILED"
  fi
  echo "FATAL: ${reason} at ${stage} pod=${pod}" >&2
  # 未完整 claim 时禁止写旧目录 / GROUP_INVALID。
  if (( LOCAL_GROUP_CLAIMED == 1 )); then
    printf '%s\n' "{\"run_id\":\"${RUN_ID}\",\"status\":\"GROUP_INVALID\",\"reason\":\"${reason}\",\"stage\":\"${stage}\"}" \
      >"${BACKUP_ROOT}/GROUP_INVALID.json"
  fi
  exit "${rc}"
}

kill_our_attempt() {
  local out="$1"
  local marker="${2:-${RUN_MARKER}}"
  local node_id=0
  local pods=("${MASTER_POD}")
  local cleanup_rc=0
  if [[ -z "${marker}" || ${#marker} -lt 8 ]]; then
    echo "CLEANUP_REJECTED_MARKER empty_or_short" >&2
    return 2
  fi
  if [[ "${NNODES}" == "2" ]]; then
    pods+=("${WORKER_POD}")
  fi
  for pod in "${pods[@]}"; do
    set +e
    pod_exec "${pod}" \
      "python3 '${CODE_DIR}/kill_attempt.py' --out-dir '${out}' --node-id ${node_id} --run-marker '${marker}'"
    local rc=$?
    set -e
    if (( rc != 0 )); then
      echo "CLEANUP_INCOMPLETE pod=${pod} node=${node_id} rc=${rc}" >&2
      cleanup_rc=1
    fi
    node_id=$((node_id + 1))
  done
  return "${cleanup_rc}"
}

# Continuous yield: shared opponent_check.py（subprocess.run ps + returncode）。
# 三态 CLEAR(0) / OPPONENT(10) / CHECK_FAILED(20)；禁止 || true / os.popen。
yield_if_opponent() {
  local out="$1" marker="${2:-${RUN_MARKER}}"
  local pod node_id=0
  local pods=("${MASTER_POD}")
  if [[ "${NNODES}" == "2" ]]; then
    pods+=("${WORKER_POD}")
  fi
  local any_opp=0
  local any_fail=0
  local detail=""
  for pod in "${pods[@]}"; do
    local result rc
    set +e
    result="$(pod_exec "${pod}" \
      "python3 '${CODE_DIR}/opponent_check.py' --mode yield --out-dir '${out}' --run-marker '${marker}' --node-id ${node_id}")"
    rc=$?
    set -e
    if (( rc != 0 && rc != 10 && rc != 20 )); then
      rc=20
      result="CHECK_FAILED|transport"
    fi
    local status
    status="$(
      EXP_LOCAL_FOR_YIELD="${EXP_LOCAL}" \
      YIELD_OUT_M="${result}" YIELD_RC_M="${rc}" \
      python3 - <<'PY'
import os, sys
sys.path.insert(0, os.environ.get("EXP_LOCAL_FOR_YIELD", "."))
from local_group_guard import classify_yield_check
v = classify_yield_check(os.environ.get("YIELD_OUT_M", ""), int(os.environ.get("YIELD_RC_M", "1")))
print(v.status)
PY
    )"
    if [[ "${status}" == "CHECK_FAILED" ]]; then
      any_fail=1
      detail="${detail}; pod=${pod} rc=${rc} out=${result}"
      echo "YIELD_CHECK_FAILED on ${pod}: rc=${rc} out=${result}" >&2
    elif [[ "${status}" == "OPPONENT" ]]; then
      any_opp=1
      detail="${detail}; pod=${pod} ${result}"
      echo "YIELD: opponent on ${pod}: ${result}" >&2
    fi
    node_id=$((node_id + 1))
  done
  if (( any_fail != 0 )); then
    YIELD_STATUS="CHECK_FAILED"
    set +e
    kill_our_attempt "${out}" "${marker}"
    YIELD_KILL_RC=$?
    set -e
    return 20
  fi
  if (( any_opp != 0 )); then
    YIELD_STATUS="OPPONENT"
    set +e
    kill_our_attempt "${out}" "${marker}"
    YIELD_KILL_RC=$?
    set -e
    return 10
  fi
  YIELD_STATUS="CLEAR"
  YIELD_KILL_RC=0
  return 0
}

pull_evidence() {
  if (( LOCAL_GROUP_CLAIMED != 1 )); then
    echo "SKIP_PULL (local group not claimed)" >&2
    return 12
  fi
  local status="OK"
  if ! jump "\$K exec -n '${NS}' '${MASTER_POD}' -- tar -C '${OUT_DIR}' -cf - ." \
    | tar -C "${BACKUP_ROOT}" -xf -; then
    status="EVIDENCE_INCOMPLETE"
    echo "EVIDENCE_INCOMPLETE=tar_pull_failed RUN_ID=${RUN_ID}" >&2
  fi
  for req in attempt_manifest.json attempt_manifest.sha256 artifact_digest.json run.log; do
    if [[ ! -f "${BACKUP_ROOT}/${req}" ]]; then
      if [[ "${req}" == "run.log" ]] || [[ -f "${OUT_DIR}/${req}" ]]; then
        status="EVIDENCE_INCOMPLETE"
        echo "EVIDENCE_INCOMPLETE=missing_${req} RUN_ID=${RUN_ID}" >&2
      fi
    fi
  done
  echo "EVIDENCE_STATUS=${status} LOCAL_BACKUP=${BACKUP_ROOT}"
  if [[ "${status}" != "OK" ]]; then
    return 12
  fi
  return 0
}

fail_stop() {
  local code="$1"
  local reason="${2:-fail_stop}"
  local kill_rc="${YIELD_KILL_RC:-}"
  echo "FAIL_STOP rc=${code} RUN_ID=${RUN_ID} reason=${reason}" >&2
  if [[ -z "${kill_rc}" ]]; then
    set +e
    kill_our_attempt "${OUT_DIR}" "${RUN_MARKER}"
    kill_rc=$?
    set -e
  fi
  if (( LOCAL_GROUP_CLAIMED == 1 )); then
    if (( kill_rc != 0 )); then
      echo "CLEANUP_INCOMPLETE after fail_stop kill_rc=${kill_rc}" >&2
      printf '%s\n' "CLEANUP_INCOMPLETE" >"${BACKUP_ROOT}/CLEANUP_INCOMPLETE"
    fi
    printf '%s\n' "{\"run_id\":\"${RUN_ID}\",\"status\":\"GROUP_INVALID\",\"reason\":\"${reason}\",\"cleanup_rc\":${kill_rc}}" \
      >"${BACKUP_ROOT}/GROUP_INVALID.json"
  fi
  pull_evidence || echo "EVIDENCE_STATUS=EVIDENCE_INCOMPLETE best_effort" >&2
  exit "${code}"
}

set +e
claim_local_group_dirs
claim_rc=$?
set -e
if (( claim_rc != 0 )); then
  echo "FATAL: local group claim failed rc=${claim_rc}" >&2
  exit "${claim_rc}"
fi
LOCAL_GROUP_CLAIMED=1
echo "[mspti] LOCAL_BACKUP=${BACKUP_ROOT} LOG_DIR=${LOG_DIR} (atomic claim)"

echo "[mspti] 同步完整目录到内容寻址 CODE_DIR=${CODE_DIR}"
COPYFILE_DISABLE=1 tar -C "${EXP_LOCAL}" -cf - \
  CMakeLists.txt collector.cpp kseg_logic.hpp sync_interpose.cpp workload.py \
  convert_trace.py strict_validate.py provenance.py kill_attempt.py megatron_mspti_hook.py \
  sitecustomize.py run_node.sh run_megatron_node.sh launch_grj.sh \
  launch_megatron_ab.sh launch_megatron_smoke.sh analyze_megatron_ab.py ab_plan.py \
  fanout_orchestrator.py local_group_guard.py opponent_check.py \
  test_kseg_logic.cpp test_collector_logic.cpp test_local.py README.md \
  | ssh "${JUMP}" \
    "export KUBECONFIG='${KUBE}'; K='${KUBECTL}'; \$K exec -i -n '${NS}' '${MASTER_POD}' -- bash --noprofile --norc -lc 'mkdir -p ${CODE_DIR} && tar -C ${CODE_DIR} -xf - && chmod +x ${CODE_DIR}/*.sh ${CODE_DIR}/*.py'"

require_idle_or_invalid "${MASTER_POD}" "pre_start"
if [[ "${NNODES}" == "2" ]]; then
  require_idle_or_invalid "${WORKER_POD}" "pre_start"
fi

pod_exec "${MASTER_POD}" "mkdir -p '${OUT_DIR}'; printf '%s\n' \
  'run_id=${RUN_ID}' 'expected_runtime_s=${EXPECTED_RUNTIME_S:-180}' \
  'scale=${NNODES}x${NPROC}' 'capture_ranks=${CAPTURE_RANKS}' \
  'code_dir=${CODE_DIR}' 'code_hash=${CODE_HASH}' >'${OUT_DIR}/launch_state.log'"

echo "[mspti] pod 内编译，预计 1–3 分钟"
pod_exec "${MASTER_POD}" \
  "source /usr/local/Ascend/cann-8.5.0/set_env.sh; mkdir -p '${CODE_DIR}/build'; \
   g++ -std=c++17 -shared -fPIC -O2 -Wall -Wextra -Wpedantic -I'${CODE_DIR}' \
   '${CODE_DIR}/collector.cpp' -I/usr/local/Ascend/cann-8.5.0/include \
   -L/usr/local/Ascend/cann-8.5.0/lib64 -Wl,-rpath,/usr/local/Ascend/cann-8.5.0/lib64 \
   -lmspti -lpthread -o '${CODE_DIR}/build/libmspti_sync_skeleton.so' >'${OUT_DIR}/build.log' 2>&1 && \
   g++ -std=c++17 -shared -fPIC -O2 -Wall -Wextra -Wpedantic '${CODE_DIR}/sync_interpose.cpp' \
   -I/usr/local/Ascend/cann-8.5.0/include -ldl -o '${CODE_DIR}/build/libmspti_sync_interpose.so' >>'${OUT_DIR}/build.log' 2>&1" \
  || fail_stop 10

pod_exec "${MASTER_POD}" "python3 '${CODE_DIR}/provenance.py' --code-dir '${CODE_DIR}' --out-dir '${OUT_DIR}' --phase pre"
pod_exec "${MASTER_POD}" "python3 '${CODE_DIR}/provenance.py' --code-dir '${CODE_DIR}' --out-dir '${OUT_DIR}' --phase build"

pod_exec "${MASTER_POD}" "python3 - <<'PY'
import json
from pathlib import Path
Path('${OUT_DIR}/config.json').write_text(json.dumps({
  'run_id': '${RUN_ID}',
  'nnodes': ${NNODES},
  'nproc_per_node': ${NPROC},
  'capture_ranks': '${CAPTURE_RANKS}',
  'collector': '${COLLECTOR:-on}',
  'warmup': ${WARMUP:-3},
  'steps': ${STEPS:-2},
  'matmul_size': ${MATMUL_SIZE:-2048},
  'allreduce_bytes': ${ALLREDUCE_BYTES:-4194304},
  'gap_us': ${GAP_US:-50},
  'reorder_us': ${REORDER_US:-1000},
  'code_dir': '${CODE_DIR}',
  'code_hash': '${CODE_HASH}',
  'cann_root': '/usr/local/Ascend/cann-8.5.0',
}, indent=2, sort_keys=True), encoding='utf-8')
PY"

MASTER_ADDR="$(jump "\$K get pod -n '${NS}' '${MASTER_POD}' -o jsonpath='{.status.podIP}'")"
[[ -n "${MASTER_ADDR}" ]] || fail_stop 4

COMMON_ENV="RUN_ID='${RUN_ID}' RUN_MARKER='${RUN_MARKER}' ARM_KIND='synthetic' MASTER_ADDR='${MASTER_ADDR}' MASTER_PORT='${MASTER_PORT}' NNODES='${NNODES}' NPROC='${NPROC}' CODE_DIR='${CODE_DIR}' OUT_DIR='${OUT_DIR}' CAPTURE_RANKS='${CAPTURE_RANKS}' COLLECTOR='${COLLECTOR:-on}' WARMUP='${WARMUP:-3}' STEPS='${STEPS:-2}' MATMUL_SIZE='${MATMUL_SIZE:-2048}' ALLREDUCE_BYTES='${ALLREDUCE_BYTES:-4194304}' GAP_US='${GAP_US:-50}' REORDER_US='${REORDER_US:-1000}' RUN_TIMEOUT_S='${RUN_TIMEOUT_S:-300}'"

echo "[mspti] 启动 ${NNODES}x${NPROC}，预计 ${EXPECTED_RUNTIME_S:-180}s 内完成"
launch_pids=()
pod_exec "${MASTER_POD}" \
  "env ${COMMON_ENV} NODE_RANK=0 setsid nohup '${CODE_DIR}/run_node.sh' </dev/null >'${OUT_DIR}/launch_0.log' 2>&1 & echo \$!" &
launch_pids+=("$!")
if [[ "${NNODES}" == "2" ]]; then
  pod_exec "${WORKER_POD}" \
    "env ${COMMON_ENV} NODE_RANK=1 setsid nohup '${CODE_DIR}/run_node.sh' </dev/null >'${OUT_DIR}/launch_1.log' 2>&1 & echo \$!" &
  launch_pids+=("$!")
fi
fanout_fail=0
for pid in "${launch_pids[@]}"; do
  set +e
  wait "${pid}"
  rc=$?
  set -e
  if (( rc != 0 )); then
    fanout_fail=1
    echo "FANOUT_FAIL wait_rc=${rc} pid=${pid}" >&2
  fi
done
if (( fanout_fail != 0 )); then
  fail_stop 15
fi

deadline=$((SECONDS + ${WAIT_TIMEOUT_S:-360}))
while (( SECONDS < deadline )); do
  # Continuous yield: opponent / check-fail → exact-marker stop us, never touch them.
  YIELD_KILL_RC=""
  YIELD_STATUS=""
  set +e
  yield_if_opponent "${OUT_DIR}" "${RUN_MARKER}"
  yrc=$?
  set -e
  if (( yrc != 0 )); then
    reason="yield_opponent"
    if [[ "${YIELD_STATUS}" == "CHECK_FAILED" ]] || (( yrc == 20 )); then
      reason="YIELD_CHECK_FAILED"
    fi
    echo "YIELD_EVIDENCE RUN_ID=${RUN_ID} reason=${reason}" >&2
    fail_stop "${yrc}" "${reason}"
  fi
  done_count=0
  fail_count=0
  for node in $(seq 0 $((NNODES - 1))); do
    if pod_exec "${MASTER_POD}" "test -f '${OUT_DIR}/node_${node}.done'" >/dev/null 2>&1; then
      done_count=$((done_count + 1))
    elif pod_exec "${MASTER_POD}" "test -f '${OUT_DIR}/node_${node}.fail'" >/dev/null 2>&1; then
      fail_count=$((fail_count + 1))
    fi
  done
  if (( fail_count > 0 )); then
    fail_stop 5 "node_fail"
  fi
  if (( done_count == NNODES )); then
    break
  fi
  sleep 3
done

if (( SECONDS >= deadline )); then
  fail_stop 6 "timeout"
fi

# convert → provenance must propagate failure (no ';' mask).
CONVERT_RC=0
set +e
pod_exec "${MASTER_POD}" \
  "python3 '${CODE_DIR}/convert_trace.py' '${OUT_DIR}' >'${OUT_DIR}/convert.log' 2>&1 \
   && python3 '${CODE_DIR}/provenance.py' --code-dir '${CODE_DIR}' --out-dir '${OUT_DIR}' --phase artifacts"
CONVERT_RC=$?
set -e
if (( CONVERT_RC != 0 )); then
  fail_stop 8 "convert_or_provenance_fail rc=${CONVERT_RC}"
fi

# Stop log writes, seal run.log, write attempt_manifest from REAL convert_rc / finalize.
pod_exec "${MASTER_POD}" "python3 - <<'PY'
import json, hashlib, subprocess, sys
from pathlib import Path
out = Path('${OUT_DIR}')
parts = []
for p in sorted(out.glob('launch_*.log')) + sorted(out.glob('node_*.log')):
    parts.append(f'--- {p.name} ---\n' + p.read_text(errors='replace'))
(out / 'run.log').write_text('\n'.join(parts), encoding='utf-8')
# Re-digest after run.log sealed.
subprocess.check_call([sys.executable, '${CODE_DIR}/provenance.py', '--code-dir', '${CODE_DIR}', '--out-dir', str(out), '--phase', 'artifacts'])
pt = json.loads((out/'provenance_source_tree.json').read_text())
pb = json.loads((out/'provenance_build.json').read_text())
art = json.loads((out/'artifact_digest.json').read_text())
agg = art.get('aggregate_sha256') or art.get('artifact_digest_sha256')
node_launch = {}
for p in sorted(out.glob('node_*.launch.json')):
    node_launch[p.name] = json.loads(p.read_text())
# Derive finalize from meta if present; else synthetic may lack meta → require convert_rc==0 and e2e.
finalize_complete = True
reasons = []
metas = list(out.glob('rank_*.mspti_meta.json'))
if metas:
    for p in sorted(metas):
        m = json.loads(p.read_text())
        reasons.append(m.get('finalize_reason'))
        if not m.get('finalize_complete', False):
            finalize_complete = False
else:
    # No meta: finalize_complete only if convert succeeded and every node has e2e.
    finalize_complete = (int('${CONVERT_RC}') == 0) and all(
        isinstance(v, dict) and (v.get('e2e_wall_ms') or 0) > 0 for v in node_launch.values()
    )
e2e_vals = [float(v['e2e_wall_ms']) for v in node_launch.values() if isinstance(v, dict) and v.get('e2e_wall_ms') is not None]
if not e2e_vals or min(e2e_vals) <= 0:
    raise SystemExit(f'FATAL: missing/non-positive e2e_wall_ms in node_launch: {e2e_vals}')
manifest = {
  'attempt_id': '${RUN_ID}',
  'run_id': '${RUN_ID}',
  'run_marker': '${RUN_MARKER}',
  'arm': 'ours',
  'arm_kind': 'synthetic',
  'exit_code': 0,
  'convert_rc': int('${CONVERT_RC}'),
  'finalize_complete': bool(finalize_complete),
  'finalize_reasons': reasons,
  'expected_ranks': ${NNODES} * ${NPROC},
  'expected_nodes': ${NNODES},
  'nnodes': ${NNODES},
  'nproc_per_node': ${NPROC},
  'workload_kind': 'synthetic',
  'required_artifacts_note': 'synthetic: sealed SO + run.log + node markers + ranks',
  'node_launch': node_launch,
  'e2e_wall_ms': max(e2e_vals),
  'code_dir': '${CODE_DIR}',
  'code_hash': '${CODE_HASH}',
  'code_dir_content_hash': '${CODE_HASH}',
  'provenance': {
    'source_tree_sha256': pt.get('source_tree_sha256'),
    'collector_so_sha256': pb.get('collector_so_sha256'),
    'artifact_digest_sha256': agg,
  },
  'artifact_digest_sha256': agg,
}
if int(manifest['convert_rc']) != 0 or not manifest['finalize_complete']:
    raise SystemExit(
        f\"FATAL: refuse seal convert_rc={manifest['convert_rc']} finalize={manifest['finalize_complete']}\"
    )
tmp = out / 'attempt_manifest.json.tmp'
tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding='utf-8')
tmp.replace(out / 'attempt_manifest.json')
h = hashlib.sha256((out / 'attempt_manifest.json').read_bytes()).hexdigest()
(out / 'attempt_manifest.sha256').write_text(h + '\n', encoding='utf-8')
print('SYNTHETIC_MANIFEST_SEALED', h[:16], 'convert_rc', manifest['convert_rc'], 'finalize', finalize_complete)
PY"

pull_evidence || { echo "FATAL: required pull failed" >&2; exit 12; }

REMOTE_MAN_HASH="$(tr -d '[:space:]' < "${BACKUP_ROOT}/attempt_manifest.sha256")"
python3 "${EXP_LOCAL}/provenance.py" \
  --out-dir "${BACKUP_ROOT}" \
  --phase local_seal \
  --run-id "${RUN_ID}" \
  --remote-manifest-sha256 "${REMOTE_MAN_HASH}"
python3 - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "${EXP_LOCAL}")
from strict_validate import validate_attempt_manifest, StrictValidationError
root = Path("${BACKUP_ROOT}")
try:
    validate_attempt_manifest(
        root,
        expected_ranks=int("${NNODES}") * int("${NPROC}"),
        capture_step=0,
        expected_nodes=int("${NNODES}"),
        require_seal=True,
        require_provenance=True,
        require_local_anchor=True,
    )
except StrictValidationError as exc:
    print("LOCAL_STRICT", exc, file=sys.stderr)
    raise SystemExit(11)
print("LOCAL_VERIFIED_SEAL_AND_STRICT_OK", root)
PY

pod_exec "${MASTER_POD}" \
  "python3 - <<'PY'
from pathlib import Path
p = Path('${OUT_DIR}/SUMMARY.md')
print(p.read_text() if p.exists() else 'NO_SUMMARY')
print('RUN_COMPLETE=${RUN_ID}')
PY"
echo "LOCAL_BACKUP=${BACKUP_ROOT}"
echo "LOG_DIR=${LOG_DIR}"
