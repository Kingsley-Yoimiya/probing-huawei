#!/usr/bin/env bash
# 单节点真实 MindSpeed/Megatron dense 训练（由 launch_megatron_ab/smoke 并行拉起）。
# 只记录本节点进程组；禁止对全局 pretrain/torchrun 做宽泛 pkill。
# provenance：先组装 EXTRA_ENV + 完整 argv，再写 node_*.launch.json。
set -euo pipefail

: "${RUN_ID:?}"
: "${ARM:?}"
: "${NODE_RANK:?}"
: "${MASTER_ADDR:?}"
: "${MASTER_PORT:?}"
: "${NNODES:?}"
: "${NPROC:?}"
: "${CODE_DIR:?}"
: "${OUT_DIR:?}"
: "${TRAIN_ITERS:?}"
: "${RUN_MARKER:?}"

source /usr/local/Ascend/cann-8.5.0/set_env.sh 2>/dev/null || \
  source /usr/local/Ascend/ascend-toolkit/set_env.sh 2>/dev/null || true
source /root/miniconda3/etc/profile.d/conda.sh
conda activate llm_test

export CUDA_DEVICE_MAX_CONNECTIONS=1
export PYTHONUNBUFFERED=1
export HCCL_CONNECT_TIMEOUT="${HCCL_CONNECT_TIMEOUT:-1800}"
export TORCHELASTIC_EXIT_BARRIER_TIMEOUT=30
export ASCEND_RT_VISIBLE_DEVICES="$(seq -s, 0 $((NPROC - 1)))"
export HCCL_IF_BASE_PORT=$((16000 + MASTER_PORT % 1000))
export LD_LIBRARY_PATH="/usr/local/Ascend/cann-8.5.0/lib64:${LD_LIBRARY_PATH:-}"
export RUN_MARKER

TP="${TP:-2}"
PP="${PP:-1}"
MBS="${MBS:-1}"
GBS="${GBS:-64}"
SEQ="${SEQ:-4096}"
LAYERS="${LAYERS:-32}"
VOCAB="${VOCAB:-151936}"
SEED="${SEED:-1234}"
DATA_PATH="${DATA_PATH:-/afs-a3-weight-share/enwiki/enwiki20230101/enwiki20230101-00000_text_document}"
CACHE="${CACHE:-/afs-a3-weight-share/yinjinrun.p-huawei/megatron-data-cache}"
CAPTURE_ITER="${MSPTI_CAPTURE_MEGATRON_ITER:-10}"
WORLD_SIZE=$((NNODES * NPROC))
DP=$((WORLD_SIZE / TP / PP))
if (( DP < 1 )); then DP=1; fi
export TP PP MBS GBS SEQ LAYERS SEED DATA_PATH CACHE WORLD_SIZE DP TRAIN_ITERS

mkdir -p "${OUT_DIR}" "${CACHE}"
rm -f "${OUT_DIR}/node_${NODE_RANK}.done" "${OUT_DIR}/node_${NODE_RANK}.fail" \
  "${OUT_DIR}/node_${NODE_RANK}.pgid" "${OUT_DIR}/node_${NODE_RANK}.pids"

PROFILE_ARGS=()
EXTRA_ENV=()
case "${ARM}" in
  normal)
    EXTRA_ENV+=(MSPTI_SKELETON=0)
    ;;
  ours)
    # Load immutable content-addressed SO copy (never the mutable build/*.so path alone).
    SEALED_SO=""
    if [[ -f "${OUT_DIR}/provenance_build.json" ]]; then
      SEALED_SO="$(python3 - <<'PY'
import json
from pathlib import Path
import os
p = Path(os.environ["OUT_DIR"]) / "provenance_build.json"
obj = json.loads(p.read_text(encoding="utf-8"))
print(obj.get("collector_so_load_path") or obj.get("collector_so_sealed_path") or "")
PY
)"
    fi
    if [[ -z "${SEALED_SO}" || ! -f "${SEALED_SO}" ]]; then
      # Prefer hash-named copy under code build/sealed/
      if [[ -d "${CODE_DIR}/build/sealed" ]]; then
        SEALED_SO="$(ls -1 "${CODE_DIR}/build/sealed"/libmspti_sync_skeleton.so.* 2>/dev/null | head -1 || true)"
      fi
    fi
    if [[ -z "${SEALED_SO}" || ! -f "${SEALED_SO}" ]]; then
      echo "FATAL: sealed collector SO missing; refuse mutable-only load" >&2
      exit 13
    fi
    SO_HASH_LOADED="$(python3 - <<PY
from hashlib import sha256
from pathlib import Path
p = Path("${SEALED_SO}")
h = sha256()
with p.open("rb") as fh:
    while True:
        b = fh.read(1024 * 1024)
        if not b:
            break
        h.update(b)
print(h.hexdigest())
PY
)"
    # Ensure attempt out_dir has sealed_bins copy for artifact seal / local rehash.
    mkdir -p "${OUT_DIR}/sealed_bins"
    SEALED_NAME="$(basename "${SEALED_SO}")"
    if [[ ! -f "${OUT_DIR}/sealed_bins/${SEALED_NAME}" ]]; then
      cp -f "${SEALED_SO}" "${OUT_DIR}/sealed_bins/${SEALED_NAME}"
      chmod a-w "${OUT_DIR}/sealed_bins/${SEALED_NAME}" || true
    fi
    EXTRA_ENV+=(
      MSPTI_SKELETON=1
      MSPTI_OUT_DIR="${OUT_DIR}"
      MSPTI_COLLECTOR_LIB="${SEALED_SO}"
      MSPTI_COLLECTOR_SO_SHA256="${SO_HASH_LOADED}"
      MSPTI_CAPTURE_MEGATRON_ITER="${CAPTURE_ITER}"
      MSPTI_CAPTURE_RANKS="${MSPTI_CAPTURE_RANKS:-all}"
      MSPTI_MAX_QUEUE_BYTES="${MSPTI_MAX_QUEUE_BYTES:-134217728}"
      MSPTI_DRAIN_TIMEOUT_MS="${MSPTI_DRAIN_TIMEOUT_MS:-30000}"
      PYTHONPATH="${CODE_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
      LD_LIBRARY_PATH="/usr/local/Ascend/cann-8.5.0/lib64:${LD_LIBRARY_PATH:-}"
    )
    export MSPTI_COLLECTOR_LIB_RESOLVED="${SEALED_SO}"
    export MSPTI_COLLECTOR_SO_SHA256_LOADED="${SO_HASH_LOADED}"
    ;;
  torch)
    EXTRA_ENV+=(MSPTI_SKELETON=0)
    PROFILE_ARGS+=(
      --profile
      --profile-step-start "${CAPTURE_ITER}"
      --profile-step-end "$((CAPTURE_ITER + 1))"
      --profile-level level0
      --profile-with-cpu
      --profile-data-simplification
      --profile-export-type text
      --profile-save-path "${OUT_DIR}/torch_prof_node${NODE_RANK}"
      --profile-ranks -1
    )
    ;;
  *)
    echo "unknown ARM=${ARM}" >&2
    exit 2
    ;;
esac

FULL_ARGV=(
  /root/miniconda3/envs/llm_test/bin/torchrun
  --nnodes="${NNODES}"
  --nproc_per_node="${NPROC}"
  --node_rank="${NODE_RANK}"
  --master_addr="${MASTER_ADDR}"
  --master_port="${MASTER_PORT}"
  pretrain_gpt.py
  --tensor-model-parallel-size "${TP}"
  --pipeline-model-parallel-size "${PP}"
  --use-mcore-models --use-distributed-optimizer
  --micro-batch-size "${MBS}" --global-batch-size "${GBS}"
)
# sequence-parallel 仅 TP>1 合法
if (( TP > 1 )); then
  FULL_ARGV+=(--sequence-parallel)
fi
FULL_ARGV+=(
  --use-flash-attn --use-rotary-position-embeddings --use-fused-rotary-pos-emb
  --use-fused-rmsnorm --use-fused-swiglu
  --tokenizer-type NullTokenizer --vocab-size "${VOCAB}"
  --num-layers "${LAYERS}" --hidden-size 4096 --ffn-hidden-size 14336
  --num-attention-heads 32 --group-query-attention --num-query-groups 8
  --seq-length "${SEQ}" --max-position-embeddings "${SEQ}"
  --make-vocab-size-divisible-by 128
  --untie-embeddings-and-output-weights --disable-bias-linear
  --attention-dropout 0.0 --hidden-dropout 0.0 --init-method-std 0.01
  --position-embedding-type rope --rotary-base 500000
  --normalization RMSNorm --norm-epsilon 1e-5 --swiglu
  --no-masked-softmax-fusion --attention-softmax-in-fp32
  --lr 1.25e-6 --min-lr 1.25e-7 --lr-decay-style cosine --lr-warmup-fraction 0.01
  --train-iters "${TRAIN_ITERS}" --weight-decay 1e-1 --clip-grad 1.0
  --adam-beta1 0.9 --adam-beta2 0.95 --initial-loss-scale 4096
  --no-gradient-accumulation-fusion --no-load-optim --no-load-rng --bf16
  --seed "${SEED}"
  --data-path "${DATA_PATH}" --split 100,0,0
  --data-cache-path "${CACHE}"
  --log-interval 1 --log-throughput --timing-log-level 1 --timing-log-option all
  --eval-iters 0 --save-interval 100000 --distributed-backend nccl
  "${PROFILE_ARGS[@]}"
)

FULL_ARGV_JSON="$(
  printf '%s\0' "${FULL_ARGV[@]}" | python3 -c '
import json, sys
parts = [x.decode("utf-8", "surrogateescape") for x in sys.stdin.buffer.read().split(b"\0") if x]
print(json.dumps(parts))
'
)"
if ((${#EXTRA_ENV[@]})); then
  EXTRA_ENV_JSON="$(
    printf '%s\0' "${EXTRA_ENV[@]}" | python3 -c '
import json, sys
parts = [x.decode("utf-8", "surrogateescape") for x in sys.stdin.buffer.read().split(b"\0") if x]
print(json.dumps(parts))
'
  )"
else
  EXTRA_ENV_JSON='[]'
fi
export FULL_ARGV_JSON EXTRA_ENV_JSON

python3 - <<'PY'
import json, os, socket, re
from pathlib import Path

out = Path(os.environ["OUT_DIR"]) / f"node_{os.environ['NODE_RANK']}.launch.json"
argv = json.loads(os.environ["FULL_ARGV_JSON"])
extra = json.loads(os.environ.get("EXTRA_ENV_JSON") or "[]")
# Exact allowlist only — no RUN_/TOKEN/SECRET/PASSWORD/KEY/VAULT/CREDENTIAL prefixes.
ALLOW = {
    "MSPTI_SKELETON",
    "MSPTI_OUT_DIR",
    "MSPTI_COLLECTOR_LIB",
    "MSPTI_COLLECTOR_SO_SHA256",
    "MSPTI_CAPTURE_MEGATRON_ITER",
    "MSPTI_CAPTURE_RANKS",
    "MSPTI_MAX_QUEUE_BYTES",
    "MSPTI_DRAIN_TIMEOUT_MS",
    "ARM",
    "SEED",
    "HCCL_CONNECT_TIMEOUT",
    "HCCL_IF_BASE_PORT",
    "ASCEND_RT_VISIBLE_DEVICES",
    "MASTER_ADDR",
    "MASTER_PORT",
    "NNODES",
    "NPROC",
    "TRAIN_ITERS",
    "TP",
    "PP",
    "MBS",
    "GBS",
    "SEQ",
    "LAYERS",
    "OUT_DIR",
    "CODE_DIR",
    "PYTHONPATH",
    "LD_LIBRARY_PATH",
    "CUDA_DEVICE_MAX_CONNECTIONS",
    "WORLD_SIZE",
    "DP",
    "DATA_PATH",
    "CACHE",
    "RUN_ID",
    "RUN_MARKER",
}
DENY_SUB = re.compile(r"(TOKEN|SECRET|PASSWORD|KEY|VAULT|CREDENTIAL)", re.I)
env_view = dict(os.environ)
for item in extra:
    if "=" in item:
        k, v = item.split("=", 1)
        env_view[k] = v
keep = {}
for k in sorted(ALLOW):
    if k not in env_view:
        continue
    if DENY_SUB.search(k):
        continue
    keep[k] = env_view[k]
# Explicitly never record injected secret-like RUN_* keys.
assert "RUN_TOKEN" not in keep
so_path = os.environ.get("MSPTI_COLLECTOR_LIB_RESOLVED") or env_view.get("MSPTI_COLLECTOR_LIB")
so_hash = os.environ.get("MSPTI_COLLECTOR_SO_SHA256_LOADED") or env_view.get("MSPTI_COLLECTOR_SO_SHA256")
payload = {
    "run_id": os.environ["RUN_ID"],
    "run_marker": os.environ["RUN_MARKER"],
    "arm": os.environ["ARM"],
    "node_rank": int(os.environ["NODE_RANK"]),
    "hostname": socket.gethostname(),
    "model": {
        "tp": int(os.environ.get("TP", "2")),
        "pp": int(os.environ.get("PP", "1")),
        "dp": int(os.environ.get("DP", "1")),
        "mbs": int(os.environ.get("MBS", "1")),
        "gbs": int(os.environ.get("GBS", "64")),
        "seq": int(os.environ.get("SEQ", "4096")),
        "layers": int(os.environ.get("LAYERS", "32")),
        "seed": int(os.environ.get("SEED", "1234")),
        "world_size": int(os.environ.get("WORLD_SIZE", "1")),
        "data_path": os.environ.get("DATA_PATH"),
        "cache": os.environ.get("CACHE"),
    },
    "train_iters": int(os.environ["TRAIN_ITERS"]),
    "capture_megatron_iter": int(os.environ.get("MSPTI_CAPTURE_MEGATRON_ITER", "10")),
    "master_port": int(os.environ["MASTER_PORT"]),
    "full_argv": argv,
    "extra_env": extra,
    "env": keep,
    "workload_kind": "megatron",
    "raw_exit_code_pending": True,
    "collector_so_loaded_path": so_path,
    "collector_so_sha256_loaded": so_hash,
    "e2e_clock": "time.monotonic_ns",
    "e2e_vs_utc_note": "e2e uses monotonic ns; UTC timestamps are separate provenance fields",
}
# Guard: argv must not start with spurious "--" from python -c -- encoding.
if argv and argv[0] == "--":
    raise SystemExit("full_argv starts with spurious '--'; refuse provenance write")
out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
print("provenance_written", out)
PY

cd /MindSpeed-LLM/MindSpeed-LLM
set +e
# e2e_wall_ms: true monotonic from command start through process exit / torch export.
# Distinct from UTC wall timestamps used in seals.
E2E_T0_NS="$(python3 -c 'import time; print(time.monotonic_ns())')"
setsid env "${EXTRA_ENV[@]}" RUN_MARKER="${RUN_MARKER}" OUT_DIR="${OUT_DIR}" RUN_ID="${RUN_ID}" ARM="${ARM}" \
  TP="${TP}" PP="${PP}" MBS="${MBS}" GBS="${GBS}" SEQ="${SEQ}" LAYERS="${LAYERS}" \
  SEED="${SEED}" DATA_PATH="${DATA_PATH}" CACHE="${CACHE}" WORLD_SIZE="${WORLD_SIZE}" DP="${DP}" \
  TRAIN_ITERS="${TRAIN_ITERS}" MSPTI_CAPTURE_MEGATRON_ITER="${CAPTURE_ITER}" \
timeout "${RUN_TIMEOUT_S:-1800}" "${FULL_ARGV[@]}" \
  >"${OUT_DIR}/node_${NODE_RANK}.log" 2>&1 &
TPID=$!
echo "${TPID}" >"${OUT_DIR}/node_${NODE_RANK}.pgid"
echo "${TPID}" >"${OUT_DIR}/node_${NODE_RANK}.pids"
# Early bootstrap only (same PGID as timeout/setsid). NOT a complete runtime set:
# TorchElastic later creates new rank PGIDs; opponent_check must discover those via
# live RUN_MARKER/OUT_DIR probes + own_pgids, not this 5s static list alone.
for _ in 1 2 3 4 5; do
  sleep 1
  ps -eo pid,pgid,args | awk -v pgid="${TPID}" '$2==pgid {print $1}' \
    >"${OUT_DIR}/node_${NODE_RANK}.pids" || true
done
# Live ownership snapshot (pid/pgid/starttime) for marker-bearing processes.
# Refreshed while training runs; checker prefers this over bare static pids.
OWN_SNAP_PID=""
(
  export OUT_DIR NODE_RANK RUN_MARKER
  while kill -0 "${TPID}" 2>/dev/null; do
    python3 - <<'PY' || true
import json, os, time
from pathlib import Path
out = Path(os.environ["OUT_DIR"])
node = os.environ["NODE_RANK"]
marker = os.environ["RUN_MARKER"]
needle = f"RUN_MARKER={marker}".encode()
out_needle = f"OUT_DIR={out}".encode()
rows = []
proc = Path("/proc")
if proc.is_dir():
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            raw = (entry / "environ").read_bytes()
        except OSError:
            continue
        parts = raw.split(b"\0")
        if needle not in parts and out_needle not in parts:
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8", errors="ignore")
            stat = (entry / "stat").read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        pgid = pid
        for line in status.splitlines():
            if line.startswith("NSpgid:"):
                try:
                    pgid = int(line.split()[1])
                except (IndexError, ValueError):
                    pass
                break
        starttime = None
        try:
            rparen = stat.rfind(")")
            fields = stat[rparen + 2 :].split()
            starttime = int(fields[19])
        except (IndexError, ValueError):
            starttime = None
        rows.append({"pid": pid, "pgid": pgid, "starttime": starttime, "ts": time.time()})
path = out / f"node_{node}.ownership.jsonl"
tmp = path.with_suffix(".jsonl.tmp")
tmp.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
tmp.replace(path)
PY
    sleep 2
  done
) &
OWN_SNAP_PID=$!
wait "${TPID}"
rc=$?
if [[ -n "${OWN_SNAP_PID}" ]]; then
  kill "${OWN_SNAP_PID}" 2>/dev/null || true
  wait "${OWN_SNAP_PID}" 2>/dev/null || true
fi
E2E_T1_NS="$(python3 -c 'import time; print(time.monotonic_ns())')"
E2E_WALL_MS="$(python3 -c 'import sys; a=int(sys.argv[1]); b=int(sys.argv[2]); print("%.3f" % ((b-a)/1e6))' "${E2E_T0_NS}" "${E2E_T1_NS}")"
set -e

python3 - <<PY
import json
from pathlib import Path
p = Path("${OUT_DIR}") / f"node_${NODE_RANK}.launch.json"
if p.exists():
    data = json.loads(p.read_text())
    data["raw_exit_code"] = int(${rc})
    data["raw_exit_code_pending"] = False
    e2e = float("${E2E_WALL_MS}")
    if e2e <= 0:
        raise SystemExit(f"non-positive e2e_wall_ms={e2e} t0_ns=${E2E_T0_NS} t1_ns=${E2E_T1_NS}")
    data["e2e_wall_ms"] = e2e
    data["e2e_t0_monotonic_ns"] = int("${E2E_T0_NS}")
    data["e2e_t1_monotonic_ns"] = int("${E2E_T1_NS}")
    data["e2e_wall_definition"] = (
        "time.monotonic_ns() from torchrun/pretrain argv start through process exit; "
        "includes torch profiler export when ARM=torch; independent of UTC timestamps; "
        "same口径 normal/ours/torch"
    )
    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
PY

if [[ "${rc}" -eq 0 ]]; then
  touch "${OUT_DIR}/node_${NODE_RANK}.done"
else
  printf '%s\n' "${rc}" >"${OUT_DIR}/node_${NODE_RANK}.fail"
fi
exit "${rc}"
