#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

typedef void (*PrepareFn)(void);

int main(void) {
    const char* so = "/root/event_handle_recovery_v4/build/libacl_event_trace_v2.so";
    setenv("ACL_EVENT_TRACE_DIR", "/tmp/min_prepare_test", 1);
    setenv("ACL_EVENT_WORK_BINARY",
           "/root/event_handle_recovery_v4/build/kernels/d51_compute_delay_kernel.o", 1);
    setenv("RANK", "0", 1);
    void* handle = dlopen(so, RTLD_NOW | RTLD_GLOBAL);
    if (!handle) {
        fprintf(stderr, "dlopen failed: %s\n", dlerror());
        return 1;
    }
    PrepareFn prepare = (PrepareFn)dlsym(handle, "acl_event_work_prepare");
    if (!prepare) {
        fprintf(stderr, "dlsym failed: %s\n", dlerror());
        return 1;
    }
    fprintf(stderr, "calling prepare\n");
    prepare();
    fprintf(stderr, "prepare_ok\n");
    return 0;
}
