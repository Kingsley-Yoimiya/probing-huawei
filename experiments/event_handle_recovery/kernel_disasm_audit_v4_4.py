#!/usr/bin/env python3
"""V4.4 disasm/.text artifact gate for production vs audit negative control."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text_size(final: Path) -> int:
    out = subprocess.check_output(["readelf", "-SW", str(final)], text=True)
    for line in out.splitlines():
        if ".text" in line and "PROGBITS" in line:
            parts = line.split()
            if len(parts) >= 6:
                return int(parts[5], 16)
    return 0


def load_disasm(path: Path) -> str:
    if path.exists() and path.stat().st_size > 0:
        return path.read_text(errors="replace")
    return ""


def count_insn_lines(disasm: str) -> int:
    return len([ln for ln in disasm.splitlines() if re.match(r"^\s+[0-9a-f]+:", ln)])


def audit_disasm(disasm: str) -> dict:
    low = disasm.lower()
    has_loop = bool(
        re.search(r"\bb\.|br\s|loop", disasm, re.I)
        or disasm.count("->") >= 2
    )
    gm_hits = len(re.findall(r"\bgm\b|global", low))
    store_hits = len(re.findall(r"\bst\b|\bstr\b|store", low))
    load_hits = len(re.findall(r"\bld\b|\bldr\b|load", low))
    insn_count = count_insn_lines(disasm)
    return {
        "has_backward_edge_hint": has_loop,
        "gm_token_hits": gm_hits,
        "store_token_hits": store_hits,
        "load_token_hits": load_hits,
        "instruction_line_count": insn_count,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True)
    p.add_argument("--final", required=True)
    p.add_argument("--neg-final", required=True)
    p.add_argument("--disasm", required=True)
    p.add_argument("--neg-disasm", required=True)
    p.add_argument("--out-json", required=True)
    p.add_argument("--min-text", type=lambda x: int(x, 0), default=0xF9)
    p.add_argument("--min-insns", type=int, default=16)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    source = Path(args.source)
    final = Path(args.final)
    neg_final = Path(args.neg_final)
    disasm_path = Path(args.disasm)
    neg_disasm_path = Path(args.neg_disasm)

    text_size = read_text_size(final)
    neg_text_size = read_text_size(neg_final)
    disasm = load_disasm(disasm_path)
    neg_disasm = load_disasm(neg_disasm_path)
    if not disasm:
        out = {"pass": False, "stop": "STOP_KERNEL_DISASM_UNAVAILABLE", "disasm_path": str(disasm_path)}
        Path(args.out_json).write_text(json.dumps(out, indent=2) + "\n")
        print(json.dumps(out, indent=2))
        raise SystemExit(3)

    prod_audit = audit_disasm(disasm)
    neg_audit = audit_disasm(neg_disasm)
    final_sha = sha256_file(final)
    source_sha = sha256_file(source)
    disasm_sha = sha256_file(disasm_path)

    reasons: list[str] = []
    if text_size <= args.min_text:
        reasons.append(f"text_too_small:{text_size:#x}<={args.min_text:#x}")
    if prod_audit["instruction_line_count"] < args.min_insns:
        reasons.append(f"insufficient_insns:{prod_audit['instruction_line_count']}")
    if not prod_audit["has_backward_edge_hint"]:
        reasons.append("no_loop_backedge_hint")
    if prod_audit["store_token_hits"] < 1 or prod_audit["load_token_hits"] < 1:
        reasons.append("missing_gm_load_store_tokens")
    if final_sha == sha256_file(neg_final):
        reasons.append("prod_neg_sha_identical")
    if text_size == neg_text_size and final_sha != sha256_file(neg_final):
        pass  # ok if different sha but same text size is suspicious only when sha same

    out = {
        "pass": len(reasons) == 0,
        "stop": None if not reasons else "STOP_KERNEL_BODY_NOT_PRESENT",
        "source_sha256": source_sha,
        "final_sha256": final_sha,
        "neg_final_sha256": sha256_file(neg_final),
        "disasm_sha256": disasm_sha,
        "text_bytes": text_size,
        "neg_text_bytes": neg_text_size,
        "production_audit": prod_audit,
        "negative_audit": neg_audit,
        "reasons": reasons,
        "paths": {
            "source": str(source.resolve()),
            "final": str(final.resolve()),
            "neg_final": str(neg_final.resolve()),
            "disasm": str(disasm_path.resolve()),
            "neg_disasm": str(neg_disasm_path.resolve()),
        },
    }
    Path(args.out_json).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    if not out["pass"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
