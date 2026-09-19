#!/usr/bin/env python3
"""Unit tests for D51 V3 analyzer: FIFO A5, nested Create A3."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analyze_event_pairs import (  # noqa: E402
    CREATE_OPS,
    RECORD_OP,
    RT_CREATE_OPS,
    WAIT_OP,
    TaskRow,
    TraceRecord,
    build_allreduce_chains,
    fifo_event_record_after_terminal,
    identify_nested_create_skips,
    rebuild_generations,
    terminal_comm_task,
)


def _rec(
    op: int,
    seq: int,
    *,
    pid: int = 1,
    tid: int = 2,
    event: int = 0x1000,
    nest: int = 0,
    parent: int = 0,
    stream: int = 0,
    enter: int = 1000,
    exit_ns: int = 2000,
) -> TraceRecord:
    return TraceRecord(
        op=op,
        call_sequence=seq,
        slot_sequence=seq,
        pid=pid,
        tid=tid,
        rank=0,
        enter_realtime_ns=enter,
        exit_realtime_ns=exit_ns,
        enter_monotonic_ns=enter,
        exit_monotonic_ns=exit_ns,
        raw_event=event,
        raw_stream=stream,
        acl_ret=0,
        committed=1,
        source=1,
        resolver_path=1,
        nested_under_acl=nest,
        parent_acl_call_sequence=parent,
        flags=0,
    )


def _mk_db(path: Path, n_comm: int = 12, break_fifo: str | None = None) -> tuple[int, int, int]:
    """Build minimal SQLite with n_comm AllReduce chains."""
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.executescript(
        """
        CREATE TABLE STRING_IDS (id INTEGER PRIMARY KEY, value TEXT);
        CREATE TABLE TASK (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            connectionId INTEGER, streamId INTEGER, taskType INTEGER,
            startNs INTEGER, endNs INTEGER
        );
        CREATE TABLE COMMUNICATION_OP (
            opName INTEGER, startNs INTEGER, endNs INTEGER, connectionId INTEGER
        );
        CREATE TABLE CANN_API (
            startNs INTEGER, endNs INTEGER, type INTEGER,
            globalTid INTEGER, connectionId INTEGER, name INTEGER
        );
        """
    )
    sid = {
        "EVENT_RECORD": 18,
        "EVENT_WAIT": 62,
        "AI_CORE": 4,
        "KERNEL_AIVEC": 20,
        "aclrtRecordEvent": 100,
        "aclrtStreamWaitEvent": 101,
    }
    for k, v in [
        ("EVENT_RECORD", 18),
        ("EVENT_WAIT", 62),
        ("AI_CORE", 4),
        ("KERNEL_AIVEC", 20),
        ("aclrtRecordEvent", 100),
        ("aclrtStreamWaitEvent", 101),
    ]:
        cur.execute("INSERT INTO STRING_IDS VALUES (?,?)", (v, k))
    for i in range(3):
        cur.execute(
            "INSERT INTO TASK VALUES (NULL,?,?,?,?,?)",
            (9000, 2, sid["KERNEL_AIVEC"], 100 + i, 200 + i),
        )

    comm_stream = 4
    compute_stream = 2
    active_start = 1_000_000
    active_end = 9_000_000
    rec_tasks: list[tuple[int, int]] = []

    for i in range(n_comm):
        comm_cid = 1000 + i * 100
        rec_cid = 5000 + i
        op_name = 200 + i
        cur.execute("INSERT INTO STRING_IDS VALUES (?,?)", (op_name, f"hcom_allReduce__{i}"))
        comm_start = active_start + i * 100_000
        comm_end = comm_start + 50_000
        cur.execute(
            "INSERT INTO COMMUNICATION_OP VALUES (?,?,?,?)",
            (op_name, comm_start, comm_end, comm_cid),
        )
        term_row = cur.execute(
            "INSERT INTO TASK VALUES (NULL,?,?,?,?,?)",
            (comm_cid, comm_stream, sid["AI_CORE"], comm_start + 1000, comm_end),
        ).lastrowid
        if break_fifo == "missing_record" and i == 0:
            pass
        elif break_fifo == "insert_between" and i == 0:
            cur.execute(
                "INSERT INTO TASK VALUES (NULL,?,?,?,?,?)",
                (comm_cid, comm_stream, sid["AI_CORE"], comm_end + 10, comm_end + 20),
            )
            cur.execute(
                "INSERT INTO TASK VALUES (NULL,?,?,?,?,?)",
                (rec_cid, comm_stream, sid["EVENT_RECORD"], comm_end + 30, comm_end + 40),
            )
        elif break_fifo == "two_records" and i == 0:
            cur.execute(
                "INSERT INTO TASK VALUES (NULL,?,?,?,?,?)",
                (rec_cid, comm_stream, sid["EVENT_RECORD"], comm_end + 10, comm_end + 20),
            )
            cur.execute(
                "INSERT INTO TASK VALUES (NULL,?,?,?,?,?)",
                (rec_cid + 1, comm_stream, sid["EVENT_RECORD"], comm_end + 30, comm_end + 40),
            )
        else:
            rec_row = cur.execute(
                "INSERT INTO TASK VALUES (NULL,?,?,?,?,?)",
                (rec_cid, comm_stream, sid["EVENT_RECORD"], comm_end + 10, comm_end + 20),
            ).lastrowid
            rec_tasks.append((rec_row, rec_cid))

        if break_fifo != "missing_record" or i > 0:
            rc = rec_cid if break_fifo not in ("missing_record",) or i > 0 else -1
            if rc >= 0:
                cur.execute(
                    "INSERT INTO CANN_API VALUES (?,?,?,?,?,?)",
                    (
                        comm_end + 15,
                        comm_end + 25,
                        0,
                        (1 << 32) | 2,
                        rc,
                        sid["aclrtRecordEvent"],
                    ),
                )
                wait_cid = 8000 + i
                cur.execute(
                    "INSERT INTO TASK VALUES (NULL,?,?,?,?,?)",
                    (wait_cid, compute_stream, sid["EVENT_WAIT"], comm_end + 100, comm_end + 110),
                )
                cur.execute(
                    "INSERT INTO CANN_API VALUES (?,?,?,?,?,?)",
                    (
                        comm_end + 105,
                        comm_end + 115,
                        0,
                        (1 << 32) | 2,
                        wait_cid,
                        sid["aclrtStreamWaitEvent"],
                    ),
                )

    con.commit()
    con.close()
    return active_start, active_end, sid["EVENT_RECORD"]


def test_nested_ex_withflag_rt_single_lifetime():
    records = [
        _rec(3, 1, event=0xABC, enter=100, exit_ns=500),
        _rec(2, 2, event=0xABC, enter=150, exit_ns=450),
        _rec(10, 3, event=0xABC, nest=1, parent=2, enter=200, exit_ns=400),
        _rec(RECORD_OP, 4, event=0xABC, stream=0x2000, enter=600, exit_ns=700),
        _rec(WAIT_OP, 5, event=0xABC, stream=0x3000, enter=800, exit_ns=900),
    ]
    skip, _, _ = identify_nested_create_skips(records)
    assert 2 in skip and 3 in skip and 1 not in skip
    rows, errors, info = rebuild_generations(records)
    assert info["lifecycle_incomplete"] == 0
    assert info["nested_create_skipped"] == 2


def test_independent_double_create_errors():
    records = [
        _rec(3, 1, event=0xABC, enter=100, exit_ns=200),
        _rec(3, 2, event=0xABC, enter=300, exit_ns=400),
    ]
    _, errors, info = rebuild_generations(records)
    assert info["lifecycle_incomplete"] >= 1
    assert any("create_without_destroy" in e for e in errors)


def test_fifo_positive_12(tmp_path: Path):
    db = tmp_path / "fx.db"
    a0, a1, rec_type = _mk_db(db, 12)
    rank0_records = [_rec(3, 0, event=0x1000, enter=1, exit_ns=2)]
    for i in range(12):
        rank0_records.append(
            _rec(
                RECORD_OP,
                i + 1,
                event=0x1000,
                stream=0x2000,
                enter=a0 + i * 100_000 + 50_015,
                exit_ns=a0 + i * 100_000 + 50_025,
            )
        )
        rank0_records.append(
            _rec(
                WAIT_OP,
                100 + i,
                event=0x1000,
                stream=0x3000,
                enter=a0 + i * 100_000 + 50_105,
                exit_ns=a0 + i * 100_000 + 50_115,
            )
        )
    _, _, gen_info = rebuild_generations(rank0_records)
    assert gen_info["lifecycle_incomplete"] == 0
    con = sqlite3.connect(db)
    string_ids = {r[0]: r[1] for r in con.execute("SELECT id, value FROM STRING_IDS")}
    con.close()
    chains, unmatched, edges = build_allreduce_chains(
        db,
        gen_info,
        a0,
        a1,
        rank0_pid=1,
        rank0_records=rank0_records,
        aligned_api=[],
        event_record_type=rec_type,
        event_wait_type=62,
        string_ids=string_ids,
    )
    assert len(chains) == 12
    assert all(c.get("event_record_connection_id") for c in chains)
    assert not any(u.get("reason") == "cann_record_not_unique" for u in unmatched)


def test_fifo_missing_event_record_fails(tmp_path: Path):
    db = tmp_path / "fx.db"
    a0, a1, rec_type = _mk_db(db, 12, break_fifo="missing_record")
    _, unmatched, _ = build_allreduce_chains(
        db,
        {"record_keys": {}, "wait_bindings": {}},
        a0,
        a1,
        rank0_pid=1,
        rank0_records=[],
        aligned_api=[],
        event_record_type=rec_type,
        event_wait_type=62,
        string_ids={18: "EVENT_RECORD", 62: "EVENT_WAIT"},
    )
    assert any(
        u.get("reason") in ("no_event_record_fifo", "no_preload_record", "no_record_key")
        for u in unmatched
    )


def test_terminal_task_ordering():
    tasks = [
        TaskRow(1, 10, 4, 63, 100, 300),
        TaskRow(2, 10, 4, 4, 50, 500),
    ]
    term = terminal_comm_task(tasks)
    assert term.rowid == 2


def test_fifo_skips_intervening_non_record():
    stream = [
        TaskRow(1, 10, 4, 4, 100, 500),
        TaskRow(2, 10, 4, 63, 510, 520),
        TaskRow(3, 11, 4, 18, 530, 540),
    ]
    rec, meta = fifo_event_record_after_terminal(stream, stream[0], 18)
    assert rec is not None
    assert rec.rowid == 3
    assert meta["fifo_candidates"] == 1
