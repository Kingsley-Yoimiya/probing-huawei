// Callback-safe MSPTI accounting. Hot paths use atomics and bounded POD only;
// serialization happens after callbacks are drained.
#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <string>

namespace mspti_skeleton {

constexpr size_t kMalformedTimestampSampleLimit = 16;
constexpr size_t kBufferLedgerCapacity = 4096;
constexpr int32_t kGetNextNotCalled = std::numeric_limits<int32_t>::min();
constexpr int32_t kGetNextInvalidBuffer = kGetNextNotCalled + 1;
constexpr int32_t kGetNextException = kGetNextNotCalled + 2;

enum class AuditRawKind : uint32_t { kKernel = 1, kCommunication = 2 };
enum class AuditContentStage : uint32_t {
    kParsed = 0, kEnqueued, kWorker, kProcessed, kEmitted, kCount
};
enum class AuditLifecyclePoint : uint32_t {
    kBeforeDisable = 0, kAfterFlush, kBeforeFree, kCount
};

constexpr size_t kAuditContentStageCount =
    static_cast<size_t>(AuditContentStage::kCount);
constexpr size_t kAuditRawKindCount = 2;
constexpr size_t kAuditLifecyclePointCount =
    static_cast<size_t>(AuditLifecyclePoint::kCount);

inline size_t AuditKindIndex(AuditRawKind kind) noexcept {
    return kind == AuditRawKind::kCommunication ? 1U : 0U;
}

inline uint64_t Mix64(uint64_t value) noexcept {
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30U)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27U)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31U);
}

inline uint64_t PointerToken(const void* pointer) noexcept {
    return Mix64(static_cast<uint64_t>(reinterpret_cast<uintptr_t>(pointer)));
}

inline uint64_t ContentHash(AuditRawKind kind, uint64_t start_ns, uint64_t end_ns,
                            uint32_t device_id, uint32_t stream_id,
                            uint64_t correlation_id, uint64_t count,
                            uint64_t bytes) noexcept {
    uint64_t hash = Mix64(static_cast<uint64_t>(kind));
    hash ^= Mix64(start_ns + 0x101ULL);
    hash ^= Mix64(end_ns + 0x202ULL);
    hash ^= Mix64((static_cast<uint64_t>(device_id) << 32U) | stream_id);
    hash ^= Mix64(correlation_id + 0x303ULL);
    hash ^= Mix64(count + 0x404ULL);
    hash ^= Mix64(bytes + 0x505ULL);
    return Mix64(hash);
}

struct ContentFingerprintSnapshot {
    uint64_t count = 0;
    uint64_t xor64 = 0;
    uint64_t sum64 = 0;
};

inline void AddFingerprint(ContentFingerprintSnapshot* target, uint64_t hash) noexcept {
    if (target != nullptr) {
        ++target->count;
        target->xor64 ^= hash;
        target->sum64 += hash;
    }
}

inline bool SameFingerprint(const ContentFingerprintSnapshot& lhs,
                            const ContentFingerprintSnapshot& rhs) noexcept {
    return lhs.count == rhs.count && lhs.xor64 == rhs.xor64 &&
           lhs.sum64 == rhs.sum64;
}

struct BufferLedgerHandle {
    uint64_t request_id = 0;
    uint32_t slot = 0;
    bool valid = false;
};

struct BufferLedgerEntry {
    uint64_t request_id = 0;
    uint64_t pointer_token = 0;
    uint64_t reuse_generation = 0;
    uint64_t request_monotonic_ns = 0;
    uint64_t complete_monotonic_ns = 0;
    uint64_t requested_size = 0;
    uint64_t completion_size = 0;
    uint64_t valid_size = 0;
    uint64_t callback_wall_ns = 0;
    uint64_t get_next_calls = 0;
    int32_t get_next_terminal_code = kGetNextNotCalled;
    uint64_t kernel_records = 0;
    uint64_t comm_records = 0;
    uint64_t unknown_records = 0;
    uint32_t first_kind = 0;
    uint32_t last_kind = 0;
    uint64_t first_correlation_id = 0;
    uint64_t last_correlation_id = 0;
    uint64_t first_start_ns = 0;
    uint64_t first_end_ns = 0;
    uint64_t last_start_ns = 0;
    uint64_t last_end_ns = 0;
    bool completed = false;
};

struct LifecycleAuditSnapshot {
    bool captured = false;
    uint64_t monotonic_ns = 0;
    uint64_t buffer_requests = 0;
    uint64_t buffer_complete_callbacks = 0;
    uint64_t owned_buffer_completes = 0;
    uint64_t inflight = 0;
    uint64_t parsed_kernel = 0;
    uint64_t parsed_comm = 0;
    uint64_t worker_kernel = 0;
    uint64_t worker_comm = 0;
    uint64_t processed_kernel = 0;
    uint64_t processed_comm = 0;
    uint64_t emitted_comm = 0;
    uint64_t emitted_kernel_raw = 0;
    uint64_t active_calls = 0;
    bool worker_busy = false;
};

struct MalformedTimestampSample {
    uint32_t raw_kind = 0;
    uint32_t activity_kind = 0;
    uint64_t buffer_complete_seq = 0;
    uint64_t record_offset = 0;
    uint64_t valid_size = 0;
    uint64_t start_ns = 0;
    uint64_t end_ns = 0;
    uint64_t correlation_id = 0;
    uint64_t count = 0;
    uint32_t device_id = 0;
    uint32_t stream_id = 0;
};

inline void AtomicMax(std::atomic<uint64_t>* target, uint64_t value) noexcept {
    uint64_t current = target->load(std::memory_order_relaxed);
    while (current < value &&
           !target->compare_exchange_weak(current, value, std::memory_order_relaxed,
                                          std::memory_order_relaxed)) {}
}

struct BufferAuditSnapshot {
    uint64_t buffer_request_count = 0;
    uint64_t buffer_complete_callback_count = 0;
    uint64_t buffer_complete_count = 0;
    uint64_t max_inflight = 0;
    uint64_t parsed_kernel = 0;
    uint64_t parsed_comm = 0;
    uint64_t parsed_unknown = 0;
    uint64_t queue_rejected_kernel = 0;
    uint64_t queue_rejected_comm = 0;
    uint64_t queue_rejected_other = 0;
    uint64_t worker_consumed_kernel = 0;
    uint64_t worker_consumed_comm = 0;
    uint64_t processed_kernel = 0;
    uint64_t processed_comm = 0;
    uint64_t emitted_total = 0;
    uint64_t emitted_comm = 0;
    uint64_t emitted_kseg = 0;
    uint64_t emitted_kseg_raw_count = 0;
    uint64_t valid_while_stopping = 0;
    uint64_t callback_wall_total_ns = 0;
    uint64_t callback_wall_max_ns = 0;
    uint64_t malformed_timestamp_count = 0;
    uint64_t malformed_sample_count = 0;
    uint64_t ledger_entry_count = 0;
    uint64_t ledger_completed_count = 0;
    uint64_t ledger_overflow_count = 0;
    std::array<MalformedTimestampSample, kMalformedTimestampSampleLimit> malformed_samples{};
    std::array<BufferLedgerEntry, kBufferLedgerCapacity> buffer_ledger{};
    std::array<std::array<ContentFingerprintSnapshot, kAuditRawKindCount>,
               kAuditContentStageCount> fingerprints{};
    std::array<LifecycleAuditSnapshot, kAuditLifecyclePointCount> lifecycle_snapshots{};
};

struct BufferAuditConservation {
    bool request_complete_balanced = false;
    bool kernel_parse_queue_balanced = false;
    bool kernel_worker_balanced = false;
    bool kernel_process_balanced = false;
    bool kernel_emit_balanced = false;
    bool comm_parse_queue_balanced = false;
    bool comm_worker_balanced = false;
    bool comm_process_balanced = false;
    bool comm_emit_balanced = false;
    bool unknown_kind_zero = false;
    bool valid_while_stopping_zero = false;
    bool inflight_final_zero = false;
    bool ledger_complete_balanced = false;
    bool ledger_no_overflow = false;
    bool fingerprint_all_ok = false;
    bool lifecycle_snapshots_complete = false;
    bool lifecycle_ordered = false;
    bool before_free_quiescent = false;
    bool all_ok = false;
};

inline bool FingerprintPipelineOk(const BufferAuditSnapshot& value) noexcept {
    for (size_t kind = 0; kind < kAuditRawKindCount; ++kind) {
        for (size_t stage = 1; stage < kAuditContentStageCount; ++stage) {
            if (!SameFingerprint(value.fingerprints[stage - 1][kind],
                                 value.fingerprints[stage][kind])) return false;
        }
    }
    return true;
}

inline BufferAuditConservation CheckBufferAuditConservation(
    const BufferAuditSnapshot& value, uint64_t enqueued_kernel,
    uint64_t enqueued_comm, uint64_t inflight_final) noexcept {
    BufferAuditConservation out;
    out.request_complete_balanced = value.buffer_request_count == value.buffer_complete_count;
    out.kernel_parse_queue_balanced =
        value.parsed_kernel == enqueued_kernel + value.queue_rejected_kernel;
    out.kernel_worker_balanced = enqueued_kernel == value.worker_consumed_kernel;
    out.kernel_process_balanced = value.worker_consumed_kernel == value.processed_kernel;
    out.kernel_emit_balanced = value.processed_kernel == value.emitted_kseg_raw_count;
    out.comm_parse_queue_balanced =
        value.parsed_comm == enqueued_comm + value.queue_rejected_comm;
    out.comm_worker_balanced = enqueued_comm == value.worker_consumed_comm;
    out.comm_process_balanced = value.worker_consumed_comm == value.processed_comm;
    out.comm_emit_balanced = value.processed_comm == value.emitted_comm;
    out.unknown_kind_zero = value.parsed_unknown == 0;
    out.valid_while_stopping_zero = value.valid_while_stopping == 0;
    out.inflight_final_zero = inflight_final == 0;
    out.ledger_complete_balanced = value.ledger_completed_count == value.ledger_entry_count;
    out.ledger_no_overflow = value.ledger_overflow_count == 0;
    out.fingerprint_all_ok = FingerprintPipelineOk(value);
    out.lifecycle_snapshots_complete = true;
    for (const auto& snapshot : value.lifecycle_snapshots) {
        out.lifecycle_snapshots_complete =
            out.lifecycle_snapshots_complete && snapshot.captured;
    }
    const auto& before_disable = value.lifecycle_snapshots[0];
    const auto& after_flush = value.lifecycle_snapshots[1];
    const auto& before_free = value.lifecycle_snapshots[2];
    out.lifecycle_ordered = out.lifecycle_snapshots_complete &&
                            before_disable.monotonic_ns <= after_flush.monotonic_ns &&
                            after_flush.monotonic_ns <= before_free.monotonic_ns;
    out.before_free_quiescent = before_free.captured && before_free.inflight == 0 &&
                                before_free.active_calls == 0 &&
                                !before_free.worker_busy;
    out.all_ok = out.request_complete_balanced && out.kernel_parse_queue_balanced &&
                 out.kernel_worker_balanced && out.kernel_process_balanced &&
                 out.kernel_emit_balanced && out.comm_parse_queue_balanced &&
                 out.comm_worker_balanced && out.comm_process_balanced &&
                 out.comm_emit_balanced && out.unknown_kind_zero &&
                 out.valid_while_stopping_zero && out.inflight_final_zero &&
                 out.ledger_complete_balanced && out.ledger_no_overflow &&
                 out.fingerprint_all_ok && out.lifecycle_snapshots_complete &&
                 out.lifecycle_ordered && out.before_free_quiescent;
    return out;
}

inline std::string BufferAuditPathForOutput(const std::string& output_path) {
    constexpr const char* suffix = ".skeleton.jsonl";
    constexpr size_t suffix_size = 15;
    if (output_path.size() >= suffix_size &&
        output_path.compare(output_path.size() - suffix_size, suffix_size, suffix) == 0) {
        return output_path.substr(0, output_path.size() - suffix_size) +
               ".buffer_audit.json";
    }
    return output_path + ".buffer_audit.json";
}

class BufferAuditCounters {
public:
    void Reset() noexcept {
#define RESET_ATOMIC(name) name.store(0, std::memory_order_relaxed)
        RESET_ATOMIC(buffer_request_count_); RESET_ATOMIC(buffer_complete_callback_count_);
        RESET_ATOMIC(buffer_complete_count_); RESET_ATOMIC(max_inflight_);
        RESET_ATOMIC(parsed_kernel_); RESET_ATOMIC(parsed_comm_); RESET_ATOMIC(parsed_unknown_);
        RESET_ATOMIC(queue_rejected_kernel_); RESET_ATOMIC(queue_rejected_comm_);
        RESET_ATOMIC(queue_rejected_other_); RESET_ATOMIC(worker_consumed_kernel_);
        RESET_ATOMIC(worker_consumed_comm_); RESET_ATOMIC(processed_kernel_);
        RESET_ATOMIC(processed_comm_); RESET_ATOMIC(emitted_total_);
        RESET_ATOMIC(emitted_comm_); RESET_ATOMIC(emitted_kseg_);
        RESET_ATOMIC(emitted_kseg_raw_count_); RESET_ATOMIC(valid_while_stopping_);
        RESET_ATOMIC(callback_wall_total_ns_); RESET_ATOMIC(callback_wall_max_ns_);
        RESET_ATOMIC(malformed_timestamp_count_); RESET_ATOMIC(malformed_sample_claims_);
        RESET_ATOMIC(ledger_completed_count_); RESET_ATOMIC(ledger_overflow_count_);
#undef RESET_ATOMIC
        for (auto& stage : fingerprints_) for (auto& kind : stage) kind.Reset();
        for (auto& entry : buffer_ledger_) entry = BufferLedgerEntry{};
        for (auto& snapshot : lifecycle_snapshots_) snapshot = LifecycleAuditSnapshot{};
    }

    BufferLedgerHandle BeginBufferRequest(const void* pointer, uint64_t reuse_generation,
                                          uint64_t requested_size,
                                          uint64_t request_monotonic_ns,
                                          uint64_t inflight_after) noexcept {
        const uint64_t request_id =
            buffer_request_count_.fetch_add(1, std::memory_order_relaxed) + 1;
        AtomicMax(&max_inflight_, inflight_after);
        BufferLedgerHandle handle{request_id, 0, false};
        if (request_id > kBufferLedgerCapacity) {
            ledger_overflow_count_.fetch_add(1, std::memory_order_relaxed);
            return handle;
        }
        handle.slot = static_cast<uint32_t>(request_id - 1);
        handle.valid = true;
        auto& entry = buffer_ledger_[handle.slot];
        entry = BufferLedgerEntry{};
        entry.request_id = request_id;
        entry.pointer_token = PointerToken(pointer);
        entry.reuse_generation = reuse_generation;
        entry.request_monotonic_ns = request_monotonic_ns;
        entry.requested_size = requested_size;
        return handle;
    }

    void NoteBufferRequest(uint64_t inflight_after) noexcept {
        (void)BeginBufferRequest(nullptr, 0, 0, 0, inflight_after);
    }
    void NoteBufferCompleteCallback() noexcept {
        buffer_complete_callback_count_.fetch_add(1, std::memory_order_relaxed);
    }

    uint64_t NoteOwnedBufferComplete(const BufferLedgerHandle& handle,
                                     uint64_t completion_size, uint64_t valid_size,
                                     uint64_t complete_monotonic_ns) noexcept {
        const uint64_t sequence =
            buffer_complete_count_.fetch_add(1, std::memory_order_relaxed) + 1;
        if (Valid(handle)) {
            auto& entry = buffer_ledger_[handle.slot];
            entry.completion_size = completion_size;
            entry.valid_size = valid_size;
            entry.complete_monotonic_ns = complete_monotonic_ns;
            entry.completed = true;
            ledger_completed_count_.fetch_add(1, std::memory_order_relaxed);
        }
        return sequence;
    }
    uint64_t NoteOwnedBufferComplete() noexcept {
        return NoteOwnedBufferComplete(BufferLedgerHandle{}, 0, 0, 0);
    }

    void NoteLedgerRecord(const BufferLedgerHandle& handle, AuditRawKind kind,
                          uint64_t correlation_id, uint64_t start_ns,
                          uint64_t end_ns) noexcept {
        if (!Valid(handle)) return;
        auto& entry = buffer_ledger_[handle.slot];
        const uint64_t before = entry.kernel_records + entry.comm_records + entry.unknown_records;
        if (kind == AuditRawKind::kCommunication) ++entry.comm_records;
        else ++entry.kernel_records;
        const uint32_t raw_kind = static_cast<uint32_t>(kind);
        if (before == 0) {
            entry.first_kind = raw_kind;
            entry.first_correlation_id = correlation_id;
            entry.first_start_ns = start_ns;
            entry.first_end_ns = end_ns;
        }
        entry.last_kind = raw_kind;
        entry.last_correlation_id = correlation_id;
        entry.last_start_ns = start_ns;
        entry.last_end_ns = end_ns;
    }
    void NoteLedgerUnknown(const BufferLedgerHandle& handle) noexcept {
        if (Valid(handle)) ++buffer_ledger_[handle.slot].unknown_records;
    }
    void NoteLedgerTerminal(const BufferLedgerHandle& handle, int32_t code,
                            uint64_t calls) noexcept {
        if (Valid(handle)) {
            buffer_ledger_[handle.slot].get_next_terminal_code = code;
            buffer_ledger_[handle.slot].get_next_calls = calls;
        }
    }

    void NoteParsed(AuditRawKind kind) noexcept { Counter(kind, parsed_kernel_, parsed_comm_); }
    void NoteParsedUnknown() noexcept {
        parsed_unknown_.fetch_add(1, std::memory_order_relaxed);
    }
    void NoteQueueRejected(AuditRawKind kind) noexcept {
        Counter(kind, queue_rejected_kernel_, queue_rejected_comm_);
    }
    void NoteQueueRejectedOther() noexcept {
        queue_rejected_other_.fetch_add(1, std::memory_order_relaxed);
    }
    void NoteWorkerConsumed(AuditRawKind kind) noexcept {
        Counter(kind, worker_consumed_kernel_, worker_consumed_comm_);
    }
    void NoteProcessed(AuditRawKind kind) noexcept {
        Counter(kind, processed_kernel_, processed_comm_);
    }

    void NoteContent(AuditContentStage stage, AuditRawKind kind, uint64_t hash) noexcept {
        fingerprints_[static_cast<size_t>(stage)][AuditKindIndex(kind)].Add(hash);
    }
    void MergeContent(AuditContentStage stage, AuditRawKind kind,
                      const ContentFingerprintSnapshot& source) noexcept {
        fingerprints_[static_cast<size_t>(stage)][AuditKindIndex(kind)].Merge(source);
    }
    void NoteEmittedTotal() noexcept {
        emitted_total_.fetch_add(1, std::memory_order_relaxed);
    }
    void NoteEmittedComm() noexcept {
        emitted_comm_.fetch_add(1, std::memory_order_relaxed);
    }
    void NoteEmittedKseg(uint64_t count) noexcept {
        emitted_kseg_.fetch_add(1, std::memory_order_relaxed);
        emitted_kseg_raw_count_.fetch_add(count, std::memory_order_relaxed);
    }
    void NoteValidWhileStopping() noexcept {
        valid_while_stopping_.fetch_add(1, std::memory_order_relaxed);
    }
    void NoteCallbackWall(const BufferLedgerHandle& handle, uint64_t duration_ns) noexcept {
        callback_wall_total_ns_.fetch_add(duration_ns, std::memory_order_relaxed);
        AtomicMax(&callback_wall_max_ns_, duration_ns);
        if (Valid(handle)) buffer_ledger_[handle.slot].callback_wall_ns = duration_ns;
    }
    void NoteCallbackWall(uint64_t duration_ns) noexcept {
        NoteCallbackWall(BufferLedgerHandle{}, duration_ns);
    }

    void NoteLifecycleSnapshot(AuditLifecyclePoint point, uint64_t monotonic_ns,
                               uint64_t inflight, uint64_t active_calls,
                               bool worker_busy) noexcept {
        auto& out = lifecycle_snapshots_[static_cast<size_t>(point)];
        out.captured = true; out.monotonic_ns = monotonic_ns;
        out.buffer_requests = buffer_request_count_.load(std::memory_order_relaxed);
        out.buffer_complete_callbacks =
            buffer_complete_callback_count_.load(std::memory_order_relaxed);
        out.owned_buffer_completes =
            buffer_complete_count_.load(std::memory_order_relaxed);
        out.inflight = inflight;
        out.parsed_kernel = parsed_kernel_.load(std::memory_order_relaxed);
        out.parsed_comm = parsed_comm_.load(std::memory_order_relaxed);
        out.worker_kernel = worker_consumed_kernel_.load(std::memory_order_relaxed);
        out.worker_comm = worker_consumed_comm_.load(std::memory_order_relaxed);
        out.processed_kernel = processed_kernel_.load(std::memory_order_relaxed);
        out.processed_comm = processed_comm_.load(std::memory_order_relaxed);
        out.emitted_comm = emitted_comm_.load(std::memory_order_relaxed);
        out.emitted_kernel_raw =
            emitted_kseg_raw_count_.load(std::memory_order_relaxed);
        out.active_calls = active_calls; out.worker_busy = worker_busy;
    }

    void NoteMalformedTimestamp(const MalformedTimestampSample& sample) noexcept {
        malformed_timestamp_count_.fetch_add(1, std::memory_order_relaxed);
        const uint64_t slot =
            malformed_sample_claims_.fetch_add(1, std::memory_order_relaxed);
        if (slot < malformed_samples_.size()) malformed_samples_[slot] = sample;
    }

    BufferAuditSnapshot Snapshot() const noexcept {
        BufferAuditSnapshot out;
#define LOAD_FIELD(field, atomic_name) out.field = atomic_name.load(std::memory_order_relaxed)
        LOAD_FIELD(buffer_request_count, buffer_request_count_);
        LOAD_FIELD(buffer_complete_callback_count, buffer_complete_callback_count_);
        LOAD_FIELD(buffer_complete_count, buffer_complete_count_); LOAD_FIELD(max_inflight, max_inflight_);
        LOAD_FIELD(parsed_kernel, parsed_kernel_); LOAD_FIELD(parsed_comm, parsed_comm_);
        LOAD_FIELD(parsed_unknown, parsed_unknown_); LOAD_FIELD(queue_rejected_kernel, queue_rejected_kernel_);
        LOAD_FIELD(queue_rejected_comm, queue_rejected_comm_); LOAD_FIELD(queue_rejected_other, queue_rejected_other_);
        LOAD_FIELD(worker_consumed_kernel, worker_consumed_kernel_);
        LOAD_FIELD(worker_consumed_comm, worker_consumed_comm_); LOAD_FIELD(processed_kernel, processed_kernel_);
        LOAD_FIELD(processed_comm, processed_comm_); LOAD_FIELD(emitted_total, emitted_total_);
        LOAD_FIELD(emitted_comm, emitted_comm_); LOAD_FIELD(emitted_kseg, emitted_kseg_);
        LOAD_FIELD(emitted_kseg_raw_count, emitted_kseg_raw_count_);
        LOAD_FIELD(valid_while_stopping, valid_while_stopping_);
        LOAD_FIELD(callback_wall_total_ns, callback_wall_total_ns_);
        LOAD_FIELD(callback_wall_max_ns, callback_wall_max_ns_);
        LOAD_FIELD(malformed_timestamp_count, malformed_timestamp_count_);
        LOAD_FIELD(ledger_completed_count, ledger_completed_count_);
        LOAD_FIELD(ledger_overflow_count, ledger_overflow_count_);
#undef LOAD_FIELD
        const uint64_t claims =
            malformed_sample_claims_.load(std::memory_order_relaxed);
        out.malformed_sample_count = claims < malformed_samples_.size() ? claims : malformed_samples_.size();
        for (size_t i = 0; i < out.malformed_sample_count; ++i) out.malformed_samples[i] = malformed_samples_[i];
        out.ledger_entry_count = out.buffer_request_count < kBufferLedgerCapacity
                                     ? out.buffer_request_count : kBufferLedgerCapacity;
        for (size_t i = 0; i < out.ledger_entry_count; ++i) out.buffer_ledger[i] = buffer_ledger_[i];
        for (size_t s = 0; s < kAuditContentStageCount; ++s)
            for (size_t k = 0; k < kAuditRawKindCount; ++k)
                out.fingerprints[s][k] = fingerprints_[s][k].Snapshot();
        out.lifecycle_snapshots = lifecycle_snapshots_;
        return out;
    }

private:
    struct FingerprintAtomic {
        std::atomic<uint64_t> count{0}, xor64{0}, sum64{0};
        void Reset() noexcept {
            count.store(0, std::memory_order_relaxed);
            xor64.store(0, std::memory_order_relaxed);
            sum64.store(0, std::memory_order_relaxed);
        }
        void Add(uint64_t hash) noexcept {
            count.fetch_add(1, std::memory_order_relaxed);
            xor64.fetch_xor(hash, std::memory_order_relaxed);
            sum64.fetch_add(hash, std::memory_order_relaxed);
        }
        void Merge(const ContentFingerprintSnapshot& value) noexcept {
            count.fetch_add(value.count, std::memory_order_relaxed);
            xor64.fetch_xor(value.xor64, std::memory_order_relaxed);
            sum64.fetch_add(value.sum64, std::memory_order_relaxed);
        }
        ContentFingerprintSnapshot Snapshot() const noexcept {
            return {count.load(std::memory_order_relaxed),
                    xor64.load(std::memory_order_relaxed),
                    sum64.load(std::memory_order_relaxed)};
        }
    };
    static bool Valid(const BufferLedgerHandle& h) noexcept {
        return h.valid && h.slot < kBufferLedgerCapacity;
    }
    static void Counter(AuditRawKind kind, std::atomic<uint64_t>& kernel,
                        std::atomic<uint64_t>& comm) noexcept {
        (kind == AuditRawKind::kCommunication ? comm : kernel)
            .fetch_add(1, std::memory_order_relaxed);
    }

    std::atomic<uint64_t> buffer_request_count_{0}, buffer_complete_callback_count_{0};
    std::atomic<uint64_t> buffer_complete_count_{0}, max_inflight_{0};
    std::atomic<uint64_t> parsed_kernel_{0}, parsed_comm_{0}, parsed_unknown_{0};
    std::atomic<uint64_t> queue_rejected_kernel_{0}, queue_rejected_comm_{0}, queue_rejected_other_{0};
    std::atomic<uint64_t> worker_consumed_kernel_{0}, worker_consumed_comm_{0};
    std::atomic<uint64_t> processed_kernel_{0}, processed_comm_{0};
    std::atomic<uint64_t> emitted_total_{0}, emitted_comm_{0}, emitted_kseg_{0};
    std::atomic<uint64_t> emitted_kseg_raw_count_{0}, valid_while_stopping_{0};
    std::atomic<uint64_t> callback_wall_total_ns_{0}, callback_wall_max_ns_{0};
    std::atomic<uint64_t> malformed_timestamp_count_{0}, malformed_sample_claims_{0};
    std::atomic<uint64_t> ledger_completed_count_{0}, ledger_overflow_count_{0};
    std::array<MalformedTimestampSample, kMalformedTimestampSampleLimit> malformed_samples_{};
    std::array<BufferLedgerEntry, kBufferLedgerCapacity> buffer_ledger_{};
    std::array<std::array<FingerprintAtomic, kAuditRawKindCount>, kAuditContentStageCount> fingerprints_{};
    std::array<LifecycleAuditSnapshot, kAuditLifecyclePointCount> lifecycle_snapshots_{};
};

}  // namespace mspti_skeleton
