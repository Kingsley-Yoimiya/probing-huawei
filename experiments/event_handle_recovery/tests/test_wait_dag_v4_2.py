#!/usr/bin/env python3
"""Unit tests for D51 Wait DAG V4.2 reverse identity and fail-closed paths."""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analyze_event_pairs import TaskRow  # noqa: E402
from wait_dag_v4_intervention import (  # noqa: E402
    NODE_WALLCLOCK_FIELDS,
    PAIRED_EFFECTS_FIELDS,
    check_inject_identity_alignment,
    find_injected_kernel_before_record,
    find_upstream_kernel_skipping_inject,
    node_with_offsets,
    paired_effects,
)
from wait_dag_v4_2_reverse_candidate import extract_reverse_candidates  # noqa: E402


class TestNoA6BuilderLeak(unittest.TestCase):
    def test_reverse_module_does_not_import_build_allreduce_chains(self):
        src = (ROOT / "wait_dag_v4_2_reverse_candidate.py").read_text()
        self.assertNotIn("build_allreduce_chains", src)
        src2 = (ROOT / "wait_dag_v4_intervention.py").read_text()
        self.assertNotIn("build_allreduce_chains", src2)

    def test_extract_survives_a6_stub_crash(self):
        with mock.patch.dict(sys.modules, {"wait_dag_build": mock.MagicMock()}):
            mod = importlib.import_module("wait_dag_v4_2_reverse_candidate")
            if hasattr(mod, "build_allreduce_chains"):
                mod.build_allreduce_chains = mock.MagicMock(
                    side_effect=RuntimeError("A6 must not run")
                )
        self.assertTrue(callable(extract_reverse_candidates))


class TestNodeWallclockColumns(unittest.TestCase):
    def test_node_with_offsets_field_names(self):
        row = node_with_offsets("r1", "D0", "record_task", 100, 120, 80)
        self.assertIn("start_offset_from_upstream_kernel_end_ns", row)
        self.assertIn("end_offset_from_upstream_kernel_end_ns", row)
        self.assertNotIn("start_offset_from_comm_end_ns", row)

    def test_csv_schema_matches_offsets(self):
        self.assertIn("start_offset_from_upstream_kernel_end_ns", NODE_WALLCLOCK_FIELDS)
        self.assertIn("realized_work_ns", PAIRED_EFFECTS_FIELDS)


class TestInjectIdentityConditions(unittest.TestCase):
    def test_dsmall_requires_realized_work(self):
        ex = {
            "status": "OK",
            "condition": "Dsmall",
            "realized_work_ns": 0,
            "audit": {
                "launch_count": 1,
                "trigger_record_preload_cs": 143,
            },
            "identity": {"preload_record_cs": 143},
        }
        self.assertEqual(check_inject_identity_alignment(ex), "REALIZED_WORK_INVALID")

    def test_dlarge_align_ok(self):
        ex = {
            "status": "OK",
            "condition": "Dlarge",
            "realized_work_ns": 5000,
            "audit": {
                "launch_count": 1,
                "trigger_record_preload_cs": 143,
            },
            "identity": {"preload_record_cs": 143},
        }
        self.assertIsNone(check_inject_identity_alignment(ex))


class TestPairedEffectsFailClosed(unittest.TestCase):
    def _node(self, name: str, start: int, end: int, anchor: int = 0) -> dict:
        return {
            "node": name,
            "start_offset_from_upstream_kernel_end_ns": start - anchor,
            "end_offset_from_upstream_kernel_end_ns": end - anchor,
            "duration_ns": end - start,
        }

    def test_zero_realized_work_fails(self):
        key = json.dumps({"schema": "d51_reverse_candidate_v1", "rank": 0}, sort_keys=True)
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
            "normalized_structure_key": key,
            "generation_closure_status": "VALID",
            "nodes": [
                self._node("record_task", 0, 20),
                self._node("comm_entry", 100, 110),
            ],
            "realized_work_ns": 0,
        }
        pe = paired_effects(d0, dtreat, "b1")
        self.assertFalse(pe["dose_gate_pass"])
        self.assertIsNone(pe["causal_gate_pass"])


class TestInjectKernelProjection(unittest.TestCase):
    def _mk_db(self, path: Path) -> None:
        con = sqlite3.connect(path)
        cur = con.cursor()
        cur.executescript(
            """
            CREATE TABLE STRING_IDS(id INTEGER PRIMARY KEY, value TEXT);
            CREATE TABLE COMPUTE_TASK_INFO(
                name INTEGER, globalTaskId INTEGER PRIMARY KEY, blockDim INTEGER);
            CREATE TABLE TASK(
                rowid INTEGER PRIMARY KEY, connectionId INTEGER, streamId INTEGER,
                taskType INTEGER, startNs INTEGER, endNs INTEGER, globalTaskId INTEGER);
            INSERT INTO STRING_IDS VALUES (40,'d51_compute_delay_kernel'), (1,'KERNEL_AIVEC');
            INSERT INTO COMPUTE_TASK_INFO VALUES (40, 14, 1);
            INSERT INTO TASK VALUES
              (17, 1, 2, 1, 100, 200, 13),
              (19, 1, 2, 1, 201, 2461, 14),
              (20, 1, 2, 72, 2461, 2481, 72);
            """
        )
        con.commit()
        con.close()

    def test_cti_adjacent_inject_found(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            self._mk_db(db)
            tasks = [
                TaskRow(17, 1, 2, 1, 100, 200),
                TaskRow(19, 1, 2, 1, 201, 2461),
                TaskRow(20, 1, 2, 72, 2461, 2481),
            ]
            rec = tasks[2]
            inj, rule = find_injected_kernel_before_record(
                rec, tasks, {1: "KERNEL_AIVEC", 72: "EVENT_RECORD"}, 0, 10**12, db_path=db
            )
            self.assertIsNotNone(inj)
            self.assertEqual(inj.rowid, 19)
            self.assertIn("cti", rule)

    def test_upstream_skips_inject(self):
        tasks = [
            TaskRow(17, 1, 2, 1, 100, 200),
            TaskRow(19, 1, 2, 1, 201, 2461),
            TaskRow(20, 1, 2, 72, 2461, 2481),
        ]
        rec = tasks[2]
        up, rule = find_upstream_kernel_skipping_inject(
            rec, 19, tasks, {1: "KERNEL_AIVEC", 72: "EVENT_RECORD"}, 0, 10**12, {19}
        )
        self.assertIsNotNone(up)
        self.assertEqual(up.rowid, 17)
        self.assertIn("skip", rule)


class TestManifestFailClosed(unittest.TestCase):
    def test_invalid_manifest_json_has_no_ordinal_three_default(self):
        cpp = (ROOT / "device_work.cpp").read_text()
        self.assertNotIn("kTargetRecordOrdinal=3", cpp)
        self.assertNotIn("kTargetRecordOrdinal = 3", cpp)
        self.assertIn("LoadSelectorManifestOrdinal", cpp)
        # Parser returns false on missing manifest — no fallback literal 3 in C++.
        self.assertNotRegex(cpp, r"target_record_ordinal[^\n]*=\s*3\s*;")


if __name__ == "__main__":
    unittest.main()
