#!/usr/bin/env python3
"""Strict validator and sealer for an independent legacy MSPTI subset arm."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
import statistics
from pathlib import Path

from buffer_audit import aggregate_buffer_audits
from strict_validate import StrictValidationError, read_jsonl_strict, validate_rank_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--expected-ranks", type=int, required=True)
    parser.add_argument("--expected-nodes", type=int, default=1)
    parser.add_argument("--capture-step", type=int, required=True)
    parser.add_argument(
        "--capture-ranks",
        default="all",
        help="all or comma-separated global ranks expected to produce trace/meta",
    )
    parser.add_argument(
        "--min-raw-kernels",
        type=int,
        required=True,
        help="absolute frozen lower bound from the preregistered healthy reference",
    )
    parser.add_argument(
        "--min-comm",
        type=int,
        required=True,
        help="absolute frozen COMM+P2P lower bound from the healthy reference",
    )
    parser.add_argument("--relative-floor", type=float, default=0.8)
    parser.add_argument(
        "--raw-completeness-ratio",
        type=float,
        default=0.98,
        help="each rank needs at least this fraction of its peer median raw-kernel count",
    )
    parser.add_argument(
        "--comm-completeness-ratio",
        type=float,
        default=0.98,
        help="each rank needs at least this fraction of its peer median COMM+P2P count",
    )
    parser.add_argument(
        "--node-layout",
        choices=("auto", "flat", "nested"),
        default="auto",
        help="nested reads node_runs/node_XX; auto selects nested for multi-node runs",
    )
    parser.add_argument(
        "--require-receipts",
        action="store_true",
        help="require one root-level atomic node_XX.receipt.json per node",
    )
    parser.add_argument("--expected-so-sha", default="")
    parser.add_argument("--expected-hccl-ledger-so-sha", default="")
    parser.add_argument(
        "--require-hccl-issued-ledger",
        action="store_true",
        help="require exact selected-rank HCCL issued-work ledgers and passing summaries",
    )
    parser.add_argument(
        "--require-manifest-thresholds",
        action="store_true",
        help="fail unless frozen minima and peer ratios are bound in planned_manifest.json",
    )
    parser.add_argument(
        "--hccl-mspti-mapping-contract",
        type=Path,
        default=None,
        help="frozen empirical HCCL-issued to MSPTI per-op mapping contract",
    )
    parser.add_argument(
        "--require-hccl-mspti-mapping-contract",
        action="store_true",
        help="fail closed unless the contract, manifest binding, HCCL schedule and MSPTI floors all match",
    )
    parser.add_argument(
        "--tp-size",
        type=int,
        default=0,
        help="optional diagnostic topology coverage; 0 disables, e.g. 2 checks TP0/TP1",
    )
    return parser.parse_args()


def atomic_json(path: Path, payload: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with tmp.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    directory_fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def atomic_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    with tmp.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    directory_fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def int_or(value: object, default: int = -1) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def normalize_capture_ranks(value: object, expected_ranks: int) -> list[int]:
    """Canonicalize all/string/list capture specs to an exact sorted rank list."""
    def parse_rank(item: object) -> int:
        if isinstance(item, bool):
            raise ValueError(f"boolean is not a rank: {item!r}")
        if isinstance(item, int):
            return item
        if isinstance(item, str) and item.strip():
            return int(item.strip())
        raise ValueError(f"rank must be an integer token, got {item!r}")

    if value == "all":
        ranks = list(range(expected_ranks))
    elif isinstance(value, str):
        if not value.strip():
            raise ValueError("empty capture-ranks string")
        ranks = sorted({parse_rank(item) for item in value.split(",")})
    elif isinstance(value, (list, tuple)):
        ranks = sorted({parse_rank(item) for item in value})
    else:
        raise ValueError(f"unsupported capture-ranks value: {value!r}")
    if not ranks or ranks[0] < 0 or ranks[-1] >= expected_ranks:
        raise ValueError(f"capture ranks must be inside world 0..{expected_ranks - 1}")
    return ranks


def metric_completeness(
    per_rank: list[dict],
    *,
    metric_key: str,
    metric_label: str,
    reference_min: int,
    peer_ratio: float,
) -> tuple[list[dict], list[str]]:
    """Return reference and peer completeness without inventing a k=1 peer pass."""
    rows: list[dict] = []
    errors: list[str] = []
    for item in per_rank:
        rank = int(item["rank"])
        peers = [
            int(peer[metric_key])
            for peer in per_rank
            if int(peer["rank"]) != rank
        ]
        peer_evaluable = bool(peers)
        peer_median = float(statistics.median(peers)) if peer_evaluable else None
        peer_floor = (
            max(reference_min, int(math.ceil(peer_median * peer_ratio)))
            if peer_median is not None
            else None
        )
        observed = int(item[metric_key])
        reference_complete = observed >= reference_min
        peer_complete = observed >= peer_floor if peer_floor is not None else None
        complete = reference_complete and peer_complete is not False
        row = {
            "rank": rank,
            metric_key: observed,
            f"peer_median_{metric_key}": peer_median,
            "required_ratio": peer_ratio,
            "reference_floor": reference_min,
            "peer_floor": peer_floor,
            "peer_evaluable": peer_evaluable,
            f"{metric_label}_reference_complete": reference_complete,
            f"{metric_label}_peer_complete": peer_complete,
            f"{metric_label}_complete": complete,
        }
        rows.append(row)
        if not reference_complete:
            errors.append(
                f"rank={rank} {metric_key}={observed} below frozen reference "
                f"floor {reference_min}"
            )
        if peer_complete is False:
            errors.append(
                f"rank={rank} {metric_key}={observed} below peer completeness floor "
                f"{peer_floor} (peer_median={peer_median:.3f}, ratio={peer_ratio})"
            )
    return rows, errors


def topology_coverage(capture_ranks: list[int], tp_size: int) -> dict:
    """Diagnostic-only TP-role coverage; never determines validation pass/fail."""
    if tp_size <= 0:
        return {
            "tp_size": None,
            "selected_tp_roles": None,
            "selected_tp_role_counts": None,
            "topology_coverage_complete": None,
        }
    counts = {role: 0 for role in range(tp_size)}
    for rank in capture_ranks:
        counts[rank % tp_size] += 1
    selected = sorted(role for role, count in counts.items() if count)
    return {
        "tp_size": tp_size,
        "selected_tp_roles": selected,
        "selected_tp_role_counts": counts,
        "topology_coverage_complete": selected == list(range(tp_size)),
    }


def validate_hccl_issued_ledgers(
    root: Path,
    *,
    node_roots: list[Path],
    capture_ranks: list[int],
    ranks_per_node: int,
    capture_step: int,
    expected_so_sha: str,
) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    rows: list[dict] = []
    expected_summaries = {
        node_roots[rank // ranks_per_node]
        / f"rank_{rank:04d}.hccl_issued_summary.json"
        for rank in capture_ranks
    }
    expected_jsonl = {
        node_roots[rank // ranks_per_node] / f"rank_{rank:04d}.hccl_issued.jsonl"
        for rank in capture_ranks
    }
    actual_summaries = set(root.rglob("rank_*.hccl_issued_summary.json"))
    actual_jsonl = set(root.rglob("rank_*.hccl_issued.jsonl"))
    if {p.resolve() for p in actual_summaries} != {
        p.resolve() for p in expected_summaries
    }:
        errors.append(
            f"HCCL issued summary inventory mismatch actual={len(actual_summaries)} "
            f"expected={len(expected_summaries)}"
        )
    if {p.resolve() for p in actual_jsonl} != {p.resolve() for p in expected_jsonl}:
        errors.append(
            f"HCCL issued jsonl inventory mismatch actual={len(actual_jsonl)} "
            f"expected={len(expected_jsonl)}"
        )
    expected_symbols = {
        "HcclAllReduce",
        "HcclAllGather",
        "HcclReduceScatter",
        "HcclBroadcast",
        "HcclSend",
        "HcclRecv",
    }
    for rank in capture_ranks:
        node_root = node_roots[rank // ranks_per_node]
        summary_path = node_root / f"rank_{rank:04d}.hccl_issued_summary.json"
        jsonl_path = node_root / f"rank_{rank:04d}.hccl_issued.jsonl"
        try:
            summary = json.loads(summary_path.read_text())
            issued_rows = [
                json.loads(line)
                for line in jsonl_path.read_text().splitlines()
                if line
            ]
        except (OSError, ValueError) as exc:
            errors.append(f"rank={rank} cannot read HCCL issued ledger: {exc}")
            continue
        if summary.get("rank") != rank or summary.get("pass") is not True:
            errors.append(f"rank={rank} HCCL issued summary identity/pass mismatch")
        if summary.get("begin_step") != capture_step or summary.get("end_step") != capture_step:
            errors.append(f"rank={rank} HCCL issued step mismatch")
        accepted = int_or(summary.get("captured_accepted"), 0)
        for key in ("captured_issued", "captured_stored", "captured_returned"):
            if int_or(summary.get(key), -1) != accepted:
                errors.append(f"rank={rank} HCCL issued {key} mismatch")
        for key in (
            "captured_error",
            "overflow_count",
            "incomplete_records",
            "active_calls_at_finalize",
        ):
            if int_or(summary.get(key), -1) != 0:
                errors.append(f"rank={rank} HCCL issued {key} not zero")
        if accepted <= 0 or len(issued_rows) != accepted:
            errors.append(f"rank={rank} HCCL issued accepted/row count invalid")
        bindings = summary.get("bindings")
        if summary.get("all_bindings_ok") is not True or not isinstance(bindings, list):
            errors.append(f"rank={rank} HCCL issued bindings invalid")
            bindings = []
        if {item.get("symbol") for item in bindings if isinstance(item, dict)} != expected_symbols:
            errors.append(f"rank={rank} HCCL issued symbol coverage mismatch")
        if any(
            item.get("resolved") is not True
            or item.get("self_interpose") is not False
            or not item.get("path")
            for item in bindings
            if isinstance(item, dict)
        ):
            errors.append(f"rank={rank} HCCL issued binding provenance mismatch")
        if any(
            row.get("rank") != rank
            or row.get("step") != capture_step
            or row.get("returned") is not True
            or row.get("rc") != 0
            for row in issued_rows
        ):
            errors.append(f"rank={rank} HCCL issued row semantics mismatch")
        if [row.get("seq") for row in issued_rows] != list(range(1, len(issued_rows) + 1)):
            errors.append(f"rank={rank} HCCL issued sequence mismatch")
        op_counts: dict[str, int] = {}
        for row in issued_rows:
            op = str(row.get("op"))
            op_counts[op] = op_counts.get(op, 0) + 1
        rows.append(
            {
                "rank": rank,
                "captured_accepted": accepted,
                "schedule_digest_fnv1a64": summary.get("schedule_digest_fnv1a64"),
                "op_counts": op_counts,
            }
        )
    if expected_so_sha:
        sealed = list((root / "sealed_bins").glob("libhccl_issued_ledger.so.*"))
        if len(sealed) != 1 or sealed[0].name != f"libhccl_issued_ledger.so.{expected_so_sha}":
            errors.append("HCCL issued ledger sealed SO inventory mismatch")
        elif sha256(sealed[0]) != expected_so_sha:
            errors.append("HCCL issued ledger sealed SO hash mismatch")
    return rows, errors


def validate_hccl_mspti_mapping(
    *,
    contract_path: Path,
    manifest: dict,
    hccl_issued: list[dict],
    trace_rows_by_rank: dict[int, list[dict]],
    capture_ranks: list[int],
    capture_step: int,
) -> tuple[dict, list[str]]:
    errors: list[str] = []
    try:
        contract_bytes = contract_path.read_bytes()
        contract = json.loads(contract_bytes)
    except (OSError, ValueError) as exc:
        return {"pass": False, "errors": [f"cannot read mapping contract: {exc}"]}, [
            f"cannot read HCCL/MSPTI mapping contract: {exc}"
        ]
    contract_sha = hashlib.sha256(contract_bytes).hexdigest()
    contract_id = contract.get("contract_id")
    if contract.get("schema_version") != 1 or not isinstance(contract_id, str):
        errors.append("HCCL/MSPTI mapping contract identity/schema mismatch")
    if manifest.get("hccl_mspti_mapping_contract_id") != contract_id:
        errors.append("planned manifest mapping contract id mismatch")
    if manifest.get("hccl_mspti_mapping_contract_sha256") != contract_sha:
        errors.append("planned manifest mapping contract hash mismatch")
    workload = contract.get("workload") or {}
    if workload.get("capture_step") != capture_step:
        errors.append("mapping contract capture_step mismatch")
    for key in ("train_iters", "tp", "pp", "mbs", "gbs", "seq", "layers", "seed"):
        if manifest.get(key) != workload.get(key):
            errors.append(f"mapping contract workload {key} mismatch")

    expected_hccl = contract.get("expected_hccl_accepted") or {}
    floors = contract.get("mspti_per_op_minimum") or {}
    op_mapping = contract.get("op_mapping") or {}
    if not expected_hccl or not floors or set(op_mapping) != set(expected_hccl):
        errors.append("mapping contract op tables are incomplete")
    hccl_by_rank = {int(item["rank"]): item for item in hccl_issued}
    per_rank: list[dict] = []
    for rank in capture_ranks:
        hccl_counts = (hccl_by_rank.get(rank) or {}).get("op_counts") or {}
        schedule_match = hccl_counts == expected_hccl
        if not schedule_match:
            errors.append(f"rank={rank} HCCL accepted schedule differs from mapping contract")
        mspti_counts = Counter(
            str(row.get("op"))
            for row in trace_rows_by_rank.get(rank, [])
            if row.get("kind") in {"COMM", "P2P"}
        )
        ops: list[dict] = []
        rank_complete = schedule_match
        for hccl_op, mspti_op in op_mapping.items():
            observed = int(mspti_counts.get(str(mspti_op), 0))
            floor = int(floors.get(str(mspti_op), -1))
            complete = floor >= 0 and observed >= floor
            if not complete:
                errors.append(
                    f"rank={rank} MSPTI op={mspti_op} observed={observed} below contract floor={floor}"
                )
            rank_complete = rank_complete and complete
            ops.append(
                {
                    "hccl_op": hccl_op,
                    "hccl_accepted": int(hccl_counts.get(hccl_op, 0)),
                    "mspti_op": mspti_op,
                    "mspti_observed": observed,
                    "minimum": floor,
                    "complete": complete,
                }
            )
        per_rank.append(
            {
                "rank": rank,
                "hccl_schedule_match": schedule_match,
                "mapping_complete": rank_complete,
                "ops": ops,
            }
        )
    payload = {
        "schema_version": 1,
        "contract_id": contract_id,
        "contract_sha256": contract_sha,
        "reference_runs": contract.get("reference_runs") or [],
        "evidence_boundary": contract.get("evidence_boundary"),
        "pass": not errors,
        "errors": errors,
        "per_rank": per_rank,
    }
    return payload, errors


def main() -> int:
    args = parse_args()
    root = args.run_dir
    errors: list[str] = []
    per_rank: list[dict] = []

    try:
        manifest = json.loads((root / "planned_manifest.json").read_text())
    except (OSError, ValueError) as exc:
        errors.append(f"cannot read planned_manifest.json: {exc}")
        manifest = {}

    if args.expected_nodes < 1:
        raise SystemExit("--expected-nodes must be positive")
    if args.expected_ranks < 1 or args.expected_ranks % args.expected_nodes:
        raise SystemExit("--expected-ranks must be positive and divisible by --expected-nodes")
    if args.tp_size < 0:
        raise SystemExit("--tp-size must be non-negative")
    if args.min_raw_kernels < 0 or args.min_comm < 0:
        raise SystemExit("frozen reference minima must be non-negative")
    if not 0 < args.raw_completeness_ratio <= 1:
        raise SystemExit("--raw-completeness-ratio must be inside (0, 1]")
    if not 0 < args.comm_completeness_ratio <= 1:
        raise SystemExit("--comm-completeness-ratio must be inside (0, 1]")
    ranks_per_node = args.expected_ranks // args.expected_nodes
    try:
        capture_ranks = normalize_capture_ranks(
            args.capture_ranks, args.expected_ranks
        )
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"invalid --capture-ranks={args.capture_ranks!r}: {exc}") from exc
    if manifest.get("expected_ranks") not in (None, args.expected_ranks):
        errors.append("planned manifest expected_ranks mismatch")
    if manifest.get("nnodes") not in (None, args.expected_nodes):
        errors.append("planned manifest nnodes mismatch")
    if manifest.get("capture_step") not in (None, args.capture_step):
        errors.append("planned manifest capture_step mismatch")
    if "capture_ranks" not in manifest:
        errors.append("planned manifest missing capture_ranks")
    else:
        try:
            manifest_capture = normalize_capture_ranks(
                manifest["capture_ranks"], args.expected_ranks
            )
        except (TypeError, ValueError) as exc:
            errors.append(f"planned manifest capture_ranks is invalid: {exc}")
        else:
            if manifest_capture != capture_ranks:
                errors.append(
                    f"planned manifest capture_ranks={manifest_capture!r} "
                    f"does not match validator={capture_ranks!r}"
                )
    if args.tp_size and manifest.get("tp") not in (None, args.tp_size):
        errors.append(
            f"planned manifest tp={manifest.get('tp')!r} "
            f"does not match validator --tp-size={args.tp_size}"
        )
    if args.expected_so_sha and manifest.get("collector_so_sha256") not in (
        None,
        args.expected_so_sha,
    ):
        errors.append("planned manifest collector SO hash mismatch")
    if args.require_hccl_issued_ledger:
        if not args.expected_hccl_ledger_so_sha:
            errors.append("expected HCCL issued ledger SO hash is required")
        if manifest.get("hccl_issued_ledger_so_sha256") != (
            args.expected_hccl_ledger_so_sha
        ):
            errors.append("planned manifest HCCL issued ledger SO hash mismatch")
    if args.require_hccl_mspti_mapping_contract:
        if not args.require_hccl_issued_ledger:
            errors.append("HCCL/MSPTI mapping contract requires HCCL issued ledger")
        if args.hccl_mspti_mapping_contract is None:
            errors.append("HCCL/MSPTI mapping contract path is required")
    threshold_bindings = {
        "frozen_min_raw_kernels": args.min_raw_kernels,
        "frozen_min_comm": args.min_comm,
    }
    for key, expected in threshold_bindings.items():
        if key not in manifest:
            if args.require_manifest_thresholds:
                errors.append(f"planned manifest missing {key}")
        elif manifest.get(key) != expected:
            errors.append(
                f"planned manifest {key}={manifest.get(key)!r} "
                f"does not match validator={expected!r}"
            )
    manifest_raw_ratio = manifest.get(
        "raw_completeness_ratio", manifest.get("peer_completeness_ratio")
    )
    manifest_comm_ratio = manifest.get(
        "comm_completeness_ratio", manifest.get("peer_completeness_ratio")
    )
    for label, observed, expected in (
        ("raw completeness ratio", manifest_raw_ratio, args.raw_completeness_ratio),
        ("comm completeness ratio", manifest_comm_ratio, args.comm_completeness_ratio),
    ):
        if observed is None:
            if args.require_manifest_thresholds:
                errors.append(f"planned manifest missing {label}")
        elif observed != expected:
            errors.append(
                f"planned manifest {label}={observed!r} "
                f"does not match validator={expected!r}"
            )
    nested = args.node_layout == "nested" or (
        args.node_layout == "auto" and args.expected_nodes > 1
    )
    node_roots = [
        root / "node_runs" / f"node_{node:02d}" if nested else root
        for node in range(args.expected_nodes)
    ]

    old_markers = [
        marker.name
        for marker in (root / "ATTEMPT_COMPLETE", root / "ATTEMPT_INVALID")
        if marker.exists()
    ]
    if old_markers:
        errors.append(f"terminal markers existed before validation: {old_markers}")
    residual_tmp = sorted(str(path.relative_to(root)) for path in root.rglob("*.tmp"))
    if residual_tmp:
        errors.append(f"residual tmp files before validation: {residual_tmp}")

    expected_trace_paths: list[Path] = []
    expected_meta_paths: list[Path] = []
    expected_audit_paths: dict[int, Path] = {}
    trace_rows_by_rank: dict[int, list[dict]] = {}
    for rank in capture_ranks:
        node = rank // ranks_per_node
        expected_trace_paths.append(node_roots[node] / f"rank_{rank:04d}.skeleton.jsonl")
        expected_meta_paths.append(node_roots[node] / f"rank_{rank:04d}.mspti_meta.json")
        expected_audit_paths[rank] = (
            node_roots[node] / f"rank_{rank:04d}.buffer_audit.json"
        )
    traces = sorted(root.rglob("rank_*.skeleton.jsonl"))
    metas = sorted(root.rglob("rank_*.mspti_meta.json"))
    if {path.resolve() for path in traces} != {path.resolve() for path in expected_trace_paths}:
        errors.append(
            f"trace inventory mismatch actual={len(traces)} expected={len(capture_ranks)}"
        )
    if {path.resolve() for path in metas} != {path.resolve() for path in expected_meta_paths}:
        errors.append(
            f"meta inventory mismatch actual={len(metas)} expected={len(capture_ranks)}"
        )
    buffer_audit, buffer_audit_errors = aggregate_buffer_audits(
        root,
        expected_paths=expected_audit_paths,
        output_path=root / "buffer_audit.json",
    )
    errors.extend(buffer_audit_errors)
    hccl_issued: list[dict] = []
    if args.require_hccl_issued_ledger:
        hccl_issued, hccl_errors = validate_hccl_issued_ledgers(
            root,
            node_roots=node_roots,
            capture_ranks=capture_ranks,
            ranks_per_node=ranks_per_node,
            capture_step=args.capture_step,
            expected_so_sha=args.expected_hccl_ledger_so_sha,
        )
        errors.extend(hccl_errors)

    for rank in capture_ranks:
        node = rank // ranks_per_node
        try:
            rows = read_jsonl_strict(node_roots[node] / f"rank_{rank:04d}.skeleton.jsonl")
            trace_rows_by_rank[rank] = rows
            meta = json.loads(
                (node_roots[node] / f"rank_{rank:04d}.mspti_meta.json").read_text()
            )
            per_rank.append(
                validate_rank_rows(
                    rows,
                    expected_rank=rank,
                    capture_step=args.capture_step,
                    capture_window_steps=1,
                    meta=meta,
                    # Keep structurally valid low-count ranks in per_rank so the
                    # frozen reference and peer gates can report distinct truth.
                    min_raw_kernels=None,
                    min_comm=None,
                    require_explicit_finalize=True,
                )
            )
        except (OSError, ValueError, StrictValidationError) as exc:
            errors.append(f"rank={rank}: {exc}")

    raws = [int(item["raw_kernels"]) for item in per_rank]
    comms = [int(item["comm"]) for item in per_rank]
    raw_center = sorted(raws)[len(raws) // 2] if raws else 0
    comm_center = sorted(comms)[len(comms) // 2] if comms else 0
    raw_floor = args.min_raw_kernels
    comm_floor = args.min_comm
    raw_completeness, raw_errors = metric_completeness(
        per_rank,
        metric_key="raw_kernels",
        metric_label="raw",
        reference_min=args.min_raw_kernels,
        peer_ratio=args.raw_completeness_ratio,
    )
    comm_completeness, comm_errors = metric_completeness(
        per_rank,
        metric_key="comm",
        metric_label="comm",
        reference_min=args.min_comm,
        peer_ratio=args.comm_completeness_ratio,
    )
    errors.extend(raw_errors)
    errors.extend(comm_errors)
    topology_diagnostic = topology_coverage(capture_ranks, args.tp_size)

    hccl_mspti_mapping: dict = {}
    if args.require_hccl_mspti_mapping_contract and args.hccl_mspti_mapping_contract is not None:
        hccl_mspti_mapping, mapping_errors = validate_hccl_mspti_mapping(
            contract_path=args.hccl_mspti_mapping_contract,
            manifest=manifest,
            hccl_issued=hccl_issued,
            trace_rows_by_rank=trace_rows_by_rank,
            capture_ranks=capture_ranks,
            capture_step=args.capture_step,
        )
        errors.extend(mapping_errors)
        atomic_json(root / "hccl_mspti_mapping.json", hccl_mspti_mapping)

    launches: list[dict] = []
    done_nodes: list[int] = []
    for node, node_root in enumerate(node_roots):
        launch_path = node_root / f"node_{node}.launch.json"
        try:
            launch = json.loads(launch_path.read_text())
        except (OSError, ValueError) as exc:
            errors.append(f"node={node}: cannot read launch receipt: {exc}")
            launch = {}
        launches.append(launch)
        if (node_root / f"node_{node}.done").exists():
            done_nodes.append(node)
        else:
            errors.append(f"missing node_{node}.done")
        if (node_root / f"node_{node}.fail").exists():
            errors.append(f"node_{node}.fail exists")
        if launch.get("raw_exit_code") != 0:
            errors.append(f"node={node} raw_exit_code={launch.get('raw_exit_code')}")
        if launch.get("arm") != "legacy_mspti":
            errors.append(f"node={node} arm={launch.get('arm')!r}")
        if int_or(launch.get("node_rank")) != node:
            errors.append(f"node={node} launch node_rank={launch.get('node_rank')}")
        if args.expected_so_sha and launch.get("collector_so_sha256_loaded") != args.expected_so_sha:
            errors.append(f"node={node} collector SO hash mismatch")

    receipt_payloads: list[dict] = []
    receipt_paths = sorted(root.glob("node_*.receipt.json"))
    if args.require_receipts and len(receipt_paths) != args.expected_nodes:
        errors.append(
            f"node receipts={len(receipt_paths)} expected={args.expected_nodes}"
        )
    for path in receipt_paths:
        try:
            receipt_payloads.append(json.loads(path.read_text()))
        except (OSError, ValueError) as exc:
            errors.append(f"bad receipt {path.name}: {exc}")
    if args.require_receipts:
        if sorted(int_or(item.get("node_index")) for item in receipt_payloads) != list(
            range(args.expected_nodes)
        ):
            errors.append("receipt node indexes do not exactly match expected nodes")
        for receipt in receipt_payloads:
            node = int_or(receipt.get("node_index"))
            want_start = node * ranks_per_node
            want_end = want_start + ranks_per_node - 1
            if receipt.get("run_rc") != 0:
                errors.append(f"node={node} receipt run_rc={receipt.get('run_rc')}")
            manifest_pods = manifest.get("pods") or []
            expected_pod = (
                manifest_pods[node].get("pod")
                if 0 <= node < len(manifest_pods)
                else None
            )
            if expected_pod and receipt.get("pod_hostname") != expected_pod:
                errors.append(f"node={node} receipt pod hostname mismatch")
            if receipt.get("expected_global_rank_start") != want_start:
                errors.append(f"node={node} receipt rank start mismatch")
            if receipt.get("expected_global_rank_end") != want_end:
                errors.append(f"node={node} receipt rank end mismatch")
            selected_ranks_on_node = [
                rank for rank in capture_ranks if rank // ranks_per_node == node
            ]
            selected_on_node = len(selected_ranks_on_node)
            if receipt.get("trace_count") != selected_on_node:
                errors.append(f"node={node} receipt trace_count mismatch")
            if receipt.get("meta_count") != selected_on_node:
                errors.append(f"node={node} receipt meta_count mismatch")
            if receipt.get("buffer_audit_count") != selected_on_node:
                errors.append(f"node={node} receipt buffer_audit_count mismatch")
            if receipt.get("buffer_audit_ranks") != selected_ranks_on_node:
                errors.append(f"node={node} receipt buffer_audit_ranks mismatch")
            try:
                receipt_trace_ranks = sorted(
                    int(rank) for rank in receipt.get("trace_ranks", [])
                )
            except (TypeError, ValueError):
                receipt_trace_ranks = []
            try:
                receipt_meta_ranks = sorted(
                    int(rank) for rank in receipt.get("meta_ranks", [])
                )
            except (TypeError, ValueError):
                receipt_meta_ranks = []
            if receipt_trace_ranks != selected_ranks_on_node:
                errors.append(
                    f"node={node} receipt trace_ranks={receipt_trace_ranks!r} "
                    f"expected={selected_ranks_on_node!r}"
                )
            if receipt_meta_ranks != selected_ranks_on_node:
                errors.append(
                    f"node={node} receipt meta_ranks={receipt_meta_ranks!r} "
                    f"expected={selected_ranks_on_node!r}"
                )
            if args.expected_so_sha and receipt.get("collector_so_sha256") != args.expected_so_sha:
                errors.append(f"node={node} receipt collector SO hash mismatch")
            if manifest.get("code_hash") and receipt.get("source_code_hash") != manifest.get(
                "code_hash"
            ):
                errors.append(f"node={node} receipt source code hash mismatch")
            if manifest.get("master_addr") and receipt.get("master_addr") != manifest.get(
                "master_addr"
            ):
                errors.append(f"node={node} receipt master address mismatch")
            if manifest.get("master_port") is not None and receipt.get(
                "master_port"
            ) != manifest.get("master_port"):
                errors.append(f"node={node} receipt master port mismatch")

        transport_path = root / "transport_rc.json"
        try:
            transport = json.loads(transport_path.read_text())
        except (OSError, ValueError) as exc:
            errors.append(f"cannot read transport_rc.json: {exc}")
            transport = {}
        expected_transport_keys = [f"node_{node}" for node in range(args.expected_nodes)]
        if sorted(transport) != expected_transport_keys:
            errors.append(
                f"transport keys={sorted(transport)} expected={expected_transport_keys}"
            )
        for key in expected_transport_keys:
            if transport.get(key) != 0:
                errors.append(f"transport {key}={transport.get(key)} expected=0")
    else:
        transport = {}

    drop_total = sum(
        int(item["drop_flags"][key])
        for item in per_rank
        for key in ("allocation", "queue", "parse", "io", "callback", "mspti")
    )
    if drop_total != 0:
        errors.append(f"drop_total={drop_total} expected=0")

    audit_by_rank = {
        int(item["rank"]): item
        for item in buffer_audit.get("per_rank", [])
        if isinstance(item, dict) and isinstance(item.get("rank"), int)
    }
    audit_trace_binding_errors: list[str] = []
    for item in per_rank:
        rank = int(item["rank"])
        counters = (audit_by_rank.get(rank) or {}).get("counters") or {}
        if counters.get("enqueued_kernel") != int(item["raw_kernels"]):
            message = (
                f"rank={rank} audit enqueued_kernel={counters.get('enqueued_kernel')} "
                f"trace/meta raw_kernels={item['raw_kernels']}"
            )
            audit_trace_binding_errors.append(message)
            errors.append(message)
        if counters.get("enqueued_comm") != int(item["comm"]):
            message = (
                f"rank={rank} audit enqueued_comm={counters.get('enqueued_comm')} "
                f"trace COMM+P2P={item['comm']}"
            )
            audit_trace_binding_errors.append(message)
            errors.append(message)
    if audit_trace_binding_errors:
        buffer_audit["errors"] = list(buffer_audit.get("errors") or []) + (
            audit_trace_binding_errors
        )
        buffer_audit["pass"] = False
        atomic_json(root / "buffer_audit.json", buffer_audit)

    raw_peer_evaluable = bool(raw_completeness) and all(
        item["peer_evaluable"] for item in raw_completeness
    )
    comm_peer_evaluable = bool(comm_completeness) and all(
        item["peer_evaluable"] for item in comm_completeness
    )

    payload = {
        "schema_version": 2,
        "pass": not errors,
        "analysis_eligibility": "lossless_gate" if not errors else "invalid",
        "errors": errors,
        "expected_nodes": args.expected_nodes,
        "expected_ranks": args.expected_ranks,
        "capture_step": args.capture_step,
        "capture_ranks": capture_ranks,
        "trace_count": len(traces),
        "meta_count": len(metas),
        "done_nodes": done_nodes,
        "node_receipts": len(receipt_payloads),
        "transport_rc": transport,
        "drop_total": drop_total,
        "buffer_audit_pass": not buffer_audit_errors and not audit_trace_binding_errors,
        "buffer_audit_count": buffer_audit.get("audit_count", 0),
        "buffer_audit_path": "buffer_audit.json",
        "hccl_issued_required": args.require_hccl_issued_ledger,
        "hccl_issued_count": len(hccl_issued),
        "hccl_issued": hccl_issued,
        "hccl_mspti_mapping_required": args.require_hccl_mspti_mapping_contract,
        "hccl_mspti_mapping": hccl_mspti_mapping,
        "raw_kernel_min": min(raws) if raws else 0,
        "raw_kernel_max": max(raws) if raws else 0,
        "raw_kernel_center": raw_center,
        "raw_kernel_floor": raw_floor,
        "frozen_min_raw_kernels": args.min_raw_kernels,
        "raw_completeness_ratio": args.raw_completeness_ratio,
        "raw_complete": bool(raw_completeness) and all(
            item["raw_complete"] for item in raw_completeness
        ),
        "raw_completeness": raw_completeness,
        "raw_reference_complete": bool(raw_completeness) and all(
            item["raw_reference_complete"] for item in raw_completeness
        ),
        "raw_peer_evaluable": raw_peer_evaluable,
        "raw_peer_complete": (
            all(item["raw_peer_complete"] for item in raw_completeness)
            if raw_peer_evaluable
            else None
        ),
        "comm_min": min(comms) if comms else 0,
        "comm_max": max(comms) if comms else 0,
        "comm_center": comm_center,
        "comm_floor": comm_floor,
        "frozen_min_comm": args.min_comm,
        "comm_completeness_ratio": args.comm_completeness_ratio,
        "comm_complete": bool(comm_completeness) and all(
            item["comm_complete"] for item in comm_completeness
        ),
        "comm_completeness": comm_completeness,
        "comm_reference_complete": bool(comm_completeness) and all(
            item["comm_reference_complete"] for item in comm_completeness
        ),
        "comm_peer_evaluable": comm_peer_evaluable,
        "comm_peer_complete": (
            all(item["comm_peer_complete"] for item in comm_completeness)
            if comm_peer_evaluable
            else None
        ),
        "completeness_evaluable": raw_peer_evaluable and comm_peer_evaluable,
        **topology_diagnostic,
        "per_rank": per_rank,
        "collector_so_sha256": sorted(
            {str(item.get("collector_so_sha256_loaded")) for item in launches}
        ),
    }
    status_path = root / f"STRICT_SUBSET{args.expected_ranks}.json"
    atomic_json(status_path, payload)

    excluded = {
        "inventory.json",
        "checksums.sha256",
        "ATTEMPT_COMPLETE",
        "ATTEMPT_INVALID",
    }
    inventory = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name in excluded or path.name.endswith(".tmp"):
            continue
        stat = path.stat()
        inventory.append(
            {
                "path": str(path.relative_to(root)),
                "bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": sha256(path),
            }
        )
    atomic_json(root / "inventory.json", inventory)
    atomic_text(
        root / "checksums.sha256",
        "".join(f"{item['sha256']}  {item['path']}\n" for item in inventory),
    )
    marker = root / ("ATTEMPT_COMPLETE" if payload["pass"] else "ATTEMPT_INVALID")
    atomic_text(marker, "PASS\n" if payload["pass"] else "INVALID\n")
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["pass"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
