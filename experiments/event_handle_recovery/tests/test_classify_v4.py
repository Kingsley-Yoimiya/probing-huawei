#!/usr/bin/env python3
"""V4 classification fixtures: P route positive, foreign/null/ambiguous negatives."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from classify_intervening_tasks import analyze  # noqa: E402


def _mk_comm_db(
    path: Path,
    *,
    n_comm: int = 1,
    n_same_comm_inter: int = 0,
    foreign: str | None = None,
    null_cid: bool = False,
    two_records: bool = False,
) -> tuple[int, int]:
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.executescript(
        """
        CREATE TABLE STRING_IDS (id INTEGER PRIMARY KEY, value TEXT);
        CREATE TABLE TASK (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            connectionId INTEGER, streamId INTEGER, taskType INTEGER,
            startNs INTEGER, endNs INTEGER, globalTaskId INTEGER, taskId INTEGER
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
    names = {
        "EVENT_RECORD": 18,
        "AI_CORE": 4,
        "SDMA": 63,
        "hcom_allReduce__fx_0_1": 1,
        "hcom_allReduce__fx_1_1": 2,
        "aclrtRecordEvent": 100,
    }
    for k, v in names.items():
        cur.execute("INSERT INTO STRING_IDS (id, value) VALUES (?, ?)", (v, k))
    active_start, active_end = 1000, 5000
    rowid = 0
    gtid = 0
    for i in range(n_comm):
        cid = 1000 + i * 100
        op_name_id = 1 if i == 0 else 2
        cur.execute(
            "INSERT INTO COMMUNICATION_OP VALUES (?, ?, ?, ?)",
            (op_name_id, active_start + i * 100, active_end - 100, cid),
        )
        # terminal AI_CORE (max endNs)
        rowid += 1
        gtid += 1
        cur.execute(
            "INSERT INTO TASK VALUES (NULL, ?, 4, 4, ?, ?, ?, ?)",
            (cid, 1100 + i * 100, 4000, gtid, 1),
        )
        term_row = rowid
        for j in range(n_same_comm_inter):
            rowid += 1
            gtid += 1
            cur.execute(
                "INSERT INTO TASK VALUES (NULL, ?, 4, 63, ?, ?, ?, ?)",
                (cid, 2000 + j, 2100 + j, gtid, 2),
            )
        if foreign == "other_target_comm" and n_comm > 1:
            rowid += 1
            gtid += 1
            other_cid = 1000 + ((i + 1) % n_comm) * 100
            cur.execute(
                "INSERT INTO TASK VALUES (NULL, ?, 4, 63, ?, ?, ?, ?)",
                (other_cid, 3000, 3100, gtid, 3),
            )
        elif foreign == "other_cid":
            rowid += 1
            gtid += 1
            cur.execute(
                "INSERT INTO TASK VALUES (NULL, ?, 4, 63, ?, ?, ?, ?)",
                (99999, 3500, 3600, gtid, 3),
            )
        if null_cid:
            rowid += 1
            gtid += 1
            cur.execute(
                "INSERT INTO TASK VALUES (NULL, NULL, 4, 63, ?, ?, ?, ?)",
                (3200, 3300, gtid, 3),
            )
        if two_records:
            rowid += 1
            gtid += 1
            cur.execute(
                "INSERT INTO TASK VALUES (NULL, ?, 4, 18, ?, ?, ?, ?)",
                (5000 + i, 4200, 4210, gtid, 4),
            )
        rowid += 1
        gtid += 1
        cur.execute(
            "INSERT INTO TASK VALUES (NULL, ?, 4, 18, ?, ?, ?, ?)",
            (5000 + i, 4300, 4310, gtid, 5),
        )
        cur.execute(
            "INSERT INTO CANN_API VALUES (?, ?, 0, 0, ?, ?)",
            (4300, 4310, 5000 + i, 100),
        )
    con.commit()
    con.close()
    return active_start, active_end


def test_p_route_same_comm_boundary(tmp_path: Path):
    db = tmp_path / "p.db"
    a0, a1 = _mk_comm_db(db, n_comm=1, n_same_comm_inter=160)
    out = tmp_path / "analysis"
    d = analyze(db, out, a0, a1, "test")
    assert d["route"] == "P"
    assert d["all_intervening_same_comm"] is True
    assert d["strict_fifo_adjacent_at_comm_set_boundary"] is True
    rows = (out / "intervening_task_taxonomy.csv").read_text()
    assert rows.count("same_comm") == 160


@pytest.mark.parametrize("n_inter", [0, 1, 159, 160, 161])
def test_p_route_independent_of_inter_count(tmp_path: Path, n_inter: int):
    db = tmp_path / f"p_{n_inter}.db"
    a0, a1 = _mk_comm_db(db, n_comm=1, n_same_comm_inter=n_inter)
    d = analyze(db, tmp_path / f"out_{n_inter}", a0, a1, "test")
    assert d["route"] == "P"
    assert d["all_intervening_same_comm"] is True


@pytest.mark.parametrize(
    "foreign,expect_route",
    [
        ("other_target_comm", "L"),
        ("other_cid", "L"),
    ],
)
def test_l_route_foreign(tmp_path: Path, foreign: str, expect_route: str):
    db = tmp_path / f"{foreign}.db"
    a0, a1 = _mk_comm_db(db, n_comm=2, n_same_comm_inter=1, foreign=foreign)
    d = analyze(db, tmp_path / "out", a0, a1, "test")
    assert d["route"] == expect_route


def test_l_route_null_cid(tmp_path: Path):
    db = tmp_path / "null.db"
    a0, a1 = _mk_comm_db(db, n_comm=1, n_same_comm_inter=0, null_cid=True)
    d = analyze(db, tmp_path / "out", a0, a1, "test")
    assert d["route"] == "L"
    assert d["all_intervening_same_comm"] is False
