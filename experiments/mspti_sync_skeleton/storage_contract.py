#!/usr/bin/env python3
"""Layered storage contract for MSPTI Megatron AB (JUMP capacity REPLAN).

Paths:
- AFS_GROUP_ROOT: authoritative group root on yinjinrun.p-huawei
- AFS_LIVE_ROOT: live attempt outputs + remote seals
- AFS_SEAL_ROOT: CHUNK_16M rebuilt sealed mirror (full verification)
- JUMP_CONTROL_ROOT: launcher control logs only (~4 GiB budget)

JUMP_LOCAL=1 means launcher runs on jump host (no SSH for kubectl), NOT that
large payloads must land on jump overlay.

Formal capacity (REPLAN 2026-08-10):
  FORMAL_CAPACITY_OK = JUMP_CONTROL_OK ∧ AFS_PAYLOAD_OK ∧ RECEIPT_PROVENANCE_OK
  Default SEAL_BACKEND=AFS_MIRROR; CAPACITY_JUMP_ONLY is illegal in formal mode.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

GIB = 2**30
MEBIBYTE = 1024**2

AFS_ROOT_DEFAULT = "/afs-a3-weight-share/yinjinrun.p-huawei"
JUMP_CONTROL_BUDGET_BYTES = 4 * GIB
JUMP_MIN_AVAIL_BYTES = 10 * GIB  # J_control floor component
JUMP_CHUNK_MIN_BYTES = 128 * GIB
AFS_MIN_AVAIL_BYTES = 200 * GIB
AFS_RESERVE_RATIO = 0.20

SEAL_BACKEND_AFS_MIRROR = "AFS_MIRROR"
SEAL_BACKEND_JUMP_CHUNK = "JUMP_CHUNK"
SEAL_BACKENDS = frozenset({SEAL_BACKEND_AFS_MIRROR, SEAL_BACKEND_JUMP_CHUNK})

# r3 frozen per-arm payload estimates (bytes) — REPLAN Step 2.3
TORCH_PAYLOAD_R3 = 41_477_256_579
OURS_PAYLOAD_R3 = 677_550_151
NORMAL_PAYLOAD_R3 = 4_516_709
P_GROUP_R3 = 2 * (TORCH_PAYLOAD_R3 + OURS_PAYLOAD_R3 + NORMAL_PAYLOAD_R3)
# 84_318_646_878 B

ARM_PAYLOADS_R3 = {
    "torch": TORCH_PAYLOAD_R3,
    "ours": OURS_PAYLOAD_R3,
    "normal": NORMAL_PAYLOAD_R3,
}

# need(p) = p + max(512 MiB, 10%×p); B_torch frozen at 45 GiB (ceil of need + guard)
def _need_destination_bytes(payload_bytes: int) -> int:
    return payload_bytes + max(512 * MEBIBYTE, int(0.10 * payload_bytes))


TORCH_NEED_R3 = _need_destination_bytes(TORCH_PAYLOAD_R3)  # 45_624_982_236
B_TORCH_R3 = 45 * GIB  # formal frozen per-arm torch budget

AFS_REQUIRED_START_R3 = max(
    AFS_MIN_AVAIL_BYTES,
    math.ceil(1.2 * 2 * P_GROUP_R3),
)

# Placeholder control peak from INVALID r3 bypass — must never pass formal gate.
INVALID_PLACEHOLDER_CONTROL_PEAK = 5105

EXIT_OK = 0
EXIT_NO_CAPACITY = 2
EXIT_INVALID_CAPACITY_MODE = 3
EXIT_INVALID_CONTROL_PEAK = 4


@dataclass(frozen=True)
class StoragePaths:
    group_id: str
    afs_group_root: Path
    afs_live_root: Path
    afs_seal_root: Path
    jump_control_root: Path

    def as_dict(self) -> dict[str, str]:
        return {
            "group_id": self.group_id,
            "afs_group_root": str(self.afs_group_root),
            "afs_live_root": str(self.afs_live_root),
            "afs_seal_root": str(self.afs_seal_root),
            "jump_control_root": str(self.jump_control_root),
        }


def compute_storage_paths(
    group_id: str,
    *,
    afs_root: str = AFS_ROOT_DEFAULT,
    jump_control_parent: str = "/root/myportal-results/mspti-control",
) -> StoragePaths:
    base = Path(afs_root) / "results/mspti-sync-skeleton/megatron-ab" / group_id
    return StoragePaths(
        group_id=group_id,
        afs_group_root=base,
        afs_live_root=base / "attempts",
        afs_seal_root=base / "sealed",
        jump_control_root=Path(jump_control_parent) / group_id,
    )


def j_control_required_bytes(control_peak_bytes: int) -> int:
    return max(JUMP_MIN_AVAIL_BYTES, 2 * int(control_peak_bytes) + 2 * GIB)


def afs_required_start_bytes(payload_group_bytes: int = P_GROUP_R3) -> int:
    return max(AFS_MIN_AVAIL_BYTES, math.ceil(1.2 * 2 * int(payload_group_bytes)))


def jump_chunk_required_bytes(
    *,
    control_peak_bytes: int,
    retained_bytes: int = 0,
    arm_budgets: Optional[list[int]] = None,
    per_arm_recycle: bool = True,
) -> int:
    """JUMP_CHUNK backend gate (non-recommended; 48 GiB jump disk → NO_CAPACITY)."""
    j_control = j_control_required_bytes(control_peak_bytes)
    budgets = arm_budgets or [B_TORCH_R3] * 6
    if per_arm_recycle:
        # First arm: max(128GiB, J_control + retained + B_1)
        b0 = budgets[0] if budgets else B_TORCH_R3
        return max(JUMP_CHUNK_MIN_BYTES, j_control + retained_bytes + b0)
    return max(
        JUMP_CHUNK_MIN_BYTES,
        j_control + retained_bytes + sum(budgets),
    )


def _avail_bytes(path: str) -> int:
    usage = shutil.disk_usage(path)
    return int(usage.free)


def _dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def parse_capacity_jump_only(env_value: Optional[str] = None) -> int:
    raw = env_value if env_value is not None else os.environ.get("CAPACITY_JUMP_ONLY", "0")
    raw = (raw or "0").strip()
    try:
        return int(raw)
    except ValueError:
        return 1  # non-numeric → treat as illegal non-zero


def validate_control_peak_provenance(
    control_peak_bytes: int,
    provenance: Optional[dict[str, Any]],
    *,
    formal_mode: bool,
) -> tuple[bool, str]:
    """Return (ok, failure_code). failure_code ∈ OK | INVALID_CONTROL_PEAK | NO_CAPACITY."""
    if not formal_mode:
        return True, "OK"
    prov = provenance or {}
    source = str(prov.get("source") or prov.get("measurement_kind") or "")
    if control_peak_bytes == INVALID_PLACEHOLDER_CONTROL_PEAK:
        if source != "measured_full_six_arm":
            return False, "INVALID_CONTROL_PEAK"
    if control_peak_bytes <= 0:
        return False, "NO_CAPACITY"
    if formal_mode and source != "measured_full_six_arm":
        return False, "NO_CAPACITY"
    if not prov.get("code_hash") and not prov.get("plan_hash"):
        return False, "NO_CAPACITY"
    if prov.get("six_arm_covered") is False:
        return False, "NO_CAPACITY"
    return True, "OK"


@dataclass
class CapacityReport:
    ok: bool
    jump_avail_bytes: int
    jump_required_bytes: int
    jump_control_bytes: int
    jump_control_budget_bytes: int
    afs_avail_bytes: int
    afs_required_bytes: int
    control_peak_bytes: int
    payload_estimate_bytes: int
    seal_backend: str
    formal_mode: bool
    failure_code: str
    reasons: list[str]
    formulas: dict[str, Any] = field(default_factory=dict)
    arm_payloads: dict[str, int] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "jump_avail_bytes": self.jump_avail_bytes,
            "jump_required_bytes": self.jump_required_bytes,
            "jump_control_bytes": self.jump_control_bytes,
            "jump_control_budget_bytes": self.jump_control_budget_bytes,
            "afs_avail_bytes": self.afs_avail_bytes,
            "afs_required_bytes": self.afs_required_bytes,
            "control_peak_bytes": self.control_peak_bytes,
            "payload_estimate_bytes": self.payload_estimate_bytes,
            "seal_backend": self.seal_backend,
            "formal_mode": self.formal_mode,
            "failure_code": self.failure_code,
            "reasons": self.reasons,
            "formulas": self.formulas,
            "arm_payloads": self.arm_payloads,
            "provenance": self.provenance,
            "B_torch_bytes": B_TORCH_R3,
            "P_group_bytes": P_GROUP_R3,
            "AFS_required_start_bytes": AFS_REQUIRED_START_R3,
        }


def check_capacity(
    *,
    jump_path: str = "/",
    afs_path: str = AFS_ROOT_DEFAULT,
    jump_control_root: Optional[Path] = None,
    control_peak_bytes: int = 0,
    control_peak_provenance: Optional[dict[str, Any]] = None,
    payload_estimate_bytes: int = P_GROUP_R3,
    seal_backend: str = SEAL_BACKEND_AFS_MIRROR,
    formal_mode: bool = False,
    capacity_jump_only: int = 0,
    skip_afs: bool = False,
    jump_avail_override: Optional[int] = None,
    afs_avail_override: Optional[int] = None,
    retained_jump_bytes: int = 0,
    arm_index: int = 0,
) -> CapacityReport:
    """Evaluate jump control + AFS capacity gates (fail-closed)."""
    reasons: list[str] = []
    failure_code = "OK"
    backend = (seal_backend or SEAL_BACKEND_AFS_MIRROR).upper()
    if backend not in SEAL_BACKENDS:
        reasons.append(f"unknown_seal_backend={backend}")
        failure_code = "NO_CAPACITY"

    if formal_mode and capacity_jump_only != 0:
        reasons.append(
            f"CAPACITY_JUMP_ONLY={capacity_jump_only} illegal in formal mode"
        )
        return CapacityReport(
            ok=False,
            jump_avail_bytes=0,
            jump_required_bytes=0,
            jump_control_bytes=0,
            jump_control_budget_bytes=JUMP_CONTROL_BUDGET_BYTES,
            afs_avail_bytes=0,
            afs_required_bytes=0,
            control_peak_bytes=control_peak_bytes,
            payload_estimate_bytes=payload_estimate_bytes,
            seal_backend=backend,
            formal_mode=formal_mode,
            failure_code="INVALID_CAPACITY_MODE",
            reasons=reasons,
        )

    peak_ok, peak_fail = validate_control_peak_provenance(
        control_peak_bytes, control_peak_provenance, formal_mode=formal_mode
    )
    if not peak_ok:
        reasons.append(
            f"control_peak={control_peak_bytes} provenance_invalid "
            f"source={(control_peak_provenance or {}).get('source')!r}"
        )
        failure_code = peak_fail

    jump_avail = (
        int(jump_avail_override)
        if jump_avail_override is not None
        else _avail_bytes(jump_path)
    )
    jump_control = _dir_size_bytes(jump_control_root) if jump_control_root else 0

    j_control_req = j_control_required_bytes(control_peak_bytes)
    if backend == SEAL_BACKEND_JUMP_CHUNK:
        arm_budgets = [B_TORCH_R3 if i < 2 or i >= 4 else B_TORCH_R3 for i in range(6)]
        # torch arms use B_torch; ours/normal smaller — conservative use B_torch for all in gate
        jump_required = jump_chunk_required_bytes(
            control_peak_bytes=control_peak_bytes,
            retained_bytes=retained_jump_bytes,
            arm_budgets=arm_budgets,
            per_arm_recycle=True,
        )
    else:
        jump_required = j_control_req

    afs_required = afs_required_start_bytes(payload_estimate_bytes)
    if skip_afs:
        afs_avail = afs_required
    else:
        afs_avail = (
            int(afs_avail_override)
            if afs_avail_override is not None
            else _avail_bytes(afs_path)
        )

    if jump_avail < jump_required:
        reasons.append(
            f"jump_avail={jump_avail} < required={jump_required} "
            f"(backend={backend}, J_control={j_control_req})"
        )
        if failure_code == "OK":
            failure_code = "NO_CAPACITY"
    if jump_control > JUMP_CONTROL_BUDGET_BYTES:
        reasons.append(
            f"jump_control={jump_control} > budget={JUMP_CONTROL_BUDGET_BYTES}"
        )
        if failure_code == "OK":
            failure_code = "NO_CAPACITY"
    elif jump_control > int(JUMP_CONTROL_BUDGET_BYTES * 0.8):
        reasons.append(
            f"jump_control_warn: {jump_control} > 80% of {JUMP_CONTROL_BUDGET_BYTES}"
        )
    if not skip_afs and afs_avail < afs_required:
        reasons.append(
            f"afs_avail={afs_avail} < required={afs_required} "
            f"(max(200GiB, ceil(1.2×2×P_group)))"
        )
        if failure_code == "OK":
            failure_code = "NO_CAPACITY"

    hard_fail_prefixes = ("jump_avail", "jump_control=", "afs_avail", "unknown_seal")
    ok = failure_code == "OK" and not any(
        r.startswith(p) for r in reasons for p in hard_fail_prefixes
    )

    formulas = {
        "GiB": GIB,
        "J_control_required": f"max(10GiB, 2×C_peak+2GiB)={j_control_req}",
        "B_torch": f"{B_TORCH_R3} B (45 GiB frozen)",
        "P_group": f"{P_GROUP_R3} B",
        "AFS_required_start": f"{afs_required} B",
        "need_torch": f"{TORCH_NEED_R3} B",
        "seal_backend": backend,
        "arm_index": arm_index,
    }

    return CapacityReport(
        ok=ok,
        jump_avail_bytes=jump_avail,
        jump_required_bytes=jump_required,
        jump_control_bytes=jump_control,
        jump_control_budget_bytes=JUMP_CONTROL_BUDGET_BYTES,
        afs_avail_bytes=afs_avail,
        afs_required_bytes=afs_required,
        control_peak_bytes=control_peak_bytes,
        payload_estimate_bytes=payload_estimate_bytes,
        seal_backend=backend,
        formal_mode=formal_mode,
        failure_code=failure_code if not ok else "OK",
        reasons=reasons,
        formulas=formulas,
        arm_payloads=dict(ARM_PAYLOADS_R3),
        provenance=dict(control_peak_provenance or {}),
    )


def build_capacity_receipt(
    report: CapacityReport,
    *,
    jump_path: str,
    afs_path: str,
    jump_control_root: Optional[Path],
    code_hash: str = "",
    plan_hash: str = "",
) -> dict[str, Any]:
    """Full auditable receipt for formal capacity PASS."""
    return {
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "jump_path": jump_path,
        "afs_path": afs_path,
        "jump_control_root": str(jump_control_root) if jump_control_root else "",
        "code_hash": code_hash,
        "plan_hash": plan_hash,
        "capacity": report.to_dict(),
        "receipt_provenance_ok": report.ok and report.failure_code == "OK",
    }


# Payload leak patterns on jump control tree (formal AFS_MIRROR).
_CONTROL_PAYLOAD_FORBIDDEN = (
    "rank_",
    ".skeleton.jsonl",
    "torch_trace",
    "CHUNK_",
    "attempt_manifest.json",
)


def scan_jump_control_payload_leak(control_root: Path) -> list[str]:
    """Return relative paths of forbidden payload files under jump control tree."""
    leaks: list[str] = []
    if not control_root.exists():
        return leaks
    for root, _dirs, files in os.walk(control_root):
        for name in files:
            rel = str((Path(root) / name).relative_to(control_root))
            low = rel.lower()
            if any(p in low for p in _CONTROL_PAYLOAD_FORBIDDEN):
                if "dry_run_attempt_config" not in low:
                    leaks.append(rel)
    return leaks


def main() -> int:
    ap = argparse.ArgumentParser(description="MSPTI storage contract paths + capacity")
    ap.add_argument("--group-id", required=True)
    ap.add_argument("--afs-root", default=AFS_ROOT_DEFAULT)
    ap.add_argument("--jump-control-parent", default="/root/myportal-results/mspti-control")
    ap.add_argument("--jump-path", default="/")
    ap.add_argument("--control-peak-bytes", type=int, default=0)
    ap.add_argument("--control-peak-receipt", type=Path, default=None)
    ap.add_argument("--payload-estimate-bytes", type=int, default=P_GROUP_R3)
    ap.add_argument("--seal-backend", default=SEAL_BACKEND_AFS_MIRROR)
    ap.add_argument("--formal-mode", action="store_true")
    ap.add_argument("--check-capacity", action="store_true")
    ap.add_argument(
        "--jump-only",
        action="store_true",
        help="Legacy dev gate: skip AFS (disallowed with --formal-mode)",
    )
    ap.add_argument("--capacity-jump-only", type=int, default=None)
    ap.add_argument("--code-hash", default="")
    ap.add_argument("--plan-hash", default="")
    ap.add_argument("--mock-jump-avail-bytes", type=int, default=None)
    ap.add_argument("--mock-afs-avail-bytes", type=int, default=None)
    ap.add_argument("--scan-control-leak", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    cap_jump = (
        args.capacity_jump_only
        if args.capacity_jump_only is not None
        else parse_capacity_jump_only()
    )

    if args.formal_mode and cap_jump != 0:
        payload = {
            "failure_code": "INVALID_CAPACITY_MODE",
            "capacity_jump_only": cap_jump,
            "formal_mode": True,
        }
        print("INVALID_CAPACITY_MODE", json.dumps(payload), file=sys.stderr)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return EXIT_INVALID_CAPACITY_MODE

    if args.formal_mode and args.jump_only:
        print("INVALID_CAPACITY_MODE jump_only disallowed in formal mode", file=sys.stderr)
        return EXIT_INVALID_CAPACITY_MODE

    paths = compute_storage_paths(
        args.group_id,
        afs_root=args.afs_root,
        jump_control_parent=args.jump_control_parent,
    )

    provenance: dict[str, Any] = {}
    if args.control_peak_receipt and args.control_peak_receipt.is_file():
        provenance = json.loads(args.control_peak_receipt.read_text(encoding="utf-8"))
        if "control_peak_provenance" in provenance:
            provenance = provenance["control_peak_provenance"]
    elif args.formal_mode:
        provenance = {"source": "", "six_arm_covered": False}

    payload_out: dict[str, Any] = paths.as_dict()

    if args.scan_control_leak:
        leaks = scan_jump_control_payload_leak(paths.jump_control_root)
        payload_out["control_payload_leak"] = leaks
        if leaks:
            print("CONTROL_PAYLOAD_LEAK", json.dumps(leaks), file=sys.stderr)
            payload_out["failure_code"] = "CONTROL_PAYLOAD_LEAK"
            text = json.dumps(payload_out, indent=2, sort_keys=True)
            if args.out:
                args.out.write_text(text, encoding="utf-8")
            return EXIT_NO_CAPACITY

    if args.check_capacity:
        rep = check_capacity(
            jump_path=args.jump_path,
            afs_path=args.afs_root,
            jump_control_root=paths.jump_control_root,
            control_peak_bytes=args.control_peak_bytes,
            control_peak_provenance=provenance,
            payload_estimate_bytes=args.payload_estimate_bytes,
            seal_backend=args.seal_backend,
            formal_mode=args.formal_mode,
            capacity_jump_only=cap_jump,
            skip_afs=args.jump_only and not args.formal_mode,
            jump_avail_override=args.mock_jump_avail_bytes,
            afs_avail_override=args.mock_afs_avail_bytes,
        )
        receipt = build_capacity_receipt(
            rep,
            jump_path=args.jump_path,
            afs_path=args.afs_root,
            jump_control_root=paths.jump_control_root,
            code_hash=args.code_hash,
            plan_hash=args.plan_hash,
        )
        payload_out["capacity_receipt"] = receipt
        cap = rep.to_dict()
        payload_out["capacity"] = cap

        if not cap["ok"]:
            code = rep.failure_code or "NO_CAPACITY"
            print(code, json.dumps(cap), file=sys.stderr)
            text = json.dumps(payload_out, indent=2, sort_keys=True)
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(text, encoding="utf-8")
            if code == "INVALID_CAPACITY_MODE":
                return EXIT_INVALID_CAPACITY_MODE
            if code == "INVALID_CONTROL_PEAK":
                return EXIT_INVALID_CONTROL_PEAK
            return EXIT_NO_CAPACITY
        print("CAPACITY_OK")

    text = json.dumps(payload_out, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
