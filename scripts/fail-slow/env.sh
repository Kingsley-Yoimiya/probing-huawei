#!/usr/bin/env bash
# 华为昇腾 Fail-Slow 默认环境（本机编排 / 跳板发射前 source）
# 身份细节：myportal config/shortcuts/huawei-ais-syy.yaml
# 注意：可被 bash/zsh source；不要 set -u（避免破坏调用方 shell）

# --- 仓库根（本仓）---
_FS_SELF="${BASH_SOURCE[0]:-}"
if [[ -z "${_FS_SELF}" && -n "${ZSH_VERSION:-}" ]]; then
  # zsh: %x = 当前被 source 的文件
  _FS_SELF="${(%):-%x}"
fi
if [[ -z "${_FS_SELF}" || "${_FS_SELF}" == "env.sh" || "${_FS_SELF}" == "-bash" ]]; then
  _FS_SELF="$0"
fi
FS_HUAWEI_ROOT="$(cd "$(dirname "${_FS_SELF}")/../.." && pwd)"
unset _FS_SELF
export FS_HUAWEI_ROOT

# --- 共享编排（probing-test；经 myportal 链接）---
# 允许覆盖：FS_SHARED_SCRIPTS=/path/to/probing-test/scripts/fail-slow
if [[ -z "${FS_SHARED_SCRIPTS:-}" ]]; then
  if [[ -d "${FS_HUAWEI_ROOT}/../probing-test/scripts/fail-slow" ]]; then
    FS_SHARED_SCRIPTS="$(cd "${FS_HUAWEI_ROOT}/../probing-test/scripts/fail-slow" && pwd)"
  elif [[ -d "${HOME}/Codespace/myportal/project/probing-test/scripts/fail-slow" ]]; then
    FS_SHARED_SCRIPTS="$(cd "${HOME}/Codespace/myportal/project/probing-test/scripts/fail-slow" && pwd)"
  else
    echo "WARN: probing-test scripts/fail-slow not found; set FS_SHARED_SCRIPTS" >&2
    FS_SHARED_SCRIPTS=""
  fi
fi
export FS_SHARED_SCRIPTS
export FS_PLATFORM_ASCEND="${FS_SHARED_SCRIPTS:+${FS_SHARED_SCRIPTS}/platform/ascend}"

# --- 结果根（禁止写 muxi-h3c）---
export FS_PLATFORM=ascend
export LOCAL_RESULT_ROOT_BASE="${LOCAL_RESULT_ROOT_BASE:-${HOME}/Codespace/myportal/results/ascend-ais}"
export AFS_RESULTS="${AFS_RESULTS:-/afs-a3-weight-share/yinjinrun.p-huawei/results}"
export AFS_PROBING="${AFS_PROBING:-/afs-a3-weight-share/yinjinrun.p-huawei/probing-huawei}"

# --- 集群身份（借用 SYY；落盘仍 yinjinrun.p-huawei）---
export FS_IDENTITY=songyiyang.p-huawei
export CLUSTER_NAME=vc-a3-241ceshi
# 默认强制 SYY kube，避免继承壳里残留的沐曦 weibozhen KUBECONFIG。
# 若确需保留外层：FS_KEEP_KUBECONFIG=1 source env.sh
_FS_SYY_KUBE="${HOME}/.kube/config-vc-a3-241ceshi-songyiyang.yaml"
if [[ "${FS_KEEP_KUBECONFIG:-0}" == "1" ]]; then
  export KUBECONFIG="${KUBECONFIG:-${_FS_SYY_KUBE}}"
else
  export KUBECONFIG="${_FS_SYY_KUBE}"
fi
unset _FS_SYY_KUBE
export JUMP_HOST="${JUMP_HOST:-ais-cf3e61a5}"
export JUMP_KUBECONFIG="${JUMP_KUBECONFIG:-/tmp/config-vc-a3-241ceshi-songyiyang.yaml}"
# 跳板 PATH 无 kubectl；必须用这个绝对路径（见 agents/RESOURCE.md）
export JUMP_KUBECTL="${JUMP_KUBECTL:-/root/.cache/volcano/kubectl/kubectl}"

# --- 运行模式：在 yysong（SYY 借权 64 卡）壳内 exec ---
# 空闲判定：yysong pod 内无活训练；禁止碰 a3-/grj-megatron（他人）
# 落盘仍 yinjinrun.p-huawei；禁止写宋一扬 AFS
export FS_RUN_MODE="${FS_RUN_MODE:-hold-exec}"
export FS_HOLD_JOBS="${FS_HOLD_JOBS:-yysong}"
export FS_HOLD_PODS_CASE="${FS_HOLD_PODS_CASE:-yysong-master-0}"
export FS_HOLD_PODS_GH="${FS_HOLD_PODS_GH:-yysong-worker-1}"
export FS_HOLD_PODS_XPU="${FS_HOLD_PODS_XPU:-yysong-worker-2}"

# --- 作业命名 / 结果标签 ---
export FS_JOB_PREFIX="${FS_JOB_PREFIX:-yjr-as}"

# --- Probing（训练进程侧；pod 内再 source platform/ascend/env.defaults）---
export PROBING_GPU_BACKEND="${PROBING_GPU_BACKEND:-npu}"
export PROBING_NPU_SOURCE="${PROBING_NPU_SOURCE:-auto}"

# --- 剂量 ---
export FS_DOSE_RECIPES="${FS_DOSE_RECIPES:-${FS_HUAWEI_ROOT}/scripts/fail-slow/dose_recipes.yaml}"

# --- 文档 ---
export FS_LEDGER="${FS_HUAWEI_ROOT}/docs/fail-slow/ledger.md"
export FS_RULES="${FS_HUAWEI_ROOT}/docs/fail-slow/rules.md"

echo "[fs-ascend] FS_HUAWEI_ROOT=${FS_HUAWEI_ROOT}"
echo "[fs-ascend] FS_SHARED_SCRIPTS=${FS_SHARED_SCRIPTS:-<unset>}"
echo "[fs-ascend] LOCAL_RESULT_ROOT_BASE=${LOCAL_RESULT_ROOT_BASE}"
echo "[fs-ascend] KUBECONFIG=${KUBECONFIG}"
echo "[fs-ascend] JUMP_KUBECTL=${JUMP_KUBECTL}"
echo "[fs-ascend] FS_RUN_MODE=${FS_RUN_MODE} HOLD=${FS_HOLD_JOBS}"
echo "[fs-ascend] FS_JOB_PREFIX=${FS_JOB_PREFIX} (exec on yysong; never a3/grj; AFS=yinjinrun.p-huawei)"
