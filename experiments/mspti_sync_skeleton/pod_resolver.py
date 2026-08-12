#!/usr/bin/env python3
"""Resolve Volcano Job pods into immutable node-rank → pod map.

Contract (P0 REPLAN):
- JOB_NAME must match an existing Volcano Job with minAvailable=16,
  master replicas=1, worker replicas=15.
- Only pods owned by that job UID are considered.
- master-0 → NODE_RANK=0; worker-{0..14} → NODE_RANK=1..15 (numeric sort).
- Reject dict-order mistakes, duplicates, gaps, extra pods, non-Ready.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


class PodResolveError(Exception):
    """Deterministic pod map resolution failure."""


WORKER_RE = re.compile(r"^worker-(\d+)$")


@dataclass(frozen=True)
class PodEntry:
    node_rank: int
    pod: str
    pod_uid: str
    pod_ip: str
    node_name: str
    task: str
    task_index: int
    phase: str
    ready: bool


def _kubectl_json(args: list[str], *, kubeconfig: str, kubectl: str) -> Any:
    cmd = [kubectl, "--kubeconfig", kubeconfig, *args, "-o", "json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise PodResolveError(f"kubectl spawn failed: {exc}") from exc
    if proc.returncode != 0:
        raise PodResolveError(
            f"kubectl failed rc={proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise PodResolveError(f"kubectl json decode failed: {exc}") from exc


def _pod_ready(pod: dict[str, Any]) -> bool:
    for cond in pod.get("status", {}).get("conditions") or []:
        if cond.get("type") == "Ready" and cond.get("status") == "True":
            return True
    return False


def resolve_job_pods(
    *,
    job_name: str,
    namespace: str = "default",
    kubeconfig: str,
    kubectl: str = "kubectl",
    expected_nodes: int = 16,
) -> dict[str, Any]:
    """Return immutable pod_map dict for the given Volcano Job."""
    job = _kubectl_json(
        ["get", "vcjob", job_name, "-n", namespace],
        kubeconfig=kubeconfig,
        kubectl=kubectl,
    )
    job_uid = str(job.get("metadata", {}).get("uid") or "")
    if not job_uid:
        raise PodResolveError(f"missing job uid for {job_name}")

    spec = job.get("spec") or {}
    min_avail = int(spec.get("minAvailable") or 0)
    if min_avail != expected_nodes:
        raise PodResolveError(
            f"minAvailable={min_avail} expected={expected_nodes} for job {job_name}"
        )

    tasks = {t.get("name"): t for t in spec.get("tasks") or []}
    master_task = tasks.get("master") or {}
    worker_task = tasks.get("worker") or {}
    master_rep = int(master_task.get("replicas") or 0)
    worker_rep = int(worker_task.get("replicas") or 0)
    if master_rep != 1 or worker_rep != expected_nodes - 1:
        raise PodResolveError(
            f"task replicas master={master_rep} worker={worker_rep} "
            f"expected 1/{expected_nodes - 1}"
        )

    pods_json = _kubectl_json(
        ["get", "pods", "-n", namespace, "-l", f"volcano.sh/job-name={job_name}"],
        kubeconfig=kubeconfig,
        kubectl=kubectl,
    )
    items = pods_json.get("items") or []
    if len(items) != expected_nodes:
        raise PodResolveError(
            f"pod count {len(items)} != expected {expected_nodes} for job {job_name}"
        )

    by_rank: dict[int, PodEntry] = {}
    seen_pods: set[str] = set()
    seen_nodes: set[str] = set()

    for pod in items:
        meta = pod.get("metadata") or {}
        owner_uids = {
            str(o.get("uid"))
            for o in meta.get("ownerReferences") or []
            if o.get("uid")
        }
        if job_uid not in owner_uids:
            raise PodResolveError(
                f"pod {meta.get('name')} owner mismatch (not job uid {job_uid})"
            )
        name = str(meta.get("name") or "")
        if name in seen_pods:
            raise PodResolveError(f"duplicate pod name {name}")
        seen_pods.add(name)

        # task name from volcano label or pod name suffix
        task = ""
        task_index = -1
        if name.endswith("-master-0"):
            task = "master"
            task_index = 0
            node_rank = 0
        else:
            m = WORKER_RE.search(name.rsplit("-", 1)[-1] if "-" in name else "")
            # pod name pattern: <job>-worker-N
            suffix = name.split(f"{job_name}-", 1)[-1] if job_name in name else name
            wm = WORKER_RE.match(suffix)
            if not wm:
                raise PodResolveError(f"unrecognized pod name pattern: {name}")
            task = "worker"
            task_index = int(wm.group(1))
            node_rank = task_index + 1

        if node_rank in by_rank:
            raise PodResolveError(f"duplicate node_rank {node_rank} pods={name},{by_rank[node_rank].pod}")

        phase = str(pod.get("status", {}).get("phase") or "")
        ready = _pod_ready(pod)
        if phase != "Running" or not ready:
            raise PodResolveError(
                f"pod {name} not Running/Ready phase={phase} ready={ready}"
            )

        node_name = str(pod.get("spec", {}).get("nodeName") or "")
        if not node_name:
            raise PodResolveError(f"pod {name} missing nodeName")
        if node_name in seen_nodes:
            raise PodResolveError(f"duplicate nodeName {node_name} for pod {name}")
        seen_nodes.add(node_name)

        pod_ip = str(pod.get("status", {}).get("podIP") or "")
        entry = PodEntry(
            node_rank=node_rank,
            pod=name,
            pod_uid=str(meta.get("uid") or ""),
            pod_ip=pod_ip,
            node_name=node_name,
            task=task,
            task_index=task_index,
            phase=phase,
            ready=ready,
        )
        by_rank[node_rank] = entry

    expected_ranks = list(range(expected_nodes))
    missing = [r for r in expected_ranks if r not in by_rank]
    if missing:
        raise PodResolveError(f"missing node ranks {missing}")

    entries = [by_rank[r] for r in expected_ranks]
    pods = {e.node_rank: e.pod for e in entries}
    pod_map: dict[str, Any] = {
        "job_name": job_name,
        "job_uid": job_uid,
        "namespace": namespace,
        "expected_nodes": expected_nodes,
        "min_available": min_avail,
        "master_replicas": master_rep,
        "worker_replicas": worker_rep,
        "pods": [
            {
                "node_rank": e.node_rank,
                "pod": e.pod,
                "pod_uid": e.pod_uid,
                "pod_ip": e.pod_ip,
                "node_name": e.node_name,
                "task": e.task,
                "task_index": e.task_index,
                "phase": e.phase,
                "ready": e.ready,
            }
            for e in entries
        ],
        "pod_by_rank": {str(k): v for k, v in pods.items()},
    }
    body = json.dumps(pod_map, sort_keys=True, separators=(",", ":")).encode("utf-8")
    pod_map["pod_map_sha256"] = hashlib.sha256(body).hexdigest()
    return pod_map


def write_pod_map(pod_map: dict[str, Any], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(pod_map, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return path


def load_pod_map(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Resolve Volcano Job pods → pod_map.json")
    ap.add_argument("--job-name", required=True)
    ap.add_argument("--namespace", default="default")
    ap.add_argument("--kubeconfig", required=True)
    ap.add_argument("--kubectl", default="kubectl")
    ap.add_argument("--expected-nodes", type=int, default=16)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--query-tsv", action="store_true", help="Print rank\\tpod per line")
    ap.add_argument("--query-json", action="store_true", help="Print full pod_map JSON")
    args = ap.parse_args()
    try:
        pod_map = resolve_job_pods(
            job_name=args.job_name,
            namespace=args.namespace,
            kubeconfig=args.kubeconfig,
            kubectl=args.kubectl,
            expected_nodes=args.expected_nodes,
        )
    except PodResolveError as exc:
        print(f"POD_RESOLVE_FAIL {exc}", file=sys.stderr)
        return 2
    write_pod_map(pod_map, args.out)
    if args.query_tsv:
        for e in pod_map["pods"]:
            print(f"{e['node_rank']}\t{e['pod']}")
    elif args.query_json:
        print(json.dumps(pod_map, indent=2, sort_keys=True))
    else:
        print(f"POD_MAP_OK nodes={args.expected_nodes} sha256={pod_map['pod_map_sha256'][:16]}")
        print(f"POD_MAP_PATH={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
