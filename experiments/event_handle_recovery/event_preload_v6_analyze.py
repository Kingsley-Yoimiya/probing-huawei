#!/usr/bin/env python3
"""D51 V6: A6 recompute on frozen V2b — preload_stream + CANN API identity."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from a6_predicate_v6 import (  # noqa: E402
    A6_DEFINITION_VERSION,
    FAIL_A2_NOT_UNIQUE,
    FAIL_CARDINALITY,
    FAIL_NO_A4_WAIT,
    FAIL_RECORD_KEY_NOT_UNIQUE,
    FAIL_REUSED,
    FAIL_SAME_STREAM,
    WAIT_IDENTITY_SOURCE,
    apply_task_diagnostic,
    check_global_wait_reuse,
    evaluate_a6_per_comm,
    record_key_str,
)
from analyze_event_pairs import (  # noqa: E402
    WAIT_OP,
    align_api,
    build_allreduce_chains,
    build_cann_ordinal_maps,
    discover_event_task_types,
    load_cann_api,
    load_profile_window,
    load_string_ids,
    pick_rank0_trace,
    rebuild_generations,
)

ANALYZER_VERSION = "v6_a6_preload_stream_cann_api"
TARGET_GAP_COMMS = ("hcom_allReduce__612_4_1", "hcom_allReduce__612_5_1")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row:
            if k not in seen:
                keys.append(k)
                seen.add(k)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def scan_tasks_by_cid(db_path: Path, cids: list[Any]) -> list[dict]:
    if not cids:
        return []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    placeholders = ",".join("?" for _ in cids)
    rows = []
    for r in cur.execute(
        f"""
        SELECT t.rowid, t.connectionId, t.streamId, t.taskType,
               COALESCE(s.value, printf('id:%d', t.taskType))
        FROM TASK AS t
        LEFT JOIN STRING_IDS AS s ON s.id = t.taskType
        WHERE t.connectionId IN ({placeholders})
        ORDER BY t.connectionId, t.startNs, t.rowid
        """,
        tuple(cids),
    ):
        rows.append(
            {
                "rowid": int(r[0]),
                "connectionId": r[1],
                "streamId": int(r[2]),
                "taskType": int(r[3]),
                "task_type_name": r[4],
            }
        )
    con.close()
    return rows


def enrich_cann_with_rowid(db_path: Path, cann_rows: list[dict]) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    keyed: dict[tuple, int] = {}
    for rowid, start_ns, end_ns, connection_id, name_id in cur.execute(
        """
        SELECT rowid, startNs, endNs, connectionId, name FROM CANN_API
        ORDER BY startNs, rowid
        """
    ):
        keyed[(int(start_ns), int(end_ns), connection_id, name_id)] = int(rowid)
    con.close()
    string_ids = None
    out = []
    for r in cann_rows:
        nr = dict(r)
        if "rowid" not in nr:
            nr["rowid"] = None
        out.append(nr)
    return out


def build_cann_ordinal_maps_with_rowid(
    db_path: Path, cann_rows: list[dict], active_start: int, active_end: int
) -> dict[tuple[int, int, str, int], dict]:
    from analyze_event_pairs import build_cann_ordinal_maps, global_tid_parts
    from collections import defaultdict

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rowid_map: dict[tuple, int] = {}
    for rowid, start_ns, end_ns, global_tid, connection_id, name_id in con.execute(
        "SELECT rowid, startNs, endNs, globalTid, connectionId, name FROM CANN_API"
    ):
        pid, tid = global_tid_parts(int(global_tid))
        rowid_map[(pid, tid, int(start_ns), int(end_ns), connection_id, name_id)] = int(rowid)
    con.close()

    base = build_cann_ordinal_maps(cann_rows, active_start, active_end)
    for key, row in base.items():
        pid, tid, name, _ord = key
        rid = rowid_map.get(
            (pid, tid, row["start_ns"], row["end_ns"], row["connection_id"], None)
        )
        if rid is None:
            for (p, t, sn, en, cid, _), rid2 in rowid_map.items():
                if p == pid and t == tid and sn == row["start_ns"] and en == row["end_ns"] and cid == row["connection_id"]:
                    rid = rid2
                    break
        row["rowid"] = rid
    return base


def build_casebook(comm_results: list, task_rows: list[dict]) -> str:
    lines = ["# D51 V6 A6 Casebook\n", f"**A6 定义：** `{A6_DEFINITION_VERSION}`\n"]
    for cr in sorted(comm_results, key=lambda x: x.comm_op_name):
        lines.append(f"## {cr.comm_op_name}\n")
        lines.append(f"- **A6：** {'PASS' if cr.a6_pass else 'FAIL'} (`{cr.reason or 'ok'}`)")
        lines.append(f"- **Record 代次键：** {cr.record_key}")
        lines.append(
            f"- **preload Record：** cs={cr.preload_record_call_sequence}, "
            f"raw_stream={cr.record_raw_stream}"
        )
        lines.append(
            f"- **compute Wait：** cs={cr.preload_wait_call_sequence}, "
            f"raw_stream={cr.wait_raw_stream}, CANN cid={cr.cann_wait_connection_id}"
        )
        lines.append(f"- **wait_identity_source：** {cr.wait_identity_source or '—'}")
        lines.append(f"- **event_wait_task_rowid：** {cr.event_wait_task_rowid}")
        if cr.comm_op_name in TARGET_GAP_COMMS:
            lines.append(
                f"- **V5 Q2 背景：** Wait stream `{cr.wait_raw_stream}` ≠ "
                f"Record stream `{cr.record_raw_stream}`；cid TASK 全表 0 行不影响 V6 A6"
            )
        lines.append("\n| Wait cs | raw_stream | 同 Record 流 | A2 ord | A2 cand | compute | 谓词 |")
        lines.append("|---|---|---|---|---|---|---|")
        for wt in cr.wait_traces:
            lines.append(
                f"| {wt.wait_call_sequence} | {wt.wait_raw_stream} | "
                f"{'是' if wt.same_as_record_stream else '否'} | {wt.a2_ordinal} | "
                f"{wt.a2_candidate_count} | {'是' if wt.counts_as_compute_wait else '否'} | "
                f"{wt.first_failed_predicate or '—'} |"
            )
        cid = cr.cann_wait_connection_id
        case_tasks = [t for t in task_rows if t["connectionId"] == cid]
        if not case_tasks and cid is not None:
            lines.append(f"\nTASK @ cid {cid}：**0 行**（全 taskType/stream）\n")
        lines.append("")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    trace_dir = Path(args.trace_dir)
    db_path = Path(args.db_path)
    analysis_dir = Path(args.analysis_dir)
    analysis_dir.mkdir(parents=True, exist_ok=True)
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    db_hash_before = sha256_file(db_path)

    if args.profile_window:
        active_start, active_end = load_profile_window(Path(args.profile_window))
    else:
        pw = trace_dir.parent / "profiler" / "profile_window.json"
        if pw.exists():
            active_start, active_end = load_profile_window(pw)
        else:
            from analyze_event_pairs import infer_active_from_db

            active_start, active_end = infer_active_from_db(db_path)

    rank0_meta, rank0_records, _ = pick_rank0_trace(
        trace_dir, pid_min=args.pid_min, pid_max=args.pid_max
    )
    rank0_pid = int(rank0_meta["pid"])

    gen_rows, gen_errors, gen_info = rebuild_generations(rank0_records)
    cann_rows = load_cann_api(db_path)
    aligned, api_unmatched = align_api(
        rank0_records,
        cann_rows,
        {"aclrtRecordEvent", "aclrtStreamWaitEvent"},
        active_start,
        active_end,
    )
    cann_by_ord = build_cann_ordinal_maps_with_rowid(db_path, cann_rows, active_start, active_end)

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    string_ids = load_string_ids(con.cursor())
    con.close()

    record_type, wait_type, _, _ = discover_event_task_types(
        db_path, active_start, active_end, string_ids
    )

    chains, unmatched_a5, _fifo = build_allreduce_chains(
        db_path,
        gen_info,
        active_start,
        active_end,
        rank0_pid,
        rank0_records,
        aligned,
        record_type,
        wait_type,
        string_ids,
    )

    chain_by_name: dict[str, dict] = {}
    for c in chains:
        chain_by_name[c["comm_op_name"]] = c
    for u in unmatched_a5:
        if "comm_op_name" in u or "op_name" in u:
            name = u.get("comm_op_name") or u.get("op_name")
            if name and name not in chain_by_name:
                chain_by_name[name] = u

    from analyze_event_pairs import load_comm_ops

    comm_ops = load_comm_ops(db_path, active_start, active_end)
    denominator = len(comm_ops)

    comm_results = []
    for op in comm_ops:
        name = op["op_name"]
        chain = dict(chain_by_name.get(name, op))
        rec_seq = chain.get("preload_record_call_sequence")
        if rec_seq is not None:
            pre_rec = next((r for r in rank0_records if r.call_sequence == rec_seq), None)
            chain["_preload_record"] = pre_rec

        cr = evaluate_a6_per_comm(
            comm_op_name=name,
            comm_connection_id=op["connection_id"],
            chain=chain,
            gen_info=gen_info,
            rank0_records=rank0_records,
            cann_by_ordinal=cann_by_ord,
            active_start=active_start,
            active_end=active_end,
        )
        comm_results.append(cr)

    _, global_reuse_ok = check_global_wait_reuse(comm_results)

    cids = [cr.cann_wait_connection_id for cr in comm_results if cr.cann_wait_connection_id]
    task_rows = scan_tasks_by_cid(db_path, list(set(cids)))
    task_by_cid: dict[Any, list[dict]] = {}
    for t in task_rows:
        task_by_cid.setdefault(t["connectionId"], []).append(t)
    apply_task_diagnostic(comm_results, task_by_cid)

    a6_pass_count = sum(1 for cr in comm_results if cr.a6_pass)

    # Old A6 baseline from chains (TASK-based unique_chain)
    old_a6_pass = sum(1 for c in chains if c.get("unique_chain"))

    a2_replay = {
        "preload_record": sum(
            1
            for r in rank0_records
            if r.op == 4 and r.acl_ret == 0 and active_start <= r.enter_realtime_ns <= active_end
        ),
        "preload_wait": sum(
            1
            for r in rank0_records
            if r.op == WAIT_OP and r.acl_ret == 0 and active_start <= r.enter_realtime_ns <= active_end
        ),
        "cann_record": sum(
            1
            for r in cann_rows
            if r["name"] == "aclrtRecordEvent" and active_start <= r["start_ns"] <= active_end
        ),
        "cann_wait": sum(
            1
            for r in cann_rows
            if r["name"] == "aclrtStreamWaitEvent" and active_start <= r["start_ns"] <= active_end
        ),
        "api_unmatched": len(api_unmatched),
    }

    a5_pass = sum(
        1
        for c in chains
        if c.get("preload_record_call_sequence") is not None
        and c.get("event_record_rowid") is not None
        and c.get("reject_reason", "") == ""
        or c.get("unique_chain") is not None
    )
    a5_unique_keys = len({c.get("raw_event") for c in chains if c.get("preload_record_call_sequence")})

    active_waits = [
        r
        for r in rank0_records
        if r.op == WAIT_OP
        and r.acl_ret == 0
        and active_start <= r.enter_realtime_ns <= active_end
    ]
    a4_bound_active = sum(1 for r in active_waits if r.call_sequence in gen_info["wait_bindings"])
    a4_bindings_all_trace = len(gen_info["wait_bindings"])

    acceptance_passed = (
        a6_pass_count == denominator == 12
        and global_reuse_ok
        and a4_bound_active == a2_replay["preload_wait"] == 24
    )

    chain_csv: list[dict] = []
    for cr in comm_results:
        chain_csv.append(
            {
                "comm_op_name": cr.comm_op_name,
                "comm_connection_id": cr.comm_connection_id,
                "record_key": cr.record_key,
                "preload_record_call_sequence": cr.preload_record_call_sequence,
                "record_raw_stream": cr.record_raw_stream,
                "n_a4_bound_waits": cr.n_a4_bound_waits,
                "n_same_stream_waits_dropped": cr.n_same_stream_waits_dropped,
                "n_cross_stream_waits": cr.n_cross_stream_waits,
                "preload_wait_call_sequence": cr.preload_wait_call_sequence,
                "wait_raw_stream": cr.wait_raw_stream,
                "a2_ordinal": cr.a2_ordinal,
                "cann_wait_rowid": cr.cann_wait_rowid,
                "cann_wait_connection_id": cr.cann_wait_connection_id,
                "a2_candidate_count": cr.a2_candidate_count,
                "n_compute_waits": cr.n_compute_waits,
                "wait_identity_source": cr.wait_identity_source,
                "event_wait_task_rowid": cr.event_wait_task_rowid,
                "wait_reused": cr.wait_reused,
                "a6_pass": cr.a6_pass,
                "reason": cr.reason,
                "failed_predicates": ";".join(cr.failed_predicates),
            }
        )
    write_csv(analysis_dir / "allreduce_chains.csv", chain_csv)

    trace_csv: list[dict] = []
    for cr in comm_results:
        for wt in cr.wait_traces:
            trace_csv.append(
                {
                    "comm_op_name": wt.comm_op_name,
                    "record_key": wt.record_key,
                    "wait_call_sequence": wt.wait_call_sequence,
                    "wait_raw_stream": wt.wait_raw_stream,
                    "record_raw_stream": wt.record_raw_stream,
                    "a4_bound": wt.a4_bound,
                    "same_as_record_stream": wt.same_as_record_stream,
                    "kept_after_stream_filter": wt.kept_after_stream_filter,
                    "a2_ordinal": wt.a2_ordinal,
                    "a2_candidate_count": wt.a2_candidate_count,
                    "a2_unique": wt.a2_unique,
                    "counts_as_compute_wait": wt.counts_as_compute_wait,
                    "claimed_by_comm_count": wt.claimed_by_comm_count,
                    "first_failed_predicate": wt.first_failed_predicate,
                    "cann_wait_connection_id": wt.cann_wait_connection_id,
                }
            )
    write_csv(analysis_dir / "a6_predicate_trace.csv", trace_csv)

    bin_hashes = {p.name: sha256_file(p) for p in sorted(trace_dir.glob("rank_*_pid_*.events.bin"))}
    db_hash_after = sha256_file(db_path)

    failures = [
        {
            "comm_op_name": cr.comm_op_name,
            "reason": cr.reason,
            "failed_predicates": cr.failed_predicates,
            "n_compute_waits": cr.n_compute_waits,
        }
        for cr in comm_results
        if not cr.a6_pass
    ]

    summary = {
        "analyzer_version": ANALYZER_VERSION,
        "a6_definition_version": A6_DEFINITION_VERSION,
        "run_id": args.run_id,
        "input_hashes": {
            "db_sha256_before": db_hash_before,
            "db_sha256_after": db_hash_after,
            "db_unchanged": db_hash_before == db_hash_after,
            "bins": bin_hashes,
        },
        "active_window": {"start_ns": active_start, "end_ns": active_end},
        "rank0_pid": rank0_pid,
        "denominator": denominator,
        "a2_replay": a2_replay,
        "a4_bound_waits": a4_bound_active,
        "a4_bindings_all_trace": a4_bindings_all_trace,
        "preload_waits_active": a2_replay["preload_wait"],
        "a5_chain_count": len(chains),
        "a5_pass_estimate": sum(1 for c in chains if c.get("preload_record_call_sequence")),
        "old_a6_unique_chain": old_a6_pass,
        "new_a6_pass_count": a6_pass_count,
        "global_wait_reuse_ok": global_reuse_ok,
        "acceptance_passed": acceptance_passed,
        "per_comm": {
            cr.comm_op_name: {
                "a6_pass": cr.a6_pass,
                "preload_wait_call_sequence": cr.preload_wait_call_sequence,
                "cann_wait_connection_id": cr.cann_wait_connection_id,
                "wait_identity_source": cr.wait_identity_source,
                "event_wait_task_rowid": cr.event_wait_task_rowid,
                "n_compute_waits": cr.n_compute_waits,
            }
            for cr in comm_results
        },
        "failures": failures,
        "target_gap_comms": {
            name: next((cr for cr in comm_results if cr.comm_op_name == name), None)
            and {
                "preload_wait_call_sequence": next(
                    cr.preload_wait_call_sequence
                    for cr in comm_results
                    if cr.comm_op_name == name
                ),
                "cann_wait_connection_id": next(
                    cr.cann_wait_connection_id for cr in comm_results if cr.comm_op_name == name
                ),
                "a6_pass": next(cr.a6_pass for cr in comm_results if cr.comm_op_name == name),
            }
            for name in TARGET_GAP_COMMS
        },
    }

    # Fix target_gap_comms serialization
    gap_info = {}
    for name in TARGET_GAP_COMMS:
        cr = next((x for x in comm_results if x.comm_op_name == name), None)
        if cr:
            gap_info[name] = {
                "preload_wait_call_sequence": cr.preload_wait_call_sequence,
                "cann_wait_connection_id": cr.cann_wait_connection_id,
                "wait_raw_stream": cr.wait_raw_stream,
                "record_raw_stream": cr.record_raw_stream,
                "a6_pass": cr.a6_pass,
                "event_wait_task_rowid": cr.event_wait_task_rowid,
            }
    summary["target_gap_comms"] = gap_info

    (analysis_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    (analysis_dir / "a6_casebook.md").write_text(build_casebook(comm_results, task_rows))

    acceptance_log = [
        f"run_id={args.run_id}",
        f"A2 Record={a2_replay['preload_record']}/{a2_replay['cann_record']}",
        f"A2 Wait={a2_replay['preload_wait']}/{a2_replay['cann_wait']}",
        f"api_unmatched={a2_replay['api_unmatched']}",
        f"A4 bound waits active={a4_bound_active}/{a2_replay['preload_wait']}",
        f"A4 bindings all_trace={a4_bindings_all_trace}",
        f"A5 chains={len(chains)}",
        f"old_A6={old_a6_pass}/{denominator}",
        f"new_A6={a6_pass_count}/{denominator}",
        f"acceptance_passed={acceptance_passed}",
    ]
    (log_dir / "acceptance.log").write_text("\n".join(acceptance_log) + "\n")
    (log_dir / "analysis.log").write_text(
        json.dumps(summary, indent=2, default=str) + "\n"
    )

    print(json.dumps(summary, indent=2, default=str))
    return 0 if acceptance_passed else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--trace-dir", required=True)
    ap.add_argument("--db-path", required=True)
    ap.add_argument("--analysis-dir", required=True)
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--profile-window", default="")
    ap.add_argument("--pid-min", type=int, default=26025)
    ap.add_argument("--pid-max", type=int, default=26040)
    args = ap.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
