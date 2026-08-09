#include <mspti/mspti.h>

#include "kseg_logic.hpp"

#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <exception>
#include <fstream>
#include <functional>
#include <mutex>
#include <queue>
#include <sstream>
#include <string>
#include <thread>
#include <time.h>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include <unistd.h>

namespace {

constexpr size_t kBufferSize = 8U * 1024U * 1024U;
constexpr size_t kDefaultMaxQueueBytes = 128U * 1024U * 1024U;  // 128 MiB / process
constexpr size_t kEventApproxBytes = 4096U;
constexpr uint64_t kDefaultDrainTimeoutMs = 12000;

uint64_t NowNs() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    return static_cast<uint64_t>(ts.tv_sec) * 1000000000ULL +
           static_cast<uint64_t>(ts.tv_nsec);
}

size_t EnvSize(const char* name, size_t fallback) {
    const char* raw = std::getenv(name);
    if (raw == nullptr || *raw == '\0') {
        return fallback;
    }
    char* end = nullptr;
    const unsigned long long value = std::strtoull(raw, &end, 10);
    if (end == raw || value == 0) {
        return fallback;
    }
    return static_cast<size_t>(value);
}

uint64_t EnvU64(const char* name, uint64_t fallback) {
    const char* raw = std::getenv(name);
    if (raw == nullptr || *raw == '\0') {
        return fallback;
    }
    char* end = nullptr;
    const unsigned long long value = std::strtoull(raw, &end, 10);
    if (end == raw) {
        return fallback;
    }
    return static_cast<uint64_t>(value);
}

std::string JsonEscape(const std::string& value) {
    std::ostringstream out;
    for (unsigned char ch : value) {
        switch (ch) {
            case '"': out << "\\\""; break;
            case '\\': out << "\\\\"; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:
                if (ch < 0x20) {
                    char buf[7];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", ch);
                    out << buf;
                } else {
                    out << static_cast<char>(ch);
                }
        }
    }
    return out.str();
}

std::string SafeString(const char* value) {
    return value == nullptr ? "" : std::string(value);
}

struct Event {
    std::string kind;
    uint64_t start_ns = 0;
    uint64_t end_ns = 0;
    int32_t device_id = -1;
    int64_t stream_id = -1;
    int64_t step = -1;
    int64_t peer_stream = -1;
    uint64_t correlation_id = 0;
    uint64_t count = 0;
    uint64_t bytes = 0;
    uint64_t active_ns = 0;
    uint64_t span_ns = 0;
    uint64_t gap_ns = 0;
    std::string op;
    std::string comm_name;
    std::string flags;
};

enum class ItemType { kRaw, kHostEvent, kStep, kBarrier };

enum class RawType { kKernel, kCommunication };

struct RawEvent {
    RawType type = RawType::kKernel;
    uint64_t start_ns = 0;
    uint64_t end_ns = 0;
    uint32_t device_id = 0;
    uint32_t stream_id = 0;
    uint64_t correlation_id = 0;
    uint64_t count = 0;
    uint64_t bytes = 0;
    int64_t step = -1;
    std::string op;
    std::string comm_name;
    std::string flags;
};

struct WorkItem {
    ItemType type = ItemType::kRaw;
    size_t accounted_bytes = 0;
    Event event;
    RawEvent raw;
};

struct RawLater {
    bool operator()(const RawEvent& lhs, const RawEvent& rhs) const {
        return lhs.start_ns > rhs.start_ns;
    }
};

uint64_t StreamKey(uint32_t device, uint32_t stream) {
    return (static_cast<uint64_t>(device) << 32U) | stream;
}

uint64_t DataTypeBytes(msptiCommunicationDataType type) {
    switch (type) {
        case MSPTI_ACTIVITY_COMMUNICATION_INT8:
        case MSPTI_ACTIVITY_COMMUNICATION_UINT8:
            return 1;
        case MSPTI_ACTIVITY_COMMUNICATION_INT16:
        case MSPTI_ACTIVITY_COMMUNICATION_UINT16:
        case MSPTI_ACTIVITY_COMMUNICATION_FP16:
        case MSPTI_ACTIVITY_COMMUNICATION_BFP16:
            return 2;
        case MSPTI_ACTIVITY_COMMUNICATION_INT32:
        case MSPTI_ACTIVITY_COMMUNICATION_UINT32:
        case MSPTI_ACTIVITY_COMMUNICATION_FP32:
            return 4;
        case MSPTI_ACTIVITY_COMMUNICATION_INT64:
        case MSPTI_ACTIVITY_COMMUNICATION_UINT64:
        case MSPTI_ACTIVITY_COMMUNICATION_FP64:
            return 8;
        case MSPTI_ACTIVITY_COMMUNICATION_INT128:
            return 16;
        default:
            return 0;
    }
}

bool IsP2p(const std::string& name) {
    std::string lower = name;
    std::transform(lower.begin(), lower.end(), lower.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    return lower.find("send") != std::string::npos || lower.find("recv") != std::string::npos;
}

size_t ApproxRawBytes(const RawEvent& raw) {
    return kEventApproxBytes + raw.op.size() + raw.comm_name.size() + raw.flags.size();
}

enum class LifecycleState {
    Idle = 0,
    Running,
    Capturing,
    Finalizing,
    Finalized,
    TerminalFailed,
};

class Collector {
public:
    // RAII: concurrent C ABI / thin-call paths register here so Finalize drain
    // waits before the unique final DROP.
    struct ActiveCallGuard {
        Collector* self = nullptr;
        explicit ActiveCallGuard(Collector* s) : self(s) {
            if (self != nullptr) {
                self->active_calls_.fetch_add(1, std::memory_order_acq_rel);
            }
        }
        ~ActiveCallGuard() {
            if (self == nullptr) {
                return;
            }
            self->active_calls_.fetch_sub(1, std::memory_order_acq_rel);
            self->NotifyDrainMaybe();
        }
        ActiveCallGuard(const ActiveCallGuard&) = delete;
        ActiveCallGuard& operator=(const ActiveCallGuard&) = delete;
    };

    static Collector& Instance() {
        // Process-lifetime intentional leak: terminal_failed may leave MSPTI
        // callbacks / outstanding buffers / worker alive. Never destroy the
        // owner so static teardown cannot terminate()/UAF via member dtors.
        static Collector* const instance = new Collector();
        return *instance;
    }

    int Start(const char* output_path, int rank, int device_id, uint64_t gap_ns,
              uint64_t reorder_ns, bool enable_capture_now) {
        std::unique_lock<std::mutex> lock(lifecycle_mu_);
        // Start only from Idle/Finalized; never during Finalizing/Capturing/TerminalFailed.
        if (LoadState() == LifecycleState::Finalizing || LoadState() == LifecycleState::Capturing ||
            LoadState() == LifecycleState::TerminalFailed || LoadState() == LifecycleState::Running ||
            running_.load() || output_path == nullptr) {
            return 1;
        }
        // Start→Finalize→Start: thorough reset of prior Finalized session.
        ResetSessionStateLocked();
        StoreState(LifecycleState::Idle);

        output_.open(output_path, std::ios::out | std::ios::trunc);
        if (!output_ || !output_.good()) {
            io_errors_.fetch_add(1);
            incomplete_.store(true);
            StoreState(LifecycleState::TerminalFailed);
            return 2;
        }
        rank_ = rank;
        default_device_id_ = device_id;
        gap_ns_ = gap_ns;
        reorder_ns_ = reorder_ns;
        max_queue_bytes_ = EnvSize("MSPTI_MAX_QUEUE_BYTES", kDefaultMaxQueueBytes);
        drain_timeout_ms_ = EnvU64("MSPTI_DRAIN_TIMEOUT_MS", kDefaultDrainTimeoutMs);
        const char* run_id = std::getenv("RUN_ID");
        run_id_ = run_id == nullptr ? "" : run_id;
        char host[256] = {};
        if (gethostname(host, sizeof(host) - 1) == 0) {
            host_ = host;
        } else {
            host_ = "unknown";
        }
        stopping_.store(false);
        intentional_buffer_leak_ = false;
        capturing_.store(enable_capture_now);
        try {
            worker_ = std::thread(&Collector::WorkerLoop, this);
        } catch (...) {
            incomplete_.store(true);
            StoreState(LifecycleState::TerminalFailed);
            return 3;
        }

        msptiResult result = msptiActivityRegisterCallbacks(BufferRequested, BufferCompleted);
        if (result != MSPTI_SUCCESS) {
            AbortStartLocked();
            return 10 + static_cast<int>(result);
        }
        result = msptiSubscribe(&subscriber_, Callback, this);
        if (result != MSPTI_SUCCESS) {
            AbortStartLocked();
            return 20 + static_cast<int>(result);
        }
        callback_subscribed_ = true;

        result = msptiEnableCallback(1, subscriber_, MSPTI_CB_DOMAIN_RUNTIME,
                                     MSPTI_CBID_RUNTIME_STREAM_SYNCHRONIZED);
        if (result != MSPTI_SUCCESS) {
            AbortStartLocked();
            return 25 + static_cast<int>(result);
        }
        runtime_callback_enabled_ = true;

        if (enable_capture_now) {
            const int enable_rc = EnableActivitiesLocked();
            if (enable_rc != 0) {
                AbortStartLocked();
                return enable_rc;
            }
            StoreState(LifecycleState::Capturing);
        } else {
            StoreState(LifecycleState::Running);
        }
        running_.store(true);
        return 0;
    }

    int CaptureBegin(int64_t step) {
        ActiveCallGuard guard(this);
        std::lock_guard<std::mutex> lock(lifecycle_mu_);
        if (LoadState() == LifecycleState::Finalizing || LoadState() == LifecycleState::Finalized ||
            LoadState() == LifecycleState::TerminalFailed) {
            return 80;
        }
        if (!running_.load() || LoadState() != LifecycleState::Running) {
            return 1;
        }
        if (capturing_.load()) {
            return 2;
        }
        last_incomplete_.store(false);
        last_capture_gate_ms_ = 0;
        const int enable_rc = EnableActivitiesLocked();
        if (enable_rc != 0) {
            return enable_rc;
        }
        capturing_.store(true);
        StoreState(LifecycleState::Capturing);
        current_step_.store(step);
        Event event;
        event.kind = "STEP";
        event.start_ns = NowNs();
        event.end_ns = event.start_ns;
        event.device_id = default_device_id_;
        event.step = step;
        event.count = 1;
        event.flags =
            "phase=begin;gate=capture_begin;time_domain=host_monotonic_raw";
        if (!EnqueueEvent(ItemType::kStep, std::move(event))) {
            queue_drops_.fetch_add(1);
            incomplete_.store(true);
            last_incomplete_.store(true);
            return 50;
        }
        return 0;
    }

    // 仅门控：入队 STEP end + Disable Activity。禁止 FlushAll / sleep / WaitAtMost。
    int CaptureEnd(int64_t step) {
        ActiveCallGuard guard(this);
        std::lock_guard<std::mutex> lock(lifecycle_mu_);
        if (LoadState() == LifecycleState::Finalizing || LoadState() == LifecycleState::Finalized ||
            LoadState() == LifecycleState::TerminalFailed) {
            return 80;
        }
        if (!running_.load() || !capturing_.load() || LoadState() != LifecycleState::Capturing) {
            return 1;
        }
        const uint64_t gate_t0 = NowNs();

        Event end_event;
        end_event.kind = "STEP";
        end_event.start_ns = NowNs();
        end_event.end_ns = end_event.start_ns;
        end_event.device_id = default_device_id_;
        end_event.step = step;
        end_event.count = 1;
        end_event.flags =
            "phase=end;gate=capture_end;time_domain=host_monotonic_raw;"
            "note=observation_boundary_no_flush";
        if (!EnqueueEvent(ItemType::kStep, std::move(end_event))) {
            queue_drops_.fetch_add(1);
            incomplete_.store(true);
            last_incomplete_.store(true);
            last_capture_gate_ms_ = (NowNs() - gate_t0) / 1e6;
            capturing_.store(false);
            StoreState(LifecycleState::Running);
            const int disable_rc = DisableActivitiesLocked();
            return disable_rc != 0 ? disable_rc : 50;
        }

        capturing_.store(false);
        StoreState(LifecycleState::Running);
        const int disable_rc = DisableActivitiesLocked();
        last_capture_gate_ms_ = (NowNs() - gate_t0) / 1e6;
        if (disable_rc != 0) {
            incomplete_.store(true);
            last_incomplete_.store(true);
            return disable_rc;
        }
        return 0;
    }

    int MarkStep(int64_t step, int phase) {
        ActiveCallGuard guard(this);
        if (!AcceptingLiveCalls()) {
            return 1;
        }
        current_step_.store(step);
        Event event;
        event.kind = "STEP";
        event.start_ns = NowNs();
        event.end_ns = event.start_ns;
        event.device_id = default_device_id_;
        event.step = step;
        event.count = 1;
        event.flags = phase == 0 ? "phase=begin;time_domain=host_monotonic_raw"
                                 : "phase=end;time_domain=host_monotonic_raw";
        if (!EnqueueEvent(ItemType::kStep, std::move(event))) {
            queue_drops_.fetch_add(1);
            incomplete_.store(true);
            return 50;
        }
        return 0;
    }

    // FlushAll(0) + 真 drain + Unsubscribe/join/DROP/关文件。Stop 同此逻辑。
    // Drain 超时/异常若仍有 outstanding：terminal failure，有意泄漏，绝不 free/close 仍可能被写的状态。
    int Finalize() {
        std::unique_lock<std::mutex> life(lifecycle_mu_);

        // Concurrent Finalize: wait for in-flight finalize to publish its result.
        if (LoadState() == LifecycleState::Finalizing) {
            finalize_cv_.wait(life, [this] {
                return LoadState() != LifecycleState::Finalizing;
            });
            return last_finalize_rc_;
        }
        if (LoadState() == LifecycleState::Finalized) {
            return last_finalize_rc_;
        }
        if (LoadState() == LifecycleState::TerminalFailed) {
            return last_finalize_rc_ != 0 ? last_finalize_rc_ : 76;
        }
        if (finalize_complete_.load() && LoadState() == LifecycleState::Finalized) {
            return last_finalize_rc_;
        }
        if (!running_.load() && !worker_.joinable() && LoadState() == LifecycleState::Idle) {
            last_finalize_rc_ = incomplete_.load() || io_errors_.load() > 0 ? 70 : 0;
            return last_finalize_rc_;
        }

        StoreState(LifecycleState::Finalizing);
        capturing_.store(false);
        int rc = 0;

        // 1) Disable KERNEL/COMM（若仍启用）。
        // Disable 失败 ⇒ MSPTI 仍可能回调：立即 TerminalFailed process-lifetime leak，
        // 不得继续 flush / stop worker / close / free / unsubscribe。
        {
            const int disable_rc = DisableActivitiesLocked();
            if (disable_rc != 0) {
                EnterTerminalFailedLocked(disable_rc);
                return last_finalize_rc_;
            }
        }

        // 2) 专用线程 FlushAll(0)，join（禁止 detach）；flag 0 仅按 MSPTI API。
        // 释放 lifecycle 锁，避免 Complete 回调与其他路径嵌套死锁。
        // flush 线程构造失败必须发布终态并唤醒并发 Finalize，禁止永久 Finalizing。
        life.unlock();
        const uint64_t flush_t0 = NowNs();
        msptiResult flush_result = MSPTI_SUCCESS;
        bool flush_thread_ok = false;
        try {
            std::thread flush_thread([&flush_result]() {
                flush_result = msptiActivityFlushAll(0);
            });
            flush_thread_ok = true;
            flush_thread.join();
        } catch (...) {
            flush_thread_ok = false;
        }
        last_finalize_flush_ms_ = (NowNs() - flush_t0) / 1e6;
        if (!flush_thread_ok) {
            incomplete_.store(true);
            life.lock();
            EnterTerminalFailedLocked(78);
            life.unlock();
            return 78;
        }
        if (flush_result != MSPTI_SUCCESS) {
            incomplete_.store(true);
            mspti_drops_.fetch_add(1);
            if (rc == 0) {
                rc = 60 + static_cast<int>(flush_result);
            }
        }

        // Flush join 后先查 inflight/queue；回调仍活时不得 Unsubscribe/close。
        const uint64_t drain_t0 = NowNs();
        const bool drained = WaitRealDrain(drain_timeout_ms_);
        last_finalize_drain_ms_ = (NowNs() - drain_t0) / 1e6;

        life.lock();
        const bool callbacks_live = HasLiveCallbacksLocked();
        if (!drained || callbacks_live) {
            incomplete_.store(true);
            last_incomplete_.store(true);
            if (rc == 0) {
                rc = 71;
            }
            // 安全失败：保留 outstanding，不 Unsubscribe/close/Free；intentional leak。
            EnterTerminalFailedLocked(rc);
            life.unlock();
            return rc;
        }

        // 3) 确认 quiescent 后再 Disable runtime callback + Unsubscribe
        if (callback_subscribed_) {
            if (runtime_callback_enabled_) {
                const msptiResult cb_rc =
                    msptiEnableCallback(0, subscriber_, MSPTI_CB_DOMAIN_RUNTIME,
                                        MSPTI_CBID_RUNTIME_STREAM_SYNCHRONIZED);
                runtime_callback_enabled_ = false;
                if (cb_rc != MSPTI_SUCCESS) {
                    incomplete_.store(true);
                    callback_drops_.fetch_add(1);
                    if (rc == 0) {
                        rc = 26 + static_cast<int>(cb_rc);
                    }
                }
            }
            const msptiResult unsub_rc = msptiUnsubscribe(subscriber_);
            if (unsub_rc != MSPTI_SUCCESS) {
                // Unsubscribe 失败：回调可能仍活。不得置 false / close / free。
                incomplete_.store(true);
                mspti_drops_.fetch_add(1);
                if (rc == 0) {
                    rc = 27 + static_cast<int>(unsub_rc);
                }
                EnterTerminalFailedLocked(rc);
                life.unlock();
                return rc;
            }
            callback_subscribed_ = false;
        }

        running_.store(false);
        life.unlock();

        // 4) 停 worker 并 join（禁止 detach）
        {
            std::lock_guard<std::mutex> qlock(queue_mu_);
            stopping_.store(true);
        }
        queue_cv_.notify_all();

        const auto join_deadline =
            std::chrono::steady_clock::now() +
            std::chrono::milliseconds(drain_timeout_ms_);
        while (worker_.joinable()) {
            if (worker_exited_.load()) {
                worker_.join();
                break;
            }
            if (std::chrono::steady_clock::now() >= join_deadline) {
                incomplete_.store(true);
                last_incomplete_.store(true);
                if (rc == 0) {
                    rc = 72;
                }
                // Fault isolation：worker 未退出时不得 close/free/unsubscribe；
                // 保留全部 callback 可达状态至进程退出（singleton 有意不析构）。
                life.lock();
                EnterTerminalFailedLocked(rc);
                life.unlock();
                return rc;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }

        // join 后再核验 queue/inflight/active_calls/seq 静默；若仍非空 → terminal，不 free outstanding。
        life.lock();
        if (LoadState() == LifecycleState::TerminalFailed) {
            // Worker exception (or other path) already published terminal result.
            life.unlock();
            return last_finalize_rc_;
        }
        if (HasLiveCallbacksLocked()) {
            incomplete_.store(true);
            if (rc == 0) {
                rc = 71;
            }
            EnterTerminalFailedLocked(rc);
            life.unlock();
            return rc;
        }
        life.unlock();

        // 5) 主线程收尾：pending + segments + DROP（DROP 必须最后写；写失败 → Finalize fail）
        DrainPending(true);
        FlushAllSegments();
        if (!WriteDropRowOnce()) {
            incomplete_.store(true);
            if (rc == 0) {
                rc = 73;
            }
        }

        // 6) flush / check / close（仅在无 live callback 时）
        {
            std::lock_guard<std::mutex> elock(emit_mu_);
            if (output_.is_open()) {
                output_.flush();
                if (!output_.good()) {
                    io_errors_.fetch_add(1);
                    incomplete_.store(true);
                    if (rc == 0) {
                        rc = 74;
                    }
                }
                output_.close();
                if (output_.fail()) {
                    io_errors_.fetch_add(1);
                    incomplete_.store(true);
                    if (rc == 0) {
                        rc = 75;
                    }
                }
            }
        }

        // 7) Unsubscribe 之后且 outstanding 为空才 free 缓冲池
        {
            std::lock_guard<std::mutex> lock(lifecycle_mu_);
            if (!intentional_buffer_leak_) {
                FreeAllBuffersLocked();
            }
        }

        if (incomplete_.load() || io_errors_.load() > 0 || allocation_drops_.load() > 0 ||
            queue_drops_.load() > 0 || parse_errors_.load() > 0 ||
            callback_drops_.load() > 0 || mspti_drops_.load() > 0) {
            if (rc == 0) {
                rc = 70;
            }
        }

        {
            std::lock_guard<std::mutex> lock(lifecycle_mu_);
            last_finalize_rc_ = rc;
            const bool ok = (rc == 0 && !incomplete_.load());
            finalize_complete_.store(ok);
            StoreState(ok ? LifecycleState::Finalized : LifecycleState::TerminalFailed);
            if (!ok && HasOutstandingBuffersUnlocked()) {
                intentional_buffer_leak_ = true;
            }
            finalize_cv_.notify_all();
        }
        return rc;
    }

    int Stop() { return Finalize(); }

    void GetLastCaptureStats(double* flush_ms, double* drain_ms, int* incomplete,
                             uint64_t* peak_queue_bytes) const {
        // CaptureEnd 仅门控：flush_ms 回传 gate 耗时，drain_ms 固定 0。
        if (flush_ms) {
            *flush_ms = last_capture_gate_ms_;
        }
        if (drain_ms) {
            *drain_ms = 0.0;
        }
        if (incomplete) {
            *incomplete = last_incomplete_.load() || incomplete_.load() ? 1 : 0;
        }
        if (peak_queue_bytes) {
            *peak_queue_bytes = peak_queue_bytes_.load();
        }
    }

    void GetLastFinalizeStats(double* flush_ms, double* drain_ms, int* complete,
                              uint64_t* raw_kernel_count, uint64_t* raw_comm_count,
                              uint64_t* peak_queue_bytes, double* capture_gate_ms) const {
        if (flush_ms) {
            *flush_ms = last_finalize_flush_ms_;
        }
        if (drain_ms) {
            *drain_ms = last_finalize_drain_ms_;
        }
        if (complete) {
            *complete = finalize_complete_.load() ? 1 : 0;
        }
        if (raw_kernel_count) {
            *raw_kernel_count = raw_kernel_count_.load();
        }
        if (raw_comm_count) {
            *raw_comm_count = raw_communication_count_.load();
        }
        if (peak_queue_bytes) {
            *peak_queue_bytes = peak_queue_bytes_.load();
        }
        if (capture_gate_ms) {
            *capture_gate_ms = last_capture_gate_ms_;
        }
    }

    void RecordThinSync(const char* function_name, uint64_t start_ns, uint64_t end_ns,
                        uintptr_t stream_token) {
        // Hold active-call until return so Finalize cannot write DROP mid-record.
        ActiveCallGuard guard(this);
        if (!AcceptingLiveCalls()) {
            return;
        }
        Event event;
        event.kind = "HSYNC";
        event.start_ns = start_ns;
        event.end_ns = end_ns;
        event.device_id = default_device_id_;
        event.step = current_step_.load();
        event.count = 1;
        event.op = SafeString(function_name);
        const bool from_torch =
            function_name != nullptr && std::strncmp(function_name, "torch.", 6) == 0;
        event.flags = from_torch
                          ? "source=workload_host_marker;time_domain=host_monotonic_raw"
                          : "source=acl_sync_interpose;time_domain=host_monotonic_raw";
        if (stream_token != 0) {
            std::ostringstream token;
            token << ";stream_handle=0x" << std::hex << stream_token;
            event.flags += token.str();
        }
        if (!EnqueueEvent(ItemType::kHostEvent, std::move(event))) {
            queue_drops_.fetch_add(1);
            incomplete_.store(true);
        }
    }

private:
    Collector() = default;
    // Singleton is intentionally never destroyed (see Instance). Destructor exists
    // only for completeness; LightShutdown must not free callback-reachable state
    // on terminal_failed (fault isolation, not success).
    ~Collector() { LightShutdown(); }
    Collector(const Collector&) = delete;
    Collector& operator=(const Collector&) = delete;

    void LightShutdown() {
        std::lock_guard<std::mutex> lock(lifecycle_mu_);
        if (LoadState() == LifecycleState::TerminalFailed || intentional_buffer_leak_) {
            // Fault isolation (NOT success): leave subscription, outstanding,
            // locks, queues, buffer pool, worker thread objects intact until
            // process exit. No close/free/unsubscribe/join that could race
            // live MSPTI callbacks.
            return;
        }
        if (finalize_complete_.load() && LoadState() == LifecycleState::Finalized) {
            FreeAllBuffersLocked();
            return;
        }
        capturing_.store(false);
        (void)DisableActivitiesLocked();
        if (callback_subscribed_ && !HasLiveCallbacksLocked()) {
            if (runtime_callback_enabled_) {
                (void)msptiEnableCallback(0, subscriber_, MSPTI_CB_DOMAIN_RUNTIME,
                                          MSPTI_CBID_RUNTIME_STREAM_SYNCHRONIZED);
                runtime_callback_enabled_ = false;
            }
            (void)msptiUnsubscribe(subscriber_);
            callback_subscribed_ = false;
        }
        {
            std::lock_guard<std::mutex> qlock(queue_mu_);
            stopping_.store(true);
        }
        queue_cv_.notify_all();
        if (worker_.joinable()) {
            worker_.join();
        }
        {
            std::lock_guard<std::mutex> elock(emit_mu_);
            if (output_.is_open()) {
                output_.close();
            }
        }
        if (!intentional_buffer_leak_) {
            FreeAllBuffersLocked();
        } else {
            FreePoolBuffersOnlyLocked();
        }
        running_.store(false);
    }

    bool HasOutstandingBuffersUnlocked() {
        std::lock_guard<std::mutex> block(buffer_pool_mu_);
        return !outstanding_buffers_.empty() || inflight_buffers_.load() > 0;
    }

    bool HasLiveCallbacksLocked() {
        // 勿在持有 queue_mu_ 时再锁 buffer_pool_mu_（与 Complete guard 锁序相反会死锁）。
        bool queue_busy = false;
        {
            std::lock_guard<std::mutex> qlock(queue_mu_);
            queue_busy = !queue_.empty() || worker_busy_.load();
        }
        return queue_busy || inflight_buffers_.load() > 0 || active_calls_.load() > 0 ||
               HasOutstandingBuffersUnlocked();
    }


    LifecycleState LoadState(std::memory_order order = std::memory_order_acquire) const {
        return state_.load(order);
    }

    void StoreState(LifecycleState st, std::memory_order order = std::memory_order_release) {
        // DROP / TerminalFailed / Finalized are terminal for the session until Start resets.
        // Never silently regress TerminalFailed → Running/Capturing/Idle without ResetSession.
        const LifecycleState prev = state_.load(std::memory_order_relaxed);
        if (prev == LifecycleState::TerminalFailed &&
            st != LifecycleState::TerminalFailed &&
            st != LifecycleState::Idle) {
            // Only ResetSessionStateLocked may clear TerminalFailed via Idle after Start gate.
            // StoreState itself refuses non-Idle transitions out of TerminalFailed.
            return;
        }
        state_.store(st, order);
    }

    bool AcceptingLiveCalls() const {
        // Unlocked hot path: must not tear-read plain enum under Finalize races.
        if (!running_.load(std::memory_order_acquire) ||
            stopping_.load(std::memory_order_acquire)) {
            return false;
        }
        const LifecycleState st = LoadState(std::memory_order_acquire);
        return st == LifecycleState::Running || st == LifecycleState::Capturing;
    }

    void EnterTerminalFailedLocked(int rc) {
        // Always mark intentional leak on terminal: even with empty outstanding,
        // subscription / worker / locks must remain intact until process exit.
        intentional_buffer_leak_ = true;
        incomplete_.store(true);
        last_incomplete_.store(true);
        last_finalize_rc_ = rc != 0 ? rc : 76;
        finalize_complete_.store(false);
        StoreState(LifecycleState::TerminalFailed);
        running_.store(false);
        // 不 close / 不 Unsubscribe / 不 Free outstanding / 不销毁 callback 可达对象。
        finalize_cv_.notify_all();
    }

    void FreePoolBuffersOnlyLocked() {
        std::lock_guard<std::mutex> block(buffer_pool_mu_);
        for (uint8_t* p : buffer_pool_) {
            if (p != nullptr) {
                std::free(p);
            }
        }
        buffer_pool_.clear();
        // outstanding intentionally retained
    }

    void FreeAllBuffersLocked() {
        std::lock_guard<std::mutex> block(buffer_pool_mu_);
        for (uint8_t* p : buffer_pool_) {
            if (p != nullptr) {
                std::free(p);
            }
        }
        buffer_pool_.clear();
        for (uint8_t* p : outstanding_buffers_) {
            if (p != nullptr) {
                std::free(p);
            }
        }
        outstanding_buffers_.clear();
        inflight_buffers_.store(0);
    }

    void ResetSessionStateLocked() {
        sequence_ = 0;
        max_seen_ns_ = 0;
        current_step_.store(-1);
        while (!pending_.empty()) {
            pending_.pop();
        }
        segments_.clear();
        step_boundaries_.clear();
        allocation_drops_.store(0);
        queue_drops_.store(0);
        parse_errors_.store(0);
        io_errors_.store(0);
        raw_kernel_count_.store(0);
        raw_communication_count_.store(0);
        emitted_count_.store(0);
        incomplete_.store(false);
        last_incomplete_.store(false);
        last_capture_gate_ms_ = 0;
        last_finalize_flush_ms_ = 0;
        last_finalize_drain_ms_ = 0;
        finalize_complete_.store(false);
        queue_bytes_ = 0;
        peak_queue_bytes_.store(0);
        inflight_buffers_.store(0);
        worker_busy_.store(false);
        {
            std::lock_guard<std::mutex> qlock(queue_mu_);
            queue_.clear();
            stopping_.store(false);
            worker_exited_.store(false);
            drop_written_.store(false);
        }
        active_calls_.store(0);
        capturing_.store(false);
        kernel_enabled_ = false;
        communication_enabled_ = false;
        callback_subscribed_ = false;
        runtime_callback_enabled_ = false;
        intentional_buffer_leak_ = false;
        last_finalize_rc_ = 0;
        callback_drops_.store(0);
        mspti_drops_.store(0);
        StoreState(LifecycleState::Idle);
        run_id_.clear();
        host_.clear();
        rank_ = -1;
        default_device_id_ = -1;
        FreeAllBuffersLocked();
    }

    // Start rollback: only clean close/free when EVERY disable/unsubscribe succeeds.
    // Any ActivityDisable / EnableCallback(0) / Unsubscribe failure → process-lifetime
    // TerminalFailed leak (do not lie about enabled/subscribed=false; do not free).
    void AbortStartLocked() {
        capturing_.store(false);
        int fail_rc = 0;
        const int disable_rc = DisableActivitiesLocked();
        if (disable_rc != 0) {
            fail_rc = disable_rc;
        }
        if (callback_subscribed_) {
            if (runtime_callback_enabled_) {
                const msptiResult cb_rc =
                    msptiEnableCallback(0, subscriber_, MSPTI_CB_DOMAIN_RUNTIME,
                                        MSPTI_CBID_RUNTIME_STREAM_SYNCHRONIZED);
                if (cb_rc != MSPTI_SUCCESS) {
                    incomplete_.store(true);
                    if (fail_rc == 0) {
                        fail_rc = 26 + static_cast<int>(cb_rc);
                    }
                    // Keep runtime_callback_enabled_=true — callback still reachable.
                } else {
                    runtime_callback_enabled_ = false;
                }
            }
            if (fail_rc == 0) {
                const msptiResult unsub_rc = msptiUnsubscribe(subscriber_);
                if (unsub_rc != MSPTI_SUCCESS) {
                    incomplete_.store(true);
                    fail_rc = 27 + static_cast<int>(unsub_rc);
                    // Keep callback_subscribed_=true.
                } else {
                    callback_subscribed_ = false;
                }
            }
        }
        if (fail_rc != 0) {
            // Process-lifetime leak: leave subscription/outstanding/worker objects.
            EnterTerminalFailedLocked(fail_rc);
            return;
        }
        // Clean rollback path only.
        {
            std::lock_guard<std::mutex> qlock(queue_mu_);
            stopping_.store(true);
        }
        queue_cv_.notify_all();
        if (worker_.joinable()) {
            worker_.join();
        }
        {
            std::lock_guard<std::mutex> elock(emit_mu_);
            if (output_.is_open()) {
                output_.close();
            }
        }
        FreeAllBuffersLocked();
        running_.store(false);
        StoreState(LifecycleState::Idle);
    }

    static void BufferRequested(uint8_t** buffer, size_t* size, size_t* max_records) noexcept {
        try {
            ActiveCallGuard guard(&Instance());
            Instance().OnBufferRequested(buffer, size, max_records);
        } catch (...) {
            try {
                Instance().allocation_drops_.fetch_add(1);
                Instance().incomplete_.store(true);
            } catch (...) {
            }
            if (buffer != nullptr) {
                *buffer = nullptr;
            }
            if (size != nullptr) {
                *size = 0;
            }
            if (max_records != nullptr) {
                *max_records = 0;
            }
        }
    }

    static void BufferCompleted(uint8_t* buffer, size_t size, size_t valid_size) noexcept {
        try {
            ActiveCallGuard guard(&Instance());
            Instance().OnBufferCompleted(buffer, size, valid_size);
        } catch (...) {
            try {
                Instance().parse_errors_.fetch_add(1);
                Instance().incomplete_.store(true);
            } catch (...) {
            }
        }
    }

    static void Callback(void* userdata, msptiCallbackDomain domain, msptiCallbackId cbid,
                         const msptiCallbackData* data) noexcept {
        try {
            auto* self = static_cast<Collector*>(userdata);
            ActiveCallGuard guard(self);
            self->OnCallback(domain, cbid, data);
        } catch (...) {
            try {
                if (userdata != nullptr) {
                    static_cast<Collector*>(userdata)->callback_drops_.fetch_add(1);
                    static_cast<Collector*>(userdata)->incomplete_.store(true);
                }
            } catch (...) {
            }
        }
    }

    int EnableActivitiesLocked() {
        msptiResult result = msptiActivityEnable(MSPTI_ACTIVITY_KIND_KERNEL);
        if (result != MSPTI_SUCCESS) {
            return 30 + static_cast<int>(result);
        }
        kernel_enabled_ = true;
        result = msptiActivityEnable(MSPTI_ACTIVITY_KIND_COMMUNICATION);
        if (result != MSPTI_SUCCESS) {
            const msptiResult disable_rc = msptiActivityDisable(MSPTI_ACTIVITY_KIND_KERNEL);
            if (disable_rc != MSPTI_SUCCESS) {
                // Keep kernel_enabled_=true: MSPTI may still deliver into us.
                // Caller (Start/Abort) must EnterTerminalFailed — never lie.
                incomplete_.store(true);
                return 42;  // enable-comm failed AND disable-kernel failed
            }
            kernel_enabled_ = false;
            return 40 + static_cast<int>(result);
        }
        communication_enabled_ = true;
        return 0;
    }

    int DisableActivitiesLocked() {
        int rc = 0;
        if (kernel_enabled_) {
            const msptiResult result = msptiActivityDisable(MSPTI_ACTIVITY_KIND_KERNEL);
            if (result != MSPTI_SUCCESS) {
                incomplete_.store(true);
                rc = 31 + static_cast<int>(result);
                // Do NOT clear kernel_enabled_: Activity may still fire.
            } else {
                kernel_enabled_ = false;
            }
        }
        if (communication_enabled_) {
            const msptiResult result = msptiActivityDisable(MSPTI_ACTIVITY_KIND_COMMUNICATION);
            if (result != MSPTI_SUCCESS) {
                incomplete_.store(true);
                if (rc == 0) {
                    rc = 41 + static_cast<int>(result);
                }
                // Do NOT clear communication_enabled_ on failure.
            } else {
                communication_enabled_ = false;
            }
        }
        return rc;
    }

    void OnBufferRequested(uint8_t** buffer, size_t* size, size_t* max_records) {
        if (buffer == nullptr || size == nullptr || max_records == nullptr) {
            return;
        }
        *buffer = nullptr;
        *size = 0;
        *max_records = 0;
        uint8_t* ptr = nullptr;
        try {
            {
                std::lock_guard<std::mutex> block(buffer_pool_mu_);
                if (!buffer_pool_.empty()) {
                    ptr = buffer_pool_.back();
                    buffer_pool_.pop_back();
                }
            }
            if (ptr == nullptr) {
                void* raw = nullptr;
                if (posix_memalign(&raw, 64, kBufferSize) != 0) {
                    allocation_drops_.fetch_add(1);
                    incomplete_.store(true);
                    return;
                }
                ptr = static_cast<uint8_t*>(raw);
            }
            {
                std::lock_guard<std::mutex> block(buffer_pool_mu_);
                try {
                    if (!outstanding_buffers_.insert(ptr).second) {
                        allocation_drops_.fetch_add(1);
                        incomplete_.store(true);
                        std::free(ptr);
                        return;
                    }
                } catch (...) {
                    allocation_drops_.fetch_add(1);
                    incomplete_.store(true);
                    std::free(ptr);
                    return;
                }
            }
            inflight_buffers_.fetch_add(1);
            *buffer = ptr;
            *size = kBufferSize;
            *max_records = 0;
        } catch (...) {
            if (ptr != nullptr) {
                std::free(ptr);
            }
            allocation_drops_.fetch_add(1);
            incomplete_.store(true);
            *buffer = nullptr;
            *size = 0;
            *max_records = 0;
        }
    }

    // Complete：校验 size；scope guard 保证每路径 free 原 buffer、outstanding 移除、
    // inflight 递减、notify。callback 边界 catch(...)，异常变 parse/allocation drop，不跨 C ABI。
    void OnBufferCompleted(uint8_t* buffer, size_t size, size_t valid_size) {
        struct CompleteGuard {
            Collector* self = nullptr;
            uint8_t* buffer = nullptr;
            bool owned = false;
            bool recycled = false;
            bool inflight_dec = false;
            ~CompleteGuard() noexcept {
                if (self == nullptr) {
                    return;
                }
                try {
                    if (owned && buffer != nullptr) {
                        if (recycled) {
                            bool pooled = false;
                            try {
                                std::lock_guard<std::mutex> block(self->buffer_pool_mu_);
                                self->buffer_pool_.push_back(buffer);
                                pooled = true;
                            } catch (...) {
                                pooled = false;
                            }
                            if (!pooled) {
                                std::free(buffer);
                            }
                        } else {
                            std::free(buffer);
                        }
                        buffer = nullptr;
                    }
                    if (owned && !inflight_dec) {
                        self->DecInflightAfterFree();
                        inflight_dec = true;
                    } else {
                        self->NotifyDrainMaybe();
                    }
                } catch (...) {
                    try {
                        if (owned && buffer != nullptr) {
                            std::free(buffer);
                            buffer = nullptr;
                        }
                        if (owned && !inflight_dec) {
                            self->DecInflightAfterFree();
                            inflight_dec = true;
                        }
                        self->parse_errors_.fetch_add(1);
                        self->incomplete_.store(true);
                    } catch (...) {
                    }
                }
            }
        };

        try {
            if (buffer == nullptr) {
                if (size != 0 || valid_size != 0) {
                    parse_errors_.fetch_add(1);
                    incomplete_.store(true);
                }
                NotifyDrainMaybe();
                return;
            }

            bool owned = false;
            {
                std::lock_guard<std::mutex> block(buffer_pool_mu_);
                owned = outstanding_buffers_.erase(buffer) > 0;
            }
            CompleteGuard guard{this, buffer, owned, false, false};

            if (!owned) {
                parse_errors_.fetch_add(1);
                incomplete_.store(true);
                return;
            }
            if (size == 0 || valid_size > size) {
                parse_errors_.fetch_add(1);
                incomplete_.store(true);
                // guard frees (not recycle)
                return;
            }

            if (!stopping_.load() && valid_size > 0) {
                msptiActivity* record = nullptr;
                while (true) {
                    const msptiResult result =
                        msptiActivityGetNextRecord(buffer, valid_size, &record);
                    if (result == MSPTI_ERROR_MAX_LIMIT_REACHED) {
                        break;
                    }
                    if (result != MSPTI_SUCCESS || record == nullptr) {
                        parse_errors_.fetch_add(1);
                        incomplete_.store(true);
                        break;
                    }
                    if (record->kind == MSPTI_ACTIVITY_KIND_KERNEL) {
                        auto* kernel = reinterpret_cast<msptiActivityKernel*>(record);
                        RawEvent event;
                        event.type = RawType::kKernel;
                        event.start_ns = kernel->start;
                        event.end_ns = kernel->end;
                        event.device_id = kernel->ds.deviceId;
                        event.stream_id = kernel->ds.streamId;
                        event.correlation_id = kernel->correlationId;
                        event.step = current_step_.load();
                        if (!EnqueueRaw(std::move(event))) {
                            queue_drops_.fetch_add(1);
                            incomplete_.store(true);
                        } else {
                            raw_kernel_count_.fetch_add(1);
                        }
                    } else if (record->kind == MSPTI_ACTIVITY_KIND_COMMUNICATION) {
                        auto* communication =
                            reinterpret_cast<msptiActivityCommunication*>(record);
                        RawEvent event;
                        event.type = RawType::kCommunication;
                        event.start_ns = communication->start;
                        event.end_ns = communication->end;
                        event.device_id = communication->ds.deviceId;
                        event.stream_id = communication->ds.streamId;
                        event.correlation_id = communication->correlationId;
                        event.count = communication->count;
                        const uint64_t element_bytes =
                            DataTypeBytes(communication->dataType);
                        event.bytes =
                            element_bytes == 0 ? 0 : element_bytes * communication->count;
                        event.step = current_step_.load();
                        event.op = SafeString(communication->name);
                        event.comm_name = SafeString(communication->commName);
                        event.flags =
                            "source=mspti_communication_activity;time_domain=device";
                        if (!EnqueueRaw(std::move(event))) {
                            queue_drops_.fetch_add(1);
                            incomplete_.store(true);
                        } else {
                            raw_communication_count_.fetch_add(1);
                        }
                    }
                }
            }
            guard.recycled = true;
        } catch (const std::bad_alloc&) {
            allocation_drops_.fetch_add(1);
            incomplete_.store(true);
        } catch (...) {
            parse_errors_.fetch_add(1);
            incomplete_.store(true);
        }
    }

    void DecInflightAfterFree() {
        if (inflight_buffers_.load() > 0) {
            inflight_buffers_.fetch_sub(1);
        }
        NotifyDrainMaybe();
    }

    void OnCallback(msptiCallbackDomain domain, msptiCallbackId cbid,
                    const msptiCallbackData* data) {
        try {
            // ActiveCallGuard held by Callback() static entry.
            if (!AcceptingLiveCalls() || !capturing_.load() || data == nullptr) {
                return;
            }
            if (domain != MSPTI_CB_DOMAIN_RUNTIME ||
                cbid != MSPTI_CBID_RUNTIME_STREAM_SYNCHRONIZED) {
                return;
            }
            thread_local std::unordered_map<uint64_t, uint64_t> enter_ns_by_corr;
            const uint64_t correlation_id = data->correlationId;
            if (data->callbackSite == MSPTI_API_ENTER) {
                enter_ns_by_corr[correlation_id] = NowNs();
                return;
            }
            uint64_t start_ns = NowNs();
            auto it = enter_ns_by_corr.find(correlation_id);
            if (it != enter_ns_by_corr.end()) {
                start_ns = it->second;
                enter_ns_by_corr.erase(it);
            }
            Event event;
            event.start_ns = start_ns;
            event.end_ns = NowNs();
            event.device_id = default_device_id_;
            event.step = current_step_.load();
            event.correlation_id = correlation_id;
            event.op = SafeString(data->functionName);
            event.count = 1;
            event.kind = "HSYNC";
            event.flags =
                "source=mspti_runtime_callback;time_domain=host_monotonic_raw;"
                "correlationData=unused_tls_pairing";
            if (!EnqueueEvent(ItemType::kHostEvent, std::move(event))) {
                queue_drops_.fetch_add(1);
                incomplete_.store(true);
            }
        } catch (...) {
            callback_drops_.fetch_add(1);
            incomplete_.store(true);
        }
    }

    bool EnqueueRaw(RawEvent raw) {
        WorkItem item;
        item.type = ItemType::kRaw;
        item.accounted_bytes = ApproxRawBytes(raw);
        item.raw = std::move(raw);
        return EnqueueItem(std::move(item));
    }

    bool EnqueueEvent(ItemType type, Event event) {
        WorkItem item;
        item.type = type;
        item.event = std::move(event);
        item.accounted_bytes = kEventApproxBytes + item.event.op.size() +
                               item.event.comm_name.size() + item.event.flags.size();
        return EnqueueItem(std::move(item));
    }

    bool EnqueueItem(WorkItem item) {
        std::lock_guard<std::mutex> lock(queue_mu_);
        if (stopping_.load()) {
            return false;
        }
        if (queue_bytes_ + item.accounted_bytes > max_queue_bytes_) {
            return false;
        }
        queue_bytes_ += item.accounted_bytes;
        if (queue_bytes_ > peak_queue_bytes_.load()) {
            peak_queue_bytes_.store(queue_bytes_);
        }
        queue_.push_back(std::move(item));
        queue_cv_.notify_one();
        return true;
    }

    bool IsQuiescentLocked() const {
        return queue_.empty() && inflight_buffers_.load() == 0 && !worker_busy_.load() &&
               active_calls_.load() == 0;
    }

    bool WaitRealDrain(uint64_t timeout_ms) {
        std::unique_lock<std::mutex> lock(queue_mu_);
        const auto deadline =
            std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
        while (true) {
            if (IsQuiescentLocked()) {
                return true;
            }
            if (queue_cv_.wait_until(lock, deadline) == std::cv_status::timeout) {
                return IsQuiescentLocked();
            }
        }
    }

    void NotifyDrainMaybe() {
        std::lock_guard<std::mutex> lock(queue_mu_);
        if (IsQuiescentLocked()) {
            queue_cv_.notify_all();
        }
    }

    bool WriteDropRowOnce() {
        if (drop_written_.exchange(true)) {
            return true;
        }
        return WriteDropRow();
    }

    bool WriteDropRow() {
        if (!output_.is_open()) {
            io_errors_.fetch_add(1);
            incomplete_.store(true);
            return false;
        }
        Event drop;
        drop.kind = "DROP";
        drop.start_ns = NowNs();
        drop.end_ns = drop.start_ns;
        drop.device_id = default_device_id_;
        drop.step = current_step_.load();
        const uint64_t alloc_n = allocation_drops_.load();
        const uint64_t queue_n = queue_drops_.load();
        const uint64_t parse_n = parse_errors_.load();
        const uint64_t io_n = io_errors_.load();
        const uint64_t cb_n = callback_drops_.load();
        const uint64_t mspti_n = mspti_drops_.load();
        drop.count = alloc_n + queue_n + parse_n + io_n + cb_n + mspti_n;
        drop.flags = "allocation=" + std::to_string(alloc_n) +
                     ";queue=" + std::to_string(queue_n) +
                     ";parse=" + std::to_string(parse_n) +
                     ";io=" + std::to_string(io_n) +
                     ";callback=" + std::to_string(cb_n) +
                     ";mspti=" + std::to_string(mspti_n) +
                     ";peak_queue_bytes=" + std::to_string(peak_queue_bytes_.load()) +
                     ";incomplete=" + std::to_string(incomplete_.load() ? 1 : 0) +
                     ";finalize=1" +
                     ";raw_kernels=" + std::to_string(raw_kernel_count_.load()) +
                     ";raw_comms=" + std::to_string(raw_communication_count_.load());
        const uint64_t io_before = io_errors_.load();
        Emit(drop);
        if (io_errors_.load() > io_before) {
            incomplete_.store(true);
            return false;
        }
        std::lock_guard<std::mutex> elock(emit_mu_);
        if (!output_.good()) {
            io_errors_.fetch_add(1);
            incomplete_.store(true);
            return false;
        }
        return true;
    }

    void WorkerLoop() {
        // Entire thread entry catch: priority_queue/map/string/I/O/alloc → worker_failure
        // TerminalFailed + wake Finalize waiters. Never std::terminate from here.
        try {
            while (true) {
                WorkItem item;
                {
                    std::unique_lock<std::mutex> lock(queue_mu_);
                    queue_cv_.wait(lock, [this] { return stopping_.load() || !queue_.empty(); });
                    if (queue_.empty()) {
                        if (stopping_.load()) {
                            break;
                        }
                        continue;
                    }
                    item = std::move(queue_.front());
                    queue_.pop_front();
                    if (queue_bytes_ >= item.accounted_bytes) {
                        queue_bytes_ -= item.accounted_bytes;
                    } else {
                        queue_bytes_ = 0;
                    }
                    worker_busy_.store(true);
                }

                if (item.type == ItemType::kRaw) {
                    PushRaw(std::move(item.raw));
                } else if (item.type == ItemType::kBarrier) {
                    DrainPending(true);
                    FlushAllSegments();
                    {
                        std::lock_guard<std::mutex> elock(emit_mu_);
                        if (output_.is_open()) {
                            output_.flush();
                        }
                    }
                } else {
                    if (item.type == ItemType::kStep &&
                        item.event.flags.find("phase=begin") != std::string::npos) {
                        step_boundaries_.emplace_back(item.event.start_ns, item.event.step);
                    }
                    Emit(item.event);
                }

                {
                    std::lock_guard<std::mutex> lock(queue_mu_);
                    worker_busy_.store(false);
                    if (IsQuiescentLocked()) {
                        queue_cv_.notify_all();
                    }
                }
            }
            DrainPending(true);
            FlushAllSegments();
            {
                std::lock_guard<std::mutex> lock(queue_mu_);
                worker_busy_.store(false);
                worker_exited_.store(true);
                queue_cv_.notify_all();
            }
        } catch (...) {
            try {
                incomplete_.store(true);
                last_incomplete_.store(true);
                {
                    std::lock_guard<std::mutex> lock(queue_mu_);
                    worker_busy_.store(false);
                    worker_exited_.store(true);
                    queue_cv_.notify_all();
                }
                {
                    std::lock_guard<std::mutex> life(lifecycle_mu_);
                    EnterTerminalFailedLocked(79);  // worker_failure
                }
            } catch (...) {
            }
        }
    }

    void PushRaw(RawEvent event) {
        max_seen_ns_ = std::max(max_seen_ns_, event.start_ns);
        pending_.push(std::move(event));
        DrainPending(false);
    }

    void DrainPending(bool all) {
        const uint64_t watermark = max_seen_ns_ > reorder_ns_ ? max_seen_ns_ - reorder_ns_ : 0;
        while (!pending_.empty() && (all || pending_.top().start_ns <= watermark)) {
            RawEvent event = pending_.top();
            pending_.pop();
            ProcessRaw(event);
        }
    }

    void ProcessRaw(const RawEvent& raw) {
        const uint64_t key = StreamKey(raw.device_id, raw.stream_id);
        const int64_t inferred_step = InferStep(raw.start_ns, raw.step);
        if (raw.type == RawType::kCommunication) {
            FlushSegment(key, raw.device_id, raw.stream_id);
            Event event;
            event.kind = IsP2p(raw.op) ? "P2P" : "COMM";
            event.start_ns = raw.start_ns;
            event.end_ns = raw.end_ns;
            event.device_id = static_cast<int32_t>(raw.device_id);
            event.stream_id = raw.stream_id;
            event.step = inferred_step;
            event.correlation_id = raw.correlation_id;
            event.count = raw.count;
            event.bytes = raw.bytes;
            event.active_ns = raw.end_ns >= raw.start_ns ? raw.end_ns - raw.start_ns : 0;
            event.span_ns = event.active_ns;
            event.op = raw.op;
            event.comm_name = raw.comm_name;
            event.flags = raw.flags;
            Emit(event);
            return;
        }

        // 禁止在 FlushSegment(erase) 后继续写旧 map 引用（悬空 → heap 破坏 → double free）。
        {
            auto it = segments_.find(key);
            if (it != segments_.end() &&
                it->second.TryMerge(raw.start_ns, raw.end_ns, inferred_step, gap_ns_)) {
                return;
            }
        }
        FlushSegment(key, raw.device_id, raw.stream_id);
        auto& fresh = segments_[key];
        fresh = mspti_skeleton::KsegAccumulator{};
        (void)fresh.TryMerge(raw.start_ns, raw.end_ns, inferred_step, gap_ns_);
    }

    int64_t InferStep(uint64_t timestamp_ns, int64_t fallback) const {
        int64_t step = fallback;
        for (const auto& boundary : step_boundaries_) {
            if (boundary.first > timestamp_ns) {
                break;
            }
            step = boundary.second;
        }
        return step;
    }

    void FlushSegment(uint64_t key, uint32_t device, uint32_t stream) {
        auto it = segments_.find(key);
        if (it == segments_.end() || !it->second.open) {
            return;
        }
        const auto& segment = it->second;
        Event event;
        event.kind = "KSEG";
        event.start_ns = segment.start_ns;
        event.end_ns = segment.end_ns;
        event.device_id = static_cast<int32_t>(device);
        event.stream_id = stream;
        event.step = segment.step;
        event.count = segment.count;
        segment.Finalize(&event.active_ns, &event.span_ns, &event.gap_ns);
        event.flags =
            "kernel_names=dropped;source=mspti_kernel_activity;time_domain=device;"
            "active_ns=interval_union";
        Emit(event);
        segments_.erase(it);
    }

    void FlushAllSegments() {
        std::vector<std::pair<uint32_t, uint32_t>> ids;
        ids.reserve(segments_.size());
        for (const auto& entry : segments_) {
            ids.emplace_back(static_cast<uint32_t>(entry.first >> 32U),
                             static_cast<uint32_t>(entry.first));
        }
        for (const auto& id : ids) {
            FlushSegment(StreamKey(id.first, id.second), id.first, id.second);
        }
    }

    void Emit(const Event& event) {
        std::lock_guard<std::mutex> elock(emit_mu_);
        EmitUnlocked(event);
    }

    void EmitUnlocked(const Event& event) {
        if (!output_.is_open()) {
            io_errors_.fetch_add(1);
            incomplete_.store(true);
            return;
        }
        output_ << "{\"run_id\":\"" << JsonEscape(run_id_) << "\","
                << "\"rank\":" << rank_ << ","
                << "\"host\":\"" << JsonEscape(host_) << "\","
                << "\"device_id\":" << event.device_id << ","
                << "\"stream_id\":" << event.stream_id << ","
                << "\"step\":" << event.step << ","
                << "\"kind\":\"" << event.kind << "\","
                << "\"start_ns\":" << event.start_ns << ","
                << "\"end_ns\":" << event.end_ns << ","
                << "\"seq\":" << sequence_++ << ","
                << "\"peer_stream\":" << event.peer_stream << ","
                << "\"correlation_id\":" << event.correlation_id << ","
                << "\"count\":" << event.count << ","
                << "\"bytes\":" << event.bytes << ","
                << "\"active_ns\":" << event.active_ns << ","
                << "\"span_ns\":" << event.span_ns << ","
                << "\"gap_ns\":" << event.gap_ns << ","
                << "\"op\":\"" << JsonEscape(event.op) << "\","
                << "\"comm_name\":\"" << JsonEscape(event.comm_name) << "\","
                << "\"flags\":\"" << JsonEscape(event.flags) << "\"}\n";
        if (!output_.good()) {
            io_errors_.fetch_add(1);
            incomplete_.store(true);
            return;
        }
        emitted_count_.fetch_add(1);
        const bool boundary =
            event.kind == "STEP" || event.kind == "DROP" || (emitted_count_.load() % 256U == 0U);
        if (boundary) {
            output_.flush();
            if (!output_.good()) {
                io_errors_.fetch_add(1);
                incomplete_.store(true);
            }
        }
    }

    std::mutex emit_mu_;
    std::mutex lifecycle_mu_;
    std::condition_variable finalize_cv_;
    std::atomic<LifecycleState> state_{LifecycleState::Idle};
    int last_finalize_rc_ = 0;
    bool intentional_buffer_leak_ = false;
    std::atomic<bool> running_{false};
    std::atomic<bool> capturing_{false};
    bool kernel_enabled_ = false;
    bool communication_enabled_ = false;
    bool callback_subscribed_ = false;
    bool runtime_callback_enabled_ = false;
    msptiSubscriberHandle subscriber_{};

    std::mutex queue_mu_;
    std::condition_variable queue_cv_;
    std::deque<WorkItem> queue_;
    size_t queue_bytes_ = 0;
    size_t max_queue_bytes_ = kDefaultMaxQueueBytes;
    std::atomic<uint64_t> peak_queue_bytes_{0};
    std::atomic<uint64_t> inflight_buffers_{0};
    std::mutex buffer_pool_mu_;
    std::vector<uint8_t*> buffer_pool_;
    std::unordered_set<uint8_t*> outstanding_buffers_;
    std::atomic<bool> worker_busy_{false};
    std::atomic<bool> stopping_{false};
    std::atomic<bool> worker_exited_{false};
    std::atomic<bool> drop_written_{false};
    std::atomic<bool> finalize_complete_{false};
    std::atomic<int> active_calls_{0};
    std::thread worker_;
    uint64_t drain_timeout_ms_ = kDefaultDrainTimeoutMs;

    std::ofstream output_;
    std::string run_id_;
    std::string host_;
    int rank_ = -1;
    int default_device_id_ = -1;
    uint64_t gap_ns_ = 50000;
    uint64_t reorder_ns_ = 1000000;
    std::atomic<int64_t> current_step_{-1};
    uint64_t sequence_ = 0;
    uint64_t max_seen_ns_ = 0;
    std::priority_queue<RawEvent, std::vector<RawEvent>, RawLater> pending_;
    std::unordered_map<uint64_t, mspti_skeleton::KsegAccumulator> segments_;
    std::vector<std::pair<uint64_t, int64_t>> step_boundaries_;

    std::atomic<uint64_t> allocation_drops_{0};
    std::atomic<uint64_t> queue_drops_{0};
    std::atomic<uint64_t> parse_errors_{0};
    std::atomic<uint64_t> io_errors_{0};
    std::atomic<uint64_t> callback_drops_{0};
    std::atomic<uint64_t> mspti_drops_{0};
    std::atomic<uint64_t> raw_kernel_count_{0};
    std::atomic<uint64_t> raw_communication_count_{0};
    std::atomic<uint64_t> emitted_count_{0};
    std::atomic<bool> incomplete_{false};

    double last_capture_gate_ms_ = 0;
    double last_finalize_flush_ms_ = 0;
    double last_finalize_drain_ms_ = 0;
    std::atomic<bool> last_incomplete_{false};
};

}  // namespace

extern "C" {

__attribute__((visibility("default")))
int mspti_skeleton_start(const char* output_path, int rank, int device_id,
                         uint64_t gap_ns, uint64_t reorder_ns) noexcept {
    try {
        return Collector::Instance().Start(
            output_path, rank, device_id, gap_ns, reorder_ns, true);
    } catch (...) {
        return 99;
    }
}

__attribute__((visibility("default")))
int mspti_skeleton_start_gated(const char* output_path, int rank, int device_id,
                               uint64_t gap_ns, uint64_t reorder_ns) noexcept {
    try {
        return Collector::Instance().Start(
            output_path, rank, device_id, gap_ns, reorder_ns, false);
    } catch (...) {
        return 99;
    }
}

__attribute__((visibility("default")))
int mspti_skeleton_capture_begin(int64_t step) noexcept {
    try {
        return Collector::Instance().CaptureBegin(step);
    } catch (...) {
        return 99;
    }
}

__attribute__((visibility("default")))
int mspti_skeleton_capture_end(int64_t step) noexcept {
    try {
        return Collector::Instance().CaptureEnd(step);
    } catch (...) {
        return 99;
    }
}

__attribute__((visibility("default")))
int mspti_skeleton_step(int64_t step, int phase) noexcept {
    try {
        return Collector::Instance().MarkStep(step, phase);
    } catch (...) {
        return 99;
    }
}

__attribute__((visibility("default")))
int mspti_skeleton_finalize() noexcept {
    try {
        return Collector::Instance().Finalize();
    } catch (...) {
        return 99;
    }
}

__attribute__((visibility("default")))
int mspti_skeleton_stop() noexcept {
    try {
        return Collector::Instance().Stop();
    } catch (...) {
        return 99;
    }
}

__attribute__((visibility("default")))
int mspti_skeleton_last_capture_stats(double* flush_ms, double* drain_ms, int* incomplete,
                                      uint64_t* peak_queue_bytes) noexcept {
    try {
        Collector::Instance().GetLastCaptureStats(
            flush_ms, drain_ms, incomplete, peak_queue_bytes);
        return 0;
    } catch (...) {
        return 99;
    }
}

__attribute__((visibility("default")))
int mspti_skeleton_last_finalize_stats(double* flush_ms, double* drain_ms, int* complete,
                                       uint64_t* raw_kernel_count, uint64_t* raw_comm_count,
                                       uint64_t* peak_queue_bytes, double* capture_gate_ms) noexcept {
    try {
        Collector::Instance().GetLastFinalizeStats(
            flush_ms, drain_ms, complete, raw_kernel_count, raw_comm_count, peak_queue_bytes,
            capture_gate_ms);
        return 0;
    } catch (...) {
        return 99;
    }
}

__attribute__((visibility("default")))
void mspti_skeleton_record_sync(
    const char* function_name, uint64_t start_ns, uint64_t end_ns, uintptr_t stream_token) noexcept {
    try {
        Collector::Instance().RecordThinSync(
            function_name, start_ns, end_ns, stream_token);
    } catch (...) {
    }
}

}
