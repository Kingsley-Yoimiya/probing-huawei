#!/usr/bin/env python3
"""Re-derive FIX Slice A artifacts from sealed graph.json (no DB re-read)."""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from analyze_event_pairs import TaskRow, TraceRecord  # noqa: E402
from wait_dag_build import (  # noqa: E402
    build_unknown_frontier,
    classify_non_a6_wait,
    write_csv,
)
from wait_dag_casebook import build_wait_dag_casebook, write_claims_md, write_schema_md  # noqa: E402
from wait_dag_schema import (  # noqa: E402
    A6_WAIT_CALL_SEQUENCES,
    A6_WAIT_STREAM,
    A6_RECORD_STREAM,
    CausalEdge,
    DagNode,
    UnknownEntry,
    build_allowed_claims,
)


def _load_nodes(graph: dict) -> dict[str, DagNode]:
    out: dict[str, DagNode] = {}
    for n in graph["nodes"]:
        attrs = dict(n.get("attrs") or {})
        for k, v in n.items():
            if k not in ("node_id", "node_type", "primary_key", "attrs"):
                attrs[k] = v
        out[n["node_id"]] = DagNode(
            node_id=n["node_id"],
            node_type=n["node_type"],
            primary_key=n["primary_key"],
            attrs=attrs,
        )
    return out


def _load_edges(graph: dict) -> list[CausalEdge]:
    edges: list[CausalEdge] = []
    for e in graph["causal_edges"]:
        edges.append(CausalEdge(**{k: v for k, v in e.items() if k in CausalEdge.__dataclass_fields__}))
    return edges


def _load_unknowns(graph: dict) -> list[UnknownEntry]:
    return [UnknownEntry(**u) for u in graph["unknowns"]]


def _task_from_rowid(nodes: dict[str, DagNode], rowid: int | None) -> TaskRow | None:
    if rowid is None:
        return None
    n = nodes.get(f"task:{rowid}")
    if not n:
        return None
    return TaskRow(
        rowid=int(rowid),
        connection_id=int(n.attrs.get("connection_id") or 0),
        task_type=0,
        stream_id=int(n.attrs.get("stream_id") or 0),
        start_ns=int(n.attrs.get("start_ns") or 0),
        end_ns=int(n.attrs.get("end_ns") or 0),
    )


def _fifo_ambiguous_streams(nodes: dict[str, DagNode], unknowns: list[UnknownEntry]) -> set[int]:
    streams: set[int] = set()
    for u in unknowns:
        if u.reason_code != "fifo_order_ambiguous":
            continue
        anchor = nodes.get(u.anchor_node)
        if anchor and anchor.attrs.get("stream_id") is not None:
            streams.add(int(anchor.attrs["stream_id"]))
    return streams


def reclassify_waits(
    nodes: dict[str, DagNode],
    edges: list[CausalEdge],
    fifo_ambiguous: set[int],
) -> list[dict]:
    eg_by_wait = {e.dst: e.src for e in edges if e.edge_type == "event_generation"}
    rows: list[dict] = []
    for nid, node in sorted(nodes.items()):
        if node.node_type != "event_wait":
            continue
        cs = int(node.attrs.get("call_sequence", 0))
        parts = nid.split(":")
        pid = int(parts[1]) if len(parts) >= 3 and parts[0] == "event_wait" else 1
        wait_rec = TraceRecord(
            op=5,
            call_sequence=cs,
            slot_sequence=cs,
            pid=pid,
            tid=0,
            rank=0,
            enter_realtime_ns=0,
            exit_monotonic_ns=0,
            exit_realtime_ns=0,
            enter_monotonic_ns=0,
            raw_event=0,
            raw_stream=int(node.attrs.get("raw_stream", 0)),
            acl_ret=0,
            committed=1,
            source=0,
            resolver_path=0,
            nested_under_acl=0,
            parent_acl_call_sequence=0,
            flags=0,
        )
        record_nid = eg_by_wait.get(nid)
        record_rec = None
        rec_task = None
        wait_task = None
        if record_nid and record_nid in nodes:
            rn = nodes[record_nid]
            record_rec = TraceRecord(
                op=4,
                call_sequence=int(rn.attrs.get("call_sequence", 0)),
                slot_sequence=0,
                pid=0,
                tid=0,
                rank=0,
                enter_realtime_ns=0,
                exit_monotonic_ns=0,
                exit_realtime_ns=0,
                enter_monotonic_ns=0,
                raw_event=0,
                raw_stream=int(rn.attrs.get("raw_stream", 0)),
                acl_ret=0,
                committed=1,
                source=0,
                resolver_path=0,
                nested_under_acl=0,
                parent_acl_call_sequence=0,
                flags=0,
            )
            rtr = rn.attrs.get("task_rowid")
            if rtr:
                rec_task = _task_from_rowid(nodes, int(rtr))
        wtr = node.attrs.get("task_rowid")
        if wtr:
            wait_task = _task_from_rowid(nodes, int(wtr))

        if cs in A6_WAIT_CALL_SEQUENCES:
            rows.append(
                {
                    "wait_call_sequence": cs,
                    "semantic_class": "comm_completion_to_compute",
                    "classification_reason": "a6_frozen_replay",
                    "record_raw_stream": record_rec.raw_stream if record_rec else None,
                    "wait_raw_stream": wait_rec.raw_stream,
                    "a6_member": True,
                    "blockers": "",
                    "required_evidence": "",
                }
            )
            continue

        semantic, reason, blockers, required = classify_non_a6_wait(
            wait_rec,
            record_rec,
            rec_task,
            wait_task,
            fifo_ambiguous,
        )
        rows.append(
            {
                "wait_call_sequence": cs,
                "semantic_class": semantic,
                "classification_reason": reason,
                "record_raw_stream": record_rec.raw_stream if record_rec else None,
                "wait_raw_stream": wait_rec.raw_stream,
                "a6_member": False,
                "blockers": ";".join(blockers),
                "required_evidence": required,
            }
        )
    return rows


def refix_from_sealed(src_analysis: Path, dst_root: Path, run_id: str) -> None:
    graph = json.loads((src_analysis / "graph.json").read_text())
    nodes = _load_nodes(graph)
    edges = _load_edges(graph)
    unknowns = _load_unknowns(graph)
    coverage = graph["coverage"]
    acceptance = graph.get("acceptance", {})

    fifo_ambiguous = _fifo_ambiguous_streams(nodes, unknowns)
    wait_rows = reclassify_waits(nodes, edges, fifo_ambiguous)

    frontier_strict = build_unknown_frontier(
        nodes, edges, unknowns, {"proven_event_generation"}, "strict"
    )
    frontier_observed = build_unknown_frontier(
        nodes,
        edges,
        unknowns,
        {"proven_event_generation", "observed_structural"},
        "observed",
    )
    frontier_rows = frontier_strict + frontier_observed

    dst_analysis = dst_root / "analysis"
    dst_analysis.mkdir(parents=True, exist_ok=True)

    graph["run_id"] = run_id
    graph["claims"] = {
        "allowed": build_allowed_claims(coverage),
        "forbidden": graph.get("claims", {}).get("forbidden", []),
    }
    (dst_analysis / "graph.json").write_text(json.dumps(graph, indent=2, ensure_ascii=False))

    for name in (
        "nodes.csv",
        "edges.csv",
        "identity_links.csv",
        "unknowns.csv",
        "coverage.json",
        "record_without_wait.csv",
        "reachable_nodes.csv",
        "acceptance.json",
    ):
        src = src_analysis / name
        if src.exists():
            shutil.copy2(src, dst_analysis / name)

    write_csv(dst_analysis / "wait_classification.csv", wait_rows)
    write_csv(dst_analysis / "unknown_frontier.csv", frontier_rows)
    write_claims_md(dst_analysis / "claims.md", acceptance, coverage)
    write_schema_md(dst_analysis / "schema.md")

    casebook = build_wait_dag_casebook(
        nodes,
        edges,
        unknowns,
        wait_rows,
        list(csv.DictReader((src_analysis / "record_without_wait.csv").open())),
        [],
        acceptance,
    )
    (dst_analysis / "wait_dag_casebook.md").write_text(casebook, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-analysis", required=True)
    ap.add_argument("--dst-root", required=True)
    ap.add_argument("--run-id", default="")
    args = ap.parse_args()
    run_id = args.run_id or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_d51_wait_dag_v1_slice_a_fix"
    )
    refix_from_sealed(Path(args.src_analysis), Path(args.dst_root), run_id)
    print(f"refix done run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
