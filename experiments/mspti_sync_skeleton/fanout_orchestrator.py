#!/usr/bin/env python3
"""Fanout launch orchestration with atomic partial-failure cleanup.

Used by local fixtures (no SSH) and documents the contract that
launch_megatron_ab.sh / launch_grj.sh must honor under set -e:

- record each node launcher handle + RUN_MARKER
- wait each node individually (never bare `wait` under set -e)
- on any launch/SSH/kubectl failure: kill exact marker on started pods,
  stop all nodes for this attempt, best-effort pull, atomic GROUP_INVALID,
  do not start subsequent attempts, return non-zero
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class NodeLaunch:
    node_rank: int
    pod: str
    handle: Any
    marker: str
    started: bool = False


@dataclass
class AttemptResult:
    attempt_id: str
    ok: bool
    evidence_status: str = "OK"
    killed_nodes: list[int] = field(default_factory=list)
    group_invalid: bool = False
    reason: str = ""
    launch_rcs: dict[int, int] = field(default_factory=dict)


LaunchFn = Callable[[dict[str, Any], int], tuple[Any, int]]
# launch_fn(attempt, node_rank) -> (handle, immediate_rc)
# immediate_rc != 0 means emit/SSH/kubectl failed before remote work started.
WaitFn = Callable[[Any], int]
KillFn = Callable[[dict[str, Any], int, str], None]
PullFn = Callable[[dict[str, Any], str], str]  # returns evidence status


def mark_group_invalid(
    group_dir: Path,
    *,
    group_id: str,
    reason: str,
    attempt_id: str = "",
    stage: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> Path:
    """Atomically write GROUP_INVALID; idempotent — keep first root cause.

    Subsequent calls append cleanup/status notes without overwriting the original
    reason/attempt/stage. Safe under concurrent best-effort cleanup paths.
    """
    group_dir.mkdir(parents=True, exist_ok=True)
    final = group_dir / "GROUP_INVALID.json"
    existing: dict[str, Any] = {}
    if final.exists():
        try:
            existing = json.loads(final.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    if existing.get("status") == "GROUP_INVALID" and existing.get("reason"):
        # Idempotent: preserve first root cause; append follow-up notes.
        notes = list(existing.get("subsequent_notes") or [])
        note: dict[str, Any] = {
            "reason": reason,
            "attempt_id": attempt_id,
            "stage": stage,
        }
        if extra:
            note["extra"] = extra
        notes.append(note)
        existing["subsequent_notes"] = notes
        if extra and extra.get("cleanup_status"):
            existing["cleanup_status"] = extra["cleanup_status"]
        payload = existing
    else:
        payload = {
            "group_id": group_id,
            "status": "GROUP_INVALID",
            "reason": reason,
            "attempt_id": attempt_id,
            "stage": stage,
            "evidence_status": "EVIDENCE_INCOMPLETE",
            "subsequent_notes": [],
        }
        if extra:
            payload["extra"] = extra
            if extra.get("cleanup_status"):
                payload["cleanup_status"] = extra["cleanup_status"]

    tmp = group_dir / "GROUP_INVALID.json.tmp"
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(final)

    cfg_path = group_dir / "group_config.json"
    cfg: dict[str, Any] = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    # Keep first invalid_reason if already set.
    if cfg.get("status") != "GROUP_INVALID" or not cfg.get("invalid_reason"):
        cfg["invalid_reason"] = reason
        cfg["invalid_attempt_id"] = attempt_id
        cfg["invalid_stage"] = stage
    cfg["status"] = "GROUP_INVALID"
    cfg_tmp = group_dir / "group_config.json.tmp"
    cfg_tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")
    cfg_tmp.replace(cfg_path)
    return final


def write_group_invalid(
    group_dir: Path,
    *,
    group_id: str,
    reason: str,
    attempt_id: str,
    stage: str = "fanout",
    extra: Optional[dict[str, Any]] = None,
) -> Path:
    """Backward-compatible alias for mark_group_invalid."""
    return mark_group_invalid(
        group_dir,
        group_id=group_id,
        reason=reason,
        attempt_id=attempt_id,
        stage=stage,
        extra=extra,
    )


def fanout_launch_attempt(
    attempt: dict[str, Any],
    *,
    group_id: str,
    group_dir: Path,
    node_ranks: list[int],
    pods: dict[int, str],
    launch_fn: LaunchFn,
    wait_fn: WaitFn,
    kill_fn: KillFn,
    pull_fn: PullFn,
) -> AttemptResult:
    """Launch all nodes for one attempt; atomic cleanup on partial failure."""
    attempt_id = str(attempt["attempt_id"])
    marker = str(attempt["run_marker"])
    nodes: list[NodeLaunch] = []
    result = AttemptResult(attempt_id=attempt_id, ok=True)

    for nr in node_ranks:
        try:
            handle, imm_rc = launch_fn(attempt, nr)
        except Exception as exc:  # noqa: BLE001
            result.ok = False
            result.reason = f"launch_exception node={nr}: {exc}"
            imm_rc = 99
            handle = None
        else:
            if imm_rc != 0:
                result.ok = False
                result.reason = f"launch_emit_fail node={nr} rc={imm_rc}"
        nl = NodeLaunch(
            node_rank=nr,
            pod=pods[nr],
            handle=handle,
            marker=marker,
            started=handle is not None and imm_rc == 0,
        )
        nodes.append(nl)
        if not result.ok:
            break

    # Wait every started node individually (collect rc; do not bare-wait under set -e).
    for nl in nodes:
        if not nl.started or nl.handle is None:
            result.launch_rcs[nl.node_rank] = result.launch_rcs.get(nl.node_rank, 98)
            continue
        try:
            rc = int(wait_fn(nl.handle))
        except Exception as exc:  # noqa: BLE001
            rc = 97
            if result.ok:
                result.ok = False
                result.reason = f"wait_exception node={nl.node_rank}: {exc}"
        result.launch_rcs[nl.node_rank] = rc
        if rc != 0 and result.ok:
            result.ok = False
            result.reason = f"wait_fail node={nl.node_rank} rc={rc}"

    if result.ok:
        return result

    # Atomic cleanup: kill exact marker on all started pods for this attempt.
    for nl in nodes:
        if nl.started:
            try:
                kill_fn(attempt, nl.node_rank, marker)
                result.killed_nodes.append(nl.node_rank)
            except Exception:
                pass

    status = "EVIDENCE_INCOMPLETE"
    try:
        status = pull_fn(attempt, "best_effort") or "EVIDENCE_INCOMPLETE"
    except Exception:
        status = "EVIDENCE_INCOMPLETE"
    result.evidence_status = status
    write_group_invalid(
        group_dir,
        group_id=group_id,
        reason=result.reason or "fanout_partial_failure",
        attempt_id=attempt_id,
    )
    result.group_invalid = True
    return result


def run_plan_attempts(
    plan: dict[str, Any],
    *,
    group_dir: Path,
    node_ranks: list[int],
    pods: dict[int, str],
    launch_fn: LaunchFn,
    wait_fn: WaitFn,
    kill_fn: KillFn,
    pull_fn: PullFn,
) -> list[AttemptResult]:
    """Consume immutable plan attempts in order; stop after first invalid."""
    results: list[AttemptResult] = []
    group_id = str(plan["group_id"])
    for attempt in plan["attempts"]:
        # If group already invalid, do not start subsequent attempts.
        if (group_dir / "GROUP_INVALID.json").exists():
            break
        res = fanout_launch_attempt(
            attempt,
            group_id=group_id,
            group_dir=group_dir,
            node_ranks=node_ranks,
            pods=pods,
            launch_fn=launch_fn,
            wait_fn=wait_fn,
            kill_fn=kill_fn,
            pull_fn=pull_fn,
        )
        results.append(res)
        if not res.ok:
            break
    return results


def simulate_worker_launch_failure_fixture(group_dir: Path) -> dict[str, Any]:
    """Local no-side-effect fixture: worker emit fails, master still 'alive'."""
    from ab_plan import DEFAULT_ATTEMPTS, build_ab_plan, write_plan

    plan = build_ab_plan(
        group_id="fixture_fanout_partial",
        code_dir="/tmp/code",
        code_hash="deadbeefdeadbeef",
        attempts=list(DEFAULT_ATTEMPTS),
        nnodes=2,
        nproc=1,
        world_size=2,
        expected_ranks=2,
        train_iters=2,
        capture_iter=1,
        base_port=39000,
        group_dir=str(group_dir),
        min_raw_kernels=80,
        min_comm=20,
        rel_raw_floor=0.8,
        rel_comm_floor=0.8,
    )
    write_plan(plan, group_dir / "group_plan.json")
    (group_dir / "group_config.json").write_text(
        json.dumps({"group_id": plan["group_id"], "status": "running"}, indent=2),
        encoding="utf-8",
    )

    killed: list[tuple[str, int, str]] = []
    launched_attempts: list[str] = []

    def launch_fn(attempt: dict[str, Any], node_rank: int) -> tuple[Any, int]:
        launched_attempts.append(f"{attempt['attempt_id']}:node{node_rank}")
        if node_rank == 0:
            return ("master-handle", 0)  # master emit OK / still alive
        return (None, 42)  # worker launch/SSH/kubectl fail

    def wait_fn(handle: Any) -> int:
        return 0 if handle == "master-handle" else 42

    def kill_fn(attempt: dict[str, Any], node_rank: int, marker: str) -> None:
        killed.append((attempt["attempt_id"], node_rank, marker))

    def pull_fn(attempt: dict[str, Any], mode: str) -> str:
        return "EVIDENCE_INCOMPLETE"

    results = run_plan_attempts(
        plan,
        group_dir=group_dir,
        node_ranks=[0, 1],
        pods={0: "master", 1: "worker"},
        launch_fn=launch_fn,
        wait_fn=wait_fn,
        kill_fn=kill_fn,
        pull_fn=pull_fn,
    )
    return {
        "results": [
            {
                "attempt_id": r.attempt_id,
                "ok": r.ok,
                "group_invalid": r.group_invalid,
                "killed_nodes": r.killed_nodes,
                "evidence_status": r.evidence_status,
                "reason": r.reason,
            }
            for r in results
        ],
        "killed": killed,
        "launched_attempts": launched_attempts,
        "group_invalid_exists": (group_dir / "GROUP_INVALID.json").exists(),
        "subsequent_skipped": len(results) == 1 and not results[0].ok,
    }


# Stages that must atomic-INVALID the group (local fixture enumeration).
FAILURE_STAGES = (
    "config",
    "provenance",
    "build",
    "fanout",
    "attempt_exit",
    "convert",
    "pull",
    "local_anchor",
    "strict",
    "analyzer",
)


def simulate_stage_failure_fixture(group_dir: Path, stage: str) -> dict[str, Any]:
    """Simulate early failure at `stage`; assert GROUP_INVALID and no subsequent launch."""
    from ab_plan import DEFAULT_ATTEMPTS, build_ab_plan, write_plan

    if stage not in FAILURE_STAGES:
        raise ValueError(f"unknown stage {stage}")

    plan = build_ab_plan(
        group_id=f"fixture_stage_{stage}",
        code_dir="/tmp/code",
        code_hash="deadbeefdeadbeef",
        attempts=list(DEFAULT_ATTEMPTS),
        nnodes=1,
        nproc=1,
        world_size=1,
        expected_ranks=1,
        train_iters=2,
        capture_iter=1,
        base_port=40000,
        group_dir=str(group_dir),
        min_raw_kernels=80,
        min_comm=20,
        rel_raw_floor=0.8,
        rel_comm_floor=0.8,
    )
    write_plan(plan, group_dir / "group_plan.json")
    (group_dir / "group_config.json").write_text(
        json.dumps({"group_id": plan["group_id"], "status": "running"}, indent=2),
        encoding="utf-8",
    )

    launched: list[str] = []

    # Pre-attempt stages: invalidate before any attempt starts.
    if stage in ("config", "provenance", "build"):
        mark_group_invalid(
            group_dir,
            group_id=plan["group_id"],
            reason=f"simulated_{stage}_fail",
            attempt_id="",
            stage=stage,
        )
        # Attempt to "start" subsequent — must refuse.
        subsequent_started = False
        if not (group_dir / "GROUP_INVALID.json").exists():
            subsequent_started = True
            launched.append(plan["attempts"][0]["attempt_id"])
        return {
            "stage": stage,
            "group_invalid": True,
            "invalid_reason": json.loads(
                (group_dir / "GROUP_INVALID.json").read_text(encoding="utf-8")
            ).get("reason"),
            "launched": launched,
            "subsequent_started": subsequent_started,
        }

    def launch_fn(attempt: dict[str, Any], node_rank: int) -> tuple[Any, int]:
        launched.append(f"{attempt['attempt_id']}:node{node_rank}")
        if stage == "fanout":
            return (None, 42)
        return ("h", 0)

    def wait_fn(handle: Any) -> int:
        if stage == "attempt_exit":
            return 7
        return 0 if handle == "h" else 42

    def kill_fn(attempt: dict[str, Any], node_rank: int, marker: str) -> None:
        return None

    def pull_fn(attempt: dict[str, Any], mode: str) -> str:
        if stage == "pull":
            mark_group_invalid(
                group_dir,
                group_id=plan["group_id"],
                reason="simulated_pull_fail",
                attempt_id=str(attempt["attempt_id"]),
                stage="pull",
            )
            return "EVIDENCE_INCOMPLETE"
        return "OK"

    # Post-attempt stages: only "run" first attempt then INVALID; refuse subsequent.
    if stage in ("pull", "convert", "local_anchor", "strict", "analyzer"):
        first = plan["attempts"][0]
        launched.append(f"{first['attempt_id']}:node0")
        mark_group_invalid(
            group_dir,
            group_id=plan["group_id"],
            reason=f"simulated_{stage}_fail",
            attempt_id=str(first["attempt_id"]),
            stage=stage,
        )
        subsequent_started = False
        inv = json.loads((group_dir / "GROUP_INVALID.json").read_text(encoding="utf-8"))
        return {
            "stage": stage,
            "group_invalid": True,
            "invalid_reason": inv.get("reason"),
            "invalid_stage": inv.get("stage"),
            "launched": launched,
            "results_n": 1,
            "subsequent_started": subsequent_started,
            "first_attempt_only": True,
        }

    results = run_plan_attempts(
        plan,
        group_dir=group_dir,
        node_ranks=[0],
        pods={0: "master"},
        launch_fn=launch_fn,
        wait_fn=wait_fn,
        kill_fn=kill_fn,
        pull_fn=pull_fn,
    )

    inv = json.loads((group_dir / "GROUP_INVALID.json").read_text(encoding="utf-8"))
    return {
        "stage": stage,
        "group_invalid": True,
        "invalid_reason": inv.get("reason"),
        "invalid_stage": inv.get("stage"),
        "launched": launched,
        "results_n": len(results),
        "subsequent_started": len(results) > 1,
        "first_attempt_only": len(results) <= 1,
    }
