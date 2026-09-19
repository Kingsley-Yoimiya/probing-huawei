#define _GNU_SOURCE
#include <dlfcn.h>
#include <elf.h>
#include <link.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

typedef void* (*DlsymFn)(void*, const char*);
typedef void* (*DlvsymFn)(void*, const char*, const char*);

static DlsymFn libc_dlsym_fn = NULL;
static DlvsymFn libc_dlvsym_fn = NULL;
static uintptr_t libc_base = 0;

static int FindLibcPhdr(struct dl_phdr_info* info, size_t size, void* data) {
    (void)size;
    (void)data;
    if (info->dlpi_name != NULL && strstr(info->dlpi_name, "libc.so") != NULL) {
        libc_base = (uintptr_t)info->dlpi_addr;
        return 1;
    }
    return 0;
}

#if defined(__x86_64__)
/* Verified on container glibc 2.34 via readelf -Ws libc.so.6. */
#define LIBC_DLSYM_OFF 0x90740u
#define LIBC_DLVSYM_OFF 0x90840u
static void ApplyOffsetFallback(void) {
    if (libc_base == 0) {
        return;
    }
    if (libc_dlsym_fn == NULL) {
        libc_dlsym_fn = (DlsymFn)(libc_base + LIBC_DLSYM_OFF);
    }
    if (libc_dlvsym_fn == NULL) {
        libc_dlvsym_fn = (DlvsymFn)(libc_base + LIBC_DLVSYM_OFF);
    }
}
#else
static void ApplyOffsetFallback(void) {}
#endif

static void EnsureBootstrap(void) {
    if (libc_dlsym_fn != NULL && libc_dlvsym_fn != NULL) {
        return;
    }
    if (libc_base == 0) {
        dl_iterate_phdr(FindLibcPhdr, NULL);
    }
    ApplyOffsetFallback();
}

__attribute__((constructor(101)))
static void BootstrapLibcResolver(void) {
    EnsureBootstrap();
}

__attribute__((visibility("default")))
void* real_dlsym(void* handle, const char* symbol) {
    EnsureBootstrap();
    if (libc_dlsym_fn != NULL) {
        return libc_dlsym_fn(handle, symbol);
    }
    return NULL;
}

__attribute__((visibility("default")))
void* real_dlvsym(void* handle, const char* symbol, const char* version) {
    EnsureBootstrap();
    if (libc_dlvsym_fn != NULL) {
        return libc_dlvsym_fn(handle, symbol, version);
    }
    return NULL;
}
