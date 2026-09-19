/* x86_64 gas: jmp through symver trampolines to libc dlsym/dlvsym. */
.text
.globl real_dlsym
.globl real_dlvsym
.type real_dlsym, @function
.type real_dlvsym, @function

.globl glibc_dlsym
.globl glibc_dlvsym

real_dlsym:
    endbr64
    jmp glibc_dlsym

real_dlvsym:
    endbr64
    jmp glibc_dlvsym

.symver glibc_dlsym, dlsym@GLIBC_2.2.5
.symver glibc_dlvsym, dlvsym@GLIBC_2.2.5
