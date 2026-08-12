#!/usr/bin/env python3
"""CHUNK_16M rebuild live attempt → AFS sealed mirror + AFS_VERIFIED_SEAL."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from chunk_pull import CHUNK_SIZE, LocalFileChunkTransport, pull_file_chunked
from provenance import build_artifact_digest, write_afs_verified_seal, sha256_file
from transfer_recovery import restore_sealed_collector_so_mode


def _live_files(live_dir: Path) -> list[dict[str, Any]]:
    entries = []
    for root, _dirs, files in os.walk(live_dir):
        for name in files:
            p = Path(root) / name
            rel = str(p.relative_to(live_dir))
            if rel.startswith(".") or "/.partial/" in f"/{rel}/":
                continue
            entries.append(
                {
                    "path": rel,
                    "sha256": sha256_file(p),
                    "size": p.stat().st_size,
                }
            )
    return sorted(entries, key=lambda e: e["path"])


def rebuild_sealed_mirror(
    *,
    live_dir: Path,
    sealed_dir: Path,
    chunk_size: int = CHUNK_SIZE,
) -> dict[str, Any]:
    live_dir = Path(live_dir)
    sealed_dir = Path(sealed_dir)
    if sealed_dir.exists():
        shutil.rmtree(sealed_dir)
    sealed_dir.mkdir(parents=True, exist_ok=True)
    digest_path = live_dir / "artifact_digest.json"
    if digest_path.exists():
        digest = json.loads(digest_path.read_text(encoding="utf-8"))
        files = digest.get("files") or []
    else:
        files = _live_files(live_dir)
    transport = LocalFileChunkTransport(root=live_dir)
    pulled = []
    for entry in files:
        rel = entry["path"]
        src = live_dir / rel
        if not src.is_file():
            raise FileNotFoundError(f"missing live file: {src}")
        rec = pull_file_chunked(
            transport,
            remote_root=live_dir,
            dest_dir=sealed_dir,
            rel=rel,
            expected_size=int(entry["size"]),
            expected_sha256=str(entry["sha256"]),
            chunk_size=chunk_size,
        )
        if not rec.ok:
            raise RuntimeError(f"chunk rebuild failed for {rel}")
        pulled.append(
            {
                "path": rel,
                "size": rec.final_size,
                "sha256": rec.final_sha256,
            }
        )
    if not (sealed_dir / "artifact_digest.json").exists() and (live_dir / "artifact_digest.json").exists():
        shutil.copy2(live_dir / "artifact_digest.json", sealed_dir / "artifact_digest.json")
    for meta in (
        "attempt_manifest.json",
        "attempt_manifest.sha256",
        "provenance_build.json",
        "provenance_source_tree.json",
        "provenance_source_snapshot.sha256",
    ):
        src = live_dir / meta
        if src.exists() and not (sealed_dir / meta).exists():
            shutil.copy2(src, sealed_dir / meta)
    restore_sealed_collector_so_mode(sealed_dir)
    build_artifact_digest(sealed_dir)
    return {"files_pulled": pulled, "sealed_dir": str(sealed_dir)}


def write_authoritative_afs_seal(
    sealed_dir: Path,
    *,
    run_id: str,
    live_root: str | None = None,
) -> dict[str, Any]:
    sealed_dir = Path(sealed_dir)
    man_sha = ""
    man_sha_path = sealed_dir / "attempt_manifest.sha256"
    if man_sha_path.exists():
        man_sha = man_sha_path.read_text(encoding="utf-8").strip()
    seal = write_afs_verified_seal(
        sealed_dir,
        run_id=run_id,
        remote_manifest_sha256=man_sha or None,
        live_root=live_root,
    )
    if (sealed_dir / "LOCAL_VERIFIED_SEAL.json").exists():
        raise RuntimeError("LOCAL_VERIFIED_SEAL must not remain after AFS seal")
    if not (sealed_dir / "AFS_VERIFIED_SEAL.json").is_file():
        raise RuntimeError("AFS_VERIFIED_SEAL.json missing after write")
    return seal


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-dir", type=Path, required=True)
    ap.add_argument("--sealed-dir", type=Path, required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--live-root", default="")
    ap.add_argument("--write-seal", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    rebuild = rebuild_sealed_mirror(live_dir=args.live_dir, sealed_dir=args.sealed_dir)
    payload: dict[str, Any] = {"rebuild": rebuild}
    if args.write_seal:
        payload["seal"] = write_authoritative_afs_seal(
            args.sealed_dir,
            run_id=args.run_id,
            live_root=args.live_root or str(args.live_dir),
        )
        print("AFS_VERIFIED_SEAL_OK", payload["seal"].get("local_manifest_sha256", "")[:16])
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
