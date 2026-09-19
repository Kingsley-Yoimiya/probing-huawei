#!/usr/bin/env python3
"""Unit tests for D51 V5 wait-gap Q1/Q2/Q3 classification (stdlib unittest)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wait_gap_analyze import a6_rejection_reason, classify_case  # noqa: E402


class TestClassifyCase(unittest.TestCase):
    def test_q1_no_preload(self):
        c, p = classify_case(
            preload_ok=False, cann_unique=False, task_rows_total=0, event_wait_rows=0, a6_rejection=""
        )
        self.assertEqual(c, "Q1")
        self.assertTrue(p["q1"])

    def test_q2_zero_tasks(self):
        c, p = classify_case(
            preload_ok=True, cann_unique=True, task_rows_total=0, event_wait_rows=0, a6_rejection=""
        )
        self.assertEqual(c, "Q2")
        self.assertTrue(p["q2"])

    def test_q3_non_event_wait_tasks(self):
        c, p = classify_case(
            preload_ok=True, cann_unique=True, task_rows_total=2, event_wait_rows=0, a6_rejection="type_rejected"
        )
        self.assertEqual(c, "Q3")
        self.assertTrue(p["q3"])

    def test_q3_stream_rejected(self):
        c, p = classify_case(
            preload_ok=True, cann_unique=True, task_rows_total=1, event_wait_rows=1, a6_rejection="stream_rejected"
        )
        self.assertEqual(c, "Q3")
        self.assertTrue(p["q3"])

    def test_q3_cardinality(self):
        c, p = classify_case(
            preload_ok=True,
            cann_unique=True,
            task_rows_total=2,
            event_wait_rows=2,
            a6_rejection="event_wait_cardinality_rejected",
        )
        self.assertEqual(c, "Q3")
        self.assertTrue(p["q3"])

    def test_unresolved_a2(self):
        c, _ = classify_case(
            preload_ok=True, cann_unique=False, task_rows_total=0, event_wait_rows=0, a6_rejection=""
        )
        self.assertEqual(c, "UNRESOLVED_A2")

    def test_mixed_cases(self):
        c1, _ = classify_case(preload_ok=True, cann_unique=True, task_rows_total=0, event_wait_rows=0, a6_rejection="")
        c2, _ = classify_case(
            preload_ok=True, cann_unique=True, task_rows_total=1, event_wait_rows=0, a6_rejection="type_rejected"
        )
        self.assertEqual(c1, "Q2")
        self.assertEqual(c2, "Q3")
        self.assertNotEqual(c1, c2)


class TestA6Rejection(unittest.TestCase):
    def test_type_rejected(self):
        self.assertEqual(
            a6_rejection_reason(
                bound_waits=1,
                event_wait_rows=0,
                event_wait_on_compute=0,
                compute_streams={2},
                wait_stream_ids=[],
            ),
            "type_rejected",
        )

    def test_stream_rejected(self):
        self.assertEqual(
            a6_rejection_reason(
                bound_waits=1,
                event_wait_rows=1,
                event_wait_on_compute=0,
                compute_streams={2},
                wait_stream_ids=[99],
            ),
            "stream_rejected",
        )


if __name__ == "__main__":
    unittest.main()
