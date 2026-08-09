#!/usr/bin/env python3
"""Local group atomic claim + fail-closed yield classification.

Contract (final-gate P1, review 9511c47f):
1) Opponent / idle checks: CLEAR=0 | OPPONENT=10 | CHECK_FAILED=20.
   Empty / unknown / transport rc≠0 → CHECK_FAILED (never CLEAR).
2) Local group dirs: atomic exclusive claim first (O_CREAT|O_EXCL), then
   single-level mkdir (no ``mkdir -p``) for BACKUP_ROOT and LOG_DIR.
   Never check-then-mkdir-p. Reject paths must not write old trees / GROUP_INVALID.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Align with opponent_check.py protocol.
try:
    from opponent_check import (  # type: ignore
        RC_CHECK_FAILED as RC_YIELD_CHECK_FAILED,
        RC_CLEAR as RC_YIELD_CLEAR,
        RC_OPPONENT as RC_YIELD_OPPONENT,
        STATUS_CHECK_FAILED as YIELD_CHECK_FAILED,
        STATUS_CLEAR as YIELD_CLEAR,
        STATUS_OPPONENT as YIELD_OPPONENT,
        classify_transport_stdout,
    )
except ImportError:  # pragma: no cover — same-dir import for bash cwd
    RC_YIELD_CLEAR = 0
    RC_YIELD_OPPONENT = 10
    RC_YIELD_CHECK_FAILED = 20
    YIELD_CLEAR = "CLEAR"
    YIELD_OPPONENT = "OPPONENT"
    YIELD_CHECK_FAILED = "CHECK_FAILED"

    def classify_transport_stdout(stdout: str, check_rc: int):  # type: ignore
        if check_rc not in (
            RC_YIELD_CLEAR,
            RC_YIELD_OPPONENT,
            RC_YIELD_CHECK_FAILED,
        ):
            return YIELD_CHECK_FAILED, RC_YIELD_CHECK_FAILED, f"transport_rc={check_rc}"
        text = (stdout or "").strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        last = lines[-1] if lines else ""
        if not last:
            return YIELD_CHECK_FAILED, RC_YIELD_CHECK_FAILED, "empty_stdout"
        if check_rc == RC_YIELD_CHECK_FAILED:
            return YIELD_CHECK_FAILED, RC_YIELD_CHECK_FAILED, last[:500]
        if check_rc == RC_YIELD_OPPONENT:
            if last.startswith(("OPPONENT", "OPP|", "OPP")):
                return YIELD_OPPONENT, RC_YIELD_OPPONENT, last[:500]
            return (
                YIELD_CHECK_FAILED,
                RC_YIELD_CHECK_FAILED,
                f"opponent_rc_mismatch={last[:200]}",
            )
        if last in ("STARTUP", YIELD_CLEAR, "OK"):
            return YIELD_CLEAR, RC_YIELD_CLEAR, last
        if last.startswith(("OPPONENT", "OPP|", "OPP")):
            return YIELD_OPPONENT, RC_YIELD_OPPONENT, last[:500]
        if last.startswith("CHECK_FAILED"):
            return YIELD_CHECK_FAILED, RC_YIELD_CHECK_FAILED, last[:500]
        return YIELD_CHECK_FAILED, RC_YIELD_CHECK_FAILED, f"unexpected={last[:200]}"


class LocalGroupExistsError(RuntimeError):
    """Raised when the local group/backup directory already exists."""


class LocalGroupClaimError(RuntimeError):
    """Atomic claim failed or aborted after partial success."""

    def __init__(self, message: str, *, rc: int = 19, claim_path: str = ""):
        super().__init__(message)
        self.rc = rc
        self.claim_path = claim_path


RC_CLAIM_OK = 0
RC_CLAIM_TAKEN = 19
RC_CLAIM_ABORTED = 21

SENTINEL_NAMES = (
    "GROUP_COMPLETE",
    "GROUP_COMPLETE.json",
    "GROUP_INVALID.json",
    "group_plan.json",
    "group_config.json",
    "dry_run_plan.json",
)


def local_group_exists(path: Path) -> bool:
    return path.exists()


def assert_local_group_absent(path: Path | str, *, label: str = "local group dir") -> Path:
    """Refuse reuse: any existing path (incl. empty dir) → error; do not create/rm."""
    p = Path(path)
    if p.exists():
        kind = "dir" if p.is_dir() else "file"
        extra = ""
        if p.is_dir():
            kids = sorted(x.name for x in p.iterdir())[:12]
            extra = f" entries={kids}" if kids else " (empty)"
        raise LocalGroupExistsError(
            f"REFUSE_LOCAL_GROUP_REUSE: {label} already exists ({kind}): {p}{extra}"
        )
    return p


def refuse_or_exit(path: Path | str, *, label: str = "local group dir") -> int:
    try:
        assert_local_group_absent(path, label=label)
    except LocalGroupExistsError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return RC_CLAIM_TAKEN
    print(f"LOCAL_GROUP_ABSENT ok path={path}")
    return 0


def _atomic_claim_file(claim_path: Path, payload: str) -> None:
    """Occupy group id via O_CREAT|O_EXCL. Raises FileExistsError if taken."""
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(str(claim_path), flags, 0o644)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)


def _atomic_mkdir_one(path: Path) -> None:
    """Single-level mkdir without parents=True (-p). Fails if exists or parent missing."""
    os.mkdir(str(path))


def claim_local_group(
    *,
    group_id: str,
    claim_parent: Path | str,
    backup_root: Path | str,
    log_dir: Path | str,
    pid: Optional[int] = None,
) -> dict[str, Any]:
    """Atomic exclusive claim then create BACKUP_ROOT + LOG_DIR.

    Order:
      1) O_CREAT|O_EXCL claim file under claim_parent / group_id.claim
      2) os.mkdir(backup_root) — no -p; existing → abort (keep claim)
      3) os.mkdir(log_dir) — no -p; existing → abort (keep claim)

    On step 2/3 failure: mark claim aborted; do NOT modify/rm old dirs;
    do NOT write GROUP_INVALID into old trees.
    """
    if not group_id or "/" in group_id or group_id in (".", ".."):
        raise LocalGroupClaimError(f"invalid group_id={group_id!r}", rc=2)

    claim_parent_p = Path(claim_parent)
    backup_p = Path(backup_root)
    log_p = Path(log_dir)
    claim_path = claim_parent_p / f"{group_id}.claim"
    now = time.time()
    payload = json.dumps(
        {
            "group_id": group_id,
            "pid": int(pid if pid is not None else os.getpid()),
            "ts": now,
            "backup_root": str(backup_p),
            "log_dir": str(log_p),
            "status": "claimed",
        },
        sort_keys=True,
    )

    try:
        _atomic_claim_file(claim_path, payload + "\n")
    except FileExistsError as exc:
        raise LocalGroupClaimError(
            f"REFUSE_LOCAL_GROUP_REUSE: claim taken path={claim_path}",
            rc=RC_CLAIM_TAKEN,
            claim_path=str(claim_path),
        ) from exc
    except OSError as exc:
        raise LocalGroupClaimError(
            f"CLAIM_FAILED: {exc}",
            rc=RC_CLAIM_TAKEN,
            claim_path=str(claim_path),
        ) from exc

    # Ensure common parents exist (infra only). Leaf BACKUP/LOG still atomic without -p.
    try:
        backup_p.parent.mkdir(parents=True, exist_ok=True)
        log_p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LocalGroupClaimError(
            f"CLAIM_ABORTED: parent_mkdir_failed:{exc} (claim kept at {claim_path})",
            rc=RC_CLAIM_ABORTED,
            claim_path=str(claim_path),
        ) from exc

    def _abort(reason: str, target: Path) -> None:
        abort = {
            "group_id": group_id,
            "status": "claim_aborted",
            "reason": reason,
            "target": str(target),
            "ts": time.time(),
            "note": "claim retained; old dirs untouched",
        }
        abort_path = claim_parent_p / f"{group_id}.claim.aborted"
        try:
            abort_path.write_text(json.dumps(abort, sort_keys=True) + "\n", encoding="utf-8")
        except OSError:
            pass
        # Also stamp claim file status without deleting exclusivity.
        try:
            claim_path.write_text(
                json.dumps({**json.loads(payload), "status": "claim_aborted", "reason": reason}, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        raise LocalGroupClaimError(
            f"CLAIM_ABORTED: {reason} target={target} (claim kept at {claim_path}; old dirs untouched)",
            rc=RC_CLAIM_ABORTED,
            claim_path=str(claim_path),
        )

    try:
        _atomic_mkdir_one(backup_p)
    except FileExistsError:
        _abort("backup_root_exists", backup_p)
    except OSError as exc:
        _abort(f"backup_mkdir_failed:{exc}", backup_p)

    try:
        _atomic_mkdir_one(log_p)
    except FileExistsError:
        _abort("log_dir_exists", log_p)
    except OSError as exc:
        _abort(f"log_mkdir_failed:{exc}", log_p)

    return {
        "ok": True,
        "group_id": group_id,
        "claim_path": str(claim_path),
        "backup_root": str(backup_p),
        "log_dir": str(log_p),
        "rc": RC_CLAIM_OK,
    }


def claim_or_exit(
    *,
    group_id: str,
    claim_parent: Path | str,
    backup_root: Path | str,
    log_dir: Path | str,
) -> int:
    try:
        info = claim_local_group(
            group_id=group_id,
            claim_parent=claim_parent,
            backup_root=backup_root,
            log_dir=log_dir,
        )
    except LocalGroupClaimError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return int(exc.rc)
    print(
        f"LOCAL_GROUP_CLAIMED ok group_id={info['group_id']} "
        f"claim={info['claim_path']} backup={info['backup_root']} log={info['log_dir']}"
    )
    return RC_CLAIM_OK


# --- Yield / opponent check classification ---------------------------------


@dataclass(frozen=True)
class YieldVerdict:
    status: str  # CLEAR | OPPONENT | CHECK_FAILED
    detail: str = ""
    rc: int = RC_YIELD_CLEAR

    @property
    def ok_to_continue(self) -> bool:
        return self.status == YIELD_CLEAR


def classify_yield_check(stdout: str, check_rc: int) -> YieldVerdict:
    """Map SSH/kubectl/opponent_check result → three-way verdict (fail-closed)."""
    status, rc, detail = classify_transport_stdout(stdout, check_rc)
    return YieldVerdict(status=status, detail=detail, rc=rc)


def classify_idle_check(stdout: str, check_rc: int) -> YieldVerdict:
    """Pre-start idle via structured checker stdout/rc."""
    if check_rc != 0:
        return YieldVerdict(
            YIELD_CHECK_FAILED,
            detail=f"idle_check_rc={check_rc}",
            rc=RC_YIELD_CHECK_FAILED,
        )
    text = (stdout or "").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    last = lines[-1] if lines else ""
    if not last:
        return YieldVerdict(
            YIELD_CHECK_FAILED,
            detail="idle_empty_stdout",
            rc=RC_YIELD_CHECK_FAILED,
        )
    if last == YIELD_CLEAR or last == "OK":
        return YieldVerdict(YIELD_CLEAR, detail=last, rc=RC_YIELD_CLEAR)
    if last.startswith(("OPPONENT", "OPP|", "OPP")):
        return YieldVerdict(YIELD_OPPONENT, detail=last[:500], rc=RC_YIELD_OPPONENT)
    if last.startswith("CHECK_FAILED"):
        return YieldVerdict(YIELD_CHECK_FAILED, detail=last[:500], rc=RC_YIELD_CHECK_FAILED)
    # Legacy idle: non-empty proc list without status prefix → OPPONENT
    if "torchrun" in last or "pretrain" in last or "megatron" in last.lower():
        return YieldVerdict(YIELD_OPPONENT, detail=last[:500], rc=RC_YIELD_OPPONENT)
    return YieldVerdict(
        YIELD_CHECK_FAILED,
        detail=f"idle_unexpected={last[:200]}",
        rc=RC_YIELD_CHECK_FAILED,
    )


def merge_pod_verdicts(verdicts: list[YieldVerdict]) -> YieldVerdict:
    for v in verdicts:
        if v.status == YIELD_CHECK_FAILED:
            return v
    for v in verdicts:
        if v.status == YIELD_OPPONENT:
            return v
    return YieldVerdict(YIELD_CLEAR, detail="all_clear", rc=RC_YIELD_CLEAR)


# --- Fixtures -------------------------------------------------------------


@dataclass
class YieldFixtureResult:
    continued: bool
    stopped: bool
    reason: str
    cleanup_rc: int
    group_invalid: bool
    invalid_reason: str
    killed: bool
    old_hashes: dict[str, str]
    cleanup_status: str = ""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def simulate_yield_monitor(
    *,
    checks: list[tuple[str, int]],
    cleanup_rc: int = 0,
    group_dir: Optional[Path] = None,
    pre_existing: Optional[dict[str, str]] = None,
    write_invalid: bool = True,
) -> YieldFixtureResult:
    """Local fixture: sequence of (stdout, rc) like the wait loop."""
    old_hashes: dict[str, str] = {}
    if group_dir is not None and pre_existing:
        group_dir.mkdir(parents=True, exist_ok=True)
        for name, content in pre_existing.items():
            p = group_dir / name
            p.write_text(content, encoding="utf-8")
            old_hashes[name] = _sha256_file(p)

    for stdout, rc in checks:
        verdict = classify_yield_check(stdout, rc)
        if verdict.ok_to_continue:
            continue
        reason = (
            "YIELD_CHECK_FAILED"
            if verdict.status == YIELD_CHECK_FAILED
            else "yield_opponent"
        )
        cleanup_status = "CLEANUP_OK" if cleanup_rc == 0 else "CLEANUP_INCOMPLETE"
        if group_dir is not None and write_invalid:
            from fanout_orchestrator import mark_group_invalid

            mark_group_invalid(
                group_dir,
                group_id=group_dir.name,
                reason=reason,
                attempt_id="fixture_attempt",
                stage="yield",
                extra={"cleanup_status": cleanup_status, "detail": verdict.detail},
            )
        return YieldFixtureResult(
            continued=False,
            stopped=True,
            reason=reason,
            cleanup_rc=cleanup_rc,
            group_invalid=True,
            invalid_reason=reason,
            killed=True,
            old_hashes=old_hashes,
            cleanup_status=cleanup_status,
        )

    return YieldFixtureResult(
        continued=True,
        stopped=False,
        reason="CLEAR",
        cleanup_rc=0,
        group_invalid=False,
        invalid_reason="",
        killed=False,
        old_hashes=old_hashes,
        cleanup_status="",
    )


def simulate_opponent_check_fixture(
    *,
    kind: str,
    group_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Mock transport/ps outcomes: ps_rc / kubectl_rc / ssh_rc / empty / malformed / opponent / clear."""
    import opponent_check as oc

    mapping = {
        "ps_rc": dict(fixture_ps_rc=1, fixture_ps_stdout=""),
        "kubectl_rc": dict(classify_stdout="", classify_rc=1),
        "ssh_rc": dict(classify_stdout="", classify_rc=255),
        "empty": dict(classify_stdout="", classify_rc=0),
        "malformed": dict(fixture_ps_rc=0, fixture_ps_stdout="not a ps table\ngarbage"),
        "opponent": dict(
            fixture_ps_rc=0,
            fixture_ps_stdout="PID PGID STAT COMMAND\n1 1 S torchrun pretrain_gpt.py\n",
        ),
        "clear": dict(
            fixture_ps_rc=0,
            fixture_ps_stdout="PID PGID STAT COMMAND\n1 1 S bash\n",
        ),
    }
    if kind not in mapping:
        raise ValueError(f"unknown fixture kind={kind}")
    spec = mapping[kind]

    if "classify_rc" in spec:
        status, rc, detail = oc.classify_transport_stdout(
            spec["classify_stdout"], spec["classify_rc"]
        )
        tool_rc = rc
        stdout = f"{status}|{detail}" if status != oc.STATUS_CLEAR else oc.STATUS_CLEAR
    else:
        argv = [
            "--mode",
            "idle",
            "--fixture-ps-rc",
            str(spec["fixture_ps_rc"]),
            "--fixture-ps-stdout",
            spec.get("fixture_ps_stdout") or "",
        ]
        tool_rc = oc.main(argv)
        if tool_rc == oc.RC_CLEAR:
            status, stdout, detail = oc.STATUS_CLEAR, oc.STATUS_CLEAR, "clear"
        elif tool_rc == oc.RC_OPPONENT:
            status, stdout, detail = oc.STATUS_OPPONENT, "OPPONENT|fixture", "opp"
        else:
            status, stdout, detail = oc.STATUS_CHECK_FAILED, "CHECK_FAILED|fixture", "fail"

    continued = status == oc.STATUS_CLEAR
    stopped = not continued
    wrote_invalid = False
    if stopped and group_dir is not None:
        group_dir.mkdir(parents=True, exist_ok=True)
        from fanout_orchestrator import mark_group_invalid

        mark_group_invalid(
            group_dir,
            group_id=group_dir.name,
            reason="YIELD_CHECK_FAILED" if status == oc.STATUS_CHECK_FAILED else "yield_opponent",
            attempt_id="fixture",
            stage="pre_start" if kind != "opponent" else "yield",
            extra={"cleanup_status": "CLEANUP_OK", "kind": kind},
        )
        wrote_invalid = True

    return {
        "kind": kind,
        "status": status,
        "rc": tool_rc,
        "continued": continued,
        "stopped": stopped,
        "wrote_invalid": wrote_invalid,
        "stdout": stdout,
    }


def simulate_local_group_refuse_fixture(
    group_dir: Path,
    *,
    seed: Optional[dict[str, str]] = None,
    empty: bool = False,
) -> dict[str, Any]:
    """Fixture: pre-seed local group (or empty), assert refuse, hashes unchanged."""
    if group_dir.exists():
        raise RuntimeError("fixture expects absent start path")
    hashes_before: dict[str, str] = {}
    if empty:
        group_dir.mkdir(parents=True, exist_ok=False)
    elif seed:
        group_dir.mkdir(parents=True, exist_ok=False)
        for name, content in seed.items():
            p = group_dir / name
            if "/" in name:
                p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            hashes_before[name] = _sha256_file(p)
    else:
        try:
            assert_local_group_absent(group_dir)
            group_dir.mkdir(parents=True, exist_ok=False)
            (group_dir / "fresh.txt").write_text("new\n", encoding="utf-8")
            return {
                "refused": False,
                "created": True,
                "hashes_before": {},
                "hashes_after": {"fresh.txt": _sha256_file(group_dir / "fresh.txt")},
                "rc": 0,
            }
        except LocalGroupExistsError:
            return {"refused": True, "created": False, "rc": RC_CLAIM_TAKEN, "unexpected": True}

    refused = False
    try:
        assert_local_group_absent(group_dir)
    except LocalGroupExistsError:
        refused = True

    hashes_after: dict[str, str] = {}
    for name in hashes_before:
        hashes_after[name] = _sha256_file(group_dir / name)

    wrote_invalid = (group_dir / "GROUP_INVALID.json").exists() and "GROUP_INVALID.json" not in (
        seed or {}
    )
    complete_and_invalid = (
        (group_dir / "GROUP_COMPLETE").exists() or (group_dir / "GROUP_COMPLETE.json").exists()
    ) and (group_dir / "GROUP_INVALID.json").exists() and "GROUP_INVALID.json" not in (seed or {})

    return {
        "refused": refused,
        "created": False,
        "hashes_before": hashes_before,
        "hashes_after": hashes_after,
        "hashes_unchanged": hashes_before == hashes_after,
        "wrote_invalid_into_old": wrote_invalid,
        "complete_and_new_invalid": complete_and_invalid,
        "rc": RC_CLAIM_TAKEN if refused else 0,
        "empty": empty,
    }


def simulate_concurrent_claim_fixture(
    *,
    group_id: str,
    parent: Path,
    n_procs: int = 3,
    seed_old: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Spawn n_procs claiming same GROUP_ID; exactly one success; old hashes intact."""
    import subprocess

    claim_parent = parent / "claims"
    backup_parent = parent / "backups"
    log_parent = parent / "logs"
    claim_parent.mkdir(parents=True, exist_ok=True)
    backup_parent.mkdir(parents=True, exist_ok=True)
    log_parent.mkdir(parents=True, exist_ok=True)

    old_backup = backup_parent / group_id
    hashes_before: dict[str, str] = {}
    if seed_old is not None:
        old_backup.mkdir(parents=True, exist_ok=False)
        for name, content in seed_old.items():
            p = old_backup / name
            if "/" in name:
                p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            hashes_before[name] = _sha256_file(p)

    here = Path(__file__).resolve().parent
    script = f"""
import sys
from pathlib import Path
sys.path.insert(0, {str(here)!r})
from local_group_guard import claim_or_exit
rc = claim_or_exit(
    group_id={group_id!r},
    claim_parent={str(claim_parent)!r},
    backup_root={str(backup_parent / group_id)!r},
    log_dir={str(log_parent / group_id)!r},
)
raise SystemExit(rc)
"""
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(n_procs)
    ]
    results = []
    for p in procs:
        out, err = p.communicate(timeout=30)
        results.append({"rc": p.returncode, "out": out, "err": err})

    ok = [r for r in results if r["rc"] == 0]
    fail = [r for r in results if r["rc"] != 0]
    hashes_after: dict[str, str] = {}
    for name in hashes_before:
        hashes_after[name] = _sha256_file(old_backup / name)

    wrote_invalid = False
    if old_backup.exists():
        wrote_invalid = (old_backup / "GROUP_INVALID.json").exists() and (
            "GROUP_INVALID.json" not in (seed_old or {})
        )

    return {
        "n_ok": len(ok),
        "n_fail": len(fail),
        "results": results,
        "hashes_before": hashes_before,
        "hashes_after": hashes_after,
        "hashes_unchanged": hashes_before == hashes_after,
        "wrote_invalid_into_old": wrote_invalid,
        "claim_exists": (claim_parent / f"{group_id}.claim").exists(),
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_abs = sub.add_parser("assert-absent", help="Exit 19 if local group path exists")
    p_abs.add_argument("path")
    p_abs.add_argument("--label", default="local group dir")

    p_claim = sub.add_parser(
        "claim",
        help="Atomic claim GROUP_ID then mkdir BACKUP_ROOT + LOG_DIR (no -p)",
    )
    p_claim.add_argument("--group-id", required=True)
    p_claim.add_argument("--claim-parent", required=True)
    p_claim.add_argument("--backup-root", required=True)
    p_claim.add_argument("--log-dir", required=True)

    p_cls = sub.add_parser("classify-yield", help="Classify one check stdout/rc")
    p_cls.add_argument("--stdout", default="")
    p_cls.add_argument("--rc", type=int, default=0)

    args = ap.parse_args(argv)
    if args.cmd == "assert-absent":
        return refuse_or_exit(args.path, label=args.label)
    if args.cmd == "claim":
        return claim_or_exit(
            group_id=args.group_id,
            claim_parent=args.claim_parent,
            backup_root=args.backup_root,
            log_dir=args.log_dir,
        )
    if args.cmd == "classify-yield":
        v = classify_yield_check(args.stdout, args.rc)
        print(json.dumps({"status": v.status, "detail": v.detail, "rc": v.rc}, sort_keys=True))
        return 0 if v.status == YIELD_CLEAR else v.rc
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
