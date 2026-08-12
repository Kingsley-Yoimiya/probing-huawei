#!/usr/bin/env python3
"""No-training capacity + AFS seal fixtures for JUMP capacity REPLAN (Builder Step 6)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from storage_contract import (
    B_TORCH_R3,
    GIB,
    INVALID_PLACEHOLDER_CONTROL_PEAK,
    JUMP_CHUNK_MIN_BYTES,
    P_GROUP_R3,
    AFS_REQUIRED_START_R3,
    SEAL_BACKEND_AFS_MIRROR,
    SEAL_BACKEND_JUMP_CHUNK,
    check_capacity,
    j_control_required_bytes,
    parse_capacity_jump_only,
)

EXP = Path(__file__).resolve().parent


def _run_case(name: str, fn) -> dict:
    try:
        ok = bool(fn())
        return {"name": name, "ok": ok, "error": ""}
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "ok": False, "error": str(exc)}


def test_invalid_capacity_mode() -> bool:
    rep = check_capacity(
        formal_mode=True,
        capacity_jump_only=1,
        control_peak_bytes=1_000_000,
        control_peak_provenance={
            "source": "measured_full_six_arm",
            "code_hash": "x",
            "plan_hash": "y",
            "six_arm_covered": True,
        },
        jump_avail_override=500 * GIB,
        afs_avail_override=500 * GIB,
    )
    return rep.failure_code == "INVALID_CAPACITY_MODE" and not rep.ok


def test_jump_chunk_20gib_fail() -> bool:
    rep = check_capacity(
        seal_backend=SEAL_BACKEND_JUMP_CHUNK,
        control_peak_bytes=5_000_000,
        control_peak_provenance={"source": "measured_full_six_arm", "code_hash": "a", "plan_hash": "b"},
        jump_avail_override=20 * GIB,
        afs_avail_override=500 * GIB,
        formal_mode=False,
    )
    return not rep.ok and rep.failure_code == "NO_CAPACITY" and rep.jump_required_bytes >= JUMP_CHUNK_MIN_BYTES


def test_afs_mirror_jump_low_fail() -> bool:
    peak = 5_000_000
    j_req = j_control_required_bytes(peak)
    rep = check_capacity(
        seal_backend=SEAL_BACKEND_AFS_MIRROR,
        control_peak_bytes=peak,
        control_peak_provenance={"source": "measured_full_six_arm", "code_hash": "a", "plan_hash": "b"},
        jump_avail_override=j_req - 1,
        afs_avail_override=500 * GIB,
        formal_mode=True,
    )
    return not rep.ok and rep.failure_code == "NO_CAPACITY"


def test_afs_low_fail() -> bool:
    rep = check_capacity(
        seal_backend=SEAL_BACKEND_AFS_MIRROR,
        control_peak_bytes=5_000_000,
        control_peak_provenance={"source": "measured_full_six_arm", "code_hash": "a", "plan_hash": "b"},
        jump_avail_override=500 * GIB,
        afs_avail_override=AFS_REQUIRED_START_R3 - 1,
        formal_mode=True,
    )
    return not rep.ok and rep.failure_code == "NO_CAPACITY"


def test_invalid_control_peak_5105() -> bool:
    rep = check_capacity(
        seal_backend=SEAL_BACKEND_AFS_MIRROR,
        control_peak_bytes=INVALID_PLACEHOLDER_CONTROL_PEAK,
        control_peak_provenance={"source": "", "six_arm_covered": False},
        jump_avail_override=500 * GIB,
        afs_avail_override=500 * GIB,
        formal_mode=True,
    )
    return not rep.ok and rep.failure_code in {"INVALID_CONTROL_PEAK", "NO_CAPACITY"}


def test_positive_receipt() -> bool:
    peak = 8_000_000
    rep = check_capacity(
        seal_backend=SEAL_BACKEND_AFS_MIRROR,
        control_peak_bytes=peak,
        control_peak_provenance={
            "source": "measured_full_six_arm",
            "code_hash": "c760ed758aaabf96",
            "plan_hash": "fixture",
            "six_arm_covered": True,
        },
        payload_estimate_bytes=P_GROUP_R3,
        jump_avail_override=j_control_required_bytes(peak) + GIB,
        afs_avail_override=AFS_REQUIRED_START_R3 + GIB,
        formal_mode=True,
    )
    return (
        rep.ok
        and rep.payload_estimate_bytes == P_GROUP_R3
        and rep.formulas.get("B_torch") == f"{B_TORCH_R3} B (45 GiB frozen)"
        and rep.afs_required_bytes == AFS_REQUIRED_START_R3
    )


def test_cli_invalid_capacity_mode(tmp: Path) -> bool:
    proc = subprocess.run(
        [
            sys.executable,
            str(EXP / "storage_contract.py"),
            "--group-id",
            "fixture-cli",
            "--check-capacity",
            "--formal-mode",
            "--capacity-jump-only",
            "1",
            "--control-peak-bytes",
            "5000000",
            "--mock-jump-avail-bytes",
            str(500 * GIB),
            "--mock-afs-avail-bytes",
            str(500 * GIB),
            "--out",
            str(tmp / "invalid_mode.json"),
        ],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 3 and "INVALID_CAPACITY_MODE" in proc.stderr


def test_afs_seal_fixture(tmp: Path) -> bool:
    group_root = tmp / "afs_group"
    out = tmp / "seal_fixture.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(EXP / "afs_seal_fixture.py"),
            "--afs-group-root",
            str(group_root),
            "--attempt-id",
            "fixture_seal_pos",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    payload = json.loads(out.read_text(encoding="utf-8"))
    pos = payload["positive"]
    neg = payload["negative_tamper"]
    seal_path = Path(pos["sealed_attempt"]) / "AFS_VERIFIED_SEAL.json"
    return (
        payload["all_ok"]
        and pos["local_verified_seal_absent"]
        and seal_path.is_file()
        and neg["afs_verified_seal_absent"]
    )


def test_launcher_dry_run_capacity(tmp: Path) -> bool:
    """DRY_RUN must pass capacity without CAPACITY_JUMP_ONLY."""
    backup = tmp / "backup"
    log_dir = tmp / "logs"
    gid = f"fixture-dry-{int(time.time())}"
    proc = subprocess.run(
        [
            "bash",
            str(EXP / "launch_megatron_ab.sh"),
        ],
        env={
            **dict(__import__("os").environ),
            "DRY_RUN": "1",
            "FIXTURE_RUN": "0",
            "GROUP_ID": gid,
            "BACKUP_PARENT": str(backup),
            "BACKUP_ROOT": str(backup / gid),
            "LOG_DIR": str(log_dir),
            "CLAIM_PARENT": str(backup / ".claims"),
            "JUMP_CONTROL_PARENT": str(backup / "jump_control"),
            "NNODES": "2",
            "NPROC": "2",
            "CONTROL_PEAK_BYTES": "5000000",
        },
        capture_output=True,
        text=True,
        cwd=str(EXP),
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "")[-2000:] + (proc.stdout or "")[-2000:])
    return "DRY_RUN complete" in proc.stdout and "CAPACITY_OK" in proc.stdout


def test_launcher_rejects_capacity_jump_only(tmp: Path) -> bool:
    proc = subprocess.run(
        ["bash", str(EXP / "launch_megatron_ab.sh")],
        env={
            **dict(__import__("os").environ),
            "GROUP_ID": f"fixture-reject-{int(time.time())}",
            "CAPACITY_JUMP_ONLY": "1",
            "JOB_NAME": "fake-job",
            "DRY_RUN": "0",
            "FIXTURE_RUN": "0",
            "FANOUT_PREFLIGHT": "0",
        },
        capture_output=True,
        text=True,
        cwd=str(EXP),
    )
    return proc.returncode == 3 and "INVALID_CAPACITY_MODE" in (proc.stderr or proc.stdout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cases = [
            ("invalid_capacity_mode", test_invalid_capacity_mode),
            ("jump_chunk_20gib_no_capacity", test_jump_chunk_20gib_fail),
            ("afs_mirror_jump_low_no_capacity", test_afs_mirror_jump_low_fail),
            ("afs_low_no_capacity", test_afs_low_fail),
            ("invalid_control_peak_5105", test_invalid_control_peak_5105),
            ("positive_receipt_r3", test_positive_receipt),
            ("cli_invalid_capacity_mode", lambda: test_cli_invalid_capacity_mode(tmp)),
            ("afs_seal_streaming_fixture", lambda: test_afs_seal_fixture(tmp)),
            ("launcher_dry_run_capacity", lambda: test_launcher_dry_run_capacity(tmp)),
            ("launcher_rejects_capacity_jump_only", lambda: test_launcher_rejects_capacity_jump_only(tmp)),
        ]
        results = [_run_case(name, fn) for name, fn in cases]

    payload = {
        "fixture_ts": ts,
        "parse_capacity_jump_only_default": parse_capacity_jump_only(),
        "P_group_bytes": P_GROUP_R3,
        "B_torch_bytes": B_TORCH_R3,
        "AFS_required_start_bytes": AFS_REQUIRED_START_R3,
        "SEAL_BACKEND_default": SEAL_BACKEND_AFS_MIRROR,
        "cases": results,
        "all_ok": all(r["ok"] for r in results),
    }
    out_path = args.out_dir / f"capacity_fixture_summary_{ts}.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print("CAPACITY_FIXTURE_OK", payload["all_ok"], "out=", out_path)
    for r in results:
        status = "PASS" if r["ok"] else "FAIL"
        print(f"  {status} {r['name']}", r.get("error", ""))
    return 0 if payload["all_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
