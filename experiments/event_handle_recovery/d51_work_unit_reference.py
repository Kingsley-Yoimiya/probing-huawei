#!/usr/bin/env python3
"""Host reference for V4.4 d51_compute_delay_kernel volatile GM proof record."""
from __future__ import annotations

K_WORK_ROUNDS = 64
DEFAULT_SCRATCH_ELEMS = 256
PROOF_RING_SIZE = 64

SLOT_NONCE = 0
GUARD_HEAD_START = 1
GUARD_HEAD_END = 4
PROOF_RING_START = 4
PROOF_RING_END = 68
LIVE_STATE_SLOT = 68
SUMMARY_SLOT = 250
FINAL_STATE_SLOT = 251
DONE_MARK_SLOT = 252
GUARD_TAIL_START = 253
GUARD_TAIL_END = 256


def slot_pattern(nonce: int, slot: int, salt: int) -> int:
    x = (nonce ^ salt ^ (slot * 0x85EBCA6B)) & 0xFFFFFFFF
    x = (x * 0x9E3779B9 + 0xC2B2AE35) & 0xFFFFFFFF
    x ^= (x >> 16)
    return ((x << 7) | (x >> 25)) & 0xFFFFFFFF


def dependent_round(x: int, r: int) -> int:
    x &= 0xFFFFFFFF
    x = (x * 0x9E3779B9 + 0x85EBCA6B + r) & 0xFFFFFFFF
    x ^= (x >> 16)
    x = ((x << 7) | (x >> 25)) & 0xFFFFFFFF
    return x


def work_unit_64_rounds(x: int, r_base: int) -> int:
    for r in range(K_WORK_ROUNDS):
        x = dependent_round(x, (r_base + r) & 0xFFFFFFFF)
    return x & 0xFFFFFFFF


def init_scratch_with_nonce(
    runtime_nonce: int, elems: int = DEFAULT_SCRATCH_ELEMS
) -> list[int]:
    nonce = runtime_nonce & 0xFFFFFFFF
    scratch = [0] * elems
    scratch[SLOT_NONCE] = nonce
    for i in range(GUARD_HEAD_START, GUARD_HEAD_END):
        scratch[i] = slot_pattern(nonce, i, 0xC1A2B3C4)
    for i in range(PROOF_RING_START, PROOF_RING_END):
        scratch[i] = slot_pattern(nonce, i, 0x0E00F00)
    scratch[LIVE_STATE_SLOT] = slot_pattern(nonce, LIVE_STATE_SLOT, 0x57A7E00)
    for i in range(GUARD_TAIL_START, GUARD_TAIL_END):
        scratch[i] = slot_pattern(nonce, i, 0xC0A2D00)
    return scratch


def init_scratch_pattern(elems: int = DEFAULT_SCRATCH_ELEMS) -> list[int]:
  """Legacy V4.3 pattern for backward-compat tests only."""
  return [(0xA51B0000 + i) & 0xFFFFFFFF for i in range(elems)]


def run_reference(scratch: list[int], iters: int) -> tuple[list[int], dict]:
    """Mirror V4.4 device kernel: volatile GM proof ring per work unit."""
    state = [v & 0xFFFFFFFF for v in scratch]
    elems = len(state)
    nonce = state[SLOT_NONCE]
    live = state[LIVE_STATE_SLOT]
    for k in range(iters):
        ring_idx = k % PROOF_RING_SIZE
        x = (live ^ nonce ^ k) & 0xFFFFFFFF
        x = work_unit_64_rounds(x, (k * K_WORK_ROUNDS) & 0xFFFFFFFF)
        proof_val = (x ^ (nonce * 0x9E3779B9) ^ k) & 0xFFFFFFFF
        state[PROOF_RING_START + ring_idx] = proof_val
        readback = proof_val
        live = (readback ^ x ^ k) & 0xFFFFFFFF
        state[LIVE_STATE_SLOT] = live
    summary = (nonce ^ iters) & 0xFFFFFFFF
    for i in range(PROOF_RING_SIZE):
        summary ^= state[PROOF_RING_START + i]
        summary &= 0xFFFFFFFF
    state[SUMMARY_SLOT] = summary
    state[FINAL_STATE_SLOT] = live
    state[DONE_MARK_SLOT] = (summary ^ live ^ nonce ^ (iters * 0x27D4EB2D)) & 0xFFFFFFFF
    meta = {
        "summary": state[SUMMARY_SLOT],
        "final_state": state[FINAL_STATE_SLOT],
        "done_mark": state[DONE_MARK_SLOT],
        "elems": elems,
    }
    return state, meta


def guard_slots() -> list[int]:
    head = list(range(GUARD_HEAD_START, GUARD_HEAD_END))
    tail = list(range(GUARD_TAIL_START, GUARD_TAIL_END))
    return head + tail


def proof_slots() -> list[int]:
    return list(range(PROOF_RING_START, PROOF_RING_END)) + [
        LIVE_STATE_SLOT,
        SUMMARY_SLOT,
        FINAL_STATE_SLOT,
        DONE_MARK_SLOT,
    ]


def checksum_from_scratch(scratch: list[int]) -> int:
    return scratch[0] & 0xFFFFFFFF
