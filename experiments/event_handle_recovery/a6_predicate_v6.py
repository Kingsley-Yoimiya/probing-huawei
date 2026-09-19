#!/usr/bin/env python3
"""D51 V6: frozen A6 predicate — preload_stream + CANN aclrtStreamWaitEvent via A2 ordinal."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from analyze_event_pairs import RecordKey, TraceRecord, WAIT_OP

API_WAIT = "aclrtStreamWaitEvent"
WAIT_IDENTITY_SOURCE = "preload_stream+cann_api"

FAIL_RECORD_KEY_NOT_UNIQUE = "record_key_not_unique"
FAIL_RECORD_NOT_UNIQUE = "record_not_unique"
FAIL_NO_A4_WAIT = "no_a4_wait"
FAIL_SAME_STREAM = "same_stream_wait_excluded"
FAIL_A2_NOT_UNIQUE = "a2_wait_not_unique"
FAIL_CARDINALITY = "compute_wait_cardinality_not_one"
FAIL_REUSED = "wait_reused_across_comm"

A6_DEFINITION_VERSION = "v6_preload_stream_cann_api"


def record_key_str(rk: RecordKey) -> str:
    return f"({rk.pid},{rk.raw_event},{rk.lifetime_epoch},{rk.reset_epoch},{rk.record_epoch})"


def record_key_for_comm(
    chain: dict[str, Any],
    gen_info: dict[str, Any],
) -> tuple[RecordKey | None, TraceRecord | None, list[str]]:
    """Extract unique Record key and preload Record from A5 chain output."""
    failures: list[str] = []
    rec_seq = chain.get("preload_record_call_sequence")
    if rec_seq is None:
        failures.append(FAIL_RECORD_KEY_NOT_UNIQUE)
        return None, None, failures

    rk = gen_info["record_keys"].get(rec_seq)
    if rk is None:
        failures.append(FAIL_RECORD_KEY_NOT_UNIQUE)
        return None, None, failures

    pre_rec = chain.get("_preload_record")
    if pre_rec is None:
        failures.append(FAIL_RECORD_NOT_UNIQUE)
        return None, None, failures

    return rk, pre_rec, failures


def a4_bound_waits(
    gen_info: dict[str, Any],
    rk: RecordKey,
    rank0_records: list[TraceRecord],
    active_start: int,
    active_end: int,
) -> list[tuple[int, TraceRecord]]:
    """All A4-bound active successful Waits for the full five-tuple Record key."""
    rk_tuple = rk.as_tuple()
    out: list[tuple[int, TraceRecord]] = []
    for seq, wk in gen_info["wait_bindings"].items():
        if wk.as_tuple() != rk_tuple:
            continue
        rec = next((r for r in rank0_records if r.call_sequence == seq), None)
        if rec is None:
            continue
        if rec.acl_ret != 0:
            continue
        if not (active_start <= rec.enter_realtime_ns <= active_end):
            continue
        out.append((seq, rec))
    out.sort(key=lambda x: x[0])
    return out


def drop_same_stream_waits(
    bound: list[tuple[int, TraceRecord]],
    record_stream: int,
) -> tuple[list[tuple[int, TraceRecord]], list[tuple[int, TraceRecord]]]:
    """Split bound Waits into cross-stream (kept) vs same-stream-as-Record (dropped)."""
    cross: list[tuple[int, TraceRecord]] = []
    same: list[tuple[int, TraceRecord]] = []
    for seq, rec in bound:
        if rec.raw_stream == record_stream:
            same.append((seq, rec))
        else:
            cross.append((seq, rec))
    return cross, same


def wait_ordinal(
    rank0_records: list[TraceRecord],
    pre_wait: TraceRecord,
    active_start: int,
    active_end: int,
) -> int:
    return sum(
        1
        for r in rank0_records
        if r.op == WAIT_OP
        and r.acl_ret == 0
        and r.pid == pre_wait.pid
        and r.tid == pre_wait.tid
        and active_start <= r.enter_realtime_ns <= active_end
        and r.call_sequence < pre_wait.call_sequence
    )


def align_wait_by_a2_ordinal(
    pre_wait: TraceRecord,
    rank0_records: list[TraceRecord],
    cann_by_ordinal: dict[tuple[int, int, str, int], dict],
    active_start: int,
    active_end: int,
) -> tuple[int, dict | None, int]:
    """Return (ordinal, unique CANN row or None, candidate_count)."""
    if pre_wait.acl_ret != 0:
        return -1, None, 0
    if not (active_start <= pre_wait.enter_realtime_ns <= active_end):
        return -1, None, 0
    ord_i = wait_ordinal(rank0_records, pre_wait, active_start, active_end)
    cann = cann_by_ordinal.get((pre_wait.pid, pre_wait.tid, API_WAIT, ord_i))
    cand = 1 if cann is not None else 0
    return ord_i, cann, cand


def a2_unique_wait(
    pre_wait: TraceRecord,
    rank0_records: list[TraceRecord],
    cann_by_ordinal: dict[tuple[int, int, str, int], dict],
    active_start: int,
    active_end: int,
) -> tuple[bool, int, dict | None, int]:
    """True iff A2 ordinal yields exactly one aclrtStreamWaitEvent."""
    ord_i, cann, cand = align_wait_by_a2_ordinal(
        pre_wait, rank0_records, cann_by_ordinal, active_start, active_end
    )
    unique = cand == 1 and cann is not None and cann.get("name", API_WAIT) == API_WAIT
    return unique, ord_i, cann, cand


@dataclass
class WaitTraceRow:
    comm_op_name: str
    record_key: str
    wait_call_sequence: int
    wait_raw_stream: int
    record_raw_stream: int
    a4_bound: bool = True
    same_as_record_stream: bool = False
    kept_after_stream_filter: bool = False
    a2_ordinal: int = -1
    a2_candidate_count: int = 0
    a2_unique: bool = False
    counts_as_compute_wait: bool = False
    cann_wait_rowid: int | None = None
    cann_wait_connection_id: Any = None
    claimed_by_comm_count: int = 0
    first_failed_predicate: str = ""


@dataclass
class CommA6Result:
    comm_op_name: str
    comm_connection_id: Any
    record_key: str
    preload_record_call_sequence: int | None
    record_raw_stream: int | None
    n_a4_bound_waits: int = 0
    n_same_stream_waits_dropped: int = 0
    n_cross_stream_waits: int = 0
    preload_wait_call_sequence: int | None = None
    wait_raw_stream: int | None = None
    a2_ordinal: int | None = None
    cann_wait_rowid: int | None = None
    cann_wait_connection_id: Any = None
    a2_candidate_count: int | None = None
    n_compute_waits: int = 0
    wait_identity_source: str = ""
    event_wait_task_rowid: int | None = None
    wait_reused: bool = False
    a6_pass: bool = False
    reason: str = ""
    failed_predicates: list[str] = field(default_factory=list)
    wait_traces: list[WaitTraceRow] = field(default_factory=list)


def select_compute_wait(
    cross_stream: list[tuple[int, TraceRecord]],
    rank0_records: list[TraceRecord],
    cann_by_ordinal: dict[tuple[int, int, str, int], dict],
    active_start: int,
    active_end: int,
    comm_op_name: str,
    record_key: str,
    record_stream: int,
) -> tuple[list[tuple[int, TraceRecord, int, dict]], list[WaitTraceRow]]:
    """Return compute Waits (seq, rec, ordinal, cann) and per-Wait trace rows."""
    compute: list[tuple[int, TraceRecord, int, dict]] = []
    traces: list[WaitTraceRow] = []
    for seq, rec in cross_stream:
        unique, ord_i, cann, cand = a2_unique_wait(
            rec, rank0_records, cann_by_ordinal, active_start, active_end
        )
        is_compute = unique
        tr = WaitTraceRow(
            comm_op_name=comm_op_name,
            record_key=record_key,
            wait_call_sequence=seq,
            wait_raw_stream=rec.raw_stream,
            record_raw_stream=record_stream,
            a4_bound=True,
            same_as_record_stream=False,
            kept_after_stream_filter=True,
            a2_ordinal=ord_i,
            a2_candidate_count=cand,
            a2_unique=unique,
            counts_as_compute_wait=is_compute,
            cann_wait_rowid=cann.get("rowid") if cann else None,
            cann_wait_connection_id=cann.get("connection_id") if cann else None,
        )
        if not unique:
            tr.first_failed_predicate = FAIL_A2_NOT_UNIQUE
        traces.append(tr)
        if is_compute and cann is not None:
            compute.append((seq, rec, ord_i, cann))
    return compute, traces


def evaluate_a6_per_comm(
    *,
    comm_op_name: str,
    comm_connection_id: Any,
    chain: dict[str, Any],
    gen_info: dict[str, Any],
    rank0_records: list[TraceRecord],
    cann_by_ordinal: dict[tuple[int, int, str, int], dict],
    active_start: int,
    active_end: int,
) -> CommA6Result:
    res = CommA6Result(
        comm_op_name=comm_op_name,
        comm_connection_id=comm_connection_id,
        record_key="",
        preload_record_call_sequence=chain.get("preload_record_call_sequence"),
        record_raw_stream=chain.get("record_stream"),
    )
    failures: list[str] = []

    rk, pre_rec, key_failures = record_key_for_comm(chain, gen_info)
    failures.extend(key_failures)
    if rk is None or pre_rec is None:
        res.failed_predicates = failures
        res.reason = failures[0] if failures else FAIL_RECORD_KEY_NOT_UNIQUE
        return res

    res.record_key = record_key_str(rk)
    res.record_raw_stream = pre_rec.raw_stream
    res.preload_record_call_sequence = pre_rec.call_sequence

    bound = a4_bound_waits(gen_info, rk, rank0_records, active_start, active_end)
    res.n_a4_bound_waits = len(bound)
    if not bound:
        failures.append(FAIL_NO_A4_WAIT)

    cross, same = drop_same_stream_waits(bound, pre_rec.raw_stream)
    res.n_same_stream_waits_dropped = len(same)
    res.n_cross_stream_waits = len(cross)

    same_traces = [
        WaitTraceRow(
            comm_op_name=comm_op_name,
            record_key=res.record_key,
            wait_call_sequence=seq,
            wait_raw_stream=rec.raw_stream,
            record_raw_stream=pre_rec.raw_stream,
            a4_bound=True,
            same_as_record_stream=True,
            kept_after_stream_filter=False,
            first_failed_predicate=FAIL_SAME_STREAM,
        )
        for seq, rec in same
    ]

    compute, cross_traces = select_compute_wait(
        cross,
        rank0_records,
        cann_by_ordinal,
        active_start,
        active_end,
        comm_op_name,
        res.record_key,
        pre_rec.raw_stream,
    )
    res.wait_traces = same_traces + cross_traces
    res.n_compute_waits = len(compute)

    # Plan V6: every cross-stream Wait w ∈ Wx(c) must satisfy a2_unique_wait(w).
    if any(not tr.a2_unique for tr in cross_traces):
        failures.append(FAIL_A2_NOT_UNIQUE)

    if res.n_compute_waits != 1:
        failures.append(FAIL_CARDINALITY)

    if len(compute) == 1:
        seq, _rec, ord_i, cann = compute[0]
        res.preload_wait_call_sequence = seq
        res.wait_raw_stream = _rec.raw_stream
        res.a2_ordinal = ord_i
        res.cann_wait_connection_id = cann.get("connection_id")
        res.cann_wait_rowid = cann.get("rowid")
        res.a2_candidate_count = 1
        res.wait_identity_source = WAIT_IDENTITY_SOURCE

    res.failed_predicates = failures
    res.a6_pass = len(failures) == 0
    res.reason = "" if res.a6_pass else failures[0]
    return res


def _pid_from_record_key(record_key: str) -> int:
    parts = record_key.strip("()").split(",")
    return int(parts[0]) if parts and parts[0] else 0


def check_global_wait_reuse(
    comm_results: list[CommA6Result],
) -> tuple[dict[tuple[int, int], list[str]], bool]:
    """Map (pid, wait_call_sequence) -> comm names; mark reuse failures."""
    claims: dict[tuple[int, int], list[str]] = {}
    for cr in comm_results:
        if cr.preload_wait_call_sequence is None:
            continue
        key = (_pid_from_record_key(cr.record_key), cr.preload_wait_call_sequence)
        claims.setdefault(key, []).append(cr.comm_op_name)

    global_ok = True
    for key, comms in claims.items():
        n = len(comms)
        for cr in comm_results:
            for wt in cr.wait_traces:
                if wt.wait_call_sequence == key[1]:
                    wt.claimed_by_comm_count = n
        if n <= 1:
            continue
        global_ok = False
        for cr in comm_results:
            if cr.preload_wait_call_sequence == key[1]:
                cr.wait_reused = True
                if FAIL_REUSED not in cr.failed_predicates:
                    cr.failed_predicates.append(FAIL_REUSED)
                cr.a6_pass = False
                cr.reason = FAIL_REUSED
            for wt in cr.wait_traces:
                if wt.wait_call_sequence == key[1]:
                    wt.first_failed_predicate = FAIL_REUSED

    return claims, global_ok


def apply_task_diagnostic(
    comm_results: list[CommA6Result],
    task_by_cid: dict[Any, list[dict]],
) -> None:
    """Optional post-hoc TASK rowid fill; must not affect a6_pass."""
    for cr in comm_results:
        cid = cr.cann_wait_connection_id
        if cid is None:
            cr.event_wait_task_rowid = None
            continue
        rows = task_by_cid.get(cid, [])
        ew = [t for t in rows if t.get("task_type_name") == "EVENT_WAIT"]
        cr.event_wait_task_rowid = ew[0]["rowid"] if len(ew) == 1 else None
