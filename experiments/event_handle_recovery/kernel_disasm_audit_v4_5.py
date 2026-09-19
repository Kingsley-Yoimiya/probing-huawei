#!/usr/bin/env python3
"""V4.5 disasm/.text gate: correct Size parsing + STOP classification."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from elf_section_parser import read_text_size

INSN_LINE_RE = re.compile(r"^\s+[0-9a-f]+:", re.IGNORECASE)
NOT_AVAILABLE_RE = re.compile(r"<not available>", re.IGNORECASE)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_disasm(path: Path) -> str:
    if path.exists() and path.stat().st_size > 0:
        return path.read_text(errors="replace")
    return ""


def count_insn_lines(disasm: str) -> int:
    return len([ln for ln in disasm.splitlines() if INSN_LINE_RE.match(ln)])


def is_disasm_decodable(disasm: str) -> bool:
    """True only when output has real address+mnemonic lines, not all <not available>."""
    if not disasm.strip():
        return False
    lines = [ln for ln in disasm.splitlines() if ln.strip()]
    if not lines:
        return False
    insn_lines = [ln for ln in lines if INSN_LINE_RE.match(ln)]
    if not insn_lines:
        return False
    # Every instruction line is <not available> => not decodable
    if all(NOT_AVAILABLE_RE.search(ln) for ln in insn_lines):
        return False
    if all(NOT_AVAILABLE_RE.search(ln) for ln in lines if "disassembly" not in ln.lower()):
        # llvm-objdump: only header + <not available> lines
        if NOT_AVAILABLE_RE.search(disasm) and count_insn_lines(disasm) == 0:
            return False
    decodable_insn = [ln for ln in insn_lines if not NOT_AVAILABLE_RE.search(ln)]
    return len(decodable_insn) > 0


def audit_disasm_body(disasm: str) -> dict:
    low = disasm.lower()
    has_loop = bool(
        re.search(r"\bb\.|br\s|loop", disasm, re.I) or disasm.count("->") >= 2
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
        "decodable": is_disasm_decodable(disasm),
    }


def body_present(audit: dict, min_insns: int) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if audit["instruction_line_count"] < min_insns:
        reasons.append(f"insufficient_insns:{audit['instruction_line_count']}")
    if not audit["has_backward_edge_hint"]:
        reasons.append("no_loop_backedge_hint")
    if audit["store_token_hits"] < 1 or audit["load_token_hits"] < 1:
        reasons.append("missing_gm_load_store_tokens")
    return len(reasons) == 0, reasons


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
    p.add_argument(
        "--evidence-mode",
        choices=["auto", "NATIVE_DISASM", "ALTERNATIVE_CONTRACT"],
        default="auto",
    )
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

    prod_audit = audit_disasm_body(disasm)
    neg_audit = audit_disasm_body(neg_disasm)
    final_sha = sha256_file(final)
    neg_sha = sha256_file(neg_final)
    source_sha = sha256_file(source)
    disasm_sha = sha256_file(disasm_path) if disasm_path.exists() else None

    # Structure precondition (alternative path component 1)
    alt_precond_reasons: list[str] = []
    if text_size <= args.min_text:
        alt_precond_reasons.append(f"text_too_small:{text_size:#x}<={args.min_text:#x}")
    if text_size == neg_text_size:
        alt_precond_reasons.append(f"text_size_equals_neg:{text_size:#x}")
    if final_sha == neg_sha:
        alt_precond_reasons.append("prod_neg_sha_identical")
    alternative_structure_precondition_pass = len(alt_precond_reasons) == 0

    decodable = prod_audit["decodable"]
    evidence_mode = args.evidence_mode
    if evidence_mode == "auto":
        evidence_mode = "NATIVE_DISASM" if decodable else "ALTERNATIVE_CONTRACT"

    stop: str | None = None
    structure_gate_pass = False
    pass_gate = False
    reasons: list[str] = []

    if not decodable:
        stop = "STOP_KERNEL_DISASM_UNAVAILABLE"
        reasons.append("disasm_not_decodable")
        if evidence_mode == "NATIVE_DISASM":
            pass_gate = False
        elif alternative_structure_precondition_pass:
            # Alternative path: disasm unavailable is expected; structure precond only
            pass_gate = True
            structure_gate_pass = False  # GM/TASK/scaling still required
        else:
            pass_gate = False
            stop = "STOP_KERNEL_DISASM_UNAVAILABLE"
            reasons.extend(alt_precond_reasons)
    else:
        body_ok, body_reasons = body_present(prod_audit, args.min_insns)
        if not body_ok:
            stop = "STOP_KERNEL_BODY_NOT_PRESENT"
            reasons.extend(body_reasons)
            pass_gate = False
        else:
            if not alternative_structure_precondition_pass:
                stop = "STOP_KERNEL_BODY_NOT_PRESENT"
                reasons.extend(alt_precond_reasons)
                pass_gate = False
            else:
                pass_gate = True
                structure_gate_pass = True
                evidence_mode = "NATIVE_DISASM"

    out = {
        "pass": pass_gate,
        "stop": stop,
        "evidence_mode": evidence_mode,
        "native_disasm_failure_code": (
            "STOP_KERNEL_DISASM_UNAVAILABLE" if not decodable else None
        ),
        "alternative_structure_precondition_pass": alternative_structure_precondition_pass,
        "structure_gate_pass": structure_gate_pass,
        "reachability_gate_pass": False,
        "alternative_contract_pass": False,
        "source_sha256": source_sha,
        "final_sha256": final_sha,
        "neg_final_sha256": neg_sha,
        "disasm_sha256": disasm_sha,
        "text_bytes": text_size,
        "neg_text_bytes": neg_text_size,
        "production_audit": prod_audit,
        "negative_audit": neg_audit,
        "reasons": reasons,
        "alt_precondition_reasons": alt_precond_reasons,
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
    if not pass_gate:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
