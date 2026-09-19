#include "../event_trace_format.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <filesystem>
#include <fstream>
#include <string>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

#include <dlfcn.h>

extern "C" {
int acl_event_trace_finalize(void);
using aclError = int32_t;
using aclrtEvent = void*;
using aclrtStream = void*;
aclError aclrtCreateEvent(aclrtEvent*);
aclError aclrtCreateEventWithFlag(aclrtEvent*, uint32_t);
aclError aclrtCreateEventExWithFlag(aclrtEvent*, uint32_t);
aclError aclrtDestroyEvent(aclrtEvent);
aclError aclrtRecordEvent(aclrtEvent, aclrtStream);
aclError aclrtResetEvent(aclrtEvent, aclrtStream);
aclError aclrtStreamWaitEvent(aclrtStream, aclrtEvent);
}

using FakeResetFn = void (*)();
using FakeSetFailCreateFn = void (*)(int);
using FakeSetHideExFn = void (*)(int);
using FakeCreateCallsFn = int (*)();

FakeResetFn FakeReset() {
    static auto fn = reinterpret_cast<FakeResetFn>(dlsym(RTLD_DEFAULT, "fake_acl_reset"));
    return fn;
}
FakeSetFailCreateFn FakeSetFailCreate() {
    static auto fn = reinterpret_cast<FakeSetFailCreateFn>(dlsym(RTLD_DEFAULT, "fake_acl_set_fail_create"));
    return fn;
}
FakeSetHideExFn FakeSetHideEx() {
    static auto fn = reinterpret_cast<FakeSetHideExFn>(dlsym(RTLD_DEFAULT, "fake_acl_set_hide_ex"));
    return fn;
}
FakeCreateCallsFn FakeCreateCalls() {
    static auto fn = reinterpret_cast<FakeCreateCallsFn>(dlsym(RTLD_DEFAULT, "fake_acl_create_calls"));
    return fn;
}

struct LoadedTrace {
    AclEventTraceHeader header{};
    std::vector<AclEventTraceRecord> records;
    AclEventTraceTrailer trailer{};
};

bool LoadTrace(const std::string& path, LoadedTrace* out) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        return false;
    }
    in.read(reinterpret_cast<char*>(&out->header), sizeof(out->header));
    if (!in || out->header.magic != ACL_EVENT_TRACE_MAGIC || out->header.version != ACL_EVENT_TRACE_VERSION) {
        return false;
    }
    out->records.resize(static_cast<size_t>(out->header.committed_count));
    if (!out->records.empty()) {
        in.read(reinterpret_cast<char*>(out->records.data()),
                static_cast<std::streamsize>(out->records.size() * sizeof(AclEventTraceRecord)));
    }
    in.read(reinterpret_cast<char*>(&out->trailer), sizeof(out->trailer));
    return in.good() && out->trailer.committed_count == out->header.committed_count;
}

int CountOp(const LoadedTrace& t, uint8_t op) {
    int n = 0;
    for (const auto& r : t.records) {
        if (r.op == op && r.acl_ret == 0) {
            ++n;
        }
    }
    return n;
}

int LogicalCreates(const LoadedTrace& t) {
    return static_cast<int>(t.header.logical_create_count);
}

int RunScenarioBody(const char* name) {
    const std::string dir = std::string("/tmp/event_seq_smoke_") + name + "_" + std::to_string(getpid());
    std::filesystem::remove_all(dir);
    std::filesystem::create_directories(dir);
    setenv("ACL_EVENT_TRACE_DIR", dir.c_str(), 1);
    setenv("RANK", "0", 1);
    setenv("LOCAL_RANK", "0", 1);
    if (auto reset = FakeReset()) {
        reset();
    }

    aclrtEvent ev = nullptr;
    aclrtStream comm = reinterpret_cast<aclrtStream>(0x2000);
    aclrtStream compute = reinterpret_cast<aclrtStream>(0x3000);

    if (std::strcmp(name, "create_record_wait") == 0) {
        aclrtCreateEvent(&ev);
        aclrtRecordEvent(ev, comm);
        aclrtStreamWaitEvent(compute, ev);
        aclrtDestroyEvent(ev);
    } else if (std::strcmp(name, "dlsym_ex_create") == 0) {
        void* h = dlopen("libfake_acl.so", RTLD_NOW | RTLD_LOCAL);
        if (h == nullptr) {
            return 1;
        }
        using Fn = aclError (*)(aclrtEvent*, uint32_t);
        auto* fn = reinterpret_cast<Fn>(dlsym(h, "aclrtCreateEventExWithFlag"));
        if (fn == nullptr) {
            return 2;
        }
        if (fn(&ev, 0) != 0 || ev == nullptr) {
            return 3;
        }
        aclrtRecordEvent(ev, comm);
        aclrtStreamWaitEvent(compute, ev);
        aclrtDestroyEvent(ev);
    } else if (std::strcmp(name, "dlsym_fallback_withflag") == 0) {
        if (auto hide = FakeSetHideEx()) {
            hide(1);
        }
        void* h = dlopen("libfake_acl.so", RTLD_NOW | RTLD_LOCAL);
        using FnEx = aclError (*)(aclrtEvent*, uint32_t);
        using FnWf = aclError (*)(aclrtEvent*, uint32_t);
        auto* ex = reinterpret_cast<FnEx>(dlsym(h, "aclrtCreateEventExWithFlag"));
        if (ex != nullptr && ex(&ev, 0) == 0) {
            return 4;
        }
        auto* wf = reinterpret_cast<FnWf>(dlsym(h, "aclrtCreateEventWithFlag"));
        if (wf == nullptr || wf(&ev, 0) != 0) {
            return 5;
        }
        aclrtRecordEvent(ev, comm);
        aclrtStreamWaitEvent(compute, ev);
        aclrtDestroyEvent(ev);
    } else if (std::strcmp(name, "double_record") == 0) {
        aclrtCreateEvent(&ev);
        aclrtRecordEvent(ev, comm);
        aclrtRecordEvent(ev, comm);
        aclrtStreamWaitEvent(compute, ev);
        aclrtDestroyEvent(ev);
    } else if (std::strcmp(name, "reset_record") == 0) {
        aclrtCreateEvent(&ev);
        aclrtRecordEvent(ev, comm);
        aclrtResetEvent(ev, comm);
        aclrtRecordEvent(ev, comm);
        aclrtStreamWaitEvent(compute, ev);
        aclrtDestroyEvent(ev);
    } else if (std::strcmp(name, "reuse_pointer") == 0) {
        aclrtCreateEvent(&ev);
        const uint64_t raw = reinterpret_cast<uint64_t>(ev);
        aclrtDestroyEvent(ev);
        ev = reinterpret_cast<aclrtEvent>(raw);
        aclrtCreateEvent(&ev);
        aclrtRecordEvent(ev, comm);
        aclrtStreamWaitEvent(compute, ev);
        aclrtDestroyEvent(ev);
    } else if (std::strcmp(name, "fail_no_epoch") == 0) {
        if (auto fail = FakeSetFailCreate()) {
            fail(1);
        }
        aclrtCreateEvent(&ev);
        aclrtRecordEvent(ev, comm);
        aclrtStreamWaitEvent(compute, ev);
        return 0;
    } else if (std::strcmp(name, "wait_without_record") == 0) {
        aclrtCreateEvent(&ev);
        aclrtStreamWaitEvent(compute, ev);
        aclrtDestroyEvent(ev);
        return 0;
    } else {
        return 99;
    }

    if (acl_event_trace_finalize() != 0) {
        return 10;
    }
    LoadedTrace trace;
    const std::string bin = dir + "/rank_0_pid_" + std::to_string(getpid()) + ".events.bin";
    if (!LoadTrace(bin, &trace)) {
        return 11;
    }
    if (trace.header.dropped != 0 || trace.header.fatal != 0 || trace.header.late_calls != 0) {
        return 12;
    }
    const int creates = LogicalCreates(trace);
    const int records = CountOp(trace, kAclEventTraceOpRecord);
    const int waits = CountOp(trace, kAclEventTraceOpWait);
    if (std::strcmp(name, "fail_no_epoch") == 0 || std::strcmp(name, "wait_without_record") == 0) {
        return 0;
    }
    if (records > 0 && creates == 0) {
        return 13;
    }
    if (std::strcmp(name, "dlsym_ex_create") == 0) {
        if (trace.header.acl_create_wrapper_calls < 1 || trace.header.resolver_wrappers_returned < 1) {
            return 14;
        }
        if (auto calls = FakeCreateCalls()) {
            if (calls() < 1) {
                return 15;
            }
        }
    }
    if (creates >= 1 && records >= 1 && waits >= 1) {
        return 0;
    }
    return 16;
}

bool RunScenario(const char* name, bool expect_pass) {
    const pid_t child = fork();
    if (child < 0) {
        return false;
    }
    if (child == 0) {
        const int rc = RunScenarioBody(name);
        _exit(rc);
    }
    int status = 0;
    if (waitpid(child, &status, 0) < 0) {
        return false;
    }
    if (WIFSIGNALED(status)) {
        std::fprintf(stderr, "%s: signal %d\n", name, WTERMSIG(status));
        return !expect_pass;
    }
    const int rc = WEXITSTATUS(status);
    if (std::strcmp(name, "fail_no_epoch") == 0 || std::strcmp(name, "wait_without_record") == 0) {
        return !expect_pass ? rc == 0 : rc != 0;
    }
    if (expect_pass) {
        if (rc != 0) {
            std::fprintf(stderr, "%s: child rc=%d\n", name, rc);
        }
        return rc == 0;
    }
    return rc != 0;
}

int main() {
    const char* cases[][2] = {
        {"create_record_wait", "1"},
        {"dlsym_ex_create", "1"},
        {"dlsym_fallback_withflag", "1"},
        {"double_record", "1"},
        {"reset_record", "1"},
        {"reuse_pointer", "1"},
        {"fail_no_epoch", "0"},
        {"wait_without_record", "0"},
    };
    int failed = 0;
    for (const auto& c : cases) {
        const bool expect = std::strcmp(c[1], "1") == 0;
        if (!RunScenario(c[0], expect)) {
            ++failed;
        }
    }
    if (failed != 0) {
        std::fprintf(stderr, "event_sequence_smoke: %d failures\n", failed);
        return 1;
    }
    std::printf("event_sequence_smoke: PASS\n");
    return 0;
}
