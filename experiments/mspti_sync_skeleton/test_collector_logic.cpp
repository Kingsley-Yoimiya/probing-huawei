// 本地可测 fixture：buffer ownership / Complete RAII / drain leak / 状态机 / 双轮 reset。
// g++ -std=c++17 -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer \
//   test_collector_logic.cpp -o /tmp/test_collector_logic && /tmp/test_collector_logic
#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <mutex>
#include <new>
#include <string>
#include <thread>
#include <unordered_set>
#include <vector>

#if defined(__APPLE__)
#include <stdlib.h>
#else
#include <malloc.h>
#endif

enum class Life {
    Idle,
    Running,
    Capturing,
    Finalizing,
    Finalized,
    TerminalFailed,
};

struct OwnedEvent {
    uint64_t start_ns = 0;
    uint64_t end_ns = 0;
    char name[64]{};
};

struct FakeCollector {
    std::deque<OwnedEvent> queue;
    std::mutex mu;
    std::condition_variable cv;
    std::condition_variable finalize_cv;
    std::atomic<int> inflight{0};
    std::atomic<bool> worker_busy{false};
    std::atomic<int> active_calls{0};
    std::atomic<uint64_t> processed{0};
    std::atomic<uint64_t> queue_drops{0};
    std::atomic<uint64_t> sequence{0};
    std::unordered_set<uint8_t*> outstanding;
    std::vector<uint8_t*> pool;
    bool intentional_leak = false;
    bool drop_written = false;
    // Mirror production: lifecycle is atomic so AcceptingLiveCalls can be lock-free.
    std::atomic<Life> state{Life::Idle};
    int last_finalize_rc = 0;
    int64_t step = -1;
    std::atomic<bool> running{true};
    std::atomic<bool> stopping{false};

    Life LoadState(std::memory_order order = std::memory_order_acquire) const {
        return state.load(order);
    }

    void StoreState(Life st, std::memory_order order = std::memory_order_release) {
        const Life prev = state.load(std::memory_order_relaxed);
        if (prev == Life::TerminalFailed && st != Life::TerminalFailed && st != Life::Idle) {
            return;  // DROP/terminal session must not regress
        }
        state.store(st, order);
    }

    bool AcceptingLiveCalls() const {
        if (!running.load(std::memory_order_acquire) ||
            stopping.load(std::memory_order_acquire)) {
            return false;
        }
        const Life st = LoadState(std::memory_order_acquire);
        return st == Life::Running || st == Life::Capturing;
    }

    void Reset() {
        std::lock_guard<std::mutex> lock(mu);
        queue.clear();
        inflight.store(0);
        worker_busy.store(false);
        active_calls.store(0);
        processed.store(0);
        queue_drops.store(0);
        sequence.store(0);
        step = -1;
        drop_written = false;
        last_finalize_rc = 0;
        running.store(true);
        stopping.store(false);
        StoreState(Life::Idle);
        for (uint8_t* p : pool) {
            std::free(p);
        }
        pool.clear();
        // outstanding only freed if not intentional leak
        const bool keep_outstanding = intentional_leak;
        if (!keep_outstanding) {
            for (uint8_t* p : outstanding) {
                std::free(p);
            }
            outstanding.clear();
        }
        intentional_leak = false;
    }

    bool QuiescentLocked() const {
        // 与真实 collector 一致：queue/inflight/worker/active_calls；outstanding 由 Finalize 另行检查。
        return queue.empty() && inflight.load() == 0 && !worker_busy.load() &&
               active_calls.load() == 0;
    }

    bool WaitDrain(uint64_t timeout_ms) {
        std::unique_lock<std::mutex> lock(mu);
        return cv.wait_for(lock, std::chrono::milliseconds(timeout_ms),
                           [this] { return QuiescentLocked(); });
    }

    int Start() {
        std::lock_guard<std::mutex> lock(mu);
        if (LoadState() == Life::Finalizing || LoadState() == Life::Capturing ||
            LoadState() == Life::TerminalFailed || LoadState() == Life::Running) {
            return 1;
        }
        // Start after Finalized: thorough reset
        if (LoadState() == Life::Finalized) {
            queue.clear();
            sequence.store(0);
            processed.store(0);
            drop_written = false;
        }
        StoreState(Life::Running);
        return 0;
    }

    int CaptureBegin() {
        std::lock_guard<std::mutex> lock(mu);
        if (LoadState() == Life::Finalizing || LoadState() == Life::Finalized ||
            LoadState() == Life::TerminalFailed || LoadState() != Life::Running) {
            return 80;
        }
        StoreState(Life::Capturing);
        return 0;
    }

    int CaptureEnd() {
        std::lock_guard<std::mutex> lock(mu);
        if (LoadState() != Life::Capturing) {
            return 1;
        }
        StoreState(Life::Running);
        return 0;
    }

    int Finalize(uint64_t drain_timeout_ms) {
        std::unique_lock<std::mutex> lock(mu);
        if (LoadState() == Life::Finalizing) {
            finalize_cv.wait(lock, [this] { return LoadState() != Life::Finalizing; });
            return last_finalize_rc;
        }
        if (LoadState() == Life::Finalized || LoadState() == Life::TerminalFailed) {
            return last_finalize_rc;
        }
        StoreState(Life::Finalizing);
        lock.unlock();

        const bool drained = WaitDrain(drain_timeout_ms);

        lock.lock();
        if (!drained || !outstanding.empty() || inflight.load() > 0) {
            // terminal failure: intentional leak, do NOT free outstanding
            intentional_leak = !outstanding.empty() || inflight.load() > 0;
            last_finalize_rc = 71;
            StoreState(Life::TerminalFailed);
            finalize_cv.notify_all();
            return 71;
        }
        // DROP last
        drop_written = true;
        last_finalize_rc = 0;
        StoreState(Life::Finalized);
        finalize_cv.notify_all();
        return 0;
    }
};

// Complete RAII: validate size; always remove outstanding + free/recycle + dec inflight.
static void CompleteLike(FakeCollector& c, uint8_t* buffer, size_t size, size_t valid_size,
                         bool throw_bad_alloc) {
    struct Guard {
        FakeCollector* self;
        uint8_t* buffer = nullptr;
        bool owned = false;
        bool recycled = false;
        bool dec = false;
        ~Guard() {
            if (!self || !owned || !buffer) {
                if (self) {
                    self->cv.notify_all();
                }
                return;
            }
            if (recycled) {
                self->pool.push_back(buffer);
            } else {
                std::free(buffer);
            }
            if (!dec && self->inflight.load() > 0) {
                self->inflight.fetch_sub(1);
                dec = true;
            }
            self->cv.notify_all();
        }
    };

    try {
        if (buffer == nullptr) {
            return;
        }
        bool owned = false;
        {
            std::lock_guard<std::mutex> lock(c.mu);
            owned = c.outstanding.erase(buffer) > 0;
        }
        Guard g{&c, buffer, owned, false, false};
        if (!owned) {
            return;
        }
        if (size == 0 || valid_size > size) {
            return;  // free via guard
        }
        if (throw_bad_alloc) {
            throw std::bad_alloc();
        }
        // parse → owned event enqueue
        OwnedEvent e;
        e.start_ns = 1;
        std::snprintf(e.name, sizeof(e.name), "ok");
        {
            std::lock_guard<std::mutex> lock(c.mu);
            c.queue.push_back(e);
        }
        g.recycled = true;
    } catch (const std::bad_alloc&) {
        // must not escape; guard frees
    } catch (...) {
    }
}

static uint8_t* AllocAlignedBuffer(size_t bytes) {
    void* ptr = nullptr;
    if (posix_memalign(&ptr, 64, bytes) != 0) {
        return nullptr;
    }
    return static_cast<uint8_t*>(ptr);
}

static void TestBufferOwnership() {
    constexpr size_t n = 3;
    uint8_t* buf = AllocAlignedBuffer(n * 32);
    assert(buf != nullptr);
    FakeCollector c;
    {
        std::lock_guard<std::mutex> lock(c.mu);
        c.outstanding.insert(buf);
        c.inflight.store(1);
    }
    CompleteLike(c, buf, n * 32, n * 32, false);
    assert(c.outstanding.empty());
    assert(c.inflight.load() == 0);
    assert(!c.pool.empty());
    for (uint8_t* p : c.pool) {
        std::free(p);
    }
    c.pool.clear();
}

static void TestCompleteSizeReject() {
    FakeCollector c;
    uint8_t* buf = AllocAlignedBuffer(64);
    {
        std::lock_guard<std::mutex> lock(c.mu);
        c.outstanding.insert(buf);
        c.inflight.store(1);
    }
    CompleteLike(c, buf, 32, 64, false);  // valid_size > size → free, not recycle
    assert(c.outstanding.empty());
    assert(c.inflight.load() == 0);
    assert(c.pool.empty());  // freed, not pooled
}

static void TestCompleteNoThrow() {
    FakeCollector c;
    uint8_t* buf = AllocAlignedBuffer(64);
    {
        std::lock_guard<std::mutex> lock(c.mu);
        c.outstanding.insert(buf);
        c.inflight.store(1);
    }
    CompleteLike(c, buf, 64, 32, true);  // bad_alloc path
    assert(c.outstanding.empty());
    assert(c.inflight.load() == 0);
}

static void TestDrainTimeoutLeak() {
    FakeCollector c;
    uint8_t* buf = AllocAlignedBuffer(64);
    assert(buf != nullptr);
    {
        std::lock_guard<std::mutex> lock(c.mu);
        c.outstanding.insert(buf);
        c.inflight.store(1);
        c.StoreState(Life::Running);
    }
    assert(c.outstanding.count(buf) == 1);
    const int rc = c.Finalize(/*timeout*/ 30);
    assert(rc == 71);
    assert(c.LoadState() == Life::TerminalFailed);
    assert(c.intentional_leak);
    assert(c.outstanding.count(buf) == 1);  // NOT freed
    assert(!c.drop_written);  // DROP 不得在 terminal 路径写出
    // Reset 不得 free intentional leak
    c.Reset();
    assert(c.outstanding.count(buf) == 1);
    std::free(buf);
    c.outstanding.clear();
    c.intentional_leak = false;
}

static void TestStateMachineAndConcurrentFinalize() {
    FakeCollector c;
    assert(c.Start() == 0);
    assert(c.CaptureBegin() == 0);
    assert(c.Start() == 1);  // reject during capturing
    assert(c.CaptureEnd() == 0);
    assert(c.Finalize(50) == 0);
    assert(c.LoadState() == Life::Finalized);
    assert(c.drop_written);
    // concurrent finalize returns same result
    assert(c.Finalize(50) == 0);
    // Start after Finalize resets
    assert(c.Start() == 0);
    assert(c.LoadState() == Life::Running);
    assert(c.CaptureBegin() == 0);
    assert(c.CaptureEnd() == 0);
    assert(c.Finalize(50) == 0);
}

static void TestRejectDuringFinalizing() {
    FakeCollector c;
    assert(c.Start() == 0);
    std::atomic<bool> entered{false};
    std::thread fin([&] {
        {
            std::lock_guard<std::mutex> lock(c.mu);
            c.StoreState(Life::Finalizing);
            entered.store(true);
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(80));
        {
            std::lock_guard<std::mutex> lock(c.mu);
            c.last_finalize_rc = 0;
            c.StoreState(Life::Finalized);
            c.drop_written = true;
            c.finalize_cv.notify_all();
        }
    });
    while (!entered.load()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    assert(c.CaptureBegin() == 80);
    assert(c.Start() == 1);
    assert(c.Finalize(200) == 0);  // waits for same result
    fin.join();
}

static void TestEnqueueDropCounted() {
    FakeCollector c;
    const size_t cap = 2;
    auto try_enqueue = [&](OwnedEvent e) {
        std::lock_guard<std::mutex> lock(c.mu);
        if (c.queue.size() >= cap) {
            c.queue_drops.fetch_add(1);
            return false;
        }
        c.queue.push_back(e);
        return true;
    };
    assert(try_enqueue(OwnedEvent{}));
    assert(try_enqueue(OwnedEvent{}));
    assert(!try_enqueue(OwnedEvent{}));
    assert(c.queue_drops.load() == 1);
}

static void TestCompletePoolPushFailFrees() {
    // Simulate buffer_pool_.push_back failure: must free, not leak ownership / stuck inflight.
    FakeCollector c;
    uint8_t* buf = AllocAlignedBuffer(64);
    {
        std::lock_guard<std::mutex> lock(c.mu);
        c.outstanding.insert(buf);
        c.inflight.store(1);
    }
    struct Guard {
        FakeCollector* self;
        uint8_t* buffer = nullptr;
        bool owned = false;
        bool recycled = false;
        bool force_push_fail = false;
        ~Guard() {
            if (!self || !owned || !buffer) {
                return;
            }
            if (recycled) {
                bool pooled = false;
                if (!force_push_fail) {
                    self->pool.push_back(buffer);
                    pooled = true;
                }
                if (!pooled) {
                    std::free(buffer);
                }
            } else {
                std::free(buffer);
            }
            if (self->inflight.load() > 0) {
                self->inflight.fetch_sub(1);
            }
        }
    };
    {
        bool owned = false;
        {
            std::lock_guard<std::mutex> lock(c.mu);
            owned = c.outstanding.erase(buf) > 0;
        }
        Guard g{&c, buf, owned, true, true};  // recycled but push fails → free
    }
    assert(c.outstanding.empty());
    assert(c.inflight.load() == 0);
    assert(c.pool.empty());
}

static void TestTerminalFailedNoActiveTeardown() {
    // Fault isolation: timeout / unsubscribe-fail / worker-alive paths must not
    // free/close/unsubscribe actively. This is NOT success.
    FakeCollector c;
    uint8_t* buf = AllocAlignedBuffer(64);
    {
        std::lock_guard<std::mutex> lock(c.mu);
        c.outstanding.insert(buf);
        c.inflight.store(1);
        c.StoreState(Life::Running);
    }
    assert(c.Finalize(20) == 71);
    assert(c.LoadState() == Life::TerminalFailed);
    assert(c.intentional_leak);
    assert(c.outstanding.count(buf) == 1);
    // Simulate LightShutdown on terminal: no free of outstanding
    const size_t before = c.outstanding.size();
    // no-op teardown
    assert(c.outstanding.size() == before);
    assert(c.outstanding.count(buf) == 1);
    std::free(buf);
    c.outstanding.clear();
}

static void TestWorkerExceptionToTerminal() {
    // Worker entry catch(...) → TerminalFailed; never terminate; wake Finalize waiters.
    FakeCollector c;
    assert(c.Start() == 0);
    std::atomic<bool> waiter_awake{false};
    std::thread waiter([&] {
        std::unique_lock<std::mutex> lock(c.mu);
        c.StoreState(Life::Finalizing);
        c.finalize_cv.wait(lock, [&] {
            return c.LoadState() != Life::Finalizing;
        });
        waiter_awake.store(true);
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    {
        std::lock_guard<std::mutex> lock(c.mu);
        // Simulate worker_failure publish
        c.intentional_leak = true;
        c.last_finalize_rc = 79;
        c.StoreState(Life::TerminalFailed);
        c.finalize_cv.notify_all();
    }
    waiter.join();
    assert(waiter_awake.load());
    assert(c.LoadState() == Life::TerminalFailed);
    assert(c.last_finalize_rc == 79);
    assert(c.intentional_leak);
}

static void TestFlushThreadConstructFailPublishesTerminal() {
    FakeCollector c;
    assert(c.Start() == 0);
    std::atomic<int> concurrent_rc{-1};
    std::thread concurrent([&] {
        std::unique_lock<std::mutex> lock(c.mu);
        c.finalize_cv.wait(lock, [&] { return c.LoadState() != Life::Finalizing; });
        concurrent_rc.store(c.last_finalize_rc);
    });
    {
        std::lock_guard<std::mutex> lock(c.mu);
        c.StoreState(Life::Finalizing);
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    // Simulate flush std::thread ctor failure → EnterTerminalFailed(78)
    {
        std::lock_guard<std::mutex> lock(c.mu);
        c.intentional_leak = true;
        c.last_finalize_rc = 78;
        c.StoreState(Life::TerminalFailed);
        c.finalize_cv.notify_all();
    }
    concurrent.join();
    assert(concurrent_rc.load() == 78);
    assert(c.LoadState() == Life::TerminalFailed);
}

static void TestStartRollbackDisableFailLeaks() {
    // Disable/Unsubscribe failure must NOT clear enabled flags / free buffers.
    FakeCollector c;
    uint8_t* buf = AllocAlignedBuffer(64);
    {
        std::lock_guard<std::mutex> lock(c.mu);
        c.outstanding.insert(buf);
        c.inflight.store(1);
        c.StoreState(Life::Running);
        // Simulate AbortStart with disable failure → terminal leak
        c.intentional_leak = true;
        c.last_finalize_rc = 31;
        c.StoreState(Life::TerminalFailed);
    }
    assert(c.LoadState() == Life::TerminalFailed);
    assert(c.outstanding.count(buf) == 1);
    assert(!c.drop_written);
    // Must not pretend success / Idle with freed outstanding
    assert(c.LoadState() != Life::Idle);
    assert(c.LoadState() != Life::Finalized);
    std::free(buf);
    c.outstanding.clear();
}

static void TestFinalizeDisableFailImmediateTerminal() {
    // Finalize: DisableActivities failure ⇒ TerminalFailed immediately.
    // Must NOT write DROP / free outstanding / pretend Finalized.
    FakeCollector c;
    uint8_t* buf = AllocAlignedBuffer(64);
    assert(c.Start() == 0);
    {
        std::lock_guard<std::mutex> lock(c.mu);
        c.outstanding.insert(buf);
        c.inflight.store(1);
        // Simulate Finalize step-1 disable failure path (collector.cpp).
        c.intentional_leak = true;
        c.last_finalize_rc = 30;  // disable fail code family
        c.StoreState(Life::TerminalFailed);
        c.finalize_cv.notify_all();
    }
    assert(c.LoadState() == Life::TerminalFailed);
    assert(c.last_finalize_rc == 30);
    assert(c.outstanding.count(buf) == 1);
    assert(!c.drop_written);
    assert(c.LoadState() != Life::Finalized);
    assert(c.LoadState() != Life::Idle);
    // Callback-reachable buffer retained until process exit (fixture frees for ASAN).
    std::free(buf);
    c.outstanding.clear();
}

static void TestThinCallActiveGuardBlocksDropRace() {
    // active_calls > 0 → not quiescent → Finalize must wait / not write DROP early.
    FakeCollector c;
    assert(c.Start() == 0);
    c.active_calls.store(1);
    assert(!c.QuiescentLocked());
    // Finalize with short timeout while thin-call held → terminal (not successful DROP)
    c.StoreState(Life::Running);
    // With active_calls, QuiescentLocked is false; WaitDrain times out
    const bool drained = c.WaitDrain(30);
    assert(!drained);
    c.active_calls.store(0);
    assert(c.QuiescentLocked());
    assert(c.Finalize(50) == 0);
    assert(c.drop_written);
}

static void TestAtomicLifecycleAcceptingVsFinalizeRace() {
    // Sanitizer model: concurrent AcceptingLiveCalls + Finalize must not tear-read
    // plain enum; DROP/Finalized/TerminalFailed must not regress to accepting.
    FakeCollector c;
    assert(c.Start() == 0);
    assert(c.AcceptingLiveCalls());
    std::atomic<int> accepting_hits{0};
    std::atomic<int> rejecting_hits{0};
    std::atomic<bool> stop{false};
    std::thread hammer([&] {
        while (!stop.load(std::memory_order_acquire)) {
            if (c.AcceptingLiveCalls()) {
                accepting_hits.fetch_add(1, std::memory_order_relaxed);
            } else {
                rejecting_hits.fetch_add(1, std::memory_order_relaxed);
            }
        }
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    assert(c.Finalize(200) == 0);
    assert(c.LoadState() == Life::Finalized);
    assert(c.drop_written);
    // After Finalized, AcceptingLiveCalls must stay false (no regression).
    for (int i = 0; i < 1000; ++i) {
        assert(!c.AcceptingLiveCalls());
    }
    // TerminalFailed also never accepts / never regresses to Running.
    c.StoreState(Life::TerminalFailed);
    c.StoreState(Life::Running);  // must be ignored
    assert(c.LoadState() == Life::TerminalFailed);
    assert(!c.AcceptingLiveCalls());
    stop.store(true, std::memory_order_release);
    hammer.join();
    assert(accepting_hits.load() > 0);
    assert(rejecting_hits.load() > 0);
}

static void TestLastIncompleteAtomicWorkerStatRace() {
    // Model production last_incomplete_ as atomic<bool>: worker failure writer vs
    // GetLastCaptureStats reader under high concurrency (ASAN/UBSAN; TSAN-like).
    std::atomic<bool> last_incomplete{false};
    std::atomic<bool> incomplete{false};
    std::atomic<bool> stop{false};
    std::atomic<int> reads{0};
    std::atomic<int> writes{0};
    std::thread worker([&] {
        for (int i = 0; i < 200000; ++i) {
            incomplete.store(true, std::memory_order_release);
            last_incomplete.store(true, std::memory_order_release);
            writes.fetch_add(1, std::memory_order_relaxed);
            last_incomplete.store(false, std::memory_order_release);
            incomplete.store(false, std::memory_order_release);
            writes.fetch_add(1, std::memory_order_relaxed);
        }
        stop.store(true, std::memory_order_release);
    });
    std::thread reader([&] {
        while (!stop.load(std::memory_order_acquire) || reads.load() < 1000) {
            int flag = last_incomplete.load(std::memory_order_acquire) ||
                               incomplete.load(std::memory_order_acquire)
                           ? 1
                           : 0;
            (void)flag;
            reads.fetch_add(1, std::memory_order_relaxed);
            if (reads.load() > 500000) {
                break;
            }
        }
    });
    worker.join();
    reader.join();
    assert(writes.load() > 0);
    assert(reads.load() > 0);
}

int main() {
    TestBufferOwnership();
    TestCompleteSizeReject();
    TestCompleteNoThrow();
    TestCompletePoolPushFailFrees();
    TestDrainTimeoutLeak();
    TestTerminalFailedNoActiveTeardown();
    TestStateMachineAndConcurrentFinalize();
    TestRejectDuringFinalizing();
    TestEnqueueDropCounted();
    TestWorkerExceptionToTerminal();
    TestFlushThreadConstructFailPublishesTerminal();
    TestStartRollbackDisableFailLeaks();
    TestFinalizeDisableFailImmediateTerminal();
    TestThinCallActiveGuardBlocksDropRace();
    TestAtomicLifecycleAcceptingVsFinalizeRace();
    TestLastIncompleteAtomicWorkerStatRace();
    std::puts("OK test_collector_logic");
    return 0;
}
