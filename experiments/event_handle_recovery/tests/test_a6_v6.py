#!/usr/bin/env python3
"""Unit tests for D51 V6 A6 predicate (stdlib unittest)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from a6_predicate_v6 import (  # noqa: E402
    FAIL_A2_NOT_UNIQUE,
    FAIL_CARDINALITY,
    FAIL_REUSED,
    FAIL_SAME_STREAM,
    WAIT_IDENTITY_SOURCE,
    a2_unique_wait,
    a4_bound_waits,
    apply_task_diagnostic,
    check_global_wait_reuse,
    drop_same_stream_waits,
    evaluate_a6_per_comm,
)
from analyze_event_pairs import RecordKey, TraceRecord  # noqa: E402


def _wait(seq: int, stream: int, pid: int = 1, tid: int = 2) -> TraceRecord:
    return TraceRecord(
        op=5,
        call_sequence=seq,
        slot_sequence=seq,
        pid=pid,
        tid=tid,
        rank=0,
        enter_realtime_ns=1000 + seq,
        exit_realtime_ns=1001 + seq,
        enter_monotonic_ns=0,
        exit_monotonic_ns=0,
        raw_event=0xABC,
        raw_stream=stream,
        acl_ret=0,
        committed=1,
        source=0,
        resolver_path=0,
        nested_under_acl=0,
        parent_acl_call_sequence=0,
        flags=0,
    )


def _record(seq: int, stream: int) -> TraceRecord:
    r = _wait(seq, stream)
    return TraceRecord(
        op=4,
        call_sequence=seq,
        slot_sequence=seq,
        pid=r.pid,
        tid=r.tid,
        rank=0,
        enter_realtime_ns=r.enter_realtime_ns,
        exit_realtime_ns=r.exit_realtime_ns,
        enter_monotonic_ns=0,
        exit_monotonic_ns=0,
        raw_event=0xABC,
        raw_stream=stream,
        acl_ret=0,
        committed=1,
        source=0,
        resolver_path=0,
        nested_under_acl=0,
        parent_acl_call_sequence=0,
        flags=0,
    )


def _fixture_gen(rk: RecordKey, wait_seqs: list[int]) -> dict:
    return {
        "record_keys": {10: rk},
        "wait_bindings": {s: rk for s in wait_seqs},
    }


class TestStreamFilter(unittest.TestCase):
    def test_same_stream_excluded(self):
        cross, same = drop_same_stream_waits([(1, _wait(1, 100)), (2, _wait(2, 200))], 100)
        self.assertEqual(len(same), 1)
        self.assertEqual(same[0][0], 1)
        self.assertEqual(len(cross), 1)
        self.assertEqual(cross[0][0], 2)


class TestA6Positive(unittest.TestCase):
    def test_cross_stream_a2_unique_passes_without_task(self):
        rk = RecordKey(1, 0xABC, 1, 0, 1)
        rec = _record(10, 100)
        wait = _wait(20, 200)
        gen = _fixture_gen(rk, [20])
        cann_by_ord = {
            (1, 2, "aclrtStreamWaitEvent", 0): {
                "name": "aclrtStreamWaitEvent",
                "connection_id": 5559,
                "rowid": 99,
                "start_ns": 1000,
            }
        }
        chain = {
            "preload_record_call_sequence": 10,
            "record_stream": 100,
            "_preload_record": rec,
        }
        cr = evaluate_a6_per_comm(
            comm_op_name="hcom_allReduce__612_4_1",
            comm_connection_id=1,
            chain=chain,
            gen_info=gen,
            rank0_records=[rec, wait],
            cann_by_ordinal=cann_by_ord,
            active_start=0,
            active_end=99999,
        )
        self.assertTrue(cr.a6_pass)
        self.assertEqual(cr.preload_wait_call_sequence, 20)
        self.assertEqual(cr.wait_identity_source, WAIT_IDENTITY_SOURCE)
        self.assertIsNone(cr.event_wait_task_rowid)


class TestTaskIndependence(unittest.TestCase):
    def test_task_rows_do_not_change_a6(self):
        rk = RecordKey(1, 0xABC, 1, 0, 1)
        rec = _record(10, 100)
        wait = _wait(20, 200)
        gen = _fixture_gen(rk, [20])
        cann_by_ord = {
            (1, 2, "aclrtStreamWaitEvent", 0): {
                "name": "aclrtStreamWaitEvent",
                "connection_id": 5559,
                "rowid": 99,
            }
        }
        chain = {"preload_record_call_sequence": 10, "record_stream": 100, "_preload_record": rec}
        base = evaluate_a6_per_comm(
            comm_op_name="c1",
            comm_connection_id=1,
            chain=chain,
            gen_info=gen,
            rank0_records=[rec, wait],
            cann_by_ordinal=cann_by_ord,
            active_start=0,
            active_end=99999,
        )
        for label, tasks in [
            ("empty", {}),
            ("foreign", {5559: [{"rowid": 1, "task_type_name": "KERNEL_AICORE", "connectionId": 5559}]},
            ),
            ("event_wait", {5559: [{"rowid": 7, "task_type_name": "EVENT_WAIT", "connectionId": 5559}]},
            ),
        ]:
            cr = evaluate_a6_per_comm(
                comm_op_name="c1",
                comm_connection_id=1,
                chain=chain,
                gen_info=gen,
                rank0_records=[rec, wait],
                cann_by_ordinal=cann_by_ord,
                active_start=0,
                active_end=99999,
            )
            apply_task_diagnostic([cr], tasks)
            self.assertEqual(cr.a6_pass, base.a6_pass, label)
            self.assertEqual(cr.preload_wait_call_sequence, base.preload_wait_call_sequence, label)


class TestNegativeCases(unittest.TestCase):
    def test_same_stream_only_fails(self):
        rk = RecordKey(1, 0xABC, 1, 0, 1)
        rec = _record(10, 100)
        wait = _wait(20, 100)
        gen = _fixture_gen(rk, [20])
        chain = {"preload_record_call_sequence": 10, "record_stream": 100, "_preload_record": rec}
        cr = evaluate_a6_per_comm(
            comm_op_name="c1",
            comm_connection_id=1,
            chain=chain,
            gen_info=gen,
            rank0_records=[rec, wait],
            cann_by_ordinal={},
            active_start=0,
            active_end=99999,
        )
        self.assertFalse(cr.a6_pass)
        self.assertEqual(cr.n_compute_waits, 0)
        self.assertIn(FAIL_CARDINALITY, cr.failed_predicates)

    def test_a2_zero_candidates_fails(self):
        rk = RecordKey(1, 0xABC, 1, 0, 1)
        rec = _record(10, 100)
        wait = _wait(20, 200)
        gen = _fixture_gen(rk, [20])
        chain = {"preload_record_call_sequence": 10, "record_stream": 100, "_preload_record": rec}
        cr = evaluate_a6_per_comm(
            comm_op_name="c1",
            comm_connection_id=1,
            chain=chain,
            gen_info=gen,
            rank0_records=[rec, wait],
            cann_by_ordinal={},
            active_start=0,
            active_end=99999,
        )
        self.assertFalse(cr.a6_pass)
        self.assertIn(FAIL_A2_NOT_UNIQUE, cr.failed_predicates)

    def test_two_compute_waits_fails(self):
        rk = RecordKey(1, 0xABC, 1, 0, 1)
        rec = _record(10, 100)
        w1 = _wait(20, 200)
        w2 = _wait(21, 201)
        gen = _fixture_gen(rk, [20, 21])
        cann_by_ord = {
            (1, 2, "aclrtStreamWaitEvent", 0): {
                "name": "aclrtStreamWaitEvent",
                "connection_id": 1,
                "rowid": 1,
            },
            (1, 2, "aclrtStreamWaitEvent", 1): {
                "name": "aclrtStreamWaitEvent",
                "connection_id": 2,
                "rowid": 2,
            },
        }
        chain = {"preload_record_call_sequence": 10, "record_stream": 100, "_preload_record": rec}
        cr = evaluate_a6_per_comm(
            comm_op_name="c1",
            comm_connection_id=1,
            chain=chain,
            gen_info=gen,
            rank0_records=[rec, w1, w2],
            cann_by_ordinal=cann_by_ord,
            active_start=0,
            active_end=99999,
        )
        self.assertFalse(cr.a6_pass)
        self.assertEqual(cr.n_compute_waits, 2)
        self.assertIn(FAIL_CARDINALITY, cr.failed_predicates)

    def test_two_cross_stream_one_a2_hit_fails(self):
        """Two cross-stream Waits but only one A2 hit → comm FAIL (not |compute|==1)."""
        rk = RecordKey(1, 0xABC, 1, 0, 1)
        rec = _record(10, 100)
        w1 = _wait(20, 200)
        w2 = _wait(21, 201)
        gen = _fixture_gen(rk, [20, 21])
        cann_by_ord = {
            (1, 2, "aclrtStreamWaitEvent", 0): {
                "name": "aclrtStreamWaitEvent",
                "connection_id": 1,
                "rowid": 1,
            },
        }
        chain = {"preload_record_call_sequence": 10, "record_stream": 100, "_preload_record": rec}
        cr = evaluate_a6_per_comm(
            comm_op_name="c1",
            comm_connection_id=1,
            chain=chain,
            gen_info=gen,
            rank0_records=[rec, w1, w2],
            cann_by_ordinal=cann_by_ord,
            active_start=0,
            active_end=99999,
        )
        self.assertFalse(cr.a6_pass)
        self.assertEqual(cr.n_compute_waits, 1)
        self.assertIn(FAIL_A2_NOT_UNIQUE, cr.failed_predicates)

    def test_a2_multiple_candidates_fails(self):
        from unittest.mock import patch

        rk = RecordKey(1, 0xABC, 1, 0, 1)
        rec = _record(10, 100)
        wait = _wait(20, 200)
        gen = _fixture_gen(rk, [20])
        chain = {"preload_record_call_sequence": 10, "record_stream": 100, "_preload_record": rec}

        def _multi_align(pre_wait, rank0_records, cann_by_ordinal, active_start, active_end):
            return 0, {"name": "aclrtStreamWaitEvent", "connection_id": 1, "rowid": 1}, 2

        with patch("a6_predicate_v6.align_wait_by_a2_ordinal", side_effect=_multi_align):
            cr = evaluate_a6_per_comm(
                comm_op_name="c1",
                comm_connection_id=1,
                chain=chain,
                gen_info=gen,
                rank0_records=[rec, wait],
                cann_by_ordinal={},
                active_start=0,
                active_end=99999,
            )
        self.assertFalse(cr.a6_pass)
        self.assertIn(FAIL_A2_NOT_UNIQUE, cr.failed_predicates)
        self.assertEqual(cr.n_compute_waits, 0)

    def test_foreign_api_closer_timestamp_does_not_save(self):
        """A nearer-timestamp CANN row at wrong ordinal must not rescue the Wait."""
        rk = RecordKey(1, 0xABC, 1, 0, 1)
        rec = _record(10, 100)
        wait = _wait(20, 200)
        wait = TraceRecord(
            **{**wait.__dict__, "enter_realtime_ns": 5000, "exit_realtime_ns": 5001}
        )
        gen = _fixture_gen(rk, [20])
        # Ordinal 0 has no match; ordinal 1 has a CANN wait with timestamp closer to preload.
        cann_by_ord = {
            (1, 2, "aclrtStreamWaitEvent", 1): {
                "name": "aclrtStreamWaitEvent",
                "connection_id": 999,
                "rowid": 88,
                "start_ns": 4999,
            },
        }
        chain = {"preload_record_call_sequence": 10, "record_stream": 100, "_preload_record": rec}
        cr = evaluate_a6_per_comm(
            comm_op_name="c1",
            comm_connection_id=1,
            chain=chain,
            gen_info=gen,
            rank0_records=[rec, wait],
            cann_by_ordinal=cann_by_ord,
            active_start=0,
            active_end=99999,
        )
        self.assertFalse(cr.a6_pass)
        self.assertIn(FAIL_A2_NOT_UNIQUE, cr.failed_predicates)
        self.assertEqual(cr.n_compute_waits, 0)

    def test_wait_reuse_global_fail(self):
        rk = RecordKey(1, 0xABC, 1, 0, 1)
        rec = _record(10, 100)
        wait = _wait(20, 200)
        gen = _fixture_gen(rk, [20])
        cann = {
            (1, 2, "aclrtStreamWaitEvent", 0): {
                "name": "aclrtStreamWaitEvent",
                "connection_id": 9,
                "rowid": 1,
            }
        }
        chain = {"preload_record_call_sequence": 10, "record_stream": 100, "_preload_record": rec}
        c1 = evaluate_a6_per_comm(
            comm_op_name="c1",
            comm_connection_id=1,
            chain=chain,
            gen_info=gen,
            rank0_records=[rec, wait],
            cann_by_ordinal=cann,
            active_start=0,
            active_end=99999,
        )
        c2 = evaluate_a6_per_comm(
            comm_op_name="c2",
            comm_connection_id=2,
            chain=chain,
            gen_info=gen,
            rank0_records=[rec, wait],
            cann_by_ordinal=cann,
            active_start=0,
            active_end=99999,
        )
        _, ok = check_global_wait_reuse([c1, c2])
        self.assertFalse(ok)
        self.assertTrue(c1.wait_reused)
        self.assertIn(FAIL_REUSED, c1.failed_predicates)

    def test_denominator_11_pass_1_fail(self):
        from a6_predicate_v6 import CommA6Result

        results = []
        for i, passed in enumerate([True] * 11 + [False]):
            results.append(
                CommA6Result(
                    comm_op_name=f"c{i}",
                    comm_connection_id=i,
                    record_key=f"(1,1,1,0,{i})",
                    preload_record_call_sequence=10,
                    record_raw_stream=100,
                    a6_pass=passed,
                    n_compute_waits=1 if passed else 0,
                    preload_wait_call_sequence=20 + i if passed else None,
                )
            )
        passed_n = sum(1 for r in results if r.a6_pass)
        self.assertEqual(passed_n, 11)
        self.assertEqual(len(results), 12)
        self.assertFalse(all(r.a6_pass for r in results))


if __name__ == "__main__":
    unittest.main()
