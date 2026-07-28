#!/usr/bin/env python3
"""Pillar C v3 PR-2 B6: prune ``probing_data/<pid>/`` dirs for non-worker pids.

Reduces the E3 dynamic-arm dump size by dropping short-lived / non-worker pids
that ended up with a full ``python.comm_collective`` + CPU/GPU ring set
(``main_empty`` + ``extra_pid`` in the offline breakdown).

Inputs
------
- ``PROBING_DATA_DIR``  root of ``probing_data`` (``<root>/<pid>/*.memt``).
- ``WORKER_PIDS_FILE``  optional manifest of pids that must be kept
  (one pid per line).  Written by ``hold_exec_run_case.sh`` during the fire
  loop from ``_pillar_c_localize.py --list-worker-pids``.
- ``CULPRIT_PIDS``      optional comma-separated list of extra pids to keep
  (from SET_OK_WORKER).  Culprit pid is always retained.

Heuristics (in priority order):

1. Keep every pid listed in ``WORKER_PIDS_FILE`` / ``CULPRIT_PIDS``.
2. Keep pids whose directory contains ``python.torch_step_timing`` **or**
   ``python.torch_trace`` (data-driven fallback — post-P1 B6 lazy rings
   these files only appear for main worker ranks that actually saved rows).
3. Delete everything else (default).  Set ``PRUNE_DRY_RUN=1`` to only report
   what would be removed.

Never touches:
- ``crash/`` and any non-pid entry (``.txt``/``.log``).
- pids whose subdirectory contains ``python.torch_step_timing`` regardless
  of manifest (safety net for main workers on old code paths).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _read_worker_pids(path: Path | None) -> set[int]:
    if not path or not path.is_file():
        return set()
    pids: set[int] = set()
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            pids.add(int(line.split()[0]))
        except ValueError:
            continue
    return pids


def _parse_pid(name: str) -> int | None:
    try:
        return int(name)
    except ValueError:
        return None


def _has_worker_signature(pid_dir: Path) -> bool:
    """A pid is a main worker if it wrote either torch_step_timing or torch_trace."""
    for hint in ("python.torch_step_timing", "python.torch_trace"):
        candidate = pid_dir / hint
        if candidate.exists():
            return True
    return False


def main() -> int:
    root = os.environ.get("PROBING_DATA_DIR", "")
    if not root:
        print("prune_extra_pids: PROBING_DATA_DIR not set — skip", file=sys.stderr)
        return 0
    root_p = Path(root)
    if not root_p.is_dir():
        print(f"prune_extra_pids: {root} not a dir — skip", file=sys.stderr)
        return 0

    manifest_path = os.environ.get("WORKER_PIDS_FILE", "")
    worker_pids = _read_worker_pids(Path(manifest_path)) if manifest_path else set()
    culprit_raw = os.environ.get("CULPRIT_PIDS", "").strip()
    keep_extra: set[int] = set()
    if culprit_raw:
        for tok in culprit_raw.replace(",", " ").split():
            try:
                keep_extra.add(int(tok))
            except ValueError:
                pass

    dry = os.environ.get("PRUNE_DRY_RUN", "0").strip().lower() in ("1", "true", "yes", "on")

    kept: list[str] = []
    removed: list[str] = []
    ignored: list[str] = []

    for child in sorted(root_p.iterdir()):
        if not child.is_dir():
            ignored.append(child.name)
            continue
        pid = _parse_pid(child.name)
        if pid is None:
            # e.g. ``crash/`` — never prune.
            ignored.append(child.name)
            continue
        if pid in worker_pids or pid in keep_extra or _has_worker_signature(child):
            kept.append(child.name)
            continue
        # Prune.
        removed.append(child.name)
        if dry:
            continue
        try:
            shutil.rmtree(child)
        except OSError as exc:
            print(f"prune_extra_pids: failed to remove {child}: {exc}", file=sys.stderr)

    summary = {
        "root": str(root_p),
        "worker_pids": sorted(worker_pids),
        "culprit_keep": sorted(keep_extra),
        "kept": len(kept),
        "removed": len(removed),
        "ignored": len(ignored),
        "dry_run": dry,
    }
    print(
        "PRUNE_EXTRA_PIDS "
        + " ".join(f"{k}={v}" for k, v in summary.items())
    )
    if removed:
        print("removed=" + ",".join(removed))
    if kept:
        print("kept=" + ",".join(kept))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
