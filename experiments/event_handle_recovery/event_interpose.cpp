#include "event_trace_format.h"
#include "device_work.h"

#ifdef ACL_EVENT_TRACE_USE_STUB
#include "acl/acl_rt.h"
#else
#include <acl/acl_rt.h>
#endif

#ifdef ACL_EVENT_TRACE_RT_ABI
#include <runtime/runtime/event.h>
#endif

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <fcntl.h>
#include <pthread.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#if !defined(ACL_EVENT_TRACE_USE_STUB) || defined(ACL_EVENT_TRACE_USE_WRAP)
extern "C" void* real_dlsym(void* handle, const char* symbol);
extern "C" void* real_dlvsym(void* handle, const char* symbol, const char* version);
#define REAL_DLSYM real_dlsym
#define REAL_DLVSYM real_dlvsym
#else
#define REAL_DLSYM dlsym
#define REAL_DLVSYM dlvsym
#endif

namespace {

inline uint64_t ReadClock(clockid_t id) {
    timespec ts{};
    clock_gettime(id, &ts);
    return static_cast<uint64_t>(ts.tv_sec) * 1000000000ULL +
           static_cast<uint64_t>(ts.tv_nsec);
}

inline uint32_t GetTid() {
    return static_cast<uint32_t>(syscall(SYS_gettid));
}

inline int ParseEnvInt(const char* key, int fallback) {
    const char* v = getenv(key);
    if (v == nullptr || *v == '\0') {
        return fallback;
    }
    char* end = nullptr;
    const long parsed = strtol(v, &end, 10);
    if (end == v) {
        return fallback;
    }
    return static_cast<int>(parsed);
}

inline uint64_t ParseEnvU64(const char* key, uint64_t fallback) {
    const char* v = getenv(key);
    if (v == nullptr || *v == '\0') {
        return fallback;
    }
    char* end = nullptr;
    const unsigned long long parsed = strtoull(v, &end, 10);
    if (end == v) {
        return fallback;
    }
    return static_cast<uint64_t>(parsed);
}

uint32_t Crc32(const void* data, size_t len) {
    static uint32_t table[256];
    static std::atomic<int> init{0};
    if (init.load(std::memory_order_acquire) == 0) {
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t c = i;
            for (int j = 0; j < 8; ++j) {
                c = (c & 1u) ? (0xEDB88320u ^ (c >> 1u)) : (c >> 1u);
            }
            table[i] = c;
        }
        init.store(1, std::memory_order_release);
    }
    uint32_t crc = 0xFFFFFFFFu;
    const auto* p = static_cast<const uint8_t*>(data);
    for (size_t i = 0; i < len; ++i) {
        crc = table[(crc ^ p[i]) & 0xFFu] ^ (crc >> 8u);
    }
    return crc ^ 0xFFFFFFFFu;
}

struct TraceState {
    AclEventTraceRecord* records{nullptr};
    AclEventTraceResolverAuditEntry resolver_audit[ACL_EVENT_TRACE_RESOLVER_AUDIT_CAP]{};
    uint64_t capacity{ACL_EVENT_TRACE_DEFAULT_CAPACITY};
    std::atomic<uint64_t> claim_seq{0};
    std::atomic<uint64_t> call_seq{0};
    std::atomic<uint64_t> committed{0};
    std::atomic<uint64_t> dropped{0};
    std::atomic<uint64_t> fatal{0};
    std::atomic<uint64_t> late_calls{0};
    std::atomic<uint64_t> resolver_queries{0};
    std::atomic<uint32_t> resolver_audit_written{0};
    std::atomic<uint64_t> resolver_wrappers_returned{0};
    std::atomic<uint64_t> resolver_target_conflict{0};
    std::atomic<uint64_t> resolver_audit_overflow{0};
    std::atomic<uint64_t> acl_create_wrapper_calls{0};
    std::atomic<uint64_t> rt_create_wrapper_calls{0};
    std::atomic<uint64_t> logical_create_count{0};
    std::atomic<bool> initialized{false};
    std::atomic<bool> finalized{false};
    std::atomic<int> finalize_guard{0};
    int pid{0};
    int rank{-1};
    int local_rank{-1};
    char out_dir[512];
};

TraceState g_state;
thread_local int g_reentrancy_depth = 0;
thread_local int g_dlsym_reentrancy = 0;
thread_local uint64_t g_acl_parent_call_sequence = 0;
thread_local int g_acl_create_nest_depth = 0;
thread_local void* g_dlsym_resolved_create_real = nullptr;

enum CreateTarget : uint8_t {
    kCreateNone = 0,
    kCreateAclEvent = 1,
    kCreateAclWithFlag = 2,
    kCreateAclExWithFlag = 3,
};

struct TargetAddrState {
    std::atomic<uint64_t> first_real{0};
    std::atomic<uint64_t> conflict{0};
};

TargetAddrState g_target_acl_create;
TargetAddrState g_target_acl_with_flag;
TargetAddrState g_target_acl_ex_with_flag;

using FnCreate = aclError (*)(aclrtEvent*);
using FnCreateWithFlag = aclError (*)(aclrtEvent*, uint32_t);
using FnCreateExWithFlag = aclError (*)(aclrtEvent*, uint32_t);
using FnDestroy = aclError (*)(aclrtEvent);
using FnRecord = aclError (*)(aclrtEvent, aclrtStream);
using FnReset = aclError (*)(aclrtEvent, aclrtStream);
using FnWait = aclError (*)(aclrtStream, aclrtEvent);
using FnWaitTimeout = aclError (*)(aclrtStream, aclrtEvent, int32_t);

FnCreate real_create = nullptr;
FnCreateWithFlag real_create_with_flag = nullptr;
FnCreateExWithFlag real_create_ex_with_flag = nullptr;
FnDestroy real_destroy = nullptr;
FnRecord real_record = nullptr;
FnReset real_reset = nullptr;
FnWait real_wait = nullptr;
FnWaitTimeout real_wait_timeout = nullptr;

using aclrtHostFunc = void (*)(void*);
using FnLaunchHostFunc = aclError (*)(aclrtStream, aclrtHostFunc, void*);
FnLaunchHostFunc real_launch_hostfunc = nullptr;

constexpr int kDelayTargetRecordOrdinal = 3;

struct DelayCallbackState {
    uint64_t delay_ns{0};
    std::atomic<uint64_t> enter_monotonic_ns{0};
    std::atomic<uint64_t> exit_monotonic_ns{0};
    std::atomic<uint32_t> invoke_count{0};
};

struct DelayInjectState {
    std::atomic<bool> armed{false};
    std::atomic<uint32_t> arm_tid{0};
    std::atomic<uint32_t> armed_tid{0};  // record_issuing_tid after first successful Record
    std::atomic<int> active_record_success_ord{0};
    std::atomic<int> active_wait_success_ord{0};
    std::atomic<uint64_t> delay_us{0};
    std::atomic<int> match_count{0};
    std::atomic<int32_t> hostfunc_submit_rc{0};
    std::atomic<int32_t> real_record_rc_after_inject{0};
    std::atomic<int> inject_failed{0};
    std::atomic<uint64_t> last_inject_preload_cs{0};
    std::atomic<uint64_t> last_inject_raw_event{0};
    std::atomic<uint64_t> last_inject_raw_stream{0};
};

DelayCallbackState g_delay_cb_state;
DelayInjectState g_delay_state;

#ifdef ACL_EVENT_TRACE_RT_ABI
using FnRtCreate = rtError_t (*)(rtEvent_t*);
using FnRtCreateWithFlag = rtError_t (*)(rtEvent_t*, uint32_t);
using FnRtCreateExWithFlag = rtError_t (*)(rtEvent_t*, uint32_t);
FnRtCreate real_rt_create = nullptr;
FnRtCreateWithFlag real_rt_create_with_flag = nullptr;
FnRtCreateExWithFlag real_rt_create_ex_with_flag = nullptr;
#endif

inline bool CharEq(const char* a, const char* b) {
    while (*a && *b) {
        if (*a != *b) {
            return false;
        }
        ++a;
        ++b;
    }
    return *a == '\0' && *b == '\0';
}

inline bool MatchExact(const char* name, const char* lit) {
    if (name == nullptr) {
        return false;
    }
    return CharEq(name, lit);
}

CreateTarget ClassifyAclCreate(const char* name) {
    if (name == nullptr) {
        return kCreateNone;
    }
    if (MatchExact(name, "aclrtCreateEventExWithFlag")) {
        return kCreateAclExWithFlag;
    }
    if (MatchExact(name, "aclrtCreateEventWithFlag")) {
        return kCreateAclWithFlag;
    }
    if (MatchExact(name, "aclrtCreateEvent")) {
        return kCreateAclEvent;
    }
    return kCreateNone;
}

TargetAddrState* TargetState(CreateTarget t) {
    switch (t) {
        case kCreateAclEvent:
            return &g_target_acl_create;
        case kCreateAclWithFlag:
            return &g_target_acl_with_flag;
        case kCreateAclExWithFlag:
            return &g_target_acl_ex_with_flag;
        default:
            return nullptr;
    }
}

void ResolveSymbols() {
    real_create = reinterpret_cast<FnCreate>(REAL_DLSYM(RTLD_NEXT, "aclrtCreateEvent"));
    real_create_with_flag =
        reinterpret_cast<FnCreateWithFlag>(REAL_DLSYM(RTLD_NEXT, "aclrtCreateEventWithFlag"));
    real_create_ex_with_flag =
        reinterpret_cast<FnCreateExWithFlag>(REAL_DLSYM(RTLD_NEXT, "aclrtCreateEventExWithFlag"));
    real_destroy = reinterpret_cast<FnDestroy>(REAL_DLSYM(RTLD_NEXT, "aclrtDestroyEvent"));
    real_record = reinterpret_cast<FnRecord>(REAL_DLSYM(RTLD_NEXT, "aclrtRecordEvent"));
    real_reset = reinterpret_cast<FnReset>(REAL_DLSYM(RTLD_NEXT, "aclrtResetEvent"));
    real_wait = reinterpret_cast<FnWait>(REAL_DLSYM(RTLD_NEXT, "aclrtStreamWaitEvent"));
    real_wait_timeout = reinterpret_cast<FnWaitTimeout>(
        REAL_DLSYM(RTLD_NEXT, "aclrtStreamWaitEventWithTimeout"));
    real_launch_hostfunc = reinterpret_cast<FnLaunchHostFunc>(
        REAL_DLSYM(RTLD_NEXT, "aclrtLaunchHostFunc"));
#ifdef ACL_EVENT_TRACE_RT_ABI
    real_rt_create = reinterpret_cast<FnRtCreate>(REAL_DLSYM(RTLD_NEXT, "rtEventCreate"));
    real_rt_create_with_flag =
        reinterpret_cast<FnRtCreateWithFlag>(REAL_DLSYM(RTLD_NEXT, "rtEventCreateWithFlag"));
    real_rt_create_ex_with_flag =
        reinterpret_cast<FnRtCreateExWithFlag>(REAL_DLSYM(RTLD_NEXT, "rtEventCreateExWithFlag"));
#endif
}

void InitState() {
    if (g_state.initialized.load(std::memory_order_acquire)) {
        return;
    }
    static pthread_once_t once = PTHREAD_ONCE_INIT;
    pthread_once(&once, []() {
        ResolveSymbols();
        g_state.pid = static_cast<int>(getpid());
        g_state.rank = ParseEnvInt("RANK", -1);
        g_state.local_rank = ParseEnvInt("LOCAL_RANK", -1);
        g_state.capacity = ParseEnvU64("ACL_EVENT_TRACE_CAPACITY", ACL_EVENT_TRACE_DEFAULT_CAPACITY);
        const char* out = getenv("ACL_EVENT_TRACE_DIR");
        if (out != nullptr) {
            std::strncpy(g_state.out_dir, out, sizeof(g_state.out_dir) - 1);
            g_state.out_dir[sizeof(g_state.out_dir) - 1] = '\0';
        } else {
            g_state.out_dir[0] = '\0';
        }
        const size_t bytes = static_cast<size_t>(g_state.capacity) * sizeof(AclEventTraceRecord);
        void* mem = mmap(nullptr, bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (mem == MAP_FAILED) {
            g_state.fatal.fetch_add(1, std::memory_order_relaxed);
            g_state.initialized.store(true, std::memory_order_release);
            return;
        }
        g_state.records = static_cast<AclEventTraceRecord*>(mem);
        std::memset(g_state.records, 0, bytes);
        g_state.initialized.store(true, std::memory_order_release);
    });
}

AclEventTraceRecord* ClaimSlot(uint8_t op, uint32_t extra_flags, uint8_t source,
                               uint8_t resolver_path) {
    InitState();
    if (!g_state.records) {
        g_state.fatal.fetch_add(1, std::memory_order_relaxed);
        return nullptr;
    }
    if (g_state.finalized.load(std::memory_order_acquire)) {
        g_state.late_calls.fetch_add(1, std::memory_order_relaxed);
        return nullptr;
    }
    const bool nested_create =
        (op == kAclEventTraceOpCreate || op == kAclEventTraceOpCreateWithFlag ||
         op == kAclEventTraceOpCreateExWithFlag || op == kAclEventTraceOpRtCreate ||
         op == kAclEventTraceOpRtCreateWithFlag || op == kAclEventTraceOpRtCreateExWithFlag);
    if (g_reentrancy_depth > 0 && !nested_create) {
        g_state.fatal.fetch_add(1, std::memory_order_relaxed);
        return nullptr;
    }
    const uint64_t slot = g_state.claim_seq.fetch_add(1, std::memory_order_relaxed);
    if (slot >= g_state.capacity) {
        g_state.dropped.fetch_add(1, std::memory_order_relaxed);
        g_state.fatal.fetch_add(1, std::memory_order_relaxed);
        return nullptr;
    }
    AclEventTraceRecord* rec = &g_state.records[slot];
    rec->format_version = ACL_EVENT_TRACE_VERSION;
    rec->op = op;
    rec->committed = 0;
    rec->reserved0 = 0;
    rec->call_sequence = g_state.call_seq.fetch_add(1, std::memory_order_relaxed) + 1;
    rec->slot_sequence = slot;
    rec->pid = static_cast<uint32_t>(g_state.pid);
    rec->tid = GetTid();
    rec->rank = g_state.rank;
    rec->local_rank = g_state.local_rank;
    rec->enter_realtime_ns = ReadClock(CLOCK_REALTIME);
    rec->enter_monotonic_ns = ReadClock(CLOCK_MONOTONIC_RAW);
    rec->raw_event = 0;
    rec->raw_stream = 0;
    rec->create_flag = 0;
    rec->acl_ret = 0;
    rec->flags = extra_flags;
    rec->source = source;
    rec->resolver_path = resolver_path;
    rec->nested_under_acl = 0;
    rec->reserved1 = 0;
    rec->parent_acl_call_sequence = 0;
    return rec;
}

void CommitSlot(AclEventTraceRecord* rec) {
    if (rec == nullptr) {
        return;
    }
    rec->exit_realtime_ns = ReadClock(CLOCK_REALTIME);
    rec->exit_monotonic_ns = ReadClock(CLOCK_MONOTONIC_RAW);
    std::atomic_thread_fence(std::memory_order_release);
    rec->committed = 1;
    g_state.committed.fetch_add(1, std::memory_order_relaxed);
}

void RecordResolverAudit(uint8_t api, const char* name, void* handle, void* real_addr,
                         void* wrapper_addr) {
    const uint32_t idx = g_state.resolver_audit_written.fetch_add(1, std::memory_order_relaxed);
    if (idx >= ACL_EVENT_TRACE_RESOLVER_AUDIT_CAP) {
        g_state.resolver_audit_overflow.fetch_add(1, std::memory_order_relaxed);
        g_state.fatal.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    AclEventTraceResolverAuditEntry* e = &g_state.resolver_audit[idx];
    e->api = api;
    e->reserved0 = 0;
    e->handle = reinterpret_cast<uint64_t>(handle);
    e->real_addr = reinterpret_cast<uint64_t>(real_addr);
    e->wrapper_addr = reinterpret_cast<uint64_t>(wrapper_addr);
    size_t n = 0;
    if (name != nullptr) {
        for (; n < sizeof(e->name) - 1 && name[n] != '\0'; ++n) {
            e->name[n] = name[n];
        }
    }
    e->name[n] = '\0';
    e->name_len = static_cast<uint16_t>(n);
}

bool TrackTargetConflict(CreateTarget target, void* real_addr, void* query_handle) {
    if (query_handle != RTLD_DEFAULT && query_handle != RTLD_NEXT && query_handle != nullptr) {
        return false;
    }
    TargetAddrState* st = TargetState(target);
    if (st == nullptr || real_addr == nullptr) {
        return false;
    }
    const uint64_t addr = reinterpret_cast<uint64_t>(real_addr);
    uint64_t expected = 0;
    if (st->first_real.compare_exchange_strong(expected, addr, std::memory_order_acq_rel)) {
        return false;
    }
    if (st->first_real.load(std::memory_order_acquire) != addr) {
        st->conflict.store(1, std::memory_order_release);
        g_state.resolver_target_conflict.fetch_add(1, std::memory_order_relaxed);
        g_state.fatal.fetch_add(1, std::memory_order_relaxed);
        return true;
    }
    return false;
}

void WriteMetadataJson(const char* final_path, uint64_t committed) {
    if (g_state.out_dir[0] == '\0') {
        return;
    }
    char meta_path[640];
    std::snprintf(
        meta_path, sizeof(meta_path), "%s/rank_%d_pid_%d.events.meta.json",
        g_state.out_dir, g_state.rank, g_state.pid);
    const int fd = open(meta_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        return;
    }
    char buf[1024];
    const int n = std::snprintf(
        buf, sizeof(buf),
        "{\"version\":%u,\"pid\":%d,\"rank\":%d,\"committed\":%llu,"
        "\"dropped\":%llu,\"fatal\":%llu,\"late_calls\":%llu,"
        "\"resolver_queries\":%llu,\"resolver_wrappers_returned\":%llu,"
        "\"resolver_target_conflict\":%llu,\"resolver_audit_overflow\":%llu,"
        "\"acl_create_wrapper_calls\":%llu,\"rt_create_wrapper_calls\":%llu,"
        "\"logical_create_count\":%llu,\"path\":\"%s\"}\n",
        ACL_EVENT_TRACE_VERSION, g_state.pid, g_state.rank,
        static_cast<unsigned long long>(committed),
        static_cast<unsigned long long>(g_state.dropped.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(g_state.fatal.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(g_state.late_calls.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(g_state.resolver_queries.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(
            g_state.resolver_wrappers_returned.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(
            g_state.resolver_target_conflict.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(
            g_state.resolver_audit_overflow.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(
            g_state.acl_create_wrapper_calls.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(
            g_state.rt_create_wrapper_calls.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(
            g_state.logical_create_count.load(std::memory_order_relaxed)),
        final_path);
    if (n > 0) {
        write(fd, buf, static_cast<size_t>(n));
    }
    close(fd);

    char audit_path[640];
    std::snprintf(
        audit_path, sizeof(audit_path), "%s/rank_%d_pid_%d.resolver_audit.json",
        g_state.out_dir, g_state.rank, g_state.pid);
    const int afd = open(audit_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (afd < 0) {
        return;
    }
    const char* prefix = "{\"entries\":[";
    write(afd, prefix, std::strlen(prefix));
    const uint32_t audit_n = std::min<uint32_t>(
        g_state.resolver_audit_written.load(std::memory_order_relaxed),
        ACL_EVENT_TRACE_RESOLVER_AUDIT_CAP);
    for (uint32_t i = 0; i < audit_n; ++i) {
        const AclEventTraceResolverAuditEntry& e = g_state.resolver_audit[i];
        char row[256];
        const int rn = std::snprintf(
            row, sizeof(row),
            "%s{\"api\":%u,\"name\":\"%s\",\"handle\":\"0x%llx\","
            "\"real_addr\":\"0x%llx\",\"wrapper_addr\":\"0x%llx\"}",
            (i == 0 ? "" : ","), e.api, e.name,
            static_cast<unsigned long long>(e.handle),
            static_cast<unsigned long long>(e.real_addr),
            static_cast<unsigned long long>(e.wrapper_addr));
        if (rn > 0) {
            write(afd, row, static_cast<size_t>(rn));
        }
    }
    const char* suffix = "]}\n";
    write(afd, suffix, std::strlen(suffix));
    close(afd);
}

void DelayHostCallback(void* args) {
    DelayCallbackState* st = static_cast<DelayCallbackState*>(args);
    if (st == nullptr) {
        return;
    }
    const uint64_t enter = ReadClock(CLOCK_MONOTONIC);
    st->enter_monotonic_ns.store(enter, std::memory_order_relaxed);
    st->invoke_count.fetch_add(1, std::memory_order_relaxed);
    if (st->delay_ns > 0) {
        const uint64_t target = enter + st->delay_ns;
        while (true) {
            const uint64_t now = ReadClock(CLOCK_MONOTONIC);
            if (now >= target) {
                break;
            }
            const uint64_t remain = target - now;
            timespec ts{};
            ts.tv_sec = static_cast<time_t>(remain / 1000000000ULL);
            ts.tv_nsec = static_cast<long>(remain % 1000000000ULL);
            clock_nanosleep(CLOCK_MONOTONIC, 0, &ts, nullptr);
        }
    }
    st->exit_monotonic_ns.store(ReadClock(CLOCK_MONOTONIC), std::memory_order_relaxed);
}

void WriteDelayAuditJson() {
    if (g_state.out_dir[0] == '\0') {
        return;
    }
    char audit_path[640];
    std::snprintf(
        audit_path, sizeof(audit_path), "%s/rank_%d_pid_%d.delay_audit.json",
        g_state.out_dir, g_state.rank, g_state.pid);
    const int fd = open(audit_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        return;
    }
    char buf[2048];
    const int n = std::snprintf(
        buf, sizeof(buf),
        "{\"rank\":%d,\"pid\":%d,\"arm_tid\":%u,\"armed_tid\":%u,"
        "\"record_issuing_tid\":%u,\"delay_us\":%llu,"
        "\"active_record_success_ord\":%d,\"active_wait_success_ord\":%d,"
        "\"match_count\":%d,\"hostfunc_submit_rc\":%d,"
        "\"real_record_rc_after_inject\":%d,\"inject_failed\":%d,"
        "\"callback_count\":%u,\"callback_enter_monotonic_ns\":%llu,"
        "\"callback_exit_monotonic_ns\":%llu,\"last_inject_preload_cs\":%llu,"
        "\"last_inject_raw_event\":%llu,\"last_inject_raw_stream\":%llu,"
        "\"target_record_ordinal\":%d}\n",
        g_state.rank, g_state.pid,
        g_delay_state.arm_tid.load(std::memory_order_relaxed),
        g_delay_state.armed_tid.load(std::memory_order_relaxed),
        g_delay_state.armed_tid.load(std::memory_order_relaxed),
        static_cast<unsigned long long>(g_delay_state.delay_us.load(std::memory_order_relaxed)),
        g_delay_state.active_record_success_ord.load(std::memory_order_relaxed),
        g_delay_state.active_wait_success_ord.load(std::memory_order_relaxed),
        g_delay_state.match_count.load(std::memory_order_relaxed),
        g_delay_state.hostfunc_submit_rc.load(std::memory_order_relaxed),
        g_delay_state.real_record_rc_after_inject.load(std::memory_order_relaxed),
        g_delay_state.inject_failed.load(std::memory_order_relaxed),
        g_delay_cb_state.invoke_count.load(std::memory_order_relaxed),
        static_cast<unsigned long long>(
            g_delay_cb_state.enter_monotonic_ns.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(
            g_delay_cb_state.exit_monotonic_ns.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(
            g_delay_state.last_inject_preload_cs.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(
            g_delay_state.last_inject_raw_event.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(
            g_delay_state.last_inject_raw_stream.load(std::memory_order_relaxed)),
        kDelayTargetRecordOrdinal);
    if (n > 0) {
        write(fd, buf, static_cast<size_t>(n));
    }
    close(fd);
}

bool WriteTraceFile() {
    if (!g_state.records || g_state.out_dir[0] == '\0') {
        return false;
    }
    const uint64_t committed = g_state.committed.load(std::memory_order_relaxed);
    char tmp_path[640];
    char final_path[640];
    std::snprintf(
        tmp_path, sizeof(tmp_path), "%s/rank_%d_pid_%d.events.bin.tmp",
        g_state.out_dir, g_state.rank, g_state.pid);
    std::snprintf(
        final_path, sizeof(final_path), "%s/rank_%d_pid_%d.events.bin",
        g_state.out_dir, g_state.rank, g_state.pid);

    const int fd = open(tmp_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        return false;
    }

    AclEventTraceHeader header{};
    header.magic = ACL_EVENT_TRACE_MAGIC;
    header.version = ACL_EVENT_TRACE_VERSION;
    header.capacity = g_state.capacity;
    header.committed_count = committed;
    header.dropped = g_state.dropped.load(std::memory_order_relaxed);
    header.fatal = g_state.fatal.load(std::memory_order_relaxed);
    header.late_calls = g_state.late_calls.load(std::memory_order_relaxed);
    header.pid = static_cast<uint32_t>(g_state.pid);
    header.rank = g_state.rank;
    header.resolver_queries = g_state.resolver_queries.load(std::memory_order_relaxed);
    header.resolver_wrappers_returned =
        g_state.resolver_wrappers_returned.load(std::memory_order_relaxed);
    header.resolver_target_conflict =
        g_state.resolver_target_conflict.load(std::memory_order_relaxed);
    header.resolver_audit_overflow =
        g_state.resolver_audit_overflow.load(std::memory_order_relaxed);
    header.acl_create_wrapper_calls =
        g_state.acl_create_wrapper_calls.load(std::memory_order_relaxed);
    header.rt_create_wrapper_calls =
        g_state.rt_create_wrapper_calls.load(std::memory_order_relaxed);
    header.logical_create_count = g_state.logical_create_count.load(std::memory_order_relaxed);
    header.header_crc32 = Crc32(&header, offsetof(AclEventTraceHeader, header_crc32));

    uint64_t checksum = 0;
    for (uint64_t i = 0; i < committed; ++i) {
        const AclEventTraceRecord& rec = g_state.records[i];
        if (rec.committed != 1) {
            close(fd);
            unlink(tmp_path);
            return false;
        }
        const auto* words = reinterpret_cast<const uint64_t*>(&rec);
        for (size_t w = 0; w < sizeof(AclEventTraceRecord) / sizeof(uint64_t); ++w) {
            checksum ^= words[w];
        }
    }

    AclEventTraceTrailer trailer{};
    trailer.committed_count = committed;
    trailer.record_checksum = checksum;
    trailer.trailer_crc32 = Crc32(&trailer, offsetof(AclEventTraceTrailer, trailer_crc32));

    write(fd, &header, sizeof(header));
    if (committed > 0) {
        write(fd, g_state.records, static_cast<size_t>(committed) * sizeof(AclEventTraceRecord));
    }
    write(fd, &trailer, sizeof(trailer));
    fsync(fd);
    close(fd);
    rename(tmp_path, final_path);
    WriteMetadataJson(final_path, committed);
    WriteDelayAuditJson();
    acl_event_work_write_audit();
    return true;
}

struct ReentrancyGuard {
    ReentrancyGuard() { ++g_reentrancy_depth; }
    ~ReentrancyGuard() { --g_reentrancy_depth; }
};

struct DlsymReentrancyGuard {
    DlsymReentrancyGuard() { ++g_dlsym_reentrancy; }
    ~DlsymReentrancyGuard() { --g_dlsym_reentrancy; }
};

aclError AclCreateImpl(aclrtEvent* event, uint32_t flag, uint8_t op, uint8_t resolver_path);

aclError WrapCreateDlsym(aclrtEvent* event) {
    return AclCreateImpl(event, 0, kAclEventTraceOpCreate, kAclEventTraceResolverDlsym);
}

aclError WrapCreateWithFlagDlsym(aclrtEvent* event, uint32_t flag) {
    return AclCreateImpl(event, flag, kAclEventTraceOpCreateWithFlag, kAclEventTraceResolverDlsym);
}

aclError WrapCreateExWithFlagDlsym(aclrtEvent* event, uint32_t flag) {
    return AclCreateImpl(event, flag, kAclEventTraceOpCreateExWithFlag,
                          kAclEventTraceResolverDlsym);
}

void* WrapperForCreateTarget(CreateTarget target) {
    switch (target) {
        case kCreateAclEvent:
            return reinterpret_cast<void*>(WrapCreateDlsym);
        case kCreateAclWithFlag:
            return reinterpret_cast<void*>(WrapCreateWithFlagDlsym);
        case kCreateAclExWithFlag:
            return reinterpret_cast<void*>(WrapCreateExWithFlagDlsym);
        default:
            return nullptr;
    }
}

void* ResolveAndMaybeWrap(void* handle, const char* symbol, uint8_t api) {
    if (g_dlsym_reentrancy > 0) {
        if (api == kAclEventTraceResolverApiDlvsym) {
            return REAL_DLVSYM(handle, symbol, nullptr);
        }
        return REAL_DLSYM(handle, symbol);
    }
    const CreateTarget target = ClassifyAclCreate(symbol);
    if (target == kCreateNone) {
        return REAL_DLSYM(handle, symbol);
    }
    DlsymReentrancyGuard guard;
    void* real = REAL_DLSYM(handle, symbol);
    g_state.resolver_queries.fetch_add(1, std::memory_order_relaxed);
    RecordResolverAudit(api, symbol, handle, real, nullptr);
    if (real == nullptr) {
        return nullptr;
    }
    if (TrackTargetConflict(target, real, handle)) {
        return real;
    }
    void* wrapper = WrapperForCreateTarget(target);
    if (wrapper != nullptr) {
        g_dlsym_resolved_create_real = real;
        g_state.resolver_wrappers_returned.fetch_add(1, std::memory_order_relaxed);
        RecordResolverAudit(api, symbol, handle, real, wrapper);
        return wrapper;
    }
    return real;
}

aclError AclCreateImpl(aclrtEvent* event, uint32_t flag, uint8_t op, uint8_t resolver_path) {
    void* dlsym_real = nullptr;
    if (resolver_path == kAclEventTraceResolverDlsym) {
        dlsym_real = g_dlsym_resolved_create_real;
    }
    FnCreateWithFlag fn = nullptr;
    if (dlsym_real != nullptr) {
        fn = reinterpret_cast<FnCreateWithFlag>(dlsym_real);
    } else if (op == kAclEventTraceOpCreate) {
        fn = reinterpret_cast<FnCreateWithFlag>(real_create);
    } else if (op == kAclEventTraceOpCreateWithFlag) {
        fn = real_create_with_flag;
    } else {
        fn = reinterpret_cast<FnCreateWithFlag>(real_create_ex_with_flag);
    }
    if (fn == nullptr) {
        InitState();
        ResolveSymbols();
        if (dlsym_real != nullptr) {
            fn = reinterpret_cast<FnCreateWithFlag>(dlsym_real);
        } else if (op == kAclEventTraceOpCreate) {
            fn = reinterpret_cast<FnCreateWithFlag>(real_create);
        } else if (op == kAclEventTraceOpCreateWithFlag) {
            fn = real_create_with_flag;
        } else {
            fn = reinterpret_cast<FnCreateWithFlag>(real_create_ex_with_flag);
        }
    }
    if (fn == nullptr) {
        return static_cast<aclError>(100000);
    }
    AclEventTraceRecord* rec =
        ClaimSlot(op, kAclEventTraceFlagNone, kAclEventTraceSourceAcl, resolver_path);
    if (rec != nullptr) {
        rec->create_flag = flag;
    }
    const uint64_t parent_seq = rec != nullptr ? rec->call_sequence : 0;
    const int saved_errno = errno;
    ReentrancyGuard guard;
  #ifdef ACL_EVENT_TRACE_RT_ABI
    ++g_acl_create_nest_depth;
    g_acl_parent_call_sequence = parent_seq;
  #endif
    aclError ret = 0;
    if (dlsym_real != nullptr) {
        if (op == kAclEventTraceOpCreate) {
            ret = reinterpret_cast<FnCreate>(dlsym_real)(event);
        } else {
            ret = fn(event, flag);
        }
        g_dlsym_resolved_create_real = nullptr;
    } else if (op == kAclEventTraceOpCreate) {
        ret = real_create(event);
    } else if (op == kAclEventTraceOpCreateWithFlag) {
        ret = real_create_with_flag(event, flag);
    } else {
        ret = real_create_ex_with_flag(event, flag);
    }
  #ifdef ACL_EVENT_TRACE_RT_ABI
    --g_acl_create_nest_depth;
  #endif
    errno = saved_errno;
    g_state.acl_create_wrapper_calls.fetch_add(1, std::memory_order_relaxed);
    if (rec != nullptr) {
        if (event != nullptr && ret == 0) {
            rec->raw_event = reinterpret_cast<uint64_t>(*event);
        }
        rec->acl_ret = static_cast<int32_t>(ret);
        CommitSlot(rec);
        if (ret == 0 && rec->nested_under_acl == 0) {
            g_state.logical_create_count.fetch_add(1, std::memory_order_relaxed);
        }
    }
    return ret;
}

#ifdef ACL_EVENT_TRACE_RT_ABI
rtError_t RtCreateImpl(rtEvent_t* evt, uint32_t flag, uint8_t op) {
    FnRtCreateWithFlag fn = nullptr;
    if (op == kAclEventTraceOpRtCreate) {
        fn = reinterpret_cast<FnRtCreateWithFlag>(real_rt_create);
    } else if (op == kAclEventTraceOpRtCreateWithFlag) {
        fn = real_rt_create_with_flag;
    } else {
        fn = reinterpret_cast<FnRtCreateWithFlag>(real_rt_create_ex_with_flag);
    }
    if (fn == nullptr) {
        InitState();
        ResolveSymbols();
        if (op == kAclEventTraceOpRtCreate) {
            fn = reinterpret_cast<FnRtCreateWithFlag>(real_rt_create);
        } else if (op == kAclEventTraceOpRtCreateWithFlag) {
            fn = real_rt_create_with_flag;
        } else {
            fn = reinterpret_cast<FnRtCreateWithFlag>(real_rt_create_ex_with_flag);
        }
    }
    if (fn == nullptr) {
        return static_cast<rtError_t>(1);
    }
    uint32_t extra = kAclEventTraceFlagNone;
    if (g_acl_create_nest_depth == 0) {
        extra |= kAclEventTraceFlagRtFallback;
    }
    AclEventTraceRecord* rec =
        ClaimSlot(op, extra, kAclEventTraceSourceRt, kAclEventTraceResolverPlt);
    if (rec != nullptr) {
        rec->create_flag = flag;
        if (g_acl_create_nest_depth > 0) {
            rec->nested_under_acl = 1;
            rec->parent_acl_call_sequence = g_acl_parent_call_sequence;
        }
    }
    const int saved_errno = errno;
    ReentrancyGuard guard;
    rtError_t ret = 0;
    if (op == kAclEventTraceOpRtCreate) {
        ret = real_rt_create(evt);
    } else if (op == kAclEventTraceOpRtCreateWithFlag) {
        ret = real_rt_create_with_flag(evt, flag);
    } else {
        ret = real_rt_create_ex_with_flag(evt, flag);
    }
    errno = saved_errno;
    g_state.rt_create_wrapper_calls.fetch_add(1, std::memory_order_relaxed);
    if (rec != nullptr) {
        if (evt != nullptr && ret == 0) {
            rec->raw_event = reinterpret_cast<uint64_t>(*evt);
        }
        rec->acl_ret = static_cast<int32_t>(ret);
        CommitSlot(rec);
        if (ret == 0 && g_acl_create_nest_depth == 0) {
            g_state.logical_create_count.fetch_add(1, std::memory_order_relaxed);
        }
    }
    return ret;
}
#endif

}  // namespace

extern "C" {

void acl_event_delay_arm(void) {
    InitState();
    const uint64_t delay_us = ParseEnvU64("ACL_EVENT_DELAY_US", 0);
    g_delay_state.delay_us.store(delay_us, std::memory_order_relaxed);
    const uint32_t tid = GetTid();
    g_delay_state.arm_tid.store(tid, std::memory_order_relaxed);
    g_delay_state.armed_tid.store(0, std::memory_order_relaxed);
    g_delay_state.active_record_success_ord.store(0, std::memory_order_relaxed);
    g_delay_state.active_wait_success_ord.store(0, std::memory_order_relaxed);
    g_delay_state.match_count.store(0, std::memory_order_relaxed);
    g_delay_state.hostfunc_submit_rc.store(0, std::memory_order_relaxed);
    g_delay_state.real_record_rc_after_inject.store(0, std::memory_order_relaxed);
    g_delay_state.inject_failed.store(0, std::memory_order_relaxed);
    g_delay_state.last_inject_preload_cs.store(0, std::memory_order_relaxed);
    g_delay_state.last_inject_raw_event.store(0, std::memory_order_relaxed);
    g_delay_state.last_inject_raw_stream.store(0, std::memory_order_relaxed);
    g_delay_cb_state.delay_ns = 0;
    g_delay_cb_state.enter_monotonic_ns.store(0, std::memory_order_relaxed);
    g_delay_cb_state.exit_monotonic_ns.store(0, std::memory_order_relaxed);
    g_delay_cb_state.invoke_count.store(0, std::memory_order_relaxed);
    const char* inject_site = getenv("ACL_EVENT_INJECT_SITE");
    if (inject_site != nullptr &&
        std::strcmp(inject_site, "AFTER_SUCCESSFUL_TARGET_WAIT") == 0) {
        // Latch issuing tid on first successful generation Record / target Wait
        // (torch_npu may enqueue Record/Wait on a worker thread != arm thread).
        g_delay_state.armed_tid.store(0, std::memory_order_relaxed);
    }
    g_delay_state.armed.store(true, std::memory_order_release);
    acl_event_work_arm();
}

void acl_event_delay_disarm(void) {
    g_delay_state.armed.store(false, std::memory_order_release);
}

int acl_event_trace_finalize(void) {
    InitState();
    if (g_state.finalize_guard.exchange(1) != 0) {
        return 0;
    }
    g_state.finalized.store(true, std::memory_order_release);
    return WriteTraceFile() ? 0 : 1;
}

__attribute__((destructor)) static void AclEventTraceDestructor() {
    if (g_state.finalize_guard.load(std::memory_order_relaxed) == 0) {
        acl_event_trace_finalize();
    }
}

void* dlsym(void* handle, const char* symbol) {
    if (g_dlsym_reentrancy > 0) {
        return REAL_DLSYM(handle, symbol);
    }
    if (symbol != nullptr &&
        (MatchExact(symbol, "dlsym") || MatchExact(symbol, "dlvsym"))) {
        return REAL_DLSYM(handle, symbol);
    }
    return ResolveAndMaybeWrap(handle, symbol, kAclEventTraceResolverApiDlsym);
}

void* dlvsym(void* handle, const char* symbol, const char* version) {
    if (g_dlsym_reentrancy > 0) {
        return REAL_DLVSYM(handle, symbol, version);
    }
    if (symbol != nullptr &&
        (MatchExact(symbol, "dlsym") || MatchExact(symbol, "dlvsym"))) {
        return REAL_DLVSYM(handle, symbol, version);
    }
    const CreateTarget target = ClassifyAclCreate(symbol);
    if (target == kCreateNone) {
        return REAL_DLVSYM(handle, symbol, version);
    }
    DlsymReentrancyGuard guard;
    void* real = REAL_DLVSYM(handle, symbol, version);
    g_state.resolver_queries.fetch_add(1, std::memory_order_relaxed);
    RecordResolverAudit(kAclEventTraceResolverApiDlvsym, symbol, handle, real, nullptr);
    if (real == nullptr) {
        return nullptr;
    }
    if (TrackTargetConflict(target, real, handle)) {
        return real;
    }
    void* wrapper = WrapperForCreateTarget(target);
    if (wrapper != nullptr) {
        g_dlsym_resolved_create_real = real;
        g_state.resolver_wrappers_returned.fetch_add(1, std::memory_order_relaxed);
        RecordResolverAudit(kAclEventTraceResolverApiDlvsym, symbol, handle, real, wrapper);
        return wrapper;
    }
    return real;
}

aclError aclrtCreateEvent(aclrtEvent* event) {
    return AclCreateImpl(event, 0, kAclEventTraceOpCreate, kAclEventTraceResolverPlt);
}

aclError aclrtCreateEventWithFlag(aclrtEvent* event, uint32_t flag) {
    return AclCreateImpl(event, flag, kAclEventTraceOpCreateWithFlag, kAclEventTraceResolverPlt);
}

aclError aclrtCreateEventExWithFlag(aclrtEvent* event, uint32_t flag) {
    return AclCreateImpl(event, flag, kAclEventTraceOpCreateExWithFlag, kAclEventTraceResolverPlt);
}

#ifdef ACL_EVENT_TRACE_RT_ABI
rtError_t rtEventCreate(rtEvent_t* evt) {
    return RtCreateImpl(evt, 0, kAclEventTraceOpRtCreate);
}

rtError_t rtEventCreateWithFlag(rtEvent_t* evt, uint32_t flag) {
    return RtCreateImpl(evt, flag, kAclEventTraceOpRtCreateWithFlag);
}

rtError_t rtEventCreateExWithFlag(rtEvent_t* evt, uint32_t flag) {
    return RtCreateImpl(evt, flag, kAclEventTraceOpRtCreateExWithFlag);
}
#endif

aclError aclrtDestroyEvent(aclrtEvent event) {
    if (real_destroy == nullptr) {
        InitState();
    }
    if (real_destroy == nullptr) {
        return static_cast<aclError>(100000);
    }
    AclEventTraceRecord* rec =
        ClaimSlot(kAclEventTraceOpDestroy, kAclEventTraceFlagNone, kAclEventTraceSourceAcl,
                  kAclEventTraceResolverPlt);
    if (rec != nullptr) {
        rec->raw_event = reinterpret_cast<uint64_t>(event);
    }
    const int saved_errno = errno;
    ReentrancyGuard guard;
    const aclError ret = real_destroy(event);
    errno = saved_errno;
    if (rec != nullptr) {
        rec->acl_ret = static_cast<int32_t>(ret);
        CommitSlot(rec);
    }
    return ret;
}

aclError aclrtRecordEvent(aclrtEvent event, aclrtStream stream) {
    if (real_record == nullptr) {
        InitState();
    }
    if (real_record == nullptr) {
        return static_cast<aclError>(100000);
    }
    AclEventTraceRecord* rec =
        ClaimSlot(kAclEventTraceOpRecord, kAclEventTraceFlagNone, kAclEventTraceSourceAcl,
                  kAclEventTraceResolverPlt);
    if (rec != nullptr) {
        rec->raw_event = reinterpret_cast<uint64_t>(event);
        rec->raw_stream = reinterpret_cast<uint64_t>(stream);
    }
    const int saved_errno = errno;
    ReentrancyGuard guard;

    const bool armed = g_delay_state.armed.load(std::memory_order_acquire);
    const bool rank0 = g_state.rank == 0 || ParseEnvInt("RANK", -1) == 0;
    const uint32_t record_issuing_tid =
        g_delay_state.armed_tid.load(std::memory_order_relaxed);
    const int ord = g_delay_state.active_record_success_ord.load(std::memory_order_relaxed);
    const bool should_inject =
        armed && rank0 && acl_event_work_should_inject(ord, record_issuing_tid);

    aclError ret = 0;
    if (should_inject) {
        if (rec != nullptr) {
            acl_event_work_on_inject_claim(rec->call_sequence, rec->raw_event, rec->raw_stream);
            g_delay_state.last_inject_preload_cs.store(rec->call_sequence, std::memory_order_relaxed);
            g_delay_state.last_inject_raw_event.store(rec->raw_event, std::memory_order_relaxed);
            g_delay_state.last_inject_raw_stream.store(rec->raw_stream, std::memory_order_relaxed);
        }
        uint64_t host_enter = 0;
        uint64_t host_exit = 0;
        const int launch_rc = acl_event_work_try_launch(stream, &host_enter, &host_exit);
        g_delay_state.hostfunc_submit_rc.store(launch_rc, std::memory_order_relaxed);
        if (launch_rc != 0) {
            g_delay_state.inject_failed.store(1, std::memory_order_relaxed);
            g_state.fatal.fetch_add(1, std::memory_order_relaxed);
            if (rec != nullptr) {
                rec->acl_ret = static_cast<int32_t>(launch_rc);
                CommitSlot(rec);
            }
            errno = saved_errno;
            return static_cast<aclError>(launch_rc);
        }
        g_delay_state.match_count.fetch_add(1, std::memory_order_relaxed);
    }

    ret = real_record(event, stream);
    if (should_inject) {
        g_delay_state.real_record_rc_after_inject.store(static_cast<int32_t>(ret),
                                                         std::memory_order_relaxed);
        acl_event_work_on_inject_record_rc(static_cast<int32_t>(ret));
    }
    if (armed && rank0 && ret == 0) {
        const uint32_t tid = GetTid();
        uint32_t latched_tid = g_delay_state.armed_tid.load(std::memory_order_relaxed);
        if (latched_tid == 0) {
            g_delay_state.armed_tid.store(tid, std::memory_order_relaxed);
            latched_tid = tid;
        }
        if (latched_tid == tid) {
            g_delay_state.active_record_success_ord.fetch_add(1, std::memory_order_relaxed);
            acl_event_work_set_record_issuing_tid(latched_tid);
        }
    }
    errno = saved_errno;
    if (rec != nullptr) {
        rec->acl_ret = static_cast<int32_t>(ret);
        CommitSlot(rec);
    }
    return ret;
}

aclError aclrtResetEvent(aclrtEvent event, aclrtStream stream) {
    if (real_reset == nullptr) {
        InitState();
    }
    if (real_reset == nullptr) {
        return static_cast<aclError>(100000);
    }
    AclEventTraceRecord* rec =
        ClaimSlot(kAclEventTraceOpReset, kAclEventTraceFlagNone, kAclEventTraceSourceAcl,
                  kAclEventTraceResolverPlt);
    if (rec != nullptr) {
        rec->raw_event = reinterpret_cast<uint64_t>(event);
        rec->raw_stream = reinterpret_cast<uint64_t>(stream);
    }
    const int saved_errno = errno;
    ReentrancyGuard guard;
    const aclError ret = real_reset(event, stream);
    errno = saved_errno;
    if (rec != nullptr) {
        rec->acl_ret = static_cast<int32_t>(ret);
        CommitSlot(rec);
    }
    return ret;
}

aclError aclrtStreamWaitEvent(aclrtStream stream, aclrtEvent event) {
    if (real_wait == nullptr) {
        InitState();
    }
    if (real_wait == nullptr) {
        return static_cast<aclError>(100000);
    }
    AclEventTraceRecord* rec =
        ClaimSlot(kAclEventTraceOpWait, kAclEventTraceFlagNone, kAclEventTraceSourceAcl,
                  kAclEventTraceResolverPlt);
    if (rec != nullptr) {
        rec->raw_event = reinterpret_cast<uint64_t>(event);
        rec->raw_stream = reinterpret_cast<uint64_t>(stream);
    }
    const int saved_errno = errno;
    ReentrancyGuard guard;

    const bool armed = g_delay_state.armed.load(std::memory_order_acquire);
    const bool rank0 = g_state.rank == 0 || ParseEnvInt("RANK", -1) == 0;
    const bool after_wait_site = acl_event_work_inject_site_is_after_wait();
    uint32_t wait_issuing_tid = g_delay_state.armed_tid.load(std::memory_order_relaxed);
    if (after_wait_site && armed && rank0 && wait_issuing_tid == 0) {
        const uint32_t tid = GetTid();
        g_delay_state.armed_tid.store(tid, std::memory_order_relaxed);
        acl_event_work_set_record_issuing_tid(tid);
        wait_issuing_tid = tid;
    }
    const uint32_t arm_tid = g_delay_state.arm_tid.load(std::memory_order_relaxed);
    uint32_t effective_wait_tid =
        wait_issuing_tid != 0 ? wait_issuing_tid : (after_wait_site ? 0U : arm_tid);
#if defined(ACL_EVENT_TRACE_USE_STUB)
    if (after_wait_site && armed && rank0 && effective_wait_tid == 0) {
        effective_wait_tid = GetTid();
    }
#endif
    const int wait_ord = g_delay_state.active_wait_success_ord.load(std::memory_order_relaxed);
    const bool should_inject_after_wait =
        rank0 && acl_event_work_should_inject_after_wait(wait_ord, effective_wait_tid);

    const aclError ret = real_wait(stream, event);

    if (ret == 0 && rec != nullptr) {
        acl_event_work_record_sidecar_snapshot(rec->call_sequence);
    }

    if (should_inject_after_wait && ret == 0) {
        if (rec != nullptr) {
            acl_event_work_on_wait_inject_claim(rec->call_sequence, rec->raw_event, rec->raw_stream);
            g_delay_state.last_inject_preload_cs.store(rec->call_sequence, std::memory_order_relaxed);
            g_delay_state.last_inject_raw_event.store(rec->raw_event, std::memory_order_relaxed);
            g_delay_state.last_inject_raw_stream.store(rec->raw_stream, std::memory_order_relaxed);
        }
        uint64_t host_enter = 0;
        uint64_t host_exit = 0;
        const int launch_rc = acl_event_work_try_launch(stream, &host_enter, &host_exit);
        g_delay_state.hostfunc_submit_rc.store(launch_rc, std::memory_order_relaxed);
        acl_event_work_on_inject_wait_rc(static_cast<int32_t>(ret));
        if (launch_rc != 0) {
            g_delay_state.inject_failed.store(1, std::memory_order_relaxed);
            g_state.fatal.fetch_add(1, std::memory_order_relaxed);
            if (rec != nullptr) {
                rec->acl_ret = static_cast<int32_t>(launch_rc);
                CommitSlot(rec);
            }
            errno = saved_errno;
            return static_cast<aclError>(launch_rc);
        }
        g_delay_state.match_count.fetch_add(1, std::memory_order_relaxed);
    }

    if (armed && rank0 && ret == 0) {
        const uint32_t tid = GetTid();
        if (effective_wait_tid != 0 && tid == effective_wait_tid) {
            g_delay_state.active_wait_success_ord.fetch_add(1, std::memory_order_relaxed);
        }
    }
    errno = saved_errno;
    if (rec != nullptr) {
        rec->acl_ret = static_cast<int32_t>(ret);
        CommitSlot(rec);
    }
    return ret;
}

aclError aclrtStreamWaitEventWithTimeout(aclrtStream stream, aclrtEvent event, int32_t timeout) {
    if (real_wait_timeout == nullptr) {
        InitState();
    }
    if (real_wait_timeout == nullptr) {
        return static_cast<aclError>(100000);
    }
    AclEventTraceRecord* rec = ClaimSlot(
        kAclEventTraceOpStreamWaitWithTimeout, kAclEventTraceFlagNone, kAclEventTraceSourceAcl,
        kAclEventTraceResolverPlt);
    if (rec != nullptr) {
        rec->raw_event = reinterpret_cast<uint64_t>(event);
        rec->raw_stream = reinterpret_cast<uint64_t>(stream);
        rec->create_flag = static_cast<uint32_t>(timeout);
    }
    const int saved_errno = errno;
    ReentrancyGuard guard;

    const bool armed = g_delay_state.armed.load(std::memory_order_acquire);
    const bool rank0 = g_state.rank == 0 || ParseEnvInt("RANK", -1) == 0;
    const bool after_wait_site = acl_event_work_inject_site_is_after_wait();
    uint32_t wait_issuing_tid = g_delay_state.armed_tid.load(std::memory_order_relaxed);
    if (after_wait_site && armed && rank0 && wait_issuing_tid == 0) {
        const uint32_t tid = GetTid();
        g_delay_state.armed_tid.store(tid, std::memory_order_relaxed);
        acl_event_work_set_record_issuing_tid(tid);
        wait_issuing_tid = tid;
    }
    const uint32_t arm_tid = g_delay_state.arm_tid.load(std::memory_order_relaxed);
    uint32_t effective_wait_tid =
        wait_issuing_tid != 0 ? wait_issuing_tid : (after_wait_site ? 0U : arm_tid);
#if defined(ACL_EVENT_TRACE_USE_STUB)
    if (after_wait_site && armed && rank0 && effective_wait_tid == 0) {
        effective_wait_tid = GetTid();
    }
#endif
    const int wait_ord = g_delay_state.active_wait_success_ord.load(std::memory_order_relaxed);
    const bool should_inject_after_wait =
        rank0 && acl_event_work_should_inject_after_wait(wait_ord, effective_wait_tid);

    const aclError ret = real_wait_timeout(stream, event, timeout);

    if (ret == 0 && rec != nullptr) {
        acl_event_work_record_sidecar_snapshot(rec->call_sequence);
    }

    if (should_inject_after_wait && ret == 0) {
        if (rec != nullptr) {
            acl_event_work_on_wait_inject_claim(rec->call_sequence, rec->raw_event, rec->raw_stream);
            g_delay_state.last_inject_preload_cs.store(rec->call_sequence, std::memory_order_relaxed);
            g_delay_state.last_inject_raw_event.store(rec->raw_event, std::memory_order_relaxed);
            g_delay_state.last_inject_raw_stream.store(rec->raw_stream, std::memory_order_relaxed);
        }
        uint64_t host_enter = 0;
        uint64_t host_exit = 0;
        const int launch_rc = acl_event_work_try_launch(stream, &host_enter, &host_exit);
        g_delay_state.hostfunc_submit_rc.store(launch_rc, std::memory_order_relaxed);
        acl_event_work_on_inject_wait_rc(static_cast<int32_t>(ret));
        if (launch_rc != 0) {
            g_delay_state.inject_failed.store(1, std::memory_order_relaxed);
            g_state.fatal.fetch_add(1, std::memory_order_relaxed);
            if (rec != nullptr) {
                rec->acl_ret = static_cast<int32_t>(launch_rc);
                CommitSlot(rec);
            }
            errno = saved_errno;
            return static_cast<aclError>(launch_rc);
        }
        g_delay_state.match_count.fetch_add(1, std::memory_order_relaxed);
    }

    if (armed && rank0 && ret == 0) {
        const uint32_t tid = GetTid();
        if (effective_wait_tid != 0 && tid == effective_wait_tid) {
            g_delay_state.active_wait_success_ord.fetch_add(1, std::memory_order_relaxed);
        }
    }
    errno = saved_errno;
    if (rec != nullptr) {
        rec->acl_ret = static_cast<int32_t>(ret);
        CommitSlot(rec);
    }
    return ret;
}

}  // extern "C"
