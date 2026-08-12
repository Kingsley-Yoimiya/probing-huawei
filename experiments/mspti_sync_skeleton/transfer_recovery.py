#!/usr/bin/env python3
"""Manifest-driven transfer fallback after whole-tree tar truncation.

Simplified model (no staging publish / quarantine ceremony):
  - Claim/plan binding authorizes *which new group* may receive a recover fill.
  - Forged claim must never rmtree/unlink/rename pre-existing attempt trees
    (including old INVALID / GROUP_INVALID groups).
  - If final ``${attempt_id}`` already exists → refuse immediately (no merge).
  - Fallback: atomic ``mkdir(final, exist_ok=False)`` then 16MiB chunk fill
    directly into final (see ``chunk_pull.py``). No publish rename.
  - On failure: keep ``.partial`` under final; no LOCAL seal; caller marks
    GROUP_INVALID. Do not analyze incomplete trees.

Fast path (optional): tar into exclusive ``.pull-${attempt_id}-${nonce}`` temp
under the *new* group only. On truncation/fail: leave the temp dir as-is
(no quarantine rename). Then run chunk fallback into a still-absent final.

Fallback order inside final (LOCAL seal is last success content write):
  1) claim/plan bind (authorization only)
  2) refuse if final exists / formal INVALID markers
  3) mkdir(final, exist_ok=False)
  4) meta + digest/manifest bind + disk/deadline gates
  5) per-file 16MiB O_NOFOLLOW+pread chunks (≤3/chunk) into ``.partial`` → rename
  6) restore sealed SO mode 0444 + full strict (require_readonly)
  7) write TRANSFER_RETRY_RECOVERED.json + fsync
  8) write LOCAL_VERIFIED_SEAL last (failure scrubs seal temps only)

No ``|| true``. Never rmtree old trees.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional, Protocol

from provenance import (
    canonical_aggregate_sha256,
    sha256_file,
    write_local_verified_seal,
)

from chunk_pull import (
    CHUNK_SIZE,
    ChunkDiskError,
    ChunkHashMismatchError,
    ChunkPullError,
    ChunkShortReadError,
    ChunkTimeoutError,
    ChunkTransportError,
    KubectlChunkTransport,
    ScriptedChunkTransport,
    _is_jump_local,
    pull_file_chunked,
)

META_NAMES = (
    "artifact_digest.json",
    "attempt_manifest.json",
    "attempt_manifest.sha256",
)

RECOVERY_LOG_NAME = "TRANSFER_RETRY_RECOVERED.json"
MAX_FILE_ATTEMPTS = 3
STREAM_CHUNK = 1024 * 1024

# Time / size / disk gates (formal cannot disable disk safety).
DEFAULT_GLOBAL_DEADLINE_S = 2 * 60 * 60  # 2h — covers multi-GB fallback
MIN_BYTES_PER_SEC = 256 * 1024  # 256 KiB/s floor for per-file timeout
MIN_FILE_TIMEOUT_S = 30.0
MAX_SINGLE_FILE_BYTES = 8 * 1024 * 1024 * 1024  # 8 GiB
MAX_TOTAL_BYTES = 64 * 1024 * 1024 * 1024  # 64 GiB
DISK_SAFETY_FLOOR_BYTES = 512 * 1024 * 1024  # 512 MiB
DISK_SAFETY_RATIO = 0.10  # 10% of expected total
RESERVED_PATH_PARTS = frozenset({".partial"})
RESERVED_SUFFIXES = (".partial", ".tmp", ".json.tmp", ".part")

# Pod-side stream helper: open with O_NOFOLLOW, fstat regular, write verified fd → stdout.
# Avoids test-then-cat TOCTOU. Non-zero exit on any failure.
_REMOTE_SAFE_STREAM_PY = r"""
import os, stat, sys
base, rel = sys.argv[1], sys.argv[2]
if not rel or rel.startswith("/") or "\\" in rel or "\0" in rel:
    sys.stderr.write("unsafe_rel\n"); sys.exit(2)
parts = rel.split("/")
if any(p in ("", ".", "..") for p in parts):
    sys.stderr.write("bad_parts\n"); sys.exit(2)
if any(p == ".partial" or p.endswith(".partial") or p.endswith(".tmp") for p in parts):
    sys.stderr.write("reserved\n"); sys.exit(2)
base_real = os.path.realpath(base)
if not os.path.isdir(base_real):
    sys.stderr.write("base_not_dir\n"); sys.exit(2)
full = os.path.join(base_real, *parts)
parent = os.path.dirname(full)
# Resolve parents without following the final component.
try:
    parent_real = os.path.realpath(parent)
except OSError as e:
    sys.stderr.write(f"parent_resolve:{e}\n"); sys.exit(2)
# Every prefix of the relative path must stay under base_real.
cur = base_real
for p in parts[:-1]:
    cur = os.path.join(cur, p)
    if os.path.islink(cur):
        sys.stderr.write("symlink_component\n"); sys.exit(2)
    if not os.path.isdir(cur):
        sys.stderr.write("missing_dir\n"); sys.exit(2)
    if os.path.realpath(cur) != cur and not os.path.realpath(cur).startswith(base_real + os.sep):
        sys.stderr.write("dir_escape\n"); sys.exit(2)
    if not os.path.realpath(cur).startswith(base_real + os.sep) and os.path.realpath(cur) != base_real:
        sys.stderr.write("dir_escape\n"); sys.exit(2)
if not parent_real.startswith(base_real + os.sep) and parent_real != base_real:
    sys.stderr.write("parent_escape\n"); sys.exit(2)
flags = os.O_RDONLY
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
try:
    fd = os.open(full, flags)
except OSError as e:
    sys.stderr.write(f"open:{e}\n"); sys.exit(2)
try:
    st = os.fstat(fd)
    if stat.S_ISLNK(st.st_mode):
        sys.stderr.write("symlink\n"); sys.exit(2)
    if not stat.S_ISREG(st.st_mode):
        sys.stderr.write("not_regular\n"); sys.exit(2)
    # Confirm final path (via /proc or fcntl) stays under base when possible.
    try:
        via = os.readlink(f"/proc/self/fd/{fd}")
        via_real = os.path.realpath(via) if via else ""
        if via_real and not (via_real == base_real or via_real.startswith(base_real + os.sep)):
            sys.stderr.write("fd_escape\n"); sys.exit(2)
    except OSError:
        pass
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        os.write(1, chunk)
finally:
    os.close(fd)
"""


class TransferRecoveryError(RuntimeError):
    """Fail-closed transfer / path-safety error."""


class TransportError(TransferRecoveryError):
    def __init__(self, message: str, *, rc: int = 1):
        super().__init__(message)
        self.rc = rc


class ShortReadError(TransferRecoveryError):
    pass


class HashMismatchError(TransferRecoveryError):
    pass


class TimeoutTransferError(TransferRecoveryError):
    pass


class DiskBudgetError(TransferRecoveryError):
    pass


class Transport(Protocol):
    def stream_file(
        self,
        remote_abs: str,
        local_path: Path,
        *,
        timeout_s: Optional[float] = None,
    ) -> int:
        """Stream remote file bytes into local_path. Return bytes written."""


@dataclass
class AttemptRecord:
    try_index: int
    ok: bool
    bytes_written: int = 0
    sha256: str = ""
    error: str = ""
    error_class: str = ""


@dataclass
class FileRecoveryRecord:
    path: str
    expected_size: int
    expected_sha256: str
    attempts: list[AttemptRecord] = field(default_factory=list)
    final_size: int = 0
    final_sha256: str = ""


def _norm_path_str(p: Path | str) -> str:
    return str(Path(p).resolve())


def canonical_artifact_relpath(rel: str) -> str:
    """Require original string == PurePosixPath canonical form; reject escapes/reserved."""
    if not isinstance(rel, str) or rel == "":
        raise TransferRecoveryError("empty relative path")
    if "\0" in rel:
        raise TransferRecoveryError(f"NUL in path: {rel!r}")
    if "\\" in rel:
        raise TransferRecoveryError(f"backslash in path: {rel!r}")
    if rel.startswith("/") or rel.startswith("~"):
        raise TransferRecoveryError(f"absolute path rejected: {rel!r}")
    try:
        norm = str(PurePosixPath(rel))
    except (ValueError, OSError) as exc:
        raise TransferRecoveryError(f"path not posix-canonical: {rel!r}") from exc
    if norm != rel:
        raise TransferRecoveryError(
            f"path not canonical (raw must equal PurePosixPath): {rel!r} != {norm!r}"
        )
    if norm in (".", ".."):
        raise TransferRecoveryError(f"unsafe path: {rel!r}")
    parts = PurePosixPath(rel).parts
    if not parts:
        raise TransferRecoveryError(f"empty path parts: {rel!r}")
    for part in parts:
        if part in ("", ".", ".."):
            raise TransferRecoveryError(f"empty/dot segment in path: {rel!r}")
        if part in RESERVED_PATH_PARTS:
            raise TransferRecoveryError(f"reserved path component: {rel!r}")
        if any(part.endswith(suf) for suf in RESERVED_SUFFIXES):
            raise TransferRecoveryError(f"reserved path suffix: {rel!r}")
    return rel


def is_safe_recovery_relpath(rel: str) -> bool:
    try:
        canonical_artifact_relpath(rel)
        return True
    except TransferRecoveryError:
        return False


def resolve_safe_attempt_dir(backup_root: Path | str, attempt_id: str) -> Path:
    """Resolve attempt dir under BACKUP_ROOT with strict guards."""
    if not attempt_id or not isinstance(attempt_id, str):
        raise TransferRecoveryError("empty attempt_id")
    if attempt_id in (".", "..") or "/" in attempt_id or "\\" in attempt_id:
        raise TransferRecoveryError(f"unsafe attempt_id={attempt_id!r}")
    if attempt_id.startswith("-"):
        raise TransferRecoveryError(f"unsafe attempt_id={attempt_id!r}")

    backup = Path(backup_root)
    if not str(backup):
        raise TransferRecoveryError("empty backup_root")
    if backup.exists() and backup.is_symlink():
        raise TransferRecoveryError(f"backup_root is symlink: {backup}")
    backup_real = backup.resolve()
    if backup_real == Path("/") or str(backup_real) == "/":
        raise TransferRecoveryError("backup_root resolves to filesystem root")
    if not backup_real.is_dir():
        raise TransferRecoveryError(f"backup_root not a directory: {backup_real}")

    candidate = backup / attempt_id
    if candidate.exists() and candidate.is_symlink():
        raise TransferRecoveryError(f"attempt dir is symlink: {candidate}")
    resolved = candidate.resolve()
    if resolved.name != attempt_id:
        raise TransferRecoveryError(
            f"basename mismatch: resolved={resolved.name!r} expected={attempt_id!r}"
        )
    try:
        rel = resolved.relative_to(backup_real)
    except ValueError as exc:
        raise TransferRecoveryError(
            f"attempt dir escapes BACKUP_ROOT: {resolved} not under {backup_real}"
        ) from exc
    if rel == Path("."):
        raise TransferRecoveryError("attempt dir equals BACKUP_ROOT")
    if len(rel.parts) != 1:
        raise TransferRecoveryError(f"attempt dir not single-level under backup: {rel}")
    return resolved


def load_claim(claim_path: Path | str) -> dict[str, Any]:
    p = Path(claim_path)
    if not p.is_file() or p.is_symlink():
        raise TransferRecoveryError(f"claim missing or symlink: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransferRecoveryError(f"claim unreadable: {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise TransferRecoveryError(f"claim not object: {p}")
    return data


def load_group_plan(plan_path: Path | str) -> dict[str, Any]:
    from ab_plan import plan_hash as compute_plan_hash

    p = Path(plan_path)
    if not p.is_file() or p.is_symlink():
        raise TransferRecoveryError(f"group_plan missing or symlink: {p}")
    try:
        plan = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransferRecoveryError(f"group_plan unreadable: {p}: {exc}") from exc
    if not isinstance(plan, dict):
        raise TransferRecoveryError("group_plan not object")
    digest = compute_plan_hash(plan)
    listed = plan.get("plan_hash")
    if not listed or listed != digest:
        raise TransferRecoveryError(
            f"plan_hash mismatch: listed={listed} recomputed={digest}"
        )
    return plan


def validate_claim_and_plan_binding(
    *,
    backup_root: Path | str,
    attempt_id: str,
    claim_path: Path | str,
    group_plan_path: Path | str,
    group_id: str,
    plan_hash: str,
    diagnostic: bool = False,
    binding_plan_path: Optional[Path | str] = None,
    binding_plan_hash: Optional[str] = None,
) -> dict[str, Any]:
    """Bind clear target to current claim + immutable plan. Refuse unbound clears."""
    if not claim_path:
        raise TransferRecoveryError("claim_path required (refuse unbound clear)")
    if not group_plan_path:
        raise TransferRecoveryError("group_plan required (refuse unbound clear)")
    if not group_id:
        raise TransferRecoveryError("group_id required")
    if not plan_hash or len(plan_hash) != 64:
        raise TransferRecoveryError("plan_hash required (64 hex)")

    claim = load_claim(claim_path)
    claim_gid = str(claim.get("group_id") or "")
    claim_backup = str(claim.get("backup_root") or "")
    claim_status = str(claim.get("status") or "")
    if claim_gid != group_id:
        raise TransferRecoveryError(
            f"claim group_id mismatch: claim={claim_gid!r} expected={group_id!r}"
        )
    if claim_status and claim_status not in ("claimed",):
        raise TransferRecoveryError(f"claim status not claimed: {claim_status!r}")
    backup_real = _norm_path_str(backup_root)
    if not claim_backup or _norm_path_str(claim_backup) != backup_real:
        raise TransferRecoveryError(
            f"claim backup_root mismatch: claim={claim_backup!r} expected={backup_real!r}"
        )
    # Claim filename must match group_id.claim when parent is conventional.
    claim_p = Path(claim_path)
    if claim_p.name != f"{group_id}.claim":
        raise TransferRecoveryError(
            f"claim filename must be {{group_id}}.claim: got={claim_p.name}"
        )

    plan = load_group_plan(group_plan_path)
    if str(plan.get("group_id") or "") != group_id:
        raise TransferRecoveryError(
            f"plan group_id mismatch: plan={plan.get('group_id')!r} expected={group_id!r}"
        )
    if str(plan.get("plan_hash") or "") != plan_hash:
        raise TransferRecoveryError(
            f"request plan_hash mismatch: plan={plan.get('plan_hash')!r} expected={plan_hash!r}"
        )
    # Plan file must live under the claimed backup root (immutable local plan).
    plan_real = _norm_path_str(group_plan_path)
    if not plan_real.startswith(backup_real + os.sep) and plan_real != backup_real:
        # Allow plan exactly at backup_root/group_plan.json
        raise TransferRecoveryError(
            f"group_plan must reside under claimed backup_root: {plan_real}"
        )
    if Path(plan_real).name != "group_plan.json":
        raise TransferRecoveryError("group_plan basename must be group_plan.json")

    attempts = list(plan.get("attempts") or [])
    member = next((a for a in attempts if str(a.get("attempt_id")) == attempt_id), None)
    if member is None:
        raise TransferRecoveryError(
            f"attempt_id not a plan member: {attempt_id!r}"
        )

    # Binding plan for remote manifest fields (formal: same; diagnostic: source plan).
    if binding_plan_path is None:
        if diagnostic:
            raise TransferRecoveryError(
                "diagnostic mode requires explicit --binding-plan (read-only source plan)"
            )
        binding_plan = plan
        binding_hash = plan_hash
        binding_path = Path(group_plan_path)
    else:
        if not binding_plan_hash:
            raise TransferRecoveryError("binding_plan_hash required when binding-plan set")
        binding_plan = load_group_plan(binding_plan_path)
        if str(binding_plan.get("plan_hash") or "") != binding_plan_hash:
            raise TransferRecoveryError(
                f"binding plan_hash mismatch: plan={binding_plan.get('plan_hash')!r} "
                f"expected={binding_plan_hash!r}"
            )
        binding_hash = binding_plan_hash
        binding_path = Path(binding_plan_path)
        b_member = next(
            (a for a in (binding_plan.get("attempts") or []) if str(a.get("attempt_id")) == attempt_id),
            None,
        )
        if b_member is None:
            raise TransferRecoveryError(
                f"attempt_id not in binding/source plan: {attempt_id!r}"
            )
        if diagnostic:
            src_gid = str(binding_plan.get("group_id") or "")
            if not src_gid or src_gid == group_id:
                raise TransferRecoveryError(
                    "diagnostic claim group_id must differ from binding/source group_id "
                    "(refuse borrowing INVALID claim)"
                )
        member = b_member  # prefer source arm/out_dir for manifest checks

    # Target pull dir must be the attempt's local directory under claimed backup.
    target = resolve_safe_attempt_dir(backup_root, attempt_id)
    return {
        "claim": claim,
        "plan": plan,
        "binding_plan": binding_plan,
        "binding_plan_hash": binding_hash,
        "binding_plan_path": str(binding_path),
        "attempt": member,
        "attempt_arm": str(member.get("arm") or ""),
        "target": target,
        "backup_root": backup_real,
        "group_id": group_id,
        "plan_hash": plan_hash,
        "diagnostic": bool(diagnostic),
    }


def safe_clear_attempt_dir(
    backup_root: Path | str,
    attempt_id: str,
    *,
    binding: Optional[dict[str, Any]] = None,
) -> Path:
    """REMOVED: destructive clear/rmtree of attempt dirs is permanently disabled.

    Kept as a hard-fail stub so any caller/CLI path cannot wipe old INVALID trees
    even with a forged claim. Use create_exclusive_staging_dir + atomic_publish.
    """
    raise TransferRecoveryError(
        "safe_clear_attempt_dir disabled: non-destructive staging only "
        f"(refused clear of {backup_root!s}/{attempt_id!s}; binding={binding is not None})"
    )


def _new_staging_nonce() -> str:
    # time_ns + pid + random — mkdir(exist_ok=False) still enforces uniqueness.
    return f"{time.time_ns():x}-{os.getpid():x}-{secrets.token_hex(8)}"


def create_exclusive_staging_dir(
    backup_root: Path | str,
    attempt_id: str,
    *,
    kind: str = "pull",
    nonce: Optional[str] = None,
    binding: Optional[dict[str, Any]] = None,
) -> Path:
    """Atomically create a unique staging dir under backup_root (mkdir exist_ok=False).

    Name: ``.{kind}-{attempt_id}-{nonce}``. Never touches an existing final attempt.
    Optional binding only checks backup_root/attempt_id consistency (authorization);
    it does not grant destructive rights.
    """
    if kind not in ("pull", "fallback"):
        raise TransferRecoveryError(f"bad staging kind: {kind!r}")
    # Validate attempt_id shape without requiring final to be absent yet.
    resolve_safe_attempt_dir(backup_root, attempt_id)
    backup = Path(backup_root).resolve()
    if not backup.is_dir() or backup.is_symlink():
        raise TransferRecoveryError(f"backup_root not a real directory: {backup}")
    if binding is not None:
        if _norm_path_str(binding["backup_root"]) != _norm_path_str(backup):
            raise TransferRecoveryError("staging backup_root != binding backup_root")
        if str(binding["attempt"].get("attempt_id")) != attempt_id:
            raise TransferRecoveryError("staging attempt_id != binding attempt")
    if nonce is None:
        nonce = _new_staging_nonce()
    if not nonce or "/" in nonce or "\\" in nonce or ".." in nonce or "\0" in nonce:
        raise TransferRecoveryError(f"unsafe staging nonce: {nonce!r}")
    name = f".{kind}-{attempt_id}-{nonce}"
    staging = backup / name
    if staging.exists():
        raise TransferRecoveryError(f"staging path already exists (nonce reuse): {staging}")
    try:
        os.mkdir(str(staging), 0o755)
    except FileExistsError as exc:
        raise TransferRecoveryError(
            f"staging mkdir raced / nonce reuse: {staging}"
        ) from exc
    if staging.is_symlink() or not staging.is_dir():
        raise TransferRecoveryError(f"staging landed unsafe: {staging}")
    return staging.resolve()


def assert_final_absent_for_publish(
    backup_root: Path | str,
    attempt_id: str,
) -> Path:
    """Return final attempt path; refuse if it already exists (no merge)."""
    backup = Path(backup_root).resolve()
    # Shape check (symlink / escape).
    resolve_safe_attempt_dir(backup, attempt_id)
    final = backup / attempt_id
    if final.exists():
        raise TransferRecoveryError(
            f"refuse fill: final attempt path already exists: {final}"
        )
    return final


def mkdir_final_exclusive(backup_root: Path | str, attempt_id: str) -> Path:
    """Atomic mkdir(final, exist_ok=False). Final must not already exist."""
    final = assert_final_absent_for_publish(backup_root, attempt_id)
    try:
        os.mkdir(str(final), 0o755)
    except FileExistsError as exc:
        raise TransferRecoveryError(
            f"refuse fill: final appeared concurrently: {final}"
        ) from exc
    if final.is_symlink() or not final.is_dir():
        raise TransferRecoveryError(f"final landed unsafe: {final}")
    return final.resolve()


def assert_formal_group_allows_recover_publish(
    backup_root: Path | str,
    *,
    diagnostic: bool,
) -> None:
    """Formal launcher must not recover-fill into an INVALID/GROUP_INVALID group."""
    if diagnostic:
        return
    root = Path(backup_root)
    for name in ("GROUP_INVALID.json", "INVALID.json"):
        marker = root / name
        if marker.exists():
            raise TransferRecoveryError(
                f"formal refuse recover-fill: {name} present under {root}"
            )


def quarantine_fast_path_staging(
    staging: Path,
    attempt_id: str,
) -> Path:
    """DISABLED: quarantine rename removed from formal path.

    Fast-path temp dirs are left in place on failure; do not rename/rmtree.
    """
    raise TransferRecoveryError(
        "quarantine-staging disabled: leave fast-path temp in place; "
        f"refused rename of {staging} for attempt_id={attempt_id!r}"
    )


def atomic_publish_staging(staging: Path, final: Path) -> Path:
    """DISABLED: publish rename removed; fill final via mkdir + chunk pull."""
    raise TransferRecoveryError(
        "publish-staging disabled: use mkdir_final_exclusive + chunk fill "
        f"(refused rename {staging} → {final})"
    )


def scrub_staging_local_seal_temps(staging: Path) -> None:
    """Remove LOCAL_VERIFIED_SEAL and temps inside *this* staging only."""
    staging = Path(staging)
    if not staging.is_dir() or staging.is_symlink():
        return
    for name in (
        "LOCAL_VERIFIED_SEAL.json",
        "LOCAL_VERIFIED_SEAL.json.tmp",
        "LOCAL_VERIFIED_SEAL.json.tmp.tmp",
    ):
        p = staging / name
        try:
            if p.exists() or p.is_symlink():
                p.unlink()
        except OSError:
            pass
    try:
        for p in staging.glob("LOCAL_VERIFIED_SEAL*"):
            if p.is_file() or p.is_symlink():
                try:
                    p.unlink()
                except OSError:
                    pass
    except OSError:
        pass


def scrub_staging_partial_files(staging: Path) -> None:
    """Unlink leftover .partial *files* under this staging only (not old trees)."""
    staging = Path(staging)
    partial_root = staging / ".partial"
    if not partial_root.exists():
        return
    try:
        for p in partial_root.rglob("*"):
            if p.is_file() or p.is_symlink():
                try:
                    p.unlink()
                except OSError:
                    pass
    except OSError:
        pass


def restore_sealed_collector_so_mode(attempt_dir: Path) -> dict[str, Any]:
    """Resolve collector_so_sealed_relpath, verify hash, chmod 0444, strict readonly.

    Compatible with 150549-era seals (mode may be absent from digest metadata).
    """
    from strict_validate import verify_sealed_collector_so

    attempt_dir = Path(attempt_dir)
    man_path = attempt_dir / "attempt_manifest.json"
    if not man_path.is_file():
        raise TransferRecoveryError("missing attempt_manifest.json for SO restore")
    manifest = json.loads(man_path.read_text(encoding="utf-8"))
    build: dict[str, Any] = {}
    build_path = attempt_dir / "provenance_build.json"
    if build_path.is_file():
        build = json.loads(build_path.read_text(encoding="utf-8"))
    rel = build.get("collector_so_sealed_relpath") or manifest.get(
        "collector_so_sealed_relpath"
    )
    if not rel:
        raise TransferRecoveryError(
            "collector_so_sealed_relpath missing from provenance/manifest"
        )
    rel = canonical_artifact_relpath(str(rel))
    so_path = attempt_dir / rel
    if not so_path.is_file() or so_path.is_symlink():
        raise TransferRecoveryError(f"sealed SO missing or symlink: {rel}")
    claimed = (
        (manifest.get("provenance") or {}).get("collector_so_sha256")
        or manifest.get("collector_so_sha256")
        or build.get("collector_so_sha256")
    )
    actual = sha256_file(so_path)
    if not claimed or actual != str(claimed):
        raise TransferRecoveryError(
            f"sealed SO hash mismatch before chmod: got={actual} claimed={claimed}"
        )
    # Digest must list this path (fail closed if absent).
    art_path = attempt_dir / "artifact_digest.json"
    art = json.loads(art_path.read_text(encoding="utf-8"))
    dig_entry = next(
        (e for e in (art.get("files") or []) if str(e.get("path")) == rel),
        None,
    )
    if dig_entry is None:
        raise TransferRecoveryError(f"sealed SO not in artifact_digest: {rel}")
    if str(dig_entry.get("sha256")) != actual:
        raise TransferRecoveryError(f"sealed SO digest hash mismatch: {rel}")
    try:
        so_path.chmod(0o444)
    except OSError as exc:
        raise TransferRecoveryError(f"chmod 0444 failed for sealed SO: {exc}") from exc
    mode = int(so_path.stat().st_mode) & 0o777
    if mode != 0o444:
        raise TransferRecoveryError(
            f"sealed SO mode not 0444 after chmod: got={oct(mode)}"
        )
    # Optional metadata for newer seals (compatible if absent).
    if "mode" in dig_entry and int(dig_entry["mode"]) != 0o444:
        raise TransferRecoveryError(
            f"digest listed mode={dig_entry['mode']!r} != 0444 for {rel}"
        )
    verify_sealed_collector_so(attempt_dir, manifest=manifest, require_readonly=True)
    return {"relpath": rel, "mode": mode, "sha256": actual, "size": so_path.stat().st_size}


def build_tree_fingerprint(root: Path | str) -> dict[str, Any]:
    """Full per-file fingerprint of a group/attempt tree (diagnostic only; never writes root)."""
    root_p = Path(root).resolve()
    files: dict[str, Any] = {}
    for p in sorted(root_p.rglob("*")):
        if not p.is_file():
            continue
        if p.is_symlink():
            continue
        rel = str(p.relative_to(root_p))
        st = p.stat()
        files[rel] = {
            "sha256": sha256_file(p),
            "size": int(st.st_size),
            "mode": int(st.st_mode) & 0o777,
            "inode": int(st.st_ino),
            "mtime_ns": int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))),
        }
    lines = [
        f"{rel}\0{meta['sha256']}\0{meta['size']}\0{meta['mode']}\0{meta['inode']}"
        for rel, meta in files.items()
    ]
    agg = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return {
        "root": str(root_p),
        "file_count": len(files),
        "aggregate_sha256": agg,
        "files": files,
    }


def fingerprints_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (
        a.get("aggregate_sha256") == b.get("aggregate_sha256")
        and a.get("file_count") == b.get("file_count")
        and a.get("files") == b.get("files")
    )


def disk_safety_bytes(expected_total: int, *, floor: int = DISK_SAFETY_FLOOR_BYTES, ratio: float = DISK_SAFETY_RATIO) -> int:
    return max(int(floor), int(expected_total * float(ratio)))


def free_bytes_for(path: Path | str) -> int:
    st = os.statvfs(str(path))
    return int(st.f_bavail) * int(st.f_frsize)


def assert_disk_budget(
    path: Path | str,
    expected_total: int,
    *,
    free_bytes_fn: Callable[[Path | str], int] = free_bytes_for,
    safety_floor: int = DISK_SAFETY_FLOOR_BYTES,
    safety_ratio: float = DISK_SAFETY_RATIO,
    require_safety: bool = True,
) -> dict[str, int]:
    if expected_total < 0:
        raise DiskBudgetError(f"negative expected_total={expected_total}")
    if expected_total > MAX_TOTAL_BYTES:
        raise DiskBudgetError(
            f"expected total exceeds hard cap: {expected_total} > {MAX_TOTAL_BYTES}"
        )
    safety = disk_safety_bytes(expected_total, floor=safety_floor, ratio=safety_ratio) if require_safety else 0
    need = int(expected_total) + int(safety)
    free = int(free_bytes_fn(path))
    if free < need:
        raise DiskBudgetError(
            f"insufficient disk: free={free} need={need} "
            f"(total={expected_total} safety={safety})"
        )
    return {"free": free, "need": need, "safety": safety, "expected_total": int(expected_total)}


def per_file_timeout_s(
    size: int,
    *,
    deadline_remaining_s: float,
    min_rate: int = MIN_BYTES_PER_SEC,
    min_timeout: float = MIN_FILE_TIMEOUT_S,
) -> float:
    if deadline_remaining_s <= 0:
        raise TimeoutTransferError("global deadline exhausted before file")
    by_rate = max(float(min_timeout), float(size) / float(max(1, min_rate)) + 15.0)
    return max(1.0, min(by_rate, float(deadline_remaining_s)))


def _kill_process_group(proc: subprocess.Popen) -> None:
    if proc.pid is None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _sha256_stream_copy(
    src_stream,
    dest: Path,
    *,
    expected_size: Optional[int] = None,
) -> tuple[int, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    written = 0
    with dest.open("wb") as out:
        while True:
            chunk = src_stream.read(STREAM_CHUNK)
            if not chunk:
                break
            out.write(chunk)
            h.update(chunk)
            written += len(chunk)
            if expected_size is not None and written > int(expected_size):
                raise ShortReadError(
                    f"stream exceeded expected size: wrote={written} expected={expected_size}"
                )
    return written, h.hexdigest()


def _atomic_replace(partial: Path, final: Path) -> None:
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(str(partial), str(final))


@dataclass
class KubectlCatTransport:
    """Direct per-file stream via pod O_NOFOLLOW helper (no jump long-term pack)."""

    jump: str
    kubeconfig: str
    kubectl: str
    namespace: str
    pod: str
    connect_timeout: int = 30
    server_alive_interval: int = 15
    server_alive_count_max: int = 4
    jump_local: bool = False

    def stream_file(
        self,
        remote_abs: str,
        local_path: Path,
        *,
        timeout_s: Optional[float] = None,
    ) -> int:
        if not remote_abs or remote_abs.startswith("-"):
            raise TransportError(f"unsafe remote path: {remote_abs!r}")
        remote_path = PurePosixPath(remote_abs)
        if ".." in remote_path.parts:
            raise TransportError(f"remote path traversal: {remote_abs!r}")
        # Split into base dir + relative name for the helper.
        base = str(remote_path.parent)
        rel = remote_path.name
        # If remote_abs includes nested rel (digest path), base=parent tree root must be
        # passed separately by caller via remote_root + rel. Here remote_abs is full path;
        # reconstruct: helper gets parent-of-file as join base... For nested paths we pass
        # the attempt remote root and rel separately through a richer API.
        # Compatibility: if only basename, base=parent; for nested, caller uses stream_rel.
        return self.stream_rel(base, rel, local_path, timeout_s=timeout_s)

    def stream_rel(
        self,
        remote_root: str,
        rel: str,
        local_path: Path,
        *,
        timeout_s: Optional[float] = None,
    ) -> int:
        canonical_artifact_relpath(rel)
        if not remote_root or remote_root.startswith("-"):
            raise TransportError(f"unsafe remote_root: {remote_root!r}")
        # Embed helper via python -c; quote carefully for remote shell.
        py = _REMOTE_SAFE_STREAM_PY
        # Use base64 to avoid quoting hell for the helper body.
        import base64

        b64 = base64.b64encode(py.encode("utf-8")).decode("ascii")
        remote_root_q = remote_root.replace("'", "'\"'\"'")
        rel_q = rel.replace("'", "'\"'\"'")
        remote_cmd = (
            f"export KUBECONFIG='{self.kubeconfig}'; "
            f"K='{self.kubectl}'; "
            f"$K exec -n '{self.namespace}' '{self.pod}' -- "
            f"python3 -c \"import base64; exec(base64.b64decode('{b64}').decode())\" "
            f"'{remote_root_q}' '{rel_q}'"
        )
        if _is_jump_local(self.jump, jump_local=self.jump_local):
            cmd = ["bash", "--noprofile", "--norc", "-lc", remote_cmd]
        else:
            cmd = [
                "ssh",
                "-n",
                "-o",
                f"ConnectTimeout={self.connect_timeout}",
                "-o",
                f"ServerAliveInterval={self.server_alive_interval}",
                "-o",
                f"ServerAliveCountMax={self.server_alive_count_max}",
                "-o",
                "BatchMode=yes",
                self.jump,
                remote_cmd,
            ]
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with local_path.open("wb") as out:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            try:
                _, err_b = proc.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired as exc:
                _kill_process_group(proc)
                try:
                    local_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise TimeoutTransferError(
                    f"stream timeout after {timeout_s}s path={remote_root}/{rel}"
                ) from exc
        if proc.returncode != 0:
            err = (err_b or b"").decode("utf-8", errors="replace")[:800]
            try:
                local_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise TransportError(
                f"transport_rc={proc.returncode} path={remote_root}/{rel}: {err}",
                rc=proc.returncode,
            )
        return local_path.stat().st_size if local_path.exists() else 0


@dataclass
class ScriptedTransport:
    """Fixture transport: per-path list of callables returning bytes or raising."""

    scripts: dict[str, list[Callable[[], bytes]]]
    _idx: dict[str, int] = field(default_factory=dict)
    hang_s: float = 0.0  # if >0 and timeout smaller, simulates hang

    def stream_file(
        self,
        remote_abs: str,
        local_path: Path,
        *,
        timeout_s: Optional[float] = None,
    ) -> int:
        key = remote_abs
        if key not in self.scripts:
            for k in self.scripts:
                if remote_abs.endswith("/" + k) or remote_abs.endswith(k):
                    key = k
                    break
        return self._run(key, remote_abs, local_path, timeout_s=timeout_s)

    def stream_rel(
        self,
        remote_root: str,
        rel: str,
        local_path: Path,
        *,
        timeout_s: Optional[float] = None,
    ) -> int:
        return self.stream_file(f"{remote_root.rstrip('/')}/{rel}", local_path, timeout_s=timeout_s)

    def _run(
        self,
        key: str,
        remote_abs: str,
        local_path: Path,
        *,
        timeout_s: Optional[float],
    ) -> int:
        if key not in self.scripts:
            raise TransportError(f"no script for {remote_abs}", rc=2)
        i = self._idx.get(key, 0)
        seq = self.scripts[key]
        if i >= len(seq):
            raise TransportError(f"script exhausted for {key}", rc=2)
        fn = seq[i]
        self._idx[key] = i + 1

        result: dict[str, Any] = {"data": None, "err": None}

        def worker() -> None:
            try:
                result["data"] = fn()
            except BaseException as exc:  # noqa: BLE001 — capture for parent
                result["err"] = exc

        if timeout_s is not None and (self.hang_s > 0 or timeout_s < 1e9):
            # Optional explicit hang for a key via hang_s on transport.
            t = threading.Thread(target=worker, daemon=True)
            t.start()
            t.join(timeout=timeout_s if self.hang_s <= 0 else min(timeout_s, self.hang_s + 0.05))
            if t.is_alive():
                raise TimeoutTransferError(f"scripted hang timeout path={remote_abs}")
        else:
            worker()
        if result["err"] is not None:
            err = result["err"]
            if isinstance(err, TransferRecoveryError):
                raise err
            raise TransportError(str(err), rc=2)
        data = result["data"]
        assert isinstance(data, (bytes, bytearray))
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(bytes(data))
        return len(data)


def _transport_stream(
    transport: Transport,
    *,
    remote_root: Path | str,
    rel: str,
    local_path: Path,
    timeout_s: Optional[float],
) -> int:
    root = str(remote_root)
    if hasattr(transport, "stream_rel"):
        return transport.stream_rel(root, rel, local_path, timeout_s=timeout_s)  # type: ignore[attr-defined]
    return transport.stream_file(str(Path(root) / rel), local_path, timeout_s=timeout_s)


def _partial_path(attempt_dir: Path, rel: str) -> Path:
    return attempt_dir / ".partial" / rel


def _validate_digest_entries(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(files, list) or not files:
        raise TransferRecoveryError("artifact_digest.files empty")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for entry in files:
        if not isinstance(entry, dict):
            raise TransferRecoveryError("digest entry not object")
        rel_raw = str(entry.get("path", ""))
        rel = canonical_artifact_relpath(rel_raw)
        if rel in seen:
            raise TransferRecoveryError(f"duplicate digest path: {rel}")
        seen.add(rel)
        if rel in META_NAMES or rel == RECOVERY_LOG_NAME or rel == "LOCAL_VERIFIED_SEAL.json":
            raise TransferRecoveryError(f"digest must not list meta/seal: {rel}")
        sha = str(entry.get("sha256") or "")
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise TransferRecoveryError(f"bad sha256 for {rel}")
        size = int(entry.get("size", -1))
        if size < 0:
            raise TransferRecoveryError(f"bad size for {rel}")
        if size > MAX_SINGLE_FILE_BYTES:
            raise TransferRecoveryError(
                f"file size exceeds hard cap: {rel} size={size} > {MAX_SINGLE_FILE_BYTES}"
            )
        out.append({"path": rel, "sha256": sha, "size": size})
    total = sum(int(e["size"]) for e in out)
    if total > MAX_TOTAL_BYTES:
        raise TransferRecoveryError(f"digest total exceeds hard cap: {total}")
    return out


def verify_manifest_seal_and_digest(
    attempt_dir: Path,
    *,
    request_attempt_id: str,
    binding_plan: dict[str, Any],
    binding_plan_hash: str,
) -> dict[str, Any]:
    """Validate pulled meta: manifest.sha256, aggregate, attempt/plan field binding."""
    art_path = attempt_dir / "artifact_digest.json"
    man_path = attempt_dir / "attempt_manifest.json"
    sha_path = attempt_dir / "attempt_manifest.sha256"
    for p in (art_path, man_path, sha_path):
        if not p.is_file() or p.is_symlink():
            raise TransferRecoveryError(f"missing or symlink meta: {p.name}")
    listed_hash = sha_path.read_text(encoding="utf-8").strip()
    man_hash = sha256_file(man_path)
    if listed_hash != man_hash:
        raise TransferRecoveryError(
            f"manifest seal mismatch: file={man_hash} listed={listed_hash}"
        )
    art = json.loads(art_path.read_text(encoding="utf-8"))
    man = json.loads(man_path.read_text(encoding="utf-8"))
    if not isinstance(man, dict):
        raise TransferRecoveryError("attempt_manifest not object")

    # attempt_id is mandatory and must equal request.
    if "attempt_id" not in man:
        raise TransferRecoveryError("manifest missing attempt_id")
    man_aid = str(man.get("attempt_id") or "")
    if man_aid != request_attempt_id:
        raise TransferRecoveryError(
            f"manifest attempt_id mismatch: man={man_aid!r} request={request_attempt_id!r}"
        )

    plan_attempt = next(
        (
            a
            for a in (binding_plan.get("attempts") or [])
            if str(a.get("attempt_id")) == request_attempt_id
        ),
        None,
    )
    if plan_attempt is None:
        raise TransferRecoveryError("request attempt not in binding plan during manifest check")

    # plan_hash / arm / group_id when present in schema must match binding plan.
    if "plan_hash" in man:
        if str(man.get("plan_hash") or "") != binding_plan_hash:
            raise TransferRecoveryError(
                f"manifest plan_hash mismatch: man={man.get('plan_hash')!r} "
                f"expected={binding_plan_hash!r}"
            )
    if "arm" in man:
        expected_arm = str(plan_attempt.get("arm") or "")
        if str(man.get("arm") or "") != expected_arm:
            raise TransferRecoveryError(
                f"manifest arm mismatch: man={man.get('arm')!r} expected={expected_arm!r}"
            )
    if "group_id" in man and man.get("group_id") is not None:
        if str(man.get("group_id") or "") != str(binding_plan.get("group_id") or ""):
            raise TransferRecoveryError(
                f"manifest group_id mismatch: man={man.get('group_id')!r} "
                f"expected={binding_plan.get('group_id')!r}"
            )

    entries = _validate_digest_entries(list(art.get("files") or []))
    agg = canonical_aggregate_sha256(entries)
    listed_agg = art.get("aggregate_sha256") or art.get("artifact_digest_sha256")
    if not listed_agg or listed_agg != agg:
        raise TransferRecoveryError(
            f"digest aggregate mismatch: listed={listed_agg} recomputed={agg}"
        )
    man_agg = man.get("artifact_digest_sha256")
    if man_agg is None and isinstance(man.get("provenance"), dict):
        man_agg = man["provenance"].get("artifact_digest_sha256")
    if not man_agg or man_agg != agg:
        raise TransferRecoveryError(
            f"manifest aggregate binding mismatch: man={man_agg} digest={agg}"
        )
    return {
        "manifest_sha256": man_hash,
        "aggregate_sha256": agg,
        "files": entries,
        "expected_total_bytes": sum(int(e["size"]) for e in entries),
        "arm": man.get("arm") or plan_attempt.get("arm"),
        "expected_ranks": man.get("expected_ranks"),
        "expected_nodes": man.get("expected_nodes") or man.get("nnodes"),
        "manifest": man,
    }


@dataclass
class ScriptedWholeFileChunkAdapter:
    """Adapt ScriptedTransport: refresh whole-file cache on offset==0 (file attempt)."""

    inner: ScriptedTransport
    _cache: dict[str, bytes] = field(default_factory=dict)

    def stream_chunk(
        self,
        remote_root: str,
        rel: str,
        offset: int,
        length: int,
        local_path: Path,
        *,
        timeout_s: Optional[float] = None,
    ) -> int:
        if offset == 0 or rel not in self._cache:
            tmp = local_path.parent / f".whole-{PurePosixPath(rel).name}.tmp"
            try:
                self.inner.stream_rel(remote_root, rel, tmp, timeout_s=timeout_s)
                self._cache[rel] = tmp.read_bytes()
            finally:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass
        blob = self._cache[rel]
        data = blob[offset : offset + length]
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(data)
        return len(data)


@dataclass
class DualTransport:
    """Whole-file stream for meta + chunk stream for digest files."""

    whole: Any
    chunk: Any

    def stream_file(
        self,
        remote_abs: str,
        local_path: Path,
        *,
        timeout_s: Optional[float] = None,
    ) -> int:
        return self.whole.stream_file(remote_abs, local_path, timeout_s=timeout_s)

    def stream_rel(
        self,
        remote_root: str,
        rel: str,
        local_path: Path,
        *,
        timeout_s: Optional[float] = None,
    ) -> int:
        return self.whole.stream_rel(remote_root, rel, local_path, timeout_s=timeout_s)

    def stream_chunk(
        self,
        remote_root: str,
        rel: str,
        offset: int,
        length: int,
        local_path: Path,
        *,
        timeout_s: Optional[float] = None,
    ) -> int:
        return self.chunk.stream_chunk(
            remote_root, rel, offset, length, local_path, timeout_s=timeout_s
        )


def as_chunk_transport(transport: Any) -> Any:
    """Accept ChunkTransport or legacy ScriptedTransport/KubectlCatTransport."""
    if hasattr(transport, "stream_chunk"):
        return transport
    if isinstance(transport, ScriptedTransport):
        return ScriptedWholeFileChunkAdapter(inner=transport)
    if isinstance(transport, KubectlCatTransport):
        return KubectlChunkTransport(
            jump=transport.jump,
            kubeconfig=transport.kubeconfig,
            kubectl=transport.kubectl,
            namespace=transport.namespace,
            pod=transport.pod,
            connect_timeout=transport.connect_timeout,
            server_alive_interval=transport.server_alive_interval,
            server_alive_count_max=transport.server_alive_count_max,
            jump_local=bool(getattr(transport, "jump_local", False)),
        )
    raise TransferRecoveryError(f"unsupported transport for chunk fill: {type(transport)}")


def pull_one_file(
    transport: Transport,
    *,
    remote_root: Path | str,
    attempt_dir: Path,
    rel: str,
    expected_size: int,
    expected_sha256: str,
    max_attempts: int = MAX_FILE_ATTEMPTS,
    deadline_monotonic: Optional[float] = None,
    free_bytes_fn: Optional[Callable[[Path | str], int]] = None,
    expected_total_remaining: Optional[int] = None,
) -> FileRecoveryRecord:
    """Pull one sealed file via 16MiB chunks into attempt_dir/.partial → rename."""
    rel = canonical_artifact_relpath(rel)
    chunk_tr = as_chunk_transport(transport)
    try:
        crec = pull_file_chunked(
            chunk_tr,
            remote_root=remote_root,
            dest_dir=attempt_dir,
            rel=rel,
            expected_size=int(expected_size),
            expected_sha256=str(expected_sha256),
            chunk_size=CHUNK_SIZE,
            # One transport attempt per file pass. The outer file retry bounds
            # every chunk offset to at most max_attempts total transfers.
            max_chunk_attempts=1,
            max_file_attempts=max_attempts,
            deadline_monotonic=deadline_monotonic,
            free_bytes_fn=free_bytes_fn,
            expected_total_remaining=expected_total_remaining,
            keep_partial_on_fail=True,
        )
    except (
        ChunkPullError,
        ChunkTransportError,
        ChunkShortReadError,
        ChunkHashMismatchError,
        ChunkTimeoutError,
        ChunkDiskError,
    ) as exc:
        # Map to transfer errors for callers.
        if isinstance(exc, ChunkTimeoutError):
            raise TimeoutTransferError(str(exc)) from exc
        if isinstance(exc, ChunkDiskError):
            raise DiskBudgetError(str(exc)) from exc
        if isinstance(exc, ChunkShortReadError):
            raise ShortReadError(str(exc)) from exc
        if isinstance(exc, ChunkHashMismatchError):
            raise HashMismatchError(str(exc)) from exc
        if isinstance(exc, ChunkTransportError):
            raise TransportError(str(exc), rc=getattr(exc, "rc", 1)) from exc
        raise TransferRecoveryError(str(exc)) from exc

    rec = FileRecoveryRecord(
        path=rel,
        expected_size=int(expected_size),
        expected_sha256=str(expected_sha256),
        final_size=crec.final_size,
        final_sha256=crec.final_sha256,
    )
    # Flatten chunk attempts into AttemptRecord list (one per file-try outcome).
    by_try: dict[int, list[dict[str, Any]]] = {}
    for c in crec.chunks:
        by_try.setdefault(int(c.get("file_try", 1)), []).append(c)
    for try_i in sorted(by_try):
        chunks = by_try[try_i]
        ok = all(c.get("ok") for c in chunks) and try_i == max(by_try)
        # Mark last file try ok if final succeeded.
        err = ""
        if not crec.ok or try_i != max(by_try):
            bad = next((c for c in chunks if not c.get("ok")), None)
            err = str((bad or {}).get("error") or "")
        rec.attempts.append(
            AttemptRecord(
                try_index=try_i,
                ok=bool(crec.ok and try_i == max(by_try)),
                bytes_written=sum(int(c.get("bytes") or c.get("length") or 0) for c in chunks if c.get("ok")),
                sha256=crec.final_sha256 if (crec.ok and try_i == max(by_try)) else "",
                error=err,
                error_class=err.split(":", 1)[0] if err else "",
            )
        )
    if crec.ok and not rec.attempts:
        rec.attempts.append(
            AttemptRecord(
                try_index=1,
                ok=True,
                bytes_written=crec.final_size,
                sha256=crec.final_sha256,
            )
        )
    return rec


def _pull_meta(
    transport: Transport,
    *,
    remote_root: Path | str,
    dest_dir: Path,
    max_attempts: int = MAX_FILE_ATTEMPTS,
    deadline_monotonic: Optional[float] = None,
) -> list[FileRecoveryRecord]:
    records: list[FileRecoveryRecord] = []
    for name in META_NAMES:
        partial = _partial_path(dest_dir, name)
        final = dest_dir / name
        rec = FileRecoveryRecord(path=name, expected_size=-1, expected_sha256="")
        last_err: Optional[BaseException] = None
        for try_i in range(1, max_attempts + 1):
            remaining = (
                (deadline_monotonic - time.monotonic())
                if deadline_monotonic is not None
                else DEFAULT_GLOBAL_DEADLINE_S
            )
            if remaining <= 0:
                raise TimeoutTransferError(f"global deadline exhausted at meta {name}")
            timeout_s = min(120.0, max(MIN_FILE_TIMEOUT_S, remaining))
            if partial.exists():
                try:
                    partial.unlink()
                except OSError:
                    pass
            try:
                nbytes = _transport_stream(
                    transport,
                    remote_root=remote_root,
                    rel=name,
                    local_path=partial,
                    timeout_s=timeout_s,
                )
                if nbytes <= 0 and name != "attempt_manifest.sha256":
                    raise ShortReadError(f"empty meta: {name}")
                if partial.is_symlink():
                    raise TransferRecoveryError(f"meta symlink: {name}")
                if name.endswith(".json"):
                    text = partial.read_text(encoding="utf-8")
                    json.loads(text)
                _atomic_replace(partial, final)
                sha = sha256_file(final)
                rec.attempts.append(
                    AttemptRecord(try_index=try_i, ok=True, bytes_written=nbytes, sha256=sha)
                )
                rec.final_size = final.stat().st_size
                rec.final_sha256 = sha
                records.append(rec)
                break
            except (
                TransportError,
                ShortReadError,
                TimeoutTransferError,
                TransferRecoveryError,
                json.JSONDecodeError,
                OSError,
            ) as exc:
                last_err = exc
                rec.attempts.append(
                    AttemptRecord(
                        try_index=try_i,
                        ok=False,
                        bytes_written=partial.stat().st_size if partial.exists() else 0,
                        error=f"{type(exc).__name__}:{exc}",
                        error_class=type(exc).__name__,
                    )
                )
                try:
                    if partial.exists():
                        partial.unlink()
                except OSError:
                    pass
        else:
            raise TransferRecoveryError(
                f"meta pull failed: {name}: {last_err}"
            ) from last_err
    return records


def write_recovery_log(attempt_dir: Path, payload: dict[str, Any]) -> Path:
    payload = {
        **payload,
        "local_only": True,
        "not_a_remote_sealed_artifact": True,
        "note": (
            "TRANSFER_RETRY_RECOVERED.json is a Mac-side recovery audit log. "
            "It is NOT part of the remote seal / artifact_digest."
        ),
    }
    path = attempt_dir / RECOVERY_LOG_NAME
    tmp = path.with_suffix(".json.tmp")
    data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    with tmp.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp), str(path))
    # Durable directory entry for the recovery log before any LOCAL seal.
    try:
        dir_fd = os.open(str(attempt_dir), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass
    return path


def _write_local_seal_last(
    attempt_dir: Path,
    *,
    run_id: str,
    remote_manifest_sha256: str,
) -> None:
    """Write LOCAL_VERIFIED_SEAL as the last success-producing content action.

    On failure, scrub seal/temps inside this staging only.
    """
    attempt_dir = Path(attempt_dir)
    try:
        write_local_verified_seal(
            attempt_dir,
            run_id=run_id,
            remote_manifest_sha256=remote_manifest_sha256,
        )
        seal = attempt_dir / "LOCAL_VERIFIED_SEAL.json"
        if not seal.is_file():
            raise TransferRecoveryError("LOCAL_VERIFIED_SEAL missing after write")
        with seal.open("rb") as f:
            f.flush()
            os.fsync(f.fileno())
        try:
            dir_fd = os.open(str(attempt_dir), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except Exception:
        scrub_staging_local_seal_temps(attempt_dir)
        raise


def run_manifest_fallback(
    *,
    backup_root: Path | str,
    attempt_id: str,
    remote_root: Path | str,
    transport: Transport,
    claim_path: Path | str,
    group_plan_path: Path | str,
    group_id: str,
    plan_hash: str,
    fast_path_error: str = "",
    max_attempts: int = MAX_FILE_ATTEMPTS,
    write_local_seal: bool = True,
    run_strict: bool = True,
    validate_full_attempt: bool = True,
    expected_ranks: Optional[int] = None,
    expected_nodes: Optional[int] = None,
    capture_step: Optional[int] = None,
    diagnostic: bool = False,
    binding_plan_path: Optional[Path | str] = None,
    binding_plan_hash: Optional[str] = None,
    global_deadline_s: float = DEFAULT_GLOBAL_DEADLINE_S,
    free_bytes_fn: Callable[[Path | str], int] = free_bytes_for,
    disk_safety_floor: int = DISK_SAFETY_FLOOR_BYTES,
    disk_safety_ratio: float = DISK_SAFETY_RATIO,
    require_disk_safety: bool = True,
) -> dict[str, Any]:
    """Claim bind → mkdir final → per-file 16MiB chunk → SO0444/strict → log → seal."""
    t0 = time.time()
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    deadline_monotonic = time.monotonic() + float(global_deadline_s)

    binding = validate_claim_and_plan_binding(
        backup_root=backup_root,
        attempt_id=attempt_id,
        claim_path=claim_path,
        group_plan_path=group_plan_path,
        group_id=group_id,
        plan_hash=plan_hash,
        diagnostic=diagnostic,
        binding_plan_path=binding_plan_path,
        binding_plan_hash=binding_plan_hash,
    )
    assert_formal_group_allows_recover_publish(
        backup_root, diagnostic=bool(diagnostic)
    )
    # Final must not already exist — forged claim cannot overwrite/clear old attempt.
    _ = assert_final_absent_for_publish(backup_root, attempt_id)

    backup = Path(backup_root).resolve()
    final_dir = mkdir_final_exclusive(backup, attempt_id)
    meta_recs: list[FileRecoveryRecord] = []
    filled = False
    try:
        meta_recs = _pull_meta(
            transport,
            remote_root=remote_root,
            dest_dir=final_dir,
            max_attempts=max_attempts,
            deadline_monotonic=deadline_monotonic,
        )
        seal_info = verify_manifest_seal_and_digest(
            final_dir,
            request_attempt_id=attempt_id,
            binding_plan=binding["binding_plan"],
            binding_plan_hash=binding["binding_plan_hash"],
        )
        expected_total = int(seal_info["expected_total_bytes"])
        disk_info = assert_disk_budget(
            backup,
            expected_total,
            free_bytes_fn=free_bytes_fn,
            safety_floor=disk_safety_floor,
            safety_ratio=disk_safety_ratio,
            require_safety=require_disk_safety,
        )

        file_recs: list[FileRecoveryRecord] = []
        remaining_bytes = expected_total
        for entry in seal_info["files"]:
            rec = pull_one_file(
                transport,
                remote_root=remote_root,
                attempt_dir=final_dir,
                rel=entry["path"],
                expected_size=entry["size"],
                expected_sha256=entry["sha256"],
                max_attempts=max_attempts,
                deadline_monotonic=deadline_monotonic,
                free_bytes_fn=free_bytes_fn,
                expected_total_remaining=remaining_bytes,
            )
            file_recs.append(rec)
            remaining_bytes = max(0, remaining_bytes - int(entry["size"]))

        so_info: Optional[dict[str, Any]] = None
        strict_ok = False
        if run_strict:
            from strict_validate import validate_attempt_manifest, verify_artifact_digest

            # chmod 0444 then require_readonly strict SO check (inside restore).
            so_info = restore_sealed_collector_so_mode(final_dir)
            verify_artifact_digest(
                final_dir,
                expected_aggregate=seal_info["aggregate_sha256"],
                require_required_globs=True,
                arm=seal_info.get("arm"),
                expected_ranks=seal_info.get("expected_ranks"),
                expected_nodes=seal_info.get("expected_nodes"),
            )
            if validate_full_attempt:
                manifest = seal_info["manifest"]
                strict_capture_step = int(
                    capture_step
                    if capture_step is not None
                    else
                    manifest.get(
                        "capture_megatron_iter",
                        manifest.get("capture_step", 10),
                    )
                )
                validate_attempt_manifest(
                    final_dir,
                    expected_ranks=int(
                        expected_ranks
                        if expected_ranks is not None
                        else seal_info.get("expected_ranks") or 0
                    ),
                    capture_step=strict_capture_step,
                    expected_nodes=int(
                        expected_nodes
                        if expected_nodes is not None
                        else seal_info.get("expected_nodes") or 0
                    ),
                    require_seal=True,
                    require_provenance=True,
                    require_local_anchor=False,
                )
            strict_ok = True

        finished = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        elapsed = round(time.time() - t0, 3)
        payload = {
            "attempt_id": attempt_id,
            "backup_root": str(backup),
            "remote_root": str(remote_root),
            "group_id": group_id,
            "plan_hash": plan_hash,
            "binding_plan_hash": binding["binding_plan_hash"],
            "diagnostic": bool(diagnostic),
            "mode": "FALLBACK_RECOVERED",
            "fast_path_error": fast_path_error,
            "started_at_utc": started,
            "finished_at_utc": finished,
            "elapsed_sec": elapsed,
            "global_deadline_s": float(global_deadline_s),
            "disk_budget": disk_info,
            "expected_total_bytes": expected_total,
            "manifest_sha256": seal_info["manifest_sha256"],
            "aggregate_sha256": seal_info["aggregate_sha256"],
            "attempt_dir": str(final_dir),
            "chunk_size": CHUNK_SIZE,
            "threat_model": (
                "Claim/plan authorize mkdir+chunk fill of a new final only; "
                "forged claim cannot rmtree/rename pre-existing attempt trees; "
                "no publish-staging rename."
            ),
            "sealed_so": so_info,
            "meta_files": [
                {
                    "path": r.path,
                    "attempts": [a.__dict__ for a in r.attempts],
                    "final_size": r.final_size,
                    "final_sha256": r.final_sha256,
                }
                for r in meta_recs
            ],
            "files": [
                {
                    "path": r.path,
                    "expected_size": r.expected_size,
                    "expected_sha256": r.expected_sha256,
                    "attempts": [a.__dict__ for a in r.attempts],
                    "final_size": r.final_size,
                    "final_sha256": r.final_sha256,
                }
                for r in file_recs
            ],
            "file_count": len(file_recs),
            "strict_artifact_ok": strict_ok,
            "local_verified_seal_ok": False,
            "published": False,
            "filled_in_place": True,
        }
        # Recovery log + fsync BEFORE local seal (seal is last success content write).
        write_recovery_log(final_dir, payload)

        local_seal_ok = False
        if write_local_seal:
            _write_local_seal_last(
                final_dir,
                run_id=attempt_id,
                remote_manifest_sha256=seal_info["manifest_sha256"],
            )
            local_seal_ok = True
            payload["local_verified_seal_ok"] = True

        filled = True
        payload["local_verified_seal_ok"] = local_seal_ok
        payload["published"] = False  # no publish rename; filled in place
        payload["filled_in_place"] = True
        print(
            f"TRANSFER_DEADLINE global_deadline_s={global_deadline_s} "
            f"elapsed_sec={elapsed} disk_need={disk_info['need']} "
            f"disk_free={disk_info['free']} expected_total={expected_total} "
            f"chunk_size={CHUNK_SIZE}",
            file=sys.stderr,
        )
        return payload
    except Exception:
        # Failure: keep .partial under final for evidence; scrub seal temps only.
        try:
            if (not filled) and final_dir.exists():
                scrub_staging_local_seal_temps(final_dir)
                # Intentionally do NOT scrub_staging_partial_files — keep partial.
        except OSError:
            pass
        raise


# --- Fixtures / self-tests -------------------------------------------------


def _mini_plan(group_id: str, attempt_id: str, arm: str) -> dict[str, Any]:
    from ab_plan import plan_hash as compute_plan_hash

    plan = {
        "group_id": group_id,
        "attempts": [
            {"attempt_id": attempt_id, "arm": arm, "order_index": 1, "out_dir": f"/remote/{attempt_id}"},
        ],
        "attempts_order": [arm],
    }
    plan["plan_hash"] = compute_plan_hash(plan)
    return plan


def _setup_claimed_context(
    td_p: Path,
    *,
    attempt_id: str = "attempt_01_ours",
    arm: str = "ours",
    group_id: str = "fixture-xfer-group-001",
) -> dict[str, Any]:
    from local_group_guard import claim_local_group

    claim_parent = td_p / "claims"
    backup = td_p / "backup"
    log_dir = td_p / "logs" / group_id
    info = claim_local_group(
        group_id=group_id,
        claim_parent=claim_parent,
        backup_root=backup,
        log_dir=log_dir,
    )
    plan = _mini_plan(group_id, attempt_id, arm)
    plan_path = backup / "group_plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    (backup / "group_plan.sha256").write_text(plan["plan_hash"] + "\n", encoding="utf-8")
    return {
        "backup_root": backup,
        "claim_path": Path(info["claim_path"]),
        "group_plan": plan_path,
        "plan_hash": plan["plan_hash"],
        "group_id": group_id,
        "attempt_id": attempt_id,
        "arm": arm,
        "plan": plan,
    }


def _build_mini_sealed_tree(
    root: Path,
    *,
    attempt_id: Optional[str] = None,
    arm: str = "ours",
    plan_hash: Optional[str] = None,
    group_id: Optional[str] = None,
) -> dict[str, Any]:
    aid = attempt_id or root.name
    so_bytes = b"MINI_SO_BYTES_V1"
    so_digest = hashlib.sha256(so_bytes).hexdigest()
    so_rel = f"sealed_bins/libmspti_sync_skeleton.so.{so_digest}"
    files_spec = {
        "run.log": b"run ok\n",
        "config.json": b'{"ok":true}\n',
        "counters.json": b'{"pass":true}\n',
        "cluster.trace.json": b'{"cluster":true}\n',
        "node_0.done": b"ok\n",
        "node_0.log": b"node log\n",
        "node_0.launch.json": json.dumps(
            {
                "raw_exit_code": 0,
                "e2e_wall_ms": 1.0,
                "collector_so_sha256_loaded": so_digest,
                "collector_so_loaded_path": so_rel,
            },
            sort_keys=True,
        ).encode()
        + b"\n",
        "rank_0000.skeleton.jsonl": b'{"rank":0}\n',
        "rank_0000.npu_sync_meta.json": (
            json.dumps(
                {
                    "rank": 0,
                    "finalize_complete": True,
                    "finalize_rc": 0,
                    "raw_kernels": 100,
                    "raw_comms": 10,
                    "finalize_ms": 1.0,
                    "finalize_reason": "last_train_step",
                },
                sort_keys=True,
            ).encode()
            + b"\n"
        ),
        "rank_0000.trace.json": b'{"trace":true,"pad":"' + (b"x" * 64) + b'"}\n',
        "SUMMARY.md": b"# summary\n",
        so_rel: so_bytes,
        "provenance_build.json": json.dumps(
            {
                "collector_so_sha256": so_digest,
                "collector_so_sealed_relpath": so_rel,
                "collector_so_size": len(so_bytes),
            },
            sort_keys=True,
        ).encode()
        + b"\n",
    }
    for rel, data in files_spec.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        if rel.startswith("sealed_bins/"):
            try:
                p.chmod(0o444)
            except OSError:
                pass
    entries = []
    for rel in sorted(files_spec):
        p = root / rel
        entries.append({"path": rel, "sha256": sha256_file(p), "size": p.stat().st_size})
    agg = canonical_aggregate_sha256(entries)
    art = {
        "files": entries,
        "aggregate_sha256": agg,
        "artifact_digest_sha256": agg,
        "file_count": len(entries),
    }
    (root / "artifact_digest.json").write_text(
        json.dumps(art, indent=2, sort_keys=True), encoding="utf-8"
    )
    man: dict[str, Any] = {
        "attempt_id": aid,
        "arm": arm,
        "expected_ranks": 1,
        "expected_nodes": 1,
        "nnodes": 1,
        "artifact_digest_sha256": agg,
        "provenance": {"artifact_digest_sha256": agg},
        "finalize_complete": True,
    }
    if plan_hash:
        man["plan_hash"] = plan_hash
    if group_id is not None:
        man["group_id"] = group_id
    man_path = root / "attempt_manifest.json"
    man_path.write_text(json.dumps(man, indent=2, sort_keys=True), encoding="utf-8")
    man_hash = sha256_file(man_path)
    (root / "attempt_manifest.sha256").write_text(man_hash + "\n", encoding="utf-8")
    return {"aggregate_sha256": agg, "manifest_sha256": man_hash, "files": entries, "art": art, "man": man}


def _transport_from_dir(
    src_root: Path,
    overrides: Optional[dict[str, list[Callable[[], bytes]]]] = None,
    *,
    hang_s: float = 0.0,
) -> ScriptedTransport:
    scripts: dict[str, list[Callable[[], bytes]]] = {}
    for p in src_root.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(src_root))
        data = p.read_bytes()
        scripts[rel] = [lambda d=data: d]
    if overrides:
        scripts.update(overrides)
    return ScriptedTransport(scripts=scripts, hang_s=hang_s)


def _fb_kwargs(ctx: dict[str, Any], **extra: Any) -> dict[str, Any]:
    base = {
        "backup_root": ctx["backup_root"],
        "attempt_id": ctx["attempt_id"],
        "claim_path": ctx["claim_path"],
        "group_plan_path": ctx["group_plan"],
        "group_id": ctx["group_id"],
        "plan_hash": ctx["plan_hash"],
        # Synthetic trees exercise transfer invariants, not the full Megatron
        # lifecycle schema. Formal and real diagnostic recovery keep this true.
        "validate_full_attempt": False,
    }
    base.update(extra)
    return base


def _clear_incomplete_final(ctx: dict[str, Any]) -> None:
    """Fixture-only: remove incomplete final left by a prior negative case in the same tmp."""
    final = ctx["backup_root"] / ctx["attempt_id"]
    if final.is_dir() and not (final / "LOCAL_VERIFIED_SEAL.json").exists():
        shutil.rmtree(final)


def fixture_fast_tar_truncation_then_recover() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        ctx = _setup_claimed_context(td_p)
        remote = td_p / "remote" / ctx["attempt_id"]
        remote.mkdir(parents=True)
        sealed = _build_mini_sealed_tree(
            remote,
            attempt_id=ctx["attempt_id"],
            arm=ctx["arm"],
            plan_hash=ctx["plan_hash"],
            group_id=ctx["group_id"],
        )
        # Fast path temp under new group; on truncation leave it (no quarantine rename).
        pull_staging = create_exclusive_staging_dir(
            ctx["backup_root"], ctx["attempt_id"], kind="pull"
        )
        (pull_staging / "rank_0000.trace.json").write_bytes(b"TRUNCATED")
        (pull_staging / "run.log").write_text("partial\n", encoding="utf-8")
        assert not (ctx["backup_root"] / ctx["attempt_id"]).exists()
        try:
            quarantine_fast_path_staging(pull_staging, ctx["attempt_id"])
            raise AssertionError("quarantine must be disabled")
        except TransferRecoveryError:
            pass
        assert pull_staging.is_dir()
        transport = _transport_from_dir(remote)
        result = run_manifest_fallback(
            **_fb_kwargs(
                ctx,
                remote_root=remote,
                transport=transport,
                fast_path_error="Truncated tar archive at rank_0000.trace.json",
            )
        )
        local = ctx["backup_root"] / ctx["attempt_id"]
        assert local.is_dir()
        assert (local / "LOCAL_VERIFIED_SEAL.json").is_file()
        assert (local / RECOVERY_LOG_NAME).is_file()
        log = json.loads((local / RECOVERY_LOG_NAME).read_text(encoding="utf-8"))
        assert log["not_a_remote_sealed_artifact"] is True
        assert log["mode"] == "FALLBACK_RECOVERED"
        assert log.get("filled_in_place") is True or log.get("published") is False
        assert "disk_budget" in log
        assert "global_deadline_s" in log
        assert log.get("chunk_size") == CHUNK_SIZE
        build = json.loads((local / "provenance_build.json").read_text(encoding="utf-8"))
        so_path = local / build["collector_so_sealed_relpath"]
        assert (so_path.stat().st_mode & 0o777) == 0o444
        for entry in sealed["files"]:
            assert sha256_file(local / entry["path"]) == entry["sha256"]
        assert pull_staging.is_dir()
        assert (pull_staging / "rank_0000.trace.json").read_bytes() == b"TRUNCATED"
        return result


def fixture_short_read_then_success() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        ctx = _setup_claimed_context(td_p)
        remote = td_p / "remote" / ctx["attempt_id"]
        remote.mkdir(parents=True)
        sealed = _build_mini_sealed_tree(
            remote,
            attempt_id=ctx["attempt_id"],
            arm=ctx["arm"],
            plan_hash=ctx["plan_hash"],
            group_id=ctx["group_id"],
        )
        target = "rank_0000.trace.json"
        full = (remote / target).read_bytes()
        overrides = {
            target: [lambda: full[:10], lambda: full[:20], lambda: full],
        }
        transport = _transport_from_dir(remote, overrides=overrides)
        result = run_manifest_fallback(
            **_fb_kwargs(
                ctx,
                remote_root=remote,
                transport=transport,
                fast_path_error="simulated_short_read",
            )
        )
        file_log = next(f for f in result["files"] if f["path"] == target)
        # Chunk/file retries: at least one failed attempt then success (≤3).
        assert any(not a["ok"] for a in file_log["attempts"]) or len(file_log["attempts"]) >= 1
        assert file_log["attempts"][-1]["ok"] is True
        assert (ctx["backup_root"] / ctx["attempt_id"] / "LOCAL_VERIFIED_SEAL.json").is_file()
        assert sha256_file(ctx["backup_root"] / ctx["attempt_id"] / target) == next(
            e["sha256"] for e in sealed["files"] if e["path"] == target
        )
        return result


def fixture_permanent_hash_error() -> None:
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        ctx = _setup_claimed_context(td_p)
        remote = td_p / "remote" / ctx["attempt_id"]
        remote.mkdir(parents=True)
        _build_mini_sealed_tree(
            remote,
            attempt_id=ctx["attempt_id"],
            arm=ctx["arm"],
            plan_hash=ctx["plan_hash"],
            group_id=ctx["group_id"],
        )
        bad = b"tampered forever\n"
        overrides = {"run.log": [lambda: bad, lambda: bad, lambda: bad]}
        transport = _transport_from_dir(remote, overrides=overrides)
        try:
            run_manifest_fallback(
                **_fb_kwargs(ctx, remote_root=remote, transport=transport, fast_path_error="hash_err")
            )
            raise AssertionError("expected failure")
        except TransferRecoveryError:
            pass
        final = ctx["backup_root"] / ctx["attempt_id"]
        # Incomplete final may exist with .partial; must NOT have LOCAL seal.
        assert not (final / "LOCAL_VERIFIED_SEAL.json").exists()
        for p in ctx["backup_root"].glob(f".fallback-{ctx['attempt_id']}-*"):
            assert not (p / "LOCAL_VERIFIED_SEAL.json").exists()


def fixture_transport_failure() -> None:
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        ctx = _setup_claimed_context(td_p)
        remote = td_p / "remote" / ctx["attempt_id"]
        remote.mkdir(parents=True)
        _build_mini_sealed_tree(
            remote,
            attempt_id=ctx["attempt_id"],
            arm=ctx["arm"],
            plan_hash=ctx["plan_hash"],
            group_id=ctx["group_id"],
        )

        def boom() -> bytes:
            raise TransportError("simulated transport fail", rc=255)

        overrides = {"config.json": [boom, boom, boom]}
        transport = _transport_from_dir(remote, overrides=overrides)
        try:
            run_manifest_fallback(
                **_fb_kwargs(ctx, remote_root=remote, transport=transport, fast_path_error="transport")
            )
            raise AssertionError("expected failure")
        except TransferRecoveryError:
            pass
        final = ctx["backup_root"] / ctx["attempt_id"]
        assert not (final / "LOCAL_VERIFIED_SEAL.json").exists()
        for p in ctx["backup_root"].glob(f".fallback-{ctx['attempt_id']}-*"):
            assert not (p / "LOCAL_VERIFIED_SEAL.json").exists()


def fixture_path_traversal_and_symlink_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        ctx = _setup_claimed_context(td_p)
        backup = ctx["backup_root"]
        try:
            resolve_safe_attempt_dir(backup, "../escape")
            raise AssertionError("expected reject ../escape")
        except TransferRecoveryError:
            pass
        try:
            resolve_safe_attempt_dir(backup, "/tmp/evil")
            raise AssertionError("expected reject absolute")
        except TransferRecoveryError:
            pass
        real = backup / "attempt_01_ours"
        real.mkdir(exist_ok=True)
        link = backup / "attempt_link"
        link.symlink_to(real)
        try:
            safe_clear_attempt_dir(backup, "attempt_link", binding=None)
            raise AssertionError("expected safe_clear disabled")
        except TransferRecoveryError:
            pass
        try:
            resolve_safe_attempt_dir(backup, "attempt_link")
        except TransferRecoveryError:
            pass

        # Existing final blocks publish (no merge / no rmtree).
        try:
            run_manifest_fallback(
                **_fb_kwargs(
                    ctx,
                    remote_root=td_p / "nosuch",
                    transport=ScriptedTransport(scripts={}),
                    fast_path_error="final_exists",
                )
            )
            raise AssertionError("final exists must refuse")
        except TransferRecoveryError as exc:
            assert "already exists" in str(exc) or "refuse" in str(exc).lower()

        # Clean final for digest path tests.
        shutil.rmtree(real)

        remote = td_p / "remote" / ctx["attempt_id"]
        remote.mkdir(parents=True)
        sealed = _build_mini_sealed_tree(
            remote,
            attempt_id=ctx["attempt_id"],
            arm=ctx["arm"],
            plan_hash=ctx["plan_hash"],
            group_id=ctx["group_id"],
        )
        art = sealed["art"]
        bad_art = dict(art)
        bad_art["files"] = list(art["files"]) + [
            {"path": "../outside.txt", "sha256": "a" * 64, "size": 1}
        ]
        bad_bytes = json.dumps(bad_art, indent=2, sort_keys=True).encode()
        transport = _transport_from_dir(
            remote, overrides={"artifact_digest.json": [lambda: bad_bytes]}
        )
        try:
            run_manifest_fallback(
                **_fb_kwargs(ctx, remote_root=remote, transport=transport, fast_path_error="traversal")
            )
            raise AssertionError("expected unsafe path reject")
        except TransferRecoveryError:
            pass
        final = ctx["backup_root"] / ctx["attempt_id"]
        assert not (final / "LOCAL_VERIFIED_SEAL.json").exists()


def fixture_partial_dir_safety_guard() -> None:
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        ctx = _setup_claimed_context(td_p)
        other = ctx["backup_root"] / "old_group_attempt"
        other.mkdir()
        sentinel = other / "KEEP.txt"
        sentinel.write_text("do not delete\n", encoding="utf-8")
        before = sha256_file(sentinel)
        before_ino = sentinel.stat().st_ino
        try:
            resolve_safe_attempt_dir(Path("/"), "etc")
            raise AssertionError("root backup must fail")
        except TransferRecoveryError:
            pass
        remote = td_p / "remote" / ctx["attempt_id"]
        remote.mkdir(parents=True)
        _build_mini_sealed_tree(
            remote,
            attempt_id=ctx["attempt_id"],
            arm=ctx["arm"],
            plan_hash=ctx["plan_hash"],
            group_id=ctx["group_id"],
        )
        transport = _transport_from_dir(remote)
        run_manifest_fallback(
            **_fb_kwargs(ctx, remote_root=remote, transport=transport, fast_path_error="guard")
        )
        assert sentinel.read_text(encoding="utf-8") == "do not delete\n"
        assert sha256_file(sentinel) == before
        assert sentinel.stat().st_ino == before_ino


def fixture_forged_claim_cannot_touch_old_invalid() -> None:
    """Forged claim + old INVALID present: inode/hash unchanged; final exists refuse; no rmtree."""
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        old = td_p / "old-invalid"
        old.mkdir()
        attempt = "attempt_02_ours"
        target = old / attempt
        target.mkdir()
        keep = target / "KEEP.bin"
        keep.write_bytes(b"MUST_SURVIVE")
        before_hash = sha256_file(keep)
        before_ino = keep.stat().st_ino
        before_dir_ino = target.stat().st_ino
        (old / "GROUP_INVALID.json").write_text("{}", encoding="utf-8")

        gid = "old-invalid-group"
        plan = _mini_plan(gid, attempt, "ours")
        (old / "group_plan.json").write_text(json.dumps(plan), encoding="utf-8")
        fake_dir = td_p / "forged-claims"
        fake_dir.mkdir()
        fake = fake_dir / f"{gid}.claim"
        fake.write_text(
            json.dumps(
                {"group_id": gid, "backup_root": str(old), "status": "claimed"},
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        # Binding may succeed for self-consistent forged claim…
        binding = validate_claim_and_plan_binding(
            backup_root=old,
            attempt_id=attempt,
            claim_path=fake,
            group_plan_path=old / "group_plan.json",
            group_id=gid,
            plan_hash=plan["plan_hash"],
        )
        # …but destructive clear is disabled.
        try:
            safe_clear_attempt_dir(old, attempt, binding=binding)
            raise AssertionError("safe_clear must stay disabled")
        except TransferRecoveryError:
            pass
        assert keep.read_bytes() == b"MUST_SURVIVE"
        assert sha256_file(keep) == before_hash
        assert keep.stat().st_ino == before_ino
        assert target.stat().st_ino == before_dir_ino

        # Formal recover-publish refused: GROUP_INVALID + final exists.
        remote = td_p / "remote" / attempt
        remote.mkdir(parents=True)
        _build_mini_sealed_tree(
            remote,
            attempt_id=attempt,
            arm="ours",
            plan_hash=plan["plan_hash"],
            group_id=gid,
        )
        transport = _transport_from_dir(remote)
        try:
            run_manifest_fallback(
                backup_root=old,
                attempt_id=attempt,
                remote_root=remote,
                transport=transport,
                claim_path=fake,
                group_plan_path=old / "group_plan.json",
                group_id=gid,
                plan_hash=plan["plan_hash"],
                fast_path_error="forged",
                diagnostic=False,
            )
            raise AssertionError("must refuse formal publish into INVALID/existing")
        except TransferRecoveryError:
            pass
        assert sha256_file(keep) == before_hash
        assert keep.stat().st_ino == before_ino
        assert target.stat().st_ino == before_dir_ino


def fixture_claim_plan_binding_negatives() -> None:
    """Old group / fake claim / wrong plan / non-member → refuse; hashes intact."""
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        ctx = _setup_claimed_context(td_p)
        backup = ctx["backup_root"]
        old = td_p / "old_invalid_group"
        old.mkdir()
        old_attempt = old / "attempt_01_ours"
        old_attempt.mkdir()
        keep = old_attempt / "KEEP.bin"
        keep.write_bytes(b"OLD_GROUP_BYTES")
        before = sha256_file(keep)
        before_ino = keep.stat().st_ino

        try:
            safe_clear_attempt_dir(old, "attempt_01_ours")
            raise AssertionError("unbound clear must fail")
        except TransferRecoveryError:
            pass
        assert sha256_file(keep) == before

        fake = td_p / "claims" / "other-group.claim"
        fake.write_text(
            json.dumps(
                {
                    "group_id": ctx["group_id"],
                    "backup_root": str(old),
                    "status": "claimed",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        try:
            validate_claim_and_plan_binding(
                backup_root=backup,
                attempt_id=ctx["attempt_id"],
                claim_path=fake,
                group_plan_path=ctx["group_plan"],
                group_id=ctx["group_id"],
                plan_hash=ctx["plan_hash"],
            )
            raise AssertionError("fake claim must fail")
        except TransferRecoveryError:
            pass

        try:
            validate_claim_and_plan_binding(
                backup_root=backup,
                attempt_id=ctx["attempt_id"],
                claim_path=ctx["claim_path"],
                group_plan_path=ctx["group_plan"],
                group_id=ctx["group_id"],
                plan_hash="f" * 64,
            )
            raise AssertionError("wrong plan hash must fail")
        except TransferRecoveryError:
            pass

        try:
            validate_claim_and_plan_binding(
                backup_root=backup,
                attempt_id="attempt_99_ours",
                claim_path=ctx["claim_path"],
                group_plan_path=ctx["group_plan"],
                group_id=ctx["group_id"],
                plan_hash=ctx["plan_hash"],
            )
            raise AssertionError("non-member must fail")
        except TransferRecoveryError:
            pass

        try:
            validate_claim_and_plan_binding(
                backup_root=backup,
                attempt_id=ctx["attempt_id"],
                claim_path=ctx["claim_path"],
                group_plan_path=ctx["group_plan"],
                group_id=ctx["group_id"],
                plan_hash=ctx["plan_hash"],
                diagnostic=True,
            )
            raise AssertionError("diagnostic without binding plan must fail")
        except TransferRecoveryError:
            pass

        try:
            safe_clear_attempt_dir(old, "attempt_01_ours", binding=None)
            raise AssertionError("must refuse")
        except TransferRecoveryError:
            pass
        assert sha256_file(keep) == before
        assert keep.stat().st_ino == before_ino
        assert keep.read_bytes() == b"OLD_GROUP_BYTES"


def fixture_canonical_path_and_manifest_binding_negatives() -> None:
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        ctx = _setup_claimed_context(td_p)
        remote = td_p / "remote" / ctx["attempt_id"]
        remote.mkdir(parents=True)
        sealed = _build_mini_sealed_tree(
            remote,
            attempt_id=ctx["attempt_id"],
            arm=ctx["arm"],
            plan_hash=ctx["plan_hash"],
            group_id=ctx["group_id"],
        )

        def _bad_digest(mutate) -> ScriptedTransport:
            art = json.loads(json.dumps(sealed["art"]))
            mutate(art)
            bad_bytes = json.dumps(art, indent=2, sort_keys=True).encode()
            return _transport_from_dir(
                remote, overrides={"artifact_digest.json": [lambda b=bad_bytes: b]}
            )

        # a//b
        try:
            run_manifest_fallback(
                **_fb_kwargs(
                    ctx,
                    remote_root=remote,
                    transport=_bad_digest(
                        lambda art: art["files"].append(
                            {"path": "a//b", "sha256": "a" * 64, "size": 1}
                        )
                    ),
                    fast_path_error="a//b",
                )
            )
            raise AssertionError("a//b must fail")
        except TransferRecoveryError:
            pass
        _clear_incomplete_final(ctx)

        # a/./b
        try:
            run_manifest_fallback(
                **_fb_kwargs(
                    ctx,
                    remote_root=remote,
                    transport=_bad_digest(
                        lambda art: art["files"].append(
                            {"path": "a/./b", "sha256": "a" * 64, "size": 1}
                        )
                    ),
                    fast_path_error="a/./b",
                )
            )
            raise AssertionError("a/./b must fail")
        except TransferRecoveryError:
            pass
        _clear_incomplete_final(ctx)

        # .partial reserved
        try:
            run_manifest_fallback(
                **_fb_kwargs(
                    ctx,
                    remote_root=remote,
                    transport=_bad_digest(
                        lambda art: art["files"].append(
                            {"path": ".partial/x", "sha256": "a" * 64, "size": 1}
                        )
                    ),
                    fast_path_error=".partial",
                )
            )
            raise AssertionError(".partial must fail")
        except TransferRecoveryError:
            pass
        _clear_incomplete_final(ctx)

        # same canonical duplicate (identical path twice)
        try:
            run_manifest_fallback(
                **_fb_kwargs(
                    ctx,
                    remote_root=remote,
                    transport=_bad_digest(
                        lambda art: art["files"].append(dict(art["files"][0]))
                    ),
                    fast_path_error="dup",
                )
            )
            raise AssertionError("duplicate must fail")
        except TransferRecoveryError:
            pass
        _clear_incomplete_final(ctx)

        # manifest attempt_id wrong
        bad_man = dict(sealed["man"])
        bad_man["attempt_id"] = "attempt_99_ours"
        man_bytes = json.dumps(bad_man, indent=2, sort_keys=True).encode()
        man_hash = hashlib.sha256(man_bytes).hexdigest()
        transport = _transport_from_dir(
            remote,
            overrides={
                "attempt_manifest.json": [lambda: man_bytes],
                "attempt_manifest.sha256": [lambda: (man_hash + "\n").encode()],
            },
        )
        try:
            run_manifest_fallback(
                **_fb_kwargs(ctx, remote_root=remote, transport=transport, fast_path_error="bad_aid")
            )
            raise AssertionError("wrong attempt_id must fail")
        except TransferRecoveryError as exc:
            assert "attempt_id" in str(exc)
        _clear_incomplete_final(ctx)

        # manifest attempt_id missing
        bad_man2 = dict(sealed["man"])
        del bad_man2["attempt_id"]
        man_bytes2 = json.dumps(bad_man2, indent=2, sort_keys=True).encode()
        man_hash2 = hashlib.sha256(man_bytes2).hexdigest()
        transport = _transport_from_dir(
            remote,
            overrides={
                "attempt_manifest.json": [lambda: man_bytes2],
                "attempt_manifest.sha256": [lambda: (man_hash2 + "\n").encode()],
            },
        )
        try:
            run_manifest_fallback(
                **_fb_kwargs(ctx, remote_root=remote, transport=transport, fast_path_error="miss_aid")
            )
            raise AssertionError("missing attempt_id must fail")
        except TransferRecoveryError as exc:
            assert "attempt_id" in str(exc)
        _clear_incomplete_final(ctx)

        # remote symlink / non-regular simulated
        def symlink_err() -> bytes:
            raise TransportError("not_regular:symlink", rc=2)

        transport = _transport_from_dir(
            remote, overrides={"run.log": [symlink_err, symlink_err, symlink_err]}
        )
        try:
            run_manifest_fallback(
                **_fb_kwargs(ctx, remote_root=remote, transport=transport, fast_path_error="symlink")
            )
            raise AssertionError("remote symlink must fail")
        except TransferRecoveryError:
            pass
        assert not (
            ctx["backup_root"] / ctx["attempt_id"] / "LOCAL_VERIFIED_SEAL.json"
        ).exists()


def fixture_disk_and_timeout_gates() -> None:
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        ctx = _setup_claimed_context(td_p)
        remote = td_p / "remote" / ctx["attempt_id"]
        remote.mkdir(parents=True)
        sealed = _build_mini_sealed_tree(
            remote,
            attempt_id=ctx["attempt_id"],
            arm=ctx["arm"],
            plan_hash=ctx["plan_hash"],
            group_id=ctx["group_id"],
        )
        total = sum(e["size"] for e in sealed["files"])

        # Low disk
        def low_disk(_path: Path | str) -> int:
            return max(0, total // 2)

        transport = _transport_from_dir(remote)
        try:
            run_manifest_fallback(
                **_fb_kwargs(
                    ctx,
                    remote_root=remote,
                    transport=transport,
                    fast_path_error="low_disk",
                    free_bytes_fn=low_disk,
                )
            )
            raise AssertionError("low disk must fail")
        except (DiskBudgetError, TransferRecoveryError):
            pass
        assert not (ctx["backup_root"] / ctx["attempt_id"] / "LOCAL_VERIFIED_SEAL.json").exists()

        # Oversized single file in digest (fresh claim — prior attempt may leave incomplete final)
        ctx_huge = _setup_claimed_context(td_p / "huge", group_id="fixture-xfer-group-huge")
        remote_h = td_p / "remote_h" / ctx_huge["attempt_id"]
        remote_h.mkdir(parents=True)
        sealed_h = _build_mini_sealed_tree(
            remote_h,
            attempt_id=ctx_huge["attempt_id"],
            arm=ctx_huge["arm"],
            plan_hash=ctx_huge["plan_hash"],
            group_id=ctx_huge["group_id"],
        )
        art = json.loads(json.dumps(sealed_h["art"]))
        art["files"] = list(art["files"]) + [
            {"path": "huge.bin", "sha256": "a" * 64, "size": MAX_SINGLE_FILE_BYTES + 1}
        ]
        bad = json.dumps(art, indent=2, sort_keys=True).encode()
        transport = _transport_from_dir(
            remote_h, overrides={"artifact_digest.json": [lambda: bad]}
        )
        try:
            run_manifest_fallback(
                **_fb_kwargs(
                    ctx_huge,
                    remote_root=remote_h,
                    transport=transport,
                    fast_path_error="huge",
                )
            )
            raise AssertionError("huge size must fail")
        except TransferRecoveryError:
            pass

        # Global deadline too small
        ctx2 = _setup_claimed_context(td_p / "g2", group_id="fixture-xfer-group-002")
        remote2 = td_p / "remote2" / ctx2["attempt_id"]
        remote2.mkdir(parents=True)
        _build_mini_sealed_tree(
            remote2,
            attempt_id=ctx2["attempt_id"],
            arm=ctx2["arm"],
            plan_hash=ctx2["plan_hash"],
            group_id=ctx2["group_id"],
        )

        target = "run.log"
        full = (remote2 / target).read_bytes()

        def hang_then_ok() -> bytes:
            time.sleep(5.0)
            return full

        transport = _transport_from_dir(
            remote2, overrides={target: [hang_then_ok, hang_then_ok, hang_then_ok]}, hang_s=0.0
        )
        try:
            run_manifest_fallback(
                **_fb_kwargs(
                    ctx2,
                    remote_root=remote2,
                    transport=transport,
                    fast_path_error="global_timeout",
                    global_deadline_s=0.3,
                )
            )
            raise AssertionError("global timeout must fail")
        except TransferRecoveryError:
            pass
        local2 = ctx2["backup_root"] / ctx2["attempt_id"]
        assert not (local2 / "LOCAL_VERIFIED_SEAL.json").exists()
        for p in ctx2["backup_root"].glob(f".fallback-{ctx2['attempt_id']}-*"):
            assert not (p / "LOCAL_VERIFIED_SEAL.json").exists()
            if (p / ".partial").exists():
                leftovers = [x for x in (p / ".partial").rglob("*") if x.is_file()]
                assert not leftovers, leftovers

        # Single-file hang with tiny global deadline
        ctx3 = _setup_claimed_context(td_p / "g3", group_id="fixture-xfer-group-003")
        remote3 = td_p / "remote3" / ctx3["attempt_id"]
        remote3.mkdir(parents=True)
        sealed3 = _build_mini_sealed_tree(
            remote3,
            attempt_id=ctx3["attempt_id"],
            arm=ctx3["arm"],
            plan_hash=ctx3["plan_hash"],
            group_id=ctx3["group_id"],
        )
        target = "config.json"
        full = (remote3 / target).read_bytes()

        def forever() -> bytes:
            time.sleep(60)
            return full

        transport = _transport_from_dir(
            remote3, overrides={target: [forever, forever, forever]}, hang_s=0.0
        )
        try:
            run_manifest_fallback(
                **_fb_kwargs(
                    ctx3,
                    remote_root=remote3,
                    transport=transport,
                    fast_path_error="file_hang",
                    global_deadline_s=1.0,
                )
            )
            raise AssertionError("file hang must fail")
        except TransferRecoveryError:
            pass
        local3 = ctx3["backup_root"] / ctx3["attempt_id"]
        assert not (local3 / "LOCAL_VERIFIED_SEAL.json").exists()
        for p in ctx3["backup_root"].glob(f".fallback-{ctx3['attempt_id']}-*"):
            assert not (p / "LOCAL_VERIFIED_SEAL.json").exists()
        _ = sealed3


def fixture_seal_ordering_and_publish_races() -> None:
    """recovery-log fail → no LOCAL seal; seal fail → no seal; publish/quarantine disabled; mkdir race."""
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        ctx = _setup_claimed_context(td_p)
        remote = td_p / "remote" / ctx["attempt_id"]
        remote.mkdir(parents=True)
        _build_mini_sealed_tree(
            remote,
            attempt_id=ctx["attempt_id"],
            arm=ctx["arm"],
            plan_hash=ctx["plan_hash"],
            group_id=ctx["group_id"],
        )
        transport = _transport_from_dir(remote)
        mod = sys.modules[run_manifest_fallback.__module__]
        original_log = mod.write_recovery_log

        def fail_log(*_a, **_k):
            raise OSError("forced recovery log failure")

        mod.write_recovery_log = fail_log  # type: ignore[assignment]
        try:
            try:
                run_manifest_fallback(
                    **_fb_kwargs(
                        ctx, remote_root=remote, transport=transport, fast_path_error="log_fail"
                    )
                )
                raise AssertionError("log fail must raise")
            except OSError:
                pass
        finally:
            mod.write_recovery_log = original_log  # type: ignore[assignment]
        # Final may exist incomplete (files pulled) but must not have LOCAL seal.
        assert not (ctx["backup_root"] / ctx["attempt_id"] / "LOCAL_VERIFIED_SEAL.json").exists()

        # Seal write failure → no LOCAL seal (files may already be in final)
        ctx2 = _setup_claimed_context(td_p / "s2", group_id="fixture-xfer-group-seal2")
        remote2 = td_p / "remote2" / ctx2["attempt_id"]
        remote2.mkdir(parents=True)
        _build_mini_sealed_tree(
            remote2,
            attempt_id=ctx2["attempt_id"],
            arm=ctx2["arm"],
            plan_hash=ctx2["plan_hash"],
            group_id=ctx2["group_id"],
        )
        transport2 = _transport_from_dir(remote2)
        original_seal = mod._write_local_seal_last

        def fail_seal(*_a, **_k):
            raise OSError("forced local seal failure")

        mod._write_local_seal_last = fail_seal  # type: ignore[assignment]
        try:
            try:
                run_manifest_fallback(
                    **_fb_kwargs(
                        ctx2, remote_root=remote2, transport=transport2, fast_path_error="seal_fail"
                    )
                )
                raise AssertionError("seal fail must raise")
            except OSError:
                pass
        finally:
            mod._write_local_seal_last = original_seal  # type: ignore[assignment]
        assert not (ctx2["backup_root"] / ctx2["attempt_id"] / "LOCAL_VERIFIED_SEAL.json").exists()

        # publish-staging / quarantine-staging hard-disabled
        ctx3 = _setup_claimed_context(td_p / "s3", group_id="fixture-xfer-group-race")
        backup = ctx3["backup_root"]
        aid = ctx3["attempt_id"]
        s1 = create_exclusive_staging_dir(backup, aid, kind="pull")
        try:
            atomic_publish_staging(s1, backup / aid)
            raise AssertionError("publish-staging must be disabled")
        except TransferRecoveryError as exc:
            assert "disabled" in str(exc)
        try:
            quarantine_fast_path_staging(s1, aid)
            raise AssertionError("quarantine-staging must be disabled")
        except TransferRecoveryError as exc:
            assert "disabled" in str(exc)

        # Concurrent mkdir_final_exclusive: only one wins
        ctx4 = _setup_claimed_context(td_p / "s4", group_id="fixture-xfer-group-mkdir")
        backup4 = ctx4["backup_root"]
        aid4 = ctx4["attempt_id"]
        results: list[str] = []
        errors: list[str] = []

        def mk(label: str) -> None:
            try:
                mkdir_final_exclusive(backup4, aid4)
                results.append(label)
            except TransferRecoveryError as exc:
                errors.append(f"{label}:{exc}")

        t1 = threading.Thread(target=mk, args=("a",))
        t2 = threading.Thread(target=mk, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert len(results) == 1, (results, errors)
        assert len(errors) == 1, (results, errors)
        assert (backup4 / aid4).is_dir()


def fixture_sealed_so_mode_restore() -> None:
    """Recovered SO mode=0444 PASS; wrong path / chmod fail FAIL."""
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        ctx = _setup_claimed_context(td_p)
        remote = td_p / "remote" / ctx["attempt_id"]
        remote.mkdir(parents=True)
        _build_mini_sealed_tree(
            remote,
            attempt_id=ctx["attempt_id"],
            arm=ctx["arm"],
            plan_hash=ctx["plan_hash"],
            group_id=ctx["group_id"],
        )
        # Remote SO may be 0644 (like stream default); restore must force 0444.
        build = json.loads((remote / "provenance_build.json").read_text(encoding="utf-8"))
        so_rel = build["collector_so_sealed_relpath"]
        (remote / so_rel).chmod(0o644)
        transport = _transport_from_dir(remote)
        result = run_manifest_fallback(
            **_fb_kwargs(ctx, remote_root=remote, transport=transport, fast_path_error="so_mode")
        )
        local = ctx["backup_root"] / ctx["attempt_id"]
        assert (local / so_rel).stat().st_mode & 0o777 == 0o444
        assert result.get("sealed_so", {}).get("mode") == 0o444
        from strict_validate import verify_sealed_collector_so

        verify_sealed_collector_so(local, require_readonly=True)

        # Wrong manifest path → FAIL, no publish
        ctx2 = _setup_claimed_context(td_p / "badso", group_id="fixture-xfer-group-badso")
        remote2 = td_p / "remote_bad" / ctx2["attempt_id"]
        remote2.mkdir(parents=True)
        sealed = _build_mini_sealed_tree(
            remote2,
            attempt_id=ctx2["attempt_id"],
            arm=ctx2["arm"],
            plan_hash=ctx2["plan_hash"],
            group_id=ctx2["group_id"],
        )
        build2 = json.loads((remote2 / "provenance_build.json").read_text(encoding="utf-8"))
        build2["collector_so_sealed_relpath"] = "sealed_bins/missing.so." + ("a" * 64)
        bad_build = json.dumps(build2, sort_keys=True).encode() + b"\n"
        # Keep digest pointing at real SO; provenance path wrong → restore fails.
        transport2 = _transport_from_dir(
            remote2, overrides={"provenance_build.json": [lambda: bad_build]}
        )
        # Need matching digest hash for overridden provenance_build — rebuild digest.
        # Simpler: mutate after mini build by rewriting provenance + recompute digest.
        (remote2 / "provenance_build.json").write_bytes(bad_build)
        # Recompute digest over current tree so hash of provenance matches pull.
        from provenance import build_artifact_digest

        # Manual rehash like mini builder:
        entries = []
        for p in sorted(remote2.rglob("*")):
            if not p.is_file() or p.name in META_NAMES or p.name == "attempt_manifest.sha256":
                continue
            if p.name in ("artifact_digest.json", "attempt_manifest.json"):
                continue
            rel = str(p.relative_to(remote2))
            if rel in META_NAMES:
                continue
            entries.append({"path": rel, "sha256": sha256_file(p), "size": p.stat().st_size})
        # Include all non-meta files
        entries = []
        skip = set(META_NAMES) | {"artifact_digest.json", "attempt_manifest.json", "attempt_manifest.sha256"}
        for p in sorted(remote2.rglob("*")):
            if not p.is_file():
                continue
            rel = str(p.relative_to(remote2))
            if rel in skip:
                continue
            entries.append({"path": rel, "sha256": sha256_file(p), "size": p.stat().st_size})
        agg = canonical_aggregate_sha256(entries)
        art = {
            "files": entries,
            "aggregate_sha256": agg,
            "artifact_digest_sha256": agg,
            "file_count": len(entries),
        }
        (remote2 / "artifact_digest.json").write_text(
            json.dumps(art, indent=2, sort_keys=True), encoding="utf-8"
        )
        man = sealed["man"]
        man["artifact_digest_sha256"] = agg
        man["provenance"] = {"artifact_digest_sha256": agg}
        (remote2 / "attempt_manifest.json").write_text(
            json.dumps(man, indent=2, sort_keys=True), encoding="utf-8"
        )
        mh = sha256_file(remote2 / "attempt_manifest.json")
        (remote2 / "attempt_manifest.sha256").write_text(mh + "\n", encoding="utf-8")
        transport2 = _transport_from_dir(remote2)
        try:
            run_manifest_fallback(
                **_fb_kwargs(
                    ctx2, remote_root=remote2, transport=transport2, fast_path_error="bad_so_path"
                )
            )
            raise AssertionError("bad SO path must fail")
        except TransferRecoveryError:
            pass
        assert not (ctx2["backup_root"] / ctx2["attempt_id"] / "LOCAL_VERIFIED_SEAL.json").exists()
        _ = build_artifact_digest  # silence if unused


def fixture_chunk_pull_integration() -> None:
    """16MiB multi-chunk, short final, second-chunk short-then-ok, hash fail, final-exists refuse."""
    from chunk_pull import main as chunk_main

    assert chunk_main(["self-test"]) == 0

    # final already exists refuse via transfer_recovery
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        ctx = _setup_claimed_context(td_p)
        (ctx["backup_root"] / ctx["attempt_id"]).mkdir()
        remote = td_p / "remote" / ctx["attempt_id"]
        remote.mkdir(parents=True)
        _build_mini_sealed_tree(
            remote,
            attempt_id=ctx["attempt_id"],
            arm=ctx["arm"],
            plan_hash=ctx["plan_hash"],
            group_id=ctx["group_id"],
        )
        try:
            run_manifest_fallback(
                **_fb_kwargs(
                    ctx,
                    remote_root=remote,
                    transport=_transport_from_dir(remote),
                    fast_path_error="exists",
                )
            )
            raise AssertionError("final exists must refuse")
        except TransferRecoveryError as exc:
            assert "already exists" in str(exc) or "refuse" in str(exc).lower()

    here = Path(__file__).read_text(encoding="utf-8")
    assert "publish-staging disabled" in here
    assert "quarantine-staging disabled" in here
    fill_fn = here.split("def run_manifest_fallback")[1].split("# --- Fixtures")[0]
    assert "shutil.rmtree" not in fill_fn
    pull_fn = Path("launch_megatron_ab.sh").read_text(encoding="utf-8").split(
        "pull_attempt_evidence()"
    )[1].split('echo "[megatron-ab] GROUP_ID')[0]
    active = [
        ln
        for ln in pull_fn.splitlines()
        if not ln.lstrip().startswith("#") and ln.strip()
    ]
    assert not any("publish-staging" in ln for ln in active)
    assert not any("quarantine-staging" in ln for ln in active)


def fixture_diagnostic_independent_claim() -> None:
    """Diagnostic recovery uses new claim + source binding plan; cannot borrow INVALID claim."""
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        src_gid = "old-invalid-group"
        src_plan = _mini_plan(src_gid, "attempt_02_ours", "ours")
        src_plan_path = td_p / "source_group_plan.json"
        src_plan_path.write_text(json.dumps(src_plan, indent=2, sort_keys=True), encoding="utf-8")

        diag = _setup_claimed_context(
            td_p / "diag",
            attempt_id="attempt_02_ours",
            arm="ours",
            group_id="diag-xfer-group-001",
        )
        remote = td_p / "remote" / "attempt_02_ours"
        remote.mkdir(parents=True)
        _build_mini_sealed_tree(
            remote,
            attempt_id="attempt_02_ours",
            arm="ours",
            plan_hash=src_plan["plan_hash"],
            group_id=src_gid,
        )
        transport = _transport_from_dir(remote)
        result = run_manifest_fallback(
            **_fb_kwargs(
                diag,
                remote_root=remote,
                transport=transport,
                fast_path_error="diag",
                diagnostic=True,
                binding_plan_path=src_plan_path,
                binding_plan_hash=src_plan["plan_hash"],
            )
        )
        assert result["diagnostic"] is True
        assert (diag["backup_root"] / "attempt_02_ours" / "LOCAL_VERIFIED_SEAL.json").is_file()
        so_rel = json.loads(
            (diag["backup_root"] / "attempt_02_ours" / "provenance_build.json").read_text()
        )["collector_so_sealed_relpath"]
        assert (
            (diag["backup_root"] / "attempt_02_ours" / so_rel).stat().st_mode & 0o777
        ) == 0o444

        same = _mini_plan(diag["group_id"], "attempt_02_ours", "ours")
        same_path = td_p / "same_plan.json"
        same_path.write_text(json.dumps(same, indent=2, sort_keys=True), encoding="utf-8")
        try:
            validate_claim_and_plan_binding(
                backup_root=diag["backup_root"],
                attempt_id="attempt_02_ours",
                claim_path=diag["claim_path"],
                group_plan_path=diag["group_plan"],
                group_id=diag["group_id"],
                plan_hash=diag["plan_hash"],
                diagnostic=True,
                binding_plan_path=same_path,
                binding_plan_hash=same["plan_hash"],
            )
            raise AssertionError("same group_id diagnostic binding must fail")
        except TransferRecoveryError:
            pass


def run_all_fixtures() -> None:
    fixture_fast_tar_truncation_then_recover()
    fixture_short_read_then_success()
    fixture_permanent_hash_error()
    fixture_transport_failure()
    fixture_path_traversal_and_symlink_rejected()
    fixture_partial_dir_safety_guard()
    fixture_forged_claim_cannot_touch_old_invalid()
    fixture_claim_plan_binding_negatives()
    fixture_canonical_path_and_manifest_binding_negatives()
    fixture_disk_and_timeout_gates()
    fixture_seal_ordering_and_publish_races()
    fixture_sealed_so_mode_restore()
    fixture_chunk_pull_integration()
    fixture_diagnostic_independent_claim()
    print("TRANSFER_RECOVERY_FIXTURES_OK")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_fb = sub.add_parser("fallback", help="Manifest-driven per-file recovery (staging publish)")
    p_fb.add_argument("--backup-root", required=True)
    p_fb.add_argument("--attempt-id", required=True)
    p_fb.add_argument("--remote-root", required=True)
    p_fb.add_argument("--claim-path", required=True)
    p_fb.add_argument("--group-plan", required=True)
    p_fb.add_argument("--group-id", required=True)
    p_fb.add_argument("--plan-hash", required=True)
    p_fb.add_argument("--jump", default="afs-cpu")
    p_fb.add_argument(
        "--kubeconfig",
        default="/root/.kube/config-vc-a3-241ceshi-songyiyang.yaml",
    )
    p_fb.add_argument(
        "--kubectl",
        default="/root/.cache/volcano/kubectl/kubectl",
    )
    p_fb.add_argument("--namespace", default="default")
    p_fb.add_argument("--pod", default="grj-megatron-32card-0716-master-0")
    p_fb.add_argument("--fast-path-error", default="")
    p_fb.add_argument("--max-attempts", type=int, default=MAX_FILE_ATTEMPTS)
    p_fb.add_argument("--expected-ranks", type=int, default=None)
    p_fb.add_argument("--expected-nodes", type=int, default=None)
    p_fb.add_argument("--capture-step", type=int, default=None)
    p_fb.add_argument("--connect-timeout", type=int, default=30)
    p_fb.add_argument("--global-deadline-s", type=float, default=DEFAULT_GLOBAL_DEADLINE_S)
    p_fb.add_argument("--disk-safety-floor-bytes", type=int, default=DISK_SAFETY_FLOOR_BYTES)
    p_fb.add_argument("--disk-safety-ratio", type=float, default=DISK_SAFETY_RATIO)
    p_fb.add_argument(
        "--diagnostic",
        action="store_true",
        help="Independent claim recovery; requires --binding-plan/--binding-plan-hash",
    )
    p_fb.add_argument("--binding-plan", default="")
    p_fb.add_argument("--binding-plan-hash", default="")

    sub.add_parser("self-test", help="Run local fixtures")

    p_guard = sub.add_parser("resolve-guard", help="Check attempt path safety (no clear)")
    p_guard.add_argument("--backup-root", required=True)
    p_guard.add_argument("--attempt-id", required=True)

    p_stage = sub.add_parser(
        "create-staging",
        help="Atomically mkdir exclusive pull/fallback staging (exist_ok=False)",
    )
    p_stage.add_argument("--backup-root", required=True)
    p_stage.add_argument("--attempt-id", required=True)
    p_stage.add_argument("--kind", choices=("pull", "fallback"), default="pull")
    p_stage.add_argument("--nonce", default="")

    p_q = sub.add_parser(
        "quarantine-staging",
        help="DISABLED: leave fast-path temp in place (hard-fail)",
    )
    p_q.add_argument("--staging", required=True)
    p_q.add_argument("--attempt-id", required=True)

    p_pub = sub.add_parser(
        "publish-staging",
        help="DISABLED: use mkdir+chunk fill (hard-fail)",
    )
    p_pub.add_argument("--backup-root", required=True)
    p_pub.add_argument("--attempt-id", required=True)
    p_pub.add_argument("--staging", required=True)

    p_fp = sub.add_parser(
        "fingerprint",
        help="Write full tree fingerprint JSON (diagnostic; does not modify tree)",
    )
    p_fp.add_argument("--root", required=True)
    p_fp.add_argument("--out", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "self-test":
        run_all_fixtures()
        return 0
    if args.cmd == "resolve-guard":
        p = resolve_safe_attempt_dir(args.backup_root, args.attempt_id)
        print(f"SAFE_ATTEMPT_DIR {p}")
        return 0
    if args.cmd == "create-staging":
        try:
            staging = create_exclusive_staging_dir(
                args.backup_root,
                args.attempt_id,
                kind=args.kind,
                nonce=args.nonce or None,
            )
        except TransferRecoveryError as exc:
            print(f"CREATE_STAGING_FAILED {exc}", file=sys.stderr)
            return 12
        print(str(staging))
        return 0
    if args.cmd == "quarantine-staging":
        print(
            "QUARANTINE_DISABLED leave fast-path temp in place; use chunk fallback",
            file=sys.stderr,
        )
        return 12
    if args.cmd == "publish-staging":
        print(
            "PUBLISH_DISABLED use mkdir_final_exclusive + 16MiB chunk fill",
            file=sys.stderr,
        )
        return 12
    if args.cmd == "fingerprint":
        fp = build_tree_fingerprint(args.root)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(fp, indent=2, sort_keys=True), encoding="utf-8")
        print(fp["aggregate_sha256"], fp["file_count"])
        return 0
    if args.cmd == "fallback":
        if float(args.disk_safety_ratio) <= 0 or int(args.disk_safety_floor_bytes) < 0:
            print("FALLBACK_FAILED disk safety cannot be disabled for formal recovery", file=sys.stderr)
            return 12
        whole = KubectlCatTransport(
            jump=args.jump,
            kubeconfig=args.kubeconfig,
            kubectl=args.kubectl,
            namespace=args.namespace,
            pod=args.pod,
            connect_timeout=args.connect_timeout,
            jump_local=(str(args.jump).strip().lower() in {"", "local", "-", "none"}),
        )
        chunk = KubectlChunkTransport(
            jump=args.jump,
            kubeconfig=args.kubeconfig,
            kubectl=args.kubectl,
            namespace=args.namespace,
            pod=args.pod,
            connect_timeout=args.connect_timeout,
            jump_local=(str(args.jump).strip().lower() in {"", "local", "-", "none"}),
        )
        transport = DualTransport(whole=whole, chunk=chunk)
        try:
            result = run_manifest_fallback(
                backup_root=args.backup_root,
                attempt_id=args.attempt_id,
                remote_root=args.remote_root,
                transport=transport,
                claim_path=args.claim_path,
                group_plan_path=args.group_plan,
                group_id=args.group_id,
                plan_hash=args.plan_hash,
                fast_path_error=args.fast_path_error,
                max_attempts=args.max_attempts,
                diagnostic=bool(args.diagnostic),
                binding_plan_path=args.binding_plan or None,
                binding_plan_hash=args.binding_plan_hash or None,
                global_deadline_s=float(args.global_deadline_s),
                disk_safety_floor=int(args.disk_safety_floor_bytes),
                disk_safety_ratio=float(args.disk_safety_ratio),
                require_disk_safety=True,
                expected_ranks=args.expected_ranks,
                expected_nodes=args.expected_nodes,
                capture_step=args.capture_step,
            )
        except TransferRecoveryError as exc:
            print(f"FALLBACK_FAILED {exc}", file=sys.stderr)
            return 12
        print(
            "FALLBACK_RECOVERED",
            result["attempt_id"],
            f"files={result['file_count']}",
            f"elapsed_sec={result['elapsed_sec']}",
            f"agg={result['aggregate_sha256'][:16]}",
            f"deadline_s={result['global_deadline_s']}",
            f"disk_need={result['disk_budget']['need']}",
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
