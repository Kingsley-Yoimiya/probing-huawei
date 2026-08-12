#!/usr/bin/env bash
# 真实 32 卡 Megatron 严格对照：唯一 group + counterbalanced attempts。
# 安全：只杀本 RUN_MARKER/pgid；对方进程出现立即让路（杀自己），绝不杀对方。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXP_LOCAL="${ROOT}/experiments/mspti_sync_skeleton"
JUMP="${JUMP:-afs-cpu}"
# JUMP_LOCAL=1：脚本已在跳板本机执行，jump/jump_n 直接 eval kubectl，不 ssh。
JUMP_LOCAL="${JUMP_LOCAL:-0}"
if [[ "${JUMP_LOCAL}" == "1" ]]; then
  # transfer_recovery / chunk_pull treat jump=local as no-ssh kubectl.
  JUMP="local"
fi
KUBE="${KUBE:-/root/.kube/config-vc-a3-241ceshi-songyiyang.yaml}"
KUBECTL="${KUBECTL:-/root/bin/kubectl}"
NS="${NS:-default}"
MASTER_POD="${MASTER_POD:-grj-megatron-32card-0716-master-0}"
WORKER_POD="${WORKER_POD:-grj-megatron-32card-0716-worker-0}"
JOB_NAME="${JOB_NAME:-}"
FANOUT_PARALLEL="${FANOUT_PARALLEL:-16}"
FANOUT_PREFLIGHT="${FANOUT_PREFLIGHT:-0}"
JUMP_CONTROL_PARENT="${JUMP_CONTROL_PARENT:-/root/myportal-results/mspti-control}"
NNODES="${NNODES:-2}"
NPROC="${NPROC:-16}"
WORLD_SIZE=$((NNODES * NPROC))
TRAIN_ITERS="${TRAIN_ITERS:-20}"
CAPTURE_ITER="${MSPTI_CAPTURE_MEGATRON_ITER:-10}"
GROUP_ID="${GROUP_ID:-$(date +%Y%m%d_%H%M%S)-megatron-ab-strict}"
AFS_ROOT="/afs-a3-weight-share/yinjinrun.p-huawei"
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
# Layered storage (P0 REPLAN): live + sealed on AFS; jump control only on overlay.
AFS_GROUP_ROOT="${AFS_ROOT}/results/mspti-sync-skeleton/megatron-ab/${GROUP_ID}"
AFS_LIVE_ROOT="${AFS_GROUP_ROOT}/attempts"
AFS_SEAL_ROOT="${AFS_GROUP_ROOT}/sealed"
JUMP_CONTROL_ROOT="${JUMP_CONTROL_PARENT}/${GROUP_ID}"
GROUP_DIR="${AFS_GROUP_ROOT}"
POD_MAP_FILE="${POD_MAP_FILE:-}"
if [[ "${FANOUT_PREFLIGHT}" == "1" ]]; then
  # Control-only tree on jump overlay (no Mac backup for no-training preflight).
  BACKUP_PARENT="${JUMP_CONTROL_PARENT}"
  BACKUP_ROOT="${JUMP_CONTROL_ROOT}"
  LOG_DIR="${JUMP_CONTROL_ROOT}/logs"
  # Claim files must not live under BACKUP_ROOT (mkdir parents=True would pre-create it).
  CLAIM_PARENT="${JUMP_CONTROL_PARENT}/.claims"
fi
# BACKUP_ROOT 可测可覆盖；默认本机唯一 group 目录。禁止复用已存在路径。
BACKUP_PARENT="${BACKUP_PARENT:-/Users/yinjinrun/Codespace/myportal/results/huawei-a3-32/mspti-sync-skeleton/megatron-ab}"
BACKUP_ROOT="${BACKUP_ROOT:-${BACKUP_PARENT}/${GROUP_ID}}"
# 本机 launcher 日志：每 run 唯一目录，不并入旧日志。
LOG_ROOT="${LOG_ROOT:-/Users/yinjinrun/Codespace/myportal/logs}"
LOG_DIR="${LOG_DIR:-${LOG_ROOT}/mspti-ab-${GROUP_ID}}"
# 原子 claim 落点（O_CREAT|O_EXCL）；与 BACKUP/LOG 分离。
CLAIM_PARENT="${CLAIM_PARENT:-${BACKUP_PARENT}/.group_claims}"
# counterbalanced 默认；仅用于生成 immutable group_plan.json。
# formal/DRY_RUN/FIXTURE_RUN 之后只消费 plan，禁止再从 ATTEMPTS 推导 id/port/marker。
ATTEMPTS="${ATTEMPTS:-normal ours torch torch ours normal}"
DESIGN_SEQUENCE="${DESIGN_SEQUENCE:-counterbalanced_v1}"
BASE_PORT="${BASE_PORT:-39100}"
SEED="${SEED:-1234}"
TP="${TP:-2}"; PP="${PP:-1}"; MBS="${MBS:-1}"; GBS="${GBS:-64}"; SEQ="${SEQ:-4096}"; LAYERS="${LAYERS:-32}"
# 256 卡冻结 GBS=512（DP=128）；禁止默默落到 GBS=64。
read -r GBS WORLD_SIZE FROZEN_DP GBS_CONTRACT < <(
  python3 "${EXP_LOCAL}/model_scale_contract.py" --format json \
    --nnodes "${NNODES}" --nproc "${NPROC}" --tp "${TP}" --pp "${PP}" --mbs "${MBS}" \
    --gbs "${GBS}" --seq "${SEQ}" --layers "${LAYERS}" --seed "${SEED}" \
    | python3 -c "import json,sys; m=json.load(sys.stdin); print(m['gbs'], m['world_size'], m['dp'], m['gbs_contract'])"
) || { echo "FATAL: GBS scale contract failed (see model_scale_contract.py)" >&2; exit 2; }
if [[ "${GBS_CONTRACT}" == "frozen_512" || "${GBS_CONTRACT}" == "frozen_128" ]]; then
  echo "[megatron-ab] GBS frozen: world_size=${WORLD_SIZE} DP=${FROZEN_DP} GBS=${GBS} contract=${GBS_CONTRACT}" >&2
fi
# pjlab-new (pvc-5gnm2) 无 /afs-a3-weight-share/enwiki；自有 shard 在 yinjinrun.p-huawei/data。
DATA_PATH="${DATA_PATH:-/afs-a3-weight-share/yinjinrun.p-huawei/data/enwiki20230101/enwiki20230101-00000_text_document}"
CACHE="${CACHE:-/afs-a3-weight-share/yinjinrun.p-huawei/megatron-data-cache}"
EXPECTED_RANKS="${EXPECTED_RANKS:-${WORLD_SIZE}}"
STRICT="${STRICT:-1}"
# 正式 ours 冻结绝对频率：必须运行前注入，不得运行后推导。
# 未设置 env 时用 conservative 默认；显式设为空字符串 → 拒绝启动。
if [[ "${MSPTI_MIN_RAW_KERNELS+x}" == "x" ]]; then
  MIN_RAW_KERNELS="${MSPTI_MIN_RAW_KERNELS}"
else
  MIN_RAW_KERNELS="7000"
fi
if [[ "${MSPTI_MIN_COMM+x}" == "x" ]]; then
  MIN_COMM="${MSPTI_MIN_COMM}"
else
  MIN_COMM="1000"
fi
REL_RAW_FLOOR="${MSPTI_REL_RAW_FLOOR:-0.8}"
REL_COMM_FLOOR="${MSPTI_REL_COMM_FLOOR:-0.8}"
DRY_RUN="${DRY_RUN:-0}"
FIXTURE_RUN="${FIXTURE_RUN:-0}"
SEAL_BACKEND="${SEAL_BACKEND:-AFS_MIRROR}"
# Formal = real training path (not dry-run / fixture / fanout-only preflight).
FORMAL_MODE=0
if [[ "${DRY_RUN}" != "1" && "${FIXTURE_RUN}" != "1" && "${FANOUT_PREFLIGHT}" != "1" ]]; then
  FORMAL_MODE=1
fi
# Formal forbids CAPACITY_JUMP_ONLY bypass (REPLAN Acceptance 1).
if [[ "${FORMAL_MODE}" == "1" ]]; then
  _cap_jump_only="${CAPACITY_JUMP_ONLY:-0}"
  if [[ "${_cap_jump_only}" != "0" ]]; then
    echo "INVALID_CAPACITY_MODE CAPACITY_JUMP_ONLY=${_cap_jump_only} forbidden in formal mode" >&2
    exit 3
  fi
  if [[ "${SEAL_BACKEND}" != "AFS_MIRROR" ]]; then
    echo "FATAL: formal mode requires SEAL_BACKEND=AFS_MIRROR (got ${SEAL_BACKEND})" >&2
    exit 3
  fi
fi
# Formal frozen artifacts — consume artifact_manifest.json when present.
FORMAL_BUILD_ROOT="${FORMAL_BUILD_ROOT:-}"
FORMAL_WHEEL_SHA256="${FORMAL_WHEEL_SHA256:-}"
FORMAL_SO_SHA256="${FORMAL_SO_SHA256:-}"
ARTIFACT_MANIFEST="${ARTIFACT_MANIFEST:-}"
PYBIN="${PYBIN:-/root/miniconda3/envs/llm_test/bin}"

if [[ -z "${MIN_RAW_KERNELS}" || -z "${MIN_COMM}" ]]; then
  echo "FATAL: formal AB requires MIN_RAW_KERNELS/MIN_COMM before launch (got empty)" >&2
  exit 2
fi
# DRY_RUN/FIXTURE_RUN must NOT early-exit before plan/config/attempt construction.
# Side-effecting remote ops are gated by executor below.

# 本机 group 原子认领：O_CREAT|O_EXCL claim → 单层 mkdir BACKUP/LOG（无 -p）。
# 禁止 check 后 mkdir -p；拒绝路径不写旧目录 / GROUP_INVALID。
claim_local_group_dirs() {
  python3 "${EXP_LOCAL}/local_group_guard.py" claim \
    --group-id "${GROUP_ID}" \
    --claim-parent "${CLAIM_PARENT}" \
    --backup-root "${BACKUP_ROOT}" \
    --log-dir "${LOG_DIR}"
}

jump() {
  if [[ "${JUMP_LOCAL}" == "1" ]]; then
    export KUBECONFIG="${KUBE}"
    K="${KUBECTL}"
    eval "$@"
    return
  fi
  ssh -o ConnectTimeout=30 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
    "${JUMP}" "export KUBECONFIG='${KUBE}'; K='${KUBECTL}'; $*"
}

# stdin-isolated jump (ssh -n): safe inside FD3 plan loop / pull paths.
jump_n() {
  if [[ "${JUMP_LOCAL}" == "1" ]]; then
    export KUBECONFIG="${KUBE}"
    K="${KUBECTL}"
    eval "$@"
    return
  fi
  ssh -n -o ConnectTimeout=30 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
    -o BatchMode=yes \
    "${JUMP}" "export KUBECONFIG='${KUBE}'; K='${KUBECTL}'; $*"
}

load_formal_artifact_manifest() {
  if [[ -n "${FORMAL_BUILD_ROOT}" && -n "${FORMAL_WHEEL_SHA256}" && -n "${FORMAL_SO_SHA256}" ]]; then
    return 0
  fi
  local manifest="${ARTIFACT_MANIFEST:-${FORMAL_BUILD_ROOT}/artifact_manifest.json}"
  if [[ -z "${FORMAL_BUILD_ROOT}" && -n "${ARTIFACT_MANIFEST}" && -f "${ARTIFACT_MANIFEST}" ]]; then
    FORMAL_BUILD_ROOT="$(python3 -c "import json; print(json.load(open('${ARTIFACT_MANIFEST}'))['build_root'])")"
  fi
  if [[ -n "${manifest}" && -f "${manifest}" ]]; then
    read -r FORMAL_BUILD_ROOT FORMAL_WHEEL_SHA256 FORMAL_SO_SHA256 < <(
      python3 -c "import json; m=json.load(open('${manifest}')); print(m['build_root'], m['wheel_sha256'], m['so_sha256'])"
    )
    ARTIFACT_MANIFEST="${manifest}"
  fi
  if [[ -z "${FORMAL_BUILD_ROOT}" || -z "${FORMAL_WHEEL_SHA256}" || -z "${FORMAL_SO_SHA256}" ]]; then
    echo "FATAL: missing formal artifact manifest / FORMAL_BUILD_ROOT/WHEEL_SHA/SO_SHA" >&2
    return 2
  fi
}

resolve_pod_map() {
  # JOB_NAME required for NNODES>2 or FANOUT_PREFLIGHT; legacy 2-node may use MASTER/WORKER.
  if [[ -n "${JOB_NAME}" ]]; then
    POD_MAP_FILE="${POD_MAP_FILE:-${BACKUP_ROOT}/pod_map.json}"
    mkdir -p "$(dirname "${POD_MAP_FILE}")"
    python3 "${EXP_LOCAL}/pod_resolver.py" \
      --job-name "${JOB_NAME}" \
      --namespace "${NS}" \
      --kubeconfig "${KUBE}" \
      --kubectl "${KUBECTL}" \
      --expected-nodes "${NNODES}" \
      --out "${POD_MAP_FILE}" || return $?
    MASTER_POD="$(python3 -c "import json; pm=json.load(open('${POD_MAP_FILE}')); print([p['pod'] for p in pm['pods'] if p['node_rank']==0][0])")"
    WORKER_POD="$(python3 -c "import json; pm=json.load(open('${POD_MAP_FILE}')); print([p['pod'] for p in pm['pods'] if p['node_rank']==1][0])" 2>/dev/null || echo "")"
    return 0
  fi
  if (( NNODES > 2 )); then
    echo "FATAL: NNODES=${NNODES} requires JOB_NAME for pod map resolution" >&2
    return 2
  fi
  return 0
}

pod_map_entries_tsv() {
  if [[ -n "${POD_MAP_FILE}" && -f "${POD_MAP_FILE}" ]]; then
    python3 -c "
import json
pm=json.load(open('${POD_MAP_FILE}'))
for p in sorted(pm['pods'], key=lambda x: x['node_rank']):
    print(p['node_rank'], p['pod'])
"
    return 0
  fi
  echo "0 ${MASTER_POD}"
  if (( NNODES >= 2 )); then
    echo "1 ${WORKER_POD}"
  fi
}

fanout_parallel_exec() {
  # Bounded parallel pod_exec: args are remote command template with __POD__ __RANK__ placeholders.
  local cmd_tpl="$1"
  local -a pids=() ranks=() pods=() rcs=()
  local parallel="${FANOUT_PARALLEL}"
  (( parallel < 1 )) && parallel=1
  (( parallel > NNODES )) && parallel="${NNODES}"
  while read -r rank pod; do
    [[ -n "${rank}" ]] || continue
    local cmd="${cmd_tpl//__POD__/${pod}}"
    cmd="${cmd//__RANK__/${rank}}"
    pod_exec "${pod}" "${cmd}" &
    pids+=("$!")
    ranks+=("${rank}")
    pods+=("${pod}")
    if ((${#pids[@]} >= parallel)); then
      local i
      for i in "${!pids[@]}"; do
        set +e
        wait "${pids[$i]}"
        rcs+=("$?")
        set -e
      done
      pids=()
    fi
  done < <(pod_map_entries_tsv)
  local i
  for i in "${!pids[@]}"; do
    set +e
    wait "${pids[$i]}"
    rcs+=("$?")
    set -e
  done
  local fail=0
  for rc in "${rcs[@]}"; do
    if (( rc != 0 )); then fail=1; fi
  done
  return "${fail}"
}

pod_exec() {
  local pod="$1" cmd="$2"
  if [[ "${DRY_RUN}" == "1" ]]; then
    echo "[dry-run] pod_exec ${pod}: ${cmd}" | head -c 240
    echo
    return 0
  fi
  jump "\$K exec -n '${NS}' '${pod}' -- bash --noprofile --norc -lc $(printf '%q' "${cmd}")"
}

remote_side_effect() {
  # Skip ssh/kubectl/training side effects in dry-run; planning path still runs.
  if [[ "${DRY_RUN}" == "1" ]]; then
    echo "[dry-run] skip: $*"
    return 0
  fi
  "$@"
}

list_train_procs() {
  # Deprecated wrapper — use structured opponent_check.py (fail-closed).
  local pod="$1"
  pod_exec "${pod}" "python3 '${CODE_DIR}/opponent_check.py' --mode idle"
}

check_idle() {
  # Fail-closed：SSH/kubectl/proc 检查本身失败 ≠ 空闲。CLEAR=0 OPPONENT=10 CHECK_FAILED=20。
  local pod="$1" active rc
  set +e
  active="$(pod_exec "${pod}" "python3 '${CODE_DIR}/opponent_check.py' --mode idle")"
  rc=$?
  set -e
  if (( rc != 0 && rc != 10 && rc != 20 )); then
    # kubectl/ssh transport failure (not checker protocol rc)
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
  # rc==0：必须看到明确 CLEAR/OK；空/未知不可当 CLEAR。
  local last
  last="$(printf '%s\n' "${active}" | awk 'NF{p=$0} END{print p}')"
  if [[ -z "${last}" || ( "${last}" != "CLEAR" && "${last}" != "OK" ) ]]; then
    echo "FATAL: idle empty/unknown stdout → CHECK_FAILED pod=${pod} out=${active}" >&2
    return 20
  fi
  return 0
}

# 点火前数据集门禁：DATA_PATH 对应 .bin/.idx 须在目标 pod 挂载上可读；FAIL 不得 claim GROUP。
dataset_gate_preflight() {
  local gate_out="${DATA_GATE_OUT:-/tmp/mspti_dataset_gate_${GROUP_ID}.json}"
  local probe_pod="${DATA_PROBE_POD:-}"
  local pod_map_early="/tmp/mspti_pod_map_early_${GROUP_ID}.json"
  if [[ "${DRY_RUN}" == "1" || "${FIXTURE_RUN}" == "1" ]]; then
    echo "[megatron-ab] DATA_GATE_SKIP dry_run/fixture"
    return 0
  fi
  if [[ -z "${probe_pod}" && -n "${JOB_NAME}" ]]; then
    python3 "${EXP_LOCAL}/pod_resolver.py" \
      --job-name "${JOB_NAME}" \
      --namespace "${NS}" \
      --kubeconfig "${KUBE}" \
      --kubectl "${KUBECTL}" \
      --expected-nodes "${NNODES}" \
      --out "${pod_map_early}" || return $?
    probe_pod="$(python3 -c "import json; pm=json.load(open('${pod_map_early}')); print([p['pod'] for p in pm['pods'] if p['node_rank']==0][0])")"
    POD_MAP_FILE="${POD_MAP_FILE:-${pod_map_early}}"
    MASTER_POD="${probe_pod}"
  fi
  if [[ -z "${probe_pod}" ]]; then
    echo "FATAL: dataset gate requires JOB_NAME (running pods) or DATA_PROBE_POD" >&2
    return 2
  fi
  echo "[megatron-ab] DATA_GATE pod=${probe_pod} data_path=${DATA_PATH}"
  if ! python3 "${EXP_LOCAL}/preflight_dataset_gate.py" \
    --data-path "${DATA_PATH}" \
    --pod "${probe_pod}" \
    --namespace "${NS}" \
    --kubeconfig "${KUBE}" \
    --kubectl "${KUBECTL}" \
    --also-check-legacy \
    --out "${gate_out}"; then
    echo "FATAL: DATA_OK=FAIL — refuse GROUP claim (see ${gate_out})" >&2
    return 2
  fi
  echo "[megatron-ab] DATA_OK=PASS receipt=${gate_out}"
  return 0
}

# 每 attempt 点火前：master pod 上对计划 MASTER_PORT 做单点 bind 探测。
master_port_preflight() {
  local port="$1" out_dir="$2" attempt_id="$3"
  local ts preflight_json="" rc=0
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [[ "${DRY_RUN}" == "1" ]]; then
    preflight_json="$(python3 -c "import json; print(json.dumps({'status':'CLEAR','port':int('${port}'),'pod':'${MASTER_POD}','ts':'${ts}','attempt_id':'${attempt_id}','dry_run':True}, sort_keys=True))")"
  else
    set +e
    preflight_json="$(pod_exec "${MASTER_POD}" "python3 - <<'PY'
import json, socket, subprocess, sys
from pathlib import Path
port = int('${port}')
out = Path('${out_dir}')
ts = '${ts}'
result = {
    'status': 'CLEAR',
    'port': port,
    'pod': '${MASTER_POD}',
    'ts': ts,
    'attempt_id': '${attempt_id}',
}
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('', port))
    s.close()
except OSError as exc:
    result['status'] = 'BUSY'
    result['error'] = f'{exc.__class__.__name__}: {exc}'
    try:
        proc = subprocess.run(['ss', '-ltnp'], capture_output=True, text=True, timeout=10)
        needle = f':{port}'
        lines = [ln for ln in proc.stdout.splitlines() if needle in ln]
        result['ss_summary'] = lines[:20] if lines else proc.stdout.splitlines()[:10]
    except Exception as ss_exc:
        result['ss_summary_error'] = str(ss_exc)
out.mkdir(parents=True, exist_ok=True)
(out / 'port_preflight.json').write_text(json.dumps(result, indent=2, sort_keys=True), encoding='utf-8')
print(json.dumps(result, sort_keys=True))
sys.exit(0 if result.get('status') == 'CLEAR' else 1)
PY")"
    rc=$?
    set -e
  fi
  printf '%s\n' "${preflight_json}" > "${LOG_DIR}/port_preflight.json"
  printf '%s\n' "${preflight_json}" > "${LOG_DIR}/port_preflight_${attempt_id}.json"
  if [[ "${DRY_RUN}" != "1" && ${rc} -ne 0 ]]; then
    echo "FATAL: master port ${port} busy on ${MASTER_POD}" >&2
    echo "${preflight_json}" >&2
    mark_group_invalid "master_port_busy:${port}" "${attempt_id}" "port_preflight"
    exit 16
  fi
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
  # 仅在本机 BACKUP_ROOT 已由本 run 完整 claim 后写入 INVALID；否则只拒绝启动。
  if (( LOCAL_GROUP_CLAIMED == 1 )) && [[ -d "${BACKUP_ROOT}" ]]; then
    mark_group_invalid "${reason}" "${ACTIVE_ATTEMPT_ID:-}" "${stage}" ""
  fi
  exit "${rc}"
}

require_idle_or_invalid_all() {
  local stage="${1:-pre_start}"
  while read -r rank pod; do
    [[ -n "${rank}" ]] || continue
    require_idle_or_invalid "${pod}" "${stage}"
  done < <(pod_map_entries_tsv)
}

# 只杀本 pod 带 exact RUN_MARKER 的进程；验收清零；禁止 || true 后宣称成功。
kill_our_attempt() {
  local out="$1"
  local marker="${2:-}"
  if [[ -z "${marker}" ]]; then
    marker="${RUN_MARKER:-}"
  fi
  if [[ -z "${marker}" || ${#marker} -lt 8 ]]; then
    echo "CLEANUP_REJECTED_MARKER empty_or_short" >&2
    return 2
  fi
  local node_id=0
  local cleanup_rc=0
  while read -r rank pod; do
    [[ -n "${rank}" ]] || continue
    node_id="${rank}"
    set +e
    pod_exec "${pod}" \
      "python3 '${CODE_DIR}/kill_attempt.py' --out-dir '${out}' --node-id ${node_id} --run-marker '${marker}'"
    local rc=$?
    set -e
    if (( rc != 0 )); then
      echo "CLEANUP_INCOMPLETE pod=${pod} node=${node_id} rc=${rc}" >&2
      cleanup_rc=1
    fi
  done < <(pod_map_entries_tsv)
  return "${cleanup_rc}"
}

mark_group_invalid() {
  local reason="$1"
  local attempt="${2:-}"
  local stage="${3:-unknown}"
  local cleanup_status="${4:-}"
  if (( LOCAL_GROUP_CLAIMED != 1 )); then
    echo "SKIP_INVALID_WRITE reason=${reason} (local group not claimed)" >&2
    return 0
  fi
  # BACKUP_ROOT 已由 atomic claim 创建；禁止 mkdir -p 回写旧树。
  set +e
  python3 - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "${EXP_LOCAL}")
from fanout_orchestrator import mark_group_invalid
extra = {}
cs = "${cleanup_status}"
if cs:
    extra["cleanup_status"] = cs
mark_group_invalid(
    Path("${BACKUP_ROOT}"),
    group_id="${GROUP_ID}",
    reason="""${reason}""",
    attempt_id="${attempt}",
    stage="${stage}",
    extra=extra or None,
)
print("GROUP_INVALID written reason=${reason} stage=${stage} attempt=${attempt}")
PY
  local wrc=$?
  set -e
  if [[ -f "${BACKUP_ROOT}/GROUP_INVALID.json" ]]; then
    GROUP_INVALID_WRITTEN=1
    return 0
  fi
  echo "FATAL: GROUP_INVALID write failed reason=${reason} stage=${stage} wrc=${wrc}" >&2
  GROUP_INVALID_WRITTEN=0
  return 1
}

ensure_group_invalid_written() {
  local reason="$1" attempt="${2:-}" stage="${3:-unknown}" cleanup_status="${4:-}"
  if [[ -f "${BACKUP_ROOT}/GROUP_INVALID.json" ]]; then
    GROUP_INVALID_WRITTEN=1
    return 0
  fi
  set +e
  mark_group_invalid "${reason}" "${attempt}" "${stage}" "${cleanup_status}"
  local wrc=$?
  set -e
  if [[ ! -f "${BACKUP_ROOT}/GROUP_INVALID.json" ]]; then
    echo "FATAL: GROUP_INVALID missing after mark reason=${reason} stage=${stage} wrc=${wrc}" >&2
    return 1
  fi
  return 0
}

GROUP_COMPLETE_FLAG=0
GROUP_INVALID_WRITTEN=0
LOCAL_GROUP_CLAIMED=0
ACTIVE_OUT_DIR=""
ACTIVE_RUN_MARKER=""
ACTIVE_ATTEMPT_ID=""

_group_trap_handler() {
  local sig="${1:-EXIT}"
  local ec="${2:-0}"
  # Avoid re-entrancy
  trap - ERR INT TERM EXIT
  if (( GROUP_COMPLETE_FLAG == 1 )); then
    return 0
  fi
  # 未认领本机目录前（含 REFUSE_LOCAL_GROUP_REUSE）禁止写盘，避免污染旧 group。
  if (( LOCAL_GROUP_CLAIMED != 1 )); then
    echo "TRAP_${sig} ec=${ec} — skip INVALID write (local group not claimed)" >&2
    if [[ "${sig}" == "INT" ]]; then exit 130; fi
    if [[ "${sig}" == "TERM" ]]; then exit 143; fi
    if [[ "${sig}" == "EXIT" && "${ec}" -ne 0 ]]; then exit "${ec}"; fi
    return 0
  fi
  if (( GROUP_INVALID_WRITTEN == 1 )) || [[ -f "${BACKUP_ROOT}/GROUP_INVALID.json" ]]; then
    echo "TRAP_${sig} ec=${ec} — GROUP_INVALID already written" >&2
    if [[ "${sig}" == "INT" ]]; then exit 130; fi
    if [[ "${sig}" == "TERM" ]]; then exit 143; fi
    if [[ "${sig}" == "EXIT" && "${ec}" -ne 0 ]]; then exit "${ec}"; fi
    return 0
  fi
  # Successful path must set GROUP_COMPLETE_FLAG; bare exit 0 without it is INVALID.
  echo "TRAP_${sig} ec=${ec} — marking GROUP_INVALID (no GROUP_COMPLETE)" >&2
  local cleanup_status=""
  if [[ -n "${ACTIVE_OUT_DIR}" && -n "${ACTIVE_RUN_MARKER}" ]]; then
    set +e
    kill_our_attempt "${ACTIVE_OUT_DIR}" "${ACTIVE_RUN_MARKER}"
    local krc=$?
    set -e
    if (( krc == 0 )); then cleanup_status="CLEANUP_OK"; else cleanup_status="CLEANUP_INCOMPLETE"; fi
  fi
  set +e
  mark_group_invalid "trap_${sig}_ec=${ec}" "${ACTIVE_ATTEMPT_ID}" "trap_${sig}" "${cleanup_status}"
  set -e
  if [[ "${sig}" == "INT" ]]; then exit 130; fi
  if [[ "${sig}" == "TERM" ]]; then exit 143; fi
  if [[ "${sig}" == "EXIT" && "${ec}" -ne 0 ]]; then exit "${ec}"; fi
}

# Traps installed only after local group is fully claimed (claim + BACKUP + LOG).

# 对方进程：共享 opponent_check.py（subprocess.run ps，检查 returncode）。
# 三态：CLEAR(0) / OPPONENT(10) / CHECK_FAILED(20)。绝不用 || true / os.popen。
# kubectl/SSH 瞬时失败：有界重试；真实 OPPONENT 立即让路；耗尽仍 fail-closed。
# 发现对方或检查失败：只停本 RUN_MARKER，绝不杀对方。
YIELD_TRANSPORT_RETRIES="${YIELD_TRANSPORT_RETRIES:-4}"
YIELD_TRANSPORT_BACKOFF_S="${YIELD_TRANSPORT_BACKOFF_S:-0.5}"

yield_check_pod_with_retry() {
  local pod="$1" node_id="$2" out="$3" marker="$4"
  local attempt=0 result rc status detail yrc
  while (( attempt < YIELD_TRANSPORT_RETRIES )); do
    set +e
    result="$(pod_exec "${pod}" \
      "python3 '${CODE_DIR}/opponent_check.py' --mode yield --out-dir '${out}' --run-marker '${marker}' --node-id ${node_id}")"
    rc=$?
    set -e
    {
      IFS= read -r status || status=""
      IFS= read -r detail || detail=""
      IFS= read -r yrc || yrc=""
    } < <(
      EXP_LOCAL_FOR_YIELD="${EXP_LOCAL}" \
      YIELD_OUT="${result}" YIELD_RC="${rc}" \
      python3 - <<'PY'
import os, sys
sys.path.insert(0, os.environ.get("EXP_LOCAL_FOR_YIELD", "."))
from local_group_guard import classify_yield_attempt
a = classify_yield_attempt(os.environ.get("YIELD_OUT", ""), int(os.environ.get("YIELD_RC", "1")))
if a.transient:
    print("TRANSIENT")
    print(a.detail)
    print("0")
elif a.verdict:
    print(a.verdict.status)
    print(a.verdict.detail)
    print(str(a.verdict.rc))
else:
    print("CHECK_FAILED")
    print("no_verdict")
    print("20")
PY
    )
    if [[ -z "${status}" || -z "${yrc}" ]]; then
      echo "CHECK_FAILED|classify_parse_incomplete pod=${pod} node=${node_id}" >&2
      printf '%s\n' "CHECK_FAILED|classify_parse_incomplete"
      return 20
    fi
    if [[ "${status}" == "TRANSIENT" ]]; then
      attempt=$((attempt + 1))
      echo "YIELD_RETRY attempt=${attempt}/${YIELD_TRANSPORT_RETRIES} pod=${pod} node=${node_id} ${detail}" >&2
      sleep "${YIELD_TRANSPORT_BACKOFF_S}"
      continue
    fi
    printf '%s\n' "${result}"
    return "${yrc}"
  done
  echo "CHECK_FAILED|transport_exhausted pod=${pod} node=${node_id} attempts=${YIELD_TRANSPORT_RETRIES} last=${detail}" >&2
  printf '%s\n' "CHECK_FAILED|transport_exhausted"
  return 20
}

yield_if_opponent() {
  local out="$1" marker="$2"
  local rank pod result rc_m verdict status yrc detail
  local merge_dir krc i n
  merge_dir="$(mktemp -d "${TMPDIR:-/tmp}/mspti-yield-merge.XXXXXX")"
  n=0
  set +e
  while read -r rank pod; do
    [[ -n "${rank}" ]] || continue
    result="$(yield_check_pod_with_retry "${pod}" "${rank}" "${out}" "${marker}")"
    rc_m=$?
    printf '%s' "${rank}" > "${merge_dir}/rank_${n}"
    printf '%s' "${pod}" > "${merge_dir}/pod_${n}"
    printf '%s' "${rc_m}" > "${merge_dir}/rc_${n}"
    printf '%s' "${result}" > "${merge_dir}/stdout_${n}"
    n=$((n + 1))
  done < <(pod_map_entries_tsv)
  set -e
  verdict="$(
    EXP_LOCAL_FOR_YIELD="${EXP_LOCAL}" MERGE_DIR="${merge_dir}" POD_COUNT="${n}" \
    python3 - <<'PY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ.get("EXP_LOCAL_FOR_YIELD", "."))
from local_group_guard import merge_fanout_pod_checks
merge_dir = Path(os.environ["MERGE_DIR"])
count = int(os.environ.get("POD_COUNT", "0"))
checks = []
for i in range(count):
    checks.append({
        "rank": int((merge_dir / f"rank_{i}").read_text(encoding="utf-8")),
        "pod": (merge_dir / f"pod_{i}").read_text(encoding="utf-8"),
        "rc": int((merge_dir / f"rc_{i}").read_text(encoding="utf-8").strip() or "99"),
        "stdout": (merge_dir / f"stdout_{i}").read_text(encoding="utf-8", errors="replace"),
    })
v = merge_fanout_pod_checks(checks)
print(v.status)
print(v.rc)
print(v.detail)
PY
  )"
  rm -rf "${merge_dir}"
  status="$(sed -n '1p' <<<"${verdict}")"
  yrc="$(sed -n '2p' <<<"${verdict}")"
  detail="$(sed -n '3,$p' <<<"${verdict}")"
  if [[ "${status}" == "CLEAR" ]]; then
    return 0
  fi
  if [[ "${status}" == "OPPONENT" ]]; then
    echo "YIELD: opponent training detected; stopping our attempt only" >&2
  else
    echo "YIELD_CHECK_FAILED: SSH/kubectl/proc check unsafe; stopping our attempt only" >&2
  fi
  echo "detail: ${detail}" >&2
  set +e
  kill_our_attempt "${out}" "${marker:-}"
  krc=$?
  set -e
  if (( krc != 0 )); then
    echo "CLEANUP_INCOMPLETE after yield status=${status} kill_rc=${krc}" >&2
  fi
  YIELD_KILL_RC="${krc}"
  YIELD_STATUS="${status}"
  return "${yrc}"
}

pull_attempt_evidence() {
  # Success-path pull: required artifacts must land; failure → non-zero + EVIDENCE_INCOMPLETE.
  # Fast path: whole-tree tar into exclusive .pull-* temp under *this* new group only.
  # On truncation/transport fail → leave temp in place (no quarantine rename), then
  # mkdir final (exist_ok=False) + 16MiB chunk fill via transfer_recovery fallback.
  # Never rmtree/unlink pre-existing attempt trees. No publish-staging rename.
  # Do NOT use `|| true`.
  local out="$1" attempt_id="$2"
  local mode="${3:-required}"  # required | best_effort
  local status="OK"
  local pull_mode="FAST_PATH"
  local fast_err=""
  local staging=""
  local publish_target="${BACKUP_ROOT}/${attempt_id}"

  if [[ -e "${publish_target}" ]]; then
    echo "EVIDENCE_INCOMPLETE=final_attempt_exists attempt=${attempt_id} path=${publish_target}" >&2
    if [[ "${mode}" == "required" ]]; then
      echo "FAIL_EVIDENCE=${publish_target} status=EVIDENCE_INCOMPLETE pull_mode=FINAL_EXISTS" >&2
      return 12
    fi
    status="EVIDENCE_INCOMPLETE"
  fi

  # Formal success path: follow the sealed manifest directly with 16MiB chunks.
  # Avoid whole-tree tar and any staging publish/rename path.
  if [[ "${mode}" == "required" ]]; then
    set +e
    python3 "${EXP_LOCAL}/transfer_recovery.py" fallback \
      --backup-root "${BACKUP_ROOT}" \
      --attempt-id "${attempt_id}" \
      --remote-root "${out}" \
      --claim-path "${CLAIM_PARENT}/${GROUP_ID}.claim" \
      --group-plan "${BACKUP_ROOT}/group_plan.json" \
      --group-id "${GROUP_ID}" \
      --plan-hash "${PLAN_HASH}" \
      --jump "${JUMP}" \
      --kubeconfig "${KUBE}" \
      --kubectl "${KUBECTL}" \
      --namespace "${NS}" \
      --pod "${MASTER_POD}" \
      --expected-ranks "${EXPECTED_RANKS}" \
      --expected-nodes "${NNODES}" \
      --capture-step "${CAPTURE_ITER}" \
      --fast-path-error "chunk_pull_only"
    local chunk_rc=$?
    set -e
    if (( chunk_rc != 0 )); then
      echo "EVIDENCE_INCOMPLETE=chunk_pull_failed attempt=${attempt_id} rc=${chunk_rc}" >&2
      echo "FAIL_EVIDENCE=${publish_target} status=EVIDENCE_INCOMPLETE pull_mode=CHUNK_FAILED" >&2
      return 12
    fi
    echo "EVIDENCE_STATUS=OK pull_mode=CHUNK_16M FAIL_EVIDENCE=${publish_target}"
    return 0
  fi

  # Failed-attempt evidence remains best-effort and may use the fast tar path.
  set +e
  staging="$(
    python3 "${EXP_LOCAL}/transfer_recovery.py" create-staging \
      --backup-root "${BACKUP_ROOT}" \
      --attempt-id "${attempt_id}" \
      --kind pull
  )"
  local stage_rc=$?
  set -e
  if (( stage_rc != 0 )) || [[ -z "${staging}" || ! -d "${staging}" ]]; then
    echo "EVIDENCE_INCOMPLETE=create_staging_failed attempt=${attempt_id} rc=${stage_rc}" >&2
    if [[ "${mode}" == "required" ]]; then
      echo "FAIL_EVIDENCE=${BACKUP_ROOT}/${attempt_id} status=EVIDENCE_INCOMPLETE pull_mode=STAGING_FAILED" >&2
      return 12
    fi
    status="EVIDENCE_INCOMPLETE"
  fi

  if [[ -n "${staging}" && -d "${staging}" ]]; then
    set +e
    jump_n "\$K exec -n '${NS}' '${MASTER_POD}' -- tar -C '${out}' -cf - ." \
      | tar -C "${staging}" -xf -
    local tar_rc=$?
    set -e
  else
    tar_rc=1
  fi

  if (( tar_rc != 0 )); then
    fast_err="tar_pull_failed rc=${tar_rc}"
    echo "EVIDENCE_INCOMPLETE=${fast_err} attempt=${attempt_id} → CHUNK_FALLBACK (leave temp=${staging})" >&2
    # Leave fast-path temp in place; do NOT quarantine-staging / publish-staging.
    set +e
    python3 "${EXP_LOCAL}/transfer_recovery.py" fallback \
      --backup-root "${BACKUP_ROOT}" \
      --attempt-id "${attempt_id}" \
      --remote-root "${out}" \
      --claim-path "${CLAIM_PARENT}/${GROUP_ID}.claim" \
      --group-plan "${BACKUP_ROOT}/group_plan.json" \
      --group-id "${GROUP_ID}" \
      --plan-hash "${PLAN_HASH}" \
      --jump "${JUMP}" \
      --kubeconfig "${KUBE}" \
      --kubectl "${KUBECTL}" \
      --namespace "${NS}" \
      --pod "${MASTER_POD}" \
      --fast-path-error "${fast_err}"
    local fb_rc=$?
    set -e
    if (( fb_rc != 0 )); then
      status="EVIDENCE_INCOMPLETE"
      pull_mode="FALLBACK_FAILED"
      echo "EVIDENCE_INCOMPLETE=fallback_failed attempt=${attempt_id} rc=${fb_rc}" >&2
      if [[ "${mode}" == "required" ]]; then
        echo "FAIL_EVIDENCE=${BACKUP_ROOT}/${attempt_id} status=${status} pull_mode=${pull_mode}" >&2
        return 12
      fi
    else
      pull_mode="FALLBACK_RECOVERED"
      echo "PULL_MODE=FALLBACK_RECOVERED attempt=${attempt_id}" >&2
    fi
  else
    # Fast path OK in temp: local seal then mv temp → final (final must still be absent).
    # This is NOT publish-staging CLI; no quarantine rename path.
    echo "PULL_MODE=FAST_PATH_TEMP attempt=${attempt_id} staging=${staging}" >&2
    for req in attempt_manifest.json attempt_manifest.sha256 artifact_digest.json run.log; do
      if [[ ! -f "${staging}/${req}" ]]; then
        status="EVIDENCE_INCOMPLETE"
        echo "EVIDENCE_INCOMPLETE=missing_${req} attempt=${attempt_id} staging" >&2
        if [[ "${mode}" == "required" ]]; then
          echo "FAIL_EVIDENCE=${staging} status=${status} pull_mode=FAST_PATH_TEMP" >&2
          return 12
        fi
      fi
    done
    if [[ -f "${staging}/attempt_manifest.sha256" ]]; then
      REMOTE_MAN_HASH="$(tr -d '[:space:]' < "${staging}/attempt_manifest.sha256")"
      if ! python3 "${EXP_LOCAL}/provenance.py" \
        --out-dir "${staging}" \
        --phase local_seal \
        --run-id "${attempt_id}" \
        --remote-manifest-sha256 "${REMOTE_MAN_HASH}" \
        | tee -a "${BACKUP_ROOT}/launcher_local.log"; then
        status="EVIDENCE_INCOMPLETE"
        echo "EVIDENCE_INCOMPLETE=local_seal_failed attempt=${attempt_id} staging" >&2
        set +e
        rm -f "${staging}/LOCAL_VERIFIED_SEAL.json" "${staging}/LOCAL_VERIFIED_SEAL.json.tmp"
        set -e
        if [[ "${mode}" == "required" ]]; then
          echo "FAIL_EVIDENCE=${staging} status=${status} pull_mode=FAST_PATH_TEMP" >&2
          return 12
        fi
      else
        if [[ -e "${publish_target}" ]]; then
          status="EVIDENCE_INCOMPLETE"
          pull_mode="FINAL_EXISTS"
          echo "EVIDENCE_INCOMPLETE=final_exists_before_mv attempt=${attempt_id}" >&2
          set +e
          rm -f "${staging}/LOCAL_VERIFIED_SEAL.json" "${staging}/LOCAL_VERIFIED_SEAL.json.tmp"
          set -e
          if [[ "${mode}" == "required" ]]; then
            echo "FAIL_EVIDENCE=${staging} status=${status} pull_mode=${pull_mode}" >&2
            return 12
          fi
        else
          set +e
          mv "${staging}" "${publish_target}"
          local mv_rc=$?
          set -e
          if (( mv_rc != 0 )) || [[ ! -d "${publish_target}" ]]; then
            status="EVIDENCE_INCOMPLETE"
            pull_mode="MV_FAILED"
            echo "EVIDENCE_INCOMPLETE=mv_temp_to_final_failed attempt=${attempt_id} rc=${mv_rc}" >&2
            if [[ "${mode}" == "required" ]]; then
              echo "FAIL_EVIDENCE=${staging} status=${status} pull_mode=${pull_mode}" >&2
              return 12
            fi
          else
            pull_mode="FAST_PATH"
            echo "PULL_MODE=FAST_PATH attempt=${attempt_id}" >&2
          fi
        fi
      fi
    elif [[ "${mode}" == "required" ]]; then
      status="EVIDENCE_INCOMPLETE"
      echo "EVIDENCE_INCOMPLETE=missing_manifest_sha attempt=${attempt_id}" >&2
      echo "FAIL_EVIDENCE=${staging} status=${status} pull_mode=FAST_PATH_TEMP" >&2
      return 12
    fi
  fi

  # Post-fill checks against final attempt dir (chunk fallback already sealed in-place).
  if [[ "${pull_mode}" == "FALLBACK_RECOVERED" || "${pull_mode}" == "FAST_PATH" ]]; then
    for req in attempt_manifest.json attempt_manifest.sha256 artifact_digest.json run.log; do
      if [[ ! -f "${BACKUP_ROOT}/${attempt_id}/${req}" ]]; then
        status="EVIDENCE_INCOMPLETE"
        echo "EVIDENCE_INCOMPLETE=missing_${req} attempt=${attempt_id}" >&2
        if [[ "${mode}" == "required" ]]; then
          echo "FAIL_EVIDENCE=${BACKUP_ROOT}/${attempt_id} status=${status} pull_mode=${pull_mode}" >&2
          return 12
        fi
      fi
    done
    if [[ "${pull_mode}" == "FAST_PATH" && -f "${BACKUP_ROOT}/${attempt_id}/attempt_manifest.sha256" ]]; then
      REMOTE_MAN_HASH="$(tr -d '[:space:]' < "${BACKUP_ROOT}/${attempt_id}/attempt_manifest.sha256")"
      if ! python3 "${EXP_LOCAL}/provenance.py" \
        --out-dir "${BACKUP_ROOT}/${attempt_id}" \
        --phase local_seal \
        --run-id "${attempt_id}" \
        --remote-manifest-sha256 "${REMOTE_MAN_HASH}" \
        | tee -a "${BACKUP_ROOT}/${attempt_id}/launcher_local.log"; then
        status="EVIDENCE_INCOMPLETE"
        echo "EVIDENCE_INCOMPLETE=local_seal_recheck_failed attempt=${attempt_id}" >&2
        if [[ "${mode}" == "required" ]]; then
          echo "FAIL_EVIDENCE=${BACKUP_ROOT}/${attempt_id} status=${status} pull_mode=${pull_mode}" >&2
          return 12
        fi
      fi
    fi
  fi
  echo "EVIDENCE_STATUS=${status} pull_mode=${pull_mode} FAIL_EVIDENCE=${BACKUP_ROOT}/${attempt_id}"
  if [[ "${status}" != "OK" && "${mode}" == "required" ]]; then
    return 12
  fi
  return 0
}

echo "[megatron-ab] GROUP_ID=${GROUP_ID}"
echo "[megatron-ab] BACKUP_ROOT=${BACKUP_ROOT}"
echo "[megatron-ab] LOG_DIR=${LOG_DIR}"
echo "[megatron-ab] ATTEMPTS=${ATTEMPTS}"
echo "[megatron-ab] 预计：warmup 可选 + 每 attempt 5–12 min；torch flush 可能更长；6 attempts ≈ 1–2h"

# 容量门禁在 GROUP claim / detach 之前；正式模式 fail-closed（无 CAPACITY_JUMP_ONLY）。
formal_capacity_preflight() {
  local cap_out="${1:-/tmp/mspti_capacity_${GROUP_ID}.json}"
  local peak_bytes="${CONTROL_PEAK_BYTES:-0}"
  local peak_receipt="${CONTROL_PEAK_RECEIPT:-}"
  if [[ "${FORMAL_MODE}" == "1" ]]; then
    if [[ -z "${peak_receipt}" ]]; then
      echo "FATAL: formal mode requires CONTROL_PEAK_RECEIPT from measured_full_six_arm" >&2
      return 4
    fi
    if [[ ! -f "${peak_receipt}" ]]; then
      echo "FATAL: missing CONTROL_PEAK_RECEIPT=${peak_receipt}" >&2
      return 4
    fi
    peak_bytes="$(python3 -c "import json; print(json.load(open('${peak_receipt}'))['control_peak_bytes'])")"
  fi
  mkdir -p "$(dirname "${cap_out}")"
  local storage_args=(
    --group-id "${GROUP_ID}"
    --check-capacity
    --jump-path /
    --afs-root "${AFS_ROOT}"
    --jump-control-parent "${JUMP_CONTROL_PARENT}"
    --control-peak-bytes "${peak_bytes}"
    --payload-estimate-bytes "${PAYLOAD_ESTIMATE_BYTES:-84318646878}"
    --seal-backend "${SEAL_BACKEND}"
    --code-hash "${CODE_HASH}"
    --out "${cap_out}"
  )
  if [[ -n "${peak_receipt}" ]]; then
    storage_args+=(--control-peak-receipt "${peak_receipt}")
  fi
  if [[ "${FORMAL_MODE}" == "1" ]]; then
    storage_args+=(--formal-mode)
  fi
  if [[ "${FANOUT_PREFLIGHT}" == "1" || "${DRY_RUN}" == "1" || "${FIXTURE_RUN}" == "1" ]]; then
    storage_args+=(--jump-only)
  fi
  if [[ -n "${AFS_CAPACITY_RECEIPT:-}" && -f "${AFS_CAPACITY_RECEIPT}" ]]; then
    storage_args+=(--mock-afs-avail-bytes "$(python3 -c "import json; print(json.load(open('${AFS_CAPACITY_RECEIPT}'))['afs_avail_bytes'])")")
    echo "[megatron-ab] AFS_CAPACITY_RECEIPT=${AFS_CAPACITY_RECEIPT} (pod mount; jump overlay excluded)"
  elif [[ "${FORMAL_MODE}" == "1" && "${FANOUT_PREFLIGHT}" != "1" && "${DRY_RUN}" != "1" && "${FIXTURE_RUN}" != "1" ]]; then
    echo "FATAL: formal mode requires AFS_CAPACITY_RECEIPT from pod PVC mount" >&2
    return 4
  fi
  python3 "${EXP_LOCAL}/storage_contract.py" "${storage_args[@]}" \
    || { echo "FATAL: capacity preflight failed (see ${cap_out})" >&2; return 2; }
  echo "[megatron-ab] CAPACITY_OK receipt=${cap_out}"
  return 0
}

set +e
_cap_dir=""
for _cand in "${JUMP_CONTROL_PARENT}/.preflight" "${BACKUP_PARENT}/.preflight" "${TMPDIR:-/tmp}/mspti-preflight"; do
  if mkdir -p "${_cand}" 2>/dev/null; then
    _cap_dir="${_cand}"
    break
  fi
done
[[ -n "${_cap_dir}" ]] || _cap_dir="${TMPDIR:-/tmp}/mspti-preflight"
formal_capacity_preflight "${_cap_dir}/${GROUP_ID}_capacity.json"
cap_preflight_rc=$?
set -e
if (( cap_preflight_rc != 0 )); then
  echo "FATAL: capacity preflight failed rc=${cap_preflight_rc}" >&2
  exit "${cap_preflight_rc}"
fi

set +e
dataset_gate_preflight
data_gate_rc=$?
set -e
if (( data_gate_rc != 0 )); then
  echo "FATAL: dataset gate failed rc=${data_gate_rc}" >&2
  exit "${data_gate_rc}"
fi

# 任何写盘前：原子 claim GROUP_ID，再单层 mkdir BACKUP/LOG（无 -p）。
set +e
claim_local_group_dirs
claim_rc=$?
set -e
if (( claim_rc != 0 )); then
  echo "FATAL: local group claim failed rc=${claim_rc}" >&2
  exit "${claim_rc}"
fi
LOCAL_GROUP_CLAIMED=1
trap '_group_trap_handler ERR $?' ERR
trap '_group_trap_handler INT 130' INT
trap '_group_trap_handler TERM 143' TERM
trap '_group_trap_handler EXIT $?' EXIT
echo "[megatron-ab] local dirs claimed BACKUP_ROOT + LOG_DIR (atomic)"
if [[ "${DRY_RUN}" == "1" || "${FIXTURE_RUN}" == "1" ]]; then
  JUMP_CONTROL_PARENT="${BACKUP_ROOT}/.jump_control"
  JUMP_CONTROL_ROOT="${JUMP_CONTROL_PARENT}/${GROUP_ID}"
fi
mkdir -p "${JUMP_CONTROL_ROOT}"
# Copy pre-claim capacity receipt into jump control tree (already validated).
if [[ -f "${_cap_dir}/${GROUP_ID}_capacity.json" ]]; then
  cp -f "${_cap_dir}/${GROUP_ID}_capacity.json" \
    "${JUMP_CONTROL_ROOT}/capacity_receipt.json"
fi
python3 "${EXP_LOCAL}/storage_contract.py" \
  --group-id "${GROUP_ID}" \
  --jump-control-parent "${JUMP_CONTROL_PARENT}" \
  --out "${JUMP_CONTROL_ROOT}/storage_paths.json" \
  || true
if [[ "${FORMAL_MODE}" == "1" ]]; then
  python3 "${EXP_LOCAL}/storage_contract.py" \
    --group-id "${GROUP_ID}" \
    --jump-control-parent "${JUMP_CONTROL_PARENT}" \
    --scan-control-leak \
    --out "${JUMP_CONTROL_ROOT}/control_leak_scan.json" \
    || { echo "FATAL: CONTROL_PAYLOAD_LEAK on jump control tree" >&2; exit 2; }
fi

if [[ "${DRY_RUN}" != "1" && "${FIXTURE_RUN}" != "1" ]]; then
  remote_side_effect resolve_pod_map || exit $?
  cp -f "${POD_MAP_FILE}" "${JUMP_CONTROL_ROOT}/pod_map.json" 2>/dev/null || true
  echo "[megatron-ab] POD_MAP=${POD_MAP_FILE}"
fi

if [[ "${FANOUT_PREFLIGHT}" == "1" ]]; then
  echo "[megatron-ab] FANOUT_PREFLIGHT=1 (no training)"
  remote_side_effect load_formal_artifact_manifest || true
  # Sync code to master only for preflight import check
  COPYFILE_DISABLE=1 tar -C "${EXP_LOCAL}" -cf - pod_resolver.py fanout_preflight.py storage_contract.py \
    | remote_side_effect jump "\$K exec -i -n '${NS}' '${MASTER_POD}' -- bash --noprofile --norc -lc 'mkdir -p ${CODE_DIR} && tar -C ${CODE_DIR} -xf -'" || true
  export KUBECONFIG="${KUBE}" KUBECTL="${KUBECTL}" NS="${NS}"
  if ! python3 "${EXP_LOCAL}/fanout_preflight.py" \
    --pod-map "${POD_MAP_FILE}" \
    --receipt-dir "${JUMP_CONTROL_ROOT}/fanout_preflight" \
    --code-dir "${CODE_DIR}" \
    --parallel "${FANOUT_PARALLEL}" \
    --wheel-sha256 "${FORMAL_WHEEL_SHA256}" \
    --so-sha256 "${FORMAL_SO_SHA256}"; then
    echo "FATAL: FANOUT_PREFLIGHT failed" >&2
    exit 2
  fi
  echo "[megatron-ab] FANOUT_PREFLIGHT_PASS done=${NNODES}/${NNODES}"
  GROUP_COMPLETE_FLAG=1
  exit 0
fi

# Shared plan construction (formal / DRY_RUN / FIXTURE_RUN).
PLAN_ARGS=(
  --group-id "${GROUP_ID}"
  --code-dir "${EXP_LOCAL}"
  --code-hash "${CODE_HASH}"
  --backup-root "${BACKUP_ROOT}"
  --group-dir "${GROUP_DIR}"
  --attempts "${ATTEMPTS}"
  --nnodes "${NNODES}"
  --nproc "${NPROC}"
  --train-iters "${TRAIN_ITERS}"
  --capture-iter "${CAPTURE_ITER}"
  --base-port "${BASE_PORT}"
  --min-raw "${MIN_RAW_KERNELS}"
  --min-comm "${MIN_COMM}"
  --rel-raw "${REL_RAW_FLOOR}"
  --rel-comm "${REL_COMM_FLOOR}"
  --design-sequence "${DESIGN_SEQUENCE}"
)

if [[ "${FIXTURE_RUN}" == "1" ]]; then
  # Local-only full postprocess; never touch cluster.
  # Use tiny world for fixture speed while keeping six-attempt structure.
  python3 "${EXP_LOCAL}/ab_plan.py" \
    --fixture-run \
    --group-id "${GROUP_ID}" \
    --code-dir "${EXP_LOCAL}" \
    --code-hash "${CODE_HASH}" \
    --backup-root "${BACKUP_ROOT}" \
    --group-dir "${BACKUP_ROOT}" \
    --attempts "${ATTEMPTS}" \
    --nnodes 1 \
    --nproc 2 \
    --train-iters "${TRAIN_ITERS}" \
    --capture-iter "${CAPTURE_ITER}" \
    --base-port "${BASE_PORT}" \
    --min-raw 80 \
    --min-comm 20 \
    --rel-raw "${REL_RAW_FLOOR}" \
    --rel-comm "${REL_COMM_FLOOR}"
  echo "[megatron-ab] FIXTURE_RUN complete; no remote side effects"
  GROUP_COMPLETE_FLAG=1
  exit 0
fi

if [[ "${DRY_RUN}" == "1" ]]; then
  # Construct + validate full six-attempt plan/provenance/thresholds/paths/postprocess.
  # Does NOT skip plan construction; only skips ssh/kubectl/training below.
  python3 "${EXP_LOCAL}/ab_plan.py" --emit-plan "${PLAN_ARGS[@]}"
  # Also materialize per-attempt config stubs + content-address path checks locally.
  python3 - <<PY
import json
from pathlib import Path
plan = json.loads(Path("${BACKUP_ROOT}/group_plan.json").read_text())
assert len(plan["attempts"]) == (1 if plan.get("design_sequence") in ("smoke_normal_v1", "smoke_ours_v1") else 6)
assert plan.get("plan_hash"), "missing plan_hash"
print("DRY_RUN_PLAN_HASH", plan["plan_hash"])
root = Path("${BACKUP_ROOT}")
for a in plan["attempts"]:
    d = root / a["attempt_id"]
    d.mkdir(parents=True, exist_ok=True)
    cfg = {
        "attempt_id": a["attempt_id"],
        "arm": a["arm"],
        "master_port": a["master_port"],
        "run_marker": a["run_marker"],
        "out_dir": a["out_dir"],
        "frozen_thresholds": plan["frozen_thresholds"],
        "code_dir": plan["code_dir"],
        "code_hash": plan["code_hash"],
        "content_addressed": True,
        "postprocess": a["postprocess"],
        "required_artifacts": a["required_artifacts"],
        "dry_run": True,
        "plan_hash": plan.get("plan_hash"),
    }
    (d / "dry_run_attempt_config.json").write_text(
        json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8"
    )
    assert a["out_dir"].endswith(a["attempt_id"])
print("DRY_RUN_ATTEMPT_CONFIGS_OK", len(plan["attempts"]))
print("DRY_RUN_POSTPROCESS", plan.get("postprocess"))
print("DRY_RUN_SEAL_ORDER", plan.get("seal_order"))
PY
  echo "[megatron-ab] DRY_RUN complete; no remote side effects"
  GROUP_COMPLETE_FLAG=1
  exit 0
fi

# 唯一 group：已存在则拒绝
if jump "\$K exec -n '${NS}' '${MASTER_POD}' -- test -e '${GROUP_DIR}'" >/dev/null 2>&1; then
  echo "FATAL: group dir already exists: ${GROUP_DIR}" >&2
  exit 9
fi

# Emit the same plan object formal path consumes.
python3 "${EXP_LOCAL}/ab_plan.py" --emit-plan "${PLAN_ARGS[@]}"
GROUP_PLAN="${BACKUP_ROOT}/group_plan.json"
[[ -f "${GROUP_PLAN}" ]] || { echo "FATAL: missing group_plan.json" >&2; exit 2; }
PLAN_HASH="$(python3 "${EXP_LOCAL}/ab_plan.py" --query-json "${GROUP_PLAN}" --query-field plan_hash)"
echo "[megatron-ab] PLAN_HASH=${PLAN_HASH}"
jump "\$K exec -i -n '${NS}' '${MASTER_POD}' -- bash --noprofile --norc -lc 'mkdir -p ${GROUP_DIR} && cat > ${GROUP_DIR}/group_plan.injected.json'" <"${BACKUP_ROOT}/group_plan.json"

echo "[megatron-ab] sync code (incl. opponent_check) before idle/yield gates"
COPYFILE_DISABLE=1 tar -C "${EXP_LOCAL}" -cf - \
  CMakeLists.txt collector.cpp kseg_logic.hpp sync_interpose.cpp workload.py \
  convert_trace.py strict_validate.py provenance.py kill_attempt.py megatron_mspti_hook.py sitecustomize.py \
  run_megatron_node.sh run_node.sh launch_grj.sh launch_megatron_ab.sh launch_megatron_smoke.sh \
  run_probing_main_pretrain.py smoke_validate_main_path.py \
  analyze_megatron_ab.py ab_plan.py fanout_orchestrator.py local_group_guard.py opponent_check.py \
  transfer_recovery.py yield_poller.py chunk_pull.py pod_resolver.py storage_contract.py fanout_preflight.py \
  afs_seal_mirror.py afs_seal_fixture.py preflight_control_peak.py process_wall_smoke_negatives.py \
  test_kseg_logic.cpp test_collector_logic.cpp test_local.py README.md \
  | if [[ "${JUMP_LOCAL}" == "1" ]]; then
      export KUBECONFIG="${KUBE}"
      K="${KUBECTL}"
      $K exec -i -n "${NS}" "${MASTER_POD}" -- bash --noprofile --norc -lc \
        "mkdir -p ${CODE_DIR} && tar -C ${CODE_DIR} -xf - && chmod +x ${CODE_DIR}/*.sh ${CODE_DIR}/*.py"
    else
      ssh -o ConnectTimeout=30 "${JUMP}" \
        "export KUBECONFIG='${KUBE}'; K='${KUBECTL}'; \$K exec -i -n '${NS}' '${MASTER_POD}' -- bash --noprofile --norc -lc 'mkdir -p ${CODE_DIR} && tar -C ${CODE_DIR} -xf - && chmod +x ${CODE_DIR}/*.sh ${CODE_DIR}/*.py'"
    fi

require_idle_or_invalid_all "pre_start"

echo "[megatron-ab] formal wheel/.so from ${FORMAL_BUILD_ROOT}"
remote_side_effect load_formal_artifact_manifest || exit $?
pod_exec "${MASTER_POD}" \
  "set -euo pipefail; \
   mkdir -p '${CODE_DIR}/build' '${GROUP_DIR}'; \
   SO_SRC='${FORMAL_BUILD_ROOT}/libmspti_sync_skeleton.so'; \
   test -f \"\$SO_SRC\" || { echo 'FATAL: missing formal SO' >&2; exit 2; }; \
   cp -f \"\$SO_SRC\" '${CODE_DIR}/build/libmspti_sync_skeleton.so'; \
   WH=\$(ls -1 '${FORMAL_BUILD_ROOT}/wheels/'*.whl 2>/dev/null | tail -1); \
   test -n \"\$WH\" && test -f \"\$WH\" || { echo 'FATAL: missing formal wheel' >&2; exit 2; }; \
   python3 - <<'PY'
import hashlib
from pathlib import Path
so = Path('${CODE_DIR}/build/libmspti_sync_skeleton.so')
so_h = hashlib.sha256(so.read_bytes()).hexdigest()
if so_h != '${FORMAL_SO_SHA256}':
    raise SystemExit(f'formal SO hash mismatch: {so_h} != ${FORMAL_SO_SHA256}')
wh = sorted(Path('${FORMAL_BUILD_ROOT}/wheels').glob('*.whl'))[-1]
wh_h = hashlib.sha256(wh.read_bytes()).hexdigest()
if wh_h != '${FORMAL_WHEEL_SHA256}':
    raise SystemExit(f'formal wheel hash mismatch: {wh_h} != ${FORMAL_WHEEL_SHA256}')
print('FORMAL_HASH_OK', so_h[:16], wh_h[:16], wh.name)
PY
   source /root/miniconda3/etc/profile.d/conda.sh && conda activate llm_test && \
   '${PYBIN}/pip' install --force-reinstall --no-deps \"\$WH\" >>'${GROUP_DIR}/wheel_install.log' 2>&1 && \
   PROBING=0 '${PYBIN}/python' -c \"import probing; print('WHEEL_INSTALL_OK', probing.__file__)\""

fanout_parallel_exec "set -euo pipefail; \
  WH=\$(ls -1 '${FORMAL_BUILD_ROOT}/wheels/'*.whl 2>/dev/null | tail -1); \
  test -n \"\$WH\" && test -f \"\$WH\" || { echo 'FATAL: missing formal wheel on pod __RANK__' >&2; exit 2; }; \
  source /root/miniconda3/etc/profile.d/conda.sh && conda activate llm_test && \
  '${PYBIN}/pip' install --force-reinstall --no-deps \"\$WH\" >>'${GROUP_DIR}/wheel_install_rank___RANK__.log' 2>&1 && \
  PROBING=0 '${PYBIN}/python' -c \"import probing; print('WHEEL_INSTALL_OK', probing.__file__)\"" || {
  echo "FATAL: wheel install fanout failed" >&2
  exit 2
}

pod_exec "${MASTER_POD}" "python3 '${CODE_DIR}/provenance.py' --code-dir '${CODE_DIR}' --out-dir '${GROUP_DIR}' --phase pre"
pod_exec "${MASTER_POD}" "python3 '${CODE_DIR}/provenance.py' --code-dir '${CODE_DIR}' --out-dir '${GROUP_DIR}' --phase build"

# pod / git / image 快照
POD_META="$(jump "\$K get pod -n '${NS}' '${MASTER_POD}' -o jsonpath='{.metadata.uid}|{.spec.nodeName}|{.status.containerStatuses[0].image}'; echo; \$K get pod -n '${NS}' '${WORKER_POD}' -o jsonpath='{.metadata.uid}|{.spec.nodeName}|{.status.containerStatuses[0].image}'; echo")"
GIT_HASH="$(cd "${EXP_LOCAL}" && git rev-parse HEAD 2>/dev/null || echo unknown)"
GIT_STATUS="$(cd "${EXP_LOCAL}" && git status --porcelain 2>/dev/null | head -50 || true)"
CODE_SHA="$(cd "${EXP_LOCAL}" && python3 - <<'PY'
import hashlib, pathlib
h=hashlib.sha256()
for p in sorted(pathlib.Path('.').glob('*')):
    if p.is_file() and p.suffix in {'.py','.cpp','.hpp','.sh','.md','.txt'}:
        h.update(p.name.encode()); h.update(p.read_bytes())
print(h.hexdigest()[:16])
PY
)"

pod_exec "${MASTER_POD}" "python3 - <<'PY'
import json
from pathlib import Path
Path('${GROUP_DIR}/group_config.json').write_text(json.dumps({
  'group_id': '${GROUP_ID}',
  'provenance': 'atomic_counterbalanced',
  'status': 'running',
  'nnodes': ${NNODES},
  'nproc_per_node': ${NPROC},
  'world_size': ${WORLD_SIZE},
  'expected_ranks': ${EXPECTED_RANKS},
  'train_iters': ${TRAIN_ITERS},
  'capture_megatron_iter': ${CAPTURE_ITER},
  'attempts_order': (json.loads(Path('${GROUP_DIR}/group_plan.injected.json').read_text()).get('attempts_order')
                    if Path('${GROUP_DIR}/group_plan.injected.json').exists() else '${ATTEMPTS}'.split()),
  'design_sequence': '${DESIGN_SEQUENCE}',
  'plan_hash': '${PLAN_HASH}',
  'code_dir_content_hash': '${CODE_HASH}',
  'workload_kind': 'megatron',
  'model': {
    'family': 'dense-gpt',
    'tp': ${TP}, 'pp': ${PP}, 'mbs': ${MBS}, 'gbs': ${GBS}, 'seq': ${SEQ},
    'layers': ${LAYERS}, 'hidden': 4096, 'ffn': 14336, 'heads': 32, 'kv_groups': 8,
    'seed': ${SEED},
    'dp': ${WORLD_SIZE} // (${TP} * ${PP}),
    'world_size': ${WORLD_SIZE},
    'data_path': '${DATA_PATH}',
    'cache': '${CACHE}',
  },
  'pods': '''${POD_META}'''.strip().splitlines(),
  'git_hash': '${GIT_HASH}',
  'code_tree_sha16': '${CODE_SHA}',
  'git_status_head': '''${GIT_STATUS}'''.splitlines()[:40],
  'strict': ${STRICT},
  'formal_build_root': '${FORMAL_BUILD_ROOT}',
  'formal_wheel_sha256': '${FORMAL_WHEEL_SHA256}',
  'formal_so_sha256': '${FORMAL_SO_SHA256}',
  'probing_main_ours': 'adaptive_v1',
  'code_dir': '${CODE_DIR}',
  'code_hash': '${CODE_HASH}',
  'frozen_thresholds': {
    'min_raw_kernels_per_rank': ${MIN_RAW_KERNELS},
    'min_comm_per_rank': ${MIN_COMM},
    'relative_raw_floor': ${REL_RAW_FLOOR},
    'relative_comm_floor': ${REL_COMM_FLOOR},
    'immutable': True,
    'injected_at': 'group_config_pre_launch',
  },
  'note': 'n=2 counterbalanced preliminary; no significance claims; smoke≠perf',
}, indent=2, sort_keys=True), encoding='utf-8')
PY"

MASTER_ADDR="$(jump "\$K get pod -n '${NS}' '${MASTER_POD}' -o jsonpath='{.status.podIP}'")"
[[ -n "${MASTER_ADDR}" ]] || { echo "no master IP" >&2; exit 4; }

# Consume immutable plan — do NOT re-derive id/port/marker from ${ATTEMPTS}.
PLAN_TSV="$(python3 "${EXP_LOCAL}/ab_plan.py" --query-tsv "${GROUP_PLAN}")"
echo "${PLAN_TSV}" > "${BACKUP_ROOT}/group_plan.tsv"
ATTEMPT_ROWS=0
while IFS=$'\t' read -r attempt_idx ATTEMPT_ID ARM MASTER_PORT RUN_MARKER OUT_DIR REQUIRE_THRESH <&3; do
  [[ "${attempt_idx}" == "order_index" ]] && continue
  [[ -n "${ATTEMPT_ID}" ]] || continue
  ATTEMPT_ROWS=$((ATTEMPT_ROWS + 1))
  RUN_ID="${GROUP_ID}-${ATTEMPT_ID}"
  if [[ -f "${BACKUP_ROOT}/GROUP_INVALID.json" ]]; then
    echo "FATAL: group already INVALID; refusing subsequent attempt ${ATTEMPT_ID}" >&2
    exit 14
  fi
  echo "[megatron-ab] === ${ATTEMPT_ID} PORT=${MASTER_PORT} MARKER=${RUN_MARKER} PLAN_HASH=${PLAN_HASH} ==="
  ACTIVE_OUT_DIR="${OUT_DIR}"
  ACTIVE_RUN_MARKER="${RUN_MARKER}"
  ACTIVE_ATTEMPT_ID="${ATTEMPT_ID}"
  require_idle_or_invalid_all "attempt_pre_start"
  master_port_preflight "${MASTER_PORT}" "${OUT_DIR}" "${ATTEMPT_ID}"
  fanout_parallel_exec "mkdir -p '${OUT_DIR}'" || {
    mark_group_invalid "mkdir_fail" "${ATTEMPT_ID}" "attempt_pre_start"
    exit 9
  }
  # Immutable copy/reference of group pre/build provenance into attempt (must exist).
  if ! pod_exec "${MASTER_POD}" "python3 - <<'PY'
from pathlib import Path
import shutil, sys
g = Path('${GROUP_DIR}')
out = Path('${OUT_DIR}')
out.mkdir(parents=True, exist_ok=True)
for name in (
    'provenance_source_tree.json',
    'provenance_source_snapshot.sha256',
    'provenance_source_snapshot.tarish.txt',
    'provenance_build.json',
):
    src = g / name
    if not src.exists():
        print('FATAL: missing group provenance', name, file=sys.stderr)
        raise SystemExit(2)
    shutil.copy2(src, out / name)
# Copy sealed immutable SO into attempt for artifact seal / local rehash.
sb = g / 'sealed_bins'
if sb.is_dir():
    dest = out / 'sealed_bins'
    dest.mkdir(parents=True, exist_ok=True)
    for p in sb.iterdir():
        if p.is_file():
            shutil.copy2(p, dest / p.name)
            try:
                (dest / p.name).chmod(0o444)
            except Exception:
                pass
print('ATTEMPT_PROVENANCE_COPIED')
PY"
  then
    mark_group_invalid "provenance_copy_fail" "${ATTEMPT_ID}" "provenance"
    exit 9
  fi

  START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  COMMON="RUN_ID='${RUN_ID}' RUN_MARKER='${RUN_MARKER}' ARM='${ARM}' MASTER_ADDR='${MASTER_ADDR}' MASTER_PORT='${MASTER_PORT}' NNODES='${NNODES}' NPROC='${NPROC}' CODE_DIR='${CODE_DIR}' OUT_DIR='${OUT_DIR}' TRAIN_ITERS='${TRAIN_ITERS}' MSPTI_CAPTURE_MEGATRON_ITER='${CAPTURE_ITER}' MSPTI_CAPTURE_RANKS='${MSPTI_CAPTURE_RANKS:-all}' RUN_TIMEOUT_S='${RUN_TIMEOUT_S:-1800}' SEED='${SEED}' TP='${TP}' PP='${PP}' MBS='${MBS}' GBS='${GBS}' SEQ='${SEQ}' LAYERS='${LAYERS}' DATA_PATH='${DATA_PATH}' CACHE='${CACHE}'"

  declare -a launch_pids=()
  declare -a launch_nodes=()
  declare -a launch_pods=()
  fanout_fail=0
  fanout_reason=""
  while read -r rank pod; do
    [[ -n "${rank}" ]] || continue
    pod_exec "${pod}" \
      "env ${COMMON} NODE_RANK=${rank} setsid nohup '${CODE_DIR}/run_megatron_node.sh' </dev/null >'${OUT_DIR}/launch_${rank}.log' 2>&1 & echo \$!" &
    launch_pids+=("$!")
    launch_nodes+=("${rank}")
    launch_pods+=("${pod}")
  done < <(pod_map_entries_tsv)
  for i in "${!launch_pids[@]}"; do
    pid="${launch_pids[$i]}"
    set +e
    wait "${pid}"
    rc=$?
    set -e
    if (( rc != 0 )); then
      fanout_fail=1
      fanout_reason="launch_emit_or_ssh_fail node=${launch_nodes[$i]} pod=${launch_pods[$i]} wait_rc=${rc}"
      echo "FANOUT_FAIL ${fanout_reason}" >&2
    fi
  done
  if (( fanout_fail != 0 )); then
    echo "FATAL fanout partial failure: ${fanout_reason}" >&2
    set +e
    kill_our_attempt "${OUT_DIR}" "${RUN_MARKER}"
    krc=$?
    set -e
    pull_attempt_evidence "${OUT_DIR}" "${ATTEMPT_ID}" best_effort || true
    mark_group_invalid "${fanout_reason}" "${ATTEMPT_ID}" "fanout" \
      "$([[ ${krc} -eq 0 ]] && echo CLEANUP_OK || echo CLEANUP_INCOMPLETE)"
    echo "GROUP_INVALID=${GROUP_ID} reason=fanout_partial attempt=${ATTEMPT_ID}" >&2
    echo "EVIDENCE_STATUS=EVIDENCE_INCOMPLETE" >&2
    exit 15
  fi

  early_deadline=$((SECONDS + ${EARLY_CHECK_S:-420}))
  saw_iter=0
  while (( SECONDS < early_deadline )); do
    YIELD_KILL_RC=0
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
      echo "attempt stopped: ${reason}" >&2
      krc="${YIELD_KILL_RC:-1}"
      pull_attempt_evidence "${OUT_DIR}" "${ATTEMPT_ID}" best_effort || true
      if ! ensure_group_invalid_written "${reason}" "${ATTEMPT_ID}" "yield" \
        "$([[ ${krc} -eq 0 ]] && echo CLEANUP_OK || echo CLEANUP_INCOMPLETE)"; then
        echo "FATAL: ensure_group_invalid_written failed before yield exit" >&2
      fi
      exit "${yrc}"
    fi
    snippet="$(pod_exec "${MASTER_POD}" "python3 - <<'PY'
from pathlib import Path
out=Path('${OUT_DIR}')
for name in ('node_1.log','node_0.log'):
    p=out/name
    if p.exists():
        print(p.read_text(errors='replace')[-4000:])
PY" || true)"
    if grep -qE 'iteration +[0-9]+/' <<<"${snippet}"; then
      saw_iter=1
      echo "${snippet}" | grep -E 'iteration +[0-9]+/' | tail -2 || true
      break
    fi
    if grep -qE 'EADDRINUSE|address already in use|DistNetworkError|unrecognized arguments' <<<"${snippet}"; then
      echo "early failure" >&2
      echo "${snippet}" >&2
      set +e
      kill_our_attempt "${OUT_DIR}" "${RUN_MARKER}"
      krc=$?
      set -e
      pull_attempt_evidence "${OUT_DIR}" "${ATTEMPT_ID}" best_effort || true
      mark_group_invalid "early_failure" "${ATTEMPT_ID}" "early_config" \
        "$([[ ${krc} -eq 0 ]] && echo CLEANUP_OK || echo CLEANUP_INCOMPLETE)"
      echo "GROUP_INVALID=${GROUP_ID} reason=early_failure attempt=${ATTEMPT_ID}" >&2
      exit 5
    fi
    sleep 5
  done
  if (( saw_iter == 0 )); then
    echo "WARN: ${ATTEMPT_ID} early window 未见 iteration" >&2
  fi

  deadline=$((SECONDS + ${WAIT_TIMEOUT_S:-1800}))
  exit_code=0
  while (( SECONDS < deadline )); do
    YIELD_KILL_RC=0
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
      krc="${YIELD_KILL_RC:-1}"
      pull_attempt_evidence "${OUT_DIR}" "${ATTEMPT_ID}" best_effort || true
      if ! ensure_group_invalid_written "${reason}" "${ATTEMPT_ID}" "yield" \
        "$([[ ${krc} -eq 0 ]] && echo CLEANUP_OK || echo CLEANUP_INCOMPLETE)"; then
        echo "FATAL: ensure_group_invalid_written failed before yield exit" >&2
      fi
      exit "${yrc}"
    fi
    status="$(pod_exec "${MASTER_POD}" "python3 - <<'PY'
from pathlib import Path
out = Path('${OUT_DIR}')
nodes = list(range(int('${NNODES}')))
done = sum(1 for n in nodes if (out / f'node_{n}.done').exists())
fail = sum(1 for n in nodes if (out / f'node_{n}.fail').exists())
first_fail = -1
for n in nodes:
    if (out / f'node_{n}.fail').exists():
        first_fail = n
        break
last = ''
for n in reversed(nodes):
    p = out / f'node_{n}.log'
    if not p.exists():
        continue
    for line in p.read_text(errors='replace').splitlines()[::-1]:
        if 'iteration' in line and '/' in line:
            last = line.strip()[:160]
            break
    if last:
        break
print(f'{done} {fail} {first_fail} {last}')
PY")"
    done_count="$(awk '{print $1}' <<<"${status}")"
    fail_count="$(awk '{print $2}' <<<"${status}")"
    first_fail_node="$(awk '{print $3}' <<<"${status}")"
    last="$(awk '{$1=$2=$3=""; sub(/^  */,""); print}' <<<"${status}")"
    echo "$(date +%H:%M:%S) ${ATTEMPT_ID} done=${done_count}/${NNODES} fail=${fail_count} ${last}"
    if (( fail_count > 0 )); then
      if [[ "${first_fail_node}" != "-1" ]]; then
        echo "FIRST_FAIL_NODE=${first_fail_node} fail=${fail_count}/${NNODES} attempt=${ATTEMPT_ID}" >&2
      fi
      exit_code=6
      break
    fi
    if (( done_count == NNODES )); then
      break
    fi
    sleep 10
  done
  if (( SECONDS >= deadline )); then
    echo "timeout ${ATTEMPT_ID}" >&2
    set +e
    kill_our_attempt "${OUT_DIR}" "${RUN_MARKER}"
    krc=$?
    set -e
    exit_code=7
    mark_group_invalid "timeout" "${ATTEMPT_ID}" "attempt_exit" \
      "$([[ ${krc} -eq 0 ]] && echo CLEANUP_OK || echo CLEANUP_INCOMPLETE)"
  fi
  if (( exit_code != 0 )); then
    # 任一 node fail：先杀本 attempt（marker 校验），再 best-effort 回拉；不得标 PASS
    set +e
    kill_our_attempt "${OUT_DIR}" "${RUN_MARKER}"
    krc=$?
    set -e
    pull_attempt_evidence "${OUT_DIR}" "${ATTEMPT_ID}" best_effort || true
    mark_group_invalid "attempt_fail exit=${exit_code}" "${ATTEMPT_ID}" "attempt_exit" \
      "$([[ ${krc} -eq 0 ]] && echo CLEANUP_OK || echo CLEANUP_INCOMPLETE)"
    echo "GROUP_INVALID=${GROUP_ID} reason=attempt_fail attempt=${ATTEMPT_ID} exit=${exit_code}" >&2
  fi

  if (( exit_code != 0 )); then
    echo "SKIP_ATTEMPT_SEAL attempt=${ATTEMPT_ID} exit=${exit_code} (GROUP_INVALID; no seal narrative)" >&2
    pull_attempt_evidence "${OUT_DIR}" "${ATTEMPT_ID}" best_effort || true
    echo "FATAL attempt ${ATTEMPT_ID} exit_code=${exit_code}" >&2
    echo "GROUP_INVALID=${GROUP_ID} reason=attempt_exit attempt=${ATTEMPT_ID}" >&2
    exit "${exit_code}"
  fi

  END_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  CONVERT_RC=0
  # 运行前从 immutable group/config 注入 frozen thresholds；缺失直接拒绝
  pod_exec "${MASTER_POD}" "python3 - <<'PY'
import json
from pathlib import Path
g = json.loads(Path('${GROUP_DIR}/group_config.json').read_text())
frozen = g.get('frozen_thresholds') or {}
if frozen.get('min_raw_kernels_per_rank') is None or frozen.get('min_comm_per_rank') is None:
    raise SystemExit('FATAL: missing frozen_thresholds in group_config')
cfg = {
  'run_id': '${RUN_ID}', 'arm': '${ARM}', 'group_id': '${GROUP_ID}',
  'attempt_id': '${ATTEMPT_ID}', 'nnodes': ${NNODES}, 'nproc_per_node': ${NPROC},
  'world_size': ${WORLD_SIZE}, 'expected_ranks': ${EXPECTED_RANKS},
  'train_iters': ${TRAIN_ITERS}, 'capture_megatron_iter': ${CAPTURE_ITER},
  'workload_kind': 'megatron', 'code_dir': '${CODE_DIR}', 'code_hash': '${CODE_HASH}',
  'frozen_thresholds': frozen, 'model': g.get('model'),
  'formal_wheel_sha256': '${FORMAL_WHEEL_SHA256}',
  'formal_so_sha256': '${FORMAL_SO_SHA256}',
  'formal_build_root': '${FORMAL_BUILD_ROOT}',
}
if '${ARM}' == 'ours':
    cfg['granularity_mode'] = 'adaptive_v1'
    cfg['probing_main'] = True
    cfg['adapt_min_samples'] = 128
    cfg['adapt_every'] = 256
Path('${OUT_DIR}/config.json').write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding='utf-8')
print('ATTEMPT_FROZEN', frozen)
PY"
  if [[ "${ARM}" == "ours" ]]; then
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
      exit_code=8
      set +e
      ensure_group_invalid_written "convert_fail rc=${CONVERT_RC}" "${ATTEMPT_ID}" "convert"
      set -e
    fi
  fi
  # Stop all log/exit writes, then final digest → manifest → remote seal.
  pod_exec "${MASTER_POD}" "python3 - <<'PY'
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
# immutable attempt manifest — written AFTER convert/artifacts; seal is final
pgids = {}
for name in ('node_0.pgid', 'node_1.pgid'):
    p = out / name
    if p.exists():
        pgids[name] = p.read_text().strip()
node_launch = {}
for p in sorted(out.glob('node_*.launch.json')):
    node_launch[p.name] = json.loads(p.read_text())
metas = []
finalize_complete = True
reasons = []
for p in sorted(out.glob('rank_*.npu_sync_meta.json')):
    m = json.loads(p.read_text())
    metas.append({k: m.get(k) for k in (
      'rank','finalize_complete','finalize_rc','finalize_reason',
      'raw_kernels','raw_comms','finalize_ms','finalize_flush_ms','finalize_drain_ms',
      'process_wall_ms','granularity_mode','adaptive_source')})
    reasons.append(m.get('finalize_reason'))
    if not m.get('finalize_complete', False):
        finalize_complete = False
if not metas:
    for p in sorted(out.glob('rank_*.mspti_meta.json')):
        m = json.loads(p.read_text())
        metas.append({k: m.get(k) for k in (
          'rank','finalize_complete','finalize_rc','finalize_reason',
          'raw_kernels','raw_comms','finalize_ms','finalize_flush_ms','finalize_drain_ms',
          'process_wall_ms')})
        reasons.append(m.get('finalize_reason'))
        if not m.get('finalize_complete', False):
            finalize_complete = False
if '${ARM}' != 'ours':
    finalize_complete = True
pt = out/'provenance_source_tree.json'
pb = out/'provenance_build.json'
ad = out/'artifact_digest.json'
snap = out/'provenance_source_snapshot.sha256'
if not pt.exists() or not pb.exists() or not ad.exists() or not snap.exists():
    raise SystemExit('FATAL: attempt provenance/artifact missing before seal')
prov_tree = json.loads(pt.read_text())
prov_build = json.loads(pb.read_text())
art = json.loads(ad.read_text())
if not prov_tree.get('source_tree_sha256') or not prov_build.get('collector_so_sha256'):
    raise SystemExit('FATAL: null provenance hashes')
if not (art.get('aggregate_sha256') or art.get('artifact_digest_sha256')):
    raise SystemExit('FATAL: null artifact aggregate')
e2e_vals = [v.get('e2e_wall_ms') for v in node_launch.values() if isinstance(v, dict) and v.get('e2e_wall_ms') is not None]
if e2e_vals is not None and e2e_vals and min(float(x) for x in e2e_vals) <= 0:
    raise SystemExit(f'FATAL: non-positive node e2e_wall_ms={e2e_vals}')
cfg = json.loads((out/'config.json').read_text()) if (out/'config.json').exists() else {}
agg = art.get('aggregate_sha256') or art.get('artifact_digest_sha256')
manifest = {
  'attempt_id': '${ATTEMPT_ID}',
  'run_id': '${RUN_ID}',
  'run_marker': '${RUN_MARKER}',
  'arm': '${ARM}',
  'order_index': ${attempt_idx},
  'started_at_utc': '${START_TS}',
  'ended_at_utc': '${END_TS}',
  'master_port': ${MASTER_PORT},
  'exit_code': ${exit_code},
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
  'model': {
    'tp': ${TP}, 'pp': ${PP}, 'mbs': ${MBS}, 'gbs': ${GBS}, 'seq': ${SEQ},
    'layers': ${LAYERS}, 'seed': ${SEED},
    'dp': ${WORLD_SIZE} // (${TP}*${PP}),
    'world_size': ${WORLD_SIZE},
    'data_path': '${DATA_PATH}',
    'cache': '${CACHE}',
  },
  'pgids': pgids,
  'node_launch': node_launch,
  'mspti_meta_summary': metas,
  'npu_sync_meta_summary': metas,
  'frozen_thresholds': cfg.get('frozen_thresholds'),
  'git_hash': '${GIT_HASH}',
  'code_dir': '${CODE_DIR}',
  'code_hash': '${CODE_HASH}',
  'code_dir_content_hash': '${CODE_HASH}',
  'plan_hash': '${PLAN_HASH}',
  'design_sequence': '${DESIGN_SEQUENCE}',
  'code_tree_sha16': '${CODE_SHA}',
  'pods': '''${POD_META}'''.strip().splitlines(),
  'strict': ${STRICT},
  'e2e_wall_ms_nodes': {k: v.get('e2e_wall_ms') for k,v in node_launch.items()},
  'e2e_wall_ms': (max((v.get('e2e_wall_ms') or 0) for v in node_launch.values())
                 if node_launch and any(v.get('e2e_wall_ms') is not None for v in node_launch.values()) else None),
  'e2e_wall_definition': 'max(node e2e_wall_ms); node wall = argv start→process exit',
  'provenance': {
    'source_tree_sha256': prov_tree.get('source_tree_sha256'),
    'collector_so_sha256': prov_build.get('collector_so_sha256'),
    'artifact_digest_sha256': agg,
    'file_count_artifacts': art.get('file_count'),
  },
  'artifact_digest_sha256': agg,
}
tmp = out / 'attempt_manifest.json.tmp'
tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding='utf-8')
tmp.replace(out / 'attempt_manifest.json')
h = hashlib.sha256((out / 'attempt_manifest.json').read_bytes()).hexdigest()
(out / 'attempt_manifest.sha256').write_text(h + '\\n', encoding='utf-8')
print('manifest', out / 'attempt_manifest.json', h[:16], 'finalize_complete', finalize_complete)
PY"

  # Success: AFS sealed mirror verification (authoritative); jump tree is control-only.
  if ! pull_attempt_evidence "${OUT_DIR}" "${ATTEMPT_ID}" required; then
    mark_group_invalid "pull_required_fail" "${ATTEMPT_ID}" "pull"
    exit 12
  fi
  AFS_ATTEMPT_SEAL="${AFS_SEAL_ROOT}/${ATTEMPT_ID}"
  if ! pod_exec "${MASTER_POD}" "python3 '${CODE_DIR}/afs_seal_mirror.py' \
    --live-dir '${OUT_DIR}' \
    --sealed-dir '${AFS_ATTEMPT_SEAL}' \
    --run-id '${ATTEMPT_ID}' \
    --live-root '${OUT_DIR}' \
    --write-seal \
    --out '${AFS_GROUP_ROOT}/${ATTEMPT_ID}_afs_seal_mirror.json'"; then
    mark_group_invalid "afs_seal_mirror_fail" "${ATTEMPT_ID}" "afs_seal"
    exit 12
  fi
  if ! pod_exec "${MASTER_POD}" "python3 - <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, '${CODE_DIR}')
from strict_validate import validate_attempt_manifest, StrictValidationError
root = Path('${AFS_ATTEMPT_SEAL}')
try:
    validate_attempt_manifest(
        root,
        expected_ranks=int('${EXPECTED_RANKS}'),
        capture_step=int('${CAPTURE_ITER}'),
        expected_nodes=int('${NNODES}'),
        require_seal=True,
        require_provenance=True,
        require_afs_anchor=True,
    )
except StrictValidationError as exc:
    print('AFS_ANCHOR_FAIL', '${ATTEMPT_ID}', exc, file=sys.stderr)
    raise SystemExit(11)
print('AFS_ANCHOR_OK', '${ATTEMPT_ID}')
PY"; then
    mark_group_invalid "afs_anchor_or_strict_fail" "${ATTEMPT_ID}" "afs_anchor"
    exit 11
  fi
done 3< "${BACKUP_ROOT}/group_plan.tsv"

EXPECTED_ATTEMPT_ROWS="$(python3 -c "import json; p=json.load(open('${BACKUP_ROOT}/group_plan.json')); print(len(p['attempts']))")"
if [[ "${ATTEMPT_ROWS}" -ne "${EXPECTED_ATTEMPT_ROWS}" ]]; then
  mark_group_invalid "plan_attempt_count_mismatch" "" "plan_consume"
  echo "FATAL: consumed ${ATTEMPT_ROWS} attempts from immutable plan; expected ${EXPECTED_ATTEMPT_ROWS}" >&2
  exit 14
fi

# Strict analyzer reads AFS sealed mirrors; LOCAL_VERIFIED_SEAL is legacy-only.
echo "[megatron-ab] analyze (strict; AFS_VERIFIED_SEAL authoritative)"
cp -f "${BACKUP_ROOT}/group_plan.json" "${BACKUP_ROOT}/group_config.json" 2>/dev/null || true
python3 - <<PY
import json
from pathlib import Path
root = Path("${BACKUP_ROOT}")
plan_path = root / "group_plan.json"
cfg = {}
if plan_path.exists():
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    cfg = {
        "group_id": plan.get("group_id"),
        "attempts_order": plan.get("attempts_order"),
        "capture_megatron_iter": plan.get("capture_megatron_iter"),
        "model": plan.get("model"),
        "frozen_thresholds": plan.get("frozen_thresholds"),
        "nnodes": plan.get("nnodes"),
        "world_size": plan.get("world_size"),
        "plan_hash": plan.get("plan_hash"),
        "design_sequence": plan.get("design_sequence"),
        "status": "analyzing",
    }
else:
    cfg = {"group_id": "${GROUP_ID}", "status": "analyzing"}
(root / "group_config.json").write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")
PY
set +e
python3 "${EXP_LOCAL}/analyze_megatron_ab.py" --strict "${BACKUP_ROOT}" \
  >"${BACKUP_ROOT}/analyze.log" 2>&1
ANALYZE_RC=$?
set -e
if (( ANALYZE_RC != 0 )); then
  mark_group_invalid "local_strict_analyzer_fail rc=${ANALYZE_RC}" "" "analyzer"
  echo "GROUP_INVALID=${GROUP_ID} reason=local_strict_analyzer_fail rc=${ANALYZE_RC}" >&2
  tail -50 "${BACKUP_ROOT}/analyze.log" >&2 || true
  exit "${ANALYZE_RC}"
fi
python3 - <<PY
import json
from pathlib import Path
g = Path("${BACKUP_ROOT}")
cfg = json.loads((g / "group_config.json").read_text(encoding="utf-8"))
cfg["status"] = "complete"
(g / "group_config.json").write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")
(g / "GROUP_COMPLETE").write_text("${GROUP_ID}\n", encoding="utf-8")
summary = (g / "SUMMARY.md").read_text(encoding="utf-8") if (g / "SUMMARY.md").exists() else ""
print(summary)
print("GROUP_COMPLETE=${GROUP_ID}")
PY
GROUP_COMPLETE_FLAG=1
ACTIVE_OUT_DIR=""
ACTIVE_RUN_MARKER=""
ACTIVE_ATTEMPT_ID=""
echo "LOCAL_BACKUP=${BACKUP_ROOT}"
