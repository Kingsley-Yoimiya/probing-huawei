#include "../event_trace_format.h"

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <thread>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

using aclError = int32_t;
using aclrtEvent = void*;
using aclrtStream = void*;

extern "C" {
void acl_event_work_prepare(void);
void acl_event_delay_arm(void);
void acl_event_delay_disarm(void);
int acl_event_trace_finalize(void);
void fake_acl_reset(void);
void fake_acl_set_fail_wait(int n);
int fake_acl_hostfunc_calls(void);
aclError aclrtCreateEvent(aclrtEvent*);
aclError aclrtRecordEvent(aclrtEvent, aclrtStream);
aclError aclrtStreamWaitEvent(aclrtStream, aclrtEvent);
}

struct WorkAudit {
    int launch_count{0};
    int launch_rc{0};
    int inject_failed{0};
    std::string inject_site;
    uint64_t trigger_wait_cs{0};
    uint64_t raw_stream{0};
};

bool LoadWorkAudit(const std::string& dir, WorkAudit* out) {
    if (out == nullptr) {
        return false;
    }
    for (const auto& ent : std::filesystem::directory_iterator(dir)) {
        const std::string name = ent.path().filename().string();
        if (name.find("device_work_audit.json") == std::string::npos) {
            continue;
        }
        std::ifstream in(ent.path());
        std::string body((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
        auto get_int = [&](const char* key) {
            const std::string needle = std::string("\"") + key + "\"";
            const size_t p = body.find(needle);
            if (p == std::string::npos) {
                return 0;
            }
            size_t i = p + needle.size();
            while (i < body.size() && (body[i] == ':' || body[i] == ' ')) {
                ++i;
            }
            return std::atoi(body.c_str() + i);
        };
        auto get_u64 = [&](const char* key) -> uint64_t {
            const std::string needle = std::string("\"") + key + "\"";
            const size_t p = body.find(needle);
            if (p == std::string::npos) {
                return 0ULL;
            }
            size_t i = p + needle.size();
            while (i < body.size() && (body[i] == ':' || body[i] == ' ')) {
                ++i;
            }
            return static_cast<uint64_t>(std::strtoull(body.c_str() + i, nullptr, 10));
        };
        auto get_str = [&](const char* key) {
            const std::string needle = std::string("\"") + key + "\"";
            const size_t p = body.find(needle);
            if (p == std::string::npos) {
                return std::string();
            }
            const size_t q1 = body.find('"', p + needle.size());
            const size_t q2 = body.find('"', q1 + 1);
            if (q1 == std::string::npos || q2 == std::string::npos) {
                return std::string();
            }
            return body.substr(q1 + 1, q2 - q1 - 1);
        };
        out->launch_count = get_int("launch_count");
        out->launch_rc = get_int("launch_rc");
        out->inject_failed = get_int("inject_failed");
        out->trigger_wait_cs = get_u64("trigger_wait_preload_cs");
        out->raw_stream = get_u64("raw_stream");
        out->inject_site = get_str("inject_site");
        return true;
    }
    return false;
}

void WriteManifest(const std::string& path, int wait_ord) {
    std::ofstream out(path);
    out << "{\"wait_active_success_ordinal\":" << wait_ord
        << ",\"record_active_success_ordinal\":0}\n";
}

aclrtStream g_comm = reinterpret_cast<aclrtStream>(0x2000);
aclrtStream g_compute = reinterpret_cast<aclrtStream>(0x1000);
aclrtEvent g_ev = reinterpret_cast<aclrtEvent>(0x3000);

int SuccessWaits(int n) {
    for (int i = 0; i < n; ++i) {
        if (aclrtStreamWaitEvent(g_comm, g_ev) != 0) {
            return 1;
        }
    }
    return 0;
}

int SuccessRecords(int n) {
    for (int i = 0; i < n; ++i) {
        if (aclrtRecordEvent(g_ev, g_compute) != 0) {
            return 1;
        }
    }
    return 0;
}

int RunScenario(const char* name) {
    const std::string dir =
        std::string("/tmp/v4_6_unit_") + name + "_" + std::to_string(getpid());
    std::filesystem::remove_all(dir);
    std::filesystem::create_directories(dir);
    setenv("ACL_EVENT_TRACE_DIR", dir.c_str(), 1);
    setenv("RANK", "0", 1);
    setenv("LOCAL_RANK", "0", 1);
    setenv("ACL_EVENT_INJECT_SITE", "AFTER_SUCCESSFUL_TARGET_WAIT", 1);
    const std::string manifest = dir + "/selector.json";
    WriteManifest(manifest, 0);
    setenv("ACL_EVENT_SELECTOR_MANIFEST", manifest.c_str(), 1);
    fake_acl_reset();

    acl_event_work_prepare();

    if (std::strcmp(name, "d0_zero_launch") == 0) {
        setenv("ACL_EVENT_WORK_ITERS", "0", 1);
        acl_event_delay_arm();
        SuccessRecords(1);
        SuccessWaits(1);
        acl_event_delay_disarm();
        acl_event_trace_finalize();
        WorkAudit a{};
        if (!LoadWorkAudit(dir, &a) || a.launch_count != 0) {
            return 1;
        }
        return 0;
    }
    if (std::strcmp(name, "wait_then_launch") == 0) {
        setenv("ACL_EVENT_WORK_ITERS", "325", 1);
        acl_event_delay_arm();
        SuccessRecords(1);
        SuccessWaits(1);
        acl_event_delay_disarm();
        acl_event_trace_finalize();
        WorkAudit a{};
        if (!LoadWorkAudit(dir, &a) || a.launch_count != 1 || a.launch_rc != 0) {
            return 2;
        }
        if (a.inject_site != "AFTER_SUCCESSFUL_TARGET_WAIT") {
            return 3;
        }
        return 0;
    }
    if (std::strcmp(name, "fail_wait_no_launch") == 0) {
        setenv("ACL_EVENT_WORK_ITERS", "325", 1);
        fake_acl_set_fail_wait(1);
        acl_event_delay_arm();
        SuccessRecords(1);
        if (SuccessWaits(1) == 0) {
            return 4;
        }
        acl_event_delay_disarm();
        acl_event_trace_finalize();
        WorkAudit a{};
        if (!LoadWorkAudit(dir, &a) || a.launch_count != 0) {
            return 5;
        }
        return 0;
    }
    if (std::strcmp(name, "wrong_ordinal") == 0) {
        WriteManifest(manifest, 1);
        setenv("ACL_EVENT_WORK_ITERS", "325", 1);
        acl_event_delay_arm();
        SuccessRecords(1);
        SuccessWaits(1);
        acl_event_delay_disarm();
        acl_event_trace_finalize();
        WorkAudit a{};
        if (!LoadWorkAudit(dir, &a) || a.launch_count != 0) {
            return 6;
        }
        return 0;
    }
    return 99;
}

int main() {
    const std::vector<const char*> cases = {
        "d0_zero_launch", "wait_then_launch", "fail_wait_no_launch", "wrong_ordinal",
    };
    int failed = 0;
    for (const char* c : cases) {
        const pid_t child = fork();
        if (child == 0) {
            const int rc = RunScenario(c);
            std::quick_exit(rc == 0 ? 0 : 1);
        }
        if (child < 0) {
            std::printf("%s: FORK_FAIL\n", c);
            failed++;
            continue;
        }
        int status = 0;
        waitpid(child, &status, 0);
        const int rc = WIFEXITED(status) ? WEXITSTATUS(status) : 1;
        std::printf("%s: %s\n", c, rc == 0 ? "PASS" : "FAIL");
        if (rc != 0) {
            failed++;
        }
    }
    return failed == 0 ? 0 : 1;
}
