#!/usr/bin/env python3
"""P3-SW-C 监控进程泄漏 sidecar（host_bound Loud）。

叙事：metric collector 常驻抢核 + 主进程轻量 RSS 泄漏。
Ascend yysong（nproc≈320）：纯 Python busy×128 咬空（pilot@132200 C1/C0=0.96）；
对齐 P3-EXT-A，CPU 压力走 stress-ng --cpu nproc --cpu-load，外加本进程泄漏线程。

环境变量：
  SIDECAR_8C_CPU_N       stress-ng --cpu N（默认 nproc）
  SIDECAR_8C_CPU_LOAD    stress-ng --cpu-load（默认 90）
  SIDECAR_8C_MB          每次泄漏 MB（默认 1）
  SIDECAR_8C_LEAK_EVERY  泄漏间隔秒（默认 1.0）
  SIDECAR_8C_MAX_CHUNKS  最大泄漏块数（默认 64）
  SIDECAR_8C_NO_STRESS   若=1 则退回纯 Python busy workers（调试）
  SIDECAR_8C_WORKERS     仅 NO_STRESS=1 时用
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import shutil
import signal
import subprocess
import sys
import threading
import time


def _busy_worker(t_end: float) -> None:
    while time.time() < t_end:
        busy_end = time.perf_counter() + 0.02
        acc = 0
        while time.perf_counter() < busy_end:
            acc += sum(range(80))


def case_8c(seconds: float) -> None:
    nproc = os.cpu_count() or 64
    _cpu_n_raw = (os.environ.get("SIDECAR_8C_CPU_N") or "").strip()
    cpu_n = int(_cpu_n_raw) if _cpu_n_raw else nproc
    _load_raw = (os.environ.get("SIDECAR_8C_CPU_LOAD") or "").strip()
    cpu_load = int(_load_raw) if _load_raw else 90
    chunk_mb = int(os.environ.get("SIDECAR_8C_MB", "1"))
    leak_every = float(os.environ.get("SIDECAR_8C_LEAK_EVERY", "1.0"))
    max_leak_chunks = int(os.environ.get("SIDECAR_8C_MAX_CHUNKS", "64"))
    no_stress = os.environ.get("SIDECAR_8C_NO_STRESS", "0") == "1"
    use_stress = (not no_stress) and bool(shutil.which("stress-ng"))

    print("SIDECAR_START kind=8c_loud", flush=True)
    print(
        f"SIDECAR_8C_START: monitoring overhead "
        f"stress={'yes' if use_stress else 'no'} cpu_n={cpu_n} cpu_load={cpu_load} "
        f"chunk_mb={chunk_mb}/{leak_every}s max_chunks={max_leak_chunks} nproc={nproc}",
        flush=True,
    )

    stopping = {"v": False}
    stress_proc: subprocess.Popen | None = None
    procs: list[mp.Process] = []

    def _on_signal(signum, _frame):
        stopping["v"] = True
        print(f"SIDECAR_SIGNAL signum={signum}", flush=True)
        if stress_proc is not None and stress_proc.poll() is None:
            stress_proc.terminate()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    t_end = time.time() + seconds

    if use_stress:
        stress_proc = subprocess.Popen(
            [
                "stress-ng",
                "--cpu",
                str(cpu_n),
                "--cpu-load",
                str(cpu_load),
                "--timeout",
                f"{int(seconds) + 30}s",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        print(f"SIDECAR_8C_STRESS pid={stress_proc.pid}", flush=True)
    else:
        n_workers = int(os.environ.get("SIDECAR_8C_WORKERS", str(max(64, cpu_n))))
        for _ in range(n_workers):
            p = mp.Process(target=_busy_worker, args=(t_end,), daemon=True)
            p.start()
            procs.append(p)
        print(f"SIDECAR_8C_BUSY_WORKERS n={n_workers}", flush=True)

    leaked_threads = []
    leaked_data = []

    def dummy_collector():
        while time.time() < t_end and not stopping["v"]:
            busy_end = time.perf_counter() + 0.01
            while time.perf_counter() < busy_end:
                _ = sum(range(50))
            time.sleep(0.01)

    while time.time() < t_end and not stopping["v"]:
        t = threading.Thread(target=dummy_collector, daemon=True)
        t.start()
        leaked_threads.append(t)
        if len(leaked_data) < max_leak_chunks:
            leaked_data.append(bytearray(chunk_mb * 1024 * 1024))
        time.sleep(leak_every)

    if stress_proc is not None and stress_proc.poll() is None:
        stress_proc.terminate()
        try:
            stress_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            stress_proc.kill()
    for p in procs:
        p.join(timeout=1)
        if p.is_alive():
            p.terminate()

    print(
        f"SIDECAR_8C_STOP threads={len(leaked_threads)} "
        f"mem≈{len(leaked_data) * chunk_mb}MB_est",
        flush=True,
    )
    print("SIDECAR_STOP kind=8c_loud", flush=True)


def main() -> None:
    try:
        mp.set_start_method("fork")
    except RuntimeError:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="8c")
    ap.add_argument("--seconds", type=float, default=900)
    ap.add_argument("--warmup-seconds", type=float, default=0.0)
    args = ap.parse_args()
    if args.case not in ("8c", "sidecar_8c"):
        print(f"WARN: 8c sidecar got case={args.case}", flush=True)
    if args.warmup_seconds > 0:
        print(f"SIDECAR_WARMUP kind=8c s={args.warmup_seconds}", flush=True)
        time.sleep(args.warmup_seconds)
    case_8c(args.seconds)


if __name__ == "__main__":
    main()
