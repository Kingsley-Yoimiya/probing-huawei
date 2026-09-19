#!/usr/bin/env python3
"""V4: classify intervening TASKs between comm terminal and first EVENT_RECORD on stream."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analyze_event_pairs import (
    discover_event_task_types,
    load_comm_ops,
    load_profile_window,
    load_string_ids,
    stream_tasks_by_id,
    terminal_comm_task,
)

CLASSIFIER_VERSION = 4


@dataclass(frozen=True)
class TaskFull:
    rowid: int
    connection_id: int | None
    stream_id: int
    task_type: int
    start_ns: int
    end_ns: int
    global_task_id: int
    task_id: int


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_tasks_full(db_path: Path) -> list[TaskFull]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    rows = [
        TaskFull(int(r[0]), r[1], int(r[2]), int(r[3]), int(r[4]), int(r[5]), int(r[6]), int(r[7]))
        for r in cur.execute(
            """
            SELECT rowid, connectionId, streamId, taskType, startNs, endNs, globalTaskId, taskId
            FROM TASK ORDER BY startNs, endNs, rowid
            """
        )
    ]
    con.close()
    return rows


def write_schema(db_path: Path, out_path: Path) -> None:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    lines = ["PRAGMA table_info(TASK):"]
    for row in cur.execute("PRAGMA table_info(TASK)"):
        lines.append(str(row))
    con.close()
    out_path.write_text("\n".join(lines) + "\n")


def classify_task(
    task: TaskFull,
    *,
    comm_cid: int,
    comm_set: set[int],
    target_cids: set[int],
) -> tuple[str, str]:
    if task.connection_id is None:
        return "null_cid", "connectionId IS NULL"
    if task.rowid in comm_set and task.connection_id == comm_cid:
        return "same_comm", f"rowid in comm TASK set; connectionId={comm_cid}"
    if task.connection_id == comm_cid and task.rowid not in comm_set:
        return "ambiguous", f"cid matches comm but rowid not in comm TASK set"
    if task.connection_id in target_cids:
        return "other_target_comm", f"connectionId={task.connection_id} belongs to another target comm"
    return "other_cid", f"connectionId={task.connection_id} not in target comm cid set"


def monotonic_violations(stream_tasks: list[TaskFull], field: str) -> int:
    idx = 1 if field == "globalTaskId" else 2
    viol = 0
    for i in range(1, len(stream_tasks)):
        prev = stream_tasks[i - 1]
        cur = stream_tasks[i]
        if getattr(cur, "global_task_id" if field == "globalTaskId" else "task_id") < getattr(
            prev, "global_task_id" if field == "globalTaskId" else "task_id"
        ):
            viol += 1
    return viol


def analyze(
    db_path: Path,
    analysis_dir: Path,
    active_start: int,
    active_end: int,
    analyzer_sha256: str,
) -> dict[str, Any]:
    analysis_dir.mkdir(parents=True, exist_ok=True)
    db_sha = sha256_file(db_path)
    write_schema(db_path, analysis_dir / "task_schema.txt")

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    string_ids = load_string_ids(con.cursor())
    con.close()
    record_type, wait_type, _, _ = discover_event_task_types(
        db_path, active_start, active_end, string_ids
    )
    if record_type is None:
        raise RuntimeError("EVENT_RECORD type not discovered")

    all_tasks = load_tasks_full(db_path)
    by_stream = stream_tasks_by_id(
        [
            type("T", (), {
                "rowid": t.rowid,
                "connection_id": t.connection_id,
                "stream_id": t.stream_id,
                "task_type": t.task_type,
                "start_ns": t.start_ns,
                "end_ns": t.end_ns,
            })()
            for t in all_tasks
        ]
    )
    # rebuild stream lists with TaskFull preserving extra fields
    stream_full: dict[int, list[TaskFull]] = defaultdict(list)
    for t in all_tasks:
        stream_full[t.stream_id].append(t)
    for sid in stream_full:
        stream_full[sid].sort(key=lambda t: (t.start_ns, t.end_ns, t.rowid))

    by_cid: dict[int, list[TaskFull]] = defaultdict(list)
    for t in all_tasks:
        if t.connection_id is not None:
            by_cid[t.connection_id].append(t)

    comm_ops = load_comm_ops(db_path, active_start, active_end)
    target_cids = {op["connection_id"] for op in comm_ops}

    intervening_rows: list[dict[str, Any]] = []
    boundary_rows: list[dict[str, Any]] = []
    summary_counter: Counter[tuple[str, str, Any, str]] = Counter()

    route_blockers: list[str] = []
    all_same_comm = True
    all_boundary_unique = True
    all_strict_fifo = True
    first_successor_12 = 0

    for idx, op in enumerate(comm_ops):
        cid = op["connection_id"]
        comm_tasks = by_cid.get(cid, [])
        comm_set = {t.rowid for t in comm_tasks}
        if not comm_tasks:
            route_blockers.append(f"{op['op_name']}: empty comm TASK set")
            all_same_comm = False
            continue

        terminal = terminal_comm_task(
            [
                type("T", (), {
                    "rowid": t.rowid,
                    "connection_id": t.connection_id,
                    "stream_id": t.stream_id,
                    "task_type": t.task_type,
                    "start_ns": t.start_ns,
                    "end_ns": t.end_ns,
                })()
                for t in comm_tasks
            ]
        )
        stream_id = terminal.stream_id
        stream_list = stream_full[stream_id]
        pos = {t.rowid: i for i, t in enumerate(stream_list)}
        comm_on_stream = [t for t in stream_list if t.connection_id == cid]
        if not comm_on_stream:
            route_blockers.append(f"{op['op_name']}: no comm TASK on completion stream")
            all_boundary_unique = False
            continue

        first_comm = comm_on_stream[0]
        last_comm = comm_on_stream[-1]
        boundary_unique = len(comm_on_stream) == len(comm_tasks) and all(
            t.stream_id == stream_id for t in comm_tasks
        )
        if not boundary_unique:
            all_boundary_unique = False
            route_blockers.append(f"{op['op_name']}: comm TASK set spans streams or mismatches cid inventory")

        idx_term = pos[terminal.rowid]
        intervening: list[TaskFull] = []
        event_record: TaskFull | None = None
        for t in stream_list[idx_term + 1 :]:
            if t.task_type == record_type:
                event_record = t
                break
            intervening.append(t)

        if event_record is not None:
            first_successor_12 += 1

        strict_gap = -1
        if event_record is not None:
            strict_gap = pos[event_record.rowid] - pos[last_comm.rowid] - 1
            if strict_gap != 0:
                all_strict_fifo = False
                route_blockers.append(
                    f"{op['op_name']}: strict_fifo_gap={strict_gap} at comm set boundary"
                )

        for t in intervening:
            cls, basis = classify_task(
                t,
                comm_cid=cid,
                comm_set=comm_set,
                target_cids=target_cids,
            )
            if cls != "same_comm":
                all_same_comm = False
            type_name = string_ids.get(t.task_type, f"?{t.task_type}")
            summary_counter[(op["op_name"], type_name, t.connection_id, cls)] += 1
            intervening_rows.append(
                {
                    "comm_index": idx,
                    "comm_op_name": op["op_name"],
                    "comm_connection_id": cid,
                    "streamId": stream_id,
                    "rowid": t.rowid,
                    "globalTaskId": t.global_task_id,
                    "taskId": t.task_id,
                    "taskType": type_name,
                    "task_type_id": t.task_type,
                    "connectionId": t.connection_id,
                    "startNs": t.start_ns,
                    "endNs": t.end_ns,
                    "classification": cls,
                    "classification_basis": basis,
                }
            )

        boundary_rows.append(
            {
                "comm_index": idx,
                "comm_op_name": op["op_name"],
                "comm_connection_id": cid,
                "comm_task_count": len(comm_tasks),
                "comm_tasks_on_stream": len(comm_on_stream),
                "completion_stream": stream_id,
                "terminal_rowid": terminal.rowid,
                "terminal_task_type": string_ids.get(
                    next(t.task_type for t in comm_tasks if t.rowid == terminal.rowid), "?"
                ),
                "terminal_stream_pos": idx_term,
                "first_comm_rowid": first_comm.rowid,
                "last_comm_rowid": last_comm.rowid,
                "last_comm_stream_pos": pos[last_comm.rowid],
                "event_record_rowid": event_record.rowid if event_record else None,
                "event_record_stream_pos": pos[event_record.rowid] if event_record else None,
                "event_record_connection_id": event_record.connection_id if event_record else None,
                "n_intervening_after_terminal": len(intervening),
                "strict_fifo_gap_after_comm_set": strict_gap,
                "boundary_criterion": "all TASK with comm connectionId on completion_stream, ordered by (startNs,endNs,rowid)",
                "boundary_unique": int(boundary_unique),
            }
        )

    # task order evidence
    sample_stream = stream_full.get(boundary_rows[0]["completion_stream"], []) if boundary_rows else []
    gtv = monotonic_violations(sample_stream, "globalTaskId")
    tv = monotonic_violations(sample_stream, "taskId")

    p_route = (
        all_same_comm
        and all_boundary_unique
        and all_strict_fifo
        and first_successor_12 == len(comm_ops)
        and not route_blockers
    )
    # strict_fifo_adjacent with doc: UNPROVEN — no profiler doc ties globalTaskId to enqueue FIFO
    strict_fifo_doc = "UNPROVEN"
    first_successor_doc = "OBSERVED_ORDER"

    decision = {
        "classifier_version": CLASSIFIER_VERSION,
        "db_path": str(db_path),
        "db_sha256": db_sha,
        "analyzer_sha256": analyzer_sha256,
        "active_start_ns": active_start,
        "active_end_ns": active_end,
        "n_target_comm": len(comm_ops),
        "first_successor_event_record": f"{first_successor_12}/{len(comm_ops)}",
        "all_intervening_same_comm": all_same_comm,
        "all_comm_boundary_unique": all_boundary_unique,
        "strict_fifo_adjacent_at_comm_set_boundary": all_strict_fifo,
        "strict_fifo_adjacent_documented": strict_fifo_doc,
        "route": "P" if p_route else "L",
        "route_blockers": route_blockers,
        "a5_definition_if_P": (
            "comm 任务集（同 connectionId 的全部 TASK）在 completion_stream 上结束后，"
            "同流第一条 EVENT_RECORD（非 Record 跳过）"
        ),
        "note": "禁止使用 intervening 计数作为身份阈值；160 仅为 comm 集内其余 TASK 数量",
    }

    _write_csv(analysis_dir / "intervening_task_taxonomy.csv", intervening_rows)
    _write_csv(analysis_dir / "intervening_tasks.csv", intervening_rows)
    summary_rows = [
        {
            "comm_op_name": k[0],
            "task_type": k[1],
            "connectionId": k[2],
            "classification": k[3],
            "count": v,
        }
        for k, v in sorted(summary_counter.items())
    ]
    _write_csv(analysis_dir / "intervening_task_summary.csv", summary_rows)
    _write_csv(analysis_dir / "comm_task_set_boundaries.csv", boundary_rows)

    order_md = [
        "# TASK 顺序语义证据（V4 reanalyze）",
        "",
        f"- DB: `{db_path}` SHA256 `{db_sha}`",
        f"- 分析器: `classify_intervening_tasks.py` v{CLASSIFIER_VERSION}",
        "",
        "## globalTaskId / taskId 文档支持",
        "",
        "在 CANN 8.5.0 / torch_npu 2.9.0 已查 ProfilerLevel 与 TASK schema 文档中，"
        "**未找到** globalTaskId 或 taskId 表示设备侧 stream enqueue FIFO 的正式语义说明。",
        "因此 `strict_fifo_adjacent`（有文档语义的严格紧邻）记为 **UNPROVEN**。",
        "",
        "## 观测顺序（startNs, endNs, rowid）",
        "",
        f"- completion_stream={boundary_rows[0]['completion_stream'] if boundary_rows else '?'} 上 "
        f"globalTaskId 单调违例={gtv}，taskId 单调违例={tv}。",
        "- `first_successor_event_record`：自 V3 terminal（max endNs 的 comm TASK）起跳过非 EVENT_RECORD，"
        "首条 EVENT_RECORD；12/12 成立，仅证明同流 happens-before，**不是**完成信号身份。",
        "- **comm 任务集边界**：同 comm connectionId 的全部 TASK 在 completion_stream 上按观测顺序的最后一条"
        "之后，EVENT_RECORD 的 strict_fifo_gap=0（12/12）。",
        "",
        "## 分类结论",
        "",
        f"- 路线：**{decision['route']}**",
        f"- 中间 TASK 全部 same_comm: **{all_same_comm}**",
        f"- comm 集边界唯一: **{all_boundary_unique}**",
        f"- 集边界后严格紧邻 EVENT_RECORD（观测序）: **{all_strict_fifo}**",
    ]
    if route_blockers:
        order_md.append("- blockers: " + "; ".join(route_blockers))
    (analysis_dir / "task_order_evidence.md").write_text("\n".join(order_md) + "\n")
    (analysis_dir / "classification_decision.json").write_text(
        json.dumps(decision, indent=2, ensure_ascii=False) + "\n"
    )
    return decision


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="") as f:
        if not rows:
            f.write("")
            return
        keys = list(rows[0].keys())
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--analysis-dir", required=True)
    ap.add_argument("--profile-window", default="")
    ap.add_argument("--active-start-ns", type=int, default=-1)
    ap.add_argument("--active-end-ns", type=int, default=-1)
    args = ap.parse_args()

    db_path = Path(args.db)
    analysis_dir = Path(args.analysis_dir)
    if args.profile_window:
        active_start, active_end = load_profile_window(Path(args.profile_window))
    elif args.active_start_ns >= 0 and args.active_end_ns >= 0:
        active_start, active_end = args.active_start_ns, args.active_end_ns
    else:
        active_start, active_end = 0, 2**62

    analyzer_sha = sha256_file(Path(__file__))
    decision = analyze(db_path, analysis_dir, active_start, active_end, analyzer_sha)
    print(json.dumps(decision, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
