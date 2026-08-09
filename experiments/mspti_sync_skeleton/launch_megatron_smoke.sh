#!/usr/bin/env bash
# Megatron MSPTI 生命周期 smoke：仅 ATTEMPTS=ours，支持 1/2/32 rank，TRAIN_ITERS=2。
# Gate=BLOCK：禁止 6×20 / 正式集成 / 5×32 / 512。失败即杀本 attempt PID 树并回拉证据。
# 冻结阈值写入不可变 group/config；可归因 provenance + artifact digest 后再 seal。
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
WORLD_SIZE=$((NNODES * NPROC))
TRAIN_ITERS="${TRAIN_ITERS:-2}"
CAPTURE_ITER="${MSPTI_CAPTURE_MEGATRON_ITER:-1}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)-mspti-smoke-${WORLD_SIZE}r}"
AFS_ROOT="/afs-a3-weight-share/yinjinrun.p-huawei"
OUT_DIR="${AFS_ROOT}/results/mspti-sync-skeleton/${RUN_ID}"
BACKUP_ROOT="/Users/yinjinrun/Codespace/myportal/results/huawei-a3-32/mspti-sync-skeleton/${RUN_ID}"
MASTER_PORT="${MASTER_PORT:-38111}"
SEED="${SEED:-1234}"

# 冻结绝对阈值（依据已通过 091736 / 092006；写入 config 后运行期不得修改）
# 1r/2r：raw>=250, comm>=10；32r：raw>=7000, comm>=1000
if (( WORLD_SIZE >= 32 )); then
  MIN_RAW_KERNELS="${MSPTI_MIN_RAW_KERNELS:-7000}"
  MIN_COMM="${MSPTI_MIN_COMM:-1000}"
  THRESH_REASON="32r conservative floor from PASS 092006/history 001500 (raw~9k,comm~1.3k)"
  TP="${TP:-2}"; PP="${PP:-1}"; MBS="${MBS:-1}"; GBS="${GBS:-64}"; SEQ="${SEQ:-4096}"; LAYERS="${LAYERS:-32}"
elif (( WORLD_SIZE == 2 )); then
  MIN_RAW_KERNELS="${MSPTI_MIN_RAW_KERNELS:-250}"
  MIN_COMM="${MSPTI_MIN_COMM:-10}"
  THRESH_REASON="2r same-order floor from PASS 091736/091847 (raw~300,comm~21)"
  TP="${TP:-1}"; PP="${PP:-1}"; MBS="${MBS:-1}"; GBS="${GBS:-2}"; SEQ="${SEQ:-1024}"; LAYERS="${LAYERS:-2}"
else
  MIN_RAW_KERNELS="${MSPTI_MIN_RAW_KERNELS:-250}"
  MIN_COMM="${MSPTI_MIN_COMM:-10}"
  THRESH_REASON="1r conservative floor from PASS 091736 (raw=357,comm=21)"
  TP="${TP:-1}"; PP="${PP:-1}"; MBS="${MBS:-1}"; GBS="${GBS:-1}"; SEQ="${SEQ:-1024}"; LAYERS="${LAYERS:-2}"
fi
REL_RAW_FLOOR="${MSPTI_REL_RAW_FLOOR:-0.8}"
REL_COMM_FLOOR="${MSPTI_REL_COMM_FLOOR:-0.8}"

DATA_PATH="${DATA_PATH:-/afs-a3-weight-share/enwiki/enwiki20230101/enwiki20230101-00000_text_document}"
CACHE="${CACHE:-/afs-a3-weight-share/yinjinrun.p-huawei/megatron-data-cache}"
EXPECTED_RANKS="${EXPECTED_RANKS:-${WORLD_SIZE}}"
ARM=ours
RUN_MARKER="MSPTI_SMOKE_${RUN_ID}"

CODE_HASH="$(
  cd "${EXP_LOCAL}" && python3 - <<'PY'
import hashlib
from pathlib import Path
h = hashlib.sha256()
for p in sorted(Path('.').iterdir()):
    if not p.is_file():
        continue
    if p.suffix in {'.cpp','.hpp','.h','.py','.sh','.md'} or p.name == 'CMakeLists.txt':
        h.update(p.name.encode()); h.update(b'\0'); h.update(p.read_bytes()); h.update(b'\n')
print(h.hexdigest()[:16])
PY
)"
CODE_DIR="${AFS_ROOT}/probing-huawei/experiments/mspti_sync_skeleton-${CODE_HASH}"

if [[ "${NNODES}" != "1" && "${NNODES}" != "2" ]]; then
  echo "NNODES must be 1 or 2" >&2
  exit 2
fi
if [[ "${NNODES}" == "2" && "${NPROC}" != "16" ]]; then
  echo "2-node smoke requires NPROC=16 (32 rank)" >&2
  exit 2
fi

jump() {
  ssh -o ConnectTimeout=30 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
    "${JUMP}" "export KUBECONFIG='${KUBE}'; K='${KUBECTL}'; $*"
}

pod_exec() {
  local pod="$1" cmd="$2"
  jump "\$K exec -n '${NS}' '${pod}' -- bash --noprofile --norc -lc $(printf '%q' "${cmd}")"
}

list_train_procs() {
  local pod="$1"
  pod_exec "${pod}" \
    "ps -eo pid,pgid,state,args | awk '/torchrun|pretrain_gpt.py/ && !/awk|bash --noprofile/ && \$3 !~ /Z/ {print}'"
}

check_idle() {
  local pod="$1" active
  active="$(list_train_procs "${pod}" || true)"
  if [[ -n "${active}" ]]; then
    echo "FATAL: ${pod} 非空闲：" >&2
    echo "${active}" >&2
    exit 3
  fi
}

# 每 pod 只读自己的 node_${id}；经 /proc 校验 exact RUN_MARKER 后才杀；禁止 glob 跨 pod / 宽泛 pkill
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

pull_evidence() {
  mkdir -p "${BACKUP_ROOT}"
  local status="OK"
  if ! jump "\$K exec -n '${NS}' '${MASTER_POD}' -- tar -C '${OUT_DIR}' -cf - ." \
    | tar -C "${BACKUP_ROOT}" -xf -; then
    status="EVIDENCE_INCOMPLETE"
    echo "EVIDENCE_INCOMPLETE=tar_pull_failed RUN_ID=${RUN_ID}" >&2
    echo "LOCAL_BACKUP=${BACKUP_ROOT} status=${status}" >&2
    return 12
  fi
  # Fail closed if critical seal artifacts missing after pullback.
  for req in attempt_manifest.json attempt_manifest.sha256 artifact_digest.json run.log; do
    if [[ ! -f "${BACKUP_ROOT}/${req}" ]]; then
      status="EVIDENCE_INCOMPLETE"
      echo "EVIDENCE_INCOMPLETE=missing_${req}" >&2
      echo "LOCAL_BACKUP=${BACKUP_ROOT} status=${status}" >&2
      return 12
    fi
  done
  # 父级独立锚：回拉后本机重算，写入 LOCAL_VERIFIED_SEAL（不依赖 vault）
  REMOTE_MAN_HASH="$(tr -d '[:space:]' < "${BACKUP_ROOT}/attempt_manifest.sha256")"
  if ! python3 "${EXP_LOCAL}/provenance.py" \
    --out-dir "${BACKUP_ROOT}" \
    --phase local_seal \
    --run-id "${RUN_ID}" \
    --remote-manifest-sha256 "${REMOTE_MAN_HASH}" \
    | tee -a "${BACKUP_ROOT}/launcher_local.log"; then
    status="EVIDENCE_INCOMPLETE"
    echo "EVIDENCE_INCOMPLETE=local_seal_failed" >&2
    echo "LOCAL_BACKUP=${BACKUP_ROOT} status=${status}" >&2
    return 12
  fi
  LOCAL_SEAL_HASH="$(python3 - <<PY
import hashlib
from pathlib import Path
p = Path('${BACKUP_ROOT}/LOCAL_VERIFIED_SEAL.json')
print(hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else 'missing')
PY
)"
  echo "LOCAL_VERIFIED_SEAL_HASH=${LOCAL_SEAL_HASH}" | tee -a "${BACKUP_ROOT}/launcher_local.log"
  echo "EVIDENCE_STATUS=${status} LOCAL_BACKUP=${BACKUP_ROOT}"
  return 0
}

fail_stop() {
  local code="$1"
  echo "FAIL_STOP rc=${code} RUN_ID=${RUN_ID}" >&2
  set +e
  kill_our_attempt "${OUT_DIR}" "${RUN_MARKER}"
  local kill_rc=$?
  set -e
  if (( kill_rc != 0 )); then
    echo "CLEANUP_INCOMPLETE after fail_stop kill_rc=${kill_rc}" >&2
    mkdir -p "${BACKUP_ROOT}"
    echo "CLEANUP_INCOMPLETE" >"${BACKUP_ROOT}/CLEANUP_INCOMPLETE"
  fi
  # Failure evidence is best-effort but must be explicit — never claim PASS.
  if ! pull_evidence; then
    echo "EVIDENCE_STATUS=EVIDENCE_INCOMPLETE best_effort FAIL_STOP" >&2
  fi
  exit "${code}"
}

echo "[smoke] RUN_ID=${RUN_ID} world=${WORLD_SIZE} iters=${TRAIN_ITERS} capture=${CAPTURE_ITER}"
echo "[smoke] CODE_DIR=${CODE_DIR} min_raw=${MIN_RAW_KERNELS} min_comm=${MIN_COMM}"
check_idle "${MASTER_POD}"
if [[ "${NNODES}" == "2" ]]; then
  check_idle "${WORKER_POD}"
fi

echo "[smoke] sync+build (content-addressed)"
COPYFILE_DISABLE=1 tar -C "${EXP_LOCAL}" -cf - \
  CMakeLists.txt collector.cpp kseg_logic.hpp sync_interpose.cpp workload.py \
  convert_trace.py strict_validate.py provenance.py kill_attempt.py megatron_mspti_hook.py sitecustomize.py \
  run_megatron_node.sh run_node.sh launch_grj.sh launch_megatron_ab.sh \
  launch_megatron_smoke.sh analyze_megatron_ab.py ab_plan.py fanout_orchestrator.py test_kseg_logic.cpp test_local.py \
  test_collector_logic.cpp README.md \
  | ssh -o ConnectTimeout=30 "${JUMP}" \
    "export KUBECONFIG='${KUBE}'; K='${KUBECTL}'; \$K exec -i -n '${NS}' '${MASTER_POD}' -- bash --noprofile --norc -lc 'mkdir -p ${CODE_DIR} && tar -C ${CODE_DIR} -xf - && chmod +x ${CODE_DIR}/*.sh ${CODE_DIR}/*.py'"

pod_exec "${MASTER_POD}" "mkdir -p '${OUT_DIR}'"
pod_exec "${MASTER_POD}" "python3 '${CODE_DIR}/provenance.py' --code-dir '${CODE_DIR}' --out-dir '${OUT_DIR}' --phase pre"

pod_exec "${MASTER_POD}" \
  "source /usr/local/Ascend/cann-8.5.0/set_env.sh; mkdir -p '${CODE_DIR}/build'; \
   g++ -std=c++17 -shared -fPIC -O2 -Wall -Wextra -Wpedantic -I'${CODE_DIR}' \
   '${CODE_DIR}/collector.cpp' -I/usr/local/Ascend/cann-8.5.0/include \
   -L/usr/local/Ascend/cann-8.5.0/lib64 -Wl,-rpath,/usr/local/Ascend/cann-8.5.0/lib64 \
   -lmspti -lpthread -o '${CODE_DIR}/build/libmspti_sync_skeleton.so' \
   >'${OUT_DIR}/build.log' 2>&1" || fail_stop 10

pod_exec "${MASTER_POD}" "python3 '${CODE_DIR}/provenance.py' --code-dir '${CODE_DIR}' --out-dir '${OUT_DIR}' --phase build"

POD_META="$(jump "\$K get pod -n '${NS}' '${MASTER_POD}' -o jsonpath='{.metadata.uid}|{.spec.nodeName}|{.status.containerStatuses[0].image}'; echo")"
if [[ "${NNODES}" == "2" ]]; then
  POD_META="${POD_META}
$(jump "\$K get pod -n '${NS}' '${WORKER_POD}' -o jsonpath='{.metadata.uid}|{.spec.nodeName}|{.status.containerStatuses[0].image}'; echo")"
fi
GIT_HASH="$(cd "${EXP_LOCAL}" && git rev-parse HEAD 2>/dev/null || echo unknown)"

# 启动前写入不可变 frozen thresholds（运行后不得改）
pod_exec "${MASTER_POD}" "python3 - <<'PY'
import json
from pathlib import Path
cfg = {
  'run_id': '${RUN_ID}',
  'arm': 'ours',
  'collector': 'on',
  'nnodes': ${NNODES},
  'nproc_per_node': ${NPROC},
  'world_size': ${WORLD_SIZE},
  'expected_ranks': ${EXPECTED_RANKS},
  'expected_nodes': ${NNODES},
  'train_iters': ${TRAIN_ITERS},
  'capture_megatron_iter': ${CAPTURE_ITER},
  'workload_kind': 'megatron',
  'model': {
    'tp': ${TP}, 'pp': ${PP}, 'mbs': ${MBS}, 'gbs': ${GBS}, 'seq': ${SEQ},
    'layers': ${LAYERS}, 'seed': ${SEED},
    'dp': max(1, ${WORLD_SIZE} // max(1, ${TP} * ${PP})),
    'world_size': ${WORLD_SIZE},
    'data_path': '${DATA_PATH}',
    'cache': '${CACHE}',
  },
  'pods': '''${POD_META}'''.strip().splitlines(),
  'git_hash': '${GIT_HASH}',
  'code_dir': '${CODE_DIR}',
  'code_hash': '${CODE_HASH}',
  'master_port': ${MASTER_PORT},
  'protocol': 'two_phase_finalize',
  'frozen_thresholds': {
    'min_raw_kernels_per_rank': ${MIN_RAW_KERNELS},
    'min_comm_per_rank': ${MIN_COMM},
    'relative_raw_floor': ${REL_RAW_FLOOR},
    'relative_comm_floor': ${REL_COMM_FLOOR},
    'reason': '''${THRESH_REASON}''',
    'immutable': True,
  },
}
Path('${OUT_DIR}/config.json').write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding='utf-8')
print('FROZEN_THRESHOLDS', cfg['frozen_thresholds'])
PY"

MASTER_ADDR="$(jump "\$K get pod -n '${NS}' '${MASTER_POD}' -o jsonpath='{.status.podIP}'")"
[[ -n "${MASTER_ADDR}" ]] || fail_stop 4

COMMON="RUN_ID='${RUN_ID}' RUN_MARKER='${RUN_MARKER}' ARM='${ARM}' MASTER_ADDR='${MASTER_ADDR}' MASTER_PORT='${MASTER_PORT}' NNODES='${NNODES}' NPROC='${NPROC}' CODE_DIR='${CODE_DIR}' OUT_DIR='${OUT_DIR}' TRAIN_ITERS='${TRAIN_ITERS}' MSPTI_CAPTURE_MEGATRON_ITER='${CAPTURE_ITER}' MSPTI_CAPTURE_RANKS='all' RUN_TIMEOUT_S='${RUN_TIMEOUT_S:-900}' SEED='${SEED}' TP='${TP}' PP='${PP}' MBS='${MBS}' GBS='${GBS}' SEQ='${SEQ}' LAYERS='${LAYERS}' DATA_PATH='${DATA_PATH}' CACHE='${CACHE}'"

START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
launch_pids=()
pod_exec "${MASTER_POD}" \
  "env ${COMMON} NODE_RANK=0 setsid nohup '${CODE_DIR}/run_megatron_node.sh' </dev/null >'${OUT_DIR}/launch_0.log' 2>&1 & echo \$!" &
launch_pids+=("$!")
if [[ "${NNODES}" == "2" ]]; then
  pod_exec "${WORKER_POD}" \
    "env ${COMMON} NODE_RANK=1 setsid nohup '${CODE_DIR}/run_megatron_node.sh' </dev/null >'${OUT_DIR}/launch_1.log' 2>&1 & echo \$!" &
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

deadline=$((SECONDS + ${WAIT_TIMEOUT_S:-900}))
exit_code=0
while (( SECONDS < deadline )); do
  status="$(pod_exec "${MASTER_POD}" "python3 - <<'PY'
from pathlib import Path
out = Path('${OUT_DIR}')
nodes = list(range(${NNODES}))
done = sum(1 for n in nodes if (out / f'node_{n}.done').exists())
fail = sum(1 for n in nodes if (out / f'node_{n}.fail').exists())
print(f'{done} {fail}')
PY")"
  done_count="$(awk '{print $1}' <<<"${status}")"
  fail_count="$(awk '{print $2}' <<<"${status}")"
  echo "$(date +%H:%M:%S) smoke done=${done_count}/${NNODES} fail=${fail_count}"
  if (( fail_count > 0 )); then
    exit_code=6
    break
  fi
  if (( done_count == NNODES )); then
    break
  fi
  sleep 5
done
if (( SECONDS >= deadline )); then
  exit_code=7
fi
if (( exit_code != 0 )); then
  fail_stop "${exit_code}"
fi

END_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
set +e
pod_exec "${MASTER_POD}" \
  "python3 '${CODE_DIR}/convert_trace.py' --strict \
    --expected-ranks '${EXPECTED_RANKS}' --expected-nodes '${NNODES}' \
    --capture-step '${CAPTURE_ITER}' --workload-kind megatron \
    --min-raw-kernels '${MIN_RAW_KERNELS}' --min-comm '${MIN_COMM}' \
    --relative-raw-floor '${REL_RAW_FLOOR}' --relative-comm-floor '${REL_COMM_FLOOR}' \
    '${OUT_DIR}' >'${OUT_DIR}/convert.log' 2>&1"
CONVERT_RC=$?
set -e
if (( CONVERT_RC != 0 )); then
  echo "STRICT convert failed rc=${CONVERT_RC}" >&2
  fail_stop 8
fi

# 停止写入 run.log/node logs/exit markers 后，才 digest → manifest → remote seal
pod_exec "${MASTER_POD}" "python3 - <<'PY'
import json, hashlib
from pathlib import Path
out = Path('${OUT_DIR}')
parts = []
for p in sorted(out.glob('launch_*.log')) + sorted(out.glob('node_*.log')):
    parts.append(f'--- {p.name} ---\\n' + p.read_text(errors='replace'))
(out / 'run.log').write_text('\\n'.join(parts), encoding='utf-8')
print('RUN_LOG_SEALED', (out/'run.log').stat().st_size)
PY"

pod_exec "${MASTER_POD}" "python3 '${CODE_DIR}/provenance.py' --code-dir '${CODE_DIR}' --out-dir '${OUT_DIR}' --phase artifacts"

pod_exec "${MASTER_POD}" "python3 - <<'PY'
import json, hashlib
from pathlib import Path
out = Path('${OUT_DIR}')
pgids = {}
for n in range(${NNODES}):
    p = out / f'node_{n}.pgid'
    if p.exists():
        pgids[p.name] = p.read_text().strip()
node_launch = {p.name: json.loads(p.read_text()) for p in sorted(out.glob('node_*.launch.json'))}
metas = []
finalize_complete = True
reasons = []
for p in sorted(out.glob('rank_*.mspti_meta.json')):
    m = json.loads(p.read_text())
    metas.append(m)
    if not m.get('finalize_complete', False):
        finalize_complete = False
    reasons.append(m.get('finalize_reason'))
prov_tree = {}
pt = out / 'provenance_source_tree.json'
pb = out / 'provenance_build.json'
snap = out / 'provenance_source_snapshot.sha256'
if not pt.exists() or not pb.exists() or not snap.exists():
    raise SystemExit('FATAL: missing attempt provenance before seal')
prov_tree = json.loads(pt.read_text())
prov_build = json.loads(pb.read_text())
art = json.loads((out / 'artifact_digest.json').read_text())
cfg = json.loads((out / 'config.json').read_text())
if not prov_tree.get('source_tree_sha256') or not prov_build.get('collector_so_sha256'):
    raise SystemExit('FATAL: null provenance hashes')
agg = art.get('aggregate_sha256') or art.get('artifact_digest_sha256')
if not agg:
    raise SystemExit('FATAL: null artifact aggregate')
e2e_vals = [v.get('e2e_wall_ms') for v in node_launch.values() if v.get('e2e_wall_ms') is not None]
e2e_wall = max(e2e_vals) if e2e_vals else None
if e2e_wall is None:
    raise SystemExit('FATAL: missing node e2e_wall_ms')
if float(e2e_wall) <= 0:
    raise SystemExit(f'FATAL: non-positive e2e_wall_ms={e2e_wall}')
manifest = {
  'attempt_id': 'attempt_01_ours',
  'run_id': '${RUN_ID}',
  'run_marker': '${RUN_MARKER}',
  'arm': 'ours',
  'order_index': 1,
  'started_at_utc': '${START_TS}',
  'ended_at_utc': '${END_TS}',
  'master_port': ${MASTER_PORT},
  'exit_code': 0,
  'convert_rc': ${CONVERT_RC},
  'finalize_complete': finalize_complete,
  'finalize_reasons': reasons,
  'train_iters': ${TRAIN_ITERS},
  'capture_megatron_iter': ${CAPTURE_ITER},
  'expected_ranks': ${EXPECTED_RANKS},
  'expected_nodes': ${NNODES},
  'nnodes': ${NNODES},
  'nproc_per_node': ${NPROC},
  'workload_kind': 'megatron',
  'pgids': pgids,
  'node_launch': node_launch,
  'e2e_wall_ms': e2e_wall,
  'e2e_wall_definition': 'max(node e2e_wall_ms); node wall = argv start→process exit',
  'mspti_meta_summary': [
    {k: m.get(k) for k in (
      'rank','finalize_complete','finalize_rc','finalize_reason',
      'raw_kernels','raw_comms','finalize_ms','finalize_flush_ms','finalize_drain_ms',
      'capture_begin_ms','capture_end_ms','process_wall_ms')}
    for m in metas
  ],
  'frozen_thresholds': cfg.get('frozen_thresholds'),
  'code_dir': '${CODE_DIR}',
  'code_hash': '${CODE_HASH}',
  'git_hash': '${GIT_HASH}',
  'pods': '''${POD_META}'''.strip().splitlines(),
  'strict': 1,
  'provenance': {
    'source_tree_sha256': prov_tree.get('source_tree_sha256'),
    'collector_so_sha256': prov_build.get('collector_so_sha256'),
    'artifact_digest_sha256': agg,
    'file_count_source': prov_tree.get('file_count'),
    'file_count_artifacts': art.get('file_count'),
  },
  'source_tree_sha256': prov_tree.get('source_tree_sha256'),
  'collector_so_sha256': prov_build.get('collector_so_sha256'),
  'artifact_digest_sha256': agg,
}
# 最终 seal：全部 done/meta/convert/counters/artifact digest 齐后原子一次写
tmp = out / 'attempt_manifest.json.tmp'
tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding='utf-8')
tmp.replace(out / 'attempt_manifest.json')
h = hashlib.sha256((out / 'attempt_manifest.json').read_bytes()).hexdigest()
(out / 'attempt_manifest.sha256').write_text(h + '\\n', encoding='utf-8')
if not finalize_complete:
    raise SystemExit('finalize_complete false')
explicit = {'last_train_step','train.finally','pretrain.finally'}
bad = [r for r in reasons if r not in explicit]
if bad:
    raise SystemExit(f'non-explicit finalize_reason: {bad}')
print('SMOKE_MANIFEST_OK', h[:16], 'reasons', sorted(set(reasons)), 'e2e', e2e_wall)
PY" || fail_stop 9

pull_evidence
# Formal local-anchor accept after pullback
python3 - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "${EXP_LOCAL}")
from strict_validate import validate_attempt_manifest, StrictValidationError
root = Path("${BACKUP_ROOT}")
try:
    validate_attempt_manifest(
        root,
        expected_ranks=int("${EXPECTED_RANKS}"),
        capture_step=int("${CAPTURE_ITER}"),
        expected_nodes=int("${NNODES}"),
        require_seal=True,
        require_provenance=True,
        require_local_anchor=True,
    )
except StrictValidationError as exc:
    print("LOCAL_ANCHOR_FAIL", exc, file=sys.stderr)
    raise SystemExit(11)
print("LOCAL_ANCHOR_OK", "${RUN_ID}")
PY
echo "SMOKE_COMPLETE=${RUN_ID}"
echo "LOCAL_BACKUP=${BACKUP_ROOT}"
