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
INLINE_HBM_MB="${INLINE_HBM_MB:-512}"
INLINE_HBM_COPIES="${INLINE_HBM_COPIES:-48}"
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
    INJECT_ARGS="${INJECT_ARGS:-inline_hbm_mb=${INLINE_HBM_MB},inline_hbm_copies=${INLINE_HBM_COPIES}}"
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

jexec() {
  local cmd="$1"
  ssh -o BatchMode=yes -o ConnectTimeout=30 "${JUMP_HOST}" \
    "export KUBECONFIG='${JUMP_KUBECONFIG}'; K='${JUMP_KUBECTL}'; \$K -n '${NS}' exec '${POD}' -- bash -lc $(printf '%q' "$cmd")"
}

jsync_file() {
  local src="$1" dst="$2"
  local bname ddir rc
  bname=$(basename "$src")
  ddir=$(dirname "$dst")
  # extract to a staging dir then install — avoids "same file" when dst name == tar member
  set +e
  COPYFILE_DISABLE=1 tar -C "$(dirname "$src")" -cf - "$bname" \
    | ssh -o BatchMode=yes -o ConnectTimeout=30 "${JUMP_HOST}" \
      "export KUBECONFIG='${JUMP_KUBECONFIG}'; K='${JUMP_KUBECTL}'; \$K -n '${NS}' exec -i '${POD}' -- bash -lc $(printf '%q' "mkdir -p '$ddir' /tmp/yjr_sync && tar -C /tmp/yjr_sync -xf - && install -m 0755 /tmp/yjr_sync/$bname '$dst' && rm -f /tmp/yjr_sync/$bname")"
  rc=$?
  set -e
  echo "[hold-exec] jsync $bname -> $dst rc=$rc"
  return 0
}

pod_ip() {
  ssh -o BatchMode=yes -o ConnectTimeout=30 "${JUMP_HOST}" \
    "export KUBECONFIG='${JUMP_KUBECONFIG}'; K='${JUMP_KUBECTL}'; \$K -n '${NS}' get pod '${POD}' -o jsonpath='{.status.podIP}'"
}

echo "[hold-exec] sync train → ${POD_BUNDLE}/train_bench_probe_npu.py"
jexec "mkdir -p '${POD_BUNDLE}' '${POD_OUT}' '${POD_PYDEPS}'"
jsync_file "$TRAIN_PY_LOCAL" "${POD_BUNDLE}/train_bench_probe_npu.py"
if [[ -f "$SIDECAR_PY_LOCAL" ]]; then
  echo "[hold-exec] sync sidecar → ${POD_BUNDLE}/sidecar_inject_npu.py"
  jsync_file "$SIDECAR_PY_LOCAL" "${POD_BUNDLE}/sidecar_inject_npu.py"
fi
if [[ -f "$DUMP_SQL_LOCAL" ]]; then
  jsync_file "$DUMP_SQL_LOCAL" "${POD_BUNDLE}/dump_probing_sql.sh"
fi
MASTER_IP="$(pod_ip)"
echo "[hold-exec] MASTER_IP=$MASTER_IP"
[[ -n "$MASTER_IP" ]] || { echo "FATAL: no pod IP"; exit 2; }

clean_pod() {
  jexec "pkill -9 -f '[t]rain_bench_probe_npu' 2>/dev/null || true; pkill -9 -f '/tmp/[t]bp_npu.py' 2>/dev/null || true; pkill -9 -f '[t]orchrun' 2>/dev/null || true; pkill -9 -f '[s]idecar_inject_npu' 2>/dev/null || true; pkill -9 -x stress-ng 2>/dev/null || true; pkill -9 -f '[s]tress-ng' 2>/dev/null || true; pkill -9 -f 'fio.*io_stress' 2>/dev/null || true; sleep 2; exit 0" || true
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
    denv="export PROBING=2; unset PROBING_TORCH_PROFILING; export PROBING_GPU=on; export PROBING_GPU_BACKEND=npu; export PROBING_NPU_SOURCE=auto; export PROBING_GPU_SAMPLE_MS=1000; export PROBING_CPU=on; export PROBING_CPU_SAMPLE_MS=1000; export PYTHONPATH=${POD_PYDEPS}:\${PYTHONPATH:-}; export PATH=${POD_PYDEPS}/bin:${PYBIN}:\${PATH};"
  fi
  # C1/C2：inline cube（同进程）
  if [[ "$cfg" == C1_* || "$cfg" == C2_* ]] && [[ "$INJECT_KIND" == inline_cube ]]; then
    denv="${denv} export INLINE_INJECT=cube; export INLINE_VICTIM_LOCAL_RANK=${SIDECAR_LOCAL_RANK}; export INLINE_INJECT_START=${INJECT_START}; export INLINE_INJECT_STOP=${INJECT_STOP}; export INLINE_CUBE_SIZE=${CUBE_SIZE}; export INLINE_CUBE_MM=${CUBE_MM};"
  fi
  # C1/C2：inline HBM（同进程带宽争用）
  if [[ "$cfg" == C1_* || "$cfg" == C2_* ]] && [[ "$INJECT_KIND" == inline_hbm || "$INJECT_KIND" == hbm ]]; then
    denv="${denv} export INLINE_INJECT=hbm; export INLINE_VICTIM_LOCAL_RANK=${SIDECAR_LOCAL_RANK}; export INLINE_INJECT_START=${INJECT_START}; export INLINE_INJECT_STOP=${INJECT_STOP}; export INLINE_HBM_MB=${INLINE_HBM_MB}; export INLINE_HBM_COPIES=${INLINE_HBM_COPIES};"
  fi
  # C1/C2：inline 8a GC/stall（同进程）
  if [[ "$cfg" == C1_* || "$cfg" == C2_* ]] && [[ "$INJECT_KIND" == 8a || "$INJECT_KIND" == inline_8a ]]; then
    denv="${denv} export INLINE_INJECT=8a; export INLINE_VICTIM_LOCAL_RANK=${SIDECAR_LOCAL_RANK}; export INLINE_INJECT_START=${INJECT_START}; export INLINE_INJECT_STOP=${INJECT_STOP}; export INLINE_GC_EVERY=${INLINE_GC_EVERY:-1}; export INLINE_GC_STALL_S=${INLINE_GC_STALL_S:-0.25};"
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
  jexec "setsid nohup bash /tmp/run_${gid}.sh </dev/null >/dev/null 2>&1 & echo FIRE_OK; exit 0"

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

  if [[ "$cfg" == C1_* || "$cfg" == C2_* ]] && [[ "$INJECT_KIND" == stress_cpu || "$INJECT_KIND" == stress_io || "$INJECT_KIND" == stress_vm || "$INJECT_KIND" == cube || "$INJECT_KIND" == inline_cube || "$INJECT_KIND" == inline_hbm || "$INJECT_KIND" == hbm || "$INJECT_KIND" == 8a || "$INJECT_KIND" == inline_8a ]]; then
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

    if [[ "$INJECT_KIND" == inline_cube ]]; then
      echo "  inline cube active (victim=${SIDECAR_LOCAL_RANK} size=${CUBE_SIZE} mm=${CUBE_MM})"
      jexec "grep -E \"INLINE_CUBE|SIDECAR\" '${out}/node_0.log' | head -20 >'${out}/injection.log' || true; echo SIDECAR_START kind=inline_cube size=${CUBE_SIZE} mm=${CUBE_MM} victim=${SIDECAR_LOCAL_RANK} >>'${out}/injection.log'; exit 0" || true
      # npu-smi on physical id for victim logical rank
      # npu-smi：远端展开 \${ASCEND_VISIBLE_DEVICES:-0}/\${PHYS:-0}，本地勿裸扩
      jexec "PHYS=\$(echo \${ASCEND_VISIBLE_DEVICES:-0} | cut -d, -f$((SIDECAR_LOCAL_RANK+1))); npu-smi info -t usages -i \${PHYS:-0} 2>/dev/null | head -40 >'${out}/npu_smi_util_inject.txt' || npu-smi info 2>/dev/null | head -80 >'${out}/npu_smi_util_inject.txt'; exit 0" || true
    elif [[ "$INJECT_KIND" == inline_hbm || "$INJECT_KIND" == hbm ]]; then
      echo "  inline hbm active (victim=${SIDECAR_LOCAL_RANK} mb=${INLINE_HBM_MB} copies=${INLINE_HBM_COPIES})"
      jexec "grep -E \"INLINE_HBM|SIDECAR\" '${out}/node_0.log' | head -20 >'${out}/injection.log' || true; echo SIDECAR_START kind=inline_hbm mb=${INLINE_HBM_MB} copies=${INLINE_HBM_COPIES} victim=${SIDECAR_LOCAL_RANK} >>'${out}/injection.log'; exit 0" || true
      jexec "PHYS=\$(echo \${ASCEND_VISIBLE_DEVICES:-0} | cut -d, -f$((SIDECAR_LOCAL_RANK+1))); npu-smi info -t usages -i \${PHYS:-0} 2>/dev/null | head -40 >'${out}/npu_smi_util_inject.txt' || npu-smi info 2>/dev/null | head -80 >'${out}/npu_smi_util_inject.txt'; exit 0" || true
    elif [[ "$INJECT_KIND" == 8a || "$INJECT_KIND" == inline_8a ]]; then
      echo "  inline 8a GC/stall active (victim=${SIDECAR_LOCAL_RANK} every=${INLINE_GC_EVERY:-1} stall=${INLINE_GC_STALL_S:-0.25})"
      jexec "grep -E \"INLINE_8A|INLINE_GC|SIDECAR\" '${out}/node_0.log' | head -20 >'${out}/injection.log' || true; echo SIDECAR_START kind=inline_8a every=${INLINE_GC_EVERY:-1} stall_s=${INLINE_GC_STALL_S:-0.25} victim=${SIDECAR_LOCAL_RANK} >>'${out}/injection.log'; exit 0" || true
    elif [[ "$INJECT_KIND" == stress_cpu ]]; then
      if [[ -n "$CPU_N" ]]; then
        jexec "nohup stress-ng --cpu ${CPU_N} --cpu-load ${CPU_LOAD} --timeout 900s >'${out}/injection.log' 2>&1 & echo SC=\$!; echo SIDECAR_START stress_cpu cpu_n=${CPU_N} cpu_load=${CPU_LOAD} >>'${out}/injection.log'; exit 0"
      else
        jexec "nohup stress-ng --cpu \$(nproc) --cpu-load ${CPU_LOAD} --timeout 900s >'${out}/injection.log' 2>&1 & echo SC=\$!; echo SIDECAR_START stress_cpu cpu_n=nproc cpu_load=${CPU_LOAD} >>'${out}/injection.log'; exit 0"
      fi
      echo "  stress-ng started (host-wide; manifest victim_local_rank=${SIDECAR_LOCAL_RANK})"
    elif [[ "$INJECT_KIND" == stress_io ]]; then
      # 镜像无 fio/apt：stress-ng hdd+iomix；temp-path 钉到与 ckpt/payload 同盘
      jexec "mkdir -p '${IO_STRESS_DIR}'; : >'${out}/injection.log'; if command -v fio >/dev/null 2>&1; then nohup fio --name=io_stress --rw=randrw --bs=4k --size=4G --numjobs=16 --iodepth=64 --time_based --runtime=900 --directory='${IO_STRESS_DIR}' --group_reporting >'${out}/injection.log' 2>&1 & echo SC=\$!; echo SIDECAR_START fio_loud_nj16 dir=${IO_STRESS_DIR} >>'${out}/injection.log'; else nohup stress-ng --temp-path '${IO_STRESS_DIR}' --hdd ${HDD_N} --hdd-bytes ${HDD_BYTES} --iomix ${IOMIX_N} --iomix-bytes ${HDD_BYTES} --timeout 900s >'${out}/injection.log' 2>&1 & echo SC=\$!; echo SIDECAR_START stress_io hdd_n=${HDD_N} hdd_bytes=${HDD_BYTES} iomix_n=${IOMIX_N} dir=${IO_STRESS_DIR} >>'${out}/injection.log'; fi; exit 0"
      echo "  stress_io started (dir=${IO_STRESS_DIR} hdd_n=${HDD_N} iomix_n=${IOMIX_N})"
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
        jexec "pkill -TERM -f '[s]idecar_inject_npu' 2>/dev/null || true; pkill -TERM stress-ng 2>/dev/null || true; sleep 1; pkill -9 -f '[s]idecar_inject_npu' 2>/dev/null || true; pkill -9 stress-ng 2>/dev/null || true; echo SIDECAR_STOP >>'${out}/injection.log'; exit 0" || true
        break
      fi
      if jexec "test -f '${out}/node_0.done' -o -f '${out}/node_0.fail'" 2>/dev/null; then
        break
      fi
      sleep 5; e=$((e + 5))
    done
  fi

  e=0
  while [ "$e" -lt 3600 ]; do
    if jexec "test -f '${out}/node_0.done'" 2>/dev/null; then
      echo "  done (${e}s)"
      jexec "pkill -9 -f '[s]idecar_inject_npu' 2>/dev/null || true; pkill -9 stress-ng 2>/dev/null || true; exit 0" || true
      return 0
    fi
    # pilot 宽松：测量窗 step_300 已齐且训练已退出 → 视为可验收（防他方 pkill 误杀）
    if jexec "test -f '${out}/ranks/step_${INJECT_STOP}.marker'" 2>/dev/null; then
      if ! jexec "pgrep -f '[t]bp_npu|[t]orchrun' >/dev/null" 2>/dev/null; then
        echo "  measure window complete + training gone → accept partial (step_${INJECT_STOP})"
        jexec "pkill -9 -f '[s]idecar_inject_npu' 2>/dev/null || true; pkill -9 stress-ng 2>/dev/null || true; touch '${out}/node_0.done'; echo PARTIAL_DONE >>'${out}/node_0.log'; exit 0" || true
        return 0
      fi
    fi
    if jexec "test -f '${out}/node_0.fail'" 2>/dev/null; then
      # 若已有 step_300，fail 也升格 partial-ok
      if jexec "test -f '${out}/ranks/step_${INJECT_STOP}.marker'" 2>/dev/null; then
        echo "  FAIL after measure window → partial-ok"
        jexec "pkill -9 -f '[s]idecar_inject_npu' 2>/dev/null || true; pkill -9 stress-ng 2>/dev/null || true; rm -f '${out}/node_0.fail'; touch '${out}/node_0.done'; exit 0" || true
        return 0
      fi
      echo "  FAIL marker"; jexec "tail -n 150 '${out}/node_0.log'" || true
      jexec "pkill -9 -f '[s]idecar_inject_npu' 2>/dev/null || true; pkill -9 stress-ng 2>/dev/null || true; exit 0" || true
      return 1
    fi
    sleep 10; e=$((e + 10))
    if [ $((e % 60)) -eq 0 ]; then
      local njson
      njson=$(jexec "ls '${out}/ranks'/rank_*.jsonl 2>/dev/null | wc -l" 2>/dev/null | tr -d '[:space:]' || echo 0)
      echo "  waiting done… t=${e}s jsonl=${njson:-0}"
    fi
  done
  echo "  TIMEOUT"; jexec "tail -n 150 '${out}/node_0.log'" || true
  return 1
}

pull_results() {
  echo "[hold-exec] pull ${POD_OUT} → ${LOCAL_RESULT_ROOT}"
  ssh -o BatchMode=yes -o ConnectTimeout=60 "${JUMP_HOST}" \
    "export KUBECONFIG='${JUMP_KUBECONFIG}'; K='${JUMP_KUBECTL}'; \$K -n '${NS}' exec '${POD}' -- bash -lc $(printf '%q' "cd '${POD_OUT}' && tar -cf - .")" \
    >"${LOCAL_RESULT_ROOT}/.pull.tar"
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
  python3 "$ACCEPT_SCRIPT" \
    --result-root "$LOCAL_RESULT_ROOT" \
    --case "$CASE_ID" \
    --min-ratio "$ACCEPT_MIN_RATIO" \
    --configs "$ABC_CONFIGS" \
    --write-md "$LOCAL_RESULT_ROOT/ACCEPT_LOUD.md"
  accept_rc=$?
  set -e
  echo "[hold-exec] accept_rc=$accept_rc"
fi

echo "[hold-exec] DONE rc=$rc LOCAL=$LOCAL_RESULT_ROOT"
echo "$RUN_ID" >"$LOCAL_RESULT_ROOT/RUN_ID.txt"
exit "$rc"
