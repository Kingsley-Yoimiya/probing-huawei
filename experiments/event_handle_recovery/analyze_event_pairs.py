#!/usr/bin/env python3
"""Align preload ACL event traces with rank0 CANN_API and rebuild comm chains (V3)."""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import struct
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ANALYZER_VERSION = 3
ACL_EVENT_TRACE_MAGIC = 0x41435445
ACL_EVENT_TRACE_VERSIONS = {2, 3}
RECORD_FMT = "<IBBHQQIIiiQQQQQQIiiBBBBQ"
HEADER_FMT = "<IIQQQQQiiIIQQQQQQQ"
TRAILER_FMT = "<QQII"
RECORD_SIZE = 112
HEADER_SIZE = struct.calcsize(HEADER_FMT)
TRAILER_SIZE = struct.calcsize(TRAILER_FMT)
assert struct.calcsize(RECORD_FMT) == RECORD_SIZE, "RECORD_FMT must match event_trace_format.h"
TRACE_BIN_RE = re.compile(r"rank_(-?\d+)_pid_(\d+)\.events\.bin$")

CREATE_OPS = {1, 2, 3}
RT_CREATE_OPS = {9, 10, 11}
ALL_CREATE_OPS = CREATE_OPS | RT_CREATE_OPS
RECORD_OP = 4
WAIT_OP = 5
RESET_OP = 6
DESTROY_OP = 7

COMPUTE_TASK_NAMES = ("KERNEL_AIVEC", "KERNEL_AICORE", "KERNEL_MIX_AIV")


@dataclass
class TraceRecord:
    op: int
    call_sequence: int
    slot_sequence: int
    pid: int
    tid: int
    rank: int
    enter_realtime_ns: int
    exit_realtime_ns: int
    enter_monotonic_ns: int
    exit_monotonic_ns: int
    raw_event: int
    raw_stream: int
    acl_ret: int
    committed: int
    source: int
    resolver_path: int
    nested_under_acl: int
    parent_acl_call_sequence: int
    flags: int


@dataclass
class EventState:
    alive: bool = False
    lifetime_epoch: int = 0
    reset_epoch: int = 0
    record_epoch: int = 0
    has_create: bool = False
    create_source: str = ""


@dataclass
class RecordKey:
    pid: int
    raw_event: int
    lifetime_epoch: int
    reset_epoch: int
    record_epoch: int

    def as_tuple(self) -> tuple[int, int, int, int, int]:
        return (self.pid, self.raw_event, self.lifetime_epoch, self.reset_epoch, self.record_epoch)


@dataclass(frozen=True)
class TaskRow:
    rowid: int
    connection_id: int
    stream_id: int
    task_type: int
    start_ns: int
    end_ns: int


def load_trace_bin(path: Path) -> tuple[dict[str, int], list[TraceRecord]]:
    data = path.read_bytes()
    if len(data) < HEADER_SIZE + TRAILER_SIZE:
        raise ValueError(f"trace too small: {path}")
    header = struct.unpack(HEADER_FMT, data[:HEADER_SIZE])
    meta = {
        "magic": header[0],
        "version": header[1],
        "capacity": header[2],
        "committed_count": header[3],
        "dropped": header[4],
        "fatal": header[5],
        "late_calls": header[6],
        "pid": header[7],
        "rank": header[8],
        "resolver_queries": header[11],
        "resolver_wrappers_returned": header[12],
        "resolver_target_conflict": header[13],
        "resolver_audit_overflow": header[14],
        "acl_create_wrapper_calls": header[15],
        "rt_create_wrapper_calls": header[16],
        "logical_create_count": header[17],
        "path": str(path),
    }
    if meta["magic"] != ACL_EVENT_TRACE_MAGIC:
        raise ValueError(f"bad magic in {path}")
    if meta["version"] not in ACL_EVENT_TRACE_VERSIONS:
        raise ValueError(f"refuse mixed format version={meta['version']} in {path}")
    body = data[HEADER_SIZE:-TRAILER_SIZE]
    if len(body) % RECORD_SIZE != 0:
        raise ValueError(
            f"record body size {len(body)} not divisible by RECORD_SIZE={RECORD_SIZE} in {path}"
        )
    records: list[TraceRecord] = []
    n = len(body) // RECORD_SIZE
    for i in range(n):
        r = struct.unpack(RECORD_FMT, body[i * RECORD_SIZE : (i + 1) * RECORD_SIZE])
        records.append(
            TraceRecord(
                op=r[1],
                call_sequence=r[4],
                slot_sequence=r[5],
                pid=r[6],
                tid=r[7],
                rank=r[8],
                enter_realtime_ns=r[10],
                exit_realtime_ns=r[11],
                enter_monotonic_ns=r[12],
                exit_monotonic_ns=r[13],
                raw_event=r[14],
                raw_stream=r[15],
                acl_ret=r[17],
                committed=r[2],
                flags=r[18],
                source=r[19],
                resolver_path=r[20],
                nested_under_acl=r[21],
                parent_acl_call_sequence=r[23],
            )
        )
    trailer = struct.unpack(TRAILER_FMT, data[-TRAILER_SIZE:])
    if trailer[0] != meta["committed_count"]:
        raise ValueError(f"trailer mismatch in {path}")
    return meta, records


def load_string_ids(cur: sqlite3.Cursor) -> dict[int, str]:
    return {int(i): v for i, v in cur.execute("SELECT id, value FROM STRING_IDS")}


def resolve_string(string_ids: dict[int, str], i: Any) -> str | None:
    if i is None:
        return None
    if isinstance(i, str):
        return i
    return string_ids.get(int(i), f"?{i}")


def nested_interval_contains(outer: TraceRecord, inner: TraceRecord) -> bool:
    return (
        outer.enter_realtime_ns <= inner.enter_realtime_ns
        and inner.exit_realtime_ns <= outer.exit_realtime_ns
        and (outer.pid, outer.tid) == (inner.pid, inner.tid)
    )


def identify_nested_create_skips(records: list[TraceRecord]) -> tuple[set[int], list[str], int]:
    """Mark inner Ex/WithFlag/RT creates that belong to one logical Create."""
    skip: set[int] = set()
    errors: list[str] = []
    acl_parent_mismatch = 0
    creates = sorted(
        [r for r in records if r.op in ALL_CREATE_OPS and r.acl_ret == 0],
        key=lambda x: x.call_sequence,
    )
    acl_by_seq = {r.call_sequence: r for r in creates if r.op in CREATE_OPS}

    for rec in creates:
        if rec.op in RT_CREATE_OPS:
            if rec.nested_under_acl and rec.parent_acl_call_sequence:
                skip.add(rec.call_sequence)
                parent = acl_by_seq.get(rec.parent_acl_call_sequence)
                if parent is None:
                    acl_parent_mismatch += 1
                    errors.append(
                        f"acl_create_parent_mismatch rt seq={rec.call_sequence} "
                        f"parent={rec.parent_acl_call_sequence}"
                    )
                elif parent.raw_event != rec.raw_event:
                    acl_parent_mismatch += 1
                    errors.append(
                        f"create_layer_mismatch acl=0x{parent.raw_event:x} rt=0x{rec.raw_event:x}"
                    )
            continue

        if rec.op not in CREATE_OPS:
            continue
        for outer in creates:
            if outer.call_sequence >= rec.call_sequence:
                break
            if outer.op not in CREATE_OPS or outer.call_sequence in skip:
                continue
            if outer.raw_event != rec.raw_event:
                continue
            if nested_interval_contains(outer, rec) and outer.call_sequence != rec.call_sequence:
                skip.add(rec.call_sequence)
                break

    return skip, errors, acl_parent_mismatch


def merge_acl_rt_creates(records: list[TraceRecord], skip: set[int]) -> list[str]:
    errors: list[str] = []
    acl_by_seq = {
        r.call_sequence: r
        for r in records
        if r.op in CREATE_OPS and r.acl_ret == 0 and r.call_sequence not in skip
    }
    for r in records:
        if r.op not in RT_CREATE_OPS or r.acl_ret != 0 or r.call_sequence in skip:
            continue
        if r.nested_under_acl and r.parent_acl_call_sequence:
            parent = acl_by_seq.get(r.parent_acl_call_sequence)
            if parent is None:
                errors.append(
                    f"rt_without_parent_acl seq={r.call_sequence} parent={r.parent_acl_call_sequence}"
                )
            elif parent.raw_event != r.raw_event:
                errors.append(
                    f"create_layer_mismatch acl=0x{parent.raw_event:x} rt=0x{r.raw_event:x}"
                )
    return errors


def rebuild_generations(records: list[TraceRecord]) -> tuple[list[dict], list[str], dict]:
    skip_nested, nest_errors, acl_parent_mismatch = identify_nested_create_skips(records)
    states: dict[tuple[int, int], EventState] = defaultdict(EventState)
    out_rows: list[dict] = []
    errors: list[str] = list(nest_errors)
    record_keys: dict[int, RecordKey] = {}
    wait_bindings: dict[int, RecordKey] = {}
    concurrent_ambiguous = 0
    lifecycle_incomplete = 0
    create_layer_mismatch = len(merge_acl_rt_creates(records, skip_nested))
    active_intervals: dict[tuple[int, int], list[tuple[int, int, int]]] = defaultdict(list)

    def overlap(a0: int, a1: int, b0: int, b1: int) -> bool:
        return not (a1 <= b0 or b1 <= a0)

    sorted_records = sorted(records, key=lambda x: x.call_sequence)

    for rec in sorted_records:
        key = (rec.pid, rec.raw_event)
        st = states[key]
        row: dict[str, Any] = {
            "call_sequence": rec.call_sequence,
            "op": rec.op,
            "pid": rec.pid,
            "tid": rec.tid,
            "raw_event": rec.raw_event,
            "raw_stream": rec.raw_stream,
            "acl_ret": rec.acl_ret,
            "source": rec.source,
            "nested_under_acl": rec.nested_under_acl,
            "parent_acl_call_sequence": rec.parent_acl_call_sequence,
            "enter_realtime_ns": rec.enter_realtime_ns,
            "exit_realtime_ns": rec.exit_realtime_ns,
            "lifetime_epoch": st.lifetime_epoch,
            "reset_epoch": st.reset_epoch,
            "record_epoch": st.record_epoch,
            "logical_create_skipped": int(rec.call_sequence in skip_nested),
        }
        is_logical_create = (
            rec.op in ALL_CREATE_OPS
            and rec.acl_ret == 0
            and rec.call_sequence not in skip_nested
        )

        if is_logical_create:
            active_intervals[key].append((rec.call_sequence, rec.enter_realtime_ns, rec.exit_realtime_ns))
            ivs = active_intervals[key]
            if len(ivs) >= 2:
                a, b = ivs[-2], ivs[-1]
                if overlap(a[1], a[2], b[1], b[2]):
                    concurrent_ambiguous += 1
                    errors.append(
                        f"concurrent_ambiguous pid={rec.pid} event=0x{rec.raw_event:x} seq={rec.call_sequence}"
                    )

        if rec.acl_ret != 0:
            out_rows.append(row)
            continue

        if is_logical_create:
            if st.alive:
                lifecycle_incomplete += 1
                errors.append(f"create_without_destroy pid={rec.pid} event=0x{rec.raw_event:x}")
            st.alive = True
            st.lifetime_epoch += 1
            st.reset_epoch = 0
            st.record_epoch = 0
            st.has_create = True
            st.create_source = "acl" if rec.op in CREATE_OPS else "rt_fallback"
        elif rec.op == DESTROY_OP:
            if not st.alive:
                lifecycle_incomplete += 1
                errors.append(f"destroy_when_dead pid={rec.pid} event=0x{rec.raw_event:x}")
            st.alive = False
        elif rec.op == RESET_OP:
            if not st.alive or not st.has_create:
                lifecycle_incomplete += 1
                errors.append(f"reset_when_dead pid={rec.pid} event=0x{rec.raw_event:x}")
            else:
                st.reset_epoch += 1
                st.record_epoch = 0
        elif rec.op == RECORD_OP:
            if not st.alive or not st.has_create:
                lifecycle_incomplete += 1
                errors.append(f"record_without_create pid={rec.pid} event=0x{rec.raw_event:x}")
            else:
                st.record_epoch += 1
                rk = RecordKey(rec.pid, rec.raw_event, st.lifetime_epoch, st.reset_epoch, st.record_epoch)
                record_keys[rec.call_sequence] = rk
                row["record_key"] = rk.as_tuple()
                row["create_source"] = st.create_source
        elif rec.op == WAIT_OP:
            if not st.alive or not st.has_create:
                lifecycle_incomplete += 1
                errors.append(f"wait_when_dead pid={rec.pid} event=0x{rec.raw_event:x}")
            else:
                candidates = [
                    (seq, rk)
                    for seq, rk in record_keys.items()
                    if rk.pid == rec.pid
                    and rk.raw_event == rec.raw_event
                    and rk.lifetime_epoch == st.lifetime_epoch
                    and rk.reset_epoch == st.reset_epoch
                    and seq < rec.call_sequence
                ]
                if not candidates:
                    lifecycle_incomplete += 1
                    errors.append(f"wait_without_record pid={rec.pid} event=0x{rec.raw_event:x}")
                else:
                    bind_seq, bind_key = max(candidates, key=lambda x: x[0])
                    wait_bindings[rec.call_sequence] = bind_key
                    row["bound_record_key"] = bind_key.as_tuple()
                    row["bound_record_call_sequence"] = bind_seq
        row["lifetime_epoch"] = st.lifetime_epoch
        row["reset_epoch"] = st.reset_epoch
        row["record_epoch"] = st.record_epoch
        out_rows.append(row)

    stats = {
        "concurrent_ambiguous": concurrent_ambiguous,
        "lifecycle_incomplete": lifecycle_incomplete,
        "create_layer_mismatch": create_layer_mismatch,
        "acl_create_parent_mismatch": acl_parent_mismatch,
        "n_record_keys": len(record_keys),
        "n_wait_bindings": len(wait_bindings),
        "nested_create_skipped": len(skip_nested),
    }
    return out_rows, errors, {"record_keys": record_keys, "wait_bindings": wait_bindings, **stats}


def export_task_histogram(
    db_path: Path, active_start: int, active_end: int, analysis_dir: Path
) -> None:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    schema_lines = ["PRAGMA table_info(TASK):"]
    for row in cur.execute("PRAGMA table_info(TASK)"):
        schema_lines.append(str(row))
    (analysis_dir / "task_schema.txt").write_text("\n".join(schema_lines) + "\n")

    def rows(where: str = "", params: tuple = ()) -> list[tuple]:
        q = f"""
        SELECT COALESCE(s.value, printf('id:%d', t.taskType)) AS task_type,
               t.taskType AS type_id,
               COUNT(*) AS n
        FROM TASK AS t
        LEFT JOIN STRING_IDS AS s ON s.id = t.taskType
        {where}
        GROUP BY t.taskType, s.value
        ORDER BY n DESC, task_type
        """
        return list(cur.execute(q, params))

    full = rows()
    active = rows("WHERE t.startNs >= ? AND t.startNs <= ?", (active_start, active_end))
    with (analysis_dir / "task_type_histogram.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scope", "task_type", "type_id", "n"])
        for task_type, type_id, n in full:
            w.writerow(["full", task_type, type_id, n])
        for task_type, type_id, n in active:
            w.writerow(["active", task_type, type_id, n])
    con.close()


def load_tasks(db_path: Path) -> list[TaskRow]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    tasks = [
        TaskRow(int(r[0]), r[1], r[2], int(r[3]), int(r[4]), int(r[5]))
        for r in cur.execute(
            "SELECT rowid, connectionId, streamId, taskType, startNs, endNs FROM TASK ORDER BY startNs"
        )
    ]
    con.close()
    return tasks


def discover_event_task_types(
    db_path: Path,
    active_start: int,
    active_end: int,
    string_ids: dict[int, str] | None = None,
) -> tuple[int | None, int | None, list[dict], list[str]]:
    """Pick EVENT_RECORD / EVENT_WAIT types by bidirectional unique CANN coverage."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    if string_ids is None:
        string_ids = load_string_ids(cur)

    cann_record = [
        r[0]
        for r in cur.execute(
            """
            SELECT c.connectionId FROM CANN_API c
            JOIN STRING_IDS s ON s.id = c.name
            WHERE s.value = 'aclrtRecordEvent' AND c.startNs BETWEEN ? AND ?
            """,
            (active_start, active_end),
        )
    ]
    cann_wait = [
        r[0]
        for r in cur.execute(
            """
            SELECT c.connectionId FROM CANN_API c
            JOIN STRING_IDS s ON s.id = c.name
            WHERE s.value = 'aclrtStreamWaitEvent' AND c.startNs BETWEEN ? AND ?
            """,
            (active_start, active_end),
        )
    ]
    cann_rec_set = set(cann_record)
    cann_wait_set = set(cann_wait)

    candidates: list[dict] = []
    notes: list[str] = []
    record_type: int | None = None
    wait_type: int | None = None

    for type_id, name in string_ids.items():
        tl = name.lower()
        if "record" not in tl and "wait" not in tl:
            continue
        active_tasks = list(
            cur.execute(
                """
                SELECT rowid, connectionId, streamId, startNs, endNs
                FROM TASK WHERE taskType = ? AND startNs BETWEEN ? AND ?
                ORDER BY startNs
                """,
                (type_id, active_start, active_end),
            )
        )
        if not active_tasks:
            continue
        task_cids = {t[1] for t in active_tasks}
        rec_overlap = task_cids & cann_rec_set
        wait_overlap = task_cids & cann_wait_set
        cand = {
            "task_type": name,
            "type_id": type_id,
            "active_count": len(active_tasks),
            "record_cid_overlap": len(rec_overlap),
            "wait_cid_overlap": len(wait_overlap),
        }
        candidates.append(cand)
        for rowid, cid, sid, start_ns, end_ns in active_tasks:
            candidates.append(
                {
                    "rowid": rowid,
                    "connectionId": cid,
                    "streamId": sid,
                    "startNs": start_ns,
                    "endNs": end_ns,
                    "taskType": name,
                    "type_id": type_id,
                }
            )

        if (
            record_type is None
            and "record" in tl
            and len(rec_overlap) == len(cann_rec_set) == len(active_tasks)
            and len(task_cids) == len(active_tasks)
        ):
            record_type = type_id
        if (
            wait_type is None
            and "wait" in tl
            and "notify" not in tl
            and len(wait_overlap) == len(active_tasks)
            and len(task_cids) == len(active_tasks)
        ):
            wait_type = type_id

    if wait_type is None:
        for type_id, name in string_ids.items():
            if name == "EVENT_WAIT":
                wait_type = type_id
                notes.append("event_wait_fallback_by_name_partial_coverage")
                break

    if record_type is None:
        notes.append("no_event_record_type_with_full_bidirectional_record_coverage")
    if wait_type is None:
        notes.append("no_event_wait_type_with_full_bidirectional_wait_coverage")

    con.close()
    return record_type, wait_type, candidates, notes


def write_event_task_candidates(candidates: list[dict], path: Path) -> None:
    with path.open("w", newline="") as f:
        if not candidates:
            w = csv.DictWriter(f, fieldnames=["note"])
            w.writeheader()
            w.writerow({"note": "empty"})
            return
        keys = sorted({k for row in candidates for k in row})
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(candidates)


def freeze_compute_streams(
    db_path: Path, active_start: int, active_end: int, string_ids: dict[int, str]
) -> set[int]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    type_ids = [i for i, v in string_ids.items() if v in COMPUTE_TASK_NAMES]
    streams: set[int] = set()
    if type_ids:
        placeholders = ",".join("?" for _ in type_ids)
        for (sid,) in cur.execute(
            f"""
            SELECT DISTINCT streamId FROM TASK
            WHERE taskType IN ({placeholders}) AND startNs BETWEEN ? AND ?
            """,
            (*type_ids, active_start, active_end),
        ):
            streams.add(int(sid))
    con.close()
    return streams


def load_cann_api(db_path: Path) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    string_ids = load_string_ids(cur)
    rows = []
    for start_ns, end_ns, typ, global_tid, connection_id, name_id in cur.execute(
        "SELECT startNs, endNs, type, globalTid, connectionId, name FROM CANN_API ORDER BY startNs"
    ):
        name = resolve_string(string_ids, name_id)
        rows.append(
            {
                "start_ns": int(start_ns),
                "end_ns": int(end_ns),
                "type": typ,
                "global_tid": int(global_tid),
                "connection_id": connection_id,
                "name": name,
            }
        )
    con.close()
    return rows


def global_tid_parts(global_tid: int) -> tuple[int, int]:
    return (global_tid >> 32) & 0xFFFFFFFF, global_tid & 0xFFFFFFFF


def align_api(
    preload_records: list[TraceRecord],
    cann_rows: list[dict],
    names: set[str],
    active_start: int,
    active_end: int,
) -> tuple[list[dict], list[dict]]:
    by_thread_pre: dict[tuple[int, int, str], list[TraceRecord]] = defaultdict(list)
    by_thread_cann: dict[tuple[int, int, str], list[dict]] = defaultdict(list)
    for r in preload_records:
        if r.op == RECORD_OP and r.acl_ret == 0 and active_start <= r.enter_realtime_ns <= active_end:
            by_thread_pre[(r.pid, r.tid, "aclrtRecordEvent")].append(r)
        elif r.op == WAIT_OP and r.acl_ret == 0 and active_start <= r.enter_realtime_ns <= active_end:
            by_thread_pre[(r.pid, r.tid, "aclrtStreamWaitEvent")].append(r)
    for r in cann_rows:
        if r["name"] not in names:
            continue
        if not (active_start <= r["start_ns"] <= active_end):
            continue
        pid, tid = global_tid_parts(r["global_tid"])
        by_thread_cann[(pid, tid, r["name"])].append(r)

    for key in by_thread_pre:
        by_thread_pre[key].sort(key=lambda x: x.call_sequence)
    for key in by_thread_cann:
        by_thread_cann[key].sort(key=lambda x: x["start_ns"])

    aligned: list[dict] = []
    unmatched: list[dict] = []
    keys = set(by_thread_cann) | set(by_thread_pre)
    for key in sorted(keys):
        cann_list = by_thread_cann.get(key, [])
        pre_list = by_thread_pre.get(key, [])
        if len(cann_list) != len(pre_list):
            for i in range(max(len(cann_list), len(pre_list))):
                c = cann_list[i] if i < len(cann_list) else None
                p = pre_list[i] if i < len(pre_list) else None
                row = _alignment_row(key, i, c, p)
                unmatched.append({**row, "reason": "count_mismatch"})
            continue
        for i, (c, p) in enumerate(zip(cann_list, pre_list)):
            row = _alignment_row(key, i, c, p)
            aligned.append(row)
    return aligned, unmatched


def _alignment_row(key: tuple[int, int, str], i: int, c: dict | None, p: TraceRecord | None) -> dict:
    return {
        "name": key[2],
        "pid": key[0],
        "tid": key[1],
        "ordinal": i,
        "cann_start_ns": c["start_ns"] if c else None,
        "cann_end_ns": c["end_ns"] if c else None,
        "cann_connection_id": c["connection_id"] if c else None,
        "preload_call_sequence": p.call_sequence if p else None,
        "preload_enter_ns": p.enter_realtime_ns if p else None,
        "preload_exit_ns": p.exit_realtime_ns if p else None,
        "start_delta_ns": int(p.enter_realtime_ns) - int(c["start_ns"]) if p and c else None,
        "end_delta_ns": int(p.exit_realtime_ns) - int(c["end_ns"]) if p and c else None,
    }


def build_preload_ordinal_maps(
    rank0_records: list[TraceRecord], active_start: int, active_end: int
) -> dict[tuple[int, int, str, int], TraceRecord]:
    """Map (pid, tid, api_name, ordinal) -> preload trace record."""
    out: dict[tuple[int, int, str, int], TraceRecord] = {}
    buckets: dict[tuple[int, int, str], list[TraceRecord]] = defaultdict(list)
    for r in rank0_records:
        if r.acl_ret != 0:
            continue
        if not (active_start <= r.enter_realtime_ns <= active_end):
            continue
        if r.op == RECORD_OP:
            buckets[(r.pid, r.tid, "aclrtRecordEvent")].append(r)
        elif r.op == WAIT_OP:
            buckets[(r.pid, r.tid, "aclrtStreamWaitEvent")].append(r)
    for key, recs in buckets.items():
        recs.sort(key=lambda x: x.call_sequence)
        for i, rec in enumerate(recs):
            out[(key[0], key[1], key[2], i)] = rec
    return out


def build_cann_ordinal_maps(
    cann_rows: list[dict], active_start: int, active_end: int
) -> dict[tuple[int, int, str, int], dict]:
    out: dict[tuple[int, int, str, int], dict] = {}
    buckets: dict[tuple[int, int, str], list[dict]] = defaultdict(list)
    for r in cann_rows:
        if r["name"] not in ("aclrtRecordEvent", "aclrtStreamWaitEvent"):
            continue
        if not (active_start <= r["start_ns"] <= active_end):
            continue
        pid, tid = global_tid_parts(r["global_tid"])
        buckets[(pid, tid, r["name"])].append(r)
    for key, rows in buckets.items():
        rows.sort(key=lambda x: x["start_ns"])
        for i, row in enumerate(rows):
            out[(key[0], key[1], key[2], i)] = row
    return out


def load_comm_ops(db_path: Path, active_start: int, active_end: int) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    string_ids = load_string_ids(cur)
    comm_ops = []
    for row in cur.execute(
        "SELECT opName, startNs, endNs, connectionId FROM COMMUNICATION_OP ORDER BY startNs"
    ):
        name = resolve_string(string_ids, row[0])
        if name is None or not name.startswith("hcom_allReduce_"):
            continue
        start_ns = int(row[1])
        if not (active_start <= start_ns <= active_end):
            continue
        comm_ops.append(
            {
                "op_name": name,
                "start_ns": start_ns,
                "end_ns": int(row[2]),
                "connection_id": row[3],
            }
        )
    con.close()
    return comm_ops


def terminal_comm_task(tasks: list[TaskRow]) -> TaskRow | None:
    if not tasks:
        return None
    return max(tasks, key=lambda t: (t.end_ns, t.start_ns, t.rowid))


def stream_tasks_by_id(all_tasks: list[TaskRow]) -> dict[int, list[TaskRow]]:
    by_stream: dict[int, list[TaskRow]] = defaultdict(list)
    for t in all_tasks:
        by_stream[t.stream_id].append(t)
    for sid in by_stream:
        by_stream[sid].sort(key=lambda t: (t.start_ns, t.end_ns, t.rowid))
    return by_stream


def fifo_event_record_after_terminal(
    stream_tasks: list[TaskRow], terminal: TaskRow, event_record_type: int
) -> tuple[TaskRow | None, dict[str, Any]]:
    meta: dict[str, Any] = {
        "fifo_candidates": 0,
        "reject_reason": "",
    }
    idx = next((i for i, t in enumerate(stream_tasks) if t.rowid == terminal.rowid), None)
    if idx is None:
        meta["reject_reason"] = "terminal_not_on_stream"
        return None, meta
    found: list[TaskRow] = []
    for t in stream_tasks[idx + 1 :]:
        if t.task_type == event_record_type:
            found.append(t)
            break
        meta["fifo_candidates"] = int(meta["fifo_candidates"]) + 1
    if not found:
        meta["reject_reason"] = "no_event_record_fifo"
        return None, meta
    if len(found) != 1:
        meta["reject_reason"] = "multiple_event_record_fifo"
        return None, meta
    return found[0], meta


def build_allreduce_chains(
    db_path: Path,
    gen_info: dict,
    active_start: int,
    active_end: int,
    rank0_pid: int,
    rank0_records: list[TraceRecord],
    aligned_api: list[dict],
    event_record_type: int | None,
    event_wait_type: int | None,
    string_ids: dict[int, str],
) -> tuple[list[dict], list[dict], list[dict]]:
    all_tasks = load_tasks(db_path)
    by_cid: dict[int, list[TaskRow]] = defaultdict(list)
    for t in all_tasks:
        by_cid[t.connection_id].append(t)
    by_stream = stream_tasks_by_id(all_tasks)

    cann_rows = load_cann_api(db_path)
    cann_record = [
        r
        for r in cann_rows
        if r["name"] == "aclrtRecordEvent" and active_start <= r["start_ns"] <= active_end
    ]
    cann_wait = [
        r
        for r in cann_rows
        if r["name"] == "aclrtStreamWaitEvent" and active_start <= r["start_ns"] <= active_end
    ]
    cann_rec_by_cid: dict[Any, list[dict]] = defaultdict(list)
    cann_wait_by_cid: dict[Any, list[dict]] = defaultdict(list)
    for r in cann_record:
        cann_rec_by_cid[r["connection_id"]].append(r)
    for r in cann_wait:
        cann_wait_by_cid[r["connection_id"]].append(r)

    preload_by_ordinal = build_preload_ordinal_maps(rank0_records, active_start, active_end)
    cann_by_ordinal = build_cann_ordinal_maps(cann_rows, active_start, active_end)
    cann_to_ordinal = {
        (row["connection_id"], row["start_ns"]): key[3]
        for key, row in cann_by_ordinal.items()
        if key[2] == "aclrtRecordEvent"
    }

    compute_streams = freeze_compute_streams(db_path, active_start, active_end, string_ids)
    wait_tasks_by_cid: dict[int, list[TaskRow]] = defaultdict(list)
    if event_wait_type is not None:
        for t in all_tasks:
            if t.task_type == event_wait_type and active_start <= t.start_ns <= active_end:
                wait_tasks_by_cid[t.connection_id].append(t)

    comm_ops = load_comm_ops(db_path, active_start, active_end)
    chains: list[dict] = []
    fifo_edges: list[dict] = []
    unmatched: list[dict] = []
    used_event_record_rows: set[int] = set()
    used_record_keys: set[tuple[int, int, int, int, int]] = set()

    if event_record_type is None:
        for idx, op in enumerate(comm_ops):
            unmatched.append({"comm_index": idx, "reason": "event_record_type_missing", **op})
        return chains, unmatched, fifo_edges

    for idx, op in enumerate(comm_ops):
        cid = op["connection_id"]
        edge: dict[str, Any] = {
            "comm_index": idx,
            "comm_op_name": op["op_name"],
            "comm_connection_id": cid,
            "comm_start_ns": op["start_ns"],
            "comm_end_ns": op["end_ns"],
            "comm_task_candidates": len(by_cid.get(cid, [])),
            "fifo_intervening_tasks": 0,
            "event_record_candidates": 0,
            "cann_record_candidates": 0,
            "reject_reason": "",
        }
        comm_tasks = by_cid.get(cid, [])
        if not comm_tasks:
            edge["reject_reason"] = "no_comm_tasks"
            fifo_edges.append(edge)
            unmatched.append({"comm_index": idx, "reason": "no_tasks", **op})
            continue

        terminal = terminal_comm_task(comm_tasks)
        if terminal is None:
            edge["reject_reason"] = "no_terminal"
            fifo_edges.append(edge)
            unmatched.append({"comm_index": idx, "reason": "no_terminal", **op})
            continue

        edge["terminal_rowid"] = terminal.rowid
        edge["terminal_task_type"] = resolve_string(string_ids, terminal.task_type)
        edge["completion_stream"] = terminal.stream_id

        stream_list = by_stream.get(terminal.stream_id, [])
        event_task, fifo_meta = fifo_event_record_after_terminal(
            stream_list, terminal, event_record_type
        )
        edge["fifo_intervening_tasks"] = fifo_meta.get("fifo_candidates", 0)
        if event_task is None:
            edge["reject_reason"] = fifo_meta.get("reject_reason", "fifo_failed")
            fifo_edges.append(edge)
            unmatched.append({"comm_index": idx, "reason": edge["reject_reason"], **op})
            continue

        edge["event_record_rowid"] = event_task.rowid
        edge["event_record_connection_id"] = event_task.connection_id
        edge["event_record_task_type"] = resolve_string(string_ids, event_task.task_type)
        edge["event_record_candidates"] = 1

        if event_task.rowid in used_event_record_rows:
            edge["reject_reason"] = "event_record_task_reused"
            fifo_edges.append(edge)
            unmatched.append({"comm_index": idx, "reason": "event_record_task_reused", **op})
            continue

        rec_cann_list = cann_rec_by_cid.get(event_task.connection_id, [])
        edge["cann_record_candidates"] = len(rec_cann_list)
        if len(rec_cann_list) != 1:
            edge["reject_reason"] = "cann_record_not_unique"
            fifo_edges.append(edge)
            unmatched.append(
                {
                    "comm_index": idx,
                    "reason": "cann_record_not_unique",
                    "n": len(rec_cann_list),
                    "event_record_cid": event_task.connection_id,
                    **op,
                }
            )
            continue

        best_rec = rec_cann_list[0]
        pid, tid = global_tid_parts(best_rec["global_tid"])
        ordinal = None
        for (p, t, name, ord_i), row in cann_by_ordinal.items():
            if (
                name == "aclrtRecordEvent"
                and row["connection_id"] == best_rec["connection_id"]
                and row["start_ns"] == best_rec["start_ns"]
            ):
                ordinal = ord_i
                break
        pre_rec = preload_by_ordinal.get((pid, tid, "aclrtRecordEvent", ordinal or 0))
        if pre_rec is None:
            edge["reject_reason"] = "no_preload_record_ordinal"
            fifo_edges.append(edge)
            unmatched.append({"comm_index": idx, "reason": "no_preload_record", **op})
            continue

        rk = gen_info["record_keys"].get(pre_rec.call_sequence)
        if rk is None:
            edge["reject_reason"] = "no_record_key"
            fifo_edges.append(edge)
            unmatched.append({"comm_index": idx, "reason": "no_record_key", **op})
            continue

        if rk.as_tuple() in used_record_keys:
            edge["reject_reason"] = "record_key_reused"
            fifo_edges.append(edge)
            unmatched.append({"comm_index": idx, "reason": "record_key_reused", **op})
            continue

        bound_waits = [
            (seq, wk) for seq, wk in gen_info["wait_bindings"].items() if wk.as_tuple() == rk.as_tuple()
        ]
        record_stream = pre_rec.raw_stream
        compute_waits: list[tuple[int, TraceRecord, dict, TaskRow | None]] = []
        for seq, _wk in bound_waits:
            pre_wait = next((r for r in rank0_records if r.call_sequence == seq), None)
            if pre_wait is None:
                continue
            if pre_wait.raw_stream == record_stream:
                continue
            wait_ordinal = sum(
                1
                for r in rank0_records
                if r.op == WAIT_OP
                and r.acl_ret == 0
                and r.pid == pre_wait.pid
                and r.tid == pre_wait.tid
                and active_start <= r.enter_realtime_ns <= active_end
                and r.call_sequence < seq
            )
            cann_for_wait = cann_by_ordinal.get(
                (pre_wait.pid, pre_wait.tid, "aclrtStreamWaitEvent", wait_ordinal)
            )
            if cann_for_wait is None:
                continue
            wait_task_rows = wait_tasks_by_cid.get(cann_for_wait["connection_id"], [])
            if len(wait_task_rows) != 1:
                continue
            wt = wait_task_rows[0]
            if compute_streams and wt.stream_id not in compute_streams:
                continue
            compute_waits.append((seq, pre_wait, cann_for_wait, wt))

        unique_chain = len(compute_waits) == 1
        chain = {
            "comm_index": idx,
            "comm_op_name": op["op_name"],
            "comm_connection_id": cid,
            "comm_start_ns": op["start_ns"],
            "comm_end_ns": op["end_ns"],
            "terminal_rowid": terminal.rowid,
            "completion_stream": terminal.stream_id,
            "event_record_rowid": event_task.rowid,
            "event_record_connection_id": event_task.connection_id,
            "record_cann_start_ns": best_rec["start_ns"],
            "record_cann_end_ns": best_rec["end_ns"],
            "record_cann_connection_id": best_rec["connection_id"],
            "preload_record_call_sequence": pre_rec.call_sequence,
            "raw_event": rk.raw_event,
            "lifetime_epoch": rk.lifetime_epoch,
            "reset_epoch": rk.reset_epoch,
            "record_epoch": rk.record_epoch,
            "record_stream": pre_rec.raw_stream,
            "n_bound_waits": len(bound_waits),
            "n_compute_waits": len(compute_waits),
            "compute_stream_ids": sorted(compute_streams),
            "unique_chain": unique_chain,
        }
        if len(compute_waits) == 1:
            seq, pre_wait, cann_w, wt = compute_waits[0]
            chain["preload_wait_call_sequence"] = seq
            chain["wait_cann_connection_id"] = cann_w["connection_id"]
            chain["event_wait_rowid"] = wt.rowid if wt else None
            chain["event_wait_stream_id"] = wt.stream_id if wt else None
            chain["wait_stream"] = pre_wait.raw_stream

        if not unique_chain:
            edge["reject_reason"] = "compute_wait_not_unique"
            unmatched.append({**chain, "reason": "compute_wait_not_unique"})
        else:
            used_event_record_rows.add(event_task.rowid)
            used_record_keys.add(rk.as_tuple())

        fifo_edges.append(edge)
        chains.append(chain)

    return chains, unmatched, fifo_edges


def find_profiler_db(run_out: Path) -> Path:
    hits = sorted(run_out.glob("**/ascend_pytorch_profiler_0.db"))
    if not hits:
        hits = sorted(
            p
            for p in run_out.glob("**/ascend_pytorch_profiler*.db")
            if p.name != "analysis.db"
        )
    if not hits:
        raise FileNotFoundError(f"no ascend_pytorch_profiler db under {run_out}")
    return hits[0]


def load_profile_window(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 2**62
    data = json.loads(path.read_text())
    return int(data["active_start_realtime_ns"]), int(data["active_end_realtime_ns"])


def infer_active_from_db(db_path: Path) -> tuple[int, int]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    row = cur.execute("SELECT MIN(startNs), MAX(endNs) FROM CANN_API").fetchone()
    con.close()
    if row and row[0] is not None:
        return int(row[0]), int(row[1])
    return 0, 2**62


def iter_trace_bins(trace_dir: Path, pid_min: int = 0, pid_max: int = 2**31 - 1) -> list[Path]:
    paths: list[Path] = []
    for path in sorted(trace_dir.glob("rank_*_pid_*.events.bin")):
        m = TRACE_BIN_RE.search(path.name)
        if not m:
            continue
        rank = int(m.group(1))
        pid = int(m.group(2))
        if rank < 0:
            continue
        if not (pid_min <= pid <= pid_max):
            continue
        paths.append(path)
    return paths


def pick_rank0_trace(
    trace_dir: Path, pid_min: int = 0, pid_max: int = 2**31 - 1
) -> tuple[dict, list[TraceRecord], list[dict]]:
    metas: list[dict] = []
    by_rank0: list[tuple[dict, list[TraceRecord]]] = []
    for path in iter_trace_bins(trace_dir, pid_min=pid_min, pid_max=pid_max):
        meta, recs = load_trace_bin(path)
        if meta["rank"] < 0:
            continue
        metas.append(meta)
        if meta["rank"] == 0:
            by_rank0.append((meta, recs))
    if not by_rank0:
        raise ValueError("no rank0 trace bin after rank>=0 / pid filter")
    rank0_meta, rank0_records = sorted(by_rank0, key=lambda x: x[0]["pid"])[0]
    return rank0_meta, rank0_records, metas


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace-dir", required=True)
    ap.add_argument("--profiler-out", required=True)
    ap.add_argument("--analysis-dir", required=True)
    ap.add_argument("--profile-window", default="")
    ap.add_argument("--active-start-ns", type=int, default=-1)
    ap.add_argument("--active-end-ns", type=int, default=-1)
    ap.add_argument("--pid-min", type=int, default=0)
    ap.add_argument("--pid-max", type=int, default=2**31 - 1)
    args = ap.parse_args()

    trace_dir = Path(args.trace_dir)
    analysis_dir = Path(args.analysis_dir)
    analysis_dir.mkdir(parents=True, exist_ok=True)

    rank0_meta, rank0_records, trace_meta = pick_rank0_trace(
        trace_dir, pid_min=args.pid_min, pid_max=args.pid_max
    )
    all_records: list[TraceRecord] = []
    for path in iter_trace_bins(trace_dir, pid_min=args.pid_min, pid_max=args.pid_max):
        _, recs = load_trace_bin(path)
        all_records.extend(recs)

    gen_rows, gen_errors, gen_info = rebuild_generations(rank0_records)
    gen_info["preload_records"] = rank0_records

    db_path = find_profiler_db(Path(args.profiler_out))
    if args.profile_window:
        active_start, active_end = load_profile_window(Path(args.profile_window))
    elif args.active_start_ns >= 0 and args.active_end_ns >= 0:
        active_start, active_end = args.active_start_ns, args.active_end_ns
    else:
        pw = Path(args.profiler_out) / "profile_window.json"
        active_start, active_end = load_profile_window(pw)
        if not pw.exists():
            active_start, active_end = infer_active_from_db(db_path)

    export_task_histogram(db_path, active_start, active_end, analysis_dir)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    string_ids = load_string_ids(con.cursor())
    con.close()
    record_type, wait_type, event_candidates, type_notes = discover_event_task_types(
        db_path, active_start, active_end, string_ids
    )
    write_event_task_candidates(event_candidates, analysis_dir / "event_task_candidates.csv")

    cann_rows = load_cann_api(db_path)
    names = {"aclrtRecordEvent", "aclrtStreamWaitEvent"}
    aligned, api_unmatched = align_api(rank0_records, cann_rows, names, active_start, active_end)

    preload_rank0_active_record = sum(
        1
        for r in rank0_records
        if r.op == RECORD_OP and r.acl_ret == 0 and active_start <= r.enter_realtime_ns <= active_end
    )
    preload_rank0_active_wait = sum(
        1
        for r in rank0_records
        if r.op == WAIT_OP and r.acl_ret == 0 and active_start <= r.enter_realtime_ns <= active_end
    )
    cann_rank0_active_record = sum(
        1
        for r in cann_rows
        if r["name"] == "aclrtRecordEvent" and active_start <= r["start_ns"] <= active_end
    )
    cann_rank0_active_wait = sum(
        1
        for r in cann_rows
        if r["name"] == "aclrtStreamWaitEvent" and active_start <= r["start_ns"] <= active_end
    )
    preload_all_ranks_total_record = sum(
        1 for r in all_records if r.op == RECORD_OP and r.acl_ret == 0
    )
    preload_all_ranks_total_wait = sum(1 for r in all_records if r.op == WAIT_OP and r.acl_ret == 0)

    count_mismatch = (
        preload_rank0_active_record != 51
        or preload_rank0_active_wait != 24
        or cann_rank0_active_record != 51
        or cann_rank0_active_wait != 24
    )
    if count_mismatch:
        md = [
            "# count_mismatch",
            f"- active window: [{active_start}, {active_end}]",
            f"- preload rank0 active record/wait: {preload_rank0_active_record}/{preload_rank0_active_wait}",
            f"- cann rank0 active record/wait: {cann_rank0_active_record}/{cann_rank0_active_wait}",
            f"- preload all ranks total record/wait: {preload_all_ranks_total_record}/{preload_all_ranks_total_wait}",
            "- expected baseline: 51/24",
        ]
        (analysis_dir / "count_mismatch.md").write_text("\n".join(md) + "\n")

    active_waits = [
        r
        for r in rank0_records
        if r.op == WAIT_OP and r.acl_ret == 0 and active_start <= r.enter_realtime_ns <= active_end
    ]
    wait_bound = sum(1 for r in active_waits if r.call_sequence in gen_info["wait_bindings"])

    chains, chain_unmatched, fifo_edges = build_allreduce_chains(
        db_path,
        gen_info,
        active_start,
        active_end,
        rank0_pid=rank0_meta["pid"],
        rank0_records=rank0_records,
        aligned_api=aligned,
        event_record_type=record_type,
        event_wait_type=wait_type,
        string_ids=string_ids,
    )
    comm_ops = load_comm_ops(db_path, active_start, active_end)
    n_comm = len(comm_ops)
    n_unique_chain = sum(1 for c in chains if c.get("unique_chain"))

    resolver_audit = sorted(trace_dir.glob("rank_*_pid_*.resolver_audit.json"))
    if resolver_audit:
        (analysis_dir / "resolver_audit.json").write_text(resolver_audit[0].read_text())

    dropped = sum(m.get("dropped", 0) for m in trace_meta)
    fatal = sum(m.get("fatal", 0) for m in trace_meta)
    late_calls = sum(m.get("late_calls", 0) for m in trace_meta)
    resolver_conflict = sum(m.get("resolver_target_conflict", 0) for m in trace_meta)

    summary = {
        "analyzer_version": ANALYZER_VERSION,
        "event_record_type_id": record_type,
        "event_wait_type_id": wait_type,
        "type_discovery_notes": type_notes,
        "A0_trace_files": len(trace_meta),
        "A1_dropped": dropped,
        "A1_fatal": fatal,
        "A1_late_calls": late_calls,
        "A1_resolver_conflict": resolver_conflict,
        "A2_preload_rank0_active_record": preload_rank0_active_record,
        "A2_preload_rank0_active_wait": preload_rank0_active_wait,
        "A2_cann_rank0_active_record": cann_rank0_active_record,
        "A2_cann_rank0_active_wait": cann_rank0_active_wait,
        "A2_preload_all_ranks_total_record": preload_all_ranks_total_record,
        "A2_preload_all_ranks_total_wait": preload_all_ranks_total_wait,
        "A2_api_unmatched": len(api_unmatched),
        "A2_count_mismatch": count_mismatch,
        "A3_lifecycle_incomplete": gen_info["lifecycle_incomplete"],
        "A3_concurrent_ambiguous": gen_info["concurrent_ambiguous"],
        "A3_create_layer_mismatch": gen_info["create_layer_mismatch"],
        "A3_acl_create_parent_mismatch": gen_info["acl_create_parent_mismatch"],
        "A4_active_waits": len(active_waits),
        "A4_wait_bound_unique": wait_bound,
        "A5_n_comm": n_comm,
        "A5_chains_found": sum(1 for c in chains if c.get("event_record_rowid")),
        "A5_chains_unique_record": sum(
            1 for c in chains if c.get("record_cann_connection_id") and not any(
                u.get("comm_index") == c.get("comm_index")
                for u in chain_unmatched
                if u.get("reason") in ("cann_record_not_unique", "event_record_task_reused", "record_key_reused")
            )
        ),
        "A6_unique_chain": n_unique_chain,
        "A6_unique_chain_rate": (n_unique_chain / n_comm) if n_comm else 0.0,
        "baseline_unique_rate": 0.25,
        "gen_errors_n": len(gen_errors),
        "chain_unmatched_n": len(chain_unmatched),
        "active_start_ns": active_start,
        "active_end_ns": active_end,
        "rank0_pid": rank0_meta["pid"],
        "db_path": str(db_path),
    }

    with (analysis_dir / "api_alignment.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(aligned[0].keys()) if aligned else ["name"])
        w.writeheader()
        w.writerows(aligned)
    with (analysis_dir / "event_generations.csv").open("w", newline="") as f:
        if gen_rows:
            fieldnames = sorted({k for r in gen_rows for k in r.keys()})
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(gen_rows)
    with (analysis_dir / "stream_fifo_edges.csv").open("w", newline="") as f:
        if fifo_edges:
            w = csv.DictWriter(f, fieldnames=list(fifo_edges[0].keys()))
            w.writeheader()
            w.writerows(fifo_edges)
    with (analysis_dir / "allreduce_chains.csv").open("w", newline="") as f:
        if chains:
            keys = sorted({k for c in chains for k in c})
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(chains)
    unmatched_rows = api_unmatched + chain_unmatched + [{"reason": e} for e in gen_errors]
    with (analysis_dir / "unmatched.csv").open("w", newline="") as f:
        if unmatched_rows:
            keys = sorted({k for r in unmatched_rows for k in r.keys()})
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(unmatched_rows)
    (analysis_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (analysis_dir / "trace_meta.json").write_text(json.dumps(trace_meta, indent=2) + "\n")
    md = [
        "# D51 Event Preload V3 分析摘要",
        "",
        f"- analyzer V{ANALYZER_VERSION}；profiler DB: `{db_path}`",
        f"- rank0 PID: {rank0_meta['pid']}",
        f"- active 窗 realtime: [{active_start}, {active_end}]",
        f"- 冻结 TASK 类型：EVENT_RECORD type_id={record_type}；EVENT_WAIT type_id={wait_type}",
        f"- preload rank0 active Record/Wait: {preload_rank0_active_record}/{preload_rank0_active_wait}",
        f"- CANN rank0 active Record/Wait: {cann_rank0_active_record}/{cann_rank0_active_wait}",
        f"- A3 lifecycle_incomplete: {gen_info['lifecycle_incomplete']}（嵌套 Create 合并跳过 {gen_info['nested_create_skipped']} 条）",
        f"- A4 active Wait 唯一绑定: {wait_bound}/{len(active_waits)}",
        f"- A5/A6 AllReduce 唯一链: {n_unique_chain}/{n_comm}（旧时间近邻基线 3/12=25%，仅对照）",
        "",
        "因果链（V3）：`hcom_allReduce_%` → comm cid 内 terminal TASK → 同 streamId FIFO 紧邻 EVENT_RECORD TASK",
        "→ 该 TASK 自己的 connectionId → 唯一 CANN aclrtRecordEvent → A2 preload Record 代次键 → A4 compute-stream Wait。",
        "comm connectionId 不得直连 Record connectionId。",
        "",
        "采集：LD_PRELOAD hook dlsym/ACL Create/Record/Wait；torch_npu profiler Level1 导出 CANN_API/TASK/COMMUNICATION_OP。",
        "16 rank 同载 preload；分析仅 rank0 trace 对 rank0 DB。",
    ]
    (analysis_dir / "SUMMARY.md").write_text("\n".join(md) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
