#!/usr/bin/env bash
# Pillar-C 单臂发射：复用 hold_exec_run_case.sh + GATE 三臂旋钮。
# 用法见 run_pillar_c_p3swa.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/fail-slow/env.sh"

ARM="${ARM:?need ARM=full_fidelity|probing_collapse|naive_downsample|e2_rate|e3a_upgrade|e4_naive|s1_mid_attach}"
CASE_ID="${CASE_ID:-P3-SW-A}"
DOSE="${DOSE:-loud}"
POD="${POD:-${FS_HOLD_PODS_C:-yysong-worker-0}}"
NPROC="${NPROC:-16}"
NNODES="${NNODES:-1}"
PARENT_RUN_ID="${PARENT_RUN_ID:?need PARENT_RUN_ID}"
OUT_FAMILY="${OUT_FAMILY:-pillar_c}"

# PR-4 多节点开关
# PILLAR_C_MULTINODE=1 时：自动设 NNODES=2，需提供 POD=<master> WORKER_POD=<worker>
PILLAR_C_MULTINODE="${PILLAR_C_MULTINODE:-0}"
WORKER_POD="${WORKER_POD:-}"
if [[ "${PILLAR_C_MULTINODE}" == "1" ]]; then
  NNODES=2
  if [[ -z "${WORKER_POD}" ]]; then
    echo "FATAL: PILLAR_C_MULTINODE=1 requires WORKER_POD=<worker-pod-name>" >&2
    exit 2
  fi
  # 默认联邦定位（e3a_upgrade 臂只改 scope；其他臂不自动开联邦定位）
  export PILLAR_C_LOCALIZE_FEDERATED="${PILLAR_C_LOCALIZE_FEDERATED:-1}"
fi
CASE_SLUG=$(echo "$CASE_ID" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9-')
# e2_rate 臂目录名含常驻 rate；e3a_upgrade 含升到的 SET rate；e4_naive 固定 naive_cut；s1 固定 mid_attach
if [[ "$ARM" == "e2_rate" ]]; then
  RESIDENT_RATE="${RESIDENT_RATE:?need RESIDENT_RATE for ARM=e2_rate}"
  ARM_DIR="rate_${RESIDENT_RATE}"
elif [[ "$ARM" == "e3a_upgrade" ]]; then
  RESIDENT_RATE="${RESIDENT_RATE:-0}"
  PILLAR_C_SET_RATE="${PILLAR_C_SET_RATE:?need PILLAR_C_SET_RATE for ARM=e3a_upgrade}"
  ARM_DIR="upgrade_rate_${PILLAR_C_SET_RATE}"
elif [[ "$ARM" == "e4_naive" ]]; then
  RESIDENT_RATE="${RESIDENT_RATE:-0}"
  ARM_DIR="naive_cut"
elif [[ "$ARM" == "s1_mid_attach" ]]; then
  RESIDENT_RATE="${RESIDENT_RATE:-0}"
  ARM_DIR="mid_attach"
else
  ARM_DIR="$ARM"
fi
ARM_RUN_ID="${ARM_RUN_ID:-${PARENT_RUN_ID}-${ARM_DIR}}"

export FS_SHARED_SCRIPTS="${FS_SHARED_SCRIPTS:-/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow}"
export FS_PLATFORM_ASCEND="${FS_PLATFORM_ASCEND:-${FS_SHARED_SCRIPTS}/platform/ascend}"
export POD_BUNDLE="${POD_BUNDLE:-/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle}"
# grj 无 /data/yinjinrun.p-huawei；强制 AFS（勿被 env.sh 的 DATA_HOME 默认盖掉）
export POD_RESULTS="/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais"
export LOCAL_RESULT_ROOT_BASE="${LOCAL_RESULT_ROOT_BASE:-${FS_HUAWEI_ROOT}/results/ascend-ais}"

PARENT_LOCAL="${LOCAL_RESULT_ROOT_BASE}/${OUT_FAMILY}/${PARENT_RUN_ID}"
LOCAL_RESULT_ROOT="${PARENT_LOCAL}/${ARM_DIR}"
POD_OUT="${POD_RESULTS}/${OUT_FAMILY}/${PARENT_RUN_ID}/${ARM_DIR}"
PROBING_DATA_DIR="${POD_OUT}/probing_data"

mkdir -p "$LOCAL_RESULT_ROOT" "$PARENT_LOCAL"

# GATE.md 三臂草稿（不重猜）
unset PROBING_COLD PROBING_COLD_MAX_TOTAL_MB PILLAR_C_SET_UPGRADE PROBING_ATTACH_AT_STEP PROBING_DEFERRED_VALUE 2>/dev/null || true
export PROBING=1
export PROBING_GPU=on
export PROBING_GPU_BACKEND=npu
export PROBING_NPU_SOURCE=auto
export PROBING_CPU=on
export PROBING_DATA_DIR

case "$ARM" in
  full_fidelity)
    export PROBING_GPU_SAMPLE_MS=50
    export PROBING_CPU_SAMPLE_MS=50
    export PROBING_TORCH_PROFILING='on,rate=1.0'
    export PILLAR_C_SET_UPGRADE=0
    ;;
  probing_collapse|probing_collapse_retry)
    export PROBING_GPU_SAMPLE_MS=500
    export PROBING_CPU_SAMPLE_MS=500
    export PROBING_TORCH_PROFILING='on,rate=0.05'
    export PILLAR_C_SET_UPGRADE=1
    ;;
  probing_collapse_neg|probing_collapse_neg_earlyset)
    # 阴性对照：SAMPLE_MS 与 full 打平（50），仅 torch rate 低→SET↑ 为变量
    export PROBING_GPU_SAMPLE_MS=50
    export PROBING_CPU_SAMPLE_MS=50
    export PROBING_TORCH_PROFILING='on,rate=0.05'
    export PILLAR_C_SET_UPGRADE=1
    ;;
  naive_downsample)
    export PROBING_GPU_SAMPLE_MS=500
    export PROBING_CPU_SAMPLE_MS=500
    export PROBING_TORCH_PROFILING='on,rate=0.05'
    export PROBING_COLD_MAX_TOTAL_MB=256
    export PILLAR_C_SET_UPGRADE=0
    ;;
  coldmax_128)
    export PROBING_GPU_SAMPLE_MS=500
    export PROBING_CPU_SAMPLE_MS=500
    export PROBING_TORCH_PROFILING='on,rate=0.05'
    export PROBING_COLD_MAX_TOTAL_MB=128
    export PILLAR_C_SET_UPGRADE=0
    ;;
  coldmax_256)
    export PROBING_GPU_SAMPLE_MS=500
    export PROBING_CPU_SAMPLE_MS=500
    export PROBING_TORCH_PROFILING='on,rate=0.05'
    export PROBING_COLD_MAX_TOTAL_MB=256
    export PILLAR_C_SET_UPGRADE=0
    ;;
  coldmax_512)
    export PROBING_GPU_SAMPLE_MS=500
    export PROBING_CPU_SAMPLE_MS=500
    export PROBING_TORCH_PROFILING='on,rate=0.05'
    export PROBING_COLD_MAX_TOTAL_MB=512
    export PILLAR_C_SET_UPGRADE=0
    ;;
  mid_set|c3_mid_set)
    # C-3：低保真起跑 → step~250 attach/SET rate=1.0
    export PROBING_GPU_SAMPLE_MS=500
    export PROBING_CPU_SAMPLE_MS=500
    export PROBING_TORCH_PROFILING='on,rate=0.05'
    export PILLAR_C_SET_UPGRADE=1
    export PILLAR_C_SET_AT_STEP="${PILLAR_C_SET_AT_STEP:-250}"
    ;;
  e2_rate|e3a_upgrade)
    # E2：常驻 rate 扫；③-A：常驻稀 + SET↑ 到 PILLAR_C_SET_RATE（自变量）
    # 周期小表保持 500ms；注入 onset 附近 SET probing.torch.profiling=on,rate=<SET_RATE>
    export PROBING_GPU_SAMPLE_MS="${PROBING_GPU_SAMPLE_MS:-500}"
    export PROBING_CPU_SAMPLE_MS="${PROBING_CPU_SAMPLE_MS:-500}"
    export PROBING_TORCH_PROFILING="on,rate=${RESIDENT_RATE}"
    export PILLAR_C_SET_UPGRADE=1
    export PILLAR_C_SET_AT_STEP="${PILLAR_C_SET_AT_STEP:-100}"
    export PILLAR_C_SET_RATE="${PILLAR_C_SET_RATE:-1.0}"
    # ③-A 默认 localize（SQL 定 culprit）；param calib 可显式 PILLAR_C_SET_SCOPE=victim
    if [[ "$ARM" == "e3a_upgrade" ]]; then
      export PILLAR_C_SET_SCOPE="${PILLAR_C_SET_SCOPE:-localize}"
    else
      export PILLAR_C_SET_SCOPE="${PILLAR_C_SET_SCOPE:-all}"
    fi
    ;;
  e4_naive)
    # E4：=E3 动态臂去掉触发升详（禁 mid SET）；常驻 rate 默认 0
    export PROBING_GPU_SAMPLE_MS="${PROBING_GPU_SAMPLE_MS:-500}"
    export PROBING_CPU_SAMPLE_MS="${PROBING_CPU_SAMPLE_MS:-500}"
    export PROBING_TORCH_PROFILING="on,rate=${RESIDENT_RATE}"
    export PILLAR_C_SET_UPGRADE=0
    unset PILLAR_C_SET_AT_STEP 2>/dev/null || true
    ;;
  s1_mid_attach)
    # S1：起训不挂 probing → step>=ATTACH_AT 才 site_hook；attach 在 onset 后
    # 标定：E1-off 20MB≈546 步；注入窗 [100,300]；默认 ATTACH_AT=150
    export PROBING=2
    export PROBING_DEFERRED_VALUE="${PROBING_DEFERRED_VALUE:-2}"
    export PROBING_ATTACH_AT_STEP="${PROBING_ATTACH_AT_STEP:-150}"
    export PROBING_GPU_SAMPLE_MS="${PROBING_GPU_SAMPLE_MS:-500}"
    export PROBING_CPU_SAMPLE_MS="${PROBING_CPU_SAMPLE_MS:-500}"
    export PROBING_TORCH_PROFILING="on,rate=${RESIDENT_RATE}"
    export PILLAR_C_SET_UPGRADE="${PILLAR_C_SET_UPGRADE:-1}"
    # SET 略晚于 attach，等 site_hook 起来（jsonl 行数≈步）
    export PILLAR_C_SET_AT_STEP="${PILLAR_C_SET_AT_STEP:-$((PROBING_ATTACH_AT_STEP + 5))}"
    ;;
  *)
    echo "unknown ARM=$ARM" >&2
    exit 2
    ;;
esac

# Loud 金标剂量（按 case；复用 B D4，不重判）
export SIDECAR_LOCAL_RANK="${SIDECAR_LOCAL_RANK:-7}"
export MODE="${MODE:-host_bound}"
export ACCEPT_MIN_RATIO="${ACCEPT_MIN_RATIO:-1.3}"
export ACCEPT_SCRIPT="${ACCEPT_SCRIPT:-/nonexistent}"
export DUMP_PROBING_SQL="${DUMP_PROBING_SQL:-1}"
export DUMP_WAIT_S="${DUMP_WAIT_S:-45}"

case "$CASE_ID" in
  P3-SW-A)
    INJECT_KIND="${INJECT_KIND:-8a}"
    export INLINE_GC_EVERY="${INLINE_GC_EVERY:-1}"
    export INLINE_GC_STALL_S="${INLINE_GC_STALL_S:-0.25}"
    MODE="${MODE:-host_bound}"
    COVER_TARGET="${COVER_TARGET:-D4_reuse_B_loud_20260725_012957}"
    INJECT_NOTE="inline_8a every=${INLINE_GC_EVERY} stall_s=${INLINE_GC_STALL_S}"
    ;;
  P3-SW-B)
    INJECT_KIND="${INJECT_KIND:-8b}"
    export INLINE_8B_MB="${INLINE_8B_MB:-16}"
    export INLINE_8B_STALL_S="${INLINE_8B_STALL_S:-0.25}"
    MODE="${MODE:-host_bound}"
    COVER_TARGET="${COVER_TARGET:-D4_reuse_B_loud_20260725_125558}"
    INJECT_NOTE="inline_8b mb=${INLINE_8B_MB} stall_s=${INLINE_8B_STALL_S}"
    ;;
  P1-HW-B)
    INJECT_KIND="${INJECT_KIND:-1b}"
    export INLINE_HBM_MB="${INLINE_HBM_MB:-512}"
    export INLINE_HBM_COPIES="${INLINE_HBM_COPIES:-6}"
    export INLINE_HBM_COPIES_MAX="${INLINE_HBM_COPIES_MAX:-48}"
    export INLINE_HBM_RAMP="${INLINE_HBM_RAMP:-1}"
    # hold_exec P1-HW-B 用 *_OVERRIDE 无条件覆盖顶层默认
    export INLINE_HBM_MB_OVERRIDE="${INLINE_HBM_MB}"
    export INLINE_HBM_COPIES_OVERRIDE="${INLINE_HBM_COPIES}"
    export INLINE_HBM_COPIES_MAX_OVERRIDE="${INLINE_HBM_COPIES_MAX}"
    export INLINE_HBM_RAMP_OVERRIDE="${INLINE_HBM_RAMP}"
    MODE="${MODE:-gpu_bound}"
    export MODE_OVERRIDE="${MODE}"
    COVER_TARGET="${COVER_TARGET:-D3_reuse_B_loud_20260725_142359}"
    INJECT_NOTE="inline_1b_ramp mb=${INLINE_HBM_MB} copies=${INLINE_HBM_COPIES}->${INLINE_HBM_COPIES_MAX} ramp=${INLINE_HBM_RAMP}"
    ;;
  P1-EXT-A)
    # recipes/ledger Loud：inline_cube 8192×64；阴性对照（塌缩应≈全量）
    INJECT_KIND="${INJECT_KIND:-inline_cube}"
    export CUBE_SIZE="${CUBE_SIZE:-8192}"
    export CUBE_MM="${CUBE_MM:-64}"
    MODE="${MODE:-gpu_bound}"
    export MODE_OVERRIDE="${MODE}"
    COVER_TARGET="${COVER_TARGET:-D2_reuse_B_loud_20260725_011129}"
    INJECT_NOTE="inline_cube size=${CUBE_SIZE} mm=${CUBE_MM}"
    ;;
  P1-SW-C)
    # recipes/ledger Loud：inline 2c n=1024 every=1；fallback 默认 0.6（torch_trace duration≥0.4s）
    INJECT_KIND="${INJECT_KIND:-2c}"
    export INLINE_2C_N="${INLINE_2C_N:-1024}"
    export INLINE_2C_EVERY="${INLINE_2C_EVERY:-1}"
    export INLINE_2C_FALLBACK_S="${INLINE_2C_FALLBACK_S:-0.6}"
    MODE="${MODE:-gpu_bound}"
    export MODE_OVERRIDE="${MODE}"
    COVER_TARGET="${COVER_TARGET:-D3_reuse_B_loud_20260725_121105}"
    INJECT_NOTE="inline_2c n=${INLINE_2C_N} every=${INLINE_2C_EVERY} fallback_s=${INLINE_2C_FALLBACK_S}"
    ;;
  *)
    echo "CASE_ID=$CASE_ID not wired in run_pillar_c_arm.sh" >&2
    exit 2
    ;;
esac
export INJECT_KIND

cat >"${LOCAL_RESULT_ROOT}/arm_manifest.yaml" <<YAML
case_id: ${CASE_ID}
dose: ${DOSE}
pillar: C
arm: ${ARM}
arm_dir: ${ARM_DIR}
out_family: ${OUT_FAMILY}
resident_rate: "${RESIDENT_RATE:-}"
parent_run_id: ${PARENT_RUN_ID}
arm_run_id: ${ARM_RUN_ID}
pod: ${POD}
world_size: $((NNODES * NPROC))
probing: ${PROBING}
probing_gpu_sample_ms: ${PROBING_GPU_SAMPLE_MS}
probing_torch_profiling: "${PROBING_TORCH_PROFILING}"
probing_cold_max_total_mb: "${PROBING_COLD_MAX_TOTAL_MB:-}"
pillar_c_set_upgrade: ${PILLAR_C_SET_UPGRADE:-0}
pillar_c_set_at_step: ${PILLAR_C_SET_AT_STEP:-}
pillar_c_set_rate: "${PILLAR_C_SET_RATE:-1.0}"
pillar_c_set_window_s: ${PILLAR_C_SET_WINDOW_S:-45}
pillar_c_set_window_steps: ${PILLAR_C_SET_WINDOW_STEPS:-0}
pillar_c_set_hang_max_s: ${PILLAR_C_SET_HANG_MAX_S:-900}
probing_attach_at_step: ${PROBING_ATTACH_AT_STEP:-}
probing_data_dir: ${PROBING_DATA_DIR}
inject_kind: ${INJECT_KIND}
inject_note: "${INJECT_NOTE}"
pod_out: ${POD_OUT}
gate_ref: _prep/pillar_c_gate/GATE.md
cover_target: ${COVER_TARGET}
YAML

echo "[pillar-c] ARM=$ARM DIR=$ARM_DIR RUN=$ARM_RUN_ID POD=$POD CASE=$CASE_ID inject=$INJECT_KIND out=${OUT_FAMILY}"
echo "[pillar-c] knobs: SAMPLE_MS=$PROBING_GPU_SAMPLE_MS TORCH=$PROBING_TORCH_PROFILING SET_UP=${PILLAR_C_SET_UPGRADE:-0} SET_AT=${PILLAR_C_SET_AT_STEP:-inject} ATTACH_AT=${PROBING_ATTACH_AT_STEP:-n/a} COLD_MAX=${PROBING_COLD_MAX_TOTAL_MB:-inf}"
echo "[pillar-c] dose: $INJECT_NOTE"
echo "$ARM_RUN_ID" >"${LOCAL_RESULT_ROOT}/RUN_ID.txt"
echo "$ARM_RUN_ID" >"${PARENT_LOCAL}/CURRENT_ARM_RUN_ID.txt"

env_args=(
  "CASE_ID=${CASE_ID}"
  "DOSE=${DOSE}"
  "PHASE=pillar_c"
  "ABC_CONFIGS=C2_probing"
  "POD=${POD}"
  "NPROC=${NPROC}"
  "NNODES=${NNODES}"
  "INJECT_KIND=${INJECT_KIND}"
  "SIDECAR_LOCAL_RANK=${SIDECAR_LOCAL_RANK}"
  "MODE=${MODE}"
  "RUN_ID=${ARM_RUN_ID}"
  "LOCAL_RESULT_ROOT=${LOCAL_RESULT_ROOT}"
  "POD_OUT=${POD_OUT}"
  "POD_BUNDLE=${POD_BUNDLE}"
  "POD_PYDEPS=${POD_BUNDLE}/pydeps"
  "PROBING=${PROBING}"
  "PROBING_GPU_SAMPLE_MS=${PROBING_GPU_SAMPLE_MS}"
  "PROBING_CPU_SAMPLE_MS=${PROBING_CPU_SAMPLE_MS}"
  "PROBING_TORCH_PROFILING=${PROBING_TORCH_PROFILING}"
  "PROBING_DATA_DIR=${PROBING_DATA_DIR}"
  "PILLAR_C_SET_UPGRADE=${PILLAR_C_SET_UPGRADE:-0}"
  "PILLAR_C_SET_AT_STEP=${PILLAR_C_SET_AT_STEP:-}"
  "PILLAR_C_SET_RATE=${PILLAR_C_SET_RATE:-1.0}"
  "PILLAR_C_SET_SCOPE=${PILLAR_C_SET_SCOPE:-all}"
  "PILLAR_C_SET_WINDOW_S=${PILLAR_C_SET_WINDOW_S:-45}"
  "PILLAR_C_SET_WINDOW_STEPS=${PILLAR_C_SET_WINDOW_STEPS:-0}"
  "PILLAR_C_SET_HANG_MAX_S=${PILLAR_C_SET_HANG_MAX_S:-900}"
  "JEXEC_POLL_TIMEOUT_S=${JEXEC_POLL_TIMEOUT_S:-25}"
  "HOLD_EXEC_SKIP_HEAVY_JSYNC=${HOLD_EXEC_SKIP_HEAVY_JSYNC:-0}"
  "PILLAR_C_LATENCY_PROBE=${PILLAR_C_LATENCY_PROBE:-0}"
  "PILLAR_C_W_STAR=${PILLAR_C_W_STAR:-100}"
  "PILLAR_C_TT_FLOOR=${PILLAR_C_TT_FLOOR:-800}"
  "PILLAR_C_LATENCY_PROBE_MAX_S=${PILLAR_C_LATENCY_PROBE_MAX_S:-600}"
  "PROBING_ATTACH_AT_STEP=${PROBING_ATTACH_AT_STEP:-}"
  "PROBING_DEFERRED_VALUE=${PROBING_DEFERRED_VALUE:-}"
  "ACCEPT_SCRIPT=${ACCEPT_SCRIPT}"
  "DUMP_PROBING_SQL=${DUMP_PROBING_SQL}"
  "FS_SHARED_SCRIPTS=${FS_SHARED_SCRIPTS}"
  "FS_PLATFORM_ASCEND=${FS_PLATFORM_ASCEND}"
  "LOCAL_RESULT_ROOT_BASE=${LOCAL_RESULT_ROOT_BASE}"
  # PR-4 多节点参数
  "PILLAR_C_MULTINODE=${PILLAR_C_MULTINODE:-0}"
  "WORKER_POD=${WORKER_POD:-}"
  "PILLAR_C_LOCALIZE_FEDERATED=${PILLAR_C_LOCALIZE_FEDERATED:-0}"
)
if [[ "$INJECT_KIND" == "8a" || "$INJECT_KIND" == "inline_8a" ]]; then
  env_args+=("INLINE_GC_EVERY=${INLINE_GC_EVERY:-1}" "INLINE_GC_STALL_S=${INLINE_GC_STALL_S:-0.25}")
fi
if [[ "$INJECT_KIND" == "8b" || "$INJECT_KIND" == "inline_8b" ]]; then
  env_args+=("INLINE_8B_MB=${INLINE_8B_MB:-16}" "INLINE_8B_STALL_S=${INLINE_8B_STALL_S:-0.25}")
fi
if [[ "$INJECT_KIND" == "1b" || "$INJECT_KIND" == "inline_hbm" || "$INJECT_KIND" == "hbm_ramp" ]]; then
  env_args+=(
    "INLINE_HBM_MB=${INLINE_HBM_MB:-512}"
    "INLINE_HBM_COPIES=${INLINE_HBM_COPIES:-6}"
    "INLINE_HBM_COPIES_MAX=${INLINE_HBM_COPIES_MAX:-48}"
    "INLINE_HBM_RAMP=${INLINE_HBM_RAMP:-1}"
    "INLINE_HBM_MB_OVERRIDE=${INLINE_HBM_MB_OVERRIDE:-${INLINE_HBM_MB:-512}}"
    "INLINE_HBM_COPIES_OVERRIDE=${INLINE_HBM_COPIES_OVERRIDE:-${INLINE_HBM_COPIES:-6}}"
    "INLINE_HBM_COPIES_MAX_OVERRIDE=${INLINE_HBM_COPIES_MAX_OVERRIDE:-${INLINE_HBM_COPIES_MAX:-48}}"
    "INLINE_HBM_RAMP_OVERRIDE=${INLINE_HBM_RAMP_OVERRIDE:-${INLINE_HBM_RAMP:-1}}"
    "MODE_OVERRIDE=${MODE_OVERRIDE:-${MODE:-gpu_bound}}"
  )
fi
if [[ "$INJECT_KIND" == "inline_cube" || "$INJECT_KIND" == "cube" ]]; then
  env_args+=(
    "CUBE_SIZE=${CUBE_SIZE:-8192}"
    "CUBE_MM=${CUBE_MM:-64}"
    "MODE_OVERRIDE=${MODE_OVERRIDE:-${MODE:-gpu_bound}}"
  )
fi
if [[ "$INJECT_KIND" == "2c" || "$INJECT_KIND" == "inline_2c" || "$INJECT_KIND" == "compile_spike" ]]; then
  env_args+=(
    "INLINE_2C_N=${INLINE_2C_N:-1024}"
    "INLINE_2C_EVERY=${INLINE_2C_EVERY:-1}"
    "INLINE_2C_FALLBACK_S=${INLINE_2C_FALLBACK_S:-0.6}"
    "MODE_OVERRIDE=${MODE_OVERRIDE:-${MODE:-gpu_bound}}"
  )
fi
if [[ -n "${PROBING_COLD_MAX_TOTAL_MB:-}" ]]; then
  env_args+=("PROBING_COLD_MAX_TOTAL_MB=${PROBING_COLD_MAX_TOTAL_MB}")
fi
if [[ -n "${PROBING_CPU_RING_MB:-}" ]]; then
  env_args+=("PROBING_CPU_RING_MB=${PROBING_CPU_RING_MB}")
fi

env "${env_args[@]}" bash "${ROOT}/scripts/fail-slow/hold_exec_run_case.sh"
rc=$?
echo "$rc" >"${LOCAL_RESULT_ROOT}/hold_exec.rc"
exit "$rc"
