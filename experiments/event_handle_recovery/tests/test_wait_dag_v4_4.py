#!/usr/bin/env python3
"""Unit tests for V4.4 host reference and layout."""
from __future__ import annotations

import unittest

from d51_work_unit_reference import (
    DONE_MARK_SLOT,
    SUMMARY_SLOT,
    guard_slots,
    init_scratch_with_nonce,
    proof_slots,
    run_reference,
)


class TestV44Reference(unittest.TestCase):
    def test_guards_stable_under_zero_iters(self) -> None:
        nonce = 0x12345678
        init = init_scratch_with_nonce(nonce)
        out, _ = run_reference(list(init), 0)
        for i in guard_slots():
            self.assertEqual(out[i], init[i])

    def test_proof_changes_with_iters(self) -> None:
        nonce = 0xABCDEF00
        init = init_scratch_with_nonce(nonce)
        out1, m1 = run_reference(list(init), 1)
        out2, m2 = run_reference(list(init), 2)
        self.assertNotEqual(m1["summary"], m2["summary"])
        self.assertTrue(any(out1[i] != init[i] for i in proof_slots()))

    def test_three_distinct_summaries_fixed_nonce(self) -> None:
        nonce = 0xFACEFEED
        init = init_scratch_with_nonce(nonce)
        summaries = set()
        for iters in (1, 2, 17, 257):
            _, meta = run_reference(list(init), iters)
            summaries.add(meta["summary"])
        self.assertGreaterEqual(len(summaries), 3)

    def test_done_mark_non_constant(self) -> None:
        nonce = 0x11112222
        init = init_scratch_with_nonce(nonce)
        _, m1 = run_reference(list(init), 4)
        _, m2 = run_reference(list(init), 8)
        self.assertNotEqual(m1["done_mark"], m2["done_mark"])
        self.assertNotEqual(m1["summary"], 0)
        self.assertNotEqual(m1["done_mark"], init[DONE_MARK_SLOT])


if __name__ == "__main__":
    unittest.main()
