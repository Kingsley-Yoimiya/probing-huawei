#!/usr/bin/env python3
"""Unit tests for D51 Wait DAG V1 (plan step 8)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analyze_event_pairs import RecordKey, TaskRow, TraceRecord  # noqa: E402
from wait_dag_build import (  # noqa: E402
    WaitDagBuilder,
    a5_edge_from_chain,
    build_unknown_frontier,
    classify_non_a6_wait,
    detect_fifo_ambiguity,
    evaluate_slice_a_acceptance,
    event_generation_src_by_a4_only,
    fifo_ambiguous_stream_ids,
    profiler_fifo_endpoints,
    project_cann_to_task,
)
from wait_dag_schema import (  # noqa: E402
    A6_RECORD_STREAM,
    A6_WAIT_CALL_SEQUENCES,
    A6_WAIT_STREAM,
    CausalEdge,
    DagNode,
    DENOMINATORS,
    UnknownEntry,
    build_allowed_claims,
    record_node_id,
    wait_node_id,
)


def _rec(seq: int, stream: int = 100, event: int = 0xABC) -> TraceRecord:
    return TraceRecord(
        op=4,
        call_sequence=seq,
        slot_sequence=seq,
        pid=1,
        tid=2,
        rank=0,
        enter_realtime_ns=1000 + seq,
        exit_realtime_ns=1001 + seq,
        enter_monotonic_ns=0,
        exit_monotonic_ns=0,
        raw_event=event,
        raw_stream=stream,
        acl_ret=0,
        committed=1,
        source=0,
        resolver_path=0,
        nested_under_acl=0,
        parent_acl_call_sequence=0,
        flags=0,
    )


def _wait(seq: int, stream: int = 200, event: int = 0xABC) -> TraceRecord:
    r = _rec(seq, stream, event)
    return TraceRecord(op=5, **{k: v for k, v in r.__dict__.items() if k != "op"})


def _baseline_ok() -> dict:
    return {
        "a2": {"preload_record": 51, "preload_wait": 24, "api_unmatched": 0},
        "a4_active": 24,
        "a5_pass": 12,
        "a6_pass": 12,
    }


class TestEventGenerationPositive(unittest.TestCase):
    def test_record_to_wait_direction(self):
        rk = RecordKey(1, 0xABC, 1, 0, 1)
        src = record_node_id(rk.pid, rk.raw_event, rk.lifetime_epoch, rk.reset_epoch, rk.record_epoch)
        dst = wait_node_id(1, 20)
        edge = CausalEdge(
            edge_id="eg_1",
            src=src,
            dst=dst,
            edge_type="event_generation",
            evidence_tier="proven_event_generation",
            identity_source="event_generation",
        )
        self.assertEqual(edge.src, src)
        self.assertEqual(edge.dst, dst)
        self.assertTrue(src.startswith("event_record:"))
        self.assertTrue(dst.startswith("event_wait:"))


class TestFifoPositive(unittest.TestCase):
    def test_three_tasks_two_adjacent_edges(self):
        tasks = [
            TaskRow(1, 10, 5, 1, 100, 200),
            TaskRow(2, 11, 5, 1, 201, 300),
            TaskRow(3, 12, 5, 1, 301, 400),
        ]
        issues = detect_fifo_ambiguity(tasks)
        self.assertEqual(issues, [])
        edges = []
        for i in range(len(tasks) - 1):
            edges.append((tasks[i].rowid, tasks[i + 1].rowid))
        self.assertEqual(edges, [(1, 2), (2, 3)])
        self.assertNotIn((1, 3), edges)


class TestMissingTaskPositive(unittest.TestCase):
    def test_event_edge_with_projection_unknown_on_builder_output(self):
        builder = WaitDagBuilder(
            trace_dir=Path("/tmp"),
            db_path=Path("/tmp/x.db"),
            active_start=0,
            active_end=10**18,
            pid_min=0,
            pid_max=99999,
            run_id="test",
        )
        wnid = wait_node_id(1, 221)
        builder.nodes[wnid] = DagNode(wnid, "event_wait", "k", {})
        rec_nid = record_node_id(1, 0xABC, 1, 0, 1)
        builder.causal_edges.append(
            CausalEdge(
                edge_id="eg",
                src=rec_nid,
                dst=wnid,
                edge_type="event_generation",
                evidence_tier="proven_event_generation",
                identity_source="event_generation",
            )
        )
        builder._add_unknown(
            wnid,
            "profiler_task_projection",
            "EVENT_WAIT TASK",
            "event_task_projection_missing",
            "cid=5559",
            "unique EVENT_WAIT TASK at CANN cid",
            True,
            True,
            False,
        )
        eg = [e for e in builder.causal_edges if e.edge_type == "event_generation"]
        unk_edges = [e for e in builder.causal_edges if e.evidence_tier == "unknown"]
        unk_rows = [u for u in builder.unknowns if u.reason_code == "event_task_projection_missing"]
        self.assertEqual(len(eg), 1)
        self.assertEqual(len(unk_rows), 1)
        self.assertTrue(any(e.dst.startswith("unknown:") for e in unk_edges))


class TestIdentityNegatives(unittest.TestCase):
    def test_cid_equal_without_type_compatible_no_projection(self):
        cann = {"connection_id": 5559, "start_ns": 100, "end_ns": 101, "global_tid": (1 << 32) | 2}
        tasks_by_cid = {
            5559: [
                TaskRow(99, 5559, 7, 42, 100, 200),
            ]
        }
        used: set[int] = set()
        task, status = project_cann_to_task(
            cann,
            "aclrtStreamWaitEvent",
            tasks_by_cid,
            {42: "KERNEL_AICORE"},
            10,
            20,
            used,
        )
        self.assertIsNone(task)
        self.assertIn("not_unique", status)

    def test_task_reuse_rejected(self):
        cann = {"connection_id": 1, "start_ns": 100, "end_ns": 101, "global_tid": (1 << 32) | 2}
        tasks_by_cid = {1: [TaskRow(5, 1, 3, 20, 100, 200)]}
        used = {5}
        task, status = project_cann_to_task(
            cann,
            "aclrtStreamWaitEvent",
            tasks_by_cid,
            {20: "EVENT_WAIT"},
            10,
            20,
            used,
        )
        self.assertIsNone(task)
        self.assertEqual(status, "task_rowid_reused")

    def test_nearer_foreign_record_not_used_via_a4_helper(self):
        bound = RecordKey(1, 0xABC, 1, 0, 1)
        foreign = RecordKey(1, 0xDEF, 1, 0, 2)
        wait_bindings = {20: bound}
        record_nid_by_tuple = {
            (1, 0xABC, 1, 0, 1): record_node_id(1, 0xABC, 1, 0, 1),
            (1, 0xDEF, 1, 0, 2): record_node_id(1, 0xDEF, 1, 0, 2),
        }
        src = event_generation_src_by_a4_only(
            20, wait_bindings, record_nid_by_tuple, foreign
        )
        self.assertEqual(src, record_nid_by_tuple[(1, 0xABC, 1, 0, 1)])
        self.assertNotEqual(src, record_nid_by_tuple[(1, 0xDEF, 1, 0, 2)])

    def test_cid_collision_does_not_create_a5_edge(self):
        chain = {
            "terminal_rowid": 10,
            "event_record_rowid": 20,
            "cid_only_match": True,
        }
        self.assertIsNone(a5_edge_from_chain(chain))
        rejected = {"reject_reason": "not_structural", "terminal_rowid": 10, "event_record_rowid": 20}
        self.assertIsNone(a5_edge_from_chain(rejected))

    def test_raw_stream_equals_profiler_streamId_no_cross_domain_fifo(self):
        preload_raw = 94341958677056
        t_stream2 = TaskRow(1, 1, 1, 2, 100, 200)
        t_stream4 = TaskRow(2, 2, 2, 4, 201, 300)
        t_numeric_collision = TaskRow(3, 3, 3, preload_raw, 100, 200)
        self.assertIsNone(profiler_fifo_endpoints(t_stream2, t_numeric_collision))
        self.assertIsNone(profiler_fifo_endpoints(t_stream4, t_numeric_collision))

    def test_builder_output_has_no_cross_domain_stream_edge_type(self):
        builder = WaitDagBuilder(
            trace_dir=Path("/tmp"),
            db_path=Path("/tmp/x.db"),
            active_start=0,
            active_end=10**18,
            pid_min=0,
            pid_max=99999,
            run_id="test",
        )
        t1 = TaskRow(1, 1, 1, 94341958677056, 100, 200)
        t2 = TaskRow(2, 2, 2, 94341958677056, 201, 300)
        if profiler_fifo_endpoints(t1, t2) is not None:
            a, b = profiler_fifo_endpoints(t1, t2)
            builder.causal_edges.append(
                CausalEdge(
                    edge_id="fifo_bad",
                    src=f"task:{a}",
                    dst=f"task:{b}",
                    edge_type="profiler_same_stream_fifo",
                    evidence_tier="observed_structural",
                    identity_source="profiler_same_stream_fifo",
                )
            )
        edge_types = {e.edge_type for e in builder.causal_edges}
        self.assertNotIn("raw_stream_equals_profiler_streamId", edge_types)
        self.assertEqual(len(builder.causal_edges), 0)


class TestFifoNegatives(unittest.TestCase):
    def test_different_streams_no_fifo_endpoints(self):
        t1 = TaskRow(1, 1, 1, 1, 100, 200)
        t2 = TaskRow(2, 2, 2, 1, 201, 300)
        self.assertIsNone(profiler_fifo_endpoints(t1, t2))

    def test_overlap_produces_ambiguity(self):
        tasks = [
            TaskRow(1, 1, 1, 1, 100, 250),
            TaskRow(2, 2, 1, 1, 200, 300),
        ]
        issues = detect_fifo_ambiguity(tasks)
        self.assertTrue(any("overlap" in i for i in issues))


class TestNonA6Classification(unittest.TestCase):
    def test_a6_polarity_only_is_unclassified(self):
        wait_rec = _wait(144, A6_RECORD_STREAM)
        record_rec = _rec(100, A6_WAIT_STREAM)
        rec_task = TaskRow(1, 1, 3, 2, 100, 200)
        wait_task = TaskRow(2, 2, 4, 4, 300, 400)
        semantic, reason, blockers, required = classify_non_a6_wait(
            wait_rec,
            record_rec,
            rec_task,
            wait_task,
            {2, 4},
        )
        self.assertEqual(semantic, "unclassified")
        self.assertIn("a6_stream_polarity_observation_only", blockers)
        self.assertIn("fifo_order_ambiguous", ";".join(blockers))
        self.assertTrue(required)
        self.assertNotEqual(semantic, "compute_ready_to_comm_candidate")


class TestDenominatorNegatives(unittest.TestCase):
    def test_wrong_denominators_fail_acceptance(self):
        ok_base = _baseline_ok()
        cases = [
            ("23/24", {**ok_base, "a2": {**ok_base["a2"], "preload_wait": 23}}),
            ("50/51", {**ok_base, "a2": {**ok_base["a2"], "preload_record": 50}}),
            ("11/12", {**ok_base, "a5_pass": 11, "a6_pass": 11}),
        ]
        for label, baseline in cases:
            passed, failures = evaluate_slice_a_acceptance(
                baseline, eg_count=24, cycles=[], a6_cs_ok=True
            )
            self.assertFalse(passed, label)
            self.assertTrue(len(failures) > 0, label)

    def test_event_generation_23_of_24_fails(self):
        passed, failures = evaluate_slice_a_acceptance(
            _baseline_ok(), eg_count=23, cycles=[], a6_cs_ok=True
        )
        self.assertFalse(passed)
        self.assertIn("event_generation=23", failures)


class TestClaimsAndFrontier(unittest.TestCase):
    def test_zero_fifo_claims_no_replay_wording(self):
        coverage = {
            "profiler_same_stream_fifo": {
                "observed_structural": 0,
                "unknown": 2,
            }
        }
        claims = build_allowed_claims(coverage)
        self.assertEqual(len(claims), 1)
        self.assertIn("零条 FIFO 边", claims[0])
        self.assertNotIn("同流观察序可重放", claims[0])

    def test_unknown_frontier_nonempty_when_anchor_reachable(self):
        rec = record_node_id(1, 1, 1, 0, 1)
        wait = wait_node_id(1, 20)
        unk_id = "unknown:event_task_projection_missing:event_wait_1_20:0"
        nodes = {
            rec: DagNode(rec, "event_record_generation", "k", {}),
            wait: DagNode(wait, "event_wait", "k", {}),
            unk_id: DagNode(unk_id, "unknown_stub", "k", {}),
        }
        edges = [
            CausalEdge(
                edge_id="eg",
                src=rec,
                dst=wait,
                edge_type="event_generation",
                evidence_tier="proven_event_generation",
                identity_source="event_generation",
            ),
            CausalEdge(
                edge_id="unk",
                src=wait,
                dst=unk_id,
                edge_type="unknown_dependency",
                evidence_tier="unknown",
                identity_source="unclassified",
            ),
        ]
        unknowns = [
            UnknownEntry(
                unknown_id=unk_id,
                anchor_node=wait,
                missing_edge_type="profiler_task_projection",
                missing_peer_role="EVENT_WAIT TASK",
                reason_code="event_task_projection_missing",
                observed_evidence="cid=5559",
                required_evidence="TASK",
                blocks_strict_cone=True,
                blocks_observed_cone=True,
                slice_b_can_resolve=False,
            )
        ]
        rows = build_unknown_frontier(
            nodes, edges, unknowns, {"proven_event_generation"}, "strict"
        )
        self.assertTrue(any(r["start_node"] == rec for r in rows))
        self.assertTrue(any("event_task_projection_missing" in r["unknown_frontier_reasons"] for r in rows))


class TestA6Replay(unittest.TestCase):
    def test_frozen_cs_set(self):
        self.assertEqual(
            A6_WAIT_CALL_SEQUENCES,
            frozenset({176, 177, 178, 179, 221, 222, 223, 224, 266, 267, 268, 269}),
        )
        self.assertEqual(len(A6_WAIT_CALL_SEQUENCES), 12)

    def test_a6_streams_frozen(self):
        self.assertEqual(A6_RECORD_STREAM, 94341995468752)
        self.assertEqual(A6_WAIT_STREAM, 94341958677056)


if __name__ == "__main__":
    unittest.main()
