#include "device_work.h"

#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <sys/syscall.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

#if defined(ACL_EVENT_TRACE_USE_WRAP)
extern "C" void* real_dlsym(void* handle, const char* symbol);
#define REAL_DLSYM real_dlsym
#else
#define REAL_DLSYM dlsym
#endif

namespace {

constexpr uint32_t kDefaultScratchElems = 256;
constexpr uint32_t kDefaultBlockDim = 1;
constexpr char kKernelName[] = "d51_compute_delay_kernel";
constexpr char kLaunchApiNameConfig[] = "aclrtLaunchKernelWithConfig";
constexpr char kLaunchApiNameHostArgs[] = "aclrtLaunchKernelWithHostArgs";
constexpr uint32_t kLaunchTimeoutUsLow = 60UL * 1000000UL;

struct D51KernelHostArgs {
    uint64_t scratch;
    uint32_t scratch_elems;
    uint32_t iters;
};

using FnBinaryLoadFromFile = aclError (*)(const char*, aclrtBinaryLoadOptions*, aclrtBinHandle*);
using FnBinaryGetFunction = aclError (*)(aclrtBinHandle, const char*, aclrtFuncHandle*);
using FnBinaryUnLoad = aclError (*)(aclrtBinHandle);
using FnLaunchKernelWithConfig = aclError (*)(aclrtFuncHandle, uint32_t, aclrtStream,
                                              aclrtLaunchKernelCfg*, aclrtArgsHandle, void*);
using FnLaunchKernelWithHostArgs = aclError (*)(aclrtFuncHandle, uint32_t, aclrtStream,
                                                aclrtLaunchKernelCfg*, void*, size_t,
                                                aclrtPlaceHolderInfo*, size_t);
using FnKernelArgsInit = aclError (*)(aclrtFuncHandle, aclrtArgsHandle*);
using FnKernelArgsAppend = aclError (*)(aclrtArgsHandle, void*, size_t, aclrtParamHandle*);
using FnKernelArgsAppendPlaceHolder = aclError (*)(aclrtArgsHandle, aclrtParamHandle*);
using FnKernelArgsGetPlaceHolderBuffer =
    aclError (*)(aclrtArgsHandle, aclrtParamHandle, size_t, void**);
using FnKernelArgsFinalize = aclError (*)(aclrtArgsHandle);
using FnKernelArgsParaUpdate = aclError (*)(aclrtArgsHandle, aclrtParamHandle, void*, size_t);
using FnMalloc = aclError (*)(void**, size_t, aclrtMemMallocPolicy);
using FnFree = aclError (*)(void*);
using FnMemcpy = aclError (*)(void*, size_t, const void*, size_t, aclrtMemcpyKind);
using FnSetDevice = aclError (*)(int32_t);

FnBinaryLoadFromFile sym_binary_load_from_file = nullptr;
using FnBinaryLoadFromData = aclError (*)(const void*, size_t, const aclrtBinaryLoadOptions*, aclrtBinHandle*);
FnBinaryLoadFromData sym_binary_load_from_data = nullptr;
FnBinaryGetFunction sym_binary_get_function = nullptr;
FnBinaryUnLoad sym_binary_unload = nullptr;
FnLaunchKernelWithConfig sym_launch_kernel_with_config = nullptr;
FnLaunchKernelWithHostArgs sym_launch_kernel_with_host_args = nullptr;
FnKernelArgsInit sym_kernel_args_init = nullptr;
FnKernelArgsAppend sym_kernel_args_append = nullptr;
FnKernelArgsAppendPlaceHolder sym_kernel_args_append_placeholder = nullptr;
FnKernelArgsGetPlaceHolderBuffer sym_kernel_args_get_placeholder_buffer = nullptr;
FnKernelArgsFinalize sym_kernel_args_finalize = nullptr;
FnKernelArgsParaUpdate sym_kernel_args_para_update = nullptr;
using FnCreateStream = aclError (*)(aclrtStream*);
using FnDestroyStream = aclError (*)(aclrtStream);
using FnSynchronizeStream = aclError (*)(aclrtStream);
using FnSynchronizeDevice = aclError (*)(int32_t);

FnCreateStream sym_create_stream = nullptr;
FnDestroyStream sym_destroy_stream = nullptr;
FnSynchronizeStream sym_synchronize_stream = nullptr;
FnSynchronizeDevice sym_synchronize_device = nullptr;
FnMalloc sym_malloc = nullptr;
FnFree sym_free = nullptr;
FnMemcpy sym_memcpy = nullptr;
FnSetDevice sym_set_device = nullptr;

struct WorkState {
    std::atomic<bool> armed{false};
    std::atomic<bool> prepared{false};
    std::atomic<uint32_t> arm_tid{0};
    std::atomic<uint32_t> record_issuing_tid{0};
    std::atomic<int> target_record_ordinal{-1};
    std::atomic<int> target_wait_ordinal{-1};
    std::atomic<int> manifest_valid{0};
    std::atomic<uint32_t> requested_iters{0};
    std::atomic<int> launch_count{0};
    std::atomic<int32_t> launch_rc{0};
    std::atomic<int32_t> real_record_rc{0};
    std::atomic<int> inject_failed{0};
    std::atomic<uint64_t> trigger_record_preload_cs{0};
    std::atomic<uint64_t> trigger_wait_preload_cs{0};
    std::atomic<int32_t> real_wait_rc{0};
    std::atomic<uint64_t> last_raw_event{0};
    std::atomic<uint64_t> last_raw_stream{0};
    std::atomic<uint64_t> host_enter_ns{0};
    std::atomic<uint64_t> host_exit_ns{0};
    std::atomic<int> chainout_launch_seq{0};
    aclrtBinHandle bin_handle{nullptr};
    aclrtFuncHandle func_handle{nullptr};
    aclrtArgsHandle args_handle{nullptr};
    aclrtParamHandle param_scratch{nullptr};
    aclrtParamHandle param_elems{nullptr};
    aclrtParamHandle param_iters{nullptr};
    // Stable host storage for kernel iters arg (append must not use stack locals).
    uint32_t kernel_iters_arg{0};
    void* scratch_dev{nullptr};
    uint32_t scratch_elems{kDefaultScratchElems};
    uint32_t scratch_bytes{kDefaultScratchElems * sizeof(uint32_t)};
    uint32_t block_dim{kDefaultBlockDim};
    char binary_path[512]{};
    char binary_sha256[65]{};
    char audit_out_dir[512]{};
    char launch_api_name[64]{};
    int rank{-1};
    int pid{0};
    pthread_mutex_t audit_mutex = PTHREAD_MUTEX_INITIALIZER;
};

WorkState g_work;

inline uint64_t ReadMonoNs() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<uint64_t>(ts.tv_sec) * 1000000000ULL +
           static_cast<uint64_t>(ts.tv_nsec);
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

bool LoadSelectorManifestOrdinal(const char* path, const char* key, int* out_ord) {
    if (path == nullptr || key == nullptr || out_ord == nullptr) {
        return false;
    }
    const int fd = open(path, O_RDONLY);
    if (fd < 0) {
        return false;
    }
    char buf[8192]{};
    const ssize_t n = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n <= 0) {
        return false;
    }
    buf[n] = '\0';
    const char* needle = key;
    const char* p = std::strstr(buf, needle);
    if (p == nullptr) {
        return false;
    }
    p += std::strlen(needle);
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

bool LoadSelectorManifestOrdinal(const char* path, int* out_ord) {
    return LoadSelectorManifestOrdinal(path, "\"record_active_success_ordinal\"", out_ord);
}

inline bool InjectSiteIsAfterWait() {
    const char* site = getenv("ACL_EVENT_INJECT_SITE");
    if (site == nullptr || site[0] == '\0') {
        return false;
    }
    return std::strcmp(site, "AFTER_SUCCESSFUL_TARGET_WAIT") == 0;
}

void ResolveLaunchSymbols() {
    if (sym_binary_load_from_file != nullptr) {
        return;
    }
    sym_binary_load_from_file =
        reinterpret_cast<FnBinaryLoadFromFile>(REAL_DLSYM(RTLD_NEXT, "aclrtBinaryLoadFromFile"));
    sym_binary_get_function =
        reinterpret_cast<FnBinaryGetFunction>(REAL_DLSYM(RTLD_NEXT, "aclrtBinaryGetFunction"));
    sym_binary_unload = reinterpret_cast<FnBinaryUnLoad>(REAL_DLSYM(RTLD_NEXT, "aclrtBinaryUnLoad"));
    sym_launch_kernel_with_config =
        reinterpret_cast<FnLaunchKernelWithConfig>(REAL_DLSYM(RTLD_NEXT, "aclrtLaunchKernelWithConfig"));
    sym_launch_kernel_with_host_args =
        reinterpret_cast<FnLaunchKernelWithHostArgs>(REAL_DLSYM(RTLD_NEXT, "aclrtLaunchKernelWithHostArgs"));
    sym_kernel_args_init =
        reinterpret_cast<FnKernelArgsInit>(REAL_DLSYM(RTLD_NEXT, "aclrtKernelArgsInit"));
    sym_kernel_args_append =
        reinterpret_cast<FnKernelArgsAppend>(REAL_DLSYM(RTLD_NEXT, "aclrtKernelArgsAppend"));
    sym_kernel_args_append_placeholder = reinterpret_cast<FnKernelArgsAppendPlaceHolder>(
        REAL_DLSYM(RTLD_NEXT, "aclrtKernelArgsAppendPlaceHolder"));
    sym_kernel_args_get_placeholder_buffer = reinterpret_cast<FnKernelArgsGetPlaceHolderBuffer>(
        REAL_DLSYM(RTLD_NEXT, "aclrtKernelArgsGetPlaceHolderBuffer"));
    sym_kernel_args_finalize =
        reinterpret_cast<FnKernelArgsFinalize>(REAL_DLSYM(RTLD_NEXT, "aclrtKernelArgsFinalize"));
    sym_kernel_args_para_update =
        reinterpret_cast<FnKernelArgsParaUpdate>(REAL_DLSYM(RTLD_NEXT, "aclrtKernelArgsParaUpdate"));
    sym_malloc = reinterpret_cast<FnMalloc>(REAL_DLSYM(RTLD_NEXT, "aclrtMalloc"));
    sym_free = reinterpret_cast<FnFree>(REAL_DLSYM(RTLD_NEXT, "aclrtFree"));
    sym_memcpy = reinterpret_cast<FnMemcpy>(REAL_DLSYM(RTLD_NEXT, "aclrtMemcpy"));
    sym_set_device = reinterpret_cast<FnSetDevice>(REAL_DLSYM(RTLD_NEXT, "aclrtSetDevice"));
    sym_create_stream =
        reinterpret_cast<FnCreateStream>(REAL_DLSYM(RTLD_NEXT, "aclrtCreateStream"));
    sym_destroy_stream =
        reinterpret_cast<FnDestroyStream>(REAL_DLSYM(RTLD_NEXT, "aclrtDestroyStream"));
    sym_synchronize_stream =
        reinterpret_cast<FnSynchronizeStream>(REAL_DLSYM(RTLD_NEXT, "aclrtSynchronizeStream"));
    sym_synchronize_device =
        reinterpret_cast<FnSynchronizeDevice>(REAL_DLSYM(RTLD_NEXT, "aclrtSynchronizeDevice"));
    sym_binary_load_from_data =
        reinterpret_cast<FnBinaryLoadFromData>(REAL_DLSYM(RTLD_NEXT, "aclrtBinaryLoadFromData"));
}

bool Sha256File(const char* path, char* out_hex, size_t out_len) {
    if (out_len < 65) {
        return false;
    }
    char cmd[640];
    std::snprintf(cmd, sizeof(cmd), "sha256sum '%s' 2>/dev/null", path);
    FILE* fp = popen(cmd, "r");
    if (fp == nullptr) {
        return false;
    }
    char line[128];
    if (fgets(line, sizeof(line), fp) == nullptr) {
        pclose(fp);
        return false;
    }
    pclose(fp);
    unsigned char hex[32];
    if (std::sscanf(line, "%64s", out_hex) != 1) {
        return false;
    }
    out_hex[64] = '\0';
    (void)hex;
    return true;
}

bool ReadBinaryFile(const char* path, std::vector<uint8_t>& out) {
    const int fd = open(path, O_RDONLY);
    if (fd < 0) {
        return false;
    }
    struct stat st{};
    if (fstat(fd, &st) != 0 || st.st_size <= 0) {
        close(fd);
        return false;
    }
    out.resize(static_cast<size_t>(st.st_size));
    const ssize_t n = read(fd, out.data(), out.size());
    close(fd);
    return n == static_cast<ssize_t>(out.size());
}

inline uint32_t SlotPattern(uint32_t nonce, uint32_t slot, uint32_t salt) {
    uint32_t x = nonce ^ salt ^ (slot * 0x85EBCA6Bu);
    x = x * 0x9E3779B9u + 0xC2B2AE35u;
    x ^= (x >> 16);
    return (x << 7) | (x >> 25);
}

void InitScratchWithNonce(uint32_t runtime_nonce, std::vector<uint32_t>& scratch) {
    const uint32_t nonce = runtime_nonce;
    const size_t n = scratch.size();
    for (size_t i = 0; i < n; ++i) {
        scratch[i] = 0;
    }
    if (n == 0) {
        return;
    }
    scratch[0] = nonce;
    for (uint32_t i = 1; i < 4 && i < n; ++i) {
        scratch[i] = SlotPattern(nonce, i, 0xC1A2B3C4u);
    }
    for (uint32_t i = 4; i < 68 && i < n; ++i) {
        scratch[i] = SlotPattern(nonce, i, 0x0E00F00u);
    }
    if (68 < n) {
        scratch[68] = SlotPattern(nonce, 68, 0x57A7E00u);
    }
    for (uint32_t i = 253; i < 256 && i < n; ++i) {
        scratch[i] = SlotPattern(nonce, i, 0xC0A2D00u);
    }
}

void FailClosed(const char* why) {
    g_work.inject_failed.store(1, std::memory_order_relaxed);
    std::fprintf(stderr, "[device_work] fail-closed: %s\n", why);
}

aclrtLaunchKernelCfg BuildAivLaunchCfg() {
    static aclrtLaunchKernelAttr attrs[3];
    attrs[0].id = ACL_RT_LAUNCH_KERNEL_ATTR_SCHEM_MODE;
    attrs[0].value.schemMode = 1;
    attrs[1].id = ACL_RT_LAUNCH_KERNEL_ATTR_TIMEOUT_US;
    attrs[1].value.timeoutUs.timeoutLow = kLaunchTimeoutUsLow;
    attrs[1].value.timeoutUs.timeoutHigh = 0;
    attrs[2].id = ACL_RT_LAUNCH_KERNEL_ATTR_ENGINE_TYPE;
    attrs[2].value.engineType = ACL_RT_ENGINE_TYPE_AIV;
    static aclrtLaunchKernelCfg cfg;
    cfg.numAttrs = 3;
    cfg.attrs = attrs;
    return cfg;
}

aclError RebuildLaunchArgs(uint32_t iters) {
    if (sym_kernel_args_init == nullptr || sym_kernel_args_append == nullptr ||
        sym_kernel_args_finalize == nullptr) {
        return static_cast<aclError>(100002);
    }
    g_work.kernel_iters_arg = iters;
    aclError rc = sym_kernel_args_init(g_work.func_handle, &g_work.args_handle);
    if (rc != 0) {
        return rc;
    }
    rc = sym_kernel_args_append(g_work.args_handle, reinterpret_cast<void**>(&g_work.scratch_dev),
                                sizeof(uintptr_t), &g_work.param_scratch);
    if (rc != 0) {
        return rc;
    }
    rc = sym_kernel_args_append(g_work.args_handle, &g_work.scratch_elems, sizeof(uint32_t),
                                &g_work.param_elems);
    if (rc != 0) {
        return rc;
    }
    rc = sym_kernel_args_append(g_work.args_handle, &g_work.kernel_iters_arg, sizeof(uint32_t),
                                &g_work.param_iters);
    if (rc != 0) {
        return rc;
    }
    return sym_kernel_args_finalize(g_work.args_handle);
}

aclError LaunchD51Kernel(aclrtStream stream, uint32_t iters) {
    g_work.kernel_iters_arg = iters;
    if (sym_launch_kernel_with_host_args != nullptr) {
        D51KernelHostArgs host_args{};
        host_args.scratch = reinterpret_cast<uint64_t>(g_work.scratch_dev);
        host_args.scratch_elems = g_work.scratch_elems;
        host_args.iters = iters;
        std::strncpy(g_work.launch_api_name, kLaunchApiNameHostArgs, sizeof(g_work.launch_api_name) - 1);
        aclrtLaunchKernelCfg cfg = BuildAivLaunchCfg();
        return sym_launch_kernel_with_host_args(g_work.func_handle, g_work.block_dim, stream, &cfg,
                                                &host_args, sizeof(host_args), nullptr, 0);
    }
    if (sym_launch_kernel_with_config == nullptr) {
        return static_cast<aclError>(100002);
    }
    std::strncpy(g_work.launch_api_name, kLaunchApiNameConfig, sizeof(g_work.launch_api_name) - 1);
    aclError rc = RebuildLaunchArgs(iters);
    if (rc != 0) {
        return rc;
    }
    aclrtLaunchKernelCfg cfg = BuildAivLaunchCfg();
    return sym_launch_kernel_with_config(g_work.func_handle, g_work.block_dim, stream, &cfg,
                                         g_work.args_handle, nullptr);
}

}  // namespace

extern "C" {

#define D51_PREP_LOG(step) (void)0

void acl_event_work_prepare(void) {
    pthread_mutex_lock(&g_work.audit_mutex);
    if (g_work.prepared.load(std::memory_order_acquire)) {
        pthread_mutex_unlock(&g_work.audit_mutex);
        return;
    }
    D51_PREP_LOG(1);
    ResolveLaunchSymbols();
    D51_PREP_LOG(2);
    g_work.rank = ParseEnvInt("RANK", -1);
    g_work.pid = static_cast<int>(getpid());
    const char* manifest_path = getenv("ACL_EVENT_SELECTOR_MANIFEST");
    int manifest_record_ord = -1;
    int manifest_wait_ord = -1;
    if (manifest_path == nullptr || manifest_path[0] == '\0' ||
        !LoadSelectorManifestOrdinal(manifest_path, &manifest_record_ord)) {
        FailClosed("selector manifest missing or invalid record ordinal");
        pthread_mutex_unlock(&g_work.audit_mutex);
        return;
    }
    if (!LoadSelectorManifestOrdinal(manifest_path, "\"wait_active_success_ordinal\"",
                                     &manifest_wait_ord)) {
        manifest_wait_ord = manifest_record_ord;
    }
    g_work.target_record_ordinal.store(manifest_record_ord, std::memory_order_relaxed);
    g_work.target_wait_ordinal.store(manifest_wait_ord, std::memory_order_relaxed);
    g_work.manifest_valid.store(1, std::memory_order_relaxed);
    const char* out = getenv("ACL_EVENT_TRACE_DIR");
    if (out != nullptr) {
        std::strncpy(g_work.audit_out_dir, out, sizeof(g_work.audit_out_dir) - 1);
    }
    const char* bin = getenv("ACL_EVENT_WORK_BINARY");
    if (bin == nullptr || bin[0] == '\0') {
        FailClosed("ACL_EVENT_WORK_BINARY unset");
        pthread_mutex_unlock(&g_work.audit_mutex);
        return;
    }
    std::strncpy(g_work.binary_path, bin, sizeof(g_work.binary_path) - 1);
    D51_PREP_LOG(3);
    char resolved[PATH_MAX]{};
    if (realpath(g_work.binary_path, resolved) != nullptr) {
        std::strncpy(g_work.binary_path, resolved, sizeof(g_work.binary_path) - 1);
    }
    D51_PREP_LOG(4);
    if (sym_binary_load_from_file == nullptr || sym_binary_get_function == nullptr ||
        sym_malloc == nullptr || sym_memcpy == nullptr ||
        (sym_launch_kernel_with_host_args == nullptr && sym_launch_kernel_with_config == nullptr)) {
        FailClosed("missing launch API symbols");
        pthread_mutex_unlock(&g_work.audit_mutex);
        return;
    }
    Sha256File(g_work.binary_path, g_work.binary_sha256, sizeof(g_work.binary_sha256));
    std::strncpy(g_work.launch_api_name, kLaunchApiNameHostArgs, sizeof(g_work.launch_api_name) - 1);
    D51_PREP_LOG(5);
    g_work.scratch_elems = static_cast<uint32_t>(ParseEnvInt("ACL_EVENT_WORK_SCRATCH_ELEMS",
                                                             static_cast<int>(kDefaultScratchElems)));
    if (g_work.scratch_elems == 0 || g_work.scratch_elems > 4096) {
        g_work.scratch_elems = kDefaultScratchElems;
    }
    g_work.scratch_bytes = g_work.scratch_elems * static_cast<uint32_t>(sizeof(uint32_t));
    g_work.block_dim = static_cast<uint32_t>(
        ParseEnvInt("ACL_EVENT_WORK_BLOCK_DIM", static_cast<int>(kDefaultBlockDim)));
    D51_PREP_LOG(6);
    if (sym_set_device != nullptr) {
        const aclError dev_rc = sym_set_device(0);
        if (dev_rc != 0) {
            std::fprintf(stderr, "[device_work] aclrtSetDevice rc=%d\n", dev_rc);
        }
    }
    aclrtBinaryLoadOptions load_opts{};
    load_opts.options = nullptr;
    load_opts.numOpt = 0;
    aclrtBinaryLoadOptions* load_opts_ptr = nullptr;
    aclError rc = sym_binary_load_from_file(g_work.binary_path, load_opts_ptr, &g_work.bin_handle);
    if (rc != 0 && sym_binary_load_from_data != nullptr) {
        std::vector<uint8_t> bin_bytes;
        if (ReadBinaryFile(g_work.binary_path, bin_bytes)) {
            rc = sym_binary_load_from_data(bin_bytes.data(), bin_bytes.size(), load_opts_ptr,
                                          &g_work.bin_handle);
        }
    }
    if (rc != 0) {
        std::fprintf(stderr, "[device_work] aclrtBinaryLoadFromFile rc=%d path=%s\n", rc,
                     g_work.binary_path);
        FailClosed("aclrtBinaryLoadFromFile failed");
        pthread_mutex_unlock(&g_work.audit_mutex);
        return;
    }
    D51_PREP_LOG(7);
    rc = sym_binary_get_function(g_work.bin_handle, kKernelName, &g_work.func_handle);
    if (rc != 0) {
        FailClosed("aclrtBinaryGetFunction failed");
        pthread_mutex_unlock(&g_work.audit_mutex);
        return;
    }
    D51_PREP_LOG(8);
    rc = sym_malloc(&g_work.scratch_dev, g_work.scratch_bytes, ACL_MEM_MALLOC_HUGE_FIRST);
    if (rc != 0) {
        FailClosed("aclrtMalloc scratch failed");
        pthread_mutex_unlock(&g_work.audit_mutex);
        return;
    }
    std::vector<uint32_t> host_init(g_work.scratch_elems);
    for (uint32_t i = 0; i < g_work.scratch_elems; ++i) {
        host_init[i] = 0xA51B0000u + i;
    }
    D51_PREP_LOG(9);
    rc = sym_memcpy(g_work.scratch_dev, g_work.scratch_bytes, host_init.data(), g_work.scratch_bytes,
                    ACL_MEMCPY_HOST_TO_DEVICE);
    if (rc != 0) {
        FailClosed("aclrtMemcpy init failed");
        pthread_mutex_unlock(&g_work.audit_mutex);
        return;
    }
    D51_PREP_LOG(10);
    if (sym_launch_kernel_with_host_args == nullptr) {
        rc = RebuildLaunchArgs(0);
        if (rc != 0) {
            FailClosed("RebuildLaunchArgs probe failed");
            pthread_mutex_unlock(&g_work.audit_mutex);
            return;
        }
    }
    D51_PREP_LOG(12);
    g_work.prepared.store(true, std::memory_order_release);
    pthread_mutex_unlock(&g_work.audit_mutex);
}

void acl_event_work_arm(void) {
    const uint32_t iters = static_cast<uint32_t>(ParseEnvInt("ACL_EVENT_WORK_ITERS", 0));
    g_work.requested_iters.store(iters, std::memory_order_relaxed);
  const uint32_t arm_tid =
      InjectSiteIsAfterWait() ? 0U : static_cast<uint32_t>(syscall(SYS_gettid));
  g_work.arm_tid.store(arm_tid, std::memory_order_relaxed);
    g_work.launch_count.store(0, std::memory_order_relaxed);
    g_work.launch_rc.store(0, std::memory_order_relaxed);
    g_work.real_record_rc.store(0, std::memory_order_relaxed);
    g_work.inject_failed.store(0, std::memory_order_relaxed);
    g_work.trigger_record_preload_cs.store(0, std::memory_order_relaxed);
    g_work.trigger_wait_preload_cs.store(0, std::memory_order_relaxed);
    g_work.real_wait_rc.store(0, std::memory_order_relaxed);
    g_work.last_raw_event.store(0, std::memory_order_relaxed);
    g_work.last_raw_stream.store(0, std::memory_order_relaxed);
    g_work.host_enter_ns.store(0, std::memory_order_relaxed);
    g_work.host_exit_ns.store(0, std::memory_order_relaxed);
    g_work.armed.store(true, std::memory_order_release);
}

void acl_event_work_disarm(void) {
    g_work.armed.store(false, std::memory_order_release);
}

void acl_event_work_cleanup(void) {
    pthread_mutex_lock(&g_work.audit_mutex);
    if (g_work.args_handle != nullptr && sym_kernel_args_finalize != nullptr) {
        // finalize already called; no separate destroy in public API
        g_work.args_handle = nullptr;
    }
    if (g_work.scratch_dev != nullptr && sym_free != nullptr) {
        sym_free(g_work.scratch_dev);
        g_work.scratch_dev = nullptr;
    }
    if (g_work.bin_handle != nullptr && sym_binary_unload != nullptr) {
        sym_binary_unload(g_work.bin_handle);
        g_work.bin_handle = nullptr;
    }
    g_work.func_handle = nullptr;
    g_work.prepared.store(false, std::memory_order_release);
    pthread_mutex_unlock(&g_work.audit_mutex);
}

bool acl_event_work_should_inject(int active_record_success_ord, uint32_t record_issuing_tid) {
    if (InjectSiteIsAfterWait()) {
        return false;
    }
    if (!g_work.armed.load(std::memory_order_acquire)) {
        return false;
    }
    if (!g_work.prepared.load(std::memory_order_acquire)) {
        return false;
    }
    if (g_work.rank != 0) {
        return false;
    }
    if (record_issuing_tid == 0) {
        return false;
    }
    if (static_cast<uint32_t>(syscall(SYS_gettid)) != record_issuing_tid) {
        return false;
    }
    if (g_work.manifest_valid.load(std::memory_order_acquire) == 0) {
        return false;
    }
    if (active_record_success_ord != g_work.target_record_ordinal.load(std::memory_order_relaxed)) {
        return false;
    }
    if (g_work.requested_iters.load(std::memory_order_relaxed) == 0) {
        return false;
    }
    if (g_work.launch_count.load(std::memory_order_relaxed) != 0) {
        return false;
    }
    if (g_work.inject_failed.load(std::memory_order_relaxed) != 0) {
        return false;
    }
    return true;
}

bool acl_event_work_inject_site_is_after_wait(void) {
    return InjectSiteIsAfterWait();
}

bool acl_event_work_should_inject_after_wait(int active_wait_success_ord,
                                             uint32_t wait_issuing_tid) {
    if (!InjectSiteIsAfterWait()) {
        return false;
    }
    if (!g_work.armed.load(std::memory_order_acquire)) {
        return false;
    }
    if (!g_work.prepared.load(std::memory_order_acquire)) {
        return false;
    }
    if (g_work.rank != 0) {
        return false;
    }
    if (wait_issuing_tid == 0) {
        return false;
    }
    if (static_cast<uint32_t>(syscall(SYS_gettid)) != wait_issuing_tid) {
        return false;
    }
    if (g_work.manifest_valid.load(std::memory_order_acquire) == 0) {
        return false;
    }
    if (active_wait_success_ord !=
        g_work.target_wait_ordinal.load(std::memory_order_relaxed)) {
        return false;
    }
    if (g_work.requested_iters.load(std::memory_order_relaxed) == 0) {
        return false;
    }
    if (g_work.launch_count.load(std::memory_order_relaxed) != 0) {
        return false;
    }
    if (g_work.inject_failed.load(std::memory_order_relaxed) != 0) {
        return false;
    }
    return true;
}

void acl_event_work_set_record_issuing_tid(uint32_t tid) {
    g_work.record_issuing_tid.store(tid, std::memory_order_relaxed);
}

void acl_event_work_on_inject_claim(uint64_t preload_cs, uint64_t raw_event, uint64_t raw_stream) {
    g_work.trigger_record_preload_cs.store(preload_cs, std::memory_order_relaxed);
    g_work.last_raw_event.store(raw_event, std::memory_order_relaxed);
    g_work.last_raw_stream.store(raw_stream, std::memory_order_relaxed);
}

void acl_event_work_on_wait_inject_claim(uint64_t preload_cs, uint64_t raw_event,
                                         uint64_t raw_stream) {
    g_work.trigger_wait_preload_cs.store(preload_cs, std::memory_order_relaxed);
    g_work.last_raw_event.store(raw_event, std::memory_order_relaxed);
    g_work.last_raw_stream.store(raw_stream, std::memory_order_relaxed);
}

int acl_event_work_try_launch(aclrtStream stream, uint64_t* host_enter_ns, uint64_t* host_exit_ns) {
    const uint32_t iters = g_work.requested_iters.load(std::memory_order_relaxed);
    const uint64_t enter = ReadMonoNs();
    g_work.host_enter_ns.store(enter, std::memory_order_relaxed);
    if (sym_launch_kernel_with_host_args == nullptr && sym_launch_kernel_with_config == nullptr) {
        g_work.launch_rc.store(100002, std::memory_order_relaxed);
        FailClosed("launch symbols missing at inject");
        const uint64_t exit = ReadMonoNs();
        g_work.host_exit_ns.store(exit, std::memory_order_relaxed);
        if (host_enter_ns) {
            *host_enter_ns = enter;
        }
        if (host_exit_ns) {
            *host_exit_ns = exit;
        }
        return 100002;
    }
    aclError rc = LaunchD51Kernel(stream, iters);
    const uint64_t exit = ReadMonoNs();
    g_work.host_exit_ns.store(exit, std::memory_order_relaxed);
    g_work.launch_rc.store(static_cast<int32_t>(rc), std::memory_order_relaxed);
    if (rc != 0) {
        FailClosed("aclrtLaunchKernelWithConfig failed");
    } else {
        g_work.launch_count.fetch_add(1, std::memory_order_relaxed);
    }
    if (host_enter_ns) {
        *host_enter_ns = enter;
    }
    if (host_exit_ns) {
        *host_exit_ns = exit;
    }
    return static_cast<int>(rc);
}

void acl_event_work_on_inject_record_rc(int32_t rc) {
    g_work.real_record_rc.store(rc, std::memory_order_relaxed);
}

void acl_event_work_on_inject_wait_rc(int32_t rc) {
    g_work.real_wait_rc.store(rc, std::memory_order_relaxed);
}

void acl_event_work_record_sidecar_snapshot(uint64_t preload_cs) {
    const char* out_dir =
        g_work.audit_out_dir[0] != '\0' ? g_work.audit_out_dir : getenv("ACL_EVENT_TRACE_DIR");
    if (out_dir == nullptr || out_dir[0] == '\0') {
        return;
    }
    const int rank = g_work.rank >= 0 ? g_work.rank : ParseEnvInt("RANK", 0);
    const int pid = g_work.pid > 0 ? g_work.pid : static_cast<int>(getpid());
    pthread_mutex_lock(&g_work.audit_mutex);
    char path[640];
    std::snprintf(path, sizeof(path), "%s/rank_%d_pid_%d.sidecar_timeline.jsonl", out_dir, rank, pid);
    const int fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd < 0) {
        pthread_mutex_unlock(&g_work.audit_mutex);
        return;
    }
    char buf[256];
    const int n = std::snprintf(
        buf, sizeof(buf),
        "{\"preload_cs\":%llu,\"launch_count\":%d,\"monotonic_ns\":%llu}\n",
        static_cast<unsigned long long>(preload_cs),
        g_work.launch_count.load(std::memory_order_relaxed),
        static_cast<unsigned long long>(ReadMonoNs()));
    if (n > 0) {
        write(fd, buf, static_cast<size_t>(n));
    }
    close(fd);
    pthread_mutex_unlock(&g_work.audit_mutex);
}

uint32_t acl_event_work_chainout_checksum(uint32_t iters, int32_t* out_launch_rc) {
    if (out_launch_rc) {
        *out_launch_rc = -1;
    }
    if (!g_work.prepared.load(std::memory_order_acquire) || g_work.scratch_dev == nullptr) {
        if (out_launch_rc) {
            *out_launch_rc = 100003;
        }
        return 0;
    }
    if (iters == 0) {
        if (out_launch_rc) {
            *out_launch_rc = 0;
        }
        return 0;
    }
    if ((sym_launch_kernel_with_host_args == nullptr && sym_launch_kernel_with_config == nullptr) ||
        sym_create_stream == nullptr || sym_synchronize_stream == nullptr ||
        sym_destroy_stream == nullptr || sym_memcpy == nullptr) {
        if (out_launch_rc) {
            *out_launch_rc = 100002;
        }
        return 0;
    }
    std::vector<uint32_t> host_init(g_work.scratch_elems);
    for (uint32_t i = 0; i < g_work.scratch_elems; ++i) {
        host_init[i] = 0xA51B0000u + i;
    }
    aclError rc = sym_memcpy(g_work.scratch_dev, g_work.scratch_bytes, host_init.data(),
                             g_work.scratch_bytes, ACL_MEMCPY_HOST_TO_DEVICE);
    if (rc != 0) {
        if (out_launch_rc) {
            *out_launch_rc = static_cast<int32_t>(rc);
        }
        return 0;
    }
    aclrtStream stream = nullptr;
    rc = sym_create_stream(&stream);
    if (rc != 0) {
        if (out_launch_rc) {
            *out_launch_rc = static_cast<int32_t>(rc);
        }
        return 0;
    }
    rc = LaunchD51Kernel(stream, iters);
    if (rc == 0) {
        rc = sym_synchronize_stream(stream);
    }
    if (rc == 0 && sym_synchronize_device != nullptr) {
        rc = sym_synchronize_device(0);
    }
    sym_destroy_stream(stream);
    if (out_launch_rc) {
        *out_launch_rc = static_cast<int32_t>(rc);
    }
    if (rc != 0) {
        return 0;
    }
    std::vector<uint32_t> host_scratch(g_work.scratch_elems);
    rc = sym_memcpy(host_scratch.data(), g_work.scratch_bytes, g_work.scratch_dev,
                    g_work.scratch_bytes, ACL_MEMCPY_DEVICE_TO_HOST);
    if (rc != 0) {
        return 0;
    }
    return host_scratch[0];
}

int32_t acl_event_work_chainout_proof(uint32_t iters, uint32_t runtime_nonce,
                                      uint32_t* out_scratch, uint32_t out_elems,
                                      int32_t* out_launch_rc) {
    if (out_launch_rc) {
        *out_launch_rc = -1;
    }
    if (!g_work.prepared.load(std::memory_order_acquire) || g_work.scratch_dev == nullptr) {
        if (out_launch_rc) {
            *out_launch_rc = 100003;
        }
        return 100003;
    }
    if (out_scratch == nullptr || out_elems == 0 || out_elems > g_work.scratch_elems) {
        if (out_launch_rc) {
            *out_launch_rc = 100004;
        }
        return 100004;
    }
    if ((sym_launch_kernel_with_host_args == nullptr && sym_launch_kernel_with_config == nullptr) ||
        sym_create_stream == nullptr || sym_synchronize_stream == nullptr ||
        sym_destroy_stream == nullptr || sym_memcpy == nullptr) {
        if (out_launch_rc) {
            *out_launch_rc = 100002;
        }
        return 100002;
    }
    std::vector<uint32_t> host_init(g_work.scratch_elems);
    InitScratchWithNonce(runtime_nonce, host_init);
    aclError rc = sym_memcpy(g_work.scratch_dev, g_work.scratch_bytes, host_init.data(),
                             g_work.scratch_bytes, ACL_MEMCPY_HOST_TO_DEVICE);
    if (rc != 0) {
        if (out_launch_rc) {
            *out_launch_rc = static_cast<int32_t>(rc);
        }
        return static_cast<int32_t>(rc);
    }
    aclrtStream stream = nullptr;
    rc = sym_create_stream(&stream);
    if (rc != 0) {
        if (out_launch_rc) {
            *out_launch_rc = static_cast<int32_t>(rc);
        }
        return static_cast<int32_t>(rc);
    }
    const uint64_t enter = ReadMonoNs();
    rc = LaunchD51Kernel(stream, iters);
    const uint64_t exit = ReadMonoNs();
    if (rc == 0) {
        rc = sym_synchronize_stream(stream);
    }
    if (rc == 0 && sym_synchronize_device != nullptr) {
        rc = sym_synchronize_device(0);
    }
    sym_destroy_stream(stream);
    if (out_launch_rc) {
        *out_launch_rc = static_cast<int32_t>(rc);
    }
    const int launch_seq = g_work.chainout_launch_seq.fetch_add(1, std::memory_order_relaxed) + 1;
    if (g_work.audit_out_dir[0] != '\0') {
        char audit_path[640];
        std::snprintf(audit_path, sizeof(audit_path),
                      "%s/rank_%d_pid_%d.chainout_launch_audit.json", g_work.audit_out_dir,
                      g_work.rank, g_work.pid);
        const int afd = open(audit_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (afd >= 0) {
            char abuf[1024];
            const int an = std::snprintf(
                abuf, sizeof(abuf),
                "{\"rank\":%d,\"pid\":%d,\"api\":\"%s\",\"kernel\":\"%s\","
                "\"iters\":%u,\"kernel_iters_arg\":%u,\"runtime_nonce\":%u,\"launch_seq\":%d,"
                "\"host_enter_ns\":%llu,\"host_exit_ns\":%llu,\"launch_rc\":%d,"
                "\"binary_sha256\":\"%s\"}\n",
                g_work.rank, g_work.pid, g_work.launch_api_name, kKernelName, iters,
                g_work.kernel_iters_arg, runtime_nonce,
                launch_seq, static_cast<unsigned long long>(enter),
                static_cast<unsigned long long>(exit), static_cast<int>(rc),
                g_work.binary_sha256);
            if (an > 0) {
                write(afd, abuf, static_cast<size_t>(an));
            }
            close(afd);
        }
    }
    if (rc != 0) {
        return static_cast<int32_t>(rc);
    }
    std::vector<uint32_t> host_scratch(g_work.scratch_elems);
    rc = sym_memcpy(host_scratch.data(), g_work.scratch_bytes, g_work.scratch_dev,
                    g_work.scratch_bytes, ACL_MEMCPY_DEVICE_TO_HOST);
    if (rc != 0) {
        if (out_launch_rc) {
            *out_launch_rc = static_cast<int32_t>(rc);
        }
        return static_cast<int32_t>(rc);
    }
    for (uint32_t i = 0; i < out_elems; ++i) {
        out_scratch[i] = host_scratch[i];
    }
    return 0;
}

void acl_event_work_write_audit(void) {
    if (g_work.audit_out_dir[0] == '\0') {
        return;
    }
    char path[640];
    std::snprintf(path, sizeof(path), "%s/rank_%d_pid_%d.device_work_audit.json",
                  g_work.audit_out_dir, g_work.rank, g_work.pid);
    const int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        return;
    }
    char buf[2048];
    const int n = std::snprintf(
        buf, sizeof(buf),
        "{\"rank\":%d,\"pid\":%d,\"arm_tid\":%u,\"record_issuing_tid\":%u,"
        "\"target_record_ordinal\":%d,\"target_wait_ordinal\":%d,"
        "\"inject_site\":\"%s\",\"trigger_record_preload_cs\":%llu,"
        "\"trigger_wait_preload_cs\":%llu,"
        "\"raw_stream\":%llu,\"requested_iters\":%u,\"block_dim\":%u,"
        "\"scratch_bytes\":%u,\"launch_api\":\"%s\",\"launch_rc\":%d,"
        "\"launch_count\":%d,\"real_record_rc\":%d,\"real_wait_rc\":%d,"
        "\"host_enter_ns\":%llu,\"host_exit_ns\":%llu,\"binary_sha256\":\"%s\","
        "\"inject_failed\":%d}\n",
        g_work.rank, g_work.pid, g_work.arm_tid.load(std::memory_order_relaxed),
        g_work.record_issuing_tid.load(std::memory_order_relaxed),
        g_work.target_record_ordinal.load(std::memory_order_relaxed),
        g_work.target_wait_ordinal.load(std::memory_order_relaxed),
        InjectSiteIsAfterWait() ? "AFTER_SUCCESSFUL_TARGET_WAIT" : "BEFORE_RECORD",
        static_cast<unsigned long long>(
            g_work.trigger_record_preload_cs.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(
            g_work.trigger_wait_preload_cs.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(g_work.last_raw_stream.load(std::memory_order_relaxed)),
        g_work.requested_iters.load(std::memory_order_relaxed), g_work.block_dim,
        g_work.scratch_bytes, g_work.launch_api_name, g_work.launch_rc.load(std::memory_order_relaxed),
        g_work.launch_count.load(std::memory_order_relaxed),
        g_work.real_record_rc.load(std::memory_order_relaxed),
        g_work.real_wait_rc.load(std::memory_order_relaxed),
        static_cast<unsigned long long>(g_work.host_enter_ns.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(g_work.host_exit_ns.load(std::memory_order_relaxed)),
        g_work.binary_sha256, g_work.inject_failed.load(std::memory_order_relaxed));
    if (n > 0) {
        write(fd, buf, static_cast<size_t>(n));
    }
    close(fd);
}

}  // extern "C"
