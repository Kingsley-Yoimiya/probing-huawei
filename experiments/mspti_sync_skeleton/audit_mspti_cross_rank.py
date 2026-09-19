#!/usr/bin/env python3
"""Read-only cross-rank completeness audit for legacy MSPTI traces.

Cross-rank timestamp comparisons are heuristics in the device clock domain.
This audit deliberately does not align device activity to host STEP/HSYNC.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import tempfile
from collections import Counter
from pathlib import Path


ACTIVITY_KINDS = ("KSEG", "COMM", "P2P")
MAX_INTERVAL_NS = 60_000_000_000
COVERAGE_BINS = 20
CLOCK_DOMAIN_NOTE = (
    "heuristic only: bins and peer comparisons use uncalibrated device timestamps; "
    "host STEP/HSYNC alignment is intentionally not attempted"
)


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _integer(row: dict, key: str, default: int = 0) -> int:
    try:
        return int(row.get(key, default))
    except (TypeError, ValueError):
        return default


def _invalid_reasons(row: dict) -> list[str]:
    start = _integer(row, "start_ns")
    end = _integer(row, "end_ns")
    reasons = []
    if start <= 0:
        reasons.append("non_positive_start")
    if end < start:
        reasons.append("end_before_start")
    if end - start > MAX_INTERVAL_NS:
        reasons.append("duration_exceeds_limit")
    return reasons


def _dominant_epoch(
    basic_valid: list[tuple[int, dict]],
) -> tuple[dict | None, set[tuple[int, int]]]:
    """Choose the largest start-time cluster separated by more than 60 seconds."""
    if not basic_valid:
        return None, set()
    ordered = sorted(basic_valid, key=lambda item: _integer(item[1], "start_ns"))
    clusters: list[list[tuple[int, dict]]] = [[ordered[0]]]
    for item in ordered[1:]:
        if (
            _integer(item[1], "start_ns")
            - _integer(clusters[-1][-1][1], "start_ns")
            > MAX_INTERVAL_NS
        ):
            clusters.append([])
        clusters[-1].append(item)
    dominant = max(
        clusters,
        key=lambda cluster: (len(cluster), _integer(cluster[-1][1], "start_ns")),
    )
    selected = {(rank, id(row)) for rank, row in dominant}
    return (
        {
            "selection": "largest activity start-time cluster separated by >60s",
            "start_ns": min(_integer(row, "start_ns") for _, row in dominant),
            "last_start_ns": max(_integer(row, "start_ns") for _, row in dominant),
            "end_ns": max(_integer(row, "end_ns") for _, row in dominant),
            "activity_intervals": len(dominant),
            "cluster_count": len(clusters),
        },
        selected,
    )


def _event_units(row: dict) -> int:
    return max(0, _integer(row, "count")) if row.get("kind") == "KSEG" else 1


def _bin_index(timestamp: int, start: int, end: int) -> int | None:
    if timestamp < start or timestamp > end or end <= start:
        return None
    if timestamp == end:
        return COVERAGE_BINS - 1
    return min(COVERAGE_BINS - 1, (timestamp - start) * COVERAGE_BINS // (end - start))


def _longest_zero_run(self_units: list[int], peer_units: list[float]) -> dict | None:
    best: tuple[int, int] | None = None
    run_start: int | None = None
    for index in range(len(self_units) + 1):
        active = (
            index < len(self_units)
            and self_units[index] == 0
            and peer_units[index] > 0
        )
        if active and run_start is None:
            run_start = index
        elif not active and run_start is not None:
            candidate = (run_start, index - 1)
            if best is None or candidate[1] - candidate[0] > best[1] - best[0]:
                best = candidate
            run_start = None
    if best is None:
        return None
    return {"start_bin": best[0], "end_bin": best[1], "length_bins": best[1] - best[0] + 1}


def _stream_summaries(rows: list[dict]) -> list[dict]:
    grouped: dict[object, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row.get("stream_id"), []).append(row)
    output = []
    for stream_id, stream_rows in grouped.items():
        ordered = sorted(
            stream_rows,
            key=lambda row: (_integer(row, "start_ns"), _integer(row, "end_ns"), _integer(row, "seq")),
        )
        corr_rows = [row for row in ordered if _integer(row, "correlation_id") > 0]
        correlations = [_integer(row, "correlation_id") for row in corr_rows]
        transitions = [
            {
                "delta": _integer(right, "correlation_id") - _integer(left, "correlation_id"),
                "left_correlation_id": _integer(left, "correlation_id"),
                "right_correlation_id": _integer(right, "correlation_id"),
                "at_start_ns": _integer(right, "start_ns"),
            }
            for left, right in zip(corr_rows, corr_rows[1:])
        ]
        forward = [item for item in transitions if item["delta"] > 0]
        resets = [item for item in transitions if item["delta"] < 0]
        output.append(
            {
                "stream_id": stream_id,
                "kinds": sorted({str(row.get("kind")) for row in ordered}),
                "records": len(ordered),
                "first_start_ns": _integer(ordered[0], "start_ns"),
                "last_end_ns": max(_integer(row, "end_ns") for row in ordered),
                "correlation": {
                    "observed_records": len(correlations),
                    "first": correlations[0] if correlations else None,
                    "last": correlations[-1] if correlations else None,
                    "minimum": min(correlations) if correlations else None,
                    "maximum": max(correlations) if correlations else None,
                    "backward_reset_count": len(resets),
                    "reset_examples": resets[:4],
                    "max_forward_jump": max((item["delta"] for item in forward), default=None),
                    "largest_forward_jump_examples": sorted(
                        forward, key=lambda item: item["delta"], reverse=True
                    )[:4],
                },
            }
        )
    return sorted(output, key=lambda item: (item["stream_id"] is None, str(item["stream_id"])))


def _phase_summary(
    units: list[int], records: list[int], peer_units: list[float], peer_records: list[float]
) -> dict:
    phases = {"prefix": (0, 4), "mid": (4, 16), "suffix": (16, 20)}
    output = {}
    for name, (lo, hi) in phases.items():
        mine = sum(units[lo:hi])
        peers = sum(peer_units[lo:hi])
        output[name] = {
            "start_bin": lo,
            "end_bin_exclusive": hi,
            "records": sum(records[lo:hi]),
            "units": mine,
            "peer_median_records": sum(peer_records[lo:hi]),
            "peer_median_units": peers,
            "unit_ratio_to_peer": mine / peers if peers else None,
        }
    return output


def audit_run(run_dir: Path, ratio: float) -> dict:
    trace_paths = sorted(run_dir.rglob("rank_*.skeleton.jsonl"))
    if not trace_paths:
        raise SystemExit("no rank traces found")
    rows_by_rank: dict[int, list[dict]] = {}
    for path in trace_paths:
        rows = load_rows(path)
        ranks = {int(row["rank"]) for row in rows}
        if len(ranks) != 1:
            raise SystemExit(f"{path}: expected exactly one rank, got {sorted(ranks)}")
        rank = ranks.pop()
        if rank in rows_by_rank:
            raise SystemExit(f"duplicate trace for rank {rank}: {path}")
        rows_by_rank[rank] = rows

    activity_by_rank = {
        rank: [row for row in rows if row.get("kind") in ACTIVITY_KINDS]
        for rank, rows in rows_by_rank.items()
    }
    basic_valid: list[tuple[int, dict]] = []
    invalid_by_rank: dict[int, list[tuple[dict, list[str]]]] = {}
    for rank, rows in activity_by_rank.items():
        invalid_by_rank[rank] = []
        for row in rows:
            reasons = _invalid_reasons(row)
            if reasons:
                invalid_by_rank[rank].append((row, reasons))
            else:
                basic_valid.append((rank, row))
    dominant_epoch, selected = _dominant_epoch(basic_valid)
    valid_by_rank = {
        rank: [row for row in rows if (rank, id(row)) in selected]
        for rank, rows in activity_by_rank.items()
    }
    outside_epoch_by_rank = {
        rank: [row for row in rows if not _invalid_reasons(row) and (rank, id(row)) not in selected]
        for rank, rows in activity_by_rank.items()
    }

    rank_starts = [
        min(_integer(row, "start_ns") for row in rows)
        for rows in valid_by_rank.values()
        if rows
    ]
    rank_ends = [
        max(_integer(row, "end_ns") for row in rows)
        for rows in valid_by_rank.values()
        if rows
    ]
    window_start = int(statistics.median(rank_starts)) if rank_starts else 0
    window_end = int(statistics.median(rank_ends)) if rank_ends else 0
    if window_end <= window_start and dominant_epoch:
        window_start = int(dominant_epoch["start_ns"])
        window_end = int(dominant_epoch["end_ns"])

    bins_by_rank: dict[int, dict[str, dict[str, list[int]]]] = {}
    for rank, rows in valid_by_rank.items():
        bins_by_rank[rank] = {}
        for kind in ACTIVITY_KINDS:
            records = [0] * COVERAGE_BINS
            units = [0] * COVERAGE_BINS
            for row in rows:
                if row.get("kind") != kind:
                    continue
                index = _bin_index(_integer(row, "start_ns"), window_start, window_end)
                if index is not None:
                    records[index] += 1
                    units[index] += _event_units(row)
            bins_by_rank[rank][kind] = {"records": records, "units": units}

    summaries = []
    for rank, rows in sorted(rows_by_rank.items()):
        valid_activity = valid_by_rank[rank]
        malformed = invalid_by_rank[rank]
        outside_epoch = outside_epoch_by_rank[rank]
        kinds = Counter(row.get("kind") for row in rows)
        comm = [row for row in valid_activity if row.get("kind") in {"COMM", "P2P"}]
        kseg = [row for row in valid_activity if row.get("kind") == "KSEG"]
        meta_paths = list(run_dir.rglob(f"rank_{rank:04d}.mspti_meta.json"))
        meta = json.loads(meta_paths[0].read_text(encoding="utf-8")) if len(meta_paths) == 1 else {}
        ordered = sorted(comm, key=lambda row: (_integer(row, "start_ns"), _integer(row, "end_ns")))
        max_gap = None
        for left, right in zip(ordered, ordered[1:]):
            gap_ns = _integer(right, "start_ns") - _integer(left, "end_ns")
            if max_gap is None or gap_ns > max_gap["gap_ns"]:
                max_gap = {
                    "gap_ns": gap_ns,
                    "start_ns": _integer(left, "end_ns"),
                    "end_ns": _integer(right, "start_ns"),
                    "left_correlation_id": left.get("correlation_id"),
                    "right_correlation_id": right.get("correlation_id"),
                }
        reason_counts = Counter(reason for _, reasons in malformed for reason in reasons)
        summaries.append(
            {
                "rank": rank,
                "kinds": dict(sorted(kinds.items())),
                "comm": len(comm),
                "raw_kernels": sum(_event_units(row) for row in kseg),
                "kseg": len(kseg),
                "ops": dict(sorted(Counter(row.get("op") for row in comm).items())),
                "peak_queue_bytes": meta.get("peak_queue_bytes"),
                "collector_ok": bool(meta)
                and meta.get("capture_end_rc") == 0
                and meta.get("finalize_rc") == 0
                and meta.get("finalize_complete") is True
                and meta.get("incomplete") is False
                and meta.get("armed_fail") is False,
                "malformed_activity_intervals": len(malformed),
                "malformed_interval_examples": [
                    {
                        "kind": row.get("kind"),
                        "seq": row.get("seq"),
                        "stream_id": row.get("stream_id"),
                        "correlation_id": row.get("correlation_id"),
                        "start_ns": row.get("start_ns"),
                        "end_ns": row.get("end_ns"),
                        "reasons": reasons,
                    }
                    for row, reasons in malformed[:4]
                ],
                "interval_sanity": {
                    "basic_invalid": len(malformed),
                    "basic_invalid_by_reason": dict(sorted(reason_counts.items())),
                    "outside_dominant_epoch": len(outside_epoch),
                    "dominant_epoch_valid": len(valid_activity),
                    "sane": not malformed and not outside_epoch,
                },
                "outside_dominant_epoch_examples": [
                    {
                        "kind": row.get("kind"),
                        "seq": row.get("seq"),
                        "stream_id": row.get("stream_id"),
                        "correlation_id": row.get("correlation_id"),
                        "start_ns": row.get("start_ns"),
                        "end_ns": row.get("end_ns"),
                    }
                    for row in outside_epoch[:4]
                ],
                "max_inter_comm_gap": max_gap,
                "streams": _stream_summaries(valid_activity),
            }
        )

    for item in summaries:
        rank = item["rank"]
        peers = [peer for peer in summaries if peer["rank"] != rank]
        raw_peer = statistics.median(peer["raw_kernels"] for peer in peers) if peers else 0
        comm_peer = statistics.median(peer["comm"] for peer in peers) if peers else 0
        item["raw_peer_median"] = raw_peer
        item["comm_peer_median"] = comm_peer
        item["raw_floor"] = math.ceil(raw_peer * ratio) if peers else 0
        item["comm_floor"] = math.ceil(comm_peer * ratio) if peers else 0
        item["raw_complete"] = not peers or item["raw_kernels"] >= item["raw_floor"]
        item["comm_complete"] = not peers or item["comm"] >= item["comm_floor"]

        gap = item["max_inter_comm_gap"]
        if gap and peers:
            lo, hi = gap["start_ns"], gap["end_ns"]
            peer_comm = [
                sum(
                    1
                    for row in valid_by_rank[peer["rank"]]
                    if row.get("kind") in {"COMM", "P2P"}
                    and _integer(row, "start_ns") >= lo
                    and _integer(row, "end_ns") <= hi
                )
                for peer in peers
            ]
            gap["peer_comm_median_in_window"] = statistics.median(peer_comm)
            gap["clock_domain_note"] = CLOCK_DOMAIN_NOTE

        coverage = {}
        for kind in ACTIVITY_KINDS:
            mine = bins_by_rank[rank][kind]
            peer_units = [
                statistics.median(
                    bins_by_rank[peer["rank"]][kind]["units"][index] for peer in peers
                )
                if peers else 0
                for index in range(COVERAGE_BINS)
            ]
            peer_records = [
                statistics.median(
                    bins_by_rank[peer["rank"]][kind]["records"][index] for peer in peers
                )
                if peers else 0
                for index in range(COVERAGE_BINS)
            ]
            zero_run = _longest_zero_run(mine["units"], peer_units)
            if zero_run and window_end > window_start:
                width = window_end - window_start
                zero_run["start_ns"] = window_start + width * zero_run["start_bin"] // COVERAGE_BINS
                zero_run["end_ns"] = window_start + width * (zero_run["end_bin"] + 1) // COVERAGE_BINS
            coverage[kind] = {
                "records_by_bin": mine["records"],
                "units_by_bin": mine["units"],
                "peer_median_records_by_bin": peer_records,
                "peer_median_units_by_bin": peer_units,
                "nonzero_bins": sum(value > 0 for value in mine["units"]),
                "prefix_mid_suffix": _phase_summary(
                    mine["units"], mine["records"], peer_units, peer_records
                ),
                "longest_peer_active_self_zero": zero_run,
            }
        item["coverage_20_bins"] = coverage

    interval_sane = all(item["interval_sanity"]["sane"] for item in summaries)
    collector_lossless = all(item["collector_ok"] for item in summaries)
    raw_complete = all(item["raw_complete"] for item in summaries)
    comm_complete = all(item["comm_complete"] for item in summaries)
    return {
        "schema_version": 1,
        "run_dir": str(run_dir),
        "rank_count": len(summaries),
        "peer_completeness_ratio": ratio,
        "collector_lossless": collector_lossless,
        "interval_sane": interval_sane,
        "raw_complete": raw_complete,
        "comm_complete": comm_complete,
        "kernel_analysis_eligible": collector_lossless and interval_sane and raw_complete,
        "comm_analysis_eligible": collector_lossless and interval_sane and raw_complete and comm_complete,
        "dominant_device_epoch": dominant_epoch,
        "coverage_bins": {
            "count": COVERAGE_BINS,
            "window_start_ns": window_start,
            "window_end_ns": window_end,
            "rank_boundary_aggregation": "median of per-rank first/last dominant-epoch activity",
            "assignment": "event start timestamp; KSEG units=sum(count), COMM/P2P units=records",
            "phase_bins": {"prefix": [0, 4], "mid": [4, 16], "suffix": [16, 20]},
        },
        "clock_domain_note": CLOCK_DOMAIN_NOTE,
        "per_rank": summaries,
    }


def _self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="audit-mspti-") as temp:
        run_dir = Path(temp)
        base = 1_000_000_000_000
        for rank in (0, 1):
            rows = []
            for index in range(COVERAGE_BINS):
                start = base + index * 1_000_000
                rows.append(
                    {"rank": rank, "kind": "KSEG", "seq": index, "stream_id": 7,
                     "correlation_id": 0, "start_ns": start, "end_ns": start + 1_000,
                     "count": 2}
                )
                if rank == 1 or index not in {8, 9, 10}:
                    corr = 3 if rank == 0 and index == 12 else 100 + index
                    rows.append(
                        {"rank": rank, "kind": "COMM", "seq": 100 + index, "stream_id": 9,
                         "correlation_id": corr, "start_ns": start + 2_000,
                         "end_ns": start + 3_000, "op": "AllReduce"}
                    )
            if rank == 0:
                rows.append(
                    {"rank": rank, "kind": "COMM", "seq": 999, "stream_id": 9,
                     "correlation_id": 999, "start_ns": base - 100_000_000_000,
                     "end_ns": base + 100_000_000_000, "op": "AllReduce"}
                )
            (run_dir / f"rank_{rank:04d}.skeleton.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
            )
            meta = {
                "capture_end_rc": 0, "finalize_rc": 0, "finalize_complete": True,
                "incomplete": False, "armed_fail": False,
            }
            (run_dir / f"rank_{rank:04d}.mspti_meta.json").write_text(
                json.dumps(meta), encoding="utf-8"
            )
        payload = audit_run(run_dir, 0.98)
        rank0 = next(item for item in payload["per_rank"] if item["rank"] == 0)
        assert rank0["malformed_activity_intervals"] == 1
        assert rank0["coverage_20_bins"]["COMM"]["longest_peer_active_self_zero"]
        stream9 = next(item for item in rank0["streams"] if item["stream_id"] == 9)
        assert stream9["correlation"]["backward_reset_count"] == 1
        assert payload["coverage_bins"]["count"] == 20


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", nargs="?", type=Path)
    parser.add_argument("--ratio", type=float, default=0.98)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--self-test", action="store_true", help="run a small offline synthetic audit")
    args = parser.parse_args()
    if args.self_test:
        _self_test()
        print(json.dumps({"self_test": "pass", "coverage_bins": COVERAGE_BINS}))
        return 0
    if args.run_dir is None:
        parser.error("run_dir is required unless --self-test is used")
    payload = audit_run(args.run_dir, args.ratio)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
