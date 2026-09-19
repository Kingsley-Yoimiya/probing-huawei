/**
 * TEMP: V4.2 vectorized kernel for chainout A/B.
 */
#include "kernel_operator.h"
using namespace AscendC;
#define EXPORT_AIV_META_INFO(kernel_name) static const struct FunLevelKType kernel_name##_kernel_type_section __attribute__((used, section(".ascend.meta." #kernel_name))) = {{F_TYPE_KTYPE, sizeof(unsigned int), K_TYPE_AIV}}
constexpr uint32_t kScratchElems = 256;
class D51ComputeDelayKernel {
public:
    __aicore__ inline void Init(GM_ADDR scratch, uint32_t scratch_elems, uint32_t iters) {
        elems_ = scratch_elems > 0 ? scratch_elems : kScratchElems;
        iters_ = iters;
        scratchGm.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t*>(scratch), elems_);
        pipe.InitBuffer(vecQueue, 1, elems_ * sizeof(uint32_t));
    }
    __aicore__ inline void Process() {
        LocalTensor<uint32_t> local = vecQueue.AllocTensor<uint32_t>();
        DataCopy(local, scratchGm, elems_);
        PipeBarrier<PIPE_ALL>();
        for (uint32_t k = 0; k < iters_; ++k) {
            for (uint32_t i = 0; i < elems_; ++i) {
                const uint32_t v = local.GetValue(i);
                local.SetValue(i, v + 1u + (k & 0xFFu));
            }
            PipeBarrier<PIPE_ALL>();
        }
        DataCopy(scratchGm, local, elems_);
        vecQueue.FreeTensor(local);
    }
private:
    TPipe pipe;
    TQue<QuePosition::VECIN, 1> vecQueue;
    GlobalTensor<uint32_t> scratchGm;
    uint32_t elems_{kScratchElems};
    uint32_t iters_{0};
};
extern "C" __global__ __aicore__ void d51_compute_delay_kernel(GM_ADDR scratch, uint32_t scratch_elems, uint32_t iters) {
    D51ComputeDelayKernel op; op.Init(scratch, scratch_elems, iters); op.Process();
}
EXPORT_AIV_META_INFO(d51_compute_delay_kernel);
