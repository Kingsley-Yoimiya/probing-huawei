#!/usr/bin/env python3
"""V4.5 parser fixture + STOP classification regression tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from elf_section_parser import parse_text_section_from_readelf_output
from kernel_disasm_audit_v4_5 import audit_disasm_body, is_disasm_decodable

# Fixture: same Offset 0xe8, different Size (prod 0x1a0=416, neg 0x64=100)
READELF_PROD = """\
Section Headers:
  [Nr] Name              Type            Address          Off    Size   ES Flg Lk Inf Al
  [ 1] .text             PROGBITS        0000000000000000 0000e8 0001a0 00  AX  0   0  4
"""

READELF_NEG = """\
Section Headers:
  [Nr] Name              Type            Address          Off    Size   ES Flg Lk Inf Al
  [ 1] .text             PROGBITS        0000000000000000 0000e8 000064 00  AX  0   0  4
"""

DISASM_ALL_NOT_AVAILABLE = """\
d51_compute_delay_kernel.o:     file format elf64-hiipu

Disassembly of section .text:

0000000000000000 <d51_compute_delay_kernel>:
       0:   <not available>
       4:   <not available>
"""

DISASM_VALID_BODY = """\
Disassembly of section .text:

0000000000000000 <d51_compute_delay_kernel>:
       0:   ld    r0, [gm]
       4:   st    r1, [gm]
       8:   b.ne  0x0
      10:   ld    r2, [gm]
      14:   st    r3, [gm]
      18:   b     0x8
      1c:   ld    r4, [gm]
      20:   st    r5, [gm]
      24:   ld    r6, [gm]
      28:   st    r7, [gm]
      2c:   ld    r8, [gm]
      30:   st    r9, [gm]
      34:   ld    r10, [gm]
      38:   st    r11, [gm]
      3c:   ld    r12, [gm]
      40:   st    r13, [gm]
      44:   ld    r14, [gm]
      48:   st    r15, [gm]
"""

DISASM_VALID_NO_BODY = """\
Disassembly of section .text:

0000000000000000 <d51_compute_delay_kernel>:
       0:   nop
       4:   nop
       8:   ret
"""


class TestReadelfSizeParser(unittest.TestCase):
    def test_prod_size_not_offset(self) -> None:
        off, size = parse_text_section_from_readelf_output(READELF_PROD)
        self.assertEqual(off, 0xE8)
        self.assertEqual(size, 0x1A0)
        self.assertEqual(size, 416)
        self.assertNotEqual(size, off)
        self.assertNotEqual(size, 232)

    def test_neg_size_not_offset(self) -> None:
        off, size = parse_text_section_from_readelf_output(READELF_NEG)
        self.assertEqual(off, 0xE8)
        self.assertEqual(size, 0x64)
        self.assertEqual(size, 100)
        self.assertNotEqual(size, off)

    def test_same_offset_different_sizes(self) -> None:
        _, prod_size = parse_text_section_from_readelf_output(READELF_PROD)
        _, neg_size = parse_text_section_from_readelf_output(READELF_NEG)
        self.assertEqual(prod_size, 416)
        self.assertEqual(neg_size, 100)
        self.assertNotEqual(prod_size, neg_size)


class TestDisasmClassification(unittest.TestCase):
    def test_all_not_available_not_decodable(self) -> None:
        self.assertFalse(is_disasm_decodable(DISASM_ALL_NOT_AVAILABLE))

    def test_valid_body_decodable(self) -> None:
        self.assertTrue(is_disasm_decodable(DISASM_VALID_BODY))
        audit = audit_disasm_body(DISASM_VALID_BODY)
        self.assertTrue(audit["has_backward_edge_hint"])
        self.assertGreaterEqual(audit["instruction_line_count"], 16)

    def test_valid_but_no_body_decodable(self) -> None:
        self.assertTrue(is_disasm_decodable(DISASM_VALID_NO_BODY))
        audit = audit_disasm_body(DISASM_VALID_NO_BODY)
        self.assertLess(audit["instruction_line_count"], 16)


class TestStopClassificationIntegration(unittest.TestCase):
    """Run kernel_disasm_audit_v4_5.main logic via subprocess-free helpers."""

    def test_not_available_implies_disasm_unavailable_class(self) -> None:
        audit = audit_disasm_body(DISASM_ALL_NOT_AVAILABLE)
        self.assertFalse(audit["decodable"])
        # Orchestrator maps this to STOP_KERNEL_DISASM_UNAVAILABLE, not BODY_NOT_PRESENT

    def test_body_missing_only_when_decodable(self) -> None:
        audit = audit_disasm_body(DISASM_VALID_NO_BODY)
        self.assertTrue(audit["decodable"])
        self.assertLess(audit["instruction_line_count"], 16)


if __name__ == "__main__":
    unittest.main()
