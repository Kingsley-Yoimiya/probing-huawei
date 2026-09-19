#!/usr/bin/env python3
"""D51 V5: classify A6 wait gaps as Q1/Q2/Q3 from frozen DB + preload bins."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from analyze_event_pairs import (  # noqa: E402
    WAIT_OP,
    RecordKey,
    TaskRow,
    align_api,
    build_allreduce_chains,
    build_cann_ordinal_maps,
    build_preload_ordinal_maps,
    discover_event_task_types,
    freeze_compute_streams,
    global_tid_parts,
    load_cann_api,
    load_profile_window,
    load_string_ids,
    load_tasks,
    pick_rank0_trace,
    rebuild_generations,
)

TARGET_COMMS = ("hcom_allReduce__612_4_1", "hcom_allReduce__612_5_1")
ANALYZER_VERSION = "v5_wait_gap"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def export_schema(db_path: Path, out: Path, active_start: int, active_end: int, rank0_pid: int) -> None:
  con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
  cur = con.cursor()
  lines = [
      f"active_start_ns={active_start}",
      f"active_end_ns={active_end}",
      f"rank0_pid={rank0_pid}",
      "",
  ]
  for table in ("TASK", "CANN_API", "STRING_IDS", "COMMUNICATION_OP"):
      lines.append(f"PRAGMA table_info({table}):")
      for row in cur.execute(f"PRAGMA table_info({table})"):
          lines.append(str(row))
      lines.append("")
  out.write_text("\n".join(lines))


def record_key_str(rk: RecordKey) -> str:
    return f"({rk.pid},{rk.raw_event},{rk.lifetime_epoch},{rk.reset_epoch},{rk.record_epoch})"


def wait_ordinal_for(
    rank0_records: list, pre_wait, active_start: int, active_end: int
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


def classify_case(
    *,
    preload_ok: bool,
    cann_unique: bool,
    task_rows_total: int,
    event_wait_rows: int,
    a6_rejection: str,
) -> tuple[str, dict[str, bool]]:
    preds = {"q1": False, "q2": False, "q3": False}
    if not preload_ok:
        preds["q1"] = True
        return "Q1", preds
    if not cann_unique:
        return "UNRESOLVED_A2", preds
    if task_rows_total == 0:
        preds["q2"] = True
        return "Q2", preds
    preds["q3"] = True
    return "Q3", preds


def a6_rejection_reason(
    *,
    bound_waits: int,
    event_wait_rows: int,
    event_wait_on_compute: int,
    compute_streams: set[int],
    wait_stream_ids: list[int],
) -> str:
    if bound_waits == 0:
        return "no_bound_wait"
    if event_wait_rows == 0:
        if bound_waits > 0:
            return "type_rejected"
        return "no_wait_task"
    if event_wait_rows > 1:
        return "event_wait_cardinality_rejected"
    if event_wait_on_compute == 0:
        return "stream_rejected"
    if bound_waits != 1:
        return "compute_wait_not_unique"
    return "accepted"


def scan_tasks_by_cid(
    db_path: Path, cids: list[int], active_start: int, active_end: int
) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    if not cids:
        con.close()
        return []
    placeholders = ",".join("?" for _ in cids)
    rows = []
    for r in cur.execute(
        f"""
        SELECT t.rowid, t.connectionId, t.globalTaskId, t.taskId, t.streamId,
               t.taskType, COALESCE(s.value, printf('id:%d', t.taskType)),
               t.startNs, t.endNs, t.deviceId, t.contextId, t.modelId
        FROM TASK AS t
        LEFT JOIN STRING_IDS AS s ON s.id = t.taskType
        WHERE t.connectionId IN ({placeholders})
        ORDER BY t.connectionId, t.startNs, t.endNs, t.rowid
        """,
        tuple(cids),
    ):
        rows.append(
            {
                "rowid": int(r[0]),
                "connectionId": r[1],
                "globalTaskId": r[2],
                "taskId": r[3],
                "streamId": int(r[4]),
                "taskType": int(r[5]),
                "task_type_name": r[6],
                "startNs": int(r[7]),
                "endNs": int(r[8]),
                "deviceId": r[9],
                "contextId": r[10],
                "modelId": r[11],
                "in_active_window": active_start <= int(r[7]) <= active_end,
            }
        )
    con.close()
    return rows


def cann_wait_coverage(
    db_path: Path,
    cann_rows: list[dict],
    event_wait_type: int | None,
    active_start: int,
    active_end: int,
) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    string_ids = load_string_ids(cur)
    con.close()

    active_waits = [
        r
        for r in cann_rows
        if r["name"] == "aclrtStreamWaitEvent" and active_start <= r["start_ns"] <= active_end
    ]
    all_tasks = load_tasks(db_path)
    by_cid: dict[int, list[TaskRow]] = defaultdict(list)
    for t in all_tasks:
        by_cid[t.connection_id].append(t)

    out = []
    for i, w in enumerate(sorted(active_waits, key=lambda x: x["start_ns"])):
        cid = w["connection_id"]
        tasks = by_cid.get(cid, [])
        ew = [t for t in tasks if event_wait_type is not None and t.task_type == event_wait_type]
        types = sorted({resolve_task_name(string_ids, t.task_type) for t in tasks})
        streams = sorted({t.stream_id for t in tasks})
        out.append(
            {
                "wait_index": i,
                "cann_connection_id": cid,
                "cann_start_ns": w["start_ns"],
                "task_rows_total": len(tasks),
                "event_wait_rows": len(ew),
                "task_types": ";".join(types) if types else "",
                "stream_ids": ";".join(str(s) for s in streams) if streams else "",
                "is_target_gap": cid in {None},  # patched later
            }
        )
    return out


def resolve_task_name(string_ids: dict[int, str], type_id: int) -> str:
    return string_ids.get(type_id, f"id:{type_id}")


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    keys = sorted({k for row in rows for k in row})
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def build_casebook(
    cases: list[dict],
    preload_rows: list[dict],
    cann_rows: list[dict],
    task_rows: list[dict],
    filter_rows: list[dict],
) -> str:
    lines = ["# D51 V5 Wait Gap Casebook\n"]
    for case in cases:
        comm = case["comm_op_name"]
        lines.append(f"## {comm}\n")
        lines.append(f"**结论：{case['conclusion']}** ({case['rejection_detail']})\n")
        lines.append(
            "| 层/类型 | call_sequence/rowid | connectionId | taskType | stream/raw_stream | "
            "Record代次键 | same_record_generation_key | 身份依据 | A6保留 | 排除谓词 |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|")

        rk = case["record_key"]
        lines.append(
            f"| preload Record | {case['preload_record_call_sequence']} | — | — | "
            f"{case['record_stream']} | {rk} | direct_binding | direct_binding | — | — |"
        )

        for pr in preload_rows:
            if pr["comm_op_name"] != comm:
                continue
            lines.append(
                f"| preload Wait | {pr['call_sequence']} | — | WAIT | {pr['raw_stream']} | "
                f"{pr['bound_record_key']} | direct_binding | direct_binding | 是 | — |"
            )

        for cr in cann_rows:
            if cr["comm_op_name"] != comm:
                continue
            lines.append(
                f"| CANN Wait | ord={cr['ordinal']} | {cr['connectionId']} | aclrtStreamWaitEvent | — | "
                f"{rk} | true | inherited_by_api_ordinal | 是 | — |"
            )

        cid = case.get("cann_connection_id")
        case_tasks = [t for t in task_rows if t["connectionId"] == cid]
        if not case_tasks:
            lines.append(
                f"| TASK (none) | — | {cid} | — | — | {rk} | inherited_by_connection_id | "
                f"inherited_by_connection_id | 否 | TASK行数=0 |"
            )
        for t in case_tasks:
            on_compute = t["streamId"] in case.get("compute_streams", [])
            kept = t["task_type_name"] == "EVENT_WAIT" and on_compute and len(case_tasks) == 1
            pred = ""
            if t["task_type_name"] != "EVENT_WAIT":
                pred = "type_rejected"
            elif not on_compute:
                pred = "stream_rejected"
            lines.append(
                f"| TASK | {t['rowid']} | {t['connectionId']} | {t['task_type_name']} | "
                f"{t['streamId']} | {rk} | true | inherited_by_connection_id | "
                f"{'是' if kept else '否'} | {pred or '—'} |"
            )

        fr = next((r for r in filter_rows if r["comm_op_name"] == comm), {})
        lines.append(
            f"\n谓词追踪：preload_waits={fr.get('preload_wait_count')}, "
            f"task_rows_same_cid={fr.get('task_rows_same_cid')}, "
            f"event_wait_rows={fr.get('event_wait_rows_same_cid')}, "
            f"compute_streams={fr.get('compute_streams')}, "
            f"a6_rejection={fr.get('a6_rejection')}\n"
        )
    return "\n".join(lines) + "\n"


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
        if not pw.exists():
            pw = trace_dir.parent / "profile_window.json"
        if pw.exists():
            active_start, active_end = load_profile_window(pw)
        else:
            from analyze_event_pairs import infer_active_from_db

            active_start, active_end = infer_active_from_db(db_path)

    rank0_meta, rank0_records, trace_metas = pick_rank0_trace(
        trace_dir, pid_min=args.pid_min, pid_max=args.pid_max
    )
    rank0_pid = int(rank0_meta["pid"])

    export_schema(
        db_path,
        analysis_dir / "schema.txt",
        active_start,
        active_end,
        rank0_pid,
    )

    gen_rows, gen_errors, gen_info = rebuild_generations(rank0_records)
    cann_rows = load_cann_api(db_path)
    aligned, api_unmatched = align_api(
        rank0_records,
        cann_rows,
        {"aclrtRecordEvent", "aclrtStreamWaitEvent"},
        active_start,
        active_end,
    )
    preload_by_ord = build_preload_ordinal_maps(rank0_records, active_start, active_end)
    cann_by_ord = build_cann_ordinal_maps(cann_rows, active_start, active_end)

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    string_ids = load_string_ids(con.cursor())
    con.close()

    record_type, wait_type, _, _ = discover_event_task_types(
        db_path, active_start, active_end, string_ids
    )
    compute_streams = freeze_compute_streams(db_path, active_start, active_end, string_ids)

    chains, unmatched, _ = build_allreduce_chains(
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

    chain_by_comm = {c["comm_op_name"]: c for c in chains + unmatched}

    preload_out: list[dict] = []
    cann_out: list[dict] = []
    case_meta: list[dict] = []
    filter_trace: list[dict] = []

    for comm in TARGET_COMMS:
        chain = chain_by_comm.get(comm, {})
        rec_seq = chain.get("preload_record_call_sequence")
        if rec_seq is None:
            case_meta.append(
                {
                    "comm_op_name": comm,
                    "record_key": "",
                    "preload_record_call_sequence": None,
                    "record_stream": None,
                    "cann_connection_id": None,
                    "compute_streams": sorted(compute_streams),
                    "conclusion": "UNRESOLVED",
                    "rejection_detail": "comm_not_in_chains",
                }
            )
            continue

        rk = gen_info["record_keys"].get(rec_seq)
        rk_tuple = rk.as_tuple() if rk else None
        rk_str = record_key_str(rk) if rk else ""

        bindings = [
            (seq, wk)
            for seq, wk in gen_info["wait_bindings"].items()
            if wk.as_tuple() == rk_tuple
        ]

        for seq, wk in bindings:
            rec = next((r for r in rank0_records if r.call_sequence == seq), None)
            if rec is None:
                continue
            in_active = active_start <= rec.enter_realtime_ns <= active_end
            preload_out.append(
                {
                    "comm_op_name": comm,
                    "record_call_sequence": rec_seq,
                    "record_key": rk_str,
                    "call_sequence": seq,
                    "op": "WAIT",
                    "pid": rec.pid,
                    "tid": rec.tid,
                    "raw_event": rec.raw_event,
                    "raw_stream": rec.raw_stream,
                    "enter_realtime_ns": rec.enter_realtime_ns,
                    "exit_realtime_ns": rec.exit_realtime_ns,
                    "acl_ret": rec.acl_ret,
                    "bound_record_call_sequence": max(
                        s for s, k in gen_info["wait_bindings"].items() if k.as_tuple() == rk_tuple
                    ),
                    "bound_record_key": rk_str,
                    "in_active_window": in_active,
                }
            )

        preload_ok = (
            len(bindings) == 1
            and len(preload_out) >= 1
            and all(
                p["comm_op_name"] != comm or (p["acl_ret"] == 0 and p["in_active_window"])
                for p in preload_out
                if p["comm_op_name"] == comm
            )
        )

        cann_unique = False
        cann_cid = None
        if preload_ok and bindings:
            seq, _ = bindings[0]
            pre_wait = next(r for r in rank0_records if r.call_sequence == seq)
            ord_i = wait_ordinal_for(rank0_records, pre_wait, active_start, active_end)
            cann_w = cann_by_ord.get((pre_wait.pid, pre_wait.tid, "aclrtStreamWaitEvent", ord_i))
            bucket_pre = sum(
                1
                for k in preload_by_ord
                if k[0] == pre_wait.pid and k[1] == pre_wait.tid and k[2] == "aclrtStreamWaitEvent"
            )
            bucket_cann = sum(
                1
                for k in cann_by_ord
                if k[0] == pre_wait.pid and k[1] == pre_wait.tid and k[2] == "aclrtStreamWaitEvent"
            )
            cand = 1 if cann_w else 0
            cann_unique = cand == 1 and bucket_pre == bucket_cann
            if cann_w:
                cann_cid = cann_w["connection_id"]
                pid, tid = global_tid_parts(cann_w["global_tid"])
                cann_out.append(
                    {
                        "comm_op_name": comm,
                        "preload_wait_call_sequence": seq,
                        "ordinal": ord_i,
                        "cann_rowid": None,
                        "pid": pid,
                        "tid": tid,
                        "startNs": cann_w["start_ns"],
                        "endNs": cann_w["end_ns"],
                        "connectionId": cann_cid,
                        "bucket_preload_count": bucket_pre,
                        "bucket_cann_count": bucket_cann,
                        "cann_candidates": cand,
                    }
                )

        case_meta.append(
            {
                "comm_op_name": comm,
                "record_key": rk_str,
                "preload_record_call_sequence": rec_seq,
                "record_stream": chain.get("record_stream"),
                "cann_connection_id": cann_cid,
                "compute_streams": sorted(compute_streams),
                "preload_ok": preload_ok,
                "cann_unique": cann_unique,
                "bindings_count": len(bindings),
            }
        )

    write_csv(analysis_dir / "wait_gap_preload.csv", preload_out)
    write_csv(analysis_dir / "wait_gap_cann.csv", cann_out)

    target_cids = [m["cann_connection_id"] for m in case_meta if m.get("cann_connection_id")]
    task_rows = scan_tasks_by_cid(db_path, target_cids, active_start, active_end)
    write_csv(analysis_dir / "wait_gap_tasks_by_cid.csv", task_rows)

    queries_sql = analysis_dir / "queries.sql"
    queries_sql.write_text(
        """-- V5 primary TASK scan (no type/stream/window filter on WHERE)
SELECT t.rowid, t.connectionId, t.globalTaskId, t.taskId, t.streamId,
       t.taskType, COALESCE(s.value, printf('id:%d', t.taskType)) AS task_type_name,
       t.startNs, t.endNs, t.deviceId, t.contextId, t.modelId
FROM TASK AS t
LEFT JOIN STRING_IDS AS s ON s.id = t.taskType
WHERE t.connectionId IN (:cid_case_4, :cid_case_5)
ORDER BY t.connectionId, t.startNs, t.endNs, t.rowid;
"""
    )

    type_stream_counts: list[dict] = []
    for m in case_meta:
        cid = m.get("cann_connection_id")
        comm = m["comm_op_name"]
        subset = [t for t in task_rows if t["connectionId"] == cid]
        if not subset:
            type_stream_counts.append(
                {
                    "comm": comm,
                    "cann_cid": cid,
                    "taskType": "",
                    "task_type_name": "(zero rows)",
                    "streamId": "",
                    "count": 0,
                }
            )
        else:
            agg: dict[tuple, int] = defaultdict(int)
            for t in subset:
                agg[(t["taskType"], t["task_type_name"], t["streamId"])] += 1
            for (tt, tn, sid), n in sorted(agg.items()):
                type_stream_counts.append(
                    {
                        "comm": comm,
                        "cann_cid": cid,
                        "taskType": tt,
                        "task_type_name": tn,
                        "streamId": sid,
                        "count": n,
                    }
                )
    write_csv(analysis_dir / "wait_gap_task_type_stream_counts.csv", type_stream_counts)

    coverage = cann_wait_coverage(db_path, cann_rows, wait_type, active_start, active_end)
    target_cid_set = set(target_cids)
    for row in coverage:
        row["is_target_gap"] = row["cann_connection_id"] in target_cid_set
    write_csv(analysis_dir / "wait_gap_cann_task_coverage.csv", coverage)

    conclusions: list[str] = []
    per_case: dict[str, dict] = {}

    for m in case_meta:
        comm = m["comm_op_name"]
        cid = m.get("cann_connection_id")
        subset = [t for t in task_rows if t["connectionId"] == cid]
        ew = [t for t in subset if wait_type is not None and t["taskType"] == wait_type]
        ew_compute = [t for t in ew if t["streamId"] in compute_streams]

        rejection = a6_rejection_reason(
            bound_waits=m.get("bindings_count", 0),
            event_wait_rows=len(ew),
            event_wait_on_compute=len(ew_compute),
            compute_streams=compute_streams,
            wait_stream_ids=[t["streamId"] for t in ew],
        )

        conclusion, preds = classify_case(
            preload_ok=bool(m.get("preload_ok")),
            cann_unique=bool(m.get("cann_unique")),
            task_rows_total=len(subset),
            event_wait_rows=len(ew),
            a6_rejection=rejection,
        )
        m["conclusion"] = conclusion
        m["rejection_detail"] = rejection
        m["task_rows_total"] = len(subset)
        m["event_wait_rows"] = len(ew)
        conclusions.append(conclusion)

        filter_trace.append(
            {
                "comm_op_name": comm,
                "preload_wait_count": m.get("bindings_count", 0),
                "cann_wait_count": 1 if m.get("cann_unique") else 0,
                "task_rows_same_cid": len(subset),
                "event_wait_rows_same_cid": len(ew),
                "task_types": ";".join(sorted({t["task_type_name"] for t in subset})),
                "stream_ids": ";".join(str(t["streamId"]) for t in subset),
                "compute_streams": str(sorted(compute_streams)),
                "raw_stream_differs_from_record": True,
                "candidates_before_type_filter": len(subset),
                "candidates_after_event_wait_filter": len(ew),
                "candidates_after_cardinality_filter": 1 if len(ew) == 1 else 0,
                "candidates_after_stream_filter": len(ew_compute),
                "a6_rejection": rejection,
            }
        )
        per_case[comm] = {
            "conclusion": conclusion,
            "predicates": preds,
            "cann_connection_id": cid,
            "task_rows_total": len(subset),
            "event_wait_rows": len(ew),
            "a6_rejection": rejection,
        }

    write_csv(analysis_dir / "wait_gap_filter_trace.csv", filter_trace)

    unique_conclusions = set(conclusions)
    if len(unique_conclusions) == 1 and conclusions[0] in ("Q1", "Q2", "Q3"):
        global_conclusion = conclusions[0]
        acceptance_passed = True
    else:
        global_conclusion = "MIXED" if len(unique_conclusions) > 1 else conclusions[0]
        acceptance_passed = False

    next_dir = {
        "Q1": "fix_hook_or_counting_then_recapture",
        "Q2": "new_plan_a6_from_cann_preload_stream_without_event_wait_task",
        "Q3": "new_plan_fix_analyzer_predicate_and_recompute",
    }.get(global_conclusion, "replan_required")

    db_hash_after = sha256_file(db_path)
    input_hashes = {
        "db_sha256_before": db_hash_before,
        "db_sha256_after": db_hash_after,
        "db_unchanged": db_hash_before == db_hash_after,
    }

    bin_hashes = {}
    for p in sorted(trace_dir.glob("rank_*_pid_*.events.bin")):
        bin_hashes[p.name] = sha256_file(p)

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

    summary = {
        "analyzer_version": ANALYZER_VERSION,
        "run_id": args.run_id,
        "input_hashes": {**input_hashes, "bins": bin_hashes},
        "active_window": {"start_ns": active_start, "end_ns": active_end},
        "rank0_pid": rank0_pid,
        "target_comms": list(TARGET_COMMS),
        "a2_replay": a2_replay,
        "event_wait_type_id": wait_type,
        "compute_streams": sorted(compute_streams),
        "cases": per_case,
        "q1/q2/q3_predicates": {c: per_case[c]["predicates"] for c in per_case},
        "per_case_conclusion": {c: per_case[c]["conclusion"] for c in per_case},
        "global_conclusion": global_conclusion,
        "next_direction": next_dir,
        "acceptance_passed": acceptance_passed,
        "coverage_gap_cids": [
            r["cann_connection_id"]
            for r in coverage
            if r["event_wait_rows"] == 0 and r["task_rows_total"] == 0
        ],
        "event_wait_total_active": sum(r["event_wait_rows"] for r in coverage),
        "cann_wait_total_active": len(coverage),
    }
    (analysis_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    casebook = build_casebook(case_meta, preload_out, cann_out, task_rows, filter_trace)
    (analysis_dir / "wait_gap_casebook.md").write_text(casebook)

    (log_dir / "analysis.log").write_text(
        f"run_id={args.run_id}\n"
        f"global_conclusion={global_conclusion}\n"
        f"acceptance_passed={acceptance_passed}\n"
        f"a2_replay={json.dumps(a2_replay)}\n"
    )

    print(json.dumps(summary, indent=2))
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
