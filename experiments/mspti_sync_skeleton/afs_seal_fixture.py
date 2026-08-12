#!/usr/bin/env python3
"""No-training AFS seal acceptance fixture (REPLAN Acceptance 2)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from afs_seal_mirror import rebuild_sealed_mirror, write_authoritative_afs_seal
from provenance import build_artifact_digest, write_local_verified_seal
from transfer_recovery import _build_mini_sealed_tree


def run_positive_fixture(
    *,
    afs_group_root: Path,
    attempt_id: str,
) -> dict:
    live_root = afs_group_root / "attempts"
    seal_root = afs_group_root / "sealed"
    live_attempt = live_root / attempt_id
    sealed_attempt = seal_root / attempt_id
    if live_attempt.exists():
        shutil.rmtree(live_attempt)
    if sealed_attempt.exists():
        shutil.rmtree(sealed_attempt)
    live_attempt.mkdir(parents=True, exist_ok=True)
    built = _build_mini_sealed_tree(
        live_attempt,
        attempt_id=attempt_id,
        arm="ours",
        group_id=afs_group_root.name,
    )
    build_artifact_digest(live_attempt)
    man_path = live_attempt / "attempt_manifest.json"
    man_hash = __import__("hashlib").sha256(man_path.read_bytes()).hexdigest()
    (live_attempt / "attempt_manifest.sha256").write_text(man_hash + "\n", encoding="utf-8")
    rebuild = rebuild_sealed_mirror(live_dir=live_attempt, sealed_dir=sealed_attempt)
    seal = write_authoritative_afs_seal(
        sealed_attempt,
        run_id=attempt_id,
        live_root=str(live_attempt),
    )
    seal_path = sealed_attempt / "AFS_VERIFIED_SEAL.json"
    return {
        "ok": True,
        "attempt_id": attempt_id,
        "afs_group_root": str(afs_group_root),
        "afs_live_root": str(live_root),
        "afs_seal_root": str(seal_root),
        "live_attempt": str(live_attempt),
        "sealed_attempt": str(sealed_attempt),
        "aggregate_sha256": built["art"]["aggregate_sha256"],
        "rebuild": rebuild,
        "afs_verified_seal": seal,
        "afs_verified_seal_path": str(seal_path),
        "local_verified_seal_absent": not (sealed_attempt / "LOCAL_VERIFIED_SEAL.json").exists(),
    }


def run_negative_tamper_fixture(*, afs_group_root: Path, attempt_id: str) -> dict:
    live_root = afs_group_root / "attempts"
    seal_root = afs_group_root / "sealed"
    live_attempt = live_root / f"{attempt_id}_neg"
    sealed_attempt = seal_root / f"{attempt_id}_neg"
    for p in (live_attempt, sealed_attempt):
        if p.exists():
            shutil.rmtree(p)
    live_attempt.mkdir(parents=True, exist_ok=True)
    _build_mini_sealed_tree(live_attempt, attempt_id=f"{attempt_id}_neg", arm="ours")
    build_artifact_digest(live_attempt)
    rebuild_sealed_mirror(live_dir=live_attempt, sealed_dir=sealed_attempt)
    # Tamper after rebuild — seal must not be written / must fail verification.
    run_log = sealed_attempt / "run.log"
    run_log.write_text(run_log.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
    error = ""
    ok = False
    try:
        write_authoritative_afs_seal(
            sealed_attempt,
            run_id=f"{attempt_id}_neg",
            live_root=str(live_attempt),
        )
        # If write succeeds, aggregate mismatch should be caught by strict re-read inside seal
        seal_path = sealed_attempt / "AFS_VERIFIED_SEAL.json"
        ok = not seal_path.exists()
        if seal_path.exists():
            error = "AFS_VERIFIED_SEAL present after tamper"
    except Exception as exc:  # noqa: BLE001
        ok = True
        error = str(exc)
    return {
        "ok": ok,
        "attempt_id": f"{attempt_id}_neg",
        "tamper_target": str(run_log),
        "afs_verified_seal_absent": not (sealed_attempt / "AFS_VERIFIED_SEAL.json").exists(),
        "error": error,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--afs-group-root",
        type=Path,
        required=True,
        help="e.g. /afs-a3-weight-share/.../megatron-ab/<GROUP_ID>",
    )
    ap.add_argument("--attempt-id", default="fixture_seal_01")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.afs_group_root.mkdir(parents=True, exist_ok=True)
    positive = run_positive_fixture(
        afs_group_root=args.afs_group_root,
        attempt_id=args.attempt_id,
    )
    negative = run_negative_tamper_fixture(
        afs_group_root=args.afs_group_root,
        attempt_id=args.attempt_id,
    )
    payload = {
        "fixture_ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "positive": positive,
        "negative_tamper": negative,
        "all_ok": positive["ok"] and negative["ok"],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    # Also write canonical seal copy beside fixture root for auditors.
    seal_copy = args.afs_group_root / "AFS_VERIFIED_SEAL.json"
    seal_copy.write_text(
        json.dumps(positive["afs_verified_seal"], indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print("AFS_SEAL_FIXTURE_OK", payload["all_ok"])
    return 0 if payload["all_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
