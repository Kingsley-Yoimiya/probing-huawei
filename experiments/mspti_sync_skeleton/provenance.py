#!/usr/bin/env python3
"""可归因 provenance：source tree hash、源码快照、.so/脚本 digest、artifact digest、seal。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Optional


SOURCE_GLOBS = (
    "*.cpp",
    "*.hpp",
    "*.h",
    "*.py",
    "*.sh",
    "CMakeLists.txt",
    "README.md",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def iter_source_files(root: Path) -> list[Path]:
    files: set[Path] = set()
    for pattern in SOURCE_GLOBS:
        for p in root.glob(pattern):
            if p.is_file() and "__pycache__" not in p.parts:
                files.add(p.resolve())
    return sorted(files, key=lambda p: str(p.relative_to(root)))


def build_source_tree(root: Path) -> dict[str, Any]:
    entries = []
    h = hashlib.sha256()
    for path in iter_source_files(root):
        rel = str(path.relative_to(root))
        digest = sha256_file(path)
        size = path.stat().st_size
        entries.append({"path": rel, "sha256": digest, "size": size})
        h.update(rel.encode())
        h.update(b"\0")
        h.update(digest.encode())
        h.update(b"\0")
        h.update(str(size).encode())
        h.update(b"\n")
    return {
        "root": str(root),
        "files": entries,
        "source_tree_sha256": h.hexdigest(),
        "file_count": len(entries),
    }


def write_source_provenance(code_dir: Path, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    tree = build_source_tree(code_dir)
    (out_dir / "provenance_source_tree.json").write_text(
        json.dumps(tree, indent=2, sort_keys=True), encoding="utf-8"
    )
    (out_dir / "provenance_source_snapshot.sha256").write_text(
        tree["source_tree_sha256"] + "\n", encoding="utf-8"
    )
    # full snapshot of file contents concatenated (for audit; not for huge binaries)
    snap_path = out_dir / "provenance_source_snapshot.tarish.txt"
    parts = []
    for entry in tree["files"]:
        path = code_dir / entry["path"]
        parts.append(f"===== {entry['path']} sha256={entry['sha256']} =====\n")
        parts.append(path.read_text(encoding="utf-8", errors="replace"))
        parts.append("\n")
    snap_path.write_text("".join(parts), encoding="utf-8")
    return tree


def record_build_artifacts(code_dir: Path, out_dir: Path) -> dict[str, Any]:
    """Record build hashes and seal an immutable collector SO copy.

    The sealed copy is named by content hash and is what nodes must load.
    Recording a hash without copying would allow silent overwrite.
    """
    import shutil

    arts: dict[str, Any] = {}
    so = code_dir / "build" / "libmspti_sync_skeleton.so"
    if so.exists():
        digest = sha256_file(so)
        size = so.stat().st_size
        seal_dir = out_dir / "sealed_bins"
        seal_dir.mkdir(parents=True, exist_ok=True)
        sealed_name = f"libmspti_sync_skeleton.so.{digest}"
        sealed_path = seal_dir / sealed_name
        if not sealed_path.exists():
            shutil.copy2(so, sealed_path)
            try:
                sealed_path.chmod(0o444)
            except OSError:
                pass
        # Verify copy integrity immediately.
        sealed_hash = sha256_file(sealed_path)
        if sealed_hash != digest:
            raise RuntimeError(
                f"sealed SO hash mismatch: src={digest} sealed={sealed_hash}"
            )
        # Also keep a content-addressed copy under code build dir for LD_PRELOAD.
        code_seal_dir = code_dir / "build" / "sealed"
        code_seal_dir.mkdir(parents=True, exist_ok=True)
        code_sealed = code_seal_dir / sealed_name
        if not code_sealed.exists():
            shutil.copy2(sealed_path, code_sealed)
            try:
                code_sealed.chmod(0o444)
            except OSError:
                pass
        arts["collector_so_sha256"] = digest
        arts["collector_so_path"] = str(so)
        arts["collector_so_size"] = size
        arts["collector_so_sealed_relpath"] = f"sealed_bins/{sealed_name}"
        arts["collector_so_sealed_path"] = str(sealed_path)
        arts["collector_so_load_path"] = str(code_sealed)
        arts["collector_so_immutable"] = True
    for name in (
        "launch_megatron_smoke.sh",
        "launch_megatron_ab.sh",
        "launch_grj.sh",
        "run_megatron_node.sh",
        "strict_validate.py",
        "convert_trace.py",
        "megatron_mspti_hook.py",
        "collector.cpp",
        "kseg_logic.hpp",
    ):
        p = code_dir / name
        if p.exists():
            arts[f"{name}_sha256"] = sha256_file(p)
    (out_dir / "provenance_build.json").write_text(
        json.dumps(arts, indent=2, sort_keys=True), encoding="utf-8"
    )
    return arts


def canonical_aggregate_sha256(files: list[dict[str, Any]]) -> str:
    """Aggregate over canonical sorted (path, sha256) entries — NOT the digest file hash."""
    h = hashlib.sha256()
    for entry in sorted(files, key=lambda e: str(e["path"])):
        rel = str(entry["path"])
        digest = str(entry["sha256"])
        h.update(rel.encode())
        h.update(b"\0")
        h.update(digest.encode())
        h.update(b"\n")
    return h.hexdigest()


def is_safe_relative_artifact_path(rel: str) -> bool:
    if not rel or rel.startswith("/") or rel.startswith("\\"):
        return False
    parts = Path(rel).parts
    if ".." in parts:
        return False
    if Path(rel).is_absolute():
        return False
    return True


def _iter_artifact_files(out_dir: Path) -> list[Path]:
    """Collect sealable artifacts including nested torch profiler traces."""
    skip_names = {
        "attempt_manifest.json",
        "attempt_manifest.sha256",
        "artifact_digest.json",
        "LOCAL_VERIFIED_SEAL.json",
        "TRANSFER_RETRY_RECOVERED.json",
        "launcher_local.log",
    }
    top_patterns = [
        "rank_*.skeleton.jsonl",
        "rank_*.npu_sync_meta.json",
        "rank_*.mspti_meta.json",
        "rank_*.trace.json",
        "node_*.done",
        "node_*.fail",
        "node_*.log",
        "node_*.pgid",
        "node_*.launch.json",
        "launch_*.log",
        "counters.json",
        "cluster.trace.json",
        "config.json",
        "SUMMARY.md",
        "convert.log",
        "build.log",
        "provenance_*.json",
        "provenance_*.sha256",
        "provenance_*.txt",
        "run.log",
        "sealed_bins/*",
    ]
    root = out_dir.resolve()
    files: set[Path] = set()
    for pattern in top_patterns:
        for path in out_dir.glob(pattern):
            if path.is_file() and path.name not in skip_names:
                files.add(path.resolve())
    # Nested torch profiler outputs (Ascend: trace_view.json under torch_prof_node*).
    for path in out_dir.glob("torch_prof_node*/**/*"):
        if not path.is_file():
            continue
        if path.name in skip_names:
            continue
        # Primary + common companion profiler files.
        if path.name in {
            "trace_view.json",
            "profiler_info.json",
            "kernel_details.csv",
            "operator_details.csv",
            "api_statistic.csv",
        } or path.suffix in {".json", ".csv", ".db", ".trace"}:
            files.add(path.resolve())
    # Any sealed SO under sealed_bins/
    for path in out_dir.glob("sealed_bins/**/*"):
        if path.is_file():
            files.add(path.resolve())
    return sorted(files, key=lambda p: str(p.relative_to(root)))

def build_artifact_digest(out_dir: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in _iter_artifact_files(out_dir):
        try:
            rel = str(path.relative_to(out_dir.resolve()))
        except Exception:
            # Fallback if resolve changed root; try non-resolved
            try:
                rel = str(path.relative_to(out_dir))
            except Exception as exc:
                raise ValueError(f"artifact path escapes out_dir: {path}") from exc
        if not is_safe_relative_artifact_path(rel):
            raise ValueError(f"unsafe artifact path: {rel}")
        if rel in seen:
            continue
        seen.add(rel)
        # Reject symlinks
        candidate = out_dir / rel
        if candidate.is_symlink():
            raise ValueError(f"symlink artifact rejected: {rel}")
        digest = sha256_file(candidate)
        files.append({"path": rel, "sha256": digest, "size": candidate.stat().st_size})
    files = sorted(files, key=lambda e: e["path"])
    aggregate = canonical_aggregate_sha256(files)
    payload = {
        "files": files,
        # Canonical aggregate over entries. Manifest must reference THIS value.
        # Never confuse with sha256(artifact_digest.json) itself.
        "aggregate_sha256": aggregate,
        "artifact_digest_sha256": aggregate,  # alias for older readers
        "file_count": len(files),
        "semantics": (
            "aggregate_sha256 = sha256 over sorted (path\\0sha256\\n) of relative "
            "artifact entries; NOT the hash of artifact_digest.json; "
            "includes nested torch_prof_node*/**/trace_view.json and sealed SO"
        ),
    }
    (out_dir / "artifact_digest.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    return payload


def write_local_verified_seal(
    local_dir: Path,
    *,
    run_id: str,
    remote_manifest_sha256: Optional[str] = None,
) -> dict[str, Any]:
    """Parent-coordinator independent trust anchor after pullback.

    Threat model: detect accidental/unexpected modification of pulled artifacts.
    Not a defense against a malicious local user with write access to the same tree.
    No vault keys are used or required.
    """
    local_dir = Path(local_dir)
    art_path = local_dir / "artifact_digest.json"
    man_path = local_dir / "attempt_manifest.json"
    if not art_path.exists() or not man_path.exists():
        raise FileNotFoundError("need artifact_digest.json and attempt_manifest.json")
    art = json.loads(art_path.read_text(encoding="utf-8"))
    man_hash = sha256_file(man_path)
    if remote_manifest_sha256 and remote_manifest_sha256 != man_hash:
        raise ValueError(
            f"local manifest hash {man_hash} != remote seal {remote_manifest_sha256}"
        )
    files_checked: list[dict[str, Any]] = []
    for entry in art.get("files") or []:
        rel = str(entry["path"])
        if not is_safe_relative_artifact_path(rel):
            raise ValueError(f"unsafe path in artifact digest: {rel}")
        path = local_dir / rel
        if not path.is_file():
            raise FileNotFoundError(f"missing artifact for local seal: {rel}")
        size = path.stat().st_size
        digest = sha256_file(path)
        if int(entry.get("size", -1)) != size or str(entry.get("sha256")) != digest:
            raise ValueError(f"local artifact mismatch: {rel}")
        files_checked.append({"path": rel, "sha256": digest, "size": size})
    aggregate = canonical_aggregate_sha256(files_checked)
    listed = art.get("aggregate_sha256") or art.get("artifact_digest_sha256")
    if listed != aggregate:
        raise ValueError(
            f"recomputed aggregate {aggregate} != digest listed {listed}"
        )
    # key local files for the anchor
    key_hashes = {
        "attempt_manifest.json": man_hash,
        "artifact_digest.json": sha256_file(art_path),
        "attempt_manifest.sha256": (
            sha256_file(local_dir / "attempt_manifest.sha256")
            if (local_dir / "attempt_manifest.sha256").exists()
            else None
        ),
    }
    for name in ("config.json", "counters.json", "cluster.trace.json"):
        p = local_dir / name
        if p.exists():
            key_hashes[name] = sha256_file(p)
    import time

    payload = {
        "run_id": run_id,
        "verified_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "remote_manifest_sha256": remote_manifest_sha256 or man_hash,
        "local_manifest_sha256": man_hash,
        "artifact_aggregate_sha256": aggregate,
        "key_file_sha256": key_hashes,
        "file_count": len(files_checked),
        "threat_model": (
            "Detect accidental modification after remote seal / pullback. "
            "Not anti-malicious-local-user; no vault secrets involved."
        ),
    }
    out = local_dir / "LOCAL_VERIFIED_SEAL.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def write_afs_verified_seal(
    sealed_dir: Path,
    *,
    run_id: str,
    remote_manifest_sha256: Optional[str] = None,
    live_root: Optional[str] = None,
) -> dict[str, Any]:
    """Full verification seal on AFS sealed mirror (replaces LOCAL_VERIFIED_SEAL).

    Same scientific strength: re-read every artifact, verify aggregate hash,
    write immutable AFS_VERIFIED_SEAL.json as the last success-producing action.
  """
    payload = write_local_verified_seal(
        sealed_dir,
        run_id=run_id,
        remote_manifest_sha256=remote_manifest_sha256,
    )
    # Rename seal file to AFS contract name; keep local alias for backward compat readers.
    local_seal = sealed_dir / "LOCAL_VERIFIED_SEAL.json"
    afs_seal = sealed_dir / "AFS_VERIFIED_SEAL.json"
    if local_seal.exists() and not afs_seal.exists():
        afs_payload = json.loads(local_seal.read_text(encoding="utf-8"))
        afs_payload["seal_kind"] = "AFS_VERIFIED_SEAL"
        if live_root:
            afs_payload["afs_live_root"] = live_root
        afs_payload["afs_sealed_root"] = str(sealed_dir)
        tmp = afs_seal.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(afs_payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(afs_seal)
        local_seal.unlink(missing_ok=True)
    elif afs_seal.exists():
        payload = json.loads(afs_seal.read_text(encoding="utf-8"))
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-dir", type=Path, required=False)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument(
        "--phase",
        choices=("pre", "build", "artifacts", "local_seal", "all"),
        default="all",
    )
    ap.add_argument("--run-id", default="")
    ap.add_argument("--remote-manifest-sha256", default="")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.phase in ("pre", "all"):
        if args.code_dir is None:
            raise SystemExit("--code-dir required for pre/all")
        write_source_provenance(args.code_dir, args.out_dir)
    if args.phase in ("build", "all"):
        if args.code_dir is None:
            raise SystemExit("--code-dir required for build/all")
        record_build_artifacts(args.code_dir, args.out_dir)
    if args.phase in ("artifacts", "all"):
        build_artifact_digest(args.out_dir)
    if args.phase == "local_seal":
        seal = write_local_verified_seal(
            args.out_dir,
            run_id=args.run_id or args.out_dir.name,
            remote_manifest_sha256=args.remote_manifest_sha256 or None,
        )
        print("LOCAL_VERIFIED_SEAL_OK", seal["local_manifest_sha256"][:16])
        return 0
    print("PROVENANCE_OK", args.phase)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
