#!/usr/bin/env python3
"""Build jump launcher wrapper from hashed CODE_DIR (never unhashed AFS_BASE).

Reads launch_megatron_ab.sh from CODE_DIR_HASHED, pins CODE_HASH/CODE_DIR, and
writes a jump-local wrapper script for JUMP_LOCAL=1 detach launches.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def build_launcher_wrap(
    *,
    code_dir_hashed: Path,
    launch_wrap: Path,
    code_hash: str,
) -> Path:
    code_dir = code_dir_hashed.resolve()
    if not code_dir.is_dir():
        raise FileNotFoundError(f"hashed code dir missing: {code_dir}")
    launcher = code_dir / "launch_megatron_ab.sh"
    if not launcher.is_file():
        raise FileNotFoundError(f"launcher missing in hashed tree: {launcher}")

    afs_base = code_dir.parent / code_dir.name.rsplit("-", 1)[0]
    if not afs_base.name.endswith("mspti_sync_skeleton"):
        afs_base = code_dir.parent / "mspti_sync_skeleton"

    text = launcher.read_text(encoding="utf-8")
    text = re.sub(
        r'^ROOT="\$\(cd "\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)/\.\./\.\." && pwd\)"\n'
        r'EXP_LOCAL="\$\{ROOT\}/experiments/mspti_sync_skeleton"\n',
        f'ROOT="{afs_base.parent}"\nEXP_LOCAL="{afs_base}"\n',
        text,
        count=1,
        flags=re.M,
    )
    text = re.sub(
        r'CODE_HASH="\$\(\n  cd "\$\{EXP_LOCAL\}" && python3 - <<\'PY\'[\s\S]*?^PY\n\)"\n'
        r'CODE_DIR="\$\{AFS_ROOT\}/probing-huawei/experiments/mspti_sync_skeleton-\$\{CODE_HASH\}"',
        f'CODE_HASH="{code_hash}"\nCODE_DIR="{code_dir}"',
        text,
        count=1,
        flags=re.M,
    )
    launch_wrap.parent.mkdir(parents=True, exist_ok=True)
    launch_wrap.write_text(text, encoding="utf-8")
    launch_wrap.chmod(0o755)
    return launch_wrap


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--code-dir-hashed", required=True, type=Path)
    ap.add_argument("--code-hash", required=True)
    ap.add_argument("--launch-wrap", required=True, type=Path)
    args = ap.parse_args(argv)
    try:
        out = build_launcher_wrap(
            code_dir_hashed=args.code_dir_hashed,
            launch_wrap=args.launch_wrap,
            code_hash=args.code_hash,
        )
    except (FileNotFoundError, OSError) as exc:
        print(f"LAUNCHER_WRAP_FAIL: {exc}", file=sys.stderr)
        return 2
    print(f"LAUNCHER_WRAP_OK {out} code_hash={args.code_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
