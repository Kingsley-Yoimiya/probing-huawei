#!/usr/bin/env python3
"""Analyze adjacent-control ratios from overhead_probe.py."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from statistics import mean, median


def summarize(values: list[float]) -> dict[str, float | list[float]]:
    return {
        "n": len(values),
        "median": median(values) if values else 0.0,
        "mean": mean(values) if values else 0.0,
        "min": min(values) if values else 0.0,
        "max": max(values) if values else 0.0,
        "values": values,
    }


def incident_stats(step_to_ms: dict[int, float]) -> tuple[int, float]:
    """Merge adjacent anomalous steps and return count plus critical-path seconds."""
    groups: list[list[float]] = []
    current: list[float] = []
    previous: int | None = None
    for step in sorted(step_to_ms):
        if previous is None or step == previous + 1:
            current.append(step_to_ms[step])
        else:
            groups.append(current)
            current = [step_to_ms[step]]
        previous = step
    if current:
        groups.append(current)
    return len(groups), sum(max(group) for group in groups) / 1000.0


def adjacent_ratios(blocks: list[dict], mode: str, value) -> list[float]:
    ratios = []
    for index, block in enumerate(blocks):
        if block["mode"] != mode:
            continue
        if index == 0 or index + 1 >= len(blocks):
            raise ValueError(f"treatment block {index} is not bracketed")
        left, right = blocks[index - 1], blocks[index + 1]
        if left["mode"] != "control" or right["mode"] != "control":
            raise ValueError(f"treatment block {index} lacks adjacent controls")
        expected = math.sqrt(value(left) * value(right))
        ratios.append(value(block) / expected)
    return ratios


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    blocks = json.loads((run_dir / "blocks.json").read_text())

    anomaly_keys: dict[tuple[int, int], float] = {}
    for path in sorted(run_dir.glob("rank_*/anomalies.json")):
        for row in json.loads(path.read_text()):
            key = (int(row["block_index"]), int(row["global_step"]))
            anomaly_keys[key] = max(anomaly_keys.get(key, 0.0), float(row["outer_ms"]))
    anomaly_modes = Counter(blocks[index]["mode"] for index, _ in anomaly_keys)

    anomaly_by_block: dict[int, dict[int, float]] = {
        index: {} for index in range(len(blocks))
    }
    for (block_index, step), duration_ms in anomaly_keys.items():
        anomaly_by_block[block_index][step] = duration_ms
    for index, block in enumerate(blocks):
        count, blocked_s = incident_stats(anomaly_by_block[index])
        block["incident_count"] = count
        block["incident_blocked_s"] = blocked_s
        adjusted_elapsed = max(1e-9, block["elapsed_s"] - blocked_s)
        block["stall_adjusted_steps_per_s"] = block["steps"] / adjusted_elapsed

    treatment_modes = sorted({block["mode"] for block in blocks} - {"control"})
    throughput_ratios = {
        mode: adjacent_ratios(blocks, mode, lambda block: block["steps_per_s"])
        for mode in treatment_modes
    }
    adjusted_ratios = {
        mode: adjacent_ratios(
            blocks, mode, lambda block: block["stall_adjusted_steps_per_s"]
        )
        for mode in treatment_modes
    }
    latency_ratios = {
        field: {
            mode: adjacent_ratios(
                blocks, mode, lambda block, field=field: block["clean_outer_ms"][field]
            )
            for mode in treatment_modes
        }
        for field in ("mean", "p50", "p90", "p99")
    }

    result = {
        "run_dir": str(run_dir),
        "n_blocks": len(blocks),
        "plan": [block["mode"] for block in blocks],
        "control_steps_per_s": summarize(
            [block["steps_per_s"] for block in blocks if block["mode"] == "control"]
        ),
        "throughput_ratio_to_adjacent_control": {
            mode: summarize(values) for mode, values in throughput_ratios.items()
        },
        "throughput_overhead_pct": {
            mode: summarize([(1.0 - value) * 100.0 for value in values])
            for mode, values in throughput_ratios.items()
        },
        "stall_adjusted_throughput_overhead_pct": {
            mode: summarize([(1.0 - value) * 100.0 for value in values])
            for mode, values in adjusted_ratios.items()
        },
        "clean_latency_ratio_to_adjacent_control": {
            field: {mode: summarize(values) for mode, values in by_mode.items()}
            for field, by_mode in latency_ratios.items()
        },
        "unique_anomaly_steps": len(anomaly_keys),
        "anomaly_steps_by_mode": dict(anomaly_modes),
        "blocks": blocks,
    }
    (run_dir / "overhead_analysis.json").write_text(json.dumps(result, indent=2, sort_keys=True))

    lines = [
        "# Stall observer overhead summary",
        "",
        f"- blocks={len(blocks)}，unique anomaly steps={len(anomaly_keys)}，"
        f"by mode={dict(anomaly_modes)}",
        "- treatment throughput is divided by the geometric mean of its two adjacent controls",
        "",
        "| mode | repeats | raw throughput overhead | stall-adjusted overhead | clean p50 | clean mean |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for mode in treatment_modes:
        overhead = result["throughput_overhead_pct"][mode]
        adjusted = result["stall_adjusted_throughput_overhead_pct"][mode]
        latency = result["clean_latency_ratio_to_adjacent_control"]["p50"][mode]
        clean_mean = result["clean_latency_ratio_to_adjacent_control"]["mean"][mode]
        lines.append(
            f"| {mode} | {overhead['n']} | {overhead['median']:.3f}% | "
            f"{adjusted['median']:.3f}% | {(latency['median'] - 1) * 100:.3f}% | "
            f"{(clean_mean['median'] - 1) * 100:.3f}% |"
        )
    (run_dir / "OVERHEAD_SUMMARY.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
