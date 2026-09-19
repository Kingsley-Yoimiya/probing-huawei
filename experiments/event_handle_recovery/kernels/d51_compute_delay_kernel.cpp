/**
 * D51 V4.4 compute-stream device-work kernel.
 * Raw volatile __gm__ GM proof record; runtime nonce + iters driven.
 */
#include "kernel_operator.h"
#include "d51_scratch_layout.h"

using namespace AscendC;

#define EXPORT_AIV_META_INFO(kernel_name)                              \
    static const struct FunLevelKType kernel_name##_kernel_type_section \
        __attribute__((used, section(".ascend.meta." #kernel_name))) = \
            {{F_TYPE_KTYPE, sizeof(unsigned int), K_TYPE_AIV}}

#define D51_COMPILER_BARRIER() asm volatile("" ::: "memory")

__aicore__ inline uint32_t DependentRound(uint32_t x, uint32_t r) {
    x = x * 0x9E3779B9u + 0x85EBCA6Bu + r;
    x ^= (x >> 16);
    x = (x << 7) | (x >> 25);
    return x;
}

__attribute__((noinline, optnone))
__aicore__ static uint32_t WorkUnit64Rounds(uint32_t x, uint32_t r_base) {
    for (uint32_t r = 0; r < kD51WorkRounds; ++r) {
        x = DependentRound(x, r_base + r);
    }
    return x;
}

extern "C" __global__ __aicore__ void d51_compute_delay_kernel(GM_ADDR scratch,
                                                             uint32_t scratch_elems,
                                                             uint32_t iters) {
    const uint32_t elems = scratch_elems > 0 ? scratch_elems : kD51ScratchElems;
    volatile __gm__ uint32_t* gm = reinterpret_cast<volatile __gm__ uint32_t*>(scratch);
    const uint32_t nonce = gm[kD51SlotNonce];
    uint32_t live = gm[kD51LiveStateSlot];

#pragma clang loop unroll(disable)
#pragma clang loop vectorize(disable)
#pragma clang loop interleave(disable)
    for (uint32_t k = 0; k < iters; ++k) {
        const uint32_t ring_idx = k % kD51ProofRingSize;
        uint32_t x = live ^ nonce ^ k;
        x = WorkUnit64Rounds(x, k * kD51WorkRounds);
        const uint32_t proof_val = x ^ (nonce * 0x9E3779B9u) ^ k;

        gm[kD51ProofRingStart + ring_idx] = proof_val;
        D51_COMPILER_BARRIER();
        const uint32_t readback = gm[kD51ProofRingStart + ring_idx];
        live = readback ^ x ^ k;
        gm[kD51LiveStateSlot] = live;
        D51_COMPILER_BARRIER();
        (void)elems;
    }

    uint32_t summary = nonce ^ iters;
    for (uint32_t i = 0; i < kD51ProofRingSize; ++i) {
        summary ^= gm[kD51ProofRingStart + i];
    }
    gm[kD51SummarySlot] = summary;
    gm[kD51FinalStateSlot] = live;
    gm[kD51DoneMarkSlot] = summary ^ live ^ nonce ^ (iters * 0x27D4EB2Du);
}

EXPORT_AIV_META_INFO(d51_compute_delay_kernel);
