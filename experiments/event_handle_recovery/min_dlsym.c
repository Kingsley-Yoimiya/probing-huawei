#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
int main(void) {
    fprintf(stderr, "start\n");
    void* s = dlsym(RTLD_DEFAULT, "aclrtCreateEvent");
    fprintf(stderr, "sym=%p\n", s);
    return s ? 0 : 1;
}
