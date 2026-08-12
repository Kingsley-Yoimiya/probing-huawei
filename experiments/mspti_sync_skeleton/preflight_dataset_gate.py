#!/usr/bin/env python3
"""Megatron enwiki dataset gate — bin+idx must exist and be readable before GROUP claim.

Checks the Megatron indexed dataset prefix (DATA_PATH without .bin/.idx suffix).
On pjlab-new (pvc-5gnm2) the public /afs-a3-weight-share/enwiki/ tree is absent;
use yinjinrun.p-huawei copy (see DATA_PATH_DEFAULT_OWN).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

DATA_PATH_DEFAULT_OWN = (
    "/afs-a3-weight-share/yinjinrun.p-huawei/data/enwiki20230101/"
    "enwiki20230101-00000_text_document"
)
DATA_PATH_LEGACY_PUBLIC = (
    "/afs-a3-weight-share/enwiki/enwiki20230101/"
    "enwiki20230101-00000_text_document"
)
# Megatron indexed dataset sanity floors (enwiki shard 00000).
MIN_BIN_BYTES = 100 * 1024**2  # 100 MiB
MIN_IDX_BYTES = 1 * 1024**2  # 1 MiB


def verify_dataset_paths(
    data_path: str,
    *,
    min_bin_bytes: int = MIN_BIN_BYTES,
    min_idx_bytes: int = MIN_IDX_BYTES,
) -> dict[str, Any]:
    """Local filesystem check (pod mount or jump with AFS)."""
    prefix = data_path.rstrip("/")
    bin_path = Path(f"{prefix}.bin")
    idx_path = Path(f"{prefix}.idx")
    receipt: dict[str, Any] = {
        "data_path": prefix,
        "bin_path": str(bin_path),
        "idx_path": str(idx_path),
        "bin_exists": bin_path.is_file(),
        "idx_exists": idx_path.is_file(),
        "bin_bytes": 0,
        "idx_bytes": 0,
        "bin_readable": False,
        "idx_readable": False,
        "errors": [],
    }
    if receipt["bin_exists"]:
        try:
            receipt["bin_bytes"] = bin_path.stat().st_size
            with bin_path.open("rb") as fh:
                fh.read(4096)
            receipt["bin_readable"] = True
        except OSError as exc:
            receipt["errors"].append(f"bin_read:{exc}")
    else:
        receipt["errors"].append("bin_missing")
    if receipt["idx_exists"]:
        try:
            receipt["idx_bytes"] = idx_path.stat().st_size
            with idx_path.open("rb") as fh:
                fh.read(4096)
            receipt["idx_readable"] = True
        except OSError as exc:
            receipt["errors"].append(f"idx_read:{exc}")
    else:
        receipt["errors"].append("idx_missing")
    if receipt["bin_bytes"] < min_bin_bytes:
        receipt["errors"].append(f"bin_too_small:{receipt['bin_bytes']}<{min_bin_bytes}")
    if receipt["idx_bytes"] < min_idx_bytes:
        receipt["errors"].append(f"idx_too_small:{receipt['idx_bytes']}<{min_idx_bytes}")
    receipt["data_ok"] = (
        receipt["bin_readable"]
        and receipt["idx_readable"]
        and receipt["bin_bytes"] >= min_bin_bytes
        and receipt["idx_bytes"] >= min_idx_bytes
        and not receipt["errors"]
    )
    return receipt


def verify_dataset_remote(
    pod: str,
    data_path: str,
    *,
    namespace: str = "default",
    kubeconfig: str = "",
    kubectl: str = "kubectl",
    container: str = "",
) -> dict[str, Any]:
    """kubectl exec read-only probe on target pod mount."""
    prefix = data_path.rstrip("/")
    py = f"""
import json, os
from pathlib import Path
prefix = '{prefix}'
bin_p = Path(prefix + '.bin')
idx_p = Path(prefix + '.idx')
r = {{
    'data_path': prefix,
    'bin_path': str(bin_p),
    'idx_path': str(idx_p),
    'hostname': os.uname().nodename,
    'bin_exists': bin_p.is_file(),
    'idx_exists': idx_p.is_file(),
    'bin_bytes': 0,
    'idx_bytes': 0,
    'bin_readable': False,
    'idx_readable': False,
    'errors': [],
}}
try:
    if r['bin_exists']:
        r['bin_bytes'] = bin_p.stat().st_size
        with bin_p.open('rb') as fh:
            fh.read(4096)
        r['bin_readable'] = True
    else:
        r['errors'].append('bin_missing')
    if r['idx_exists']:
        r['idx_bytes'] = idx_p.stat().st_size
        with idx_p.open('rb') as fh:
            fh.read(4096)
        r['idx_readable'] = True
    else:
        r['errors'].append('idx_missing')
    if r['bin_bytes'] < {MIN_BIN_BYTES}:
        r['errors'].append(f'bin_too_small:{{r["bin_bytes"]}}<{MIN_BIN_BYTES}')
    if r['idx_bytes'] < {MIN_IDX_BYTES}:
        r['errors'].append(f'idx_too_small:{{r["idx_bytes"]}}<{MIN_IDX_BYTES}')
except Exception as e:
    r['errors'].append(f'probe_exception:{{e}}')
r['data_ok'] = (
    r['bin_readable'] and r['idx_readable']
    and r['bin_bytes'] >= {MIN_BIN_BYTES}
    and r['idx_bytes'] >= {MIN_IDX_BYTES}
    and not r['errors']
)
print(json.dumps(r, sort_keys=True))
"""
    cmd = [kubectl]
    if kubeconfig:
        cmd.extend(["--kubeconfig", kubeconfig])
    cmd.extend(["exec", "-n", namespace, pod])
    if container:
        cmd.extend(["-c", container])
    cmd.extend(["--", "python3", "-c", py])
    env = os.environ.copy()
    if kubeconfig:
        env["KUBECONFIG"] = kubeconfig
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    receipt: dict[str, Any] = {
        "pod": pod,
        "namespace": namespace,
        "exec_rc": proc.returncode,
        "exec_stderr": proc.stderr.strip(),
        "data_ok": False,
        "errors": [],
    }
    if proc.returncode != 0:
        receipt["errors"].append(f"exec_failed:rc={proc.returncode}")
        if proc.stderr.strip():
            receipt["errors"].append(proc.stderr.strip()[:500])
        return receipt
    try:
        remote = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        receipt["errors"].append(f"json_parse:{exc}")
        receipt["raw_stdout"] = proc.stdout[:2000]
        return receipt
    receipt.update(remote)
    return receipt


def build_summary(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "DATA_OK": "PASS" if receipt.get("data_ok") else "FAIL",
        "data_path": receipt.get("data_path"),
        "bin_path": receipt.get("bin_path"),
        "idx_path": receipt.get("idx_path"),
        "bin_bytes": receipt.get("bin_bytes"),
        "idx_bytes": receipt.get("idx_bytes"),
        "pod": receipt.get("pod"),
        "hostname": receipt.get("hostname"),
        "errors": receipt.get("errors", []),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Megatron dataset gate (bin+idx)")
    ap.add_argument("--data-path", default=os.environ.get("DATA_PATH", DATA_PATH_DEFAULT_OWN))
    ap.add_argument("--pod", default="", help="kubectl exec target pod (recommended)")
    ap.add_argument("--namespace", default="default")
    ap.add_argument("--kubeconfig", default=os.environ.get("KUBECONFIG", ""))
    ap.add_argument("--kubectl", default=os.environ.get("KUBECTL", "kubectl"))
    ap.add_argument("--container", default="")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--also-check-legacy", action="store_true",
                    help="Record whether legacy public enwiki path exists (diagnostic)")
    args = ap.parse_args()

    if args.pod:
        receipt = verify_dataset_remote(
            args.pod,
            args.data_path,
            namespace=args.namespace,
            kubeconfig=args.kubeconfig,
            kubectl=args.kubectl,
            container=args.container,
        )
    else:
        receipt = verify_dataset_paths(args.data_path)

    if args.also_check_legacy and args.data_path != DATA_PATH_LEGACY_PUBLIC:
        receipt["legacy_public"] = verify_dataset_paths(DATA_PATH_LEGACY_PUBLIC)

    summary = build_summary(receipt)
    out_obj = {"summary": summary, "receipt": receipt}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out_obj, indent=2, sort_keys=True), encoding="utf-8")
    print(f"DATA_OK={summary['DATA_OK']} data_path={summary.get('data_path')}")
    if summary["errors"]:
        print("errors:", "; ".join(summary["errors"]))
    return 0 if summary["DATA_OK"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
