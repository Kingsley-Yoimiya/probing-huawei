#include <acl/acl_rt.h>

#include <cstdint>
#include <dlfcn.h>
#include <time.h>

namespace {

uint64_t NowNs() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    return static_cast<uint64_t>(ts.tv_sec) * 1000000000ULL + static_cast<uint64_t>(ts.tv_nsec);
}

void Record(const char* name, uint64_t start_ns, uint64_t end_ns, uintptr_t stream_token) {
    using RecordFunction = void (*)(const char*, uint64_t, uint64_t, uintptr_t);
    auto record = reinterpret_cast<RecordFunction>(
        dlsym(RTLD_DEFAULT, "mspti_skeleton_record_sync"));
    if (record != nullptr) {
        record(name, start_ns, end_ns, stream_token);
    }
}

}  // namespace

extern "C" {

aclError aclrtSynchronizeStream(aclrtStream stream) {
    using Function = aclError (*)(aclrtStream);
    static Function real = reinterpret_cast<Function>(dlsym(RTLD_NEXT, "aclrtSynchronizeStream"));
    if (real == nullptr) {
        return static_cast<aclError>(1);
    }
    const uint64_t start_ns = NowNs();
    const aclError result = real(stream);
    Record("aclrtSynchronizeStream", start_ns, NowNs(), reinterpret_cast<uintptr_t>(stream));
    return result;
}

aclError aclrtSynchronizeStreamWithTimeout(aclrtStream stream, int32_t timeout) {
    using Function = aclError (*)(aclrtStream, int32_t);
    static Function real = reinterpret_cast<Function>(
        dlsym(RTLD_NEXT, "aclrtSynchronizeStreamWithTimeout"));
    if (real == nullptr) {
        return static_cast<aclError>(1);
    }
    const uint64_t start_ns = NowNs();
    const aclError result = real(stream, timeout);
    Record(
        "aclrtSynchronizeStreamWithTimeout", start_ns, NowNs(),
        reinterpret_cast<uintptr_t>(stream));
    return result;
}

aclError aclrtSynchronizeDevice() {
    using Function = aclError (*)();
    static Function real = reinterpret_cast<Function>(dlsym(RTLD_NEXT, "aclrtSynchronizeDevice"));
    if (real == nullptr) {
        return static_cast<aclError>(1);
    }
    const uint64_t start_ns = NowNs();
    const aclError result = real();
    Record("aclrtSynchronizeDevice", start_ns, NowNs(), 0);
    return result;
}

aclError aclrtSynchronizeDeviceWithTimeout(int32_t timeout) {
    using Function = aclError (*)(int32_t);
    static Function real = reinterpret_cast<Function>(
        dlsym(RTLD_NEXT, "aclrtSynchronizeDeviceWithTimeout"));
    if (real == nullptr) {
        return static_cast<aclError>(1);
    }
    const uint64_t start_ns = NowNs();
    const aclError result = real(timeout);
    Record("aclrtSynchronizeDeviceWithTimeout", start_ns, NowNs(), 0);
    return result;
}

}
