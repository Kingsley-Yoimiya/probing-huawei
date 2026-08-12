#!/usr/bin/env python3
"""把紧凑 skeleton JSONL 转换为 Chrome/Perfetto trace，并生成计数摘要。

正式路径默认 --strict fail-closed。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from strict_validate import (
    StrictValidationError,
    read_jsonl_strict,
    resolve_meta_raw_comms,
    resolve_meta_raw_kernels,
    validate_attempt_manifest,
    validate_ours_dir,
)


DEVICE_KINDS = {"KSEG", "COMM", "P2P"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    parser.add_argument(
        "--strict",
        dest="strict",
        action="store_true",
        default=True,
        help="正式路径默认开启；坏行/缺 rank 直接失败",
    )
    parser.add_argument(
        "--no-strict",
        dest="strict",
        action="store_false",
        help="关闭 fail-closed（仅调试）",
    )
    parser.add_argument("--expected-ranks", type=int, default=None)
    parser.add_argument("--capture-step", type=int, default=None)
    parser.add_argument(
        "--workload-kind",
        default=None,
        help="summary 文案：megatron|synthetic；默认读 config/manifest",
    )
    parser.add_argument(
        "--min-raw-kernels",
        type=int,
        default=None,
        help="冻结绝对下限（CLI/config）；禁止仅用本批自参考",
    )
    parser.add_argument(
        "--min-comm",
        type=int,
        default=None,
        help="冻结每 rank COMM+P2P 绝对下限（CLI/config）",
    )
    parser.add_argument(
        "--relative-raw-floor",
        type=float,
        default=0.8,
        help="每 rank raw >= floor * 跨 rank 稳健中心；与绝对下限取 max",
    )
    parser.add_argument(
        "--relative-comm-floor",
        type=float,
        default=0.8,
        help="每 rank COMM >= floor * 跨 rank 稳健中心",
    )
    parser.add_argument(
        "--expected-nodes",
        type=int,
        default=None,
        help="父 launcher 节点数；用于 done/fail 门禁",
    )
    return parser.parse_args()


def lane_id(row: dict[str, Any]) -> int:
    kind = row["kind"]
    if kind == "HSYNC":
        return 10
    if kind == "STEP":
        return 11
    if kind == "DROP":
        return 12
    stream = int(row.get("stream_id", -1))
    return 1000 + max(0, stream)


def trace_event(row: dict[str, Any], anchor: int, device_shift: int) -> dict[str, Any]:
    start_ns = int(row["start_ns"])
    end_ns = int(row["end_ns"])
    if row["kind"] in DEVICE_KINDS and "source=mspti_hccl_callback" not in row.get("flags", ""):
        start_ns += device_shift
        end_ns += device_shift
    args = {
        key: row.get(key)
        for key in (
            "step",
            "device_id",
            "stream_id",
            "peer_stream",
            "correlation_id",
            "count",
            "bytes",
            "active_ns",
            "span_ns",
            "gap_ns",
            "op",
            "comm_name",
            "flags",
        )
    }
    event = {
        "name": row["kind"] if not row.get("op") else f"{row['kind']}:{row['op']}",
        "cat": row["kind"],
        "pid": int(row["rank"]),
        "tid": lane_id(row),
        "ts": (start_ns - anchor) / 1000.0,
        "args": args,
    }
    if row["kind"] in {"STEP", "DROP"}:
        event.update({"ph": "i", "s": "t"})
    else:
        event.update({"ph": "X", "dur": max(0, end_ns - start_ns) / 1000.0})
    return event


def metadata_events(rank: int, host: str, stream_ids: set[int]) -> list[dict[str, Any]]:
    events = [
        {
            "name": "process_name",
            "ph": "M",
            "pid": rank,
            "tid": 0,
            "args": {"name": f"rank {rank} @ {host}"},
        },
        {
            "name": "thread_name",
            "ph": "M",
            "pid": rank,
            "tid": 10,
            "args": {"name": "Host synchronize"},
        },
        {
            "name": "thread_name",
            "ph": "M",
            "pid": rank,
            "tid": 11,
            "args": {"name": "Training step"},
        },
        {
            "name": "thread_name",
            "ph": "M",
            "pid": rank,
            "tid": 12,
            "args": {"name": "Drop accounting"},
        },
    ]
    for stream_id in sorted(stream_ids):
        events.append(
            {
                "name": "thread_name",
                "ph": "M",
                "pid": rank,
                "tid": 1000 + stream_id,
                "args": {"name": f"NPU stream {stream_id}"},
            }
        )
    return events


def load_config(run_dir: Path) -> dict[str, Any]:
    for name in ("attempt_manifest.json", "config.json", "arm_manifest.json"):
        path = run_dir / name
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    parent = run_dir.parent / "group_config.json"
    if parent.exists():
        return json.loads(parent.read_text(encoding="utf-8"))
    return {}


def workload_description(kind: str, config: dict[str, Any]) -> str:
    if kind == "megatron":
        model = config.get("model") or {}
        return (
            "真实 MindSpeed/Megatron `pretrain_gpt.py` 训练；"
            f"TP={model.get('tp', '?')} PP={model.get('pp', '?')} "
            f"GBS={model.get('gbs', '?')} SEQ={model.get('seq', '?')} "
            f"layers={model.get('layers', '?')}（非 synthetic matmul）"
        )
    if kind == "synthetic":
        return "synthetic：两次 FP16 matmul + GELU + HCCL AllReduce（非 Megatron）"
    return f"workload_kind={kind}"


def main() -> int:
    convert_command_t0 = time.perf_counter()
    args = parse_args()
    run_dir = Path(args.run_dir)
    config = load_config(run_dir)
    expected_ranks = args.expected_ranks
    if expected_ranks is None:
        expected_ranks = int(
            config.get("expected_ranks")
            or config.get("world_size")
            or (
                int(config.get("nnodes", 0) or 0)
                * int(config.get("nproc_per_node", 0) or 0)
            )
            or 0
        )
    capture_step = args.capture_step
    if capture_step is None:
        capture_step = int(
            config.get("capture_megatron_iter")
            or config.get("capture_step")
            or 10
        )
    workload_kind = (
        args.workload_kind
        or config.get("workload_kind")
        or config.get("model_kind")
        or "unknown"
    )

    skeleton_paths = sorted(run_dir.glob("rank_*.skeleton.jsonl"))
    meta_rows = []
    for meta_path in sorted(run_dir.glob("rank_*.meta.json")):
        meta_rows.append(json.loads(meta_path.read_text(encoding="utf-8")))
    mspti_metas = []
    for meta_path in sorted(run_dir.glob("rank_*.mspti_meta.json")):
        mspti_metas.append(json.loads(meta_path.read_text(encoding="utf-8")))

    arm_kind = str(
        config.get("arm")
        or config.get("collector_arm")
        or config.get("collector")
        or ""
    ).strip().lower()
    explicit_collector_off = arm_kind in {"normal", "off", "baseline", "collector-off"}

    if not skeleton_paths:
        # Empty dir is NEVER an automatic collector-off PASS.
        if args.strict and not explicit_collector_off:
            print(
                "[convert_trace] STRICT FAIL: no skeleton JSONL; "
                "collector-off requires explicit arm kind "
                "(normal|off|baseline|collector-off) in manifest/config",
                file=sys.stderr,
            )
            (run_dir / "counters.json").write_text(
                json.dumps(
                    {
                        "pass": False,
                        "strict": True,
                        "error": "empty skeletons without explicit collector-off arm",
                        "arm_kind": arm_kind,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            return 2
        if not explicit_collector_off and expected_ranks > 0:
            print(
                "[convert_trace] FAIL: no skeleton and arm not explicitly collector-off",
                file=sys.stderr,
            )
            return 2
        measured = [float(row["measured_ms"]) for row in meta_rows]
        final_sync = [float(row["final_sync_ms"]) for row in meta_rows]
        step_values: list[float] = []
        for row in meta_rows:
            step_values.extend(float(v) for v in row.get("step_host_ms", []))
        counters = {
            "event_counts": {},
            "source_counts": {},
            "per_rank": {},
            "raw_kernel_records_represented": 0,
            "kseg_records": 0,
            "kernel_record_compression_ratio": None,
            "drop_count": 0,
            "jsonl_bytes": 0,
            "cluster_trace_bytes": 0,
            "rank_meta_count": len(meta_rows),
            "measured_ms_p50": statistics.median(measured) if measured else None,
            "measured_ms_max": max(measured) if measured else None,
            "final_sync_ms_p50": statistics.median(final_sync) if final_sync else None,
            "step_host_ms_p50": statistics.median(step_values) if step_values else None,
            "collector_start_ms_p50": None,
            "collector_stop_ms_p50": None,
            "collector_mode": "off",
            "arm_kind": arm_kind or "explicit_off",
            "strict": bool(args.strict),
            "pass": True,
        }
        (run_dir / "counters.json").write_text(
            json.dumps(counters, indent=2, sort_keys=True), encoding="utf-8"
        )
        run_id = config.get("run_id", run_dir.name)
        summary = f"""# MSPTI NPU 同步骨架采集摘要（collector off）

- run_id：`{run_id}`
- workload：{workload_description(str(workload_kind), config)}
- collector：off（显式 arm={arm_kind or 'explicit_off'}）
"""
        (run_dir / "SUMMARY.md").write_text(summary, encoding="utf-8")
        (run_dir / "cluster.trace.json").write_text(
            json.dumps({"traceEvents": [], "displayTimeUnit": "ns"}, separators=(",", ":")),
            encoding="utf-8",
        )
        return 0

    try:
        if args.strict:
            if expected_ranks <= 0:
                raise StrictValidationError("strict mode requires expected_ranks>0")
            min_raw = args.min_raw_kernels
            if min_raw is None and os.environ.get("MSPTI_MIN_RAW_KERNELS"):
                min_raw = int(os.environ["MSPTI_MIN_RAW_KERNELS"])
            min_comm = args.min_comm
            if min_comm is None and os.environ.get("MSPTI_MIN_COMM"):
                min_comm = int(os.environ["MSPTI_MIN_COMM"])
            strict_info = validate_ours_dir(
                run_dir,
                expected_ranks=expected_ranks,
                capture_step=capture_step,
                min_raw_kernels=min_raw,
                min_comm=min_comm,
                relative_raw_floor=float(args.relative_raw_floor),
                relative_comm_floor=float(args.relative_comm_floor),
            )
            # attempt_manifest 在 convert 之后才 seal；已存在时做完整门禁。
            if (run_dir / "attempt_manifest.json").exists():
                validate_attempt_manifest(
                    run_dir,
                    expected_ranks=expected_ranks,
                    capture_step=capture_step,
                    expected_nodes=args.expected_nodes,
                    require_seal=(run_dir / "attempt_manifest.sha256").exists(),
                    require_provenance=(run_dir / "artifact_digest.json").exists(),
                )
        else:
            strict_info = {"ok": False, "note": "strict disabled"}
    except StrictValidationError as exc:
        print(f"[convert_trace] STRICT FAIL: {exc}", file=sys.stderr)
        (run_dir / "counters.json").write_text(
            json.dumps(
                {"pass": False, "strict": True, "error": str(exc)},
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return 3

    cluster_events: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    per_rank: dict[str, Any] = {}
    raw_kernel_count = 0
    drop_count = 0
    total_jsonl_bytes = 0
    # convert_trace_write_ms: from skeleton read through cluster.trace.json write.
    # Does NOT include counters.json fsync (field is embedded in counters). Outer runner
    # records convert_command_wall_ms covering full convert process including counters.
    convert_t0 = time.perf_counter()

    for path in skeleton_paths:
        # 正式与调试均要求整行可解析；strict 额外做 rank/step/DROP/KSEG invariant。
        rows = read_jsonl_strict(path)
        rank = int(rows[0]["rank"])
        host = str(rows[0]["host"])
        host_starts = [
            int(row["start_ns"])
            for row in rows
            if row["kind"] == "STEP" and "phase=begin" in row.get("flags", "")
        ]
        device_starts = [
            int(row["start_ns"])
            for row in rows
            if row["kind"] in DEVICE_KINDS
            and "source=mspti_hccl_callback" not in row.get("flags", "")
        ]
        host_anchor = min(host_starts) if host_starts else min(int(row["start_ns"]) for row in rows)
        device_anchor = min(device_starts) if device_starts else host_anchor
        clock_delta_ns = device_anchor - host_anchor
        clocks_compatible = abs(clock_delta_ns) <= 10_000_000_000
        device_shift = 0 if clocks_compatible else host_anchor - device_anchor
        rank_anchor = host_anchor

        stream_ids = {
            int(row["stream_id"])
            for row in rows
            if int(row.get("stream_id", -1)) >= 0
        }
        events = metadata_events(rank, host, stream_ids)
        events.extend(trace_event(row, rank_anchor, device_shift) for row in rows)
        rank_trace = {"traceEvents": events, "displayTimeUnit": "ns"}
        (run_dir / f"rank_{rank:04d}.trace.json").write_text(
            json.dumps(rank_trace, separators=(",", ":")), encoding="utf-8"
        )
        cluster_events.extend(events)

        rank_counts = Counter(str(row["kind"]) for row in rows)
        counts.update(rank_counts)
        for row in rows:
            flags = str(row.get("flags", ""))
            if "source=" in flags:
                source = flags.split("source=", 1)[1].split(";", 1)[0]
                source_counts[source] += 1
            if row["kind"] == "KSEG":
                raw_kernel_count += int(row.get("count", 0))
            if row["kind"] == "DROP":
                drop_count += int(row.get("count", 0))
        total_jsonl_bytes += path.stat().st_size
        per_rank[str(rank)] = {
            "host": host,
            "events": len(rows),
            "counts": dict(rank_counts),
            "streams": sorted(stream_ids),
            "host_device_first_event_delta_ns": clock_delta_ns,
            "clock_mode": "shared_monotonic" if clocks_compatible else "first_event_aligned_for_view_only",
        }

    if not cluster_events:
        print("[convert_trace] FAIL: empty cluster events", file=sys.stderr)
        return 4

    cluster_trace = {"traceEvents": cluster_events, "displayTimeUnit": "ns"}
    (run_dir / "cluster.trace.json").write_text(
        json.dumps(cluster_trace, separators=(",", ":")), encoding="utf-8"
    )
    convert_trace_write_ms = (time.perf_counter() - convert_t0) * 1000.0
    if (run_dir / "cluster.trace.json").stat().st_size <= 2:
        print("[convert_trace] FAIL: empty cluster.trace.json", file=sys.stderr)
        return 4

    measured = [float(row["measured_ms"]) for row in meta_rows]
    final_sync = [float(row["final_sync_ms"]) for row in meta_rows]
    step_values = []
    for row in meta_rows:
        step_values.extend(float(v) for v in row.get("step_host_ms", []))
    collector_start = [
        float(row["collector_start_ms"]) for row in meta_rows if row.get("collector_selected")
    ]
    collector_stop = [
        float(row["collector_stop_ms"]) for row in meta_rows if row.get("collector_selected")
    ]

    meta_raw_kernels = []
    meta_raw_comms = []
    for m in mspti_metas:
        try:
            raw = resolve_meta_raw_kernels(m)
            if raw is not None:
                meta_raw_kernels.append(int(raw))
            comm = resolve_meta_raw_comms(m)
            if comm is not None:
                meta_raw_comms.append(int(comm))
        except StrictValidationError:
            pass
    def _meta_float(m, *keys):
        for k in keys:
            if m.get(k) is not None:
                return float(m[k])
        return None

    finalize_total_vals = [
        v for v in (_meta_float(m, "finalize_total_ms", "finalize_ms") for m in mspti_metas) if v is not None
    ]
    finalize_flush_vals = [
        v for v in (_meta_float(m, "finalize_flush_ms") for m in mspti_metas) if v is not None
    ]
    finalize_drain_vals = [
        v for v in (_meta_float(m, "finalize_drain_ms") for m in mspti_metas) if v is not None
    ]
    capture_begin_vals = [
        v for v in (_meta_float(m, "capture_begin_ms") for m in mspti_metas) if v is not None
    ]
    capture_end_vals = [
        v for v in (_meta_float(m, "capture_end_ms") for m in mspti_metas) if v is not None
    ]
    process_wall_vals = [
        v for v in (_meta_float(m, "process_wall_ms") for m in mspti_metas) if v is not None
    ]
    convert_wall_ms = None
    # filled below after convert timing if available
    distribution = {
        "per_rank_kseg": {r: per_rank[r]["counts"].get("KSEG", 0) for r in per_rank},
        "per_rank_comm": {
            r: per_rank[r]["counts"].get("COMM", 0) + per_rank[r]["counts"].get("P2P", 0)
            for r in per_rank
        },
        "per_rank_raw_kernels_meta": {
            str(m.get("rank")): m.get("raw_kernels") for m in mspti_metas
        },
        "per_rank_raw_comms_meta": {
            str(m.get("rank")): m.get("raw_comms") for m in mspti_metas
        },
        "raw_kernels_meta_p50": statistics.median(meta_raw_kernels) if meta_raw_kernels else None,
        "raw_comms_meta_p50": statistics.median(meta_raw_comms) if meta_raw_comms else None,
        "finalize_total_ms_p50": statistics.median(finalize_total_vals) if finalize_total_vals else None,
        "finalize_flush_ms_p50": statistics.median(finalize_flush_vals) if finalize_flush_vals else None,
        "finalize_drain_ms_p50": statistics.median(finalize_drain_vals) if finalize_drain_vals else None,
        "capture_begin_ms_p50": statistics.median(capture_begin_vals) if capture_begin_vals else None,
        "capture_end_ms_p50": statistics.median(capture_end_vals) if capture_end_vals else None,
        "process_wall_ms_p50": statistics.median(process_wall_vals) if process_wall_vals else None,
        # aliases matching meta field names (must not be null under strict ours)
        "finalize_ms_p50": statistics.median(finalize_total_vals) if finalize_total_vals else None,
    }

    counters = {
        "event_counts": dict(counts),
        "source_counts": dict(source_counts),
        "per_rank": per_rank,
        "distribution": distribution,
        "raw_kernel_records_represented": raw_kernel_count,
        "raw_kernels_from_meta_sum": sum(meta_raw_kernels) if meta_raw_kernels else None,
        "raw_comms_from_meta_sum": sum(meta_raw_comms) if meta_raw_comms else None,
        "kseg_records": counts["KSEG"],
        "comm_records": counts.get("COMM", 0) + counts.get("P2P", 0),
        "kernel_record_compression_ratio": (
            raw_kernel_count / counts["KSEG"] if counts["KSEG"] else None
        ),
        "drop_count": drop_count,
        "jsonl_bytes": total_jsonl_bytes,
        "jsonl_mib": total_jsonl_bytes / (1024 * 1024),
        "cluster_trace_bytes": (run_dir / "cluster.trace.json").stat().st_size,
        "cluster_trace_mib": (run_dir / "cluster.trace.json").stat().st_size / (1024 * 1024),
        "rank_meta_count": len(meta_rows),
        "mspti_meta_count": len(mspti_metas),
        "measured_ms_p50": statistics.median(measured) if measured else None,
        "measured_ms_max": max(measured) if measured else None,
        "final_sync_ms_p50": statistics.median(final_sync) if final_sync else None,
        "step_host_ms_p50": statistics.median(step_values) if step_values else None,
        "collector_start_ms_p50": statistics.median(collector_start) if collector_start else None,
        "collector_stop_ms_p50": statistics.median(collector_stop) if collector_stop else None,
        "collector_mode": "on",
        "strict": bool(args.strict),
        "pass": True if (not args.strict or drop_count == 0) else False,
        "strict_info": strict_info,
        "workload_kind": workload_kind,
        "expected_ranks": expected_ranks,
        "capture_step": capture_step,
        "finalize_total_ms_p50": distribution.get("finalize_total_ms_p50"),
        "finalize_flush_ms_p50": distribution.get("finalize_flush_ms_p50"),
        "finalize_drain_ms_p50": distribution.get("finalize_drain_ms_p50"),
        "finalize_ms_p50": distribution.get("finalize_ms_p50"),
        "capture_begin_ms_p50": distribution.get("capture_begin_ms_p50"),
        "capture_end_ms_p50": distribution.get("capture_end_ms_p50"),
        "process_wall_ms_p50": distribution.get("process_wall_ms_p50"),
        "convert_trace_write_ms": convert_trace_write_ms,
        "convert_export_wall_ms": convert_trace_write_ms,  # deprecated alias
        "convert_command_wall_ms": None,  # filled just before return
    }
    if args.strict and (
        drop_count != 0
        or counts.get("KSEG", 0) <= 0
        or (meta_raw_kernels and min(meta_raw_kernels) <= 0)
    ):
        counters["pass"] = False
        (run_dir / "counters.json").write_text(
            json.dumps(counters, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(
            f"[convert_trace] STRICT FAIL: drop_count={drop_count} "
            f"kseg={counts.get('KSEG', 0)} raw_meta_min="
            f"{min(meta_raw_kernels) if meta_raw_kernels else None}",
            file=sys.stderr,
        )
        return 5

    (run_dir / "counters.json").write_text(
        json.dumps(counters, indent=2, sort_keys=True), encoding="utf-8"
    )

    run_id = config.get("run_id", run_dir.name)
    compression = counters["kernel_record_compression_ratio"]
    compression_text = "无 KSEG" if compression is None else f"{compression:.2f}x"
    summary = f"""# MSPTI NPU 同步骨架采集摘要

- run_id：`{run_id}`
- workload：{workload_description(str(workload_kind), config)}
- 规模：expected_ranks={expected_ranks}；capture_step={capture_step}
- strict：{args.strict}；pass：{counters["pass"]}
- 事件计数：`{json.dumps(dict(counts), ensure_ascii=False, sort_keys=True)}`
- 原始 kernel 记录（由 KSEG.count 汇总）：{raw_kernel_count}
- KSEG 压缩比：{compression_text}
- DROP count 合计：{drop_count}
- JSONL：{total_jsonl_bytes} bytes ({counters["jsonl_mib"]:.3f} MiB)
- cluster trace：`cluster.trace.json` ({counters["cluster_trace_mib"]:.3f} MiB)

## 字段

- `KSEG`：`active_ns` 为重叠 kernel 区间并集；`span_ns=end-start`；`gap_ns=span-active`；不变量 active≤span 且 gap≥0。
- `COMM`/`P2P`：MSPTI COMMUNICATION Activity。
- `HSYNC`：runtime STREAM_SYNCHRONIZED host callback（**非** MSPTI 设备真 sync）。
- `STEP`/`DROP`：门控边界与丢包记账。

## 两阶段协议

- CaptureEnd：仅 Disable KERNEL/COMM；无 Flush / 无固定 sleep / 不写最终 DROP。
- Finalize：训练返回后 FlushAll(0)+Unsubscribe+join worker+写 DROP；失败不得 PASS。

## 已知缺口

- SWAIT：CANN 8.5.0 无公开 wait CBID，不采集、不伪造。
"""
    (run_dir / "SUMMARY.md").write_text(summary, encoding="utf-8")
    counters["convert_command_wall_ms"] = (time.perf_counter() - convert_command_t0) * 1000.0
    (run_dir / "counters.json").write_text(
        json.dumps(counters, indent=2, sort_keys=True), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
