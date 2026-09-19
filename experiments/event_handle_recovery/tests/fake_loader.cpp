#include <cstdint>
#include <cstdio>
#include <dlfcn.h>

extern "C" {

using aclError = int32_t;
using aclrtEvent = void*;

using FnCreateEx = aclError (*)(aclrtEvent*, uint32_t);

int fake_loader_probe_ex(void* lib_handle, void** out_wrapper, int* real_calls) {
    if (out_wrapper) {
        *out_wrapper = nullptr;
    }
    if (real_calls) {
        *real_calls = 0;
    }
    void* h = lib_handle;
    if (h == nullptr) {
        h = dlopen("libfake_acl.so", RTLD_NOW | RTLD_LOCAL);
        if (h == nullptr) {
            return 1;
        }
    }
    auto* fn = reinterpret_cast<FnCreateEx>(dlsym(h, "aclrtCreateEventExWithFlag"));
    if (fn == nullptr) {
        return 2;
    }
    aclrtEvent ev = nullptr;
    const aclError rc = fn(&ev, 0);
    if (rc != 0 || ev == nullptr) {
        return 3;
    }
    if (out_wrapper) {
        *out_wrapper = reinterpret_cast<void*>(fn);
    }
    return 0;
}

int fake_loader_probe_missing_ex(void* lib_handle) {
    void* h = lib_handle;
    if (h == nullptr) {
        h = dlopen("libfake_acl.so", RTLD_NOW | RTLD_LOCAL);
    }
    auto* fn = reinterpret_cast<FnCreateEx>(dlsym(h, "aclrtCreateEventExWithFlag"));
    if (fn != nullptr) {
        return 1;
    }
    auto* fb = reinterpret_cast<FnCreateEx>(dlsym(h, "aclrtCreateEventWithFlag"));
    return fb == nullptr ? 2 : 0;
}

}  // extern "C"
