#!/usr/bin/env python3
"""V4.5: probe CANN native disasm tools on prod FINAL; pick first decodable."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from elf_section_parser import read_text_section
from kernel_disasm_audit_v4_5 import is_disasm_decodable

INSN_LINE_RE = re.compile(r"^\s+[0-9a-f]+:", re.IGNORECASE)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_cmd(cmd: list[str], timeout: int = 120) -> dict:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
        )
        return {
            "cmd": cmd,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"cmd": cmd, "exit_code": -9, "stdout": "", "stderr": "timeout"}
    except FileNotFoundError:
        return {"cmd": cmd, "exit_code": 127, "stdout": "", "stderr": "not_found"}


def count_decoded_insns(text: str) -> int:
    return len(
        [
            ln
            for ln in text.splitlines()
            if INSN_LINE_RE.match(ln) and "<not available>" not in ln.lower()
        ]
    )


def probe_candidates(
    final: Path,
    out_dir: Path,
    cann_root: Path,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    ccec = cann_root / "bin/ccec"
    llvm_objdump = cann_root / "bin/llvm-objdump"
    objdump = cann_root / "bin/objdump"

    readelf_h = run_cmd(["readelf", "-h", str(final)])
    readelf_s = run_cmd(["readelf", "-SW", str(final)])
    machine = ""
    for line in readelf_h.get("stdout", "").splitlines():
        if "Machine:" in line:
            machine = line.split(":", 1)[1].strip()
            break

    probes: list[dict] = []

    def try_tool(name: str, cmd: list[str], out_name: str) -> None:
        result = run_cmd(cmd)
        out_path = out_dir / out_name
        combined = (result.get("stdout") or "") + (result.get("stderr") or "")
        out_path.write_text(combined, errors="replace")
        insn_count = count_decoded_insns(combined)
        decodable = is_disasm_decodable(combined)
        probes.append(
            {
                "tool": name,
                "command": cmd,
                "exit_code": result["exit_code"],
                "output_path": str(out_path.resolve()),
                "output_bytes": out_path.stat().st_size if out_path.exists() else 0,
                "output_sha256": sha256_file(out_path) if out_path.exists() else None,
                "decoded_instruction_count": insn_count,
                "decodable": decodable,
            }
        )

    if ccec.exists():
        help_out = run_cmd([str(ccec), "--help"])
        (out_dir / "ccec_help.txt").write_text(
            help_out.get("stdout", "") + help_out.get("stderr", ""), errors="replace"
        )
        for flag in ("--cce-aicore-disassemble", "-S", "--print-after-all"):
            try_tool(f"ccec_{flag}", [str(ccec), flag, str(final)], f"ccec_{flag.replace('/', '_')}.txt")

    if llvm_objdump.exists():
        help_out = run_cmd([str(llvm_objdump), "--help"])
        (out_dir / "llvm_objdump_help.txt").write_text(
            help_out.get("stdout", "") + help_out.get("stderr", ""), errors="replace"
        )
        for extra in ([], ["-m", "elf64-hiipu"], ["--triple=elf64-hiipu"]):
            label = "llvm_objdump" + ("_" + "_".join(extra) if extra else "")
            try_tool(label, [str(llvm_objdump), "-d", *extra, str(final)], f"{label}.txt")

    if objdump.exists():
        try_tool("cann_objdump", [str(objdump), "-d", str(final)], "cann_objdump.txt")

    nm = cann_root / "bin/nm"
    if nm.exists():
        nm_result = run_cmd([str(nm), "-S", str(final)])
        nm_path = out_dir / "nm_symtab.txt"
        nm_path.write_text(nm_result.get("stdout", ""), errors="replace")
        probes.append(
            {
                "tool": "nm_symtab",
                "command": [str(nm), "-S", str(final)],
                "exit_code": nm_result["exit_code"],
                "output_path": str(nm_path.resolve()),
                "decoded_instruction_count": 0,
                "decodable": False,
            }
        )

    readelf_path = out_dir / "readelf_sections.txt"
    readelf_path.write_text(readelf_s.get("stdout", ""), errors="replace")
    text_off, text_size = read_text_section(final)

    native_tool = None
    native_disasm_path = None
    for p in probes:
        if p.get("decodable"):
            native_tool = p["tool"]
            native_disasm_path = p["output_path"]
            break

    evidence_mode = "NATIVE_DISASM" if native_tool else "ALTERNATIVE_CONTRACT"
    return {
        "evidence_mode": evidence_mode,
        "native_disasm_tool": native_tool,
        "native_disasm_path": native_disasm_path,
        "native_disasm_failure_code": (
            None if native_tool else "STOP_KERNEL_DISASM_UNAVAILABLE"
        ),
        "machine": machine,
        "text_offset": text_off,
        "text_size": text_size,
        "final_sha256": sha256_file(final),
        "probes": probes,
        "readelf_sections_path": str(readelf_path.resolve()),
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--final", required=True)
    p.add_argument("--out-json", required=True)
    p.add_argument("--probe-dir", required=True)
    p.add_argument("--cann-root", default="/usr/local/Ascend/cann-8.5.1")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    report = probe_candidates(
        Path(args.final),
        Path(args.probe_dir),
        Path(args.cann_root),
    )
    Path(args.out_json).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
