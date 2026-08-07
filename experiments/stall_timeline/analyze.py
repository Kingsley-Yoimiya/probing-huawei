#!/usr/bin/env python3
"""Analyze host/device stall timeline runs."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("run_dir")
    p.add_argument("--hole-ms", type=float, default=200.0)
    return p.parse_args()


def _median(xs: Iterable[float]) -> float:
    vals = list(xs)
    return statistics.median(vals) if vals else 0.0


def _pct(n: int, d: int) -> float:
    return 100.0 * n / d if d else 0.0


def _quantile(xs: Iterable[float], q: float) -> float:
    vals = sorted(xs)
    if not vals:
        return 0.0
    pos = (len(vals) - 1) * min(1.0, max(0.0, q))
    lo = int(pos)
    hi = min(len(vals) - 1, lo + 1)
    frac = pos - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def main() -> None:
    args = parse_args()
    root = Path(args.run_dir)
    rows = []
    metas = []
    for p in sorted(root.glob("rank_*/timeline.jsonl")):
        rows.extend(json.loads(line) for line in p.read_text().splitlines() if line.strip())
        mp = p.parent / "meta.json"
        if mp.exists():
            metas.append(json.loads(mp.read_text()))
    if not rows:
        raise SystemExit(f"no timeline rows under {root}")

    by_step = defaultdict(list)
    by_rank = defaultdict(list)
    for row in rows:
        by_step[int(row["step"])].append(row)
        by_rank[int(row["rank"])].append(row)

    inject_rank = int(metas[0].get("inject_rank", -1)) if metas else -1
    logical_world = int(metas[0].get("world_size", len(by_rank))) if metas else len(by_rank)
    logical_steps = (
        int(metas[0].get("completed_steps", metas[0].get("iters", len(by_step))))
        if metas
        else len(by_step)
    )
    injection_steps = sorted(
        {int(r["step"]) for r in rows if int(r.get("injected", 0)) == 1}
    )
    sampled_injection_steps = []
    device_top1_hits = 0
    host_top1_hits = 0
    peer_propagated_steps = 0
    host_cluster_hits = 0
    device_origin_hits = 0

    for step in injection_steps:
        sr = by_step[step]
        victim = next((r for r in sr if int(r["rank"]) == inject_rank), None)
        if not victim:
            continue
        if float(victim.get("host_iter_ms", 0.0)) >= args.hole_ms:
            host_cluster_hits += 1
        if int(victim.get("sampled", 0)):
            sampled_injection_steps.append(step)
            sampled = [r for r in sr if int(r.get("sampled", 0)) and float(r.get("device_compute_ms", -1)) >= 0]
            if sampled:
                top = max(sampled, key=lambda r: float(r["device_compute_ms"]))
                device_top1_hits += int(int(top["rank"]) == inject_rank)
            if float(victim.get("device_compute_ms", -1)) >= args.hole_ms:
                device_origin_hits += 1
        top_host = max(sr, key=lambda r: float(r.get("host_sync_ms", 0.0)))
        host_top1_hits += int(int(top_host["rank"]) == inject_rank)
        peers = [r for r in sr if int(r["rank"]) != inject_rank]
        if peers and sum(float(r.get("host_iter_ms", 0.0)) >= args.hole_ms for r in peers) >= len(peers) / 2:
            peer_propagated_steps += 1

    clean = [r for r in rows if int(r.get("injected", 0)) == 0]
    host_anomaly_steps = sorted(
        step
        for step, step_rows in by_step.items()
        if any(float(r.get("host_iter_ms", 0.0)) >= args.hole_ms for r in step_rows)
    )
    device_anomaly_steps = []
    device_longest_sampled = defaultdict(int)
    for step in host_anomaly_steps:
        sampled = [
            r
            for r in by_step[step]
            if int(r.get("sampled", 0)) and float(r.get("device_phase_ms", r.get("device_compute_ms", -1.0))) >= 0
        ]
        if not sampled:
            continue
        top = max(
            sampled,
            key=lambda r: float(r.get("device_phase_ms", r.get("device_compute_ms", -1.0))),
        )
        if float(top.get("device_phase_ms", top.get("device_compute_ms", -1.0))) >= args.hole_ms:
            device_anomaly_steps.append(step)
            # A long collective event on a sampled peer proves device-visible
            # propagation, but does not by itself make that peer the origin.
            device_longest_sampled[str(int(top["rank"]))] += 1

    all_ranks = set(by_rank)
    step_shapes = {}
    step_host_candidates = {}
    for step in host_anomaly_steps:
        slow_ranks = {
            int(r["rank"])
            for r in by_step[step]
            if float(r.get("host_iter_ms", 0.0)) >= args.hole_ms
        }
        if len(slow_ranks) == 1:
            step_shapes[step] = "singleton"
            step_host_candidates[step] = next(iter(slow_ranks))
        elif len(slow_ranks) == len(all_ranks) - 1:
            step_shapes[step] = "all_but_one"
            step_host_candidates[step] = next(iter(all_ranks - slow_ranks))
        elif len(slow_ranks) == len(all_ranks):
            step_shapes[step] = "global"
        else:
            step_shapes[step] = "partial"

    incident_steps = []
    for step in host_anomaly_steps:
        if not incident_steps or step != incident_steps[-1][-1] + 1:
            incident_steps.append([step])
        else:
            incident_steps[-1].append(step)
    incidents = []
    incident_candidate_counts = Counter()
    for steps in incident_steps:
        candidates = {step_host_candidates[s] for s in steps if s in step_host_candidates}
        candidate = next(iter(candidates)) if len(candidates) == 1 else None
        if candidate is not None:
            incident_candidate_counts[str(candidate)] += 1
        incidents.append(
            {
                "start_step": steps[0],
                "end_step": steps[-1],
                "shapes": [step_shapes[s] for s in steps],
                "host_candidate_rank": candidate,
                "max_host_iter_ms": max(
                    float(r.get("host_iter_ms", 0.0)) for s in steps for r in by_step[s]
                ),
                "device_visible": any(s in device_anomaly_steps for s in steps),
            }
        )
    row_span_s = (
        (max(int(r["ts_ns"]) for r in rows) - min(int(r["ts_ns"]) for r in rows)) / 1e9
        if len(rows) > 1
        else 0.0
    )
    completed_spans = [float(m["completed_span_s"]) for m in metas if "completed_span_s" in m]
    observed_span_s = _median(completed_spans) if completed_spans else row_span_s
    incident_blocked_ms_sum = sum(float(x["max_host_iter_ms"]) for x in incidents)
    severity_ms = [float(x["max_host_iter_ms"]) for x in incidents]
    severity_bins = Counter()
    for value in severity_ms:
        if value < 300:
            severity_bins["200-300"] += 1
        elif value < 500:
            severity_bins["300-500"] += 1
        elif value < 650:
            severity_bins["500-650"] += 1
        elif value < 750:
            severity_bins["650-750"] += 1
        else:
            severity_bins[">=750"] += 1
    incident_step_gaps = [
        b["start_step"] - a["start_step"] for a, b in zip(incidents, incidents[1:])
    ]
    incident_wall_times = [
        min(int(r["ts_ns"]) for r in by_step[x["start_step"]]) / 1e9 for x in incidents
    ]
    incident_wall_gaps = [b - a for a, b in zip(incident_wall_times, incident_wall_times[1:])]

    z95 = 1.96
    incident_p = len(incidents) / logical_steps if logical_steps else 0.0
    wilson_den = 1.0 + z95 * z95 / logical_steps if logical_steps else 1.0
    wilson_center = (
        (incident_p + z95 * z95 / (2.0 * logical_steps)) / wilson_den
        if logical_steps
        else 0.0
    )
    wilson_half = (
        z95
        * math.sqrt(
            incident_p * (1.0 - incident_p) / logical_steps
            + z95 * z95 / (4.0 * logical_steps * logical_steps)
        )
        / wilson_den
        if logical_steps
        else 0.0
    )

    probing_roundtrip_exact_ranks = 0
    probing_rows = 0
    for rank, rank_rows in by_rank.items():
        probing_path = root / f"rank_{rank:04d}" / "probing_timeline.jsonl"
        if not probing_path.exists():
            continue
        query_rows = [
            json.loads(line) for line in probing_path.read_text().splitlines() if line.strip()
        ]
        probing_rows += len(query_rows)
        raw_keys = {(int(r["rank"]), int(r["step"])) for r in rank_rows}
        query_keys = {(int(r["rank"]), int(r["step"])) for r in query_rows}
        if len(rank_rows) == len(query_rows) and raw_keys == query_keys:
            probing_roundtrip_exact_ranks += 1
    record_mode = str(metas[0].get("record_mode", "full")) if metas else "full"
    if record_mode == "anomaly" and metas:
        scheme = str(metas[0].get("scheme", "host"))
        if scheme == "sentinel":
            sampling_coverage_pct = 100.0
        elif scheme == "host":
            sampling_coverage_pct = 0.0
        elif scheme == "rotate":
            sampling_coverage_pct = _pct(
                int(metas[0].get("sample_ranks", 1)), logical_world
            )
        else:
            sampling_coverage_pct = 100.0 * float(metas[0].get("sample_rate", 0.0))
    else:
        sampling_coverage_pct = _pct(
            sum(int(r.get("sampled", 0)) for r in rows), logical_steps * logical_world
        )

    result = {
        "run_dir": str(root),
        "scheme": rows[0].get("scheme"),
        "world_size": logical_world,
        "n_rows": len(rows),
        "n_steps": logical_steps,
        "inject_rank": inject_rank,
        "n_injection_steps": len(injection_steps),
        "n_host_anomaly_steps": len(host_anomaly_steps),
        "n_host_anomaly_incidents": len(incidents),
        "n_device_visible_anomaly_steps": len(device_anomaly_steps),
        "n_host_only_anomaly_steps": len(host_anomaly_steps) - len(device_anomaly_steps),
        "device_longest_sampled_rank_counts": dict(
            sorted(device_longest_sampled.items(), key=lambda kv: int(kv[0]))
        ),
        "host_anomaly_shape_counts": dict(Counter(step_shapes.values())),
        "host_incident_candidate_counts": dict(incident_candidate_counts),
        "host_anomaly_incidents": incidents,
        "observed_span_s": observed_span_s,
        "row_timestamp_span_s": row_span_s,
        "incident_blocked_ms_sum": incident_blocked_ms_sum,
        "incident_time_fraction_pct": _pct(incident_blocked_ms_sum, observed_span_s * 1000.0),
        "incident_rate_per_min": 60.0 * len(incidents) / observed_span_s if observed_span_s else 0.0,
        "incident_rate_per_million_syncs": 1e6 * len(incidents) / logical_steps,
        "incident_rate_per_million_syncs_ci95_wilson": [
            1e6 * (wilson_center - wilson_half),
            1e6 * (wilson_center + wilson_half),
        ],
        "incident_severity_ms": {
            "min": min(severity_ms) if severity_ms else 0.0,
            "p25": _quantile(severity_ms, 0.25),
            "p50": _quantile(severity_ms, 0.50),
            "p75": _quantile(severity_ms, 0.75),
            "p90": _quantile(severity_ms, 0.90),
            "p95": _quantile(severity_ms, 0.95),
            "max": max(severity_ms) if severity_ms else 0.0,
            "bins": dict(severity_bins),
        },
        "incident_interarrival": {
            "step_gap_p50": _quantile(incident_step_gaps, 0.50),
            "step_gap_p90": _quantile(incident_step_gaps, 0.90),
            "wall_gap_s_p50": _quantile(incident_wall_gaps, 0.50),
            "wall_gap_s_p90": _quantile(incident_wall_gaps, 0.90),
        },
        "n_sampled_injection_steps": len(sampled_injection_steps),
        # ``record_mode=sampled`` intentionally omits ordinary unsampled rows,
        # so the denominator must be the logical world x step grid rather than
        # the number of persisted rows.
        "sampling_coverage_pct": sampling_coverage_pct,
        "host_cluster_detection_pct": _pct(host_cluster_hits, len(injection_steps)),
        "device_origin_detection_conditional_pct": _pct(
            device_origin_hits, len(sampled_injection_steps)
        ),
        "device_origin_detection_unconditional_pct": _pct(
            device_origin_hits, len(injection_steps)
        ),
        "device_top1_localization_conditional_pct": _pct(
            device_top1_hits, len(sampled_injection_steps)
        ),
        "device_top1_localization_unconditional_pct": _pct(
            device_top1_hits, len(injection_steps)
        ),
        "host_sync_top1_is_culprit_pct": _pct(host_top1_hits, len(injection_steps)),
        "peer_propagation_pct": _pct(peer_propagated_steps, len(injection_steps)),
        "clean_host_iter_p50_ms": _median(float(r["host_iter_ms"]) for r in clean),
        "clean_host_sync_p50_ms": _median(float(r["host_sync_ms"]) for r in clean),
        "clean_device_compute_p50_ms": _median(
            float(r["device_compute_ms"])
            for r in clean
            if int(r.get("sampled", 0)) and float(r.get("device_compute_ms", -1)) >= 0
        ),
        "clean_device_collective_p50_ms": _median(
            float(r.get("device_collective_ms", -1.0))
            for r in clean
            if int(r.get("sampled", 0)) and float(r.get("device_collective_ms", -1.0)) >= 0
        ),
        "probing_query_ok_ranks": sum(
            (root / f"rank_{rank:04d}" / "probing_timeline.jsonl").exists()
            for rank in by_rank
        ),
        "probing_roundtrip_exact_ranks": probing_roundtrip_exact_ranks,
        "probing_roundtrip_rows": probing_rows,
    }
    (root / "analysis.json").write_text(json.dumps(result, indent=2, sort_keys=True))

    lines = [
        f"# Stall timeline summary — {result['scheme']}",
        "",
        f"- world={result['world_size']}，steps={result['n_steps']}，rows={result['n_rows']}",
        f"- device event sampling coverage={result['sampling_coverage_pct']:.1f}%",
        f"- controlled injection steps={result['n_injection_steps']}，victim sampled={result['n_sampled_injection_steps']}",
        f"- host anomaly steps={result['n_host_anomaly_steps']} / incidents={result['n_host_anomaly_incidents']}，device-visible={result['n_device_visible_anomaly_steps']}，host-only={result['n_host_only_anomaly_steps']}",
        f"- longest sampled device ranks（not origin proof）={result['device_longest_sampled_rank_counts']}",
        f"- host anomaly shapes={result['host_anomaly_shape_counts']}，incident candidates={result['host_incident_candidate_counts']}",
        f"- observed span={result['observed_span_s']:.3f}s，incident critical-path sum≈{result['incident_blocked_ms_sum']:.1f}ms（{result['incident_time_fraction_pct']:.2f}%），rate={result['incident_rate_per_min']:.2f}/min={result['incident_rate_per_million_syncs']:.1f}/M syncs",
        f"- incident rate 95% Wilson CI={result['incident_rate_per_million_syncs_ci95_wilson'][0]:.1f}–{result['incident_rate_per_million_syncs_ci95_wilson'][1]:.1f}/M syncs",
        f"- severity p50={result['incident_severity_ms']['p50']:.1f}ms，p90={result['incident_severity_ms']['p90']:.1f}ms，bins={result['incident_severity_ms']['bins']}",
        f"- inter-arrival p50={result['incident_interarrival']['wall_gap_s_p50']:.2f}s/{result['incident_interarrival']['step_gap_p50']:.0f} syncs，p90={result['incident_interarrival']['wall_gap_s_p90']:.2f}s/{result['incident_interarrival']['step_gap_p90']:.0f} syncs",
        f"- host cluster detection={result['host_cluster_detection_pct']:.1f}%",
        f"- device origin detection（conditional）={result['device_origin_detection_conditional_pct']:.1f}%",
        f"- device origin detection（unconditional）={result['device_origin_detection_unconditional_pct']:.1f}%",
        f"- device top-1 localization（conditional）={result['device_top1_localization_conditional_pct']:.1f}%",
        f"- device top-1 localization（unconditional）={result['device_top1_localization_unconditional_pct']:.1f}%",
        f"- host-sync top-1 happens to be culprit={result['host_sync_top1_is_culprit_pct']:.1f}%",
        f"- peer propagation={result['peer_propagation_pct']:.1f}%",
        f"- clean p50 host_iter={result['clean_host_iter_p50_ms']:.3f} ms，host_sync={result['clean_host_sync_p50_ms']:.3f} ms，device_compute={result['clean_device_compute_p50_ms']:.3f} ms，device_collective={result['clean_device_collective_p50_ms']:.3f} ms",
        f"- Probing SQL round-trip ranks={result['probing_query_ok_ranks']}/{result['world_size']}",
        f"- Probing/raw exact key round-trip={result['probing_roundtrip_exact_ranks']}/{result['world_size']} ranks，rows={result['probing_roundtrip_rows']}/{result['n_rows']}",
    ]
    (root / "SUMMARY.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
