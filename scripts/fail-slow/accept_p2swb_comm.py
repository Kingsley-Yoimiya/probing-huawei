#!/usr/bin/env python3
"""P2-SW-B Loud 验收：主证 C1/C0 comm_ms 中位（measure 100–300）。

对齐沐曦：step 常 <1.15 不自动 FAIL；comm_ratio≥min_ratio → PASS。
退出码：0=达标；1=未达标 / 数据不足；2=用法错误。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


def find_rank0(case_root: Path, cfg: str) -> Path | None:
    hits = sorted(case_root.glob(f"by_pod/*/round_1/{cfg}/ranks/rank_0000.jsonl"))
    return hits[0] if hits else None


def median_metric(path: Path, key: str, lo: int = 100, hi: int = 300) -> float | None:
    xs: list[float] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            s = o.get("step")
            if s is None or not (lo <= int(s) <= hi):
                continue
            if key in o:
                xs.append(float(o[key]))
    if not xs:
        return None
    return float(statistics.median(xs))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result-root", required=True)
    ap.add_argument("--case", default="P2-SW-B")
    ap.add_argument("--min-ratio", type=float, default=1.3)
    # hold_exec 兼容：accept_loud 传 --min-ratio；spike 传 --min-median-ratio
    ap.add_argument("--min-median-ratio", type=float, default=None)
    ap.add_argument("--lo", type=int, default=100)
    ap.add_argument("--hi", type=int, default=300)
    ap.add_argument("--configs", default="C0_baseline,C1_inject_none,C2_probing")
    ap.add_argument("--write-md", default="")
    ap.add_argument("--ineffective-below", type=float, default=1.1)
    args = ap.parse_args()

    min_ratio = (
        args.min_median_ratio if args.min_median_ratio is not None else args.min_ratio
    )
    root = Path(args.result_root) / args.case
    cfgs = [c.strip() for c in args.configs.split(",") if c.strip()]

    step_meds: dict[str, float | None] = {}
    comm_meds: dict[str, float | None] = {}
    for cfg in cfgs:
        p = find_rank0(root, cfg)
        step_meds[cfg] = median_metric(p, "step_ms", args.lo, args.hi) if p else None
        comm_meds[cfg] = median_metric(p, "comm_ms", args.lo, args.hi) if p else None

    c0s, c1s = step_meds.get("C0_baseline"), step_meds.get("C1_inject_none")
    c0c, c1c = comm_meds.get("C0_baseline"), comm_meds.get("C1_inject_none")
    step_r = (c1s / c0s) if (c0s and c1s and c0s > 0) else None
    comm_r = (c1c / c0c) if (c0c and c1c and c0c > 0) else None

    if comm_r is None:
        verdict = "DATA_MISSING"
        ok = False
    elif comm_r >= min_ratio:
        verdict = "PASS"
        ok = True
    elif comm_r < args.ineffective_below:
        verdict = "injection_ineffective"
        ok = False
    else:
        verdict = "FAIL_WEAK"
        ok = False

    inj_logs = list(root.glob("by_pod/*/round_1/C1_inject_none/injection.log"))
    inj = "no_log"
    if inj_logs:
        text = inj_logs[0].read_text(errors="replace")
        if "SIDECAR_START" in text or "hccl_algo" in text.lower():
            inj = "hccl_algo"
        else:
            inj = "log_present"

    lines = [
        f"# Loud acceptance: {args.case} (comm_ms primary)",
        "",
        f"- window: measure step [{args.lo}, {args.hi}] rank0",
        f"- threshold C1/C0 **comm_ms** ≥ **{min_ratio}** (step 不强制)",
        f"- injection.log: `{inj}`",
        f"- verdict: **{verdict}**",
        "",
        "| config | median step_ms | median comm_ms | vs C0 (step/comm) |",
        "|---|---:|---:|---|",
    ]
    for cfg in cfgs:
        sm, cm = step_meds.get(cfg), comm_meds.get(cfg)
        vs = ""
        if cfg != "C0_baseline" and c0s and sm:
            vs += f"step={(sm / c0s):.3f}"
        if cfg != "C0_baseline" and c0c and cm:
            vs += ("; " if vs else "") + f"comm={(cm / c0c):.3f}"
        lines.append(
            f"| {cfg} | {sm if sm is not None else 'NA'} | "
            f"{cm if cm is not None else 'NA'} | {vs or '-'} |"
        )
    lines += [
        "",
        f"- step_ratio={step_r}",
        f"- **comm_ratio={comm_r}** ← 主证",
    ]
    md = "\n".join(lines) + "\n"
    print(md)
    if args.write_md:
        Path(args.write_md).write_text(md)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
