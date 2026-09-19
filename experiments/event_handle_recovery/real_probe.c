#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>

extern void* real_dlsym(void* handle, const char* symbol);

int main(int argc, char** argv) {
    const char* sym = argv[1];
    fprintf(stderr, "probe %s\n", sym);
    void* s = real_dlsym(RTLD_DEFAULT, sym);
    fprintf(stderr, "real %s=%p\n", sym, s);
    return s ? 0 : 1;
}
