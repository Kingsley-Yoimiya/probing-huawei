#include "../event_trace_format.h"

#include <atomic>
#include <cstdint>

namespace {

using rtError_t = int32_t;
using rtEvent_t = void*;

std::atomic<uint64_t> g_next_handle{0x5000};

}  // namespace

extern "C" {

void fake_rt_reset() { g_next_handle.store(0x5000); }

rtError_t rtEventCreate(rtEvent_t* evt) {
    const uint64_t h = g_next_handle.fetch_add(8);
    if (evt) {
        *evt = reinterpret_cast<rtEvent_t>(h);
    }
    return 0;
}

rtError_t rtEventCreateWithFlag(rtEvent_t* evt, uint32_t) {
    return rtEventCreate(evt);
}

rtError_t rtEventCreateExWithFlag(rtEvent_t* evt, uint32_t) {
    return rtEventCreate(evt);
}

}  // extern "C"
