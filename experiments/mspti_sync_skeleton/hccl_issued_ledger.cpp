// Independent host-side HCCL issued-work ledger.
//
// Build as a shared library and load before libtorch_npu (normally with
// LD_PRELOAD).  The six public Hccl* entry points below forward to libhccl and
// record API entry/return into a fixed in-process ring.  There is no file I/O,
// allocation, mutex, or device synchronization on the recorded hot path.

#include <array>
#include <atomic>
#include <cerrno>
#include <cinttypes>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <fcntl.h>
#include <mutex>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

namespace {

using HcclComm = void*;
using aclrtStream = void*;
using HcclResult = int32_t;
using HcclDataType = int32_t;
using HcclReduceOp = int32_t;

using AllReduceFn = HcclResult (*)(void*, void*, uint64_t, HcclDataType,
                                   HcclReduceOp, HcclComm, aclrtStream);
using BroadcastFn = HcclResult (*)(void*, uint64_t, HcclDataType, uint32_t,
                                   HcclComm, aclrtStream);
using AllGatherFn = HcclResult (*)(void*, void*, uint64_t, HcclDataType,
                                   HcclComm, aclrtStream);
using ReduceScatterFn = HcclResult (*)(void*, void*, uint64_t, HcclDataType,
                                       HcclReduceOp, HcclComm, aclrtStream);
using SendFn = HcclResult (*)(void*, uint64_t, HcclDataType, uint32_t,
                              HcclComm, aclrtStream);
using RecvFn = HcclResult (*)(void*, uint64_t, HcclDataType, uint32_t,
                              HcclComm, aclrtStream);

constexpr uint64_t kCapacity = 8192;
constexpr HcclResult kUnresolvedSymbolRc = 1;

enum class Op : uint32_t {
    kAllReduce = 1,
    kAllGather = 2,
    kReduceScatter = 3,
    kBroadcast = 4,
    kSend = 5,
    kRecv = 6,
};

const char* OpName(Op op) noexcept {
    switch (op) {
        case Op::kAllReduce:
            return "HcclAllReduce";
        case Op::kAllGather:
            return "HcclAllGather";
        case Op::kReduceScatter:
            return "HcclReduceScatter";
        case Op::kBroadcast:
            return "HcclBroadcast";
        case Op::kSend:
            return "HcclSend";
        case Op::kRecv:
            return "HcclRecv";
    }
    return "unknown";
}

uint64_t NowNs() noexcept {
    struct timespec ts {};
#if defined(CLOCK_MONOTONIC_RAW)
    constexpr clockid_t clock_id = CLOCK_MONOTONIC_RAW;
#else
    constexpr clockid_t clock_id = CLOCK_MONOTONIC;
#endif
    if (clock_gettime(clock_id, &ts) != 0) {
        return 0;
    }
    return static_cast<uint64_t>(ts.tv_sec) * 1000000000ULL +
           static_cast<uint64_t>(ts.tv_nsec);
}

uint64_t ThreadId() noexcept {
#if defined(__linux__)
    return static_cast<uint64_t>(::gettid());
#else
    uint64_t tid = 0;
    pthread_threadid_np(nullptr, &tid);
    return tid;
#endif
}

struct Entry {
    // state: 0=unused, 1=entry recorded, 2=real HCCL call returned.
    std::atomic<uint32_t> state{0};
    uint32_t op = 0;
    int32_t dtype = 0;
    int32_t aux = 0;
    int32_t rc = kUnresolvedSymbolRc;
    int64_t step = -1;
    uint64_t seq = 0;
    uint64_t pid = 0;
    uint64_t tid = 0;
    uint64_t count = 0;
    uint64_t entry_ns = 0;
    uint64_t return_ns = 0;
    uintptr_t comm = 0;
    uintptr_t stream = 0;
};

struct Binding {
    const char* name = nullptr;
    void* fn = nullptr;
    char path[1024]{};
    bool self_interpose = false;
};

std::array<Entry, kCapacity> g_entries;
std::atomic<uint64_t> g_seen_total{0};
std::atomic<uint64_t> g_outside_gate_total{0};
std::atomic<uint64_t> g_reserved_total{0};
std::atomic<uint64_t> g_overflow_total{0};
std::atomic<uint64_t> g_returned_total{0};
std::atomic<uint64_t> g_success_total{0};
std::atomic<uint64_t> g_error_total{0};
std::atomic<uint64_t> g_active_calls{0};
std::atomic<bool> g_gate{false};
std::atomic<int64_t> g_step{-1};
std::atomic<int64_t> g_end_step{-1};
std::atomic<uint64_t> g_begin_count{0};
std::atomic<uint64_t> g_end_count{0};
std::atomic<uint64_t> g_begin_ns{0};
std::atomic<uint64_t> g_end_ns{0};
std::atomic<bool> g_finalized{false};
std::atomic<int> g_finalize_rc{0};
std::mutex g_finalize_mu;

std::once_flag g_bind_once;
std::array<Binding, 6> g_bindings{{
    {"HcclAllReduce", nullptr, {}, false},
    {"HcclAllGather", nullptr, {}, false},
    {"HcclReduceScatter", nullptr, {}, false},
    {"HcclBroadcast", nullptr, {}, false},
    {"HcclSend", nullptr, {}, false},
    {"HcclRecv", nullptr, {}, false},
}};
char g_self_path[1024]{};

bool IsSelfSymbol(void* symbol) noexcept {
    if (symbol == nullptr) {
        return false;
    }
    Dl_info self_info {};
    Dl_info symbol_info {};
    if (dladdr(reinterpret_cast<void*>(&IsSelfSymbol), &self_info) == 0 ||
        dladdr(symbol, &symbol_info) == 0) {
        return false;
    }
    return self_info.dli_fbase != nullptr &&
           self_info.dli_fbase == symbol_info.dli_fbase;
}

void SetBindingPath(Binding* binding) noexcept {
    if (binding == nullptr || binding->fn == nullptr) {
        return;
    }
    Dl_info info {};
    if (dladdr(binding->fn, &info) != 0 && info.dli_fname != nullptr) {
        std::snprintf(binding->path, sizeof(binding->path), "%s", info.dli_fname);
    }
}

void* ResolveFromLibrary(const char* path, const char* name) noexcept {
    if (path == nullptr || *path == '\0') {
        return nullptr;
    }
    void* handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (handle == nullptr) {
        return nullptr;
    }
    void* symbol = dlsym(handle, name);
    if (IsSelfSymbol(symbol)) {
        return nullptr;
    }
    return symbol;
}

void ResolveBindings() noexcept {
    Dl_info self_info {};
    if (dladdr(reinterpret_cast<void*>(&ResolveBindings), &self_info) != 0 &&
        self_info.dli_fname != nullptr) {
        std::snprintf(g_self_path, sizeof(g_self_path), "%s", self_info.dli_fname);
    }
    const char* explicit_path = std::getenv("HCCL_ISSUED_LEDGER_HCCL_SO");
    constexpr const char* candidates[] = {
        "/usr/local/Ascend/cann-8.5.0/aarch64-linux/lib64/libhccl.so",
        "/usr/local/Ascend/cann-8.5.0/lib64/libhccl.so",
        "/usr/local/Ascend/ascend-toolkit/latest/lib64/libhccl.so",
        "libhccl.so",
    };
    for (Binding& binding : g_bindings) {
        void* symbol = dlsym(RTLD_NEXT, binding.name);
        if (IsSelfSymbol(symbol)) {
            binding.self_interpose = true;
            symbol = nullptr;
        }
        if (symbol == nullptr && explicit_path != nullptr && *explicit_path != '\0') {
            symbol = ResolveFromLibrary(explicit_path, binding.name);
        }
        if (symbol == nullptr) {
            for (const char* candidate : candidates) {
                symbol = ResolveFromLibrary(candidate, binding.name);
                if (symbol != nullptr) {
                    break;
                }
            }
        }
        if (IsSelfSymbol(symbol)) {
            binding.self_interpose = true;
            symbol = nullptr;
        }
        binding.fn = symbol;
        SetBindingPath(&binding);
    }
}

void EnsureBindings() noexcept {
    try {
        std::call_once(g_bind_once, ResolveBindings);
    } catch (...) {
    }
}

Binding* FindBinding(const char* name) noexcept {
    EnsureBindings();
    for (Binding& binding : g_bindings) {
        if (std::strcmp(binding.name, name) == 0) {
            return &binding;
        }
    }
    return nullptr;
}

struct Token {
    Entry* entry = nullptr;
    bool captured = false;
};

Token RecordEntry(Op op, uint64_t count, int32_t dtype, int32_t aux,
                  HcclComm comm, aclrtStream stream) noexcept {
    g_seen_total.fetch_add(1, std::memory_order_relaxed);
    if (!g_gate.load(std::memory_order_acquire)) {
        g_outside_gate_total.fetch_add(1, std::memory_order_relaxed);
        return {};
    }
    const uint64_t index = g_reserved_total.fetch_add(1, std::memory_order_relaxed);
    g_active_calls.fetch_add(1, std::memory_order_relaxed);
    if (index >= kCapacity) {
        g_overflow_total.fetch_add(1, std::memory_order_relaxed);
        return {nullptr, true};
    }
    Entry& entry = g_entries[static_cast<size_t>(index)];
    entry.op = static_cast<uint32_t>(op);
    entry.dtype = dtype;
    entry.aux = aux;
    entry.rc = kUnresolvedSymbolRc;
    entry.step = g_step.load(std::memory_order_relaxed);
    entry.seq = index + 1;
    entry.pid = static_cast<uint64_t>(getpid());
    entry.tid = ThreadId();
    entry.count = count;
    entry.entry_ns = NowNs();
    entry.return_ns = 0;
    entry.comm = reinterpret_cast<uintptr_t>(comm);
    entry.stream = reinterpret_cast<uintptr_t>(stream);
    entry.state.store(1, std::memory_order_release);
    return {&entry, true};
}

void RecordReturn(Token token, HcclResult rc) noexcept {
    if (!token.captured) {
        return;
    }
    if (token.entry != nullptr) {
        token.entry->rc = rc;
        token.entry->return_ns = NowNs();
        token.entry->state.store(2, std::memory_order_release);
    }
    g_returned_total.fetch_add(1, std::memory_order_relaxed);
    if (rc == 0) {
        g_success_total.fetch_add(1, std::memory_order_relaxed);
    } else {
        g_error_total.fetch_add(1, std::memory_order_relaxed);
    }
    g_active_calls.fetch_sub(1, std::memory_order_relaxed);
}

uint64_t Fnv1aUpdate(uint64_t hash, const void* data, size_t size) noexcept {
    const auto* bytes = static_cast<const uint8_t*>(data);
    for (size_t index = 0; index < size; ++index) {
        hash ^= bytes[index];
        hash *= 1099511628211ULL;
    }
    return hash;
}

void JsonString(FILE* output, const char* text) {
    std::fputc('"', output);
    if (text != nullptr) {
        for (const unsigned char* ptr =
                 reinterpret_cast<const unsigned char*>(text);
             *ptr != 0; ++ptr) {
            switch (*ptr) {
                case '"':
                    std::fputs("\\\"", output);
                    break;
                case '\\':
                    std::fputs("\\\\", output);
                    break;
                case '\n':
                    std::fputs("\\n", output);
                    break;
                case '\r':
                    std::fputs("\\r", output);
                    break;
                case '\t':
                    std::fputs("\\t", output);
                    break;
                default:
                    if (*ptr < 0x20) {
                        std::fprintf(output, "\\u%04x", static_cast<unsigned>(*ptr));
                    } else {
                        std::fputc(*ptr, output);
                    }
            }
        }
    }
    std::fputc('"', output);
}

int OpenTemporary(const char* final_path, char* temporary, size_t size) noexcept {
    const int written = std::snprintf(temporary, size, "%s.tmp.%d", final_path,
                                      static_cast<int>(getpid()));
    if (written <= 0 || static_cast<size_t>(written) >= size) {
        return -1;
    }
    return open(temporary, O_CREAT | O_TRUNC | O_WRONLY, 0644);
}

bool CommitFile(FILE* output, int fd, const char* temporary,
                const char* final_path) noexcept {
    if (output == nullptr || fd < 0) {
        return false;
    }
    bool ok = std::fflush(output) == 0;
    if (ok) {
        ok = fsync(fd) == 0;
    }
    if (std::fclose(output) != 0) {
        ok = false;
    }
    if (ok && rename(temporary, final_path) != 0) {
        ok = false;
    }
    if (!ok) {
        unlink(temporary);
    }
    return ok;
}

int EnvRank() noexcept {
    const char* value = std::getenv("RANK");
    if (value == nullptr || *value == '\0') {
        return -1;
    }
    char* end = nullptr;
    errno = 0;
    const long rank = std::strtol(value, &end, 10);
    if (errno != 0 || end == value || *end != '\0' || rank < -1 || rank > 1000000) {
        return -1;
    }
    return static_cast<int>(rank);
}

bool BuildPath(char* output, size_t size, const char* suffix) noexcept {
    const char* out_dir = std::getenv("HCCL_ISSUED_LEDGER_OUT_DIR");
    if (out_dir == nullptr || *out_dir == '\0') {
        return false;
    }
    const int rank = EnvRank();
    if (rank < 0) {
        return false;
    }
    const int written = std::snprintf(output, size, "%s/rank_%04d.%s", out_dir,
                                      rank, suffix);
    return written > 0 && static_cast<size_t>(written) < size;
}

int WriteOutputs() noexcept {
    char jsonl_path[4096]{};
    char summary_path[4096]{};
    if (!BuildPath(jsonl_path, sizeof(jsonl_path), "hccl_issued.jsonl") ||
        !BuildPath(summary_path, sizeof(summary_path), "hccl_issued_summary.json")) {
        return 20;
    }

    char jsonl_tmp[4200]{};
    int jsonl_fd = OpenTemporary(jsonl_path, jsonl_tmp, sizeof(jsonl_tmp));
    if (jsonl_fd < 0) {
        return 21;
    }
    FILE* jsonl = fdopen(jsonl_fd, "w");
    if (jsonl == nullptr) {
        close(jsonl_fd);
        unlink(jsonl_tmp);
        return 21;
    }

    const uint64_t reserved = g_reserved_total.load(std::memory_order_acquire);
    const uint64_t stored = reserved < kCapacity ? reserved : kCapacity;
    uint64_t incomplete = 0;
    uint64_t digest = 1469598103934665603ULL;
    for (uint64_t index = 0; index < stored; ++index) {
        const Entry& entry = g_entries[static_cast<size_t>(index)];
        const uint32_t state = entry.state.load(std::memory_order_acquire);
        if (state != 2) {
            ++incomplete;
        }
        digest = Fnv1aUpdate(digest, &entry.op, sizeof(entry.op));
        digest = Fnv1aUpdate(digest, &entry.count, sizeof(entry.count));
        digest = Fnv1aUpdate(digest, &entry.dtype, sizeof(entry.dtype));
        digest = Fnv1aUpdate(digest, &entry.aux, sizeof(entry.aux));
        std::fprintf(
            jsonl,
            "{\"schema_version\":1,\"instrumentation\":\"hccl_issued_ledger_v1\","
            "\"rank\":%d,\"pid\":%" PRIu64 ",\"tid\":%" PRIu64
            ",\"step\":%" PRId64 ",\"seq\":%" PRIu64 ",\"op\":",
            EnvRank(), entry.pid, entry.tid, entry.step, entry.seq);
        JsonString(jsonl, OpName(static_cast<Op>(entry.op)));
        std::fprintf(
            jsonl,
            ",\"count\":%" PRIu64 ",\"dtype\":%d,\"aux\":%d,"
            "\"comm_handle\":\"0x%" PRIxPTR "\",\"stream_handle\":\"0x%" PRIxPTR
            "\",\"entry_ns\":%" PRIu64 ",\"return_ns\":%" PRIu64
            ",\"rc\":%d,\"returned\":%s}\n",
            entry.count, entry.dtype, entry.aux, entry.comm, entry.stream,
            entry.entry_ns, entry.return_ns, entry.rc, state == 2 ? "true" : "false");
    }
    if (!CommitFile(jsonl, jsonl_fd, jsonl_tmp, jsonl_path)) {
        return 22;
    }

    bool bindings_ok = true;
    for (const Binding& binding : g_bindings) {
        bindings_ok = bindings_ok && binding.fn != nullptr && !binding.self_interpose;
    }
    const uint64_t overflow = g_overflow_total.load(std::memory_order_relaxed);
    const uint64_t begin_count = g_begin_count.load(std::memory_order_relaxed);
    const uint64_t end_count = g_end_count.load(std::memory_order_relaxed);
    const bool pass = bindings_ok && begin_count == 1 && end_count == 1 &&
                      g_end_step.load(std::memory_order_relaxed) ==
                          g_step.load(std::memory_order_relaxed) &&
                      !g_gate.load(std::memory_order_relaxed) &&
                      g_begin_ns.load(std::memory_order_relaxed) > 0 &&
                      g_end_ns.load(std::memory_order_relaxed) >=
                          g_begin_ns.load(std::memory_order_relaxed) &&
                      overflow == 0 && incomplete == 0 &&
                      g_error_total.load(std::memory_order_relaxed) == 0 && stored > 0;

    char summary_tmp[4200]{};
    int summary_fd = OpenTemporary(summary_path, summary_tmp, sizeof(summary_tmp));
    if (summary_fd < 0) {
        return 23;
    }
    FILE* summary = fdopen(summary_fd, "w");
    if (summary == nullptr) {
        close(summary_fd);
        unlink(summary_tmp);
        return 23;
    }
    std::fprintf(
        summary,
        "{\n  \"schema_version\": 1,\n"
        "  \"instrumentation\": \"hccl_issued_ledger_v1\",\n"
        "  \"rank\": %d,\n  \"pid\": %d,\n  \"capacity\": %" PRIu64 ",\n"
        "  \"clock\": \"CLOCK_MONOTONIC_RAW_or_MONOTONIC\",\n"
        "  \"entry_semantics\": \"issued at Hccl* API entry; accepted iff return rc=0\",\n"
        "  \"begin_step\": %" PRId64 ",\n  \"end_step\": %" PRId64 ",\n"
        "  \"begin_count\": %" PRIu64 ",\n"
        "  \"end_count\": %" PRIu64 ",\n  \"begin_ns\": %" PRIu64 ",\n"
        "  \"end_ns\": %" PRIu64 ",\n  \"seen_total\": %" PRIu64 ",\n"
        "  \"outside_gate_total\": %" PRIu64 ",\n"
        "  \"captured_issued\": %" PRIu64 ",\n"
        "  \"captured_stored\": %" PRIu64 ",\n"
        "  \"captured_returned\": %" PRIu64 ",\n"
        "  \"captured_accepted\": %" PRIu64 ",\n"
        "  \"captured_error\": %" PRIu64 ",\n"
        "  \"overflow_count\": %" PRIu64 ",\n"
        "  \"incomplete_records\": %" PRIu64 ",\n"
        "  \"active_calls_at_finalize\": %" PRIu64 ",\n"
        "  \"schedule_digest_fnv1a64\": \"%016" PRIx64 "\",\n"
        "  \"self_so_path\": ",
        EnvRank(), static_cast<int>(getpid()), kCapacity,
        g_step.load(std::memory_order_relaxed),
        g_end_step.load(std::memory_order_relaxed), begin_count, end_count,
        g_begin_ns.load(std::memory_order_relaxed),
        g_end_ns.load(std::memory_order_relaxed),
        g_seen_total.load(std::memory_order_relaxed),
        g_outside_gate_total.load(std::memory_order_relaxed), reserved, stored,
        g_returned_total.load(std::memory_order_relaxed),
        g_success_total.load(std::memory_order_relaxed),
        g_error_total.load(std::memory_order_relaxed), overflow, incomplete,
        g_active_calls.load(std::memory_order_relaxed), digest);
    JsonString(summary, g_self_path);
    std::fputs(",\n  \"bindings\": [\n", summary);
    for (size_t index = 0; index < g_bindings.size(); ++index) {
        const Binding& binding = g_bindings[index];
        std::fputs("    {\"symbol\": ", summary);
        JsonString(summary, binding.name);
        std::fprintf(summary, ", \"resolved\": %s, \"self_interpose\": %s, \"path\": ",
                     binding.fn != nullptr ? "true" : "false",
                     binding.self_interpose ? "true" : "false");
        JsonString(summary, binding.path);
        std::fprintf(summary, "}%s\n", index + 1 == g_bindings.size() ? "" : ",");
    }
    std::fprintf(summary,
                 "  ],\n  \"all_bindings_ok\": %s,\n  \"overflow_fail_closed\": true,\n"
                 "  \"pass\": %s\n}\n",
                 bindings_ok ? "true" : "false", pass ? "true" : "false");
    if (!CommitFile(summary, summary_fd, summary_tmp, summary_path)) {
        return 24;
    }
    return pass ? 0 : 30;
}

template <typename Fn, typename Call>
HcclResult Forward(const char* symbol, Token token, Call&& call) noexcept {
    Binding* binding = FindBinding(symbol);
    HcclResult rc = kUnresolvedSymbolRc;
    if (binding != nullptr && binding->fn != nullptr) {
        rc = call(reinterpret_cast<Fn>(binding->fn));
    }
    RecordReturn(token, rc);
    return rc;
}

}  // namespace

extern "C" {

int hccl_issued_ledger_begin(int64_t step) noexcept {
    EnsureBindings();
    const uint64_t attempt =
        g_begin_count.fetch_add(1, std::memory_order_relaxed) + 1;
    if (attempt != 1 || g_finalized.load(std::memory_order_acquire) ||
        g_gate.exchange(true, std::memory_order_acq_rel)) {
        return 1;
    }
    g_step.store(step, std::memory_order_relaxed);
    g_begin_ns.store(NowNs(), std::memory_order_relaxed);
    return 0;
}

int hccl_issued_ledger_end(int64_t step) noexcept {
    const uint64_t attempt =
        g_end_count.fetch_add(1, std::memory_order_relaxed) + 1;
    g_end_step.store(step, std::memory_order_relaxed);
    if (!g_gate.exchange(false, std::memory_order_acq_rel)) {
        return 2;
    }
    g_end_ns.store(NowNs(), std::memory_order_relaxed);
    return attempt == 1 && step == g_step.load(std::memory_order_relaxed) ? 0 : 3;
}

int hccl_issued_ledger_finalize() noexcept {
    std::lock_guard<std::mutex> lock(g_finalize_mu);
    if (g_finalized.load(std::memory_order_acquire)) {
        return g_finalize_rc.load(std::memory_order_acquire);
    }
    if (g_gate.exchange(false, std::memory_order_acq_rel)) {
        g_end_ns.store(NowNs(), std::memory_order_relaxed);
    }
    EnsureBindings();
    const int rc = WriteOutputs();
    g_finalize_rc.store(rc, std::memory_order_release);
    g_finalized.store(true, std::memory_order_release);
    return rc;
}

uint64_t hccl_issued_ledger_capacity() noexcept { return kCapacity; }

HcclResult HcclAllReduce(void* send, void* recv, uint64_t count,
                         HcclDataType dtype, HcclReduceOp op, HcclComm comm,
                         aclrtStream stream) noexcept {
    Token token = RecordEntry(Op::kAllReduce, count, dtype, op, comm, stream);
    return Forward<AllReduceFn>("HcclAllReduce", token, [&](AllReduceFn fn) {
        return fn(send, recv, count, dtype, op, comm, stream);
    });
}

HcclResult HcclAllGather(void* send, void* recv, uint64_t count,
                         HcclDataType dtype, HcclComm comm,
                         aclrtStream stream) noexcept {
    Token token = RecordEntry(Op::kAllGather, count, dtype, 0, comm, stream);
    return Forward<AllGatherFn>("HcclAllGather", token, [&](AllGatherFn fn) {
        return fn(send, recv, count, dtype, comm, stream);
    });
}

HcclResult HcclReduceScatter(void* send, void* recv, uint64_t count,
                             HcclDataType dtype, HcclReduceOp op,
                             HcclComm comm, aclrtStream stream) noexcept {
    Token token = RecordEntry(Op::kReduceScatter, count, dtype, op, comm, stream);
    return Forward<ReduceScatterFn>("HcclReduceScatter", token,
                                    [&](ReduceScatterFn fn) {
        return fn(send, recv, count, dtype, op, comm, stream);
    });
}

HcclResult HcclBroadcast(void* buffer, uint64_t count, HcclDataType dtype,
                         uint32_t root, HcclComm comm,
                         aclrtStream stream) noexcept {
    Token token = RecordEntry(Op::kBroadcast, count, dtype,
                              static_cast<int32_t>(root), comm, stream);
    return Forward<BroadcastFn>("HcclBroadcast", token, [&](BroadcastFn fn) {
        return fn(buffer, count, dtype, root, comm, stream);
    });
}

HcclResult HcclSend(void* buffer, uint64_t count, HcclDataType dtype,
                    uint32_t peer, HcclComm comm, aclrtStream stream) noexcept {
    Token token = RecordEntry(Op::kSend, count, dtype,
                              static_cast<int32_t>(peer), comm, stream);
    return Forward<SendFn>("HcclSend", token, [&](SendFn fn) {
        return fn(buffer, count, dtype, peer, comm, stream);
    });
}

HcclResult HcclRecv(void* buffer, uint64_t count, HcclDataType dtype,
                    uint32_t peer, HcclComm comm, aclrtStream stream) noexcept {
    Token token = RecordEntry(Op::kRecv, count, dtype,
                              static_cast<int32_t>(peer), comm, stream);
    return Forward<RecvFn>("HcclRecv", token, [&](RecvFn fn) {
        return fn(buffer, count, dtype, peer, comm, stream);
    });
}

}  // extern "C"
