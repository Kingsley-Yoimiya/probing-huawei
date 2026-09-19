#include "device_work.h"

#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <unistd.h>

namespace {

std::atomic<bool> g_armed{false};
std::atomic<bool> g_prepared{false};
std::atomic<bool> g_manifest_valid{false};
std::atomic<int> g_target_wait_ord{-1};
std::atomic<uint32_t> g_requested_iters{0};
std::atomic<int> g_launch_count{0};
std::atomic<int32_t> g_launch_rc{0};
std::atomic<uint64_t> g_trigger_wait_cs{0};
std::atomic<uint64_t> g_last_stream{0};
std::atomic<int> g_inject_failed{0};
int g_rank{-1};

bool InjectSiteIsAfterWait() {
    const char* site = getenv("ACL_EVENT_INJECT_SITE");
    return site != nullptr && std::strcmp(site, "AFTER_SUCCESSFUL_TARGET_WAIT") == 0;
}

bool LoadWaitOrdinal(const char* path, int* out_ord) {
    if (path == nullptr || out_ord == nullptr) {
        return false;
    }
    const int fd = open(path, O_RDONLY);
    if (fd < 0) {
        return false;
    }
    char buf[4096]{};
    const ssize_t n = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n <= 0) {
        return false;
    }
    buf[n] = '\0';
    const char* key = "\"wait_active_success_ordinal\"";
    const char* p = std::strstr(buf, key);
    if (p == nullptr) {
        return false;
    }
    p += std::strlen(key);
    while (*p == ' ' || *p == ':' || *p == '\t') {
        ++p;
    }
    char* end = nullptr;
    const long val = strtol(p, &end, 10);
    if (end == p || val < 0) {
        return false;
    }
    *out_ord = static_cast<int>(val);
    return true;
}

void WriteAudit() {
    const char* dir = getenv("ACL_EVENT_TRACE_DIR");
    if (dir == nullptr) {
        return;
    }
    char path[512];
    std::snprintf(path, sizeof(path), "%s/rank_0_pid_%d.device_work_audit.json", dir, getpid());
    const int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        return;
    }
    char buf[1024];
    const int n = std::snprintf(
        buf, sizeof(buf),
        "{\"rank\":0,\"pid\":%d,\"launch_count\":%d,\"launch_rc\":%d,"
        "\"inject_failed\":%d,\"inject_site\":\"%s\","
        "\"trigger_wait_preload_cs\":%llu,\"raw_stream\":%llu,"
        "\"requested_iters\":%u,\"real_wait_rc\":0}\n",
        getpid(), g_launch_count.load(), g_launch_rc.load(), g_inject_failed.load(),
        InjectSiteIsAfterWait() ? "AFTER_SUCCESSFUL_TARGET_WAIT" : "BEFORE_RECORD",
        static_cast<unsigned long long>(g_trigger_wait_cs.load()),
        static_cast<unsigned long long>(g_last_stream.load()),
        g_requested_iters.load());
    if (n > 0) {
        write(fd, buf, static_cast<size_t>(n));
    }
    close(fd);
}

}  // namespace

extern "C" {

void acl_event_work_prepare(void) {
    const char* rank = getenv("RANK");
    if (rank != nullptr) {
        g_rank = std::atoi(rank);
    }
    const char* manifest = getenv("ACL_EVENT_SELECTOR_MANIFEST");
    int ord = 0;
    if (manifest != nullptr && manifest[0] != '\0') {
        const int fd = open(manifest, O_RDONLY);
        if (fd >= 0) {
            close(fd);
            if (!LoadWaitOrdinal(manifest, &ord)) {
                ord = 0;
            }
            g_target_wait_ord.store(ord);
            g_manifest_valid.store(true);
        }
    }
    g_prepared.store(true);
}

void acl_event_work_arm(void) {
    const char* iters = getenv("ACL_EVENT_WORK_ITERS");
    g_requested_iters.store(static_cast<uint32_t>(iters ? std::atoi(iters) : 0));
    g_launch_count.store(0);
    g_launch_rc.store(0);
    g_inject_failed.store(0);
    g_trigger_wait_cs.store(0);
    g_armed.store(true);
}

void acl_event_work_disarm(void) { g_armed.store(false); }

void acl_event_work_cleanup(void) {
    g_prepared.store(false);
    g_manifest_valid.store(false);
}

void acl_event_work_set_record_issuing_tid(uint32_t) {}

void acl_event_work_on_inject_claim(uint64_t, uint64_t, uint64_t) {}

void acl_event_work_on_inject_record_rc(int32_t) {}

void acl_event_work_on_wait_inject_claim(uint64_t preload_cs, uint64_t raw_event, uint64_t raw_stream) {
    g_trigger_wait_cs.store(preload_cs);
    g_last_stream.store(raw_stream != 0 ? raw_stream : raw_event);
}

void acl_event_work_on_inject_wait_rc(int32_t) {}

void acl_event_work_record_sidecar_snapshot(uint64_t preload_cs) {
    const char* dir = getenv("ACL_EVENT_TRACE_DIR");
    if (dir == nullptr) {
        return;
    }
    char path[512];
    std::snprintf(path, sizeof(path), "%s/rank_0_pid_%d.sidecar_timeline.jsonl", dir, getpid());
    const int fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd < 0) {
        return;
    }
    char buf[128];
    const int n = std::snprintf(
        buf, sizeof(buf),
        "{\"preload_cs\":%llu,\"launch_count\":%d,\"monotonic_ns\":0}\n",
        static_cast<unsigned long long>(preload_cs), g_launch_count.load());
    if (n > 0) {
        write(fd, buf, static_cast<size_t>(n));
    }
    close(fd);
}

void acl_event_work_write_audit(void) { WriteAudit(); }

int acl_event_work_try_launch(void* stream, uint64_t* host_enter_ns, uint64_t* host_exit_ns) {
    (void)stream;
    if (host_enter_ns) {
        *host_enter_ns = 0;
    }
    if (host_exit_ns) {
        *host_exit_ns = 0;
    }
    g_launch_rc.store(0);
    g_launch_count.fetch_add(1);
    return 0;
}

bool acl_event_work_should_inject(int, uint32_t) { return false; }

bool acl_event_work_inject_site_is_after_wait(void) { return InjectSiteIsAfterWait(); }

bool acl_event_work_should_inject_after_wait(int active_wait_success_ord, uint32_t wait_issuing_tid) {
    if (!InjectSiteIsAfterWait()) {
        return false;
    }
    if (wait_issuing_tid == 0) {
        return false;
    }
    if (active_wait_success_ord != g_target_wait_ord.load()) {
        return false;
    }
    if (g_requested_iters.load() == 0) {
        return false;
    }
    if (g_launch_count.load() != 0) {
        return false;
    }
    if (g_inject_failed.load() != 0) {
        return false;
    }
    return true;
}

uint32_t acl_event_work_chainout_checksum(uint32_t, int32_t* out_launch_rc) {
    if (out_launch_rc) {
        *out_launch_rc = 0;
    }
    return 0;
}

int32_t acl_event_work_chainout_proof(uint32_t, uint32_t, uint32_t*, uint32_t, int32_t* out_launch_rc) {
    if (out_launch_rc) {
        *out_launch_rc = 0;
    }
    return 0;
}

}  // extern "C"
