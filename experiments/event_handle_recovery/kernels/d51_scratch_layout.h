#pragma once

#include <cstdint>

// Shared scratch layout for V4.4 GM proof record (256 x uint32_t = 1024 bytes).
constexpr uint32_t kD51ScratchElems = 256;
constexpr uint32_t kD51WorkRounds = 64;
constexpr uint32_t kD51ProofRingSize = 64;

constexpr uint32_t kD51SlotNonce = 0;
constexpr uint32_t kD51GuardHeadStart = 1;
constexpr uint32_t kD51GuardHeadEnd = 4;
constexpr uint32_t kD51ProofRingStart = 4;
constexpr uint32_t kD51ProofRingEnd = 68;
constexpr uint32_t kD51LiveStateSlot = 68;
constexpr uint32_t kD51SummarySlot = 250;
constexpr uint32_t kD51FinalStateSlot = 251;
constexpr uint32_t kD51DoneMarkSlot = 252;
constexpr uint32_t kD51GuardTailStart = 253;
constexpr uint32_t kD51GuardTailEnd = 256;

inline uint32_t D51SlotPattern(uint32_t nonce, uint32_t slot, uint32_t salt) {
    uint32_t x = nonce ^ salt ^ (slot * 0x85EBCA6Bu);
    x = x * 0x9E3779B9u + 0xC2B2AE35u;
    x ^= (x >> 16);
    return (x << 7) | (x >> 25);
}
