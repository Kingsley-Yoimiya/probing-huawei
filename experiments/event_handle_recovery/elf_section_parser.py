#!/usr/bin/env python3
"""Parse readelf -SW section headers: robust .text Size (not Offset)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

# readelf -SW: [Nr] Name Type Address Off Size ES ...
_TEXT_SECTION_RE = re.compile(
    r"\[\s*\d+\]\s+\.text\s+PROGBITS\s+(?P<addr>\S+)\s+(?P<offset>\S+)\s+(?P<size>\S+)",
    re.IGNORECASE,
)


def parse_text_section_from_readelf_output(output: str) -> tuple[int, int]:
    """Return (.text_offset, .text_size) in bytes from readelf -SW stdout."""
    for line in output.splitlines():
        m = _TEXT_SECTION_RE.search(line)
        if m:
            return int(m.group("offset"), 16), int(m.group("size"), 16)
    return 0, 0


def read_text_section(elf_path: Path | str) -> tuple[int, int]:
    """Run readelf -SW on elf_path; return (.text_offset, .text_size)."""
    out = subprocess.check_output(["readelf", "-SW", str(elf_path)], text=True)
    return parse_text_section_from_readelf_output(out)


def read_text_size(elf_path: Path | str) -> int:
    """Return .text Size column in bytes (never Offset)."""
    _offset, size = read_text_section(elf_path)
    return size
