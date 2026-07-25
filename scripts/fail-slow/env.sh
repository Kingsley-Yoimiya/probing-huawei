#!/usr/bin/env bash
# 华为昇腾 Fail-Slow 默认环境（本机编排 / 跳板发射前 source）
# 对外：只需本仓 + probing-test（或 FS_SHARED_SCRIPTS）+ 机器权限；**不依赖 myportal**。
# 注意：可被 bash/zsh source；不要 set -u（避免破坏调用方 shell）

# --- 仓库根（本仓 probing-huawei）---
_FS_SELF="${BASH_SOURCE[0]:-}"
if [[ -z "${_FS_SELF}" && -n "${ZSH_VERSION:-}" ]]; then
  _FS_SELF="${(%):-%x}"
fi
if [[ -z "${_FS_SELF}" || "${_FS_SELF}" == "env.sh" || "${_FS_SELF}" == "-bash" ]]; then
  _FS_SELF="$0"
fi
FS_HUAWEI_ROOT="$(cd "$(dirname "${_FS_SELF}")/../.." && pwd)"
unset _FS_SELF
export FS_HUAWEI_ROOT

# --- 共享编排（probing-test/scripts/fail-slow；含 platform/ascend）---
# 优先顺序：显式 FS_SHARED_SCRIPTS → 与本仓同级的 probing-test → 本仓 vendor → （可选）个人布局
if [[ -z "${FS_SHARED_SCRIPTS:-}" ]]; then
  if [[ -d "${FS_HUAWEI_ROOT}/../probing-test/scripts/fail-slow" ]]; then
    FS_SHARED_SCRIPTS="$(cd "${FS_HUAWEI_ROOT}/../probing-test/scripts/fail-slow" && pwd)"
  elif [[ -d "${FS_HUAWEI_ROOT}/vendor/probing-test/scripts/fail-slow" ]]; then
    FS_SHARED_SCRIPTS="$(cd "${FS_HUAWEI_ROOT}/vendor/probing-test/scripts/fail-slow" && pwd)"
  elif [[ -d "${HOME}/Codespace/probing-test/scripts/fail-slow" ]]; then
    FS_SHARED_SCRIPTS="$(cd "${HOME}/Codespace/probing-test/scripts/fail-slow" && pwd)"
  else
    echo "WARN: probing-test scripts/fail-slow not found; export FS_SHARED_SCRIPTS=/path/to/probing-test/scripts/fail-slow" >&2
    FS_SHARED_SCRIPTS=""
  fi
fi
export FS_SHARED_SCRIPTS
export FS_PLATFORM_ASCEND="${FS_SHARED_SCRIPTS:+${FS_SHARED_SCRIPTS}/platform/ascend}"

# --- 结果根（本机备份；不绑任何私有门户仓）---
# 默认写在本仓 results/ascend-ais/；也可用 FS_LOCAL_RESULTS 或 LOCAL_RESULT_ROOT_BASE 覆盖
export FS_PLATFORM=ascend
_FS_DEFAULT_LOCAL="${FS_HUAWEI_ROOT}/results/ascend-ais"
export LOCAL_RESULT_ROOT_BASE="${LOCAL_RESULT_ROOT_BASE:-${FS_LOCAL_RESULTS:-${_FS_DEFAULT_LOCAL}}}"
unset _FS_DEFAULT_LOCAL
mkdir -p "${LOCAL_RESULT_ROOT_BASE}" 2>/dev/null || true

# --- 集群侧真盘（pod 内；有 AFS/PVC 权限即可）---
export AFS_HOME="${AFS_HOME:-/afs-a3-weight-share/yinjinrun.p-huawei}"
# 部分镜像真盘在 /data/<user>/，与 AFS 前缀同名子树
export DATA_HOME="${DATA_HOME:-/data/yinjinrun.p-huawei}"
export AFS_RESULTS="${AFS_RESULTS:-${AFS_HOME}/results}"
export AFS_PROBING="${AFS_PROBING:-${AFS_HOME}/probing-huawei}"
export POD_RESULTS="${POD_RESULTS:-${DATA_HOME}/results/ascend-ais}"

# --- 集群身份（借用 SYY 进 vc-a3-241ceshi；落盘仍 yinjinrun.p-huawei）---
# kube 文件自备：见 docs/fail-slow/IDENTITY.md（不进仓密钥正文）
export FS_IDENTITY="${FS_IDENTITY:-songyiyang.p-huawei}"
export CLUSTER_NAME="${CLUSTER_NAME:-vc-a3-241ceshi}"
_FS_SYY_KUBE="${KUBECONFIG_SYY:-${HOME}/.kube/config-vc-a3-241ceshi-songyiyang.yaml}"
if [[ "${FS_KEEP_KUBECONFIG:-0}" == "1" ]]; then
  export KUBECONFIG="${KUBECONFIG:-${_FS_SYY_KUBE}}"
else
  export KUBECONFIG="${_FS_SYY_KUBE}"
fi
unset _FS_SYY_KUBE
export JUMP_HOST="${JUMP_HOST:-ais-cf3e61a5}"
export JUMP_KUBECONFIG="${JUMP_KUBECONFIG:-/tmp/config-vc-a3-241ceshi-songyiyang.yaml}"
export JUMP_KUBECTL="${JUMP_KUBECTL:-/root/.cache/volcano/kubectl/kubectl}"

# --- 运行模式：在 yysong（SYY 借权）壳内 exec ---
export FS_RUN_MODE="${FS_RUN_MODE:-hold-exec}"
export FS_HOLD_JOBS="${FS_HOLD_JOBS:-yysong}"
export FS_HOLD_PODS_CASE="${FS_HOLD_PODS_CASE:-yysong-master-0}"
export FS_HOLD_PODS_GH="${FS_HOLD_PODS_GH:-yysong-worker-1}"
export FS_HOLD_PODS_XPU="${FS_HOLD_PODS_XPU:-yysong-worker-2}"

export FS_JOB_PREFIX="${FS_JOB_PREFIX:-yjr-as}"

export PROBING_GPU_BACKEND="${PROBING_GPU_BACKEND:-npu}"
export PROBING_NPU_SOURCE="${PROBING_NPU_SOURCE:-auto}"

export FS_DOSE_RECIPES="${FS_DOSE_RECIPES:-${FS_HUAWEI_ROOT}/scripts/fail-slow/dose_recipes.yaml}"
export FS_LEDGER="${FS_HUAWEI_ROOT}/docs/fail-slow/ledger.md"
export FS_RULES="${FS_HUAWEI_ROOT}/docs/fail-slow/rules.md"

echo "[fs-ascend] FS_HUAWEI_ROOT=${FS_HUAWEI_ROOT}"
echo "[fs-ascend] FS_SHARED_SCRIPTS=${FS_SHARED_SCRIPTS:-<unset>}"
echo "[fs-ascend] LOCAL_RESULT_ROOT_BASE=${LOCAL_RESULT_ROOT_BASE}"
echo "[fs-ascend] POD_RESULTS=${POD_RESULTS}  AFS_HOME=${AFS_HOME}"
echo "[fs-ascend] KUBECONFIG=${KUBECONFIG}"
echo "[fs-ascend] JUMP=${JUMP_HOST} KUBECTL=${JUMP_KUBECTL}"
echo "[fs-ascend] FS_RUN_MODE=${FS_RUN_MODE} HOLD=${FS_HOLD_JOBS} PREFIX=${FS_JOB_PREFIX}"
echo "[fs-ascend] note: myportal NOT required; override LOCAL_RESULT_ROOT_BASE / FS_SHARED_SCRIPTS as needed"
