/**
 * V4.4 audit negative control: no proof loop / GM dependency stores.
 * Compiled with same flags; must not be loaded for treatment.
 */
#include "kernel_operator.h"
#include "d51_scratch_layout.h"

using namespace AscendC;

#define EXPORT_AIV_META_INFO(kernel_name)                              \
    static const struct FunLevelKType kernel_name##_kernel_type_section \
        __attribute__((used, section(".ascend.meta." #kernel_name))) = \
            {{F_TYPE_KTYPE, sizeof(unsigned int), K_TYPE_AIV}}

extern "C" __global__ __aicore__ void d51_compute_delay_kernel(GM_ADDR scratch,
                                                             uint32_t scratch_elems,
                                                             uint32_t iters) {
    volatile __gm__ uint32_t* gm = reinterpret_cast<volatile __gm__ uint32_t*>(scratch);
    const uint32_t nonce = gm[kD51SlotNonce];
    (void)scratch_elems;
    (void)iters;
    gm[kD51DoneMarkSlot] = nonce ^ 0xDEADBEEFu;
}

EXPORT_AIV_META_INFO(d51_compute_delay_kernel);
