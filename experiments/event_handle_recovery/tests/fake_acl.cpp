#include "../event_trace_format.h"

#include <atomic>
#include <cstdint>
#include <cstring>
#include <map>
#include <mutex>

namespace {

using aclError = int32_t;
using aclrtEvent = void*;
using aclrtStream = void*;

std::atomic<uint64_t> g_next_handle{0x1000};
std::mutex g_mu;
std::map<uint64_t, bool> g_alive;
std::atomic<int32_t> g_fail_next_create{0};
std::atomic<int32_t> g_fail_next_record{0};
std::atomic<int32_t> g_fail_next_reset{0};
std::atomic<int32_t> g_fail_next_destroy{0};
std::atomic<int32_t> g_fail_next_wait{0};
std::atomic<int32_t> g_fail_next_hostfunc{0};
std::atomic<int32_t> g_hide_ex{0};
std::atomic<int32_t> g_create_calls{0};
std::atomic<int32_t> g_hostfunc_calls{0};
std::atomic<uint64_t> g_last_hostfunc_stream{0};
std::atomic<int32_t> g_record_calls_before_hostfunc{0};
std::atomic<int32_t> g_record_calls{0};

uint64_t PtrKey(void* p) { return reinterpret_cast<uint64_t>(p); }

}  // namespace

extern "C" {

void fake_acl_reset() {
    std::lock_guard<std::mutex> lock(g_mu);
    g_alive.clear();
    g_next_handle.store(0x1000);
    g_fail_next_create.store(0);
    g_fail_next_record.store(0);
    g_fail_next_reset.store(0);
    g_fail_next_destroy.store(0);
    g_fail_next_wait.store(0);
    g_fail_next_hostfunc.store(0);
    g_hide_ex.store(0);
    g_create_calls.store(0);
    g_hostfunc_calls.store(0);
    g_last_hostfunc_stream.store(0);
    g_record_calls_before_hostfunc.store(0);
    g_record_calls.store(0);
}

void fake_acl_set_fail_create(int n) { g_fail_next_create.store(n); }
void fake_acl_set_fail_record(int n) { g_fail_next_record.store(n); }
void fake_acl_set_fail_reset(int n) { g_fail_next_reset.store(n); }
void fake_acl_set_fail_destroy(int n) { g_fail_next_destroy.store(n); }
void fake_acl_set_fail_wait(int n) { g_fail_next_wait.store(n); }
void fake_acl_set_fail_hostfunc(int n) { g_fail_next_hostfunc.store(n); }
void fake_acl_set_hide_ex(int n) { g_hide_ex.store(n); }
int fake_acl_create_calls() { return g_create_calls.load(); }
int fake_acl_hostfunc_calls() { return g_hostfunc_calls.load(); }
uint64_t fake_acl_last_hostfunc_stream() { return g_last_hostfunc_stream.load(); }
int fake_acl_record_calls() { return g_record_calls.load(); }
int fake_acl_record_calls_before_hostfunc() { return g_record_calls_before_hostfunc.load(); }

using aclrtHostFunc = void (*)(void*);

aclError aclrtLaunchHostFunc(aclrtStream stream, aclrtHostFunc fn, void* args) {
    if (g_fail_next_hostfunc.fetch_sub(1) > 0) {
        return 1;
    }
    g_hostfunc_calls.fetch_add(1);
    g_last_hostfunc_stream.store(PtrKey(stream));
    g_record_calls_before_hostfunc.store(g_record_calls.load());
    if (fn != nullptr) {
        fn(args);
    }
    return 0;
}

aclError aclrtCreateEvent(aclrtEvent* event) {
    g_create_calls.fetch_add(1);
    if (g_fail_next_create.fetch_sub(1) > 0) {
        return 1;
    }
    const uint64_t h = g_next_handle.fetch_add(8);
    void* ptr = reinterpret_cast<void*>(h);
    {
        std::lock_guard<std::mutex> lock(g_mu);
        g_alive[h] = true;
    }
    if (event) {
        *event = ptr;
    }
    return 0;
}

aclError aclrtCreateEventWithFlag(aclrtEvent* event, uint32_t) {
    return aclrtCreateEvent(event);
}

aclError aclrtCreateEventExWithFlag(aclrtEvent* event, uint32_t flag) {
    if (g_hide_ex.load() != 0) {
        return 1;
    }
    return aclrtCreateEventWithFlag(event, flag);
}

aclError aclrtDestroyEvent(aclrtEvent event) {
    if (g_fail_next_destroy.fetch_sub(1) > 0) {
        return 1;
    }
    const uint64_t key = PtrKey(event);
    std::lock_guard<std::mutex> lock(g_mu);
    auto it = g_alive.find(key);
    if (it == g_alive.end() || !it->second) {
        return 1;
    }
    it->second = false;
    return 0;
}

aclError aclrtRecordEvent(aclrtEvent event, aclrtStream) {
    g_record_calls.fetch_add(1);
    if (g_fail_next_record.fetch_sub(1) > 0) {
        return 1;
    }
    const uint64_t key = PtrKey(event);
    std::lock_guard<std::mutex> lock(g_mu);
    auto it = g_alive.find(key);
    if (it == g_alive.end() || !it->second) {
        return 1;
    }
    return 0;
}

aclError aclrtResetEvent(aclrtEvent event, aclrtStream) {
    if (g_fail_next_reset.fetch_sub(1) > 0) {
        return 1;
    }
    const uint64_t key = PtrKey(event);
    std::lock_guard<std::mutex> lock(g_mu);
    auto it = g_alive.find(key);
    if (it == g_alive.end() || !it->second) {
        return 1;
    }
    return 0;
}

aclError aclrtStreamWaitEvent(aclrtStream, aclrtEvent event) {
    if (g_fail_next_wait.fetch_sub(1) > 0) {
        return 1;
    }
    const uint64_t key = PtrKey(event);
    std::lock_guard<std::mutex> lock(g_mu);
    auto it = g_alive.find(key);
    if (it == g_alive.end() || !it->second) {
        return 1;
    }
    return 0;
}

aclError aclrtStreamWaitEventWithTimeout(aclrtStream stream, aclrtEvent event, int32_t) {
    return aclrtStreamWaitEvent(stream, event);
}

}  // extern "C"
