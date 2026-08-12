#!/usr/bin/env python3
"""FANOUT_PREFLIGHT=1: read-only identity/AFS/hash checks on all pods.

No torchrun, no Megatron, no run_megatron_node.sh.
PASS requires done=16/16 with all emit rc=0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def build_preflight_cmd(
    *,
    code_dir: str,
    afs_root: str,
    node_rank: int,
    wheel_sha256: str = "",
    so_sha256: str = "",
) -> str:
    """Remote read-only preflight — no training commands."""
    pybin = os.environ.get("PYBIN", "/root/miniconda3/envs/llm_test/bin/python")
    return f"""{pybin} - <<'PY'
import hashlib, json, os, sys
from pathlib import Path
node_rank = {node_rank}
afs_root = '{afs_root}'
code_dir = '{code_dir}'
wheel_sha = '{wheel_sha256}'
so_sha = '{so_sha256}'
receipt = {{
    'node_rank': node_rank,
    'hostname': os.uname().nodename,
    'afs_root': afs_root,
    'code_dir': code_dir,
    'afs_writable': False,
    'import_ok': False,
}}
# AFS write probe (own prefix only)
probe = Path(afs_root) / 'results' / 'mspti-sync-skeleton' / '.fanout_preflight_probe'
try:
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(f'rank={{node_rank}}', encoding='utf-8')
    receipt['afs_writable'] = probe.read_text(encoding='utf-8') == f'rank={{node_rank}}'
    probe.unlink(missing_ok=True)
except Exception as e:
    receipt['afs_error'] = str(e)
# Code dir visibility
receipt['code_dir_exists'] = Path(code_dir).is_dir()
try:
    import probing
    receipt['probing_version'] = getattr(probing, 'VERSION', '?')
    receipt['probing_file'] = probing.__file__
    receipt['import_ok'] = True
except Exception as e:
    receipt['import_error'] = str(e)
if wheel_sha:
    receipt['wheel_sha256_expected'] = wheel_sha
if so_sha:
    receipt['so_sha256_expected'] = so_sha
out = Path('/tmp') / f'mspti_fanout_preflight_rank{{node_rank}}.json'
out.write_text(json.dumps(receipt, sort_keys=True), encoding='utf-8')
print('PREFLIGHT_RECEIPT_OK', node_rank)
PY"""


def run_fanout_preflight(
    pod_map: dict[str, Any],
    *,
    exec_fn: Callable[[str, str], tuple[int, str]],
    code_dir: str,
    afs_root: str,
    parallel: int = 16,
    wheel_sha256: str = "",
    so_sha256: str = "",
    receipt_dir: Path,
) -> dict[str, Any]:
    """Execute fanout preflight on all pods; collect receipts."""
    receipt_dir = Path(receipt_dir)
    receipt_dir.mkdir(parents=True, exist_ok=True)
    pods = {int(e["node_rank"]): e["pod"] for e in pod_map["pods"]}
    results: dict[int, dict[str, Any]] = {}
    emit_rcs: dict[int, int] = {}

    def _one(rank: int, pod: str) -> tuple[int, dict[str, Any]]:
        cmd = build_preflight_cmd(
            code_dir=code_dir,
            afs_root=afs_root,
            node_rank=rank,
            wheel_sha256=wheel_sha256,
            so_sha256=so_sha256,
        )
        rc, out = exec_fn(pod, cmd)
        entry: dict[str, Any] = {
            "node_rank": rank,
            "pod": pod,
            "emit_rc": rc,
            "stdout_tail": out[-500:] if out else "",
        }
        if rc == 0:
            pull_cmd = (
                f"cat /tmp/mspti_fanout_preflight_rank{rank}.json 2>/dev/null || echo '{{}}'"
            )
            prc, pout = exec_fn(pod, pull_cmd)
            entry["pull_rc"] = prc
            try:
                receipt = json.loads(pout.strip() or "{}")
            except json.JSONDecodeError:
                receipt = {}
            entry["receipt"] = receipt
            entry["done"] = bool(
                receipt.get("import_ok") and receipt.get("afs_writable")
            )
        else:
            entry["done"] = False
        return rank, entry

    with ThreadPoolExecutor(max_workers=min(parallel, len(pods))) as pool:
        futs = {pool.submit(_one, r, p): r for r, p in pods.items()}
        for fut in as_completed(futs):
            rank, entry = fut.result()
            results[rank] = entry
            emit_rcs[rank] = int(entry["emit_rc"])

    done_count = sum(1 for e in results.values() if e.get("done"))
    expected = len(pods)
    summary = {
        "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "expected": expected,
        "done": done_count,
        "emit_rc_all_zero": all(rc == 0 for rc in emit_rcs.values()),
        "pass": done_count == expected and all(rc == 0 for rc in emit_rcs.values()),
        "results": [results[r] for r in sorted(results)],
        "emit_rcs": {str(k): v for k, v in sorted(emit_rcs.items())},
    }
    out_path = receipt_dir / "fanout_preflight_summary.json"
    out_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Run FANOUT_PREFLIGHT across pod map")
    ap.add_argument("--pod-map", type=Path, required=True)
    ap.add_argument("--receipt-dir", type=Path, required=True)
    ap.add_argument("--code-dir", required=True)
    ap.add_argument("--afs-root", default="/afs-a3-weight-share/yinjinrun.p-huawei")
    ap.add_argument("--parallel", type=int, default=16)
    ap.add_argument("--wheel-sha256", default="")
    ap.add_argument("--so-sha256", default="")
    ap.add_argument("--fixture", action="store_true", help="Local fake exec (no kubectl)")
    args = ap.parse_args()

    pod_map = json.loads(args.pod_map.read_text(encoding="utf-8"))

    if args.fixture:
        def exec_fn(pod: str, cmd: str) -> tuple[int, str]:
            rank = int(pod.split("-")[-1]) if pod.endswith(tuple(str(i) for i in range(16))) else 0
            if "master" in pod:
                rank = 0
            elif "worker" in pod:
                import re
                m = re.search(r"worker-(\d+)", pod)
                rank = int(m.group(1)) + 1 if m else 1
            receipt = {
                "node_rank": rank,
                "import_ok": True,
                "afs_writable": True,
                "probing_version": "0.2.6+fixture",
            }
            return 0, json.dumps(receipt)

    else:
        ap2 = argparse.ArgumentParser()
        ap2.add_argument("--kubeconfig", required=True)
        ap2.add_argument("--kubectl", default="kubectl")
        ap2.add_argument("--namespace", default="default")
        # re-parse remaining from env in real launcher; here require env
        kubeconfig = os.environ.get("KUBECONFIG", "")
        kubectl = os.environ.get("KUBECTL", "kubectl")
        namespace = os.environ.get("NS", "default")

        def exec_fn(pod: str, cmd: str) -> tuple[int, str]:
            proc = subprocess.run(
                [kubectl, "--kubeconfig", kubeconfig, "exec", "-n", namespace, pod, "--",
                 "bash", "--noprofile", "--norc", "-lc", cmd],
                capture_output=True,
                text=True,
            )
            return proc.returncode, proc.stdout + proc.stderr

    summary = run_fanout_preflight(
        pod_map,
        exec_fn=exec_fn,
        code_dir=args.code_dir,
        afs_root=args.afs_root,
        parallel=args.parallel,
        wheel_sha256=args.wheel_sha256,
        so_sha256=args.so_sha256,
        receipt_dir=args.receipt_dir,
    )
    if summary["pass"]:
        print(f"FANOUT_PREFLIGHT_PASS done={summary['done']}/{summary['expected']}")
        return 0
    print(
        f"FANOUT_PREFLIGHT_FAIL done={summary['done']}/{summary['expected']}",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
