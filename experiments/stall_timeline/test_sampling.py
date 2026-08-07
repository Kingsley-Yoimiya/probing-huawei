#!/usr/bin/env python3
"""Small dependency-free checks for the sampling plans."""

import unittest

from probe import _sample_plan


def sampled(scheme: str, step: int, rank: int, **kwargs) -> bool:
    hit, _ = _sample_plan(
        scheme,
        step=step,
        rank=rank,
        world=kwargs.get("world", 32),
        rate=kwargs.get("rate", 0.1),
        sample_ranks=kwargs.get("sample_ranks", 4),
        seed=kwargs.get("seed", 20260806),
    )
    return hit


class SamplingPlanTest(unittest.TestCase):
    def test_rotate_has_fixed_budget_and_spans_both_nodes(self) -> None:
        for step in range(16):
            ranks = [r for r in range(32) if sampled("rotate", step, r)]
            self.assertEqual(len(ranks), 4)
            self.assertTrue(any(r < 16 for r in ranks))
            self.assertTrue(any(r >= 16 for r in ranks))

    def test_rotate_covers_every_rank_in_eight_steps(self) -> None:
        seen = {r for step in range(8) for r in range(32) if sampled("rotate", step, r)}
        self.assertEqual(seen, set(range(32)))

    def test_random_is_deterministic_and_near_target_rate(self) -> None:
        xs = [sampled("random", step, rank) for step in range(1000) for rank in range(32)]
        ys = [sampled("random", step, rank) for step in range(1000) for rank in range(32)]
        self.assertEqual(xs, ys)
        self.assertLess(abs(sum(xs) / len(xs) - 0.1), 0.01)


if __name__ == "__main__":
    unittest.main()
