#!/usr/bin/env python3
"""Unit tests for D51 Wait DAG V2 pair-level FIFO and reverse Wait predicate."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analyze_event_pairs import TaskRow  # noqa: E402
from wait_dag_build import profiler_fifo_endpoints  # noqa: E402
from wait_dag_v2_fifo import (  # noqa: E402
    build_fifo_adjacency,
    build_pair_level_fifo,
    evaluate_adjacent_pair,
    evaluate_reverse_wait_predicate,
    sort_active_tasks,
)
from wait_dag_v2_cone import (  # noqa: E402
    build_reverse_wait_witness,
    walk_fifo_path,
)
from wait_dag_v2_schema import A6_RECORD_STREAM, A6_WAIT_STREAM, NON_A6_WAIT_CS  # noqa: E402


class _EdgeIdGen:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}_{self.n}"


def _pair_audit(stream_id: int, tasks: list[TaskRow], active_start=0, active_end=10**18):
    stream_tasks = {stream_id: tasks}
    edges, rows, unknowns, meta = build_pair_level_fifo(
        stream_tasks, active_start, active_end, _EdgeIdGen()
    )
    return edges, rows, unknowns, meta


class TestFifoPositive(unittest.TestCase):
    def test_three_tasks_two_adjacent_edges(self):
        tasks = [
            TaskRow(1, 10, 5, 1, 100, 200),
            TaskRow(2, 11, 5, 1, 201, 300),
            TaskRow(3, 12, 5, 1, 301, 400),
        ]
        edges, rows, unknowns, meta = _pair_audit(5, tasks)
        self.assertEqual(len(edges), 2)
        self.assertEqual(
            [(int(e.src.split(":")[1]), int(e.dst.split(":")[1])) for e in edges],
            [(1, 2), (2, 3)],
        )
        self.assertEqual(meta["sortable_pair_count"], 2)
        self.assertEqual(len(unknowns), 0)
        self.assertNotIn((1, 3), [(r["src_rowid"], r["dst_rowid"]) for r in rows if r["verdict"] == "sortable"])

    def test_boundary_end_equals_start_sortable(self):
        tasks = [
            TaskRow(1, 10, 5, 1, 100, 200),
            TaskRow(2, 11, 5, 1, 200, 300),
        ]
        verdict, reason = evaluate_adjacent_pair(tasks[0], tasks[1])
        self.assertEqual(verdict, "sortable")
        self.assertEqual(reason, "adjacent_nonoverlap")


class TestFifoMixed(unittest.TestCase):
    def test_overlap_does_not_zero_whole_stream(self):
        tasks = [
            TaskRow(1, 10, 5, 1, 100, 200),
            TaskRow(2, 11, 5, 1, 201, 280),
            TaskRow(3, 12, 5, 1, 250, 350),
            TaskRow(4, 13, 5, 1, 351, 450),
        ]
        edges, rows, unknowns, meta = _pair_audit(5, tasks)
        edge_pairs = {(int(e.src.split(":")[1]), int(e.dst.split(":")[1])) for e in edges}
        self.assertEqual(edge_pairs, {(1, 2), (3, 4)})
        self.assertEqual(len(unknowns), 1)
        self.assertEqual(unknowns[0]["reason_code"], "adjacent_interval_overlap")
        self.assertEqual(meta["sortable_pair_count"], 2)
        self.assertEqual(meta["overlap_pair_count"], 1)


class TestFifoNegatives(unittest.TestCase):
    def test_overlap_no_edge(self):
        tasks = [
            TaskRow(1, 1, 1, 1, 100, 250),
            TaskRow(2, 2, 1, 1, 200, 300),
        ]
        edges, _, unknowns, _ = _pair_audit(1, tasks)
        self.assertEqual(len(edges), 0)
        self.assertEqual(len(unknowns), 1)

    def test_cross_stream_no_edge(self):
        t1 = TaskRow(1, 1, 1, 1, 100, 200)
        t2 = TaskRow(2, 2, 2, 2, 201, 300)
        self.assertIsNone(profiler_fifo_endpoints(t1, t2))
        stream_tasks = {1: [t1], 2: [t2]}
        edges, _, _, _ = build_pair_level_fifo(stream_tasks, 0, 10**18, _EdgeIdGen())
        self.assertEqual(len(edges), 0)

    def test_raw_stream_numeric_collision_no_cross_domain(self):
        preload_raw = 94341958677056
        t_stream2 = TaskRow(1, 1, 1, 2, 100, 200)
        t_collision = TaskRow(2, 2, 2, preload_raw, 201, 300)
        self.assertIsNone(profiler_fifo_endpoints(t_stream2, t_collision))

    def test_non_adjacent_no_direct_edge(self):
        tasks = [
            TaskRow(1, 10, 5, 1, 100, 200),
            TaskRow(2, 11, 5, 1, 201, 300),
            TaskRow(3, 12, 5, 1, 401, 500),
        ]
        edges, _, _, _ = _pair_audit(5, tasks)
        pairs = {(int(e.src.split(":")[1]), int(e.dst.split(":")[1])) for e in edges}
        self.assertIn((1, 2), pairs)
        self.assertIn((2, 3), pairs)
        self.assertNotIn((1, 3), pairs)


class TestPairAuditAccounting(unittest.TestCase):
    def test_sortable_plus_overlap_plus_malformed_equals_adjacent(self):
        tasks = [
            TaskRow(1, 1, 1, 1, 100, 200),
            TaskRow(2, 2, 1, 1, 150, 250),
            TaskRow(3, 3, 1, 1, 300, 400),
        ]
        _, _, _, meta = _pair_audit(1, tasks)
        self.assertEqual(
            meta["sortable_pair_count"] + meta["overlap_pair_count"] + meta["malformed_pair_count"],
            meta["adjacent_pair_count"],
        )


class TestReversePredicateFixture(unittest.TestCase):
    def test_non_a6_set_frozen(self):
        self.assertEqual(
            NON_A6_WAIT_CS,
            frozenset({144, 153, 162, 171, 189, 198, 207, 216, 234, 243, 252, 261}),
        )

    def test_missing_projection_stays_unclassified(self):
        class _Rec:
            raw_stream = 94341958677056

        class _Wait:
            raw_stream = 94341995468752

        semantic, trace, summary = evaluate_reverse_wait_predicate(
            144,
            _Wait(),
            _Rec(),
            None,
            None,
            {"wait_bindings": {144: object()}},
            {},
            {},
            {},
            set(),
            [],
            {},
            {},
            0,
            10**18,
        )
        self.assertEqual(semantic, "unclassified")
        c3 = next(t for t in trace if t["condition"] == "C3")
        self.assertFalse(c3["passed"])


def _synthetic_reverse_fixture(
    *,
    record_stream=A6_WAIT_STREAM,
    wait_stream=A6_RECORD_STREAM,
    record_task=None,
    wait_task=None,
    fifo_fwd=None,
    fifo_rev=None,
    overlap_pairs=None,
    comm_ops=None,
    tasks_by_cid=None,
    all_tasks_by_rowid=None,
    wait_cs=144,
):
    class _Rec:
        raw_stream = record_stream

    class _Wait:
        raw_stream = wait_stream

    if record_task is None:
        record_task = TaskRow(102, 900, 2, 10, 300, 400)
    if wait_task is None:
        wait_task = TaskRow(103, 901, 4, 11, 500, 600)
    if fifo_fwd is None:
        fifo_fwd = {100: [101], 101: [102], 103: [104]}
    if fifo_rev is None:
        fifo_rev = {101: [100], 102: [101], 104: [103]}
    if overlap_pairs is None:
        overlap_pairs = set()
    if all_tasks_by_rowid is None:
        all_tasks_by_rowid = {
            100: TaskRow(100, 1, 2, 1, 100, 200),
            101: TaskRow(101, 2, 2, 20, 201, 299),
            102: record_task,
            103: wait_task,
            104: TaskRow(104, 902, 4, 12, 601, 700),
        }
    if comm_ops is None:
        comm_ops = [{"op_name": "hcom_allReduce__test_0_1", "connection_id": 999}]
    if tasks_by_cid is None:
        tasks_by_cid = {999: [all_tasks_by_rowid[104]]}
    string_ids = {1: "KERNEL_AIVEC", 10: "EVENT_RECORD", 11: "EVENT_WAIT", 12: "AI_CORE", 20: "MEMCPY"}
    return evaluate_reverse_wait_predicate(
        wait_cs,
        _Wait(),
        _Rec(),
        record_task,
        wait_task,
        {"wait_bindings": {wait_cs: object()}},
        all_tasks_by_rowid,
        fifo_fwd,
        fifo_rev,
        overlap_pairs,
        comm_ops,
        tasks_by_cid,
        string_ids,
        0,
        10**18,
    )


class TestReversePredicateSynthetic(unittest.TestCase):
    def test_c1_to_c8_positive_passes(self):
        semantic, trace, summary = _synthetic_reverse_fixture()
        self.assertEqual(semantic, "compute_ready_to_comm_candidate")
        self.assertTrue(all(t["passed"] for t in trace))
        self.assertEqual(summary["kernel_predecessor_rowid"], 100)
        self.assertEqual(summary["comm_successor_rowid"], 104)

    def test_stream_swap_only_fails_not_pass(self):
        semantic, trace, _ = _synthetic_reverse_fixture(
            record_stream=A6_RECORD_STREAM,
            wait_stream=A6_WAIT_STREAM,
        )
        self.assertEqual(semantic, "unclassified")
        c2 = next(t for t in trace if t["condition"] == "C2")
        self.assertFalse(c2["passed"])
        self.assertTrue(any(not t["passed"] for t in trace if t["condition"] != "C2"))

    def test_overlap_frontier_blocks_c4(self):
        semantic, trace, _ = _synthetic_reverse_fixture(
            overlap_pairs={(100, 101)},
            fifo_rev={102: [101], 104: [103], 101: [100]},
            all_tasks_by_rowid={
                100: TaskRow(100, 1, 2, 1, 100, 200),
                101: TaskRow(101, 2, 2, 20, 201, 299),
                102: TaskRow(102, 900, 2, 10, 300, 400),
                103: TaskRow(103, 901, 4, 11, 500, 600),
                104: TaskRow(104, 902, 4, 12, 601, 700),
            },
        )
        self.assertEqual(semantic, "unclassified")
        c4 = next(t for t in trace if t["condition"] == "C4")
        self.assertFalse(c4["passed"])
        self.assertIn("overlap", c4["detail"])

    def test_q_not_first_comm_entry_fails_c6(self):
        comm_entry = TaskRow(105, 903, 4, 12, 650, 750)
        wait_task = TaskRow(103, 901, 4, 11, 700, 800)
        earlier_comm = TaskRow(106, 904, 4, 12, 500, 600)
        semantic, trace, _ = _synthetic_reverse_fixture(
            wait_task=wait_task,
            fifo_fwd={100: [101], 101: [102], 103: [105]},
            fifo_rev={101: [100], 102: [101], 105: [103]},
            all_tasks_by_rowid={
                100: TaskRow(100, 1, 2, 1, 100, 200),
                101: TaskRow(101, 2, 2, 20, 201, 299),
                102: TaskRow(102, 900, 2, 10, 300, 400),
                103: wait_task,
                105: comm_entry,
                106: earlier_comm,
            },
            tasks_by_cid={999: [earlier_comm, comm_entry]},
        )
        self.assertEqual(semantic, "unclassified")
        c6 = next(t for t in trace if t["condition"] == "C6")
        self.assertFalse(c6["passed"])

    def test_multiple_fifo_successors_fail(self):
        semantic, trace, _ = _synthetic_reverse_fixture(
            fifo_fwd={100: [101], 101: [102], 103: [104, 105]},
            fifo_rev={101: [100], 102: [101], 104: [103], 105: [103]},
            all_tasks_by_rowid={
                100: TaskRow(100, 1, 2, 1, 100, 200),
                101: TaskRow(101, 2, 2, 20, 201, 299),
                102: TaskRow(102, 900, 2, 10, 300, 400),
                103: TaskRow(103, 901, 4, 11, 500, 600),
                104: TaskRow(104, 902, 4, 12, 601, 700),
                105: TaskRow(105, 903, 4, 12, 701, 800),
            },
        )
        self.assertEqual(semantic, "unclassified")
        c5 = next(t for t in trace if t["condition"] == "C5")
        self.assertFalse(c5["passed"])

    def test_multiple_kernel_predecessors_fail(self):
        fifo_fwd = {100: [102], 101: [102], 103: [104]}
        fifo_rev = {102: [100, 101], 104: [103]}
        semantic, trace, _ = _synthetic_reverse_fixture(
            fifo_fwd=fifo_fwd,
            fifo_rev=fifo_rev,
            all_tasks_by_rowid={
                100: TaskRow(100, 1, 2, 1, 100, 200),
                101: TaskRow(101, 2, 2, 1, 150, 250),
                102: TaskRow(102, 900, 2, 10, 300, 400),
                103: TaskRow(103, 901, 4, 11, 500, 600),
                104: TaskRow(104, 902, 4, 12, 601, 700),
            },
        )
        self.assertEqual(semantic, "unclassified")
        c4 = next(t for t in trace if t["condition"] == "C4")
        self.assertFalse(c4["passed"])


class TestConeWitnessSynthetic(unittest.TestCase):
    def test_walk_fifo_no_flood_to_unrelated_task(self):
        fifo_fwd = {17: [18], 18: [19], 19: [709], 20: [21]}
        edge_by_pair = {
            (17, 18): type("E", (), {"edge_id": "f1", "edge_type": "profiler_same_stream_fifo", "evidence_tier": "observed_structural"})(),
            (18, 19): type("E", (), {"edge_id": "f2", "edge_type": "profiler_same_stream_fifo", "evidence_tier": "observed_structural"})(),
            (19, 709): type("E", (), {"edge_id": "f3", "edge_type": "profiler_same_stream_fifo", "evidence_tier": "observed_structural"})(),
            (20, 21): type("E", (), {"edge_id": "f4", "edge_type": "profiler_same_stream_fifo", "evidence_tier": "observed_structural"})(),
        }
        steps, err = walk_fifo_path(17, 19, fifo_fwd, edge_by_pair)
        self.assertEqual(err, "ok")
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[-1]["dst_node"], "task:19")
        self.assertNotIn("task:709", [s["dst_node"] for s in steps])

    def test_reverse_witness_minimal_chain(self):
        from wait_dag_schema import CausalEdge, DagNode

        record_nid = "event_record:1:1:1:1:1"
        wait_nid = "event_wait:1:144"
        nodes = {
            "task:17": DagNode("task:17", "profiler_task", "17", {}),
            "task:18": DagNode("task:18", "profiler_task", "18", {}),
            "task:19": DagNode("task:19", "profiler_task", "19", {}),
            "task:20": DagNode("task:20", "profiler_task", "20", {}),
            "task:21": DagNode("task:21", "profiler_task", "21", {}),
            record_nid: DagNode(
                record_nid,
                "event_record_generation",
                "rk",
                {"task_rowid": 19, "task_projection_status": "projected"},
            ),
            wait_nid: DagNode(
                wait_nid,
                "event_wait",
                "wk",
                {"task_rowid": 20, "task_projection_status": "projected", "call_sequence": 144},
            ),
            "comm:hcom_allReduce__612_0_1": DagNode(
                "comm:hcom_allReduce__612_0_1", "communication_op", "c", {}
            ),
        }
        causal_edges = [
            CausalEdge(
                edge_id="f1",
                src="task:17",
                dst="task:18",
                edge_type="profiler_same_stream_fifo",
                evidence_tier="observed_structural",
                identity_source="profiler_same_stream_fifo",
            ),
            CausalEdge(
                edge_id="f2",
                src="task:18",
                dst="task:19",
                edge_type="profiler_same_stream_fifo",
                evidence_tier="observed_structural",
                identity_source="profiler_same_stream_fifo",
            ),
            CausalEdge(
                edge_id="f3",
                src="task:19",
                dst="task:709",
                edge_type="profiler_same_stream_fifo",
                evidence_tier="observed_structural",
                identity_source="profiler_same_stream_fifo",
            ),
            CausalEdge(
                edge_id="f4",
                src="task:20",
                dst="task:21",
                edge_type="profiler_same_stream_fifo",
                evidence_tier="observed_structural",
                identity_source="profiler_same_stream_fifo",
            ),
            CausalEdge(
                edge_id="eg1",
                src=record_nid,
                dst=wait_nid,
                edge_type="event_generation",
                evidence_tier="proven_event_generation",
                identity_source="event_generation",
                semantic_class="cross_stream_event_wait",
            ),
        ]
        event_to_task = {record_nid: "task:19", wait_nid: "task:20"}
        task_to_event = {"task:19": record_nid, "task:20": wait_nid}
        summary = {
            "kernel_predecessor_rowid": 17,
            "record_task_rowid": 19,
            "wait_task_rowid": 20,
            "comm_successor_rowid": 21,
            "comm_op_name": "hcom_allReduce__612_0_1",
        }
        steps, complete, blocker = build_reverse_wait_witness(
            144,
            summary,
            wait_nid,
            record_nid,
            nodes,
            causal_edges,
            event_to_task,
            task_to_event,
        )
        self.assertTrue(complete, blocker)
        self.assertLessEqual(len(steps), 8)
        summary_nodes = [steps[0]["src_node"]] + [s["dst_node"] for s in steps]
        self.assertIn("task:17", summary_nodes)
        self.assertIn("task:19", summary_nodes)
        self.assertIn(wait_nid, summary_nodes)
        self.assertIn("task:20", summary_nodes)
        self.assertIn("task:21", summary_nodes)
        self.assertNotIn("task:709", summary_nodes)


if __name__ == "__main__":
    unittest.main()
