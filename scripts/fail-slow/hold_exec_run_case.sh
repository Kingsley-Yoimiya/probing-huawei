#!/usr/bin/env bash
# Ascend hold-exec：在 yysong-* 内 torchrun，不新建 vcjob。
# 本机 source env.sh 后调用；经跳板 JUMP_KUBECTL exec。
#
# 例：
#   source scripts/fail-slow/env.sh
#   CASE_ID=P3-EXT-A DOSE=loud PHASE=pilot ABC_CONFIGS=C0_baseline,C1_inject_none \
#     bash scripts/fail-slow/hold_exec_run_case.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/fail-slow/env.sh"

CASE_ID="${CASE_ID:?need CASE_ID}"
DOSE="${DOSE:-loud}"
PHASE="${PHASE:-pilot}"
POD="${POD:-${FS_HOLD_PODS_CASE:-yysong-master-0}}"
NS="${NS:-default}"
NNODES="${NNODES:-1}"
NPROC="${NPROC:-16}"
ITERS="${ITERS:-500}"
WARMUP="${WARMUP:-50}"
SEED="${SEED:-42}"
MODE="${MODE:-host_bound}"
MODEL="${MODEL:-gpt2}"
SEQ="${SEQ:-1024}"
BATCH="${BATCH:-8}"
SIDECAR_LOCAL_RANK="${SIDECAR_LOCAL_RANK:-7}"
INJECT_START="${INJECT_START:-100}"
INJECT_STOP="${INJECT_STOP:-300}"
CPU_LOAD="${CPU_LOAD:-90}"
CPU_N="${CPU_N:-}"
HOST_BOUND_MATMUL="${HOST_BOUND_MATMUL:-768}"
SIDECAR_WARMUP="${SIDECAR_WARMUP:-8}"
SIDECAR_PY_LOCAL="${SIDECAR_PY_LOCAL:-${ROOT}/scripts/fail-slow/sidecar_inject_npu.py}"
DUTY="${DUTY:-}"
SIZE="${SIZE:-}"
CUBE_SIZE="${CUBE_SIZE:-4096}"
CUBE_MM="${CUBE_MM:-16}"
INLINE_GC_EVERY="${INLINE_GC_EVERY:-1}"
INLINE_GC_STALL_S="${INLINE_GC_STALL_S:-0.25}"
INLINE_8B_MB="${INLINE_8B_MB:-16}"
INLINE_8B_STALL_S="${INLINE_8B_STALL_S:-0.25}"
SIDECAR_8C_PY_LOCAL="${SIDECAR_8C_PY_LOCAL:-${ROOT}/scripts/fail-slow/sidecar_inject_8c.py}"
SIDECAR_8C_CPU_N="${SIDECAR_8C_CPU_N:-}"
SIDECAR_8C_CPU_LOAD="${SIDECAR_8C_CPU_LOAD:-90}"
SIDECAR_8C_MB="${SIDECAR_8C_MB:-1}"
SIDECAR_8C_LEAK_EVERY="${SIDECAR_8C_LEAK_EVERY:-1.0}"
SIDECAR_8C_MAX_CHUNKS="${SIDECAR_8C_MAX_CHUNKS:-64}"
INLINE_HBM_MB="${INLINE_HBM_MB:-512}"
INLINE_HBM_COPIES="${INLINE_HBM_COPIES:-48}"
INLINE_HBM_COPIES_MAX="${INLINE_HBM_COPIES_MAX:-48}"
INLINE_HBM_RAMP="${INLINE_HBM_RAMP:-0}"
INLINE_2A_CHUNKS="${INLINE_2A_CHUNKS:-12}"
INLINE_2A_STALL_MB="${INLINE_2A_STALL_MB:-768}"
INLINE_2A_STALL_S="${INLINE_2A_STALL_S:-0.25}"
RARE_SHAPE_SEQ="${RARE_SHAPE_SEQ:-1536}"
RARE_SHAPE_EVERY="${RARE_SHAPE_EVERY:-1}"
INLINE_2C_N="${INLINE_2C_N:-1024}"
INLINE_2C_EVERY="${INLINE_2C_EVERY:-1}"
INLINE_2C_FALLBACK_S="${INLINE_2C_FALLBACK_S:-0.6}"
CKPT_EVERY="${CKPT_EVERY:-100}"
FLUSH_EVERY="${FLUSH_EVERY:-5}"
IO_PAYLOAD="${IO_PAYLOAD:-}"
IO_READ_KB="${IO_READ_KB:-0}"
IO_STRESS_DIR="${IO_STRESS_DIR:-}"
HDD_N="${HDD_N:-}"
HDD_BYTES="${HDD_BYTES:-2G}"
IOMIX_N="${IOMIX_N:-}"
FIO_NUMJOBS="${FIO_NUMJOBS:-16}"
FIO_IODEPTH="${FIO_IODEPTH:-64}"
FIO_SIZE="${FIO_SIZE:-4G}"
FIO_BS="${FIO_BS:-4k}"
VM_N="${VM_N:-96}"
VM_BYTES="${VM_BYTES:-6G}"
CASE_SLUG=$(echo "$CASE_ID" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9-')
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)-yjr-as-c-${CASE_SLUG}-${DOSE}}"
LOCAL_RESULT_ROOT="${LOCAL_RESULT_ROOT:-${LOCAL_RESULT_ROOT_BASE}/${RUN_ID}}"
POD_BUNDLE="${POD_BUNDLE:-/data/yinjinrun.p-huawei/probe-bundle}"
POD_PYDEPS="${POD_PYDEPS:-${POD_BUNDLE}/pydeps}"
POD_OUT="${POD_OUT:-/data/yinjinrun.p-huawei/results/ascend-ais/${RUN_ID}}"
ABC_CONFIGS="${ABC_CONFIGS:-C0_baseline,C1_inject_none}"
TRAIN_PY_LOCAL="${TRAIN_PY_LOCAL:-${FS_PLATFORM_ASCEND}/train_bench_probe_npu.py}"
DUMP_SQL_LOCAL="${DUMP_SQL_LOCAL:-${FS_PLATFORM_ASCEND}/dump_probing_sql.sh}"
DUMP_PROBING_SQL="${DUMP_PROBING_SQL:-1}"
DUMP_WAIT_S="${DUMP_WAIT_S:-45}"
PYBIN="/root/miniconda3/envs/llm_test/bin"
TORCHRUN="${TORCHRUN:-${PYBIN}/torchrun}"
WORK="${LOCAL_RESULT_ROOT}/_work"
mkdir -p "$LOCAL_RESULT_ROOT" "$WORK"

case "$CASE_ID" in
  P3-EXT-A)
    INJECT_KIND=stress_cpu
    MODE="${MODE_OVERRIDE:-host_bound}"
    ACCEPT_MIN_RATIO="${ACCEPT_MIN_RATIO:-1.3}"
    INJECT_ARGS="${INJECT_ARGS:-cpu_load=${CPU_LOAD}}"
    ;;
  P1-EXT-A)
    # Ascend：外挂 cube 进程隔离无效；默认 INLINE（dose_recipes loud）
    INJECT_KIND="${INJECT_KIND:-inline_cube}"
    MODE="${MODE_OVERRIDE:-gpu_bound}"
    ACCEPT_MIN_RATIO="${ACCEPT_MIN_RATIO:-1.5}"
    CUBE_SIZE="${CUBE_SIZE:-4096}"
    CUBE_MM="${CUBE_MM:-16}"
    INJECT_ARGS="${INJECT_ARGS:-inline_cube_size=${CUBE_SIZE},inline_cube_mm=${CUBE_MM}}"
    ;;
  P1-EXT-B)
    # Ascend：外挂 memory sidecar 隔离无效（同 P1-EXT-A）；优先 INLINE HBM
    INJECT_KIND="${INJECT_KIND:-inline_hbm}"
    MODE="${MODE_OVERRIDE:-gpu_bound}"
    ACCEPT_MIN_RATIO="${ACCEPT_MIN_RATIO:-1.6}"
    INLINE_HBM_MB="${INLINE_HBM_MB:-512}"
    INLINE_HBM_COPIES="${INLINE_HBM_COPIES:-48}"
    INLINE_HBM_RAMP="${INLINE_HBM_RAMP:-0}"
    INJECT_ARGS="${INJECT_ARGS:-inline_hbm_mb=${INLINE_HBM_MB},inline_hbm_copies=${INLINE_HBM_COPIES}}"
    ;;
  P1-HW-B)
    # OUTLINE 1B：显存带宽渐进衰减。MetaX 外挂 1b 与 pipeline 门闩不齐且常咬空；
    # Ascend 同 P1-EXT-B：默认 INLINE 渐进 HBM（copies 6→48），非改频。
    # 注意：顶层已设 INLINE_HBM_* 默认，此处必须无条件覆盖（不能用 :-）
    INJECT_KIND="${INJECT_KIND:-1b}"
    MODE="${MODE_OVERRIDE:-gpu_bound}"
    ACCEPT_MIN_RATIO="${ACCEPT_MIN_RATIO:-1.3}"
    INLINE_HBM_MB="${INLINE_HBM_MB_OVERRIDE:-512}"
    INLINE_HBM_COPIES="${INLINE_HBM_COPIES_OVERRIDE:-6}"
    INLINE_HBM_COPIES_MAX="${INLINE_HBM_COPIES_MAX_OVERRIDE:-48}"
    INLINE_HBM_RAMP="${INLINE_HBM_RAMP_OVERRIDE:-1}"
    INJECT_ARGS="${INJECT_ARGS:-inline_hbm_mb=${INLINE_HBM_MB},inline_hbm_copies=${INLINE_HBM_COPIES},inline_hbm_copies_max=${INLINE_HBM_COPIES_MAX},ramp=1}"
    ;;
  P3-SW-A)
    # 对象泄漏→GC：INLINE_INJECT=8a（外挂 GC 无效）；默认 stall=0.25s / every=1
    INJECT_KIND="${INJECT_KIND:-8a}"
    MODE="${MODE_OVERRIDE:-host_bound}"
    ACCEPT_MIN_RATIO="${ACCEPT_MIN_RATIO:-1.3}"
    INLINE_GC_EVERY="${INLINE_GC_EVERY:-1}"
    INLINE_GC_STALL_S="${INLINE_GC_STALL_S:-0.25}"
    INJECT_ARGS="${INJECT_ARGS:-inline_gc_every=${INLINE_GC_EVERY},inline_gc_stall_s=${INLINE_GC_STALL_S}}"
    ;;
  P3-SW-B)
    # dataloader 泄漏：INLINE_INJECT=8b（外挂 sidecar 大内存机咬空）；沐曦冻结 mb=16 stall=0.25
    INJECT_KIND="${INJECT_KIND:-8b}"
    MODE="${MODE_OVERRIDE:-host_bound}"
    ACCEPT_MIN_RATIO="${ACCEPT_MIN_RATIO:-1.3}"
    INLINE_8B_MB="${INLINE_8B_MB:-16}"
    INLINE_8B_STALL_S="${INLINE_8B_STALL_S:-0.25}"
    INJECT_ARGS="${INJECT_ARGS:-mb=${INLINE_8B_MB},stall_s=${INLINE_8B_STALL_S}}"
    ;;
  P3-SW-C)
    # 监控自身泄漏：sidecar=stress-ng CPU + 主进程 RSS 泄漏
    # calibrated@135238：cpu_n=nproc（空=sidecar 默认）cpu_load=90 → C1/C0=2.49
    INJECT_KIND="${INJECT_KIND:-8c}"
    MODE="${MODE_OVERRIDE:-host_bound}"
    ACCEPT_MIN_RATIO="${ACCEPT_MIN_RATIO:-1.3}"
    HOST_BOUND_MATMUL="${HOST_BOUND_MATMUL:-768}"
    SIDECAR_8C_CPU_N="${SIDECAR_8C_CPU_N:-}"
    SIDECAR_8C_CPU_LOAD="${SIDECAR_8C_CPU_LOAD:-90}"
    SIDECAR_8C_MB="${SIDECAR_8C_MB:-1}"
    SIDECAR_8C_LEAK_EVERY="${SIDECAR_8C_LEAK_EVERY:-1.0}"
    SIDECAR_8C_MAX_CHUNKS="${SIDECAR_8C_MAX_CHUNKS:-64}"
    INJECT_ARGS="${INJECT_ARGS:-cpu_n=${SIDECAR_8C_CPU_N:-nproc},cpu_load=${SIDECAR_8C_CPU_LOAD},mb=${SIDECAR_8C_MB},leak_every=${SIDECAR_8C_LEAK_EVERY},max_chunks=${SIDECAR_8C_MAX_CHUNKS}}"
    ;;
  P1-SW-A)
    # 显存碎片化→骤停：INLINE_INJECT=2a；沐曦 loud=chunks12/stall768MB/0.25s
    INJECT_KIND="${INJECT_KIND:-2a}"
    MODE="${MODE_OVERRIDE:-gpu_bound}"
    ACCEPT_MIN_RATIO="${ACCEPT_MIN_RATIO:-1.3}"
    INLINE_2A_CHUNKS="${INLINE_2A_CHUNKS:-12}"
    INLINE_2A_STALL_MB="${INLINE_2A_STALL_MB:-768}"
    INLINE_2A_STALL_S="${INLINE_2A_STALL_S:-0.25}"
    INJECT_ARGS="${INJECT_ARGS:-chunks=${INLINE_2A_CHUNKS},stall_mb=${INLINE_2A_STALL_MB},stall_s=${INLINE_2A_STALL_S}}"
    ;;
  P1-SW-B)
    # 动态 shape / 罕见 seq：INLINE_INJECT=2b；沐曦 loud=rare_seq=1536,every=1；accept≥1.15
    INJECT_KIND="${INJECT_KIND:-2b}"
    MODE="${MODE_OVERRIDE:-gpu_bound}"
    ACCEPT_MIN_RATIO="${ACCEPT_MIN_RATIO:-1.15}"
    RARE_SHAPE_SEQ="${RARE_SHAPE_SEQ:-1536}"
    RARE_SHAPE_EVERY="${RARE_SHAPE_EVERY:-1}"
    INJECT_ARGS="${INJECT_ARGS:-rare_seq=${RARE_SHAPE_SEQ},every=${RARE_SHAPE_EVERY}}"
    ;;
  P1-SW-C)
    # 首次编译尖刺：INLINE_INJECT=2c；沐曦 loud=n=1024,every=1,fallback=0.2；tip 用 spike 闸门
    INJECT_KIND="${INJECT_KIND:-2c}"
    MODE="${MODE_OVERRIDE:-gpu_bound}"
    ACCEPT_MIN_RATIO="${ACCEPT_MIN_RATIO:-1.3}"
    INLINE_2C_N="${INLINE_2C_N:-1024}"
    INLINE_2C_EVERY="${INLINE_2C_EVERY:-1}"
    INLINE_2C_FALLBACK_S="${INLINE_2C_FALLBACK_S:-0.6}"
    INJECT_ARGS="${INJECT_ARGS:-n=${INLINE_2C_N},every=${INLINE_2C_EVERY},fallback_s=${INLINE_2C_FALLBACK_S}}"
    # median 常盲；默认改用 tip spike accept（可用 ACCEPT_SCRIPT 覆盖）
    ACCEPT_SCRIPT="${ACCEPT_SCRIPT:-${FS_SHARED_SCRIPTS}/agent_overlays/p1c-20260724/accept_p1swc_spike.py}"
    ;;
  P3-EXT-B)
    # 抢磁盘 IO：镜像无 fio/apt → stress-ng --hdd/--iomix；同盘 ckpt+payload 才咬 step_ms
    # 注意：顶层已设 CKPT_EVERY/IO_READ_KB 默认，此处必须无条件覆盖，不能用 :-
    INJECT_KIND="${INJECT_KIND:-stress_io}"
    MODE="${MODE_OVERRIDE:-host_bound}"
    ACCEPT_MIN_RATIO="${ACCEPT_MIN_RATIO:-1.3}"
    CKPT_EVERY="${CKPT_EVERY_OVERRIDE:-20}"
    FLUSH_EVERY="${FLUSH_EVERY_OVERRIDE:-1}"
    IO_STRESS_DIR="${IO_STRESS_DIR:-${POD_BUNDLE}/io_stress}"
    IO_PAYLOAD="${IO_PAYLOAD:-${IO_STRESS_DIR}/payload.bin}"
    IO_READ_KB="${IO_READ_KB_OVERRIDE:-1024}"
    HDD_N="${HDD_N:-32}"
    HDD_BYTES="${HDD_BYTES:-2G}"
    IOMIX_N="${IOMIX_N:-16}"
    INJECT_ARGS="${INJECT_ARGS:-fio_nj=${FIO_NUMJOBS:-16},iodepth=${FIO_IODEPTH:-64},bs=${FIO_BS:-4k},size=${FIO_SIZE:-4G},ckpt_every=${CKPT_EVERY},io_read_kb=${IO_READ_KB}}"
    ;;
  P3-EXT-C)
    # 抢内存带宽：stress-ng --vm --vm-keep --page-in；看 PSI memory vs cpu（本机核若无 /proc/pressure 则记 UNAVAIL）
    INJECT_KIND="${INJECT_KIND:-stress_vm}"
    MODE="${MODE_OVERRIDE:-host_bound}"
    ACCEPT_MIN_RATIO="${ACCEPT_MIN_RATIO:-1.3}"
    VM_N="${VM_N:-96}"
    VM_BYTES="${VM_BYTES:-6G}"
    INJECT_ARGS="${INJECT_ARGS:-vm_n=${VM_N},vm_bytes=${VM_BYTES}}"
    ;;
  P2-SW-B)
    # HCCL 通信算法切换（对齐沐曦 mccl_algo）：C0/C1/C2 同开大 AllReduce；
    # 仅 C1/C2 钳 HCCL_ALGO=ring（+可选小 buffsize）。主证=comm_ms。
    INJECT_KIND="${INJECT_KIND:-hccl_algo}"
    MODE="${MODE_OVERRIDE:-gpu_bound}"
    ACCEPT_MIN_RATIO="${ACCEPT_MIN_RATIO:-1.3}"
    HCCL_ALGO_V="${HCCL_ALGO_V:-ring}"
    HCCL_STRESS_MB="${HCCL_STRESS_MB:-512}"
    HCCL_BUFFSIZE_V="${HCCL_BUFFSIZE_V:-}"
    INJECT_ARGS="${INJECT_ARGS:-algo=${HCCL_ALGO_V},stress_mb=${HCCL_STRESS_MB}${HCCL_BUFFSIZE_V:+,buffsize=${HCCL_BUFFSIZE_V}}}"
    ACCEPT_SCRIPT="${ACCEPT_SCRIPT:-${ROOT}/scripts/fail-slow/accept_p2swb_comm.py}"
    ;;
  P2-SW-C)
    # 拓扑映射漂移（对齐沐曦 topo_5c）：C1/C2 逆序 ASCEND_VISIBLE + TOPO_EXTRA_AR。
    # 主证优先 comm_ms（EXTRA_AR 落 comm 窗）；step 弱不 FAIL。禁 P2P_DISABLE。
    INJECT_KIND="${INJECT_KIND:-topo_5c}"
    MODE="${MODE_OVERRIDE:-gpu_bound}"
    ACCEPT_MIN_RATIO="${ACCEPT_MIN_RATIO:-1.15}"
    TOPO_DEVICE_REV="${TOPO_DEVICE_REV:-1}"
    TOPO_EXTRA_AR="${TOPO_EXTRA_AR:-512}"
    TOPO_AR_ELEMS="${TOPO_AR_ELEMS:-262144}"
    INJECT_ARGS="${INJECT_ARGS:-device_rev=${TOPO_DEVICE_REV},topo_extra_ar=${TOPO_EXTRA_AR},topo_ar_elems=${TOPO_AR_ELEMS}}"
    ACCEPT_SCRIPT="${ACCEPT_SCRIPT:-${ROOT}/scripts/fail-slow/accept_p2swc.py}"
    ;;
  *)
    echo "hold_exec_run_case: CASE_ID=$CASE_ID not yet wired" >&2
    exit 2
    ;;
esac

cat >"$LOCAL_RESULT_ROOT/manifest.yaml" <<YAML
case_id: ${CASE_ID}
dose: ${DOSE}
phase: ${PHASE}
run_id: ${RUN_ID}
world_size: $((NNODES * NPROC))
nnodes: ${NNODES}
nproc: ${NPROC}
pod: ${POD}
pool: pool-case
hold_job: yysong
mode: ${MODE}
inject_kind: ${INJECT_KIND}
inject_args: "${INJECT_ARGS}"
inject_window_measure: [${INJECT_START}, ${INJECT_STOP}]
victim_local_rank: ${SIDECAR_LOCAL_RANK}
host_bound_matmul: ${HOST_BOUND_MATMUL}
seed: ${SEED}
iters: ${ITERS}
warmup: ${WARMUP}
model: ${MODEL}
seq: ${SEQ}
batch: ${BATCH}
abc_configs: ${ABC_CONFIGS}
tools: [C0, C1, C2]
afs_pod_out: ${POD_OUT}
label_prefix: yjr-as-c
YAML

echo "[hold-exec] RUN_ID=$RUN_ID POD=$POD CASE=$CASE_ID configs=$ABC_CONFIGS"

SKIP_HEAVY_JSYNC="${HOLD_EXEC_SKIP_HEAVY_JSYNC:-0}"

JEXEC_POLL_TIMEOUT_S="${JEXEC_POLL_TIMEOUT_S:-25}"

jexec() {
  local cmd="$1"
  if [[ "${JUMP_HOST}" == "localhost" || "${JUMP_HOST}" == "127.0.0.1" ]]; then
    export KUBECONFIG="${JUMP_KUBECONFIG:-${KUBECONFIG}}"
    K="${JUMP_KUBECTL:-kubectl}"
    "$K" -n "${NS}" exec "${POD}" -- bash -lc "$cmd"
  else
    ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30 "${JUMP_HOST}" \
      "export KUBECONFIG='${JUMP_KUBECONFIG}'; K='${JUMP_KUBECTL}'; \$K -n '${NS}' exec '${POD}' -- bash -lc $(printf '%q' "$cmd")"
  fi
}

# 轮询用：Mac 侧超时；保留 exit code（供 test -f 等判断）
_jexec_poll_run() {
  local t="$1"; shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$t" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$t" "$@"
  else
    python3 - "$t" "$@" <<'PY'
import subprocess, sys
t = float(sys.argv[1])
rc = subprocess.call(sys.argv[2:], timeout=t)
sys.exit(rc)
PY
  fi
}

jexec_poll() {
  local cmd="$1"
  local t="${2:-${JEXEC_POLL_TIMEOUT_S}}"
  if [[ "${JUMP_HOST}" == "localhost" || "${JUMP_HOST}" == "127.0.0.1" ]]; then
    export KUBECONFIG="${JUMP_KUBECONFIG:-${KUBECONFIG}}"
    K="${JUMP_KUBECTL:-kubectl}"
    _jexec_poll_run "$t" "$K" -n "${NS}" exec "${POD}" -- bash -lc "$cmd"
  else
    _jexec_poll_run "$t" ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15 "${JUMP_HOST}" \
      "export KUBECONFIG='${JUMP_KUBECONFIG}'; K='${JUMP_KUBECTL}'; \$K -n '${NS}' exec '${POD}' -- bash -lc $(printf '%q' "$cmd")"
  fi
}

# B3：从 set_upgrade.log 读字段；阻塞 jexec + awk；空读重试；可选本机副本
read_set_upgrade_field() {
  local remote_log="$1" field="$2" local_copy="${3:-}" attempt v=""
  for attempt in 1 2 3 4 5; do
    case "$field" in
      set_l)
        v=$(jexec "awk -F= '/^SET_L=/{print \$2; exit}' '${remote_log}' 2>/dev/null" 2>/dev/null | tr -d '[:space:]')
        ;;
      set_ok_pid)
        v=$(jexec "awk '/^SET_OK_WORKER/{for(i=1;i<=NF;i++) if(\$i~/^pid=/){sub(/^pid=/,\"\",\$i); print \$i; exit}}' '${remote_log}' 2>/dev/null" 2>/dev/null | tr -d '[:space:]')
        ;;
      culprit_rank)
        v=$(jexec "awk -F= '/^CULPRIT_RANK=/{print \$2; exit}' '${remote_log}' 2>/dev/null" 2>/dev/null | tr -d '[:space:]')
        ;;
      culprit_pid)
        v=$(jexec "awk -F= '/^CULPRIT_PID=/{print \$2; exit}' '${remote_log}' 2>/dev/null" 2>/dev/null | tr -d '[:space:]')
        ;;
    esac
    if [[ -n "$v" ]] && [[ "$v" =~ ^[0-9]+$ ]]; then
      echo "$v"
      return 0
    fi
    if [[ -n "$local_copy" && -f "$local_copy" ]]; then
      case "$field" in
        set_l) v=$(awk -F= '/^SET_L=/{print $2; exit}' "$local_copy" 2>/dev/null | tr -d '[:space:]') ;;
        set_ok_pid) v=$(awk '/^SET_OK_WORKER/{for(i=1;i<=NF;i++) if($i~/^pid=/){sub(/^pid=/,"",$i); print $i; exit}}' "$local_copy" 2>/dev/null | tr -d '[:space:]') ;;
        culprit_rank) v=$(awk -F= '/^CULPRIT_RANK=/{print $2; exit}' "$local_copy" 2>/dev/null | tr -d '[:space:]') ;;
        culprit_pid) v=$(awk -F= '/^CULPRIT_PID=/{print $2; exit}' "$local_copy" 2>/dev/null | tr -d '[:space:]') ;;
      esac
      if [[ -n "$v" ]] && [[ "$v" =~ ^[0-9]+$ ]]; then
        echo "$v"
        return 0
      fi
    fi
    sleep 2
  done
  return 1
}

jsync_file() {
  local src="$1" dst="$2"
  local bname ddir rc
  bname=$(basename "$src")
  ddir=$(dirname "$dst")
  # extract to a staging dir then install — avoids "same file" when dst name == tar member
  set +e
  if [[ "${JUMP_HOST}" == "localhost" || "${JUMP_HOST}" == "127.0.0.1" ]]; then
    export KUBECONFIG="${JUMP_KUBECONFIG:-${KUBECONFIG}}"
    K="${JUMP_KUBECTL:-kubectl}"
    COPYFILE_DISABLE=1 tar -C "$(dirname "$src")" -cf - "$bname" \
      | "$K" -n "${NS}" exec -i "${POD}" -- bash -lc "mkdir -p '$ddir' /tmp/yjr_sync && tar -C /tmp/yjr_sync -xf - && install -m 0755 /tmp/yjr_sync/$bname '$dst' && rm -f /tmp/yjr_sync/$bname"
  else
    COPYFILE_DISABLE=1 tar -C "$(dirname "$src")" -cf - "$bname" \
      | ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30 "${JUMP_HOST}" \
        "export KUBECONFIG='${JUMP_KUBECONFIG}'; K='${JUMP_KUBECTL}'; \$K -n '${NS}' exec -i '${POD}' -- bash -lc $(printf '%q' "mkdir -p '$ddir' /tmp/yjr_sync && tar -C /tmp/yjr_sync -xf - && install -m 0755 /tmp/yjr_sync/$bname '$dst' && rm -f /tmp/yjr_sync/$bname")"
  fi
  rc=$?
  set -e
  echo "[hold-exec] jsync $bname -> $dst rc=$rc"
  return 0
}

pod_ip() {
  if [[ "${JUMP_HOST}" == "localhost" || "${JUMP_HOST}" == "127.0.0.1" ]]; then
    export KUBECONFIG="${JUMP_KUBECONFIG:-${KUBECONFIG}}"
    K="${JUMP_KUBECTL:-kubectl}"
    "$K" -n "${NS}" get pod "${POD}" -o jsonpath='{.status.podIP}'
  else
    ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30 "${JUMP_HOST}" \
      "export KUBECONFIG='${JUMP_KUBECONFIG}'; K='${JUMP_KUBECTL}'; \$K -n '${NS}' get pod '${POD}' -o jsonpath='{.status.podIP}'"
  fi
}

echo "[hold-exec] sync train → ${POD_BUNDLE}/train_bench_probe_npu.py"
jexec "mkdir -p '${POD_BUNDLE}' '${POD_OUT}' '${POD_PYDEPS}'; exit 0" || true
if [[ "${SKIP_HEAVY_JSYNC}" != "1" ]]; then
  jsync_file "$TRAIN_PY_LOCAL" "${POD_BUNDLE}/train_bench_probe_npu.py"
  if [[ -f "$SIDECAR_PY_LOCAL" ]]; then
    echo "[hold-exec] sync sidecar → ${POD_BUNDLE}/sidecar_inject_npu.py"
    jsync_file "$SIDECAR_PY_LOCAL" "${POD_BUNDLE}/sidecar_inject_npu.py"
  fi
  if [[ -f "$SIDECAR_8C_PY_LOCAL" ]]; then
    echo "[hold-exec] sync 8c sidecar → ${POD_BUNDLE}/sidecar_inject_8c.py"
    jsync_file "$SIDECAR_8C_PY_LOCAL" "${POD_BUNDLE}/sidecar_inject_8c.py"
  fi
  if [[ -f "$DUMP_SQL_LOCAL" ]]; then
    jsync_file "$DUMP_SQL_LOCAL" "${POD_BUNDLE}/dump_probing_sql.sh"
  fi
  LOCALIZE_PY_LOCAL="${LOCALIZE_PY_LOCAL:-${ROOT}/scripts/fail-slow/pillar_c_localize_culprit.py}"
  if [[ -f "$LOCALIZE_PY_LOCAL" ]]; then
    echo "[hold-exec] sync localize → ${POD_BUNDLE}/pillar_c_localize_culprit.py"
    jsync_file "$LOCALIZE_PY_LOCAL" "${POD_BUNDLE}/pillar_c_localize_culprit.py"
  fi
else
  echo "[hold-exec] SKIP_HEAVY_JSYNC=1 — reuse bundle scripts (train/sidecar/dump/localize/hold_exec; no tar)"
fi
LOCALIZE_PY_LOCAL="${LOCALIZE_PY_LOCAL:-${ROOT}/scripts/fail-slow/pillar_c_localize_culprit.py}"
MASTER_IP="$(pod_ip)"
echo "[hold-exec] MASTER_IP=$MASTER_IP"
[[ -n "$MASTER_IP" ]] || { echo "FATAL: no pod IP"; exit 2; }

echo "[hold-exec] checking pod idle…"

clean_pod() {
  jexec "pkill -9 -f '[t]rain_bench_probe_npu' 2>/dev/null || true; pkill -9 -f '/tmp/[t]bp_npu.py' 2>/dev/null || true; pkill -9 -f '[t]orchrun' 2>/dev/null || true; pkill -9 -f '[s]idecar_inject_npu' 2>/dev/null || true; pkill -9 -f '[s]idecar_inject_8c' 2>/dev/null || true; pkill -9 -x stress-ng 2>/dev/null || true; pkill -9 -f '[s]tress-ng' 2>/dev/null || true; pkill -9 -f 'fio.*io_stress' 2>/dev/null || true; sleep 2; exit 0" || true
}

fire_config() {
  local cfg="$1" gid="$2"
  local out="${POD_OUT}/${CASE_ID}/by_pod/${POD}/round_1/${cfg}"
  local port=$((30000 + gid * 100))
  echo "========== ${CASE_ID} / ${cfg} (gid=$gid port=$port) =========="
  clean_pod
  echo "[hold-exec] prep outdir ${out}"
  jexec "rm -rf '${out}'; mkdir -p '${out}/ranks'; cp -f '${POD_BUNDLE}/train_bench_probe_npu.py' /tmp/tbp_npu.py; exit 0" || true

  local denv="unset PROBING PROBING_TORCH_PROFILING PROBING_GPU INLINE_INJECT 2>/dev/null || true; export PROBING=0;"
  if [ "$cfg" = "C2_probing" ]; then
    # 默认 C2；Pillar-C 三臂可经环境覆盖（PROBING / SAMPLE_MS / TORCH / COLD_MAX / DATA_DIR）
    local _p="${PROBING:-2}"
    local _sms="${PROBING_GPU_SAMPLE_MS:-1000}"
    local _csms="${PROBING_CPU_SAMPLE_MS:-${_sms}}"
    denv="export PROBING=${_p}; export PROBING_GPU=on; export PROBING_GPU_BACKEND=npu; export PROBING_NPU_SOURCE=auto; export PROBING_GPU_SAMPLE_MS=${_sms}; export PROBING_CPU=on; export PROBING_CPU_SAMPLE_MS=${_csms}; export PYTHONPATH=${POD_PYDEPS}:\${PYTHONPATH:-}; export PATH=${POD_PYDEPS}/bin:${PYBIN}:\${PATH};"
    if [[ -n "${PROBING_TORCH_PROFILING+x}" ]]; then
      if [[ -n "${PROBING_TORCH_PROFILING}" ]]; then
        denv="${denv} export PROBING_TORCH_PROFILING='${PROBING_TORCH_PROFILING}';"
      else
        denv="${denv} unset PROBING_TORCH_PROFILING;"
      fi
    else
      denv="${denv} unset PROBING_TORCH_PROFILING;"
    fi
    if [[ -n "${PROBING_COLD_MAX_TOTAL_MB:-}" ]]; then
      denv="${denv} export PROBING_COLD_MAX_TOTAL_MB=${PROBING_COLD_MAX_TOTAL_MB};"
    else
      denv="${denv} unset PROBING_COLD_MAX_TOTAL_MB 2>/dev/null || true;"
    fi
    if [[ -n "${PROBING_DATA_DIR:-}" ]]; then
      denv="${denv} export PROBING_DATA_DIR='${PROBING_DATA_DIR}';"
    fi
    # 冷层默认开（GATE G1）；显式 PROBING_COLD=off 才关
    if [[ -n "${PROBING_COLD:-}" ]]; then
      denv="${denv} export PROBING_COLD='${PROBING_COLD}';"
    else
      denv="${denv} unset PROBING_COLD 2>/dev/null || true;"
    fi
    # Param-Calib / P-FIX：cpu.utilization 环容量（MiB）；未设则用 wheel 默认
    if [[ -n "${PROBING_CPU_RING_MB:-}" ]]; then
      denv="${denv} export PROBING_CPU_RING_MB=${PROBING_CPU_RING_MB};"
    fi
    if [[ -n "${PROBING_SPAN_BACKENDS:-}" ]]; then
      denv="${denv} export PROBING_SPAN_BACKENDS='${PROBING_SPAN_BACKENDS}';"
    fi
    if [[ -n "${PROBING_TORCH_MIN_STEP_INTERVAL:-}" ]]; then
      denv="${denv} export PROBING_TORCH_MIN_STEP_INTERVAL=${PROBING_TORCH_MIN_STEP_INTERVAL};"
    fi
    # Pillar-C S1：晚 attach（训练步内 site_hook；非 ptrace — Ascend 无 libprobing.so）
    if [[ -n "${PROBING_ATTACH_AT_STEP:-}" ]]; then
      denv="${denv} export PROBING_ATTACH_AT_STEP=${PROBING_ATTACH_AT_STEP};"
      if [[ -n "${PROBING_DEFERRED_VALUE:-}" ]]; then
        denv="${denv} export PROBING_DEFERRED_VALUE=${PROBING_DEFERRED_VALUE};"
      fi
    fi
  fi
  # C1/C2：inline cube（同进程）
  if [[ "$cfg" == C1_* || "$cfg" == C2_* ]] && [[ "$INJECT_KIND" == inline_cube ]]; then
    denv="${denv} export INLINE_INJECT=cube; export INLINE_VICTIM_LOCAL_RANK=${SIDECAR_LOCAL_RANK}; export INLINE_INJECT_START=${INJECT_START}; export INLINE_INJECT_STOP=${INJECT_STOP}; export INLINE_CUBE_SIZE=${CUBE_SIZE}; export INLINE_CUBE_MM=${CUBE_MM};"
  fi
  # C1/C2：inline HBM（同进程带宽争用；1b=渐进 ramp）
  if [[ "$cfg" == C1_* || "$cfg" == C2_* ]] && [[ "$INJECT_KIND" == "inline_hbm" || "$INJECT_KIND" == "hbm" || "$INJECT_KIND" == "1b" || "$INJECT_KIND" == "hbm_ramp" ]]; then
    denv="${denv} export INLINE_INJECT=hbm; export INLINE_VICTIM_LOCAL_RANK=${SIDECAR_LOCAL_RANK}; export INLINE_INJECT_START=${INJECT_START}; export INLINE_INJECT_STOP=${INJECT_STOP}; export INLINE_HBM_MB=${INLINE_HBM_MB}; export INLINE_HBM_COPIES=${INLINE_HBM_COPIES}; export INLINE_HBM_COPIES_MAX=${INLINE_HBM_COPIES_MAX:-48}; export INLINE_HBM_RAMP=${INLINE_HBM_RAMP:-0};"
    if [[ "$INJECT_KIND" == "1b" || "$INJECT_KIND" == "hbm_ramp" || "${INLINE_HBM_RAMP}" == "1" ]]; then
      denv="${denv} export INLINE_HBM_RAMP=1;"
    fi
  fi
  # C1/C2：inline 8a GC/stall（同进程）
  if [[ "$cfg" == C1_* || "$cfg" == C2_* ]] && [[ "$INJECT_KIND" == 8a || "$INJECT_KIND" == inline_8a ]]; then
    denv="${denv} export INLINE_INJECT=8a; export INLINE_VICTIM_LOCAL_RANK=${SIDECAR_LOCAL_RANK}; export INLINE_INJECT_START=${INJECT_START}; export INLINE_INJECT_STOP=${INJECT_STOP}; export INLINE_GC_EVERY=${INLINE_GC_EVERY:-1}; export INLINE_GC_STALL_S=${INLINE_GC_STALL_S:-0.25};"
  fi
  # C1/C2：inline 8b dataloader 泄漏（同进程 leak MB + data_stall）
  if [[ "$cfg" == C1_* || "$cfg" == C2_* ]] && [[ "$INJECT_KIND" == 8b || "$INJECT_KIND" == inline_8b ]]; then
    denv="${denv} export INLINE_INJECT=8b; export INLINE_VICTIM_LOCAL_RANK=${SIDECAR_LOCAL_RANK}; export INLINE_INJECT_START=${INJECT_START}; export INLINE_INJECT_STOP=${INJECT_STOP}; export INLINE_8B_MB=${INLINE_8B_MB:-16}; export INLINE_8B_STALL_S=${INLINE_8B_STALL_S:-0.25};"
  fi
  # C1/C2：inline 2a 显存碎片化→骤停（同进程，barrier 前）
  if [[ "$cfg" == C1_* || "$cfg" == C2_* ]] && [[ "$INJECT_KIND" == 2a || "$INJECT_KIND" == inline_2a ]]; then
    denv="${denv} export INLINE_INJECT=2a; export INLINE_VICTIM_LOCAL_RANK=${SIDECAR_LOCAL_RANK}; export INLINE_INJECT_START=${INJECT_START}; export INLINE_INJECT_STOP=${INJECT_STOP}; export INLINE_2A_CHUNKS=${INLINE_2A_CHUNKS:-12}; export INLINE_2A_STALL_MB=${INLINE_2A_STALL_MB:-768}; export INLINE_2A_STALL_S=${INLINE_2A_STALL_S:-0.25};"
  fi
  # C1/C2：inline 2b 罕见 shape（同进程 pad/truncate seq）
  if [[ "$cfg" == C1_* || "$cfg" == C2_* ]] && [[ "$INJECT_KIND" == 2b || "$INJECT_KIND" == inline_2b || "$INJECT_KIND" == rare_shape ]]; then
    denv="${denv} export INLINE_INJECT=2b; export INLINE_VICTIM_LOCAL_RANK=${SIDECAR_LOCAL_RANK}; export INLINE_INJECT_START=${INJECT_START}; export INLINE_INJECT_STOP=${INJECT_STOP}; export RARE_SHAPE_SEQ=${RARE_SHAPE_SEQ:-1536}; export RARE_SHAPE_EVERY=${RARE_SHAPE_EVERY:-1};"
  fi
  # C1/C2：inline 2c 首次编译尖刺（同进程 compile/fallback）
  if [[ "$cfg" == C1_* || "$cfg" == C2_* ]] && [[ "$INJECT_KIND" == 2c || "$INJECT_KIND" == inline_2c || "$INJECT_KIND" == compile_spike ]]; then
    denv="${denv} export INLINE_INJECT=2c; export INLINE_VICTIM_LOCAL_RANK=${SIDECAR_LOCAL_RANK}; export INLINE_INJECT_START=${INJECT_START}; export INLINE_INJECT_STOP=${INJECT_STOP}; export INLINE_2C_N=${INLINE_2C_N:-1024}; export INLINE_2C_EVERY=${INLINE_2C_EVERY:-1}; export INLINE_2C_FALLBACK_S=${INLINE_2C_FALLBACK_S:-0.6};"
  fi
  # P2-SW-B：C0/C1/C2 同开 HCCL_STRESS_MB；仅 C1/C2 钳 HCCL_ALGO（+可选 buffsize）
  if [[ "$INJECT_KIND" == hccl_algo || "$INJECT_KIND" == mccl_algo ]]; then
    denv="${denv} export HCCL_STRESS_MB=${HCCL_STRESS_MB:-512};"
    if [[ "$cfg" == C1_* || "$cfg" == C2_* ]]; then
      denv="${denv} export HCCL_ALGO='level0:NA;level1:${HCCL_ALGO_V:-ring}';"
      if [[ -n "${HCCL_BUFFSIZE_V:-}" ]]; then
        denv="${denv} export HCCL_BUFFSIZE=${HCCL_BUFFSIZE_V};"
      fi
    fi
  fi
  # P2-SW-C：仅 C1/C2 逆序 ASCEND_VISIBLE + TOPO_EXTRA_AR（模拟拓扑漂移绕远）
  if [[ "$INJECT_KIND" == topo_5c || "$INJECT_KIND" == 5c || "$INJECT_KIND" == topo ]]; then
    if [[ "$cfg" == C1_* || "$cfg" == C2_* ]]; then
      denv="${denv} export TOPO_EXTRA_AR=${TOPO_EXTRA_AR:-512}; export TOPO_AR_ELEMS=${TOPO_AR_ELEMS:-262144};"
      if [[ "${TOPO_DEVICE_REV:-1}" == "1" ]]; then
        denv="${denv} _AVD=\${ASCEND_VISIBLE_DEVICES:-0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15}; export ASCEND_VISIBLE_DEVICES=\$(echo \"\$_AVD\" | tr ',' '\\n' | tac | paste -sd, -);"
      fi
    fi
  fi

  # P3-EXT-B：ckpt 与 stress IO 同盘，否则咬不到 step_ms
  CKPT_DIR_EFFECTIVE="${POD_BUNDLE}/ckpt"
  if [[ "$INJECT_KIND" == stress_io && -n "${IO_STRESS_DIR}" ]]; then
    CKPT_DIR_EFFECTIVE="${IO_STRESS_DIR}/ckpt"
    jexec "mkdir -p '${IO_STRESS_DIR}' '${CKPT_DIR_EFFECTIVE}'; exit 0" || true
  fi

  cat >"${WORK}/run_${gid}.sh" <<LAUNCH
#!/usr/bin/env bash
set -euo pipefail
source /root/miniconda3/etc/profile.d/conda.sh
conda activate llm_test
export PYTHONUNBUFFERED=1
export PATH=${PYBIN}:\${PATH}
export PYTHONPATH=${POD_PYDEPS}:\${PYTHONPATH:-}
export GLOO_SOCKET_IFNAME=\${GLOO_SOCKET_IFNAME:-eth0}
export HCCL_CONNECT_TIMEOUT=\${HCCL_CONNECT_TIMEOUT:-1800}
export HCCL_EXEC_TIMEOUT=\${HCCL_EXEC_TIMEOUT:-600}
export HOST_BOUND_MATMUL=${HOST_BOUND_MATMUL}
export CKPT_DIR=${CKPT_DIR_EFFECTIVE}
${denv}
OUT='${out}'
rm -f "\$OUT/node_0.done" "\$OUT/node_0.fail"
rm -rf "\$OUT/ranks"
mkdir -p "\$OUT/ranks"
${TORCHRUN} --nnodes=${NNODES} --nproc_per_node=${NPROC} --node_rank=0 \\
  --master_addr=${MASTER_IP} --master_port=${port} \\
  /tmp/tbp_npu.py --iters=${ITERS} --warmup=${WARMUP} --seed=${SEED} --mode=${MODE} --model=${MODEL} --seq=${SEQ} --batch=${BATCH} \\
  --flush-every=${FLUSH_EVERY} --ckpt-every=${CKPT_EVERY} \\
  --io-payload='${IO_PAYLOAD}' --io-read-kb=${IO_READ_KB} \\
  --run-id=${RUN_ID} --group=${gid} --config='${cfg}' --round=1 \\
  --out-dir="\$OUT/ranks" > "\$OUT/node_0.log" 2>&1
rc=\$?
if [[ \$rc -eq 0 ]]; then touch "\$OUT/node_0.done"; else echo \$rc > "\$OUT/node_0.fail"; fi
LAUNCH
  chmod +x "${WORK}/run_${gid}.sh"
  jsync_file "${WORK}/run_${gid}.sh" "/tmp/run_${gid}.sh"
  echo "[hold-exec] firing ${cfg}…"
  jexec "setsid nohup bash /tmp/run_${gid}.sh </dev/null >/dev/null 2>&1 & echo FIRE_OK; exit 0" || true

  local e=0
  while [ "$e" -lt 360 ]; do
    if jexec "test -f '${out}/ranks/warmup_done'" 2>/dev/null; then
      echo "  warmup ok (${e}s)"; break
    fi
    if jexec "test -f '${out}/node_0.fail'" 2>/dev/null; then
      echo "  FAIL before warmup"; jexec "tail -n 100 '${out}/node_0.log'" || true
      return 1
    fi
    sleep 5; e=$((e + 5))
    if [ $((e % 30)) -eq 0 ]; then
      echo "  waiting warmup… ${e}s"
      jexec "tail -n 15 '${out}/node_0.log' 2>/dev/null || true" || true
    fi
  done
  if [ "$e" -ge 360 ]; then
    echo "  warmup timeout"; jexec "tail -n 120 '${out}/node_0.log'" || true
    return 1
  fi

  # C2 健康臂（INJECT_KIND=none）：dump 不得绑在注入门闩内（PR-1 baseline 教训）
  if [[ "$cfg" == "C2_probing" ]] && [[ "${DUMP_PROBING_SQL}" == "1" ]] \
     && [[ "$INJECT_KIND" == "none" || -z "$INJECT_KIND" ]]; then
    echo "  waiting ${DUMP_WAIT_S}s for SQL dump (no inject)…"
    sleep "${DUMP_WAIT_S}"
    if jexec "pgrep -f 'tbp_npu|train_bench_probe_npu' >/dev/null" 2>/dev/null; then
      echo "  dumping Probing SQL / host_psi…"
      jexec "export OUT_DIR='${out}' CASE='${CASE_ID}' CODE_DIR='${POD_BUNDLE}' VICTIM_LOCAL_RANK='${SIDECAR_LOCAL_RANK}' PYTHONPATH='${POD_PYDEPS}:\${PYTHONPATH:-}' PATH='/usr/bin:/bin:${POD_PYDEPS}/bin:${PYBIN}:\${PATH:-}'; /bin/bash '${POD_BUNDLE}/dump_probing_sql.sh' >'${out}/probing_dump.log' 2>&1; exit 0" || true
      echo "  SQL dump attempted → ${out}/probing/"
    else
      echo "  SQL dump skipped: training not running"
    fi
  fi

  if [[ "$cfg" == C1_* || "$cfg" == C2_* ]] && [[ "$INJECT_KIND" == stress_cpu || "$INJECT_KIND" == stress_io || "$INJECT_KIND" == stress_vm || "$INJECT_KIND" == cube || "$INJECT_KIND" == inline_cube || "$INJECT_KIND" == inline_hbm || "$INJECT_KIND" == hbm || "$INJECT_KIND" == "1b" || "$INJECT_KIND" == "hbm_ramp" || "$INJECT_KIND" == 8a || "$INJECT_KIND" == inline_8a || "$INJECT_KIND" == 8b || "$INJECT_KIND" == inline_8b || "$INJECT_KIND" == 8c || "$INJECT_KIND" == sidecar_8c || "$INJECT_KIND" == 2a || "$INJECT_KIND" == inline_2a || "$INJECT_KIND" == 2b || "$INJECT_KIND" == inline_2b || "$INJECT_KIND" == rare_shape || "$INJECT_KIND" == 2c || "$INJECT_KIND" == inline_2c || "$INJECT_KIND" == compile_spike || "$INJECT_KIND" == hccl_algo || "$INJECT_KIND" == mccl_algo || "$INJECT_KIND" == topo_5c || "$INJECT_KIND" == 5c || "$INJECT_KIND" == topo ]]; then
    echo "  wait measure step ${INJECT_START}…"
    e=0
    while [ "$e" -lt 2400 ]; do
      if jexec "test -f '${out}/ranks/step_${INJECT_START}.marker'" 2>/dev/null; then
        echo "  measure step ${INJECT_START} reached (${e}s)"; break
      fi
      if jexec "test -f '${out}/node_0.fail'" 2>/dev/null; then
        echo "  FAIL before inject"; jexec "tail -n 100 '${out}/node_0.log'" || true
        return 1
      fi
      sleep 5; e=$((e + 5))
      if [ $((e % 60)) -eq 0 ]; then echo "  waiting step ${INJECT_START}… ${e}s"; fi
    done
    if [ "$e" -ge 2400 ]; then echo "  step ${INJECT_START} timeout"; return 1; fi

    if [[ "$INJECT_KIND" == hccl_algo || "$INJECT_KIND" == mccl_algo ]]; then
      echo "  hccl_algo armed (algo=${HCCL_ALGO_V:-ring} stress_mb=${HCCL_STRESS_MB:-512} buffsize=${HCCL_BUFFSIZE_V:-default})"
      jexec "printf '%s\n' 'INLINE_INJECT kind=hccl_algo' 'SIDECAR_START kind=hccl_algo' \"HCCL_ALGO=level0:NA;level1=${HCCL_ALGO_V:-ring}\" \"HCCL_STRESS_MB=${HCCL_STRESS_MB:-512}\" \"HCCL_BUFFSIZE=${HCCL_BUFFSIZE_V:-default}\" >'${out}/injection.log'; exit 0" || true
    elif [[ "$INJECT_KIND" == topo_5c || "$INJECT_KIND" == 5c || "$INJECT_KIND" == topo ]]; then
      echo "  topo_5c armed (device_rev=${TOPO_DEVICE_REV:-1} TOPO_EXTRA_AR=${TOPO_EXTRA_AR:-512} TOPO_AR_ELEMS=${TOPO_AR_ELEMS:-262144})"
      jexec "printf '%s\n' 'SIDECAR_WARMUP kind=topo_5c' 'SIDECAR_START kind=topo_5c' \"TOPO_EXTRA_AR=${TOPO_EXTRA_AR:-512}\" \"TOPO_AR_ELEMS=${TOPO_AR_ELEMS:-262144}\" \"DEVICE_REV=${TOPO_DEVICE_REV:-1}\" >'${out}/injection.log'; exit 0" || true
    elif [[ "$INJECT_KIND" == inline_cube ]]; then
      echo "  inline cube active (victim=${SIDECAR_LOCAL_RANK} size=${CUBE_SIZE} mm=${CUBE_MM})"
      jexec "grep -E \"INLINE_CUBE|SIDECAR\" '${out}/node_0.log' | head -20 >'${out}/injection.log' || true; echo SIDECAR_START kind=inline_cube size=${CUBE_SIZE} mm=${CUBE_MM} victim=${SIDECAR_LOCAL_RANK} >>'${out}/injection.log'; exit 0" || true
      # npu-smi on physical id for victim logical rank
      # npu-smi：远端展开 \${ASCEND_VISIBLE_DEVICES:-0}/\${PHYS:-0}，本地勿裸扩
      jexec "PHYS=\$(echo \${ASCEND_VISIBLE_DEVICES:-0} | cut -d, -f$((SIDECAR_LOCAL_RANK+1))); npu-smi info -t usages -i \${PHYS:-0} 2>/dev/null | head -40 >'${out}/npu_smi_util_inject.txt' || npu-smi info 2>/dev/null | head -80 >'${out}/npu_smi_util_inject.txt'; exit 0" || true
    elif [[ "$INJECT_KIND" == "inline_hbm" || "$INJECT_KIND" == "hbm" || "$INJECT_KIND" == "1b" || "$INJECT_KIND" == "hbm_ramp" ]]; then
      echo "  inline hbm active (victim=${SIDECAR_LOCAL_RANK} mb=${INLINE_HBM_MB} copies=${INLINE_HBM_COPIES} max=${INLINE_HBM_COPIES_MAX:-48} ramp=${INLINE_HBM_RAMP:-0})"
      jexec "grep -E \"INLINE_HBM|SIDECAR\" '${out}/node_0.log' | head -20 >'${out}/injection.log' || true; echo SIDECAR_START kind=inline_hbm_1b mb=${INLINE_HBM_MB} copies=${INLINE_HBM_COPIES} copies_max=${INLINE_HBM_COPIES_MAX:-48} ramp=${INLINE_HBM_RAMP:-0} victim=${SIDECAR_LOCAL_RANK} >>'${out}/injection.log'; exit 0" || true
      jexec "PHYS=\$(echo \${ASCEND_VISIBLE_DEVICES:-0} | cut -d, -f$((SIDECAR_LOCAL_RANK+1))); npu-smi info -t usages -i \${PHYS:-0} 2>/dev/null | head -40 >'${out}/npu_smi_util_inject.txt' || npu-smi info 2>/dev/null | head -80 >'${out}/npu_smi_util_inject.txt'; exit 0" || true
    elif [[ "$INJECT_KIND" == 8a || "$INJECT_KIND" == inline_8a ]]; then
      echo "  inline 8a GC/stall active (victim=${SIDECAR_LOCAL_RANK} every=${INLINE_GC_EVERY:-1} stall=${INLINE_GC_STALL_S:-0.25})"
      jexec "grep -E \"INLINE_8A|INLINE_GC|SIDECAR\" '${out}/node_0.log' | head -20 >'${out}/injection.log' || true; echo SIDECAR_START kind=inline_8a every=${INLINE_GC_EVERY:-1} stall_s=${INLINE_GC_STALL_S:-0.25} victim=${SIDECAR_LOCAL_RANK} >>'${out}/injection.log'; exit 0" || true
    elif [[ "$INJECT_KIND" == 8b || "$INJECT_KIND" == inline_8b ]]; then
      echo "  inline 8b leak/stall active (victim=${SIDECAR_LOCAL_RANK} mb=${INLINE_8B_MB:-16} stall=${INLINE_8B_STALL_S:-0.25})"
      jexec "grep -E \"INLINE_8B|SIDECAR\" '${out}/node_0.log' | head -20 >'${out}/injection.log' || true; echo SIDECAR_START kind=inline_8b mb=${INLINE_8B_MB:-16} stall_s=${INLINE_8B_STALL_S:-0.25} victim=${SIDECAR_LOCAL_RANK} >>'${out}/injection.log'; exit 0" || true
    elif [[ "$INJECT_KIND" == 8c || "$INJECT_KIND" == sidecar_8c ]]; then
      # 监控泄漏：外挂 host sidecar（非 attach 训练 PID）；host_bound 咬 data_ms/step
      echo "  8c sidecar fire (cpu_n=${SIDECAR_8C_CPU_N:-nproc} load=${SIDECAR_8C_CPU_LOAD} mb=${SIDECAR_8C_MB}/${SIDECAR_8C_LEAK_EVERY}s)"
      jexec "rm -f '${out}/injection.log'; cp -f '${POD_BUNDLE}/sidecar_inject_8c.py' /tmp/sidecar_inject_8c.py; export SIDECAR_8C_CPU_LOAD=${SIDECAR_8C_CPU_LOAD} SIDECAR_8C_MB=${SIDECAR_8C_MB} SIDECAR_8C_LEAK_EVERY=${SIDECAR_8C_LEAK_EVERY} SIDECAR_8C_MAX_CHUNKS=${SIDECAR_8C_MAX_CHUNKS}; ${SIDECAR_8C_CPU_N:+export SIDECAR_8C_CPU_N=${SIDECAR_8C_CPU_N};} PYTHONUNBUFFERED=1 nohup ${PYBIN}/python -u /tmp/sidecar_inject_8c.py --case 8c --seconds 1800 >'${out}/injection.log' 2>&1 & echo SC=\$!; exit 0"
      e=0
      while [ "$e" -lt 60 ]; do
        if jexec "grep -q 'SIDECAR_START' '${out}/injection.log'" 2>/dev/null; then
          echo "  8c sidecar START ok (${e}s)"; break
        fi
        if jexec "test -f '${out}/node_0.fail'" 2>/dev/null; then
          echo "  8c START aborted: training fail"; return 1
        fi
        if ! jexec "pgrep -f '[s]idecar_inject_8c' >/dev/null" 2>/dev/null; then
          echo "  8c sidecar died without START"; jexec "tail -n 60 '${out}/injection.log'" || true
          return 1
        fi
        sleep 2; e=$((e + 2))
      done
      if [ "$e" -ge 60 ]; then
        echo "  8c sidecar START timeout"; jexec "tail -n 80 '${out}/injection.log'" || true
        return 1
      fi
    elif [[ "$INJECT_KIND" == 2a || "$INJECT_KIND" == inline_2a ]]; then
      echo "  inline 2a frag active (victim=${SIDECAR_LOCAL_RANK} chunks=${INLINE_2A_CHUNKS:-12} stall_mb=${INLINE_2A_STALL_MB:-768} stall_s=${INLINE_2A_STALL_S:-0.25})"
      jexec "grep -E \"INLINE_2A|SIDECAR\" '${out}/node_0.log' | head -20 >'${out}/injection.log' || true; echo SIDECAR_START kind=inline_2a chunks=${INLINE_2A_CHUNKS:-12} stall_mb=${INLINE_2A_STALL_MB:-768} stall_s=${INLINE_2A_STALL_S:-0.25} victim=${SIDECAR_LOCAL_RANK} >>'${out}/injection.log'; exit 0" || true
      jexec "PHYS=\$(echo \${ASCEND_VISIBLE_DEVICES:-0} | cut -d, -f$((SIDECAR_LOCAL_RANK+1))); npu-smi info -t usages -i \${PHYS:-0} 2>/dev/null | head -40 >'${out}/npu_smi_util_inject.txt' || npu-smi info 2>/dev/null | head -80 >'${out}/npu_smi_util_inject.txt'; exit 0" || true
    elif [[ "$INJECT_KIND" == 2b || "$INJECT_KIND" == inline_2b || "$INJECT_KIND" == rare_shape ]]; then
      echo "  inline 2b rare_shape active (victim=${SIDECAR_LOCAL_RANK} rare_seq=${RARE_SHAPE_SEQ:-1536} every=${RARE_SHAPE_EVERY:-1})"
      jexec "grep -E \"INLINE_RARE_SHAPE|SIDECAR\" '${out}/node_0.log' | head -20 >'${out}/injection.log' || true; echo SIDECAR_START kind=inline_2b rare_seq=${RARE_SHAPE_SEQ:-1536} every=${RARE_SHAPE_EVERY:-1} victim=${SIDECAR_LOCAL_RANK} >>'${out}/injection.log'; exit 0" || true
    elif [[ "$INJECT_KIND" == 2c || "$INJECT_KIND" == inline_2c || "$INJECT_KIND" == compile_spike ]]; then
      echo "  inline 2c compile tip active (victim=${SIDECAR_LOCAL_RANK} n=${INLINE_2C_N:-1024} every=${INLINE_2C_EVERY:-1} fallback_s=${INLINE_2C_FALLBACK_S:-0.6})"
      jexec "grep -E \"INLINE_2C|SIDECAR\" '${out}/node_0.log' | head -40 >'${out}/injection.log' || true; echo SIDECAR_START kind=inline_2c n=${INLINE_2C_N:-1024} every=${INLINE_2C_EVERY:-1} fallback_s=${INLINE_2C_FALLBACK_S:-0.6} victim=${SIDECAR_LOCAL_RANK} >>'${out}/injection.log'; exit 0" || true
    elif [[ "$INJECT_KIND" == stress_cpu ]]; then
      if [[ -n "$CPU_N" ]]; then
        jexec "nohup stress-ng --cpu ${CPU_N} --cpu-load ${CPU_LOAD} --timeout 900s >'${out}/injection.log' 2>&1 & echo SC=\$!; echo SIDECAR_START stress_cpu cpu_n=${CPU_N} cpu_load=${CPU_LOAD} >>'${out}/injection.log'; exit 0"
      else
        jexec "nohup stress-ng --cpu \$(nproc) --cpu-load ${CPU_LOAD} --timeout 900s >'${out}/injection.log' 2>&1 & echo SC=\$!; echo SIDECAR_START stress_cpu cpu_n=nproc cpu_load=${CPU_LOAD} >>'${out}/injection.log'; exit 0"
      fi
      echo "  stress-ng started (host-wide; manifest victim_local_rank=${SIDECAR_LOCAL_RANK})"
    elif [[ "$INJECT_KIND" == stress_io ]]; then
      # 剂量走 FIO_* / HDD_*（quiet/masked 可弱化）；无 fio 时回退 stress-ng；temp-path 钉同盘
      jexec "mkdir -p '${IO_STRESS_DIR}'; : >'${out}/injection.log'; if command -v fio >/dev/null 2>&1; then nohup fio --name=io_stress --rw=randrw --bs=${FIO_BS} --size=${FIO_SIZE} --numjobs=${FIO_NUMJOBS} --iodepth=${FIO_IODEPTH} --time_based --runtime=900 --directory='${IO_STRESS_DIR}' --group_reporting >'${out}/injection.log' 2>&1 & echo SC=\$!; echo SIDECAR_START fio_nj=${FIO_NUMJOBS} iodepth=${FIO_IODEPTH} bs=${FIO_BS} size=${FIO_SIZE} dir=${IO_STRESS_DIR} >>'${out}/injection.log'; else nohup stress-ng --temp-path '${IO_STRESS_DIR}' --hdd ${HDD_N} --hdd-bytes ${HDD_BYTES} --iomix ${IOMIX_N} --iomix-bytes ${HDD_BYTES} --timeout 900s >'${out}/injection.log' 2>&1 & echo SC=\$!; echo SIDECAR_START stress_io hdd_n=${HDD_N} hdd_bytes=${HDD_BYTES} iomix_n=${IOMIX_N} dir=${IO_STRESS_DIR} >>'${out}/injection.log'; fi; exit 0"
      echo "  stress_io started (dir=${IO_STRESS_DIR} fio_nj=${FIO_NUMJOBS} iodepth=${FIO_IODEPTH} size=${FIO_SIZE} hdd_n=${HDD_N} iomix_n=${IOMIX_N})"
      e=0
      while [ "$e" -lt 30 ]; do
        if jexec "grep -q 'SIDECAR_START' '${out}/injection.log'" 2>/dev/null; then
          echo "  stress_io START ok (${e}s)"; break
        fi
        sleep 1; e=$((e + 1))
      done
    elif [[ "$INJECT_KIND" == stress_vm ]]; then
      # 内存带宽争用：vm-keep + page-in；剂量 vm_n × vm_bytes（2Ti 主机 loud≈96×6G）
      jexec ": >'${out}/injection.log'; echo SIDECAR_START stress_vm_n=${VM_N}_bytes=${VM_BYTES} >>'${out}/injection.log'; (test -r /proc/pressure/memory && { echo '---PSI_MEMORY---'; cat /proc/pressure/memory; echo '---PSI_CPU---'; cat /proc/pressure/cpu; } || echo 'PSI_UNAVAIL no_/proc/pressure') >>'${out}/injection.log'; nohup stress-ng --vm ${VM_N} --vm-bytes ${VM_BYTES} --vm-keep --page-in --timeout 900s >>'${out}/injection.log' 2>&1 & echo SC=\$!; exit 0"
      echo "  stress_vm started (vm_n=${VM_N} vm_bytes=${VM_BYTES})"
      e=0
      while [ "$e" -lt 30 ]; do
        if jexec "grep -q 'SIDECAR_START' '${out}/injection.log'" 2>/dev/null; then
          echo "  stress_vm START ok (${e}s)"; break
        fi
        sleep 1; e=$((e + 1))
      done
      # 旁证：注入后采样 PSI（若有）与 free
      jexec "{ echo '---AFTER_INJECT---'; date -Iseconds; free -h | head -3; (test -r /proc/pressure/memory && { cat /proc/pressure/memory; cat /proc/pressure/cpu; } || echo PSI_UNAVAIL); } >>'${out}/injection.log'; exit 0" || true
    elif [[ "$INJECT_KIND" == cube ]]; then
      # 同逻辑 device 共卡：不改 ASCEND_VISIBLE_DEVICES，钉 SIDECAR_DEVICE=local_rank
      jexec "rm -f '${out}/injection.log'; cp -f '${POD_BUNDLE}/sidecar_inject_npu.py' /tmp/sidecar_inject_npu.py; SIDECAR_DEVICE=${SIDECAR_LOCAL_RANK} PYTHONUNBUFFERED=1 nohup ${PYBIN}/python -u /tmp/sidecar_inject_npu.py --kind cube --duty ${DUTY} --size ${SIZE} --warmup-seconds ${SIDECAR_WARMUP} --seconds 1800 --device ${SIDECAR_LOCAL_RANK} >'${out}/injection.log' 2>&1 & echo SC=\$!; exit 0"
      echo "  cube sidecar fired (device=${SIDECAR_LOCAL_RANK} duty=${DUTY} size=${SIZE})"
      # 等 SIDECAR_START，避免空窗
      e=0
      budget=$(( SIDECAR_WARMUP + 45 ))
      while [ "$e" -lt "$budget" ]; do
        if jexec "grep -q 'SIDECAR_START' '${out}/injection.log'" 2>/dev/null; then
          echo "  sidecar START ok (${e}s)"; break
        fi
        if jexec "test -f '${out}/node_0.fail'" 2>/dev/null; then
          echo "  sidecar START aborted: training fail"; return 1
        fi
        if ! jexec "pgrep -f '[s]idecar_inject_npu' >/dev/null" 2>/dev/null; then
          echo "  sidecar died without START"; jexec "tail -n 60 '${out}/injection.log'" || true
          return 1
        fi
        sleep 2; e=$((e + 2))
      done
      if [ "$e" -ge "$budget" ]; then
        echo "  sidecar START timeout"; jexec "tail -n 80 '${out}/injection.log'" || true
        return 1
      fi
      # 旁证：npu-smi util（不升 D）
      jexec "npu-smi info -t usages -i ${SIDECAR_LOCAL_RANK} 2>/dev/null | head -40 >'${out}/npu_smi_util_inject.txt' || npu-smi info 2>/dev/null | head -80 >'${out}/npu_smi_util_inject.txt'; exit 0" || true
    fi

    # Pillar-C SET↑：SHOW TABLES→worker。
    # C-3：PILLAR_C_SET_AT_STEP=N → 等 jsonl 行数 L≥N（禁止等 step_N.marker，训练不写）。
    if [[ "$cfg" == "C2_probing" ]] && [[ "${PILLAR_C_SET_UPGRADE:-0}" == "1" ]]; then
      local set_at_l="${PILLAR_C_SET_AT_STEP:-}"
      if [[ -n "$set_at_l" ]]; then
        echo "  Pillar-C SET↑ wait L>=${set_at_l} (jsonl lines; NOT step marker)…"
        e=0
        while [ "$e" -lt 2400 ]; do
          local cur_l
          cur_l=$(jexec "wc -l <'${out}/ranks/rank_0000.jsonl' 2>/dev/null || echo 0" 2>/dev/null | tr -d '[:space:]')
          cur_l=${cur_l:-0}
          if [[ "$cur_l" =~ ^[0-9]+$ ]] && [ "$cur_l" -ge "$set_at_l" ]; then
            echo "  L=${cur_l} >= ${set_at_l} (${e}s) → attach/SET"
            break
          fi
          if jexec "test -f '${out}/node_0.done' -o -f '${out}/node_0.fail'" 2>/dev/null; then
            echo "  SET aborted: training ended before L>=${set_at_l} (L=${cur_l})"
            break
          fi
          sleep 2; e=$((e + 2))
          if [ $((e % 60)) -eq 0 ]; then echo "  waiting L>=${set_at_l}… t=${e}s L=${cur_l}"; fi
        done
      else
        echo "  Pillar-C SET↑ at inject start…"
      fi
      # ③-A 等：升到的 rate 可配（默认 1.0）；真相键 probing.torch.profiling=
      # PR-2：scope=localize（默认）→ 编排层 SQL 定位 culprit，仅对 culprit SET；
      #   SQL 空/超时 → fallback 全 rank（对照臂）。legacy: victim|all。
      local set_rate="${PILLAR_C_SET_RATE:-1.0}"
      local set_scope="${PILLAR_C_SET_SCOPE:-localize}"
      local set_victim="${SIDECAR_LOCAL_RANK:-7}"
      local localize_py="${LOCALIZE_PY_LOCAL}"
      jsync_file "$localize_py" "${out}/_pillar_c_localize.py"
      echo "  Pillar-C SET upgrade probing.torch.profiling=on,rate=${set_rate} scope=${set_scope} (localize→culprit SET)…"
      # 必须带 /usr/bin:/bin：jexec 非 login 时 PATH 可能空，否则 date/ps/awk 全挂 → SET_FAIL_ALL
      # timeout 包住 probing，避免单 pid 读回卡死整臂
      jexec "export PATH='/usr/bin:/bin:${POD_PYDEPS}/bin:${PYBIN}:\${PATH:-}' PYTHONPATH='${POD_PYDEPS}:\${PYTHONPATH:-}'
: >'${out}/set_upgrade.log'
TS0=\$(date -Iseconds); echo SET_BEGIN ts=\$TS0 trigger=L_ge_${set_at_l:-inject} set_rate=${set_rate} scope=${set_scope} victim=${set_victim} >>'${out}/set_upgrade.log'
L=\$(wc -l <'${out}/ranks/rank_0000.jsonl' 2>/dev/null || echo 0); echo SET_L=\$L >>'${out}/set_upgrade.log'
echo SET_TARGET=probing.torch.profiling=on,rate=${set_rate} >>'${out}/set_upgrade.log'
T_MARK=\$(python3 -c 'import time;print(int(time.time()*1000))')
echo SET_T0_MS=\$T_MARK >>'${out}/set_upgrade.log'
ATTACH_WAIT_S=\"\${PILLAR_C_ATTACH_READY_WAIT_S:-45}\"
ATTACH_RETRY_S=\"\${PILLAR_C_ATTACH_RETRY_INTERVAL_S:-2}\"
PROBE_TIMEOUT_S=\"\${PILLAR_C_LOCALIZE_TIMEOUT_S:-8}\"
ATTACH_RETRIES=\"\${PILLAR_C_ATTACH_RETRIES:-3}\"
SET_BLOCK_TIMEOUT_S=\"\${PILLAR_C_SET_BLOCK_TIMEOUT_S:-120}\"
echo ATTACH_CFG wait_s=\$ATTACH_WAIT_S retry_s=\$ATTACH_RETRY_S probe_timeout_s=\$PROBE_TIMEOUT_S attach_retries=\$ATTACH_RETRIES set_block_timeout_s=\$SET_BLOCK_TIMEOUT_S >>'${out}/set_upgrade.log'
# SET/localize 前：等至少 victim rank 或半数 worker probing attach 就绪（8a stall 瞬时不可 attach）
_attach_ready=0
_aw=0
while [ \"\$_aw\" -lt \"\$ATTACH_WAIT_S\" ]; do
  _victim_pid=
  _ok_n=0
  _seen_lr=
  for pid in \$(ps -eo pid,args | awk '/\\/tmp\\/tbp_npu\\.py|train_bench_probe_npu/ && \$0 !~ /awk|bash|torchrun/ {print \$1}'); do
    if ! test -d \"/proc/\$pid\"; then continue; fi
    lr=\$(tr '\\0' '\\n' < /proc/\$pid/environ 2>/dev/null | awk -F= '\$1==\"LOCAL_RANK\"{print \$2; exit}')
  if [[ \"\$lr\" == '${set_victim}' ]]; then _victim_pid=\$pid; fi
    if timeout \"\$PROBE_TIMEOUT_S\" probing -t \"\$pid\" query 'SHOW TABLES' >/tmp/probe_pre_\$pid.txt 2>&1; then
      _ok_n=\$((_ok_n+1))
      _seen_lr=\"\$_seen_lr \$lr\"
    fi
  done
  if [[ -n \"\$_victim_pid\" ]] && timeout \"\$PROBE_TIMEOUT_S\" probing -t \"\$_victim_pid\" query 'SHOW TABLES' >/tmp/probe_pre_victim.txt 2>&1; then
    echo ATTACH_READY victim_pid=\$_victim_pid t=\${_aw}s ok_n=\$_ok_n >>'${out}/set_upgrade.log'
    _attach_ready=1
    break
  fi
  if [ \"\$_ok_n\" -ge 8 ]; then
    echo ATTACH_READY majority ok_n=\$_ok_n t=\${_aw}s lr=\$_seen_lr >>'${out}/set_upgrade.log'
    _attach_ready=1
    break
  fi
  sleep \"\$ATTACH_RETRY_S\"
  _aw=\$((_aw + ATTACH_RETRY_S))
done
if [[ \"\$_attach_ready\" != '1' ]]; then
  echo ATTACH_READY_TIMEOUT t=\${_aw}s ok_n=\$_ok_n victim_pid=\${_victim_pid:-none} >>'${out}/set_upgrade.log'
else
  echo PILLAR_C_ATTACH_PREVALIDATED=1 >>'${out}/set_upgrade.log'
fi
cands=
LOCALIZE_FALLBACK=0
CULPRIT_RANK=
CULPRIT_PID=
if [[ '${set_scope}' == 'localize' ]]; then
  export OUT='${out}' CASE_ID='${CASE_ID}' TRIGGER_STEP=\"\$L\" SIDECAR_LOCAL_RANK='${set_victim}'
  export PILLAR_C_LOCALIZE_MODE=\"\${PILLAR_C_LOCALIZE_MODE:-auto}\"
  export PILLAR_C_LOCALIZE_WINDOW=\"\${PILLAR_C_LOCALIZE_WINDOW:-20}\"
  export PILLAR_C_LOCALIZE_TIMEOUT_S=\"\${PILLAR_C_LOCALIZE_TIMEOUT_S:-8}\"
  export PILLAR_C_LOCALIZE_RETRIES=\"\${PILLAR_C_LOCALIZE_RETRIES:-\$([[ \"\$_attach_ready\" == '1' ]] && echo 1 || echo 2)}\"
  export PILLAR_C_LOCALIZE_RETRY_PAUSE_S=\"\${PILLAR_C_LOCALIZE_RETRY_PAUSE_S:-2}\"
  export PILLAR_C_LOCALIZE_TOTAL_BUDGET_S=\"\${PILLAR_C_LOCALIZE_TOTAL_BUDGET_S:-\$([[ \"\$_attach_ready\" == '1' ]] && echo 60 || echo 90)}\"
  export PILLAR_C_LOCALIZE_PARALLEL=\"\${PILLAR_C_LOCALIZE_PARALLEL:-16}\"
  export PILLAR_C_ATTACH_PREVALIDATED=\"\${PILLAR_C_ATTACH_PREVALIDATED:-\$([[ \"\$_attach_ready\" == '1' ]] && echo 1 || echo 0)}\"
  export PILLAR_C_LOCALIZE_ATTACH_WAIT_S=\"\${PILLAR_C_LOCALIZE_ATTACH_WAIT_S:-4}\"
  export PILLAR_C_LOCALIZE_SECONDARY=\"\${PILLAR_C_LOCALIZE_SECONDARY:-1}\"
  loc_out=\$(timeout \"\$SET_BLOCK_TIMEOUT_S\" python3 '${out}/_pillar_c_localize.py' 2>>'${out}/set_upgrade.log' || echo 'LOCALIZE_TIMEOUT')
  echo \"\$loc_out\" >>'${out}/set_upgrade.log'
  CULPRIT_RANK=\$(echo \"\$loc_out\" | awk -F= '/^CULPRIT_RANK=/{print \$2; exit}')
  CULPRIT_PID=\$(echo \"\$loc_out\" | awk -F= '/^CULPRIT_PID=/{print \$2; exit}')
  LOCALIZE_FALLBACK=\$(echo \"\$loc_out\" | awk -F= '/^LOCALIZE_FALLBACK=/{print \$2; exit}')
  LOCALIZE_FALLBACK=\${LOCALIZE_FALLBACK:-1}
  echo LOCALIZE_FALLBACK=\$LOCALIZE_FALLBACK culprit_rank=\$CULPRIT_RANK culprit_pid=\$CULPRIT_PID >>'${out}/set_upgrade.log'
  if [[ \"\$LOCALIZE_FALLBACK\" == '0' && -n \"\$CULPRIT_PID\" ]]; then
    cands=\" \$CULPRIT_PID\"
    echo CANDS_LOCALIZE=\$cands >>'${out}/set_upgrade.log'
  else
    echo LOCALIZE_FALLBACK_ALL_RANKS >>'${out}/set_upgrade.log'
    cands=\$(SIDECAR_LOCAL_RANK='${set_victim}' python3 '${out}/_pillar_c_localize.py' --list-worker-pids 2>/dev/null | tr '\\n' ' ')
    echo CANDS_FALLBACK=\$cands >>'${out}/set_upgrade.log'
  fi
elif [[ '${set_scope}' == 'victim' ]]; then
  cands=\$(SIDECAR_LOCAL_RANK='${set_victim}' python3 '${out}/_pillar_c_localize.py' --list-worker-pids --local-rank=${set_victim} 2>/dev/null | tr '\\n' ' ')
  echo CANDS_VICTIM=\$cands >>'${out}/set_upgrade.log'
else
  cands=\$(python3 '${out}/_pillar_c_localize.py' --list-worker-pids 2>/dev/null | tr '\\n' ' ')
  echo CANDS_ALL=\$cands >>'${out}/set_upgrade.log'
fi
# PR-2 B6: snapshot main worker pids to worker_pids.txt so ``pull_results``
# can prune non-worker pid dirs before tar-pull.
python3 '${out}/_pillar_c_localize.py' --list-worker-pids 2>/dev/null | awk 'NF' >'${out}/worker_pids.txt' || true
echo WORKER_PIDS_SNAPSHOT count=\$(wc -l <'${out}/worker_pids.txt' 2>/dev/null | tr -d ' ') >>'${out}/set_upgrade.log'
OK=
for pid in \$cands; do
  _attached=0
  _ar=0
  while [ \"\$_ar\" -lt \"\$ATTACH_RETRIES\" ]; do
    if ! test -d \"/proc/\$pid\"; then
      echo ATTACH_FAIL pid=\$pid reason=pid_churned >>'${out}/set_upgrade.log'
      break
    fi
    if timeout \"\$PROBE_TIMEOUT_S\" probing -t \$pid query 'SHOW TABLES' >/tmp/probe_ping_\$pid.txt 2>&1; then
      _attached=1
      break
    fi
    _ar=\$((_ar+1))
    sleep \"\$ATTACH_RETRY_S\"
  done
  if [[ \"\$_attached\" == '1' ]]; then
    echo ATTACH_OK pid=\$pid retries=\$_ar >>'${out}/set_upgrade.log'
    T_ATT=\$(python3 -c 'import time;print(int(time.time()*1000))')
    echo ATTACH_T_MS=\$T_ATT >>'${out}/set_upgrade.log'
    # C0 真相键：probing.torch.profiling（勿写 torch.profiling=；后者不触发 live sync）
    if ! timeout \"\$PROBE_TIMEOUT_S\" probing -t \$pid config 'probing.torch.profiling=on,rate=${set_rate}' >>'${out}/set_upgrade.log' 2>&1; then
      echo SET_CMD_FAIL pid=\$pid >>'${out}/set_upgrade.log'
      continue
    fi
    # 读回校验（短超时）；失败仍记 SET_OK（config 已成功）
    if ! timeout 8 probing -t \$pid query \"SELECT value FROM information_schema.df_settings WHERE name='probing.torch.profiling'\" >/tmp/probe_cfg_\$pid.txt 2>&1; then
      timeout 8 probing -t \$pid eval \"import probing; print(getattr(probing,'get_config',lambda k:None)('probing.torch.profiling'))\" >/tmp/probe_cfg_\$pid.txt 2>&1 || true
    fi
    cat /tmp/probe_cfg_\$pid.txt >>'${out}/set_upgrade.log' 2>/dev/null || true
    if ! grep -qE \"on,rate=${set_rate}|rate=${set_rate}\" /tmp/probe_cfg_\$pid.txt 2>/dev/null; then
      echo SET_READBACK_UNVERIFIED pid=\$pid >>'${out}/set_upgrade.log'
    fi
    T_SET=\$(python3 -c 'import time;print(int(time.time()*1000))')
    echo SET_T1_MS=\$T_SET >>'${out}/set_upgrade.log'
    echo SET_OK_WORKER pid=\$pid >>'${out}/set_upgrade.log'
    echo SET_UPGRADE ts=\$(date -Iseconds) step=\$L pid=\$pid rate=${set_rate} >>'${out}/set_upgrade.log'
    python3 -c \"t0=int('\$T_MARK'); t1=int('\$T_SET'); print(f'SET_LATENCY_MS={t1-t0}')\" >>'${out}/set_upgrade.log' 2>/dev/null || echo SET_LATENCY_MS=? >>'${out}/set_upgrade.log'
    OK=\$pid
    # localize/victim：通常 1 pid；fallback/all：多 rank 均 SET（对照臂）
  else
    echo ATTACH_FAIL pid=\$pid retries=\$_ar >>'${out}/set_upgrade.log'
  fi
done
if [[ -z \"\$OK\" ]]; then echo SET_FAIL_ALL >>'${out}/set_upgrade.log'; fi
echo SET_END ts=\$(date -Iseconds) >>'${out}/set_upgrade.log'
exit 0" || true
      # B3：时基优先升详窗（可兼步数）；先到者触发 SET_DOWNGRADE rate=0（同 culprit pid）
      local set_win_s="${PILLAR_C_SET_WINDOW_S:-45}"
      local set_win_steps="${PILLAR_C_SET_WINDOW_STEPS:-0}"
      if { [[ "${set_win_s}" =~ ^[0-9]+$ ]] && [[ "${set_win_s}" -gt 0 ]]; } \
         || { [[ "${set_win_steps}" =~ ^[0-9]+$ ]] && [[ "${set_win_steps}" -gt 0 ]]; }; then
        local hang_max_s="${PILLAR_C_SET_HANG_MAX_S:-900}"
        local set_l_val set_pid_val culprit_rank_val set_pid_source
        local local_set_log="${LOCAL_RESULT_ROOT}/_work/set_upgrade_snapshot.log"
        mkdir -p "$(dirname "$local_set_log")"
        jexec "cat '${out}/set_upgrade.log' 2>/dev/null" >"$local_set_log" 2>/dev/null || true
        set_l_val=$(read_set_upgrade_field "${out}/set_upgrade.log" set_l "$local_set_log" || echo 0)
        set_pid_val=$(read_set_upgrade_field "${out}/set_upgrade.log" set_ok_pid "$local_set_log" || true)
        set_pid_source=set_ok_worker
        if [[ -z "${set_pid_val}" ]]; then
          set_pid_val=$(read_set_upgrade_field "${out}/set_upgrade.log" culprit_pid "$local_set_log" || true)
          [[ -n "${set_pid_val}" ]] && set_pid_source=culprit_pid
        fi
        culprit_rank_val=$(read_set_upgrade_field "${out}/set_upgrade.log" culprit_rank "$local_set_log" || true)
        culprit_rank_val=${culprit_rank_val:-${set_victim}}
        set_l_val=${set_l_val:-0}
        if [[ -z "${set_pid_val}" ]]; then
          echo "  B3 WARN: SET_OK pid empty after retries → FALLBACK victim rank ${set_victim} (still time-downgrade)"
          jexec "echo B3_PID_FALLBACK victim_rank=${set_victim} reason=set_ok_empty >>'${out}/set_upgrade.log'; exit 0" 2>/dev/null || true
          set_pid_val=$(jexec "SIDECAR_LOCAL_RANK='${set_victim}' python3 '${out}/_pillar_c_localize.py' --list-worker-pids --local-rank=${set_victim} 2>/dev/null | head -1" 2>/dev/null | tr -d '[:space:]')
          set_pid_source=victim_fallback
        fi
        if [[ -z "${set_pid_val}" ]] || ! [[ "${set_pid_val}" =~ ^[0-9]+$ ]]; then
          echo "  B3 FATAL: no pid for downgrade even after victim fallback"
          jexec "echo B3_DOWNGRADE_NO_PID ts=\$(date -Iseconds) >>'${out}/set_upgrade.log'; exit 0" 2>/dev/null || true
        else
        local downgrade_at=0
        if [[ "${set_win_steps}" =~ ^[0-9]+$ ]] && [[ "${set_win_steps}" -gt 0 ]]; then
          downgrade_at=$((set_l_val + set_win_steps))
        fi
        local rank_jsonl
        rank_jsonl=$(printf 'rank_%04d.jsonl' "${culprit_rank_val}")
          echo "  Pillar-C B3: pid=${set_pid_val} source=${set_pid_source}; window_s=${set_win_s} window_steps=${set_win_steps}; time-or-steps → SET_DOWNGRADE (hang_max=${hang_max_s}s)…"
          local upgrade_ts
          upgrade_ts=$(date +%s)
          e=0
          local last_l=-1 stall_acc=0 downgrade_done=0 hang_detected=0 downgrade_reason=""
          while [ "$e" -lt 3600 ]; do
            local cur_l elapsed_s
            cur_l=$(jexec_poll "wc -l <'${out}/ranks/${rank_jsonl}' 2>/dev/null || wc -l <'${out}/ranks/rank_0000.jsonl' 2>/dev/null || echo 0")
            cur_l=$(echo "${cur_l:-0}" | tr -d '[:space:]')
            elapsed_s=$(( $(date +%s) - upgrade_ts ))
            if jexec_poll "test -f '${out}/node_0.done' -o -f '${out}/node_0.fail'" 15; then
              echo "  B3 window end: training exited (L=${cur_l} elapsed=${elapsed_s}s)"
              break
            fi
            if [[ "${set_win_s}" =~ ^[0-9]+$ ]] && [[ "${set_win_s}" -gt 0 ]] && [ "$elapsed_s" -ge "$set_win_s" ]; then
              echo "  elapsed=${elapsed_s}s >= window_s=${set_win_s} (L=${cur_l}) → SET_DOWNGRADE reason=time"
              downgrade_done=1
              downgrade_reason=time
              break
            fi
            if [[ "${set_win_steps}" =~ ^[0-9]+$ ]] && [[ "${set_win_steps}" -gt 0 ]] \
               && [[ "$cur_l" =~ ^[0-9]+$ ]] && [ "$cur_l" -ge "$downgrade_at" ]; then
              echo "  L=${cur_l} >= ${downgrade_at} (${e}s) → SET_DOWNGRADE reason=steps"
              downgrade_done=1
              downgrade_reason=steps
              break
            fi
            if [[ "$cur_l" == "$last_l" ]]; then
              stall_acc=$((stall_acc + 5))
            else
              stall_acc=0
              last_l="$cur_l"
            fi
            # 降回后若仍 stall → HANG_DETECTED（在降回执行后再判；此处仅升详窗内 stall）
            if [ "$stall_acc" -ge "$hang_max_s" ]; then
              echo "  HANG_DETECTED: jsonl L stalled at ${cur_l} for ${stall_acc}s (>=${hang_max_s}s)"
              hang_detected=1
              jexec_poll "echo HANG_DETECTED ts=\$(date -Iseconds) step=${cur_l} stall_s=${stall_acc} >>'${out}/set_upgrade.log'; exit 0" 15
              break
            fi
            sleep 5
            e=$((e + 5))
            if [ $((e % 60)) -eq 0 ]; then
              echo "  B3 wait… t=${e}s elapsed=${elapsed_s}s L=${cur_l} stall=${stall_acc}s downgrade_at=${downgrade_at:-n/a}"
            fi
          done
          if [[ "$downgrade_done" == "1" ]]; then
            jexec "export PATH='/usr/bin:/bin:${POD_PYDEPS}/bin:${PYBIN}:\${PATH:-}' PYTHONPATH='${POD_PYDEPS}:\${PYTHONPATH:-}'
PROBE_TIMEOUT_S=\"\${PILLAR_C_LOCALIZE_TIMEOUT_S:-8}\"
PID='${set_pid_val}'
DL=\$(wc -l <'${out}/ranks/${rank_jsonl}' 2>/dev/null || wc -l <'${out}/ranks/rank_0000.jsonl' 2>/dev/null || echo 0)
ELAPSED=\$(( \$(date +%s) - ${upgrade_ts} ))
TS_DG=\$(date -Iseconds)
echo SET_DOWNGRADE ts=\$TS_DG step=\$DL pid=\$PID rate=0 reason=${downgrade_reason} window_s=${set_win_s} window_steps=${set_win_steps} elapsed_s=\$ELAPSED upgrade_step=${set_l_val} >>'${out}/set_upgrade.log'
if test -d /proc/\$PID; then
  if timeout \"\$PROBE_TIMEOUT_S\" probing -t \$PID config 'probing.torch.profiling=on,rate=0' >>'${out}/set_upgrade.log' 2>&1; then
    echo SET_DOWNGRADE_OK pid=\$PID reason=${downgrade_reason} >>'${out}/set_upgrade.log'
  else
    echo SET_DOWNGRADE_FAIL pid=\$PID reason=${downgrade_reason} >>'${out}/set_upgrade.log'
  fi
else
  echo SET_DOWNGRADE_SKIP pid_churned pid=\$PID reason=${downgrade_reason} >>'${out}/set_upgrade.log'
fi
exit 0" || true
            # 降回后：若 step 仍 stall → HANG_DETECTED 停训
            local post_last_l=-1 post_stall=0 post_e=0
            while [ "$post_e" -lt "$hang_max_s" ]; do
              local post_l
              post_l=$(jexec_poll "wc -l <'${out}/ranks/${rank_jsonl}' 2>/dev/null || wc -l <'${out}/ranks/rank_0000.jsonl' 2>/dev/null || echo 0")
              post_l=$(echo "${post_l:-0}" | tr -d '[:space:]')
              if jexec_poll "test -f '${out}/node_0.done' -o -f '${out}/node_0.fail'" 15; then
                break
              fi
              if [[ "$post_l" == "$post_last_l" ]]; then
                post_stall=$((post_stall + 5))
              else
                post_stall=0
                post_last_l="$post_l"
              fi
              if [ "$post_stall" -ge "$hang_max_s" ]; then
                echo "  HANG_DETECTED post-downgrade: L stalled at ${post_l} for ${post_stall}s"
                hang_detected=1
                jexec_poll "echo HANG_DETECTED ts=\$(date -Iseconds) step=${post_l} stall_s=${post_stall} phase=post_downgrade >>'${out}/set_upgrade.log'; exit 0" 15
                break
              fi
              sleep 5
              post_e=$((post_e + 5))
            done
          fi
          if [[ "$hang_detected" == "1" ]]; then
            echo "  B3 BLOCKED: stall — stop training, preserve evidence"
            jexec_poll "pkill -TERM -f '[t]bp_npu' 2>/dev/null || true; pkill -TERM -f '[t]orchrun' 2>/dev/null || true; sleep 3; pkill -9 -f '[t]bp_npu' 2>/dev/null || true; pkill -9 -f '[t]orchrun' 2>/dev/null || true; echo B3_HANG_STOP ts=\$(date -Iseconds) >>'${out}/set_upgrade.log'; exit 0" 60
          fi
        fi
      fi
      # ③-B：SET 后在线轮询 TT
      if [[ "${PILLAR_C_LATENCY_PROBE:-0}" == "1" ]]; then
        local w_star="${PILLAR_C_W_STAR:-100}"
        local tt_floor="${PILLAR_C_TT_FLOOR:-800}"
        local probe_max_s="${PILLAR_C_LATENCY_PROBE_MAX_S:-600}"
        local probe_py="${ROOT}/scripts/fail-slow/param_calib/set_latency_probe.py"
        echo "  Pillar-C latency probe (W*=${w_star} TT_floor=${tt_floor} max=${probe_max_s}s)…"
        if [[ -f "$probe_py" ]]; then
          jsync_file "$probe_py" "${out}/_latency_probe.py"
          jexec "export OUT='${out}' PATH='/usr/bin:/bin:${POD_PYDEPS}/bin:${PYBIN}:\${PATH:-}' PYTHONPATH='${POD_PYDEPS}:\${PYTHONPATH:-}'
SET_L=\$(awk -F= '/^SET_L=/{print \$2; exit}' '${out}/set_upgrade.log' 2>/dev/null || echo '')
PID=\$(awk '/^SET_OK_WORKER pid=/{print \$2; exit}' '${out}/set_upgrade.log' 2>/dev/null | sed 's/pid=//')
T0=\$(awk -F= '/^SET_T0_MS=/{print \$2; exit}' '${out}/set_upgrade.log' 2>/dev/null || echo 0)
if [[ -z \"\$PID\" ]]; then echo PROBE_SKIP no_SET_OK_pid >'${out}/set_latency_probe.log'; exit 0; fi
export SET_L PID T0_MS=\"\$T0\" W_STAR='${w_star}' TT_FLOOR='${tt_floor}' PROBE_MAX_S='${probe_max_s}'
python3 '${out}/_latency_probe.py'; exit 0" || true
        else
          echo "  WARN: missing $probe_py — skip latency probe"
          jexec "echo PROBE_SKIP missing_script >'${out}/set_latency_probe.log'; exit 0" || true
        fi
      fi
      if [[ -n "${PROBING_DATA_DIR:-}" ]]; then
        jexec "DATA='${PROBING_DATA_DIR}'; OUTF='${out}/volume_at_upgrade.txt'; python3 -c \"
import os,struct
root='\$DATA'; hots=segs=0; hot_b=cold_b=ro=0
print('DATA_ROOT', root)
if os.path.isdir(root):
  for dp,_,fns in os.walk(root):
    for fn in fns:
      p=os.path.join(dp,fn)
      try: sz=os.path.getsize(p)
      except OSError: continue
      if fn.endswith('.memc'):
        segs+=1; cold_b+=sz
      else:
        try:
          hdr=open(p,'rb').read(64)
          if len(hdr)>=64 and hdr[:4]==b'MEMT':
            _,r=struct.unpack_from('<II',hdr,56); hots+=1; hot_b+=sz; ro+=r
        except Exception: pass
print(f'hot_memt={hots} hot_bytes={hot_b} cold_segs={segs} cold_bytes={cold_b} rows_overwritten_sum={ro}')
\" >\"\$OUTF\" 2>&1; exit 0" || true
      fi
    fi

    # C2：注入窗内 dump Probing SQL + host_psi（对齐共享 pipeline）
    if [[ "$cfg" == "C2_probing" ]] && [[ "${DUMP_PROBING_SQL}" == "1" ]]; then
      echo "  waiting ${DUMP_WAIT_S}s into inject window for SQL dump…"
      sleep "${DUMP_WAIT_S}"
      if jexec "pgrep -f 'tbp_npu|train_bench_probe_npu' >/dev/null" 2>/dev/null; then
        echo "  dumping Probing SQL / host_psi…"
        jexec "export OUT_DIR='${out}' CASE='${CASE_ID}' CODE_DIR='${POD_BUNDLE}' VICTIM_LOCAL_RANK='${SIDECAR_LOCAL_RANK}' PYTHONPATH='${POD_PYDEPS}:\${PYTHONPATH:-}' PATH='/usr/bin:/bin:${POD_PYDEPS}/bin:${PYBIN}:\${PATH:-}'; /bin/bash '${POD_BUNDLE}/dump_probing_sql.sh' >'${out}/probing_dump.log' 2>&1; exit 0" || true
        echo "  SQL dump attempted → ${out}/probing/"
      else
        echo "  SQL dump skipped: training not running"
      fi
    fi

    e=0
    while [ "$e" -lt 2400 ]; do
      if jexec "test -f '${out}/ranks/step_${INJECT_STOP}.marker'" 2>/dev/null; then
        echo "  measure step ${INJECT_STOP} → stop injectors"
        jexec "pkill -TERM -f '[s]idecar_inject_npu' 2>/dev/null || true; pkill -TERM -f '[s]idecar_inject_8c' 2>/dev/null || true; pkill -TERM stress-ng 2>/dev/null || true; sleep 1; pkill -9 -f '[s]idecar_inject_npu' 2>/dev/null || true; pkill -9 -f '[s]idecar_inject_8c' 2>/dev/null || true; pkill -9 stress-ng 2>/dev/null || true; echo SIDECAR_STOP >>'${out}/injection.log'; exit 0" || true
        break
      fi
      if jexec "test -f '${out}/node_0.done' -o -f '${out}/node_0.fail'" 2>/dev/null; then
        break
      fi
      sleep 5; e=$((e + 5))
    done
  fi

  e=0
  # B8：driver 兜底 — 若 PILLAR_C_NO_PROGRESS_KILL_S 秒内所有 rank_*.jsonl 的 mtime + 行数都不变，
  # append NO_JSONL_PROGRESS_<S>S 到 node_0.log + kill torchrun + touch node_0.done。
  # 0 或 negative 关闭；默认 90 秒。
  local no_progress_kill_s="${PILLAR_C_NO_PROGRESS_KILL_S:-90}"
  local last_progress_sig=""
  local no_progress_stall=0
  local kill_triggered=0
  while [ "$e" -lt 3600 ]; do
    if jexec "test -f '${out}/node_0.done'" 2>/dev/null; then
      echo "  done (${e}s)"
      jexec "pkill -9 -f '[s]idecar_inject_npu' 2>/dev/null || true; pkill -9 -f '[s]idecar_inject_8c' 2>/dev/null || true; pkill -9 stress-ng 2>/dev/null || true; exit 0" || true
      return 0
    fi
    # pilot 宽松：测量窗 step_300 已齐且训练已退出 → 视为可验收（防他方 pkill 误杀）
    if jexec "test -f '${out}/ranks/step_${INJECT_STOP}.marker'" 2>/dev/null; then
      if ! jexec "pgrep -f '[t]bp_npu|[t]orchrun' >/dev/null" 2>/dev/null; then
        echo "  measure window complete + training gone → accept partial (step_${INJECT_STOP})"
        jexec "pkill -9 -f '[s]idecar_inject_npu' 2>/dev/null || true; pkill -9 -f '[s]idecar_inject_8c' 2>/dev/null || true; pkill -9 stress-ng 2>/dev/null || true; touch '${out}/node_0.done'; echo PARTIAL_DONE >>'${out}/node_0.log'; exit 0" || true
        return 0
      fi
    fi
    if jexec "test -f '${out}/node_0.fail'" 2>/dev/null; then
      # 若已有 step_300，fail 也升格 partial-ok
      if jexec "test -f '${out}/ranks/step_${INJECT_STOP}.marker'" 2>/dev/null; then
        echo "  FAIL after measure window → partial-ok"
        jexec "pkill -9 -f '[s]idecar_inject_npu' 2>/dev/null || true; pkill -9 -f '[s]idecar_inject_8c' 2>/dev/null || true; pkill -9 stress-ng 2>/dev/null || true; rm -f '${out}/node_0.fail'; touch '${out}/node_0.done'; exit 0" || true
        return 0
      fi
      echo "  FAIL marker"; jexec "tail -n 150 '${out}/node_0.log'" || true
      jexec "pkill -9 -f '[s]idecar_inject_npu' 2>/dev/null || true; pkill -9 -f '[s]idecar_inject_8c' 2>/dev/null || true; pkill -9 stress-ng 2>/dev/null || true; exit 0" || true
      return 1
    fi

    # B8：no-jsonl-progress 兜底 kill
    if [[ "${no_progress_kill_s}" -gt 0 ]] && [[ "${kill_triggered}" -eq 0 ]]; then
      local sig
      sig=$(jexec "cd '${out}/ranks' 2>/dev/null && for f in rank_*.jsonl; do [ -f \"\$f\" ] || continue; s=\$(stat -c '%Y %s' \"\$f\" 2>/dev/null); l=\$(wc -l <\"\$f\" 2>/dev/null); echo \"\$f \$s \$l\"; done | sort" 2>/dev/null | tr -d '\r')
      if [[ -n "${sig}" ]]; then
        if [[ "${sig}" == "${last_progress_sig}" ]]; then
          no_progress_stall=$((no_progress_stall + 10))
          if [[ ${no_progress_stall} -ge ${no_progress_kill_s} ]]; then
            echo "  NO_JSONL_PROGRESS_${no_progress_kill_s}S → kill torchrun (driver bailout)"
            jexec "echo NO_JSONL_PROGRESS_${no_progress_kill_s}S ts=\$(date -Iseconds) >>'${out}/node_0.log'; pkill -9 -f 'torchrun' 2>/dev/null || true; pkill -9 -f '[t]bp_npu|[t]rain_bench_probe' 2>/dev/null || true; pkill -9 -f '[s]idecar_inject_npu' 2>/dev/null || true; pkill -9 -f '[s]idecar_inject_8c' 2>/dev/null || true; pkill -9 stress-ng 2>/dev/null || true; touch '${out}/node_0.done'; exit 0" || true
            kill_triggered=1
          fi
        else
          no_progress_stall=0
          last_progress_sig="${sig}"
        fi
      fi
    fi

    sleep 10; e=$((e + 10))
    if [ $((e % 60)) -eq 0 ]; then
      local njson
      njson=$(jexec "ls '${out}/ranks'/rank_*.jsonl 2>/dev/null | wc -l" 2>/dev/null | tr -d '[:space:]' || echo 0)
      echo "  waiting done… t=${e}s jsonl=${njson:-0} no_progress_stall=${no_progress_stall}s"
    fi
  done
  echo "  TIMEOUT"; jexec "tail -n 150 '${out}/node_0.log'" || true
  return 1
}

pull_results() {
  # PR-2 B6: prune non-worker pid subdirs from probing_data/ before tar-pull.
  # Cuts ``extra_pid`` (18 short-lived pids in B5d) + ``main_empty`` overhead
  # when combined with Python-side lazy rings.  Manifest built during fire loop.
  if [[ -n "${PROBING_DATA_DIR:-}" ]] && [[ "${PILLAR_C_PRUNE_EXTRA_PIDS:-1}" == "1" ]]; then
    local prune_py="${ROOT}/scripts/fail-slow/prune_extra_pids.py"
    local prune_out="${LOCAL_RESULT_ROOT}/_work/prune_extra_pids.log"
    mkdir -p "$(dirname "$prune_out")"
    if [[ -f "$prune_py" ]]; then
      # push script to pod (idempotent) then run it there against POD_OUT.
      jsync_file "$prune_py" "${POD_BUNDLE}/prune_extra_pids.py" 2>>"$prune_out" || true
      local pod_manifest="${POD_OUT}/worker_pids.txt"
      local pod_culprit_env=""
      # Feed culprit pids from set_upgrade.log (SET_OK_WORKER pid=<n>).
      pod_culprit_env="CULPRIT_PIDS=\$(awk '/^SET_OK_WORKER pid=/{print \$2}' '${POD_OUT}/set_upgrade.log' 2>/dev/null | sed 's/pid=//' | tr '\n' ',')"
      echo "[hold-exec] prune extra_pid dumps under ${POD_OUT}/probing_data" >>"$prune_out"
      jexec "${pod_culprit_env}; export PROBING_DATA_DIR='${POD_OUT}/probing_data' WORKER_PIDS_FILE='${pod_manifest}' PRUNE_DRY_RUN='${PILLAR_C_PRUNE_DRY_RUN:-0}' PATH='/usr/bin:/bin:${POD_PYDEPS}/bin:${PYBIN}:\${PATH:-}' PYTHONPATH='${POD_PYDEPS}:\${PYTHONPATH:-}'; python3 '${POD_BUNDLE}/prune_extra_pids.py' 2>&1 || true" \
        >>"$prune_out" 2>&1 || true
    fi
  fi
  echo "[hold-exec] pull ${POD_OUT} → ${LOCAL_RESULT_ROOT}"
  if [[ "${JUMP_HOST}" == "localhost" || "${JUMP_HOST}" == "127.0.0.1" ]]; then
    export KUBECONFIG="${JUMP_KUBECONFIG:-${KUBECONFIG}}"
    K="${JUMP_KUBECTL:-kubectl}"
    "$K" -n "${NS}" exec "${POD}" -- bash -lc "cd '${POD_OUT}' && tar -cf - ." >"${LOCAL_RESULT_ROOT}/.pull.tar"
  else
    ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=60 "${JUMP_HOST}" \
      "export KUBECONFIG='${JUMP_KUBECONFIG}'; K='${JUMP_KUBECTL}'; \$K -n '${NS}' exec '${POD}' -- bash -lc $(printf '%q' "cd '${POD_OUT}' && tar -cf - .")" \
      >"${LOCAL_RESULT_ROOT}/.pull.tar"
  fi
  tar -C "$LOCAL_RESULT_ROOT" -xf "${LOCAL_RESULT_ROOT}/.pull.tar"
  rm -f "${LOCAL_RESULT_ROOT}/.pull.tar"
  find "$LOCAL_RESULT_ROOT" -name 'rank_*.jsonl' | wc -l | awk '{print "[hold-exec] jsonl_files="$1}'
}

idle=$(jexec "pgrep -af 'torchrun|megatron|tbp_npu' 2>/dev/null | grep -v defunct | grep -v 'bash -lc' | head -5" 2>/dev/null || true)
if [[ -n "${idle}" ]]; then
  echo "[hold-exec] BUSY on ${POD}:"
  echo "$idle"
  if echo "$idle" | grep -qE 'yjr-as|tbp_npu|train_bench_probe_npu'; then
    echo "[hold-exec] clearing our residue…"
    clean_pod
  else
    echo "[hold-exec] foreign process — STOP"
    cat >"$LOCAL_RESULT_ROOT/BLOCKED.md" <<MD
# BLOCKED · ${CASE_ID}

## 现象
- ${POD} BUSY，且不像我们的残留：
\`\`\`
${idle}
\`\`\`

## 已试
- IDLE 复查后发现占用；未清他人进程

## 需要 Loop 做什么
- 确认占用方；空闲后重派
MD
    exit 3
  fi
else
  echo "[hold-exec] ${POD} IDLE"
fi

rc=0
IFS=',' read -r -a CFGS <<< "$ABC_CONFIGS"
for cfg in "${CFGS[@]}"; do
  cfg=$(echo "$cfg" | tr -d ' ')
  case "$cfg" in
    C0_baseline) gid=0 ;;
    C1_inject_none) gid=1 ;;
    C2_probing) gid=2 ;;
    *) gid=9 ;;
  esac
  if ! fire_config "$cfg" "$gid"; then
    rc=1
    echo "[hold-exec] config $cfg failed"
    pull_results || true
    break
  fi
  pull_results
done

clean_pod

ACCEPT_SCRIPT="${ACCEPT_SCRIPT:-${FS_SHARED_SCRIPTS}/accept_loud.py}"
if [[ -f "$ACCEPT_SCRIPT" ]]; then
  set +e
  if [[ "$(basename "$ACCEPT_SCRIPT")" == "accept_p1swc_spike.py" ]]; then
    python3 "$ACCEPT_SCRIPT" \
      --result-root "$LOCAL_RESULT_ROOT" \
      --case "$CASE_ID" \
      --min-median-ratio "$ACCEPT_MIN_RATIO" \
      --write-md "$LOCAL_RESULT_ROOT/ACCEPT_LOUD.md"
  else
    python3 "$ACCEPT_SCRIPT" \
      --result-root "$LOCAL_RESULT_ROOT" \
      --case "$CASE_ID" \
      --min-ratio "$ACCEPT_MIN_RATIO" \
      --configs "$ABC_CONFIGS" \
      --write-md "$LOCAL_RESULT_ROOT/ACCEPT_LOUD.md"
  fi
  accept_rc=$?
  set -e
  echo "[hold-exec] accept_rc=$accept_rc"
fi

echo "[hold-exec] DONE rc=$rc LOCAL=$LOCAL_RESULT_ROOT"
echo "$RUN_ID" >"$LOCAL_RESULT_ROOT/RUN_ID.txt"
exit "$rc"
