#define _GNU_SOURCE
/* Minimal bootstrap probe: dlsym aclrtCreateEvent must finish in <2s. */
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

typedef int (*FnCreate)(void**);

int main(void) {
    void* sym = dlsym(RTLD_DEFAULT, "aclrtCreateEvent");
    if (sym == NULL) {
        fprintf(stderr, "min_create: dlsym aclrtCreateEvent -> NULL\n");
        return 2;
    }
    printf("min_create dlsym sym=%p\n", sym);
    if (getenv("MIN_CREATE_CALL") != NULL) {
        void* evt = NULL;
        FnCreate fn = (FnCreate)sym;
        int rc = fn(&evt);
        printf("min_create call rc=%d evt=%p\n", rc, evt);
        return rc == 0 ? 0 : 1;
    }
    printf("min_create rc=0\n");
    return 0;
}
