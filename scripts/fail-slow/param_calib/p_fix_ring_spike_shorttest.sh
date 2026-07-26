#!/usr/bin/env bash
# P-FIX 短测 · 关键小表环 + 注入尖刺（勿跑正式 ②-A/③）
# 目标 pod：grj-w0（确认无对方 torchrun/megatron；bash 自匹配勿当占用）
# 落盘：POD_RESULTS/_prep/pillar_c_gate/artifacts/p_fix_<TS>/
set -uo pipefail

BUNDLE="${POD_BUNDLE:-/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle}"
OUT="${GATE_OUT:-/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/_prep/pillar_c_gate}"
PY="${PYBIN:-/root/miniconda3/envs/llm_test/bin/python}"
export PATH="${BUNDLE}/pydeps/bin:${PY%/*}:${PATH}"
export PYTHONPATH="${BUNDLE}/pydeps:${PYTHONPATH:-}"
export ASCEND_RT_VISIBLE_DEVICES="${ASCEND_RT_VISIBLE_DEVICES:-0}"
export PROBING_GPU_BACKEND=npu
export PROBING_NPU_SOURCE=auto
export PROBING_CPU=on
export PROBING_CPU_SAMPLE_MS="${PROBING_CPU_SAMPLE_MS:-50}"
# 默认 8MiB；短测可显式钉死
export PROBING_CPU_RING_MB="${PROBING_CPU_RING_MB:-8}"

TS=$(date +%Y%m%d_%H%M%S)
ART="$OUT/artifacts/p_fix_${TS}"
mkdir -p "$ART" "$OUT/logs"
LOG="$OUT/logs/p_fix_${TS}.log"
exec > >(tee -a "$LOG") 2>&1

echo "[p-fix] start $TS art=$ART bundle=$BUNDLE ring_mb=$PROBING_CPU_RING_MB sample_ms=$PROBING_CPU_SAMPLE_MS"

busy=$(ps -eo pid,cmd | awk '
  /torchrun|megatron|pretrain_gpt|train_bench_probe/ && !/awk|p_fix|bash -lc|pgrep|defunct/ {print}
')
if [[ -n "${busy}" ]]; then
  echo "[p-fix] OWNER BUSY — yield"
  echo "$busy"
  echo "P_FIX_VERDICT=YIELD" | tee "$ART/SUMMARY.txt"
  exit 90
fi
echo IDLE

kill_workers() {
  pkill -9 -f '[p]_fix_worker' 2>/dev/null || true
}
trap kill_workers EXIT

WORKER="$ART/p_fix_worker.py"
cat >"$WORKER" <<'PY'
"""短测 worker：周期采 cpu.utilization + 可选 duration 尖刺模块。"""
import os, sys, time

print("[p_fix_worker] boot", os.getpid(), flush=True)
open(os.environ["PFIX_PID_FILE"], "w").write(str(os.getpid()))
os.environ.setdefault("PROBING", "1")
import probing  # noqa: F401
print("[p_fix_worker] probing imported", flush=True)

import torch
import torch_npu  # noqa: F401

device = torch.device("npu:0")
mode = os.environ.get("PFIX_MODE", "ring")  # ring | spike | both
sample_ms = int(os.environ.get("PROBING_CPU_SAMPLE_MS", "50"))
hold_s = float(os.environ.get("PFIX_HOLD_S", "25"))
stall_s = float(os.environ.get("INLINE_2C_FALLBACK_S", "0.6"))
spike_every = int(os.environ.get("PFIX_SPIKE_EVERY", "5"))

class SpikeMod(torch.nn.Module):
    """挂在 Sequential 内，让 torch_trace 录到 post duration 尖刺。"""

    def __init__(self, stall, every):
        super().__init__()
        self.stall = float(stall)
        self.every = max(1, int(every))
        self._i = 0
        self.bias = torch.nn.Parameter(torch.zeros(1), requires_grad=False)

    def forward(self, x):
        do = (self._i % self.every) == 0
        self._i += 1
        if do:
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < self.stall:
                time.sleep(0.01)
        return x + self.bias.to(dtype=x.dtype, device=x.device)

layers = [torch.nn.Linear(64, 128), torch.nn.ReLU()]
if mode in ("spike", "both"):
    layers.append(SpikeMod(stall_s, spike_every))
layers.append(torch.nn.Linear(128, 64))
model = torch.nn.Sequential(*layers).to(device)
opt = torch.optim.SGD(model.parameters(), lr=0.01)
x = torch.randn(16, 64, device=device)

# enable torch profiling（env 已设；API 用 probing.config.set）
if mode in ("spike", "both"):
    try:
        from probing import config as _pc
        _pc.set("probing.torch.profiling", "on,rate=1.0")
        print("[p_fix_worker] SET probing.torch.profiling=on,rate=1.0", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[p_fix_worker] set_config fail (env may apply): {exc}", flush=True)

t_end = time.time() + hold_s
step = 0
while time.time() < t_end:
    opt.zero_grad()
    y = model(x).sum()
    y.backward()
    opt.step()
    if mode in ("spike", "both") and (step % spike_every) == 0:
        print(f"[p_fix_worker] SPIKE step={step} stall_s={stall_s}", flush=True)
    elif step % 10 == 0:
        print(f"[p_fix_worker] step={step}", flush=True)
    step += 1
    time.sleep(max(0.0, sample_ms / 1000.0 * 0.5))

open(os.environ["PFIX_DONE"], "w").write(str(step))
print(f"[p_fix_worker] DONE steps={step}", flush=True)
PY

run_one() {
  local mode="$1"
  local hold="$2"
  local tag="$3"
  local data="$ART/data_${tag}"
  mkdir -p "$data"
  local pidf="$ART/${tag}.pid" donef="$ART/${tag}.done" logf="$ART/${tag}_train.log"
  rm -f "$pidf" "$donef"
  (
    export PROBING=1
    export PROBING_DATA_DIR="$data"
    export PROBING_CPU=on
    export PROBING_CPU_SAMPLE_MS
    export PROBING_CPU_RING_MB
    export PROBING_TORCH_PROFILING='on,rate=1.0'
    export PFIX_MODE="$mode"
    export PFIX_HOLD_S="$hold"
    export PFIX_PID_FILE="$pidf"
    export PFIX_DONE="$donef"
    export INLINE_2C_FALLBACK_S="${INLINE_2C_FALLBACK_S:-0.6}"
    "$PY" "$WORKER"
  ) >"$logf" 2>&1 &
  local wpid=$!
  for i in $(seq 1 60); do
    [[ -f "$pidf" ]] && break
    sleep 0.5
  done
  if [[ ! -f "$pidf" ]]; then
    echo "[p-fix] $tag FAIL no pid"; return 1
  fi
  local tpid
  tpid=$(cat "$pidf")
  echo "[p-fix] $tag worker pid=$tpid mode=$mode hold=${hold}s"
  # wait done or timeout
  local deadline=$(( $(date +%s) + hold + 90 ))
  while [[ ! -f "$donef" ]]; do
    if ! kill -0 "$tpid" 2>/dev/null; then
      echo "[p-fix] $tag worker exited early"; break
    fi
    if [[ $(date +%s) -ge $deadline ]]; then
      echo "[p-fix] $tag timeout"; kill -9 "$tpid" 2>/dev/null || true
      break
    fi
    # yield check mid-run
    busy2=$(ps -eo pid,cmd | awk '
      /torchrun|megatron|pretrain_gpt/ && !/awk|p_fix|bash -lc|pgrep|defunct/ {print}
    ')
    if [[ -n "${busy2}" ]]; then
      echo "[p-fix] OWNER RETURNED — yield"; echo "$busy2"
      kill -9 "$tpid" 2>/dev/null || true
      echo "P_FIX_VERDICT=YIELD" | tee "$ART/SUMMARY.txt"
      exit 90
    fi
    sleep 1
  done
  wait "$wpid" 2>/dev/null || true
  echo "$tpid" >"$ART/${tag}.final_pid"
}

# --- A: ring retention （hold ~25s @ 50ms → 旧 32KiB 只能 ~1s）---
run_one ring 25 ring
RING_PID=$(cat "$ART/ring.final_pid" 2>/dev/null || true)
RING_FILE=$(find "$ART/data_ring" -name 'cpu.utilization' 2>/dev/null | head -1)
echo "[p-fix] ring file=$RING_FILE"

"$PY" - "$RING_FILE" "$ART/ring_inspect.json" <<'PY'
import json, struct, sys
from pathlib import Path
path = Path(sys.argv[1]) if sys.argv[1] else None
out = Path(sys.argv[2])
res = {"ok": False, "reason": "missing"}
if path and path.is_file():
    buf = path.read_bytes()
    magic, ver, hsz, bom, ts, flags, ncols, nchunks, chunk_size, data_off = struct.unpack_from(
        "<IHHHHIIIII", buf, 0
    )
    cap = nchunks * chunk_size
    # collect process-scope ts
    cols = []
    for i in range(ncols):
        off = 64 + i * 64
        namelen = struct.unpack_from("<H", buf, off)[0]
        name = buf[off + 2 : off + 2 + namelen].decode()
        dtype, esz = struct.unpack_from("<II", buf, off + 56)
        cols.append((name, dtype))
    rows = []
    CHUNK_HDR = 40
    for c in range(nchunks):
        cs = data_off + c * chunk_size
        gen, used, row_count, *_ = struct.unpack_from("<QIIIIqq", buf, cs)
        if not used or not row_count:
            continue
        pos = cs + CHUNK_HDR
        end = cs + CHUNK_HDR + used
        for _ in range(row_count):
            if pos + 4 > end:
                break
            row_len = struct.unpack_from("<I", buf, pos)[0]
            data_off_row = pos + 4
            row_end = data_off_row + row_len
            if row_end > end:
                break
            data = buf[data_off_row:row_end]
            rec = {}
            p = 0
            for name, dtype in cols:
                if dtype == 1:
                    rec[name] = data[p]; p += 1
                elif dtype in (2, 7, 4):
                    fmt = {2: "<i", 7: "<I", 4: "<f"}[dtype]
                    rec[name] = struct.unpack_from(fmt, data, p)[0]; p += 4
                elif dtype in (3, 5, 6):
                    fmt = {3: "<q", 5: "<d", 6: "<Q"}[dtype]
                    rec[name] = struct.unpack_from(fmt, data, p)[0]; p += 8
                elif dtype in (8, 9):
                    raw = struct.unpack_from("<i", buf, data_off_row + p)[0]
                    if raw < 0:
                        ref = cs + (-raw)
                        ln = struct.unpack_from("<I", buf, ref)[0]
                        payload = buf[ref + 4 : ref + 4 + ln]; p += 4
                    else:
                        payload = buf[data_off_row + p + 4 : data_off_row + p + 4 + raw]
                        p += 4 + raw
                    rec[name] = payload.decode("utf-8", "replace") if dtype == 8 else payload
                else:
                    break
            rows.append(rec)
            pos = row_end
    proc = [r for r in rows if r.get("scope") == "process"]
    tss = [int(r["ts"]) for r in proc if "ts" in r]
    span_s = (max(tss) - min(tss)) / 1e6 if tss else 0.0
    # PASS: capacity ≥4MiB AND span ≥10s（旧环 ~1s）
    ok = cap >= 4 * 1024 * 1024 and span_s >= 10.0
    res = {
        "ok": ok,
        "path": str(path),
        "nchunks": nchunks,
        "chunk_size": chunk_size,
        "capacity_bytes": cap,
        "capacity_mb": round(cap / (1024 * 1024), 3),
        "n_rows": len(rows),
        "n_process": len(proc),
        "span_s": round(span_s, 3),
        "legacy_32kib_would_span_s": 1.0,
        "criterion": "capacity>=4MiB AND process_ts_span>=10s",
    }
out.write_text(json.dumps(res, indent=2), encoding="utf-8")
print(json.dumps(res, indent=2))
sys.exit(0 if res.get("ok") else 1)
PY
RING_RC=$?

# --- B: spike duration（短 hold，profiling on）---
run_one spike 12 spike
SPIKE_PID=$(cat "$ART/spike.final_pid" 2>/dev/null || true)

"$PY" - "$ART" <<'PY'
import json, os, re, sys
from pathlib import Path
from statistics import median
art = Path(sys.argv[1])
# Prefer live query dump if we saved; else parse MEMT torch_trace
tt = None
for p in (art / "data_spike").rglob("python.torch_trace"):
    tt = p
    break
res = {"ok": False, "reason": "no_torch_trace", "path": str(tt) if tt else None}
SPIKE_ABS = 0.40
SPIKE_RATIO = 3.0

def read_memt(path: Path):
    import struct
    buf = path.read_bytes()
    magic, ver, hsz, bom, ts, flags, ncols, nchunks, chunk_size, data_off = struct.unpack_from(
        "<IHHHHIIIII", buf, 0
    )
    cols = []
    for i in range(ncols):
        off = 64 + i * 64
        namelen = struct.unpack_from("<H", buf, off)[0]
        name = buf[off + 2 : off + 2 + namelen].decode()
        dtype, esz = struct.unpack_from("<II", buf, off + 56)
        cols.append((name, dtype))
    rows = []
    CHUNK_HDR = 40
    for c in range(nchunks):
        cs = data_off + c * chunk_size
        gen, used, row_count, *_ = struct.unpack_from("<QIIIIqq", buf, cs)
        if not used or not row_count:
            continue
        pos = cs + CHUNK_HDR
        end = cs + CHUNK_HDR + used
        for _ in range(row_count):
            if pos + 4 > end:
                break
            row_len = struct.unpack_from("<I", buf, pos)[0]
            data_off_row = pos + 4
            row_end = data_off_row + row_len
            if row_end > end:
                break
            data = buf[data_off_row:row_end]
            rec = {}
            p = 0
            for name, dtype in cols:
                if dtype == 1:
                    rec[name] = data[p]; p += 1
                elif dtype in (2, 7, 4):
                    fmt = {2: "<i", 7: "<I", 4: "<f"}[dtype]
                    rec[name] = struct.unpack_from(fmt, data, p)[0]; p += 4
                elif dtype in (3, 5, 6):
                    fmt = {3: "<q", 5: "<d", 6: "<Q"}[dtype]
                    rec[name] = struct.unpack_from(fmt, data, p)[0]; p += 8
                elif dtype in (8, 9):
                    raw = struct.unpack_from("<i", buf, data_off_row + p)[0]
                    if raw < 0:
                        ref = cs + (-raw)
                        ln = struct.unpack_from("<I", buf, ref)[0]
                        payload = buf[ref + 4 : ref + 4 + ln]; p += 4
                    else:
                        payload = buf[data_off_row + p + 4 : data_off_row + p + 4 + raw]
                        p += 4 + raw
                    rec[name] = payload.decode("utf-8", "replace") if dtype == 8 else payload
                else:
                    break
            rows.append(rec)
            pos = row_end
    return rows

if tt and tt.is_file():
    rows = read_memt(tt)
    durs = []
    for r in rows:
        stage = str(r.get("stage") or "")
        if "post" not in stage:
            continue
        try:
            d = float(r.get("duration") or 0.0)
        except Exception:
            continue
        if d > 0:
            durs.append((d, str(r.get("module") or ""), int(r.get("local_step") or -1)))
    if not durs:
        res = {"ok": False, "reason": "no_post_duration", "n_rows": len(rows), "path": str(tt)}
    else:
        vals = [d for d, _, _ in durs]
        med = median(vals)
        spikes = [x for x in durs if x[0] >= SPIKE_ABS and (med <= 0 or x[0] / med >= SPIKE_RATIO)]
        top = max(durs, key=lambda x: x[0])
        res = {
            "ok": len(spikes) >= 1,
            "path": str(tt),
            "n_post_durs": len(durs),
            "median_s": round(med, 4),
            "top_dur_s": round(top[0], 4),
            "top_module": top[1][:80],
            "top_step": top[2],
            "n_spikes": len(spikes),
            "criterion": f"post_duration>={SPIKE_ABS}s AND >={SPIKE_RATIO}x median",
        }
        if spikes:
            s = max(spikes, key=lambda x: x[0])
            res["spike"] = {"dur_s": round(s[0], 4), "module": s[1][:80], "step": s[2]}

# also check train log marker
log = art / "spike_train.log"
if log.is_file():
    text = log.read_text(encoding="utf-8", errors="replace")
    res["log_has_duration_spike"] = "INLINE_2C_DURATION_SPIKE" in text or "SPIKE step=" in text

(art / "spike_inspect.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
print(json.dumps(res, indent=2))
sys.exit(0 if res.get("ok") else 1)
PY
SPIKE_RC=$?

{
  echo "P_FIX_RING_RC=$RING_RC"
  echo "P_FIX_SPIKE_RC=$SPIKE_RC"
  if [[ $RING_RC -eq 0 && $SPIKE_RC -eq 0 ]]; then
    echo "P_FIX_VERDICT=PASS"
  else
    echo "P_FIX_VERDICT=FAIL"
  fi
  echo "ART=$ART"
} | tee "$ART/SUMMARY.txt"

echo "[p-fix] done verdict=$(grep P_FIX_VERDICT "$ART/SUMMARY.txt")"
exit $([[ $RING_RC -eq 0 && $SPIKE_RC -eq 0 ]] && echo 0 || echo 1)
