#!/usr/bin/env python3
"""Unit tests for D51 Wait DAG V4.3 gate splitting and legacy dose exclusion."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wait_dag_v4_intervention import (  # noqa: E402
    PAIRED_EFFECTS_FIELDS,
    evaluate_causal_gate,
    evaluate_dose_gate,
    evaluate_structure_gate,
    is_legacy_illegal_dose,
    paired_effects,
)


class TestGateFields(unittest.TestCase):
    def test_paired_fields_have_split_gates(self):
        self.assertIn("structure_gate_pass", PAIRED_EFFECTS_FIELDS)
        self.assertIn("dose_gate_pass", PAIRED_EFFECTS_FIELDS)
        self.assertIn("causal_gate_pass", PAIRED_EFFECTS_FIELDS)
        self.assertNotIn("status", PAIRED_EFFECTS_FIELDS)


class TestLegacyIllegalDose(unittest.TestCase):
    def test_v42_200_iters_flagged(self):
        dtreat = {
            "run_id": "x_small_dsmall",
            "identity": {"requested_iters": 200},
        }
        self.assertTrue(is_legacy_illegal_dose(dtreat, 2000))


class TestPairedGateSemantics(unittest.TestCase):
    def _node(self, name: str, start: int, end: int, anchor: int = 0) -> dict:
        return {
            "node": name,
            "start_offset_from_upstream_kernel_end_ns": start - anchor,
            "end_offset_from_upstream_kernel_end_ns": end - anchor,
            "duration_ns": end - start,
        }

    def test_structure_fail_no_causal_eval(self):
        key = '{"rank":0}'
        d0 = {
            "run_id": "d0",
            "normalized_structure_key": key,
            "generation_closure_status": "VALID",
            "nodes": [self._node("record_task", 0, 10), self._node("comm_entry", 100, 110)],
            "slack_record_to_comm_ns": 90,
            "slack_record_to_wait_ns": 50,
        }
        dtreat = {
            "run_id": "t1",
            "condition": "Dsmall",
            "normalized_structure_key": "other",
            "generation_closure_status": "VALID",
            "nodes": [self._node("record_task", 0, 20), self._node("comm_entry", 100, 110)],
            "realized_work_ns": 50_000,
        }
        pe = paired_effects(d0, dtreat, "b1_dsmall")
        self.assertFalse(pe["structure_gate_pass"])
        self.assertIsNone(pe["causal_gate_pass"])

    def test_illegal_dose_no_causal_pass(self):
        key = '{"rank":0}'
        d0 = {
            "run_id": "d0",
            "normalized_structure_key": key,
            "generation_closure_status": "VALID",
            "nodes": [
                self._node("record_task", 0, 10),
                self._node("wait_task", 20, 30),
                self._node("comm_entry", 100, 110),
            ],
            "slack_record_to_comm_ns": 90_000,
            "slack_record_to_wait_ns": 50,
        }
        dtreat = {
            "run_id": "legacy_small_dsmall",
            "condition": "Dsmall",
            "normalized_structure_key": key,
            "generation_closure_status": "VALID",
            "nodes": [
                self._node("record_task", 0, 20),
                self._node("wait_task", 20, 30),
                self._node("comm_entry", 100, 110),
            ],
            "realized_work_ns": 2000,
            "identity": {"requested_iters": 200},
        }
        pe = paired_effects(d0, dtreat, "b1_dsmall")
        self.assertTrue(pe["legacy_illegal_dose"])
        self.assertFalse(pe["dose_gate_pass"])
        self.assertIsNone(pe["causal_gate_pass"])

    def test_structure_pass_dose_fail_causal_null(self):
        key = '{"rank":0}'
        d0 = {
            "run_id": "d0",
            "normalized_structure_key": key,
            "generation_closure_status": "VALID",
            "nodes": [
                self._node("record_task", 0, 10),
                self._node("wait_task", 20, 30),
                self._node("comm_entry", 100, 110),
            ],
            "slack_record_to_comm_ns": 100_000,
            "slack_record_to_wait_ns": 50,
        }
        dtreat = {
            "run_id": "t1",
            "condition": "Dsmall",
            "normalized_structure_key": key,
            "generation_closure_status": "VALID",
            "nodes": [
                self._node("record_task", 0, 20),
                self._node("wait_task", 20, 30),
                self._node("comm_entry", 100, 110),
            ],
            "realized_work_ns": 5_000,
        }
        pe = paired_effects(d0, dtreat, "b1_dsmall")
        self.assertTrue(pe["structure_gate_pass"])
        self.assertFalse(pe["dose_gate_pass"])
        self.assertIsNone(pe["causal_gate_pass"])

    def test_legal_dose_causal_fail(self):
        key = '{"rank":0}'
        slack = 724_000
        realized = slack + 2_000_000
        d0 = {
            "run_id": "d0",
            "normalized_structure_key": key,
            "generation_closure_status": "VALID",
            "nodes": [
                self._node("record_task", 0, 10),
                self._node("wait_task", 20, 30),
                self._node("comm_entry", slack + 10, slack + 20),
            ],
            "slack_record_to_comm_ns": slack,
            "slack_record_to_wait_ns": 50,
        }
        dtreat = {
            "run_id": "t1",
            "condition": "Dlarge",
            "normalized_structure_key": key,
            "generation_closure_status": "VALID",
            "nodes": [
                self._node("record_task", 0, 10 + 17_000),
                self._node("wait_task", 20, 30),
                self._node("comm_entry", slack + 10, slack + 20),
            ],
            "realized_work_ns": realized,
        }
        pe = paired_effects(d0, dtreat, "b1_dlarge")
        self.assertTrue(pe["structure_gate_pass"])
        self.assertTrue(pe["dose_gate_pass"])
        self.assertFalse(pe["causal_gate_pass"])


class TestGateHelpers(unittest.TestCase):
    def test_structure_mismatch(self):
        ok, reason = evaluate_structure_gate(
            {"normalized_structure_key": "a", "generation_closure_status": "VALID"},
            {"normalized_structure_key": "b", "generation_closure_status": "VALID"},
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "PAIR_STRUCTURE_MISMATCH")

    def test_causal_dsmall_mismatch(self):
        ok, _ = evaluate_causal_gate(
            kind="Dsmall",
            record_shift=100,
            wait_shift=0,
            comm_entry_shift=0,
            realized_work=5_000_000,
            slack_comm=724_000,
            predicted_comm=0,
        )
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
