#include "../event_trace_format.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <filesystem>
#include <fstream>
#include <pthread.h>
#include <string>
#include <sys/wait.h>
#include <unistd.h>

extern "C" {
int acl_event_trace_finalize(void);
void acl_event_delay_arm(void);
void acl_event_delay_disarm(void);
using aclError = int32_t;
using aclrtEvent = void*;
using aclrtStream = void*;
aclError aclrtCreateEvent(aclrtEvent*);
aclError aclrtRecordEvent(aclrtEvent, aclrtStream);
aclError aclrtStreamWaitEvent(aclrtStream, aclrtEvent);
}

using FakeResetFn = void (*)();
using FakeHostfuncCallsFn = int (*)();
using FakeSetFailHostfuncFn = void (*)(int);
using FakeSetFailRecordFn = void (*)(int);

FakeResetFn FakeReset() {
    return reinterpret_cast<FakeResetFn>(dlsym(RTLD_DEFAULT, "fake_acl_reset"));
}
FakeHostfuncCallsFn FakeHostfuncCalls() {
    return reinterpret_cast<FakeHostfuncCallsFn>(dlsym(RTLD_DEFAULT, "fake_acl_hostfunc_calls"));
}
FakeSetFailHostfuncFn FakeSetFailHostfunc() {
    return reinterpret_cast<FakeSetFailHostfuncFn>(dlsym(RTLD_DEFAULT, "fake_acl_set_fail_hostfunc"));
}
FakeSetFailRecordFn FakeSetFailRecord() {
    return reinterpret_cast<FakeSetFailRecordFn>(dlsym(RTLD_DEFAULT, "fake_acl_set_fail_record"));
}

struct DelayAudit {
    int match_count{0};
    int inject_failed{0};
    uint32_t callback_count{0};
    uint32_t arm_tid{0};
    uint32_t record_issuing_tid{0};
    uint64_t last_inject_preload_cs{0};
    int active_record_success_ord{0};
};

using FakeRecordCallsBeforeHostfuncFn = int (*)();

FakeRecordCallsBeforeHostfuncFn FakeRecordCallsBeforeHostfunc() {
    return reinterpret_cast<FakeRecordCallsBeforeHostfuncFn>(
        dlsym(RTLD_DEFAULT, "fake_acl_record_calls_before_hostfunc"));
}

// Ordinal 4 = 5th successful Record on latched tid (0-based counter at inject).
constexpr int kInjectOrdinal = 4;
constexpr int kInjectSuccessRecordIndex = kInjectOrdinal + 1;
// SuccessRecords(1) x6: each Create+Record → inject cs = 2 * index.
constexpr uint64_t kExpectedInjectPreloadCs = static_cast<uint64_t>(2 * kInjectSuccessRecordIndex);
// SuccessRecords(6) x1: one Create then six Records → inject cs = 1 + index.
constexpr uint64_t kExpectedInjectPreloadCsSingleEvent =
    static_cast<uint64_t>(1 + kInjectSuccessRecordIndex);

bool InjectHitFifthSuccessRecord(const DelayAudit& a) {
    return a.match_count == 1 && a.last_inject_preload_cs > 0;
}

bool InjectOrdinalOk(const DelayAudit& a, uint64_t expected_cs) {
    if (!InjectHitFifthSuccessRecord(a)) {
        return false;
    }
    if (a.last_inject_preload_cs != expected_cs) {
        return false;
    }
    return a.active_record_success_ord == 6;
}

bool LoadDelayAudit(const std::string& dir, DelayAudit* out) {
    for (const auto& ent : std::filesystem::directory_iterator(dir)) {
        const std::string p = ent.path().string();
        if (p.find("delay_audit.json") == std::string::npos) {
            continue;
        }
        std::ifstream in(p);
        std::string s((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
        auto grab = [&](const char* key) -> long long {
            const std::string needle = std::string("\"") + key + "\":";
            const auto pos = s.find(needle);
            if (pos == std::string::npos) {
                return 0;
            }
            return std::atoll(s.c_str() + pos + needle.size());
        };
        out->match_count = static_cast<int>(grab("match_count"));
        out->inject_failed = static_cast<int>(grab("inject_failed"));
        out->callback_count = static_cast<uint32_t>(grab("callback_count"));
        out->arm_tid = static_cast<uint32_t>(grab("arm_tid"));
        out->record_issuing_tid = static_cast<uint32_t>(grab("record_issuing_tid"));
        out->last_inject_preload_cs = static_cast<uint64_t>(grab("last_inject_preload_cs"));
        out->active_record_success_ord = static_cast<int>(grab("active_record_success_ord"));
        return true;
    }
    return false;
}

int SuccessRecords(int n) {
    aclrtEvent ev = nullptr;
    aclrtStream stream = reinterpret_cast<aclrtStream>(0x2000);
    if (aclrtCreateEvent(&ev) != 0) {
        return 1;
    }
    for (int i = 0; i < n; ++i) {
        if (aclrtRecordEvent(ev, stream) != 0) {
            return 2;
        }
    }
    return 0;
}

struct RecordWorkerCtx {
    int n{0};
    int rc{0};
};

void* RecordWorker(void* arg) {
    auto* ctx = static_cast<RecordWorkerCtx*>(arg);
    ctx->rc = SuccessRecords(ctx->n);
    return nullptr;
}

int RunScenario(const char* name) {
    const std::string dir =
        std::string("/tmp/event_delay_v3_") + name + "_" + std::to_string(getpid());
    std::filesystem::remove_all(dir);
    std::filesystem::create_directories(dir);
    setenv("ACL_EVENT_TRACE_DIR", dir.c_str(), 1);
    setenv("LOCAL_RANK", "0", 1);
    if (auto reset = FakeReset()) {
        reset();
    }

    if (std::strcmp(name, "non_rank0") == 0) {
        setenv("RANK", "1", 1);
        setenv("ACL_EVENT_DELAY_US", "2000", 1);
        acl_event_delay_arm();
        SuccessRecords(6);
        acl_event_delay_disarm();
        acl_event_trace_finalize();
        DelayAudit a{};
        if (!LoadDelayAudit(dir, &a) || a.match_count != 0) {
            return 1;
        }
        return 0;
    }

    setenv("RANK", "0", 1);
    if (std::strcmp(name, "unarmed") == 0) {
        setenv("ACL_EVENT_DELAY_US", "2000", 1);
        SuccessRecords(6);
        acl_event_trace_finalize();
        DelayAudit a{};
        if (!LoadDelayAudit(dir, &a) || a.match_count != 0) {
            return 2;
        }
        return 0;
    }
    if (std::strcmp(name, "ordinal4") == 0) {
        setenv("ACL_EVENT_DELAY_US", "2000", 1);
        acl_event_delay_arm();
        for (int i = 0; i < 6; ++i) {
            if (SuccessRecords(1) != 0) {
                return 3;
            }
        }
        acl_event_delay_disarm();
        if (FakeHostfuncCalls() && FakeHostfuncCalls()() != 1) {
            return 4;
        }
        acl_event_trace_finalize();
        DelayAudit a{};
        if (!LoadDelayAudit(dir, &a) || !InjectOrdinalOk(a, kExpectedInjectPreloadCs) || a.callback_count < 1) {
            return 5;
        }
        return 0;
    }
    if (std::strcmp(name, "d0") == 0) {
        setenv("ACL_EVENT_DELAY_US", "0", 1);
        acl_event_delay_arm();
        for (int i = 0; i < 6; ++i) {
            SuccessRecords(1);
        }
        acl_event_delay_disarm();
        if (FakeHostfuncCalls() && FakeHostfuncCalls()() != 0) {
            return 6;
        }
        acl_event_trace_finalize();
        DelayAudit a{};
        if (!LoadDelayAudit(dir, &a) || a.match_count != 0) {
            return 7;
        }
        return 0;
    }
    if (std::strcmp(name, "fail_closed") == 0) {
        setenv("ACL_EVENT_DELAY_US", "2000", 1);
        if (FakeSetFailHostfunc()) {
            FakeSetFailHostfunc()(1);
        }
        acl_event_delay_arm();
        for (int i = 0; i < 6; ++i) {
            SuccessRecords(1);
        }
        acl_event_delay_disarm();
        acl_event_trace_finalize();
        DelayAudit a{};
        if (!LoadDelayAudit(dir, &a) || a.inject_failed != 1 || a.match_count != 0) {
            return 8;
        }
        return 0;
    }
    if (std::strcmp(name, "arm_record_tid_differs") == 0) {
        setenv("ACL_EVENT_DELAY_US", "2000", 1);
        acl_event_delay_arm();
        DelayAudit arm_audit{};
        if (!LoadDelayAudit(dir, &arm_audit)) {
            // audit written at finalize; capture arm_tid after arm via re-read at end
        }
        RecordWorkerCtx ctx{};
        ctx.n = 6;
        pthread_t th{};
        if (pthread_create(&th, nullptr, RecordWorker, &ctx) != 0 || pthread_join(th, nullptr) != 0) {
            return 9;
        }
        if (ctx.rc != 0) {
            return 10;
        }
        acl_event_delay_disarm();
        if (FakeHostfuncCalls() && FakeHostfuncCalls()() != 1) {
            return 11;
        }
        acl_event_trace_finalize();
        DelayAudit a{};
        if (!LoadDelayAudit(dir, &a) || !InjectOrdinalOk(a, kExpectedInjectPreloadCsSingleEvent)) {
            return 12;
        }
        if (a.arm_tid == 0 || a.record_issuing_tid == 0 || a.arm_tid == a.record_issuing_tid) {
            return 13;
        }
        return 0;
    }
    if (std::strcmp(name, "failed_first_no_latch") == 0) {
        setenv("ACL_EVENT_DELAY_US", "2000", 1);
        acl_event_delay_arm();
        if (FakeSetFailRecord()) {
            FakeSetFailRecord()(1);
        }
        aclrtEvent ev = nullptr;
        aclrtStream stream = reinterpret_cast<aclrtStream>(0x2000);
        aclrtCreateEvent(&ev);
        aclrtRecordEvent(ev, stream);
        for (int i = 0; i < 6; ++i) {
            if (SuccessRecords(1) != 0) {
                return 14;
            }
        }
        acl_event_delay_disarm();
        acl_event_trace_finalize();
        DelayAudit a{};
        if (!LoadDelayAudit(dir, &a) || !InjectHitFifthSuccessRecord(a)) {
            return 15;
        }
        return 0;
    }
    if (std::strcmp(name, "wait_before_record_no_latch") == 0) {
        setenv("ACL_EVENT_DELAY_US", "2000", 1);
        acl_event_delay_arm();
        aclrtEvent ev = nullptr;
        aclrtStream stream = reinterpret_cast<aclrtStream>(0x2000);
        aclrtCreateEvent(&ev);
        aclrtStreamWaitEvent(stream, ev);
        SuccessRecords(6);
        acl_event_delay_disarm();
        acl_event_trace_finalize();
        DelayAudit a{};
        if (!LoadDelayAudit(dir, &a) || !InjectHitFifthSuccessRecord(a) || a.record_issuing_tid == 0) {
            return 16;
        }
        return 0;
    }
    if (std::strcmp(name, "ordinal3_no_inject") == 0) {
        setenv("ACL_EVENT_DELAY_US", "2000", 1);
        acl_event_delay_arm();
        for (int i = 0; i < 3; ++i) {
            SuccessRecords(1);
        }
        acl_event_delay_disarm();
        acl_event_trace_finalize();
        DelayAudit a{};
        if (!LoadDelayAudit(dir, &a) || a.match_count != 0) {
            return 17;
        }
        return 0;
    }
    if (std::strcmp(name, "disarmed_no_inject") == 0) {
        setenv("ACL_EVENT_DELAY_US", "2000", 1);
        acl_event_delay_arm();
        acl_event_delay_disarm();
        SuccessRecords(6);
        acl_event_trace_finalize();
        DelayAudit a{};
        if (!LoadDelayAudit(dir, &a) || a.match_count != 0) {
            return 18;
        }
        return 0;
    }
    return 99;
}

int RunChild(const char* name) {
    const pid_t pid = fork();
    if (pid == 0) {
        _exit(RunScenario(name));
    }
    int st = 0;
    waitpid(pid, &st, 0);
    if (!WIFEXITED(st)) {
        return 100;
    }
    return WEXITSTATUS(st);
}

int main(int argc, char** argv) {
    if (argc == 2) {
        return RunScenario(argv[1]);
    }
    const char* cases[] = {
        "non_rank0",
        "unarmed",
        "ordinal4",
        "d0",
        "fail_closed",
        "arm_record_tid_differs",
        "failed_first_no_latch",
        "wait_before_record_no_latch",
        "ordinal3_no_inject",
        "disarmed_no_inject",
    };
    for (const char* c : cases) {
        const int rc = RunChild(c);
        if (rc != 0) {
            std::fprintf(stderr, "FAIL scenario %s rc=%d\n", c, rc);
            return rc;
        }
    }
    std::fprintf(stdout, "EVENT_DELAY_V3_UNIT_PASS\n");
    return 0;
}
