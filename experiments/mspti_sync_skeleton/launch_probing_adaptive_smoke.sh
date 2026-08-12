#!/usr/bin/env bash
# N1: 单 rank adaptive_v1 smoke — 正式 wheel + native .so，禁止 site-packages overlay
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXP_LOCAL="${ROOT}/experiments/mspti_sync_skeleton"
JUMP="${JUMP:-afs-cpu}"
KUBE="${KUBE:-/root/.kube/config-vc-a3-241ceshi-songyiyang.yaml}"
KUBECTL="${KUBECTL:-/root/bin/kubectl}"
NS="${NS:-default}"
MASTER_POD="${MASTER_POD:-grj-megatron-32card-0716-master-0}"
WORKER_POD="${WORKER_POD:-grj-megatron-32card-0716-worker-0}"

RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)-probing-adaptive-smoke-1r}"
AFS_ROOT="/afs-a3-weight-share/yinjinrun.p-huawei"
OUT_DIR="${AFS_ROOT}/results/probing-adaptive-smoke/${RUN_ID}"
BUILD_ROOT="${AFS_ROOT}/probing-huawei/native-adaptive-build/${RUN_ID}"
BACKUP_ROOT="/Users/yinjinrun/Codespace/myportal/results/huawei-a3-32/probing-adaptive-smoke/${RUN_ID}"
LOG_DIR="/Users/yinjinrun/Codespace/myportal/logs/probing-adaptive-smoke-${RUN_ID}"
MASTER_PORT="${MASTER_PORT:-38231}"
RUN_MARKER="PROBING_ADAPTIVE_SMOKE_${RUN_ID}"

NNODES=1
NPROC=1
TRAIN_ITERS=3
CAPTURE_STEP=2
SEED=1234
TP=1
PP=1
MBS=1
GBS=1
SEQ=1024
LAYERS=2
DATA_PATH="/afs-a3-weight-share/enwiki/enwiki20230101/enwiki20230101-00000_text_document"
CACHE="/afs-a3-weight-share/yinjinrun.p-huawei/megatron-data-cache"

GIT_HASH="$(cd "${ROOT}" && git rev-parse HEAD 2>/dev/null || echo unknown)"
SOURCE_TAR="${BUILD_ROOT}/probing-huawei-src.tar.gz"
WHEEL_GLOB="probing-0.2.6+native.adaptive.*-cp38-abi3-linux_aarch64.whl"
WHEEL_OUT="${BUILD_ROOT}/wheels/${WHEEL_GLOB}"
SO_OUT="${BUILD_ROOT}/libmspti_sync_skeleton.so"
PYBIN="/root/miniconda3/envs/llm_test/bin"
RUST_ENV="${AFS_ROOT}/toolchains/rust-env.sh"
CARGO_TARGET="${AFS_ROOT}/probing-huawei/build-handoff-bed9ee1/target"
CARGO_HOME="${AFS_ROOT}/toolchains/rust/cargo"

mkdir -p "${BACKUP_ROOT}" "${LOG_DIR}"
exec > >(tee -a "${LOG_DIR}/launcher.log") 2>&1

MYPORTAL="${MYPORTAL:-$(cd "${ROOT}/../myportal" 2>/dev/null && pwd)}"
MYPORTAL="${MYPORTAL:-/Users/yinjinrun/Codespace/myportal}"
PREFLIGHT_LOG="${LOG_DIR}/verify_channels.log"
echo "[precheck] verify_channels -> ${PREFLIGHT_LOG}"
if ! python3 "${MYPORTAL}/setup/check/verify_channels.py" >"${PREFLIGHT_LOG}" 2>&1; then
  echo "FATAL: verify_channels failed; see ${PREFLIGHT_LOG}" >&2
  exit 2
fi
echo "[precheck] verify_channels OK"

jump() {
  ssh -o ConnectTimeout=30 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
    "${JUMP}" "export KUBECONFIG='${KUBE}'; K='${KUBECTL}'; $*"
}

pod_exec() {
  local pod="$1" cmd="$2"
  jump "\$K exec -n '${NS}' '${pod}' -- bash --noprofile --norc -lc $(printf '%q' "${cmd}")"
}

check_idle() {
  local pod="$1" active
  active="$(pod_exec "${pod}" \
    "ps -eo pid,pgid,state,args | awk '/torchrun|pretrain_gpt.py/ && !/awk|bash --noprofile/ && \$3 !~ /Z/ {print}'" || true)"
  if [[ -n "${active}" ]]; then
    echo "FATAL: ${pod} 非空闲（对手进程）" >&2
    echo "${active}" >&2
    return 10
  fi
  return 0
}

pull_evidence() {
  mkdir -p "${BACKUP_ROOT}"
  jump "\$K exec -n '${NS}' '${MASTER_POD}' -- tar -C '${OUT_DIR}' -cf - ." \
    | tar -C "${BACKUP_ROOT}" -xf - 2>/dev/null || true
  jump "\$K exec -n '${NS}' '${MASTER_POD}' -- tar -C '${BUILD_ROOT}' -cf - wheels build.log wheel_build.log source_sha256.txt libmspti_sync_skeleton.so.sha256 2>/dev/null" \
    | tar -C "${BACKUP_ROOT}/build" -xf - 2>/dev/null || mkdir -p "${BACKUP_ROOT}/build"
  echo "LOCAL_BACKUP=${BACKUP_ROOT}"
}

fail_stop() {
  local code="$1" reason="${2:-fail}"
  echo "FAIL_STOP rc=${code} reason=${reason} RUN_ID=${RUN_ID}" >&2
  pull_evidence || true
  exit "${code}"
}

echo "[precheck] grj pods + idle"
jump "\$K get pod -n '${NS}' '${MASTER_POD}' '${WORKER_POD}' -o wide"
check_idle "${MASTER_POD}" || fail_stop 3 "opponent_master"
check_idle "${WORKER_POD}" || fail_stop 3 "opponent_worker"

echo "[sync] probing-huawei source tree -> ${SOURCE_TAR}"
COPYFILE_DISABLE=1 tar -C "${ROOT}" \
  --exclude='.git' --exclude='results' --exclude='.venv-builder' --exclude='target' \
  -czf - . \
  | ssh -o ConnectTimeout=30 "${JUMP}" \
    "export KUBECONFIG='${KUBE}'; K='${KUBECTL}'; \$K exec -i -n '${NS}' '${MASTER_POD}' -- bash --noprofile --norc -lc 'mkdir -p ${BUILD_ROOT} && cat > ${SOURCE_TAR}'"

echo "[hash] source content-addressed snapshot"
pod_exec "${MASTER_POD}" \
  "python3 - <<'PY'
import hashlib, tarfile
from pathlib import Path
p = Path('${SOURCE_TAR}')
h = hashlib.sha256()
with p.open('rb') as f:
    while b := f.read(1<<20):
        h.update(b)
Path('${BUILD_ROOT}/source_sha256.txt').write_text(h.hexdigest() + '\\n', encoding='utf-8')
print('SOURCE_SHA256', h.hexdigest())
PY"

echo "[build] formal wheel (no overlay)"
pod_exec "${MASTER_POD}" \
  "set -euo pipefail; \
   unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy; \
   source /usr/local/Ascend/ascend-toolkit/set_env.sh 2>/dev/null || source /usr/local/Ascend/cann-8.5.0/set_env.sh; \
   source /root/miniconda3/etc/profile.d/conda.sh && conda activate llm_test; \
   source '${RUST_ENV}'; \
   export CARGO_HOME='${CARGO_HOME}'; \
   export CARGO_TARGET_DIR='${CARGO_TARGET}'; \
   mkdir -p \"\$CARGO_HOME\" \"\$CARGO_TARGET_DIR\" '${BUILD_ROOT}/wheels' '${BUILD_ROOT}/src'; \
   cat > \"\$CARGO_HOME/config.toml\" <<'CFG'
[source.crates-io]
replace-with = \"tuna\"
[source.tuna]
registry = \"sparse+https://mirrors.tuna.tsinghua.edu.cn/crates.io-index/\"
CFG
   tar -xzf '${SOURCE_TAR}' -C '${BUILD_ROOT}/src'; \
   cd '${BUILD_ROOT}/src'; \
   rm -rf dist; mkdir -p python/probing/bundled_web/public web/dist dist; \
   echo '<!doctype html><title>probing</title>' > python/probing/bundled_web/public/index.html; \
   cp python/probing/bundled_web/public/index.html web/dist/index.html; \
   rm -rf python/probing/bundled_web && mkdir -p python/probing/bundled_web; \
   cp -a web/dist/. python/probing/bundled_web/; \
   cargo build -p probing-hccl-profapi --release >>'${BUILD_ROOT}/wheel_build.log' 2>&1; \
   mkdir -p python/probing/shim/hccl; \
   cp -f \"\$CARGO_TARGET_DIR/release/libprofapi.so\" python/probing/shim/hccl/; \
   PROBING_BUILD_TAG='native.adaptive.${RUN_ID}'; \
   sed -i 's/^version = \"0.2.6\"/version = \"0.2.6+native.adaptive.${RUN_ID}\"/' pyproject.toml; \
   '${PYBIN}/python' -m maturin build --release --features extension-module,gpu,kmsg --out dist >>'${BUILD_ROOT}/wheel_build.log' 2>&1; \
   WH=\$(ls -1 dist/probing-0.2.6+native.adaptive.*.whl | tail -1); \
   cp -f \"\$WH\" '${BUILD_ROOT}/wheels/'; \
   echo WHEEL_OK \"\$WH\" >>'${BUILD_ROOT}/wheel_build.log'"

echo "[build] native collector .so"
pod_exec "${MASTER_POD}" \
  "source /usr/local/Ascend/cann-8.5.0/set_env.sh; \
   g++ -std=c++17 -shared -fPIC -O2 -Wall -Wextra -Wpedantic \
   -I'${BUILD_ROOT}/src/experiments/mspti_sync_skeleton' \
   '${BUILD_ROOT}/src/experiments/mspti_sync_skeleton/collector.cpp' \
   -I/usr/local/Ascend/cann-8.5.0/include \
   -L/usr/local/Ascend/cann-8.5.0/lib64 -Wl,-rpath,/usr/local/Ascend/cann-8.5.0/lib64 \
   -lmspti -lpthread -o '${SO_OUT}' >'${BUILD_ROOT}/build.log' 2>&1" || fail_stop 10 "build_so"

pod_exec "${MASTER_POD}" \
  "python3 - <<'PY'
from hashlib import sha256
from pathlib import Path
for label, path in [('so', Path('${SO_OUT}')), ('wheel', sorted(Path('${BUILD_ROOT}/wheels').glob('*.whl'))[-1])]:
    h = sha256()
    with path.open('rb') as f:
        while b := f.read(1<<20): h.update(b)
    Path(f'${BUILD_ROOT}/{label}_sha256.txt' if label=='so' else '${BUILD_ROOT}/wheel_sha256.txt').write_text(h.hexdigest()+'\\n', encoding='utf-8')
    print(label.upper(), path.name, h.hexdigest())
PY"

WHEEL_NAME="$(pod_exec "${MASTER_POD}" "ls -1 ${BUILD_ROOT}/wheels/*.whl | tail -1")"
SO_SHA="$(pod_exec "${MASTER_POD}" "cat ${BUILD_ROOT}/so_sha256.txt")"
WHEEL_SHA="$(pod_exec "${MASTER_POD}" "cat ${BUILD_ROOT}/wheel_sha256.txt")"

echo "[install] pip install formal wheel (no overlay)"
pod_exec "${MASTER_POD}" \
  "source /root/miniconda3/etc/profile.d/conda.sh && conda activate llm_test && \
   '${PYBIN}/pip' install --force-reinstall --no-deps '${WHEEL_NAME}' >>'${BUILD_ROOT}/wheel_build.log' 2>&1 && \
   PROBING=0 '${PYBIN}/python' -c \"import probing; import probing.profiling.npu_sync as n; print('INSTALL_OK', probing.__file__, n.__file__)\"" \
  || fail_stop 11 "pip_install_failed"

pod_exec "${MASTER_POD}" "mkdir -p '${OUT_DIR}'"

CANN_VER="$(pod_exec "${MASTER_POD}" "source /usr/local/Ascend/cann-8.5.0/set_env.sh; python3 -c 'import os; print(os.environ.get(\"ASCEND_HOME_PATH\",\"unknown\"))'")"
pod_exec "${MASTER_POD}" "python3 - <<'PY'
import json
from pathlib import Path
cfg = {
  'run_id': '${RUN_ID}',
  'arm': 'probing_main_adaptive',
  'granularity_mode': 'adaptive_v1',
  'overlay': False,
  'wheel': '${WHEEL_NAME}',
  'wheel_sha256': '${WHEEL_SHA}',
  'collector_so': '${SO_OUT}',
  'collector_so_sha256': '${SO_SHA}',
  'git_hash': '${GIT_HASH}',
  'source_tar': '${SOURCE_TAR}',
  'cann': '${CANN_VER}',
  'train_iters': ${TRAIN_ITERS},
  'capture_step': ${CAPTURE_STEP},
}
Path('${OUT_DIR}/config.json').write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding='utf-8')
print('CONFIG_OK')
PY"

MASTER_ADDR="$(jump "\$K get pod -n '${NS}' '${MASTER_POD}' -o jsonpath='{.status.podIP}'")"
[[ -n "${MASTER_ADDR}" ]] || fail_stop 4 "no_master_ip"

echo "[run] 1-rank adaptive_v1 smoke"
pod_exec "${MASTER_POD}" \
  "source /usr/local/Ascend/cann-8.5.0/set_env.sh; source /root/miniconda3/etc/profile.d/conda.sh; conda activate llm_test; \
   export CUDA_DEVICE_MAX_CONNECTIONS=1 PYTHONUNBUFFERED=1 HCCL_CONNECT_TIMEOUT=1800 ASCEND_RT_VISIBLE_DEVICES=0; \
   export PROBING=2 PROBING_NPU_SYNC_SKELETON=1; \
   export PROBING_NPU_SYNC_SKELETON_LIB='${SO_OUT}'; \
   export PROBING_NPU_SYNC_SKELETON_OUT_DIR='${OUT_DIR}'; \
   export PROBING_NPU_SYNC_SKELETON_RANKS=0; \
   export PROBING_NPU_SYNC_SKELETON_STEP=${CAPTURE_STEP}; \
   export PROBING_NPU_SYNC_SKELETON_WINDOW_START_STEP=${CAPTURE_STEP}; \
   export PROBING_NPU_SYNC_SKELETON_WINDOW_STEPS=1; \
   export PROBING_NPU_SYNC_SKELETON_GRANULARITY_MODE=adaptive_v1; \
   export PROBING_NPU_SYNC_SKELETON_GAP_US=50 PROBING_NPU_SYNC_SKELETON_REORDER_US=1000; \
   export PROBING_NPU_SYNC_SKELETON_ADAPT_MIN_SAMPLES=32; \
   export PROBING_NPU_SYNC_SKELETON_ADAPT_EVERY=64; \
   unset MSPTI_SKELETON; \
   export PYTHONPATH='${BUILD_ROOT}/src/experiments/mspti_sync_skeleton':\${PYTHONPATH:-}; \
   export TRAIN_ITERS='${TRAIN_ITERS}'; \
   export RUN_MARKER='${RUN_MARKER}' RUN_ID='${RUN_ID}' OUT_DIR='${OUT_DIR}'; \
   cd /MindSpeed-LLM/MindSpeed-LLM && \
   timeout 600 '${PYBIN}/torchrun' \
     --nnodes=${NNODES} --nproc_per_node=${NPROC} --node_rank=0 \
     --master_addr='${MASTER_ADDR}' --master_port=${MASTER_PORT} \
     '${BUILD_ROOT}/src/experiments/mspti_sync_skeleton/run_probing_main_pretrain.py' \
     --tensor-model-parallel-size ${TP} --pipeline-model-parallel-size ${PP} \
     --use-mcore-models --use-distributed-optimizer \
     --micro-batch-size ${MBS} --global-batch-size ${GBS} \
     --use-flash-attn --use-rotary-position-embeddings --use-fused-rotary-pos-emb \
     --use-fused-rmsnorm --use-fused-swiglu \
     --tokenizer-type NullTokenizer --vocab-size 151936 \
     --num-layers ${LAYERS} --hidden-size 4096 --ffn-hidden-size 14336 \
     --num-attention-heads 32 --group-query-attention --num-query-groups 8 \
     --seq-length ${SEQ} --max-position-embeddings ${SEQ} \
     --make-vocab-size-divisible-by 128 --untie-embeddings-and-output-weights --disable-bias-linear \
     --attention-dropout 0.0 --hidden-dropout 0.0 --init-method-std 0.01 \
     --position-embedding-type rope --rotary-base 500000 \
     --normalization RMSNorm --norm-epsilon 1e-5 --swiglu \
     --no-masked-softmax-fusion --attention-softmax-in-fp32 \
     --lr 1.25e-6 --min-lr 1.25e-7 --lr-decay-style cosine --lr-warmup-fraction 0.01 \
     --train-iters ${TRAIN_ITERS} --weight-decay 1e-1 --clip-grad 1.0 \
     --adam-beta1 0.9 --adam-beta2 0.95 --initial-loss-scale 4096 \
     --no-gradient-accumulation-fusion --no-load-optim --no-load-rng --bf16 \
     --seed ${SEED} --data-path '${DATA_PATH}' --split 100,0,0 \
     --data-cache-path '${CACHE}' --log-interval 1 --log-throughput --timing-log-level 1 \
     --eval-iters 0 --save-interval 100000 --distributed-backend nccl \
     >'${OUT_DIR}/node_0.log' 2>&1; echo TORCHRUN_RC=\$? >'${OUT_DIR}/node_0.rc'" \
  || fail_stop 5 "torchrun_failed"

RC_LINE="$(pod_exec "${MASTER_POD}" "cat '${OUT_DIR}/node_0.rc' 2>/dev/null || echo TORCHRUN_RC=missing")"
echo "${RC_LINE}"
[[ "${RC_LINE}" == "TORCHRUN_RC=0" ]] || fail_stop 5 "torchrun_nonzero ${RC_LINE}"

echo "[validate] adaptive ingest + skill SQL"
VALIDATE_RC=0
pod_exec "${MASTER_POD}" \
  "source /root/miniconda3/etc/profile.d/conda.sh; conda activate llm_test; \
   export CAPTURE_STEP=${CAPTURE_STEP} EXPECT_GRANULARITY_MODE=adaptive_v1; \
   '${PYBIN}/python' '${BUILD_ROOT}/src/experiments/mspti_sync_skeleton/smoke_validate_main_path.py' \
     --out-dir '${OUT_DIR}' \
     --skill-root '/root/miniconda3/envs/llm_test/lib/python3.10/site-packages/probing/bundled_skills/npu_sync_skeleton' \
     --report '${OUT_DIR}/smoke_validate.json' \
     --phase all" \
  || VALIDATE_RC=8

[[ "${VALIDATE_RC}" -eq 0 ]] || fail_stop "${VALIDATE_RC}" "validate_failed"

pod_exec "${MASTER_POD}" "python3 - <<'PY'
import json
from pathlib import Path
out = Path('${OUT_DIR}')
val = json.loads((out/'smoke_validate.json').read_text())
meta = json.loads((out/'rank_0000.npu_sync_meta.json').read_text())
summary = {
  'run_id': '${RUN_ID}',
  'status': 'SMOKE_COMPLETE' if val.get('positive_artifacts',{}).get('ok') else 'SMOKE_INCOMPLETE',
  'trustworthy': val.get('positive_artifacts',{}).get('trustworthy_via_skill'),
  'granularity_mode': meta.get('granularity_mode'),
  'adaptive_source': meta.get('adaptive_source'),
  'threshold_updates': (meta.get('adaptive') or {}).get('threshold_updates'),
  'collector_so_sha256': '${SO_SHA}',
  'wheel_sha256': '${WHEEL_SHA}',
  'validate': val,
}
(out/'SUMMARY.md').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print('SUMMARY_OK', summary['status'], 'trustworthy', summary['trustworthy'], 'mode', summary['granularity_mode'])
PY"

pull_evidence
cp -f "${BACKUP_ROOT}/smoke_validate.json" "${LOG_DIR}/" 2>/dev/null || true
cp -f "${BACKUP_ROOT}/SUMMARY.md" "${LOG_DIR}/" 2>/dev/null || true
cp -f "${BACKUP_ROOT}/config.json" "${LOG_DIR}/" 2>/dev/null || true

echo "SMOKE_RUN_ID=${RUN_ID}"
echo "AFS_OUT=${OUT_DIR}"
echo "AFS_BUILD=${BUILD_ROOT}"
echo "LOCAL_BACKUP=${BACKUP_ROOT}"
echo "LOG_DIR=${LOG_DIR}"
