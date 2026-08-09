#!/usr/bin/env python3
"""安全终止：仅杀 /proc 环境含 exact RUN_MARKER 的进程。

协议：TERM → 有界 poll → 仍存则对仍带 exact marker 的 PID 发 KILL → 再 poll。
- 空 / 过短 marker → 拒绝（非 0）
- 只杀每个匹配进程；不依赖复用 PGID；PID 复用且 marker 变化 → 不杀
- 返回 0 仅当 exact marker 进程为 0；否则非 0 并记录 CLEANUP_INCOMPLETE
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path


def environ_has_marker(pid: int, marker: str) -> bool:
    env_path = Path(f"/proc/{pid}/environ")
    try:
        raw = env_path.read_bytes()
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return False
    needle = f"RUN_MARKER={marker}".encode()
    parts = raw.split(b"\0")
    return needle in parts


def read_seeds(out_dir: Path, node_id: int) -> set[int]:
    seeds: set[int] = set()
    for name in (f"node_{node_id}.pgid", f"node_{node_id}.pids"):
        path = out_dir / name
        if not path.exists():
            continue
        for tok in path.read_text(encoding="utf-8", errors="replace").split():
            try:
                seeds.add(int(tok))
            except ValueError:
                pass
    return seeds


def iter_marker_pids(marker: str) -> list[int]:
    found: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return found
    try:
        entries = list(proc.iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if environ_has_marker(pid, marker):
            found.append(pid)
    return found


def kill_pid(pid: int, sig: int) -> str:
    try:
        os.kill(pid, sig)
        return f"kill:{pid}:sig{sig}:ok"
    except ProcessLookupError:
        return f"kill:{pid}:sig{sig}:gone"
    except PermissionError as exc:
        return f"kill:{pid}:sig{sig}:perm:{exc}"


def poll_marker_gone(marker: str, timeout_s: float, interval_s: float = 0.05) -> list[int]:
    deadline = time.monotonic() + timeout_s
    remaining: list[int] = []
    while True:
        remaining = [p for p in iter_marker_pids(marker) if Path(f"/proc/{p}").exists()]
        if not remaining:
            return []
        if time.monotonic() >= deadline:
            return remaining
        time.sleep(interval_s)


def escalate_kill(
    marker: str,
    *,
    seeds: set[int],
    term_timeout_s: float,
    kill_timeout_s: float,
) -> tuple[int, list[str], str]:
    """TERM matching PIDs → poll → KILL remaining → poll. Return (rc, actions, status)."""
    actions: list[str] = []
    if not marker or len(marker) < 8:
        actions.append("reject:empty_or_short_marker")
        return 2, actions, "CLEANUP_REJECTED_MARKER"

    # Seed candidates: only act if they still carry the exact marker (PID reuse safe).
    marked = set(iter_marker_pids(marker))
    for seed in sorted(seeds):
        if seed in marked or environ_has_marker(seed, marker):
            actions.append(kill_pid(seed, int(signal.SIGTERM)))
        else:
            live = Path(f"/proc/{seed}").exists()
            actions.append(
                f"seed:{seed}:skip_{'reuse_or_foreign' if live else 'gone'}"
            )

    # Also TERM every currently marked process (leader may have disappeared).
    for pid in sorted(iter_marker_pids(marker)):
        if Path(f"/proc/{pid}").exists():
            actions.append(kill_pid(pid, int(signal.SIGTERM)))

    remaining = poll_marker_gone(marker, term_timeout_s)
    actions.append(f"after_term_remaining={remaining}")
    if not remaining:
        return 0, actions, "CLEANUP_OK"

    # Escalate: KILL only PIDs that still carry the exact marker.
    for pid in sorted(remaining):
        if environ_has_marker(pid, marker) and Path(f"/proc/{pid}").exists():
            actions.append(kill_pid(pid, int(signal.SIGKILL)))
        else:
            actions.append(f"kill_escalate:{pid}:skip_marker_changed_or_gone")

    remaining2 = poll_marker_gone(marker, kill_timeout_s)
    actions.append(f"after_kill_remaining={remaining2}")
    if remaining2:
        return 1, actions, "CLEANUP_INCOMPLETE"
    return 0, actions, "CLEANUP_OK"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--node-id", type=int, required=True)
    ap.add_argument("--run-marker", required=True)
    ap.add_argument("--term-timeout-s", type=float, default=2.0)
    ap.add_argument("--kill-timeout-s", type=float, default=2.0)
    ap.add_argument(
        "--signal",
        type=int,
        default=None,
        help="Legacy single-signal mode (discouraged); prefer escalate protocol.",
    )
    args = ap.parse_args()
    marker = args.run_marker
    if not marker or len(marker) < 8:
        print("CLEANUP_REJECTED_MARKER empty_or_short", file=sys.stderr)
        return 2

    seeds = read_seeds(args.out_dir, args.node_id)

    if args.signal is not None:
        # Backward-compatible single shot, still verify clearance.
        actions: list[str] = []
        for pid in sorted(set(iter_marker_pids(marker)) | {
            s for s in seeds if environ_has_marker(s, marker)
        }):
            actions.append(kill_pid(pid, int(args.signal)))
        remaining = poll_marker_gone(marker, args.term_timeout_s)
        status = "CLEANUP_OK" if not remaining else "CLEANUP_INCOMPLETE"
        print(
            f"killed_node {args.node_id} marker={marker} "
            f"seeds={sorted(seeds)} actions={actions} status={status} remaining={remaining}"
        )
        return 0 if status == "CLEANUP_OK" else 1

    rc, actions, status = escalate_kill(
        marker,
        seeds=seeds,
        term_timeout_s=float(args.term_timeout_s),
        kill_timeout_s=float(args.kill_timeout_s),
    )
    print(
        f"killed_node {args.node_id} marker={marker} "
        f"seeds={sorted(seeds)} actions={actions} status={status}"
    )
    if status == "CLEANUP_INCOMPLETE":
        print(f"CLEANUP_INCOMPLETE marker={marker} node={args.node_id}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
