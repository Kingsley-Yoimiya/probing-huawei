#!/usr/bin/env bash
# Plan §7: 单 rank Probing 主路径 smoke（PROBING_NPU_SYNC_SKELETON=1，static，非实验 hook）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXP_LOCAL="${ROOT}/experiments/mspti_sync_skeleton"
JUMP="${JUMP:-afs-cpu}"
KUBE="${KUBE:-/root/.kube/config-vc-a3-241ceshi-songyiyang.yaml}"
KUBECTL="${KUBECTL:-/root/bin/kubectl}"
NS="${NS:-default}"
MASTER_POD="${MASTER_POD:-grj-megatron-32card-0716-master-0}"
WORKER_POD="${WORKER_POD:-grj-megatron-32card-0716-worker-0}"

RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)-probing-main-smoke-1r}"
AFS_ROOT="/afs-a3-weight-share/yinjinrun.p-huawei"
OUT_DIR="${AFS_ROOT}/results/probing-main-smoke/${RUN_ID}"
OVERLAY_DIR="${AFS_ROOT}/probing-huawei/main-path-overlay-${RUN_ID}"
BACKUP_ROOT="/Users/yinjinrun/Codespace/myportal/results/huawei-a3-32/probing-main-smoke/${RUN_ID}"
LOG_DIR="/Users/yinjinrun/Codespace/myportal/logs/probing-main-smoke-${RUN_ID}"
MASTER_PORT="${MASTER_PORT:-38221}"
RUN_MARKER="PROBING_MAIN_SMOKE_${RUN_ID}"

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

CODE_HASH="$(
  cd "${EXP_LOCAL}" && python3 - <<'PY'
import hashlib
from pathlib import Path
h = hashlib.sha256()
for p in sorted(Path('.').iterdir()):
    if p.is_file() and (p.suffix in {'.cpp','.hpp','.h','.py','.sh','.md'} or p.name == 'CMakeLists.txt'):
        h.update(p.name.encode()); h.update(b'\0'); h.update(p.read_bytes()); h.update(b'\n')
print(h.hexdigest()[:16])
PY
)"
CODE_DIR="${AFS_ROOT}/probing-huawei/experiments/mspti_sync_skeleton-${CODE_HASH}"
GIT_HASH="$(cd "${ROOT}" && git rev-parse HEAD 2>/dev/null || echo unknown)"

mkdir -p "${BACKUP_ROOT}" "${LOG_DIR}"
exec > >(tee -a "${LOG_DIR}/launcher.log") 2>&1

MYPORTAL="${MYPORTAL:-$(cd "${ROOT}/../myportal" 2>/dev/null && pwd)}"
MYPORTAL="${MYPORTAL:-/Users/yinjinrun/Codespace/myportal}"
PREFLIGHT_LOG="${LOG_DIR}/verify_channels.log"
echo "[precheck] verify_channels -> ${PREFLIGHT_LOG}"
if ! python3 "${MYPORTAL}/setup/check/verify_channels.py" \
  >"${PREFLIGHT_LOG}" 2>&1; then
  echo "FATAL: verify_channels failed; see ${PREFLIGHT_LOG}" >&2
  exit 2
fi
echo "[precheck] verify_channels OK"

RESTORE_DONE=0
restore_site_packages() {
  if [[ "${RESTORE_DONE}" == "1" ]]; then
    return 0
  fi
  echo "[restore] site-packages backup (trap/exit path)"
  pod_exec "${MASTER_POD}" \
    "SITE=/root/miniconda3/envs/llm_test/lib/python3.10/site-packages; BK=${OVERLAY_DIR}/site-packages.bak; \
     if [[ ! -d \"\${BK}\" ]]; then echo RESTORE_SKIP no_backup; exit 0; fi; \
     if [[ -f \"\${BK}/probing/ext/torch.py\" ]]; then cp -a \"\${BK}/probing/ext/torch.py\" \"\${SITE}/probing/ext/torch.py\"; fi; \
     if [[ -f \"\${BK}/probing/profiling/__init__.py\" ]]; then cp -a \"\${BK}/probing/profiling/__init__.py\" \"\${SITE}/probing/profiling/__init__.py\"; fi; \
     if [[ -f \"\${BK}/probing/bundled_skills/catalog.yaml\" ]]; then cp -a \"\${BK}/probing/bundled_skills/catalog.yaml\" \"\${SITE}/probing/bundled_skills/catalog.yaml\"; fi; \
     if [[ -d \"\${BK}/probing_profiling_npu_sync\" ]]; then rm -rf \"\${SITE}/probing/profiling/npu_sync\"; cp -a \"\${BK}/probing_profiling_npu_sync\" \"\${SITE}/probing/profiling/npu_sync\"; fi; \
     if [[ -d \"\${BK}/bundled_npu_sync_skeleton\" ]]; then rm -rf \"\${SITE}/probing/bundled_skills/npu_sync_skeleton\"; cp -a \"\${BK}/bundled_npu_sync_skeleton\" \"\${SITE}/probing/bundled_skills/npu_sync_skeleton\"; fi; \
     echo RESTORE_OK" \
    || echo "[restore] WARN: restore failed" >&2
  RESTORE_DONE=1
}
trap restore_site_packages EXIT

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
    | tar -C "${BACKUP_ROOT}" -xf -
  echo "LOCAL_BACKUP=${BACKUP_ROOT}"
}

fail_stop() {
  local code="$1" reason="${2:-fail}"
  echo "FAIL_STOP rc=${code} reason=${reason} RUN_ID=${RUN_ID}" >&2
  pull_evidence || true
  restore_site_packages || true
  exit "${code}"
}

echo "[precheck] grj pods + idle"
jump "\$K get pod -n '${NS}' '${MASTER_POD}' '${WORKER_POD}' -o wide"
check_idle "${MASTER_POD}" || fail_stop 3 "opponent_master"
check_idle "${WORKER_POD}" || fail_stop 3 "opponent_worker"

echo "[sync] mspti_sync_skeleton -> ${CODE_DIR}"
COPYFILE_DISABLE=1 tar -C "${EXP_LOCAL}" -cf - \
  CMakeLists.txt collector.cpp kseg_logic.hpp sync_interpose.cpp workload.py \
  convert_trace.py strict_validate.py provenance.py kill_attempt.py megatron_mspti_hook.py \
  sitecustomize.py run_megatron_node.sh run_node.sh launch_grj.sh \
  smoke_validate_main_path.py opponent_check.py run_probing_main_pretrain.py sitecustomize.py README.md \
  | ssh -o ConnectTimeout=30 "${JUMP}" \
    "export KUBECONFIG='${KUBE}'; K='${KUBECTL}'; \$K exec -i -n '${NS}' '${MASTER_POD}' -- bash --noprofile --norc -lc 'mkdir -p ${CODE_DIR} && tar -C ${CODE_DIR} -xf - && chmod +x ${CODE_DIR}/*.sh ${CODE_DIR}/*.py'"

echo "[sync] probing patch files -> ${OVERLAY_DIR}"
COPYFILE_DISABLE=1 tar -C "${ROOT}" -cf - \
  python/probing/profiling/npu_sync \
  python/probing/profiling/__init__.py \
  python/probing/ext/torch.py \
  python/probing/bundled_skills/npu_sync_skeleton \
  python/probing/bundled_skills/catalog.yaml \
  | ssh -o ConnectTimeout=30 "${JUMP}" \
    "export KUBECONFIG='${KUBE}'; K='${KUBECTL}'; \$K exec -i -n '${NS}' '${MASTER_POD}' -- bash --noprofile --norc -lc 'mkdir -p ${OVERLAY_DIR} && tar -C ${OVERLAY_DIR} -xf -'"

echo "[patch] install npu_sync into llm_test site-packages (backup + restore on exit)"
pod_exec "${MASTER_POD}" \
  "source /root/miniconda3/etc/profile.d/conda.sh; conda activate llm_test; \
   SITE=/root/miniconda3/envs/llm_test/lib/python3.10/site-packages; \
   BK=${OVERLAY_DIR}/site-packages.bak; mkdir -p \"\${BK}\"; \
   for f in probing/ext/torch.py probing/profiling/__init__.py probing/bundled_skills/catalog.yaml; do \
     if [[ -f \"\${SITE}/\${f}\" ]]; then \
       d=\$(dirname \"\${f}\"); mkdir -p \"\${BK}/\${d}\"; cp -a \"\${SITE}/\${f}\" \"\${BK}/\${f}\"; \
     fi; \
   done; \
   if [[ -d \"\${SITE}/probing/profiling/npu_sync\" ]]; then cp -a \"\${SITE}/probing/profiling/npu_sync\" \"\${BK}/probing_profiling_npu_sync\"; fi; \
   if [[ -d \"\${SITE}/probing/bundled_skills/npu_sync_skeleton\" ]]; then cp -a \"\${SITE}/probing/bundled_skills/npu_sync_skeleton\" \"\${BK}/bundled_npu_sync_skeleton\"; fi; \
   cp -a '${OVERLAY_DIR}/python/probing/ext/torch.py' \"\${SITE}/probing/ext/torch.py\"; \
   cp -a '${OVERLAY_DIR}/python/probing/profiling/__init__.py' \"\${SITE}/probing/profiling/__init__.py\"; \
   cp -a '${OVERLAY_DIR}/python/probing/profiling/npu_sync' \"\${SITE}/probing/profiling/\"; \
   cp -a '${OVERLAY_DIR}/python/probing/bundled_skills/npu_sync_skeleton' \"\${SITE}/probing/bundled_skills/\"; \
   cp -a '${OVERLAY_DIR}/python/probing/bundled_skills/catalog.yaml' \"\${SITE}/probing/bundled_skills/catalog.yaml\"; \
   /root/miniconda3/envs/llm_test/bin/python -c \"import probing.ext.torch as t; import probing.profiling.npu_sync as n; print('PATCH_OK', t.__file__, n.__file__)\"" \
  || fail_stop 11 "site_packages_patch_failed"

pod_exec "${MASTER_POD}" "mkdir -p '${OUT_DIR}'"

echo "[build] collector .so in pod (CANN 8.5.0)"
pod_exec "${MASTER_POD}" \
  "source /usr/local/Ascend/cann-8.5.0/set_env.sh; mkdir -p '${CODE_DIR}/build'; \
   g++ -std=c++17 -shared -fPIC -O2 -Wall -Wextra -Wpedantic -I'${CODE_DIR}' \
   '${CODE_DIR}/collector.cpp' -I/usr/local/Ascend/cann-8.5.0/include \
   -L/usr/local/Ascend/cann-8.5.0/lib64 -Wl,-rpath,/usr/local/Ascend/cann-8.5.0/lib64 \
   -lmspti -lpthread -o '${CODE_DIR}/build/libmspti_sync_skeleton.so' \
   >'${OUT_DIR}/build.log' 2>&1" || fail_stop 10 "build_so"

SO_SHA="$(pod_exec "${MASTER_POD}" "python3 - <<'PY'
from hashlib import sha256
from pathlib import Path
p=Path('${CODE_DIR}/build/libmspti_sync_skeleton.so')
h=sha256()
with p.open('rb') as f:
    while b:=f.read(1<<20): h.update(b)
print(h.hexdigest())
PY")"

pod_exec "${MASTER_POD}" "python3 '${CODE_DIR}/provenance.py' --code-dir '${CODE_DIR}' --out-dir '${OUT_DIR}' --phase pre"
pod_exec "${MASTER_POD}" "python3 '${CODE_DIR}/provenance.py' --code-dir '${CODE_DIR}' --out-dir '${OUT_DIR}' --phase build"

POD_META="$(jump "\$K get pod -n '${NS}' '${MASTER_POD}' -o jsonpath='{.metadata.uid}|{.spec.nodeName}|{.status.containerStatuses[0].image}'; echo")"
CANN_VER="$(pod_exec "${MASTER_POD}" "source /usr/local/Ascend/cann-8.5.0/set_env.sh; python3 -c 'import os; print(os.environ.get(\"ASCEND_HOME_PATH\",\"unknown\"))'")"

pod_exec "${MASTER_POD}" "python3 - <<'PY'
import json
from pathlib import Path
cfg = {
  'run_id': '${RUN_ID}',
  'arm': 'probing_main',
  'granularity_mode': 'static',
  'nnodes': ${NNODES},
  'nproc_per_node': ${NPROC},
  'train_iters': ${TRAIN_ITERS},
  'capture_step': ${CAPTURE_STEP},
  'probing_main': True,
  'mspti_skeleton': False,
  'git_hash': '${GIT_HASH}',
  'code_dir': '${CODE_DIR}',
  'code_hash': '${CODE_HASH}',
  'overlay_dir': '${OVERLAY_DIR}',
  'collector_so': '${CODE_DIR}/build/libmspti_sync_skeleton.so',
  'collector_so_sha256': '${SO_SHA}',
  'cann': '${CANN_VER}',
  'pods': '''${POD_META}'''.strip().splitlines(),
  'master_port': ${MASTER_PORT},
}
Path('${OUT_DIR}/config.json').write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding='utf-8')
print('CONFIG_OK')
PY"

MASTER_ADDR="$(jump "\$K get pod -n '${NS}' '${MASTER_POD}' -o jsonpath='{.status.podIP}'")"
[[ -n "${MASTER_ADDR}" ]] || fail_stop 4 "no_master_ip"

echo "[run] 1-rank Megatron + PROBING_NPU_SYNC_SKELETON (static), TRAIN_ITERS=${TRAIN_ITERS} capture=${CAPTURE_STEP}"
pod_exec "${MASTER_POD}" \
  "source /usr/local/Ascend/cann-8.5.0/set_env.sh; source /root/miniconda3/etc/profile.d/conda.sh; conda activate llm_test; \
   export CUDA_DEVICE_MAX_CONNECTIONS=1 PYTHONUNBUFFERED=1 HCCL_CONNECT_TIMEOUT=1800 ASCEND_RT_VISIBLE_DEVICES=0; \
   export PROBING=2 PROBING_NPU_SYNC_SKELETON=1; \
   export PROBING_NPU_SYNC_SKELETON_LIB='${CODE_DIR}/build/libmspti_sync_skeleton.so'; \
   export PROBING_NPU_SYNC_SKELETON_OUT_DIR='${OUT_DIR}'; \
   export PROBING_NPU_SYNC_SKELETON_RANKS=0; \
   export PROBING_NPU_SYNC_SKELETON_STEP=${CAPTURE_STEP}; \
   export PROBING_NPU_SYNC_SKELETON_WINDOW_START_STEP=${CAPTURE_STEP}; \
   export PROBING_NPU_SYNC_SKELETON_WINDOW_STEPS=1; \
   export PROBING_NPU_SYNC_SKELETON_GRANULARITY_MODE=static; \
   export PROBING_NPU_SYNC_SKELETON_GAP_US=50 PROBING_NPU_SYNC_SKELETON_REORDER_US=1000; \
   unset MSPTI_SKELETON; \
   export PYTHONPATH='${CODE_DIR}':\${PYTHONPATH:-}; \
   export TRAIN_ITERS='${TRAIN_ITERS}'; \
   export RUN_MARKER='${RUN_MARKER}' RUN_ID='${RUN_ID}' OUT_DIR='${OUT_DIR}'; \
   cd /MindSpeed-LLM/MindSpeed-LLM && \
   timeout 600 /root/miniconda3/envs/llm_test/bin/torchrun \
     --nnodes=${NNODES} --nproc_per_node=${NPROC} --node_rank=0 \
     --master_addr='${MASTER_ADDR}' --master_port=${MASTER_PORT} \
     '${CODE_DIR}/run_probing_main_pretrain.py' \
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
if [[ "${RC_LINE}" != "TORCHRUN_RC=0" ]]; then
  fail_stop 5 "torchrun_nonzero ${RC_LINE}"
fi

echo "[validate] ingest + skill SQL + negatives"
VALIDATE_RC=0
pod_exec "${MASTER_POD}" \
  "source /root/miniconda3/etc/profile.d/conda.sh; conda activate llm_test; \
   export CAPTURE_STEP=${CAPTURE_STEP}; \
   /root/miniconda3/envs/llm_test/bin/python '${CODE_DIR}/smoke_validate_main_path.py' \
     --out-dir '${OUT_DIR}' \
     --skill-root '/root/miniconda3/envs/llm_test/lib/python3.10/site-packages/probing/bundled_skills/npu_sync_skeleton' \
     --report '${OUT_DIR}/smoke_validate.json' \
     --phase positive" \
  || VALIDATE_RC=8

pod_exec "${MASTER_POD}" \
  "source /root/miniconda3/etc/profile.d/conda.sh; conda activate llm_test; \
   export CAPTURE_STEP=${CAPTURE_STEP}; \
   /root/miniconda3/envs/llm_test/bin/python '${CODE_DIR}/smoke_validate_main_path.py' \
     --out-dir '${OUT_DIR}' \
     --skill-root '/root/miniconda3/envs/llm_test/lib/python3.10/site-packages/probing/bundled_skills/npu_sync_skeleton' \
     --report '${OUT_DIR}/smoke_validate_neg.json' \
     --phase negatives" \
  || VALIDATE_RC=8

pod_exec "${MASTER_POD}" \
  "python3 - <<'PY'
import json
from pathlib import Path
out = Path('${OUT_DIR}')
pos = json.loads((out/'smoke_validate.json').read_text()) if (out/'smoke_validate.json').exists() else {}
neg = json.loads((out/'smoke_validate_neg.json').read_text()) if (out/'smoke_validate_neg.json').exists() else {}
merged = {**pos, **neg}
(out/'smoke_validate.json').write_text(json.dumps(merged, indent=2, sort_keys=True), encoding='utf-8')
print('MERGE_OK', 'neg_missing_sidecar', merged.get('negative_missing_sidecar', {}).get('ok'),
      'neg_dual', merged.get('negative_dual_switch', {}).get('ok'))
PY"

if [[ "${VALIDATE_RC}" -ne 0 ]]; then
  fail_stop "${VALIDATE_RC}" "validate_failed"
fi

restore_site_packages

pod_exec "${MASTER_POD}" "python3 - <<'PY'
import json, hashlib
from pathlib import Path
out = Path('${OUT_DIR}')
parts = []
for p in sorted(out.glob('node_*.log')) + sorted(out.glob('build.log')):
    parts.append(f'--- {p.name} ---\\n' + p.read_text(errors='replace'))
(out / 'run.log').write_text('\\n'.join(parts), encoding='utf-8')
val = json.loads((out/'smoke_validate.json').read_text())
summary = {
  'run_id': '${RUN_ID}',
  'status': 'SMOKE_COMPLETE' if val.get('positive_artifacts',{}).get('ok') else 'SMOKE_INCOMPLETE',
  'trustworthy': val.get('positive_artifacts',{}).get('trustworthy_via_skill'),
  'granularity_mode': 'static',
  'collector_so_sha256': '${SO_SHA}',
  'validate': val,
}
(out/'SUMMARY.md').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print('SUMMARY_OK', summary['status'], 'trustworthy', summary['trustworthy'])
PY"

pull_evidence
cp -f "${BACKUP_ROOT}/smoke_validate.json" "${LOG_DIR}/" 2>/dev/null || true
cp -f "${BACKUP_ROOT}/SUMMARY.md" "${LOG_DIR}/" 2>/dev/null || true
cp -f "${BACKUP_ROOT}/config.json" "${LOG_DIR}/" 2>/dev/null || true

echo "SMOKE_RUN_ID=${RUN_ID}"
echo "AFS_OUT=${OUT_DIR}"
echo "LOCAL_BACKUP=${BACKUP_ROOT}"
echo "LOG_DIR=${LOG_DIR}"
