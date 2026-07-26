#!/usr/bin/env python3
"""Online SET→live / live→enough latency probe (runs inside hold pod).

Env:
  OUT, PID, SET_L, T0_MS, W_STAR, TT_FLOOR, PROBE_MAX_S
Writes: $OUT/set_latency_probe.log
"""
from __future__ import annotations

import os
import subprocess
import time

out = os.environ["OUT"]
pid = os.environ["PID"]
set_l = int(os.environ.get("SET_L") or -1)
t0 = int(os.environ.get("T0_MS") or 0)
w_star = int(os.environ.get("W_STAR") or 100)
tt_floor = int(os.environ.get("TT_FLOOR") or 800)
max_s = int(os.environ.get("PROBE_MAX_S") or 600)
logp = os.path.join(out, "set_latency_probe.log")
jsonl = os.path.join(out, "ranks", "rank_0000.jsonl")


def log(msg: str) -> None:
    with open(logp, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def lines() -> int:
    try:
        with open(jsonl, encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def q_tt():
    try:
        r = subprocess.run(
            [
                "timeout",
                "8",
                "probing",
                "-t",
                pid,
                "query",
                "SELECT COUNT(*) AS n, MIN(global_step) AS gmin, MAX(global_step) AS gmax FROM python.torch_trace",
            ],
            capture_output=True,
            text=True,
            timeout=12,
        )
        text = (r.stdout or "") + "\n" + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 0, -1, -1, str(e)
    for line in text.splitlines():
        if "│" not in line:
            continue
        cells = [c.strip() for c in line.split("│") if c.strip()]
        if len(cells) < 3:
            continue
        if cells[0] in ("n", "─") or set(cells[0]) <= set("─├┼┤"):
            continue
        try:
            n = int(float(cells[0]))
            gmin = int(float(cells[1])) if cells[1] not in ("", "None") else -1
            gmax = int(float(cells[2])) if cells[2] not in ("", "None") else -1
            return n, gmin, gmax, None
        except ValueError:
            continue
    return 0, -1, -1, "parse_fail"


with open(logp, "w", encoding="utf-8") as f:
    f.write(
        f"PROBE_BEGIN ts={time.strftime('%Y-%m-%dT%H:%M:%S%z')} set_l={set_l} pid={pid} "
        f"w_star={w_star} tt_floor={tt_floor}\n"
    )

live_l = live_gmin = None
enough_tt_l = enough_w_l = None
deadline = time.time() + max_s
while time.time() < deadline:
    L = lines()
    now = int(time.time() * 1000)
    n, gmin, gmax, err = q_tt()
    log(
        f"PROBE_TICK t_ms={now} L={L} n={n} gmin={gmin} gmax={gmax}"
        + (f" err={err}" if err else "")
    )
    if live_l is None and n > 0:
        live_l, live_gmin = L, gmin
        log(f"LATENCY_LIVE L={L} gmin={gmin} gmax={gmax} n={n} t_ms={now}")
        if set_l >= 0:
            log(f"LATENCY_SET_TO_LIVE_STEPS={L - set_l}")
            if gmin >= 0:
                log(f"LATENCY_SET_TO_LIVE_GSTEPS={gmin - set_l}")
        if t0:
            log(f"LATENCY_SET_TO_LIVE_MS={now - t0}")
    if live_l is not None and enough_tt_l is None and n >= tt_floor:
        enough_tt_l = L
        log(f"LATENCY_ENOUGH_TT L={L} n={n} floor={tt_floor} t_ms={now}")
        log(f"LATENCY_LIVE_TO_ENOUGH_TT_STEPS={L - live_l}")
    if (
        live_l is not None
        and enough_w_l is None
        and gmax >= 0
        and live_gmin is not None
        and live_gmin >= 0
    ):
        span = gmax - live_gmin + 1
        if span >= w_star:
            enough_w_l = L
            log(
                f"LATENCY_ENOUGH_WSTAR L={L} gmax={gmax} span={span} "
                f"w_star={w_star} t_ms={now}"
            )
            log(f"LATENCY_LIVE_TO_ENOUGH_W_STEPS={L - live_l}")
    if live_l is not None and enough_tt_l is not None and enough_w_l is not None:
        log("PROBE_DONE both_enough")
        break
    if os.path.exists(os.path.join(out, "node_0.done")) or os.path.exists(
        os.path.join(out, "node_0.fail")
    ):
        log("PROBE_STOP training_ended")
        break
    time.sleep(2)

log(f"PROBE_END live_l={live_l} enough_tt_l={enough_tt_l} enough_w_l={enough_w_l}")
