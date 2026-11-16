/* Linux counterparts of the Win32 calls probe.c's patches and diagnostics use (the reverse of compat_win.h), so that
 * code stays one copy for both systems. Only what probe.c needs, with the Windows meaning: VirtualAlloc at an address
 * gives exactly that address or nothing, ReadProcessMemory on this process fails instead of faulting, CreateThread
 * starts a detached thread. Included by probe.c after the system headers, with _GNU_SOURCE. */
#ifndef BB_COMPAT_POSIX_H
#define BB_COMPAT_POSIX_H
#ifndef _WIN32
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/uio.h>
#include <time.h>
#include <unistd.h>

/* Types, sized so the format strings written for Windows (%lu for DWORD, %ld for LONG) still match. */
typedef unsigned long DWORD;
typedef long LONG;
typedef long long LONG64;
typedef unsigned long long ULONGLONG;
typedef unsigned long long DWORD64;
typedef size_t SIZE_T;
typedef uintptr_t DWORD_PTR;
typedef void *HANDLE;
typedef void *LPVOID;
typedef int BOOL;
typedef union { long long QuadPart; } LARGE_INTEGER;
#define WINAPI
#define FALSE 0
#define TRUE 1

/* Atomics (full barriers, as on Windows). */
#define InterlockedIncrement(p) __atomic_add_fetch((p), 1, __ATOMIC_SEQ_CST)
#define InterlockedExchange(p, v) __atomic_exchange_n((p), (v), __ATOMIC_SEQ_CST)
static inline LONG64 InterlockedCompareExchange64(volatile LONG64 *target, LONG64 value, LONG64 expected) {
    __atomic_compare_exchange_n(target, &expected, value, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    return expected; /* the value before, as on Windows */
}

/* Clocks: the performance counter in nanoseconds, the tick count in milliseconds, both monotonic. */
static inline long long compat_monotonic_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (long long)t.tv_sec * 1000000000ll + t.tv_nsec;
}
static inline BOOL QueryPerformanceCounter(LARGE_INTEGER *out) { out->QuadPart = compat_monotonic_ns(); return TRUE; }
static inline BOOL QueryPerformanceFrequency(LARGE_INTEGER *out) { out->QuadPart = 1000000000ll; return TRUE; }
static inline ULONGLONG GetTickCount64(void) { return (ULONGLONG)(compat_monotonic_ns() / 1000000); }
static inline void Sleep(DWORD ms) {
    struct timespec t = {(time_t)(ms / 1000), (long)(ms % 1000) * 1000000l};
    while (nanosleep(&t, &t) && errno == EINTR) {}
}
#define YieldProcessor() __builtin_ia32_pause()

/* Threads: started detached; the handle is only for CloseHandle, which does nothing. */
typedef DWORD (*LPTHREAD_START_ROUTINE)(LPVOID);
typedef struct { LPTHREAD_START_ROUTINE start; LPVOID arg; } CompatThreadStart;
static inline void *compat_thread_main(void *p) {
    CompatThreadStart s = *(CompatThreadStart *)p;
    free(p);
    s.start(s.arg);
    return NULL;
}
static inline HANDLE CreateThread(void *security, size_t stack, LPTHREAD_START_ROUTINE start, LPVOID arg, DWORD flags,
                                  DWORD *id) {
    (void)security; (void)stack; (void)flags; (void)id;
    CompatThreadStart *s = malloc(sizeof(*s));
    if (!s) return NULL;
    s->start = start; s->arg = arg;
    pthread_t thread;
    if (pthread_create(&thread, NULL, compat_thread_main, s)) { free(s); return NULL; }
    pthread_detach(thread);
    return (HANDLE)(uintptr_t)1;
}
static inline BOOL CloseHandle(HANDLE handle) { (void)handle; return TRUE; }

/* Memory. */
#define MEM_COMMIT 0x1000
#define MEM_RESERVE 0x2000
#define PAGE_NOACCESS PROT_NONE
#define PAGE_EXECUTE_READ (PROT_READ | PROT_EXEC)
#define PAGE_EXECUTE_READWRITE (PROT_READ | PROT_WRITE | PROT_EXEC)
static inline void *VirtualAlloc(void *address, size_t size, DWORD type, DWORD protect) {
    (void)type;
    void *p = mmap(address, size, (int)protect,
                   MAP_PRIVATE | MAP_ANONYMOUS | (address ? MAP_FIXED_NOREPLACE : 0), -1, 0);
    if (p == MAP_FAILED) return NULL;
    /* A kernel before 4.17 takes MAP_FIXED_NOREPLACE as a hint: anywhere else is no good either. */
    if (address && p != address) { munmap(p, size); return NULL; }
    return p;
}
/* Whole pages, as Windows does. The old protection is not known here: it is given as read, write and execute (what
 * the game's image has on Linux), so putting it back leaves the pages as the game had them. */
static inline BOOL VirtualProtect(void *address, size_t size, DWORD protect, DWORD *old) {
    const uintptr_t page = (uintptr_t)sysconf(_SC_PAGESIZE);
    const uintptr_t from = (uintptr_t)address & ~(page - 1);
    const uintptr_t to = ((uintptr_t)address + size + page - 1) & ~(page - 1);
    if (old) *old = PAGE_EXECUTE_READWRITE;
    return mprotect((void *)from, to - from, (int)protect) == 0;
}
static inline BOOL FlushInstructionCache(HANDLE process, const void *address, size_t size) {
    (void)process; (void)address; (void)size; /* x86-64 keeps the instruction cache coherent */
    return TRUE;
}
static inline DWORD GetLastError(void) { return (DWORD)errno; }

/* This process's memory, read without faulting on an unmapped address. */
static inline HANDLE GetCurrentProcess(void) { return NULL; }
static inline BOOL ReadProcessMemory(HANDLE process, const void *address, void *out, size_t size, SIZE_T *got) {
    (void)process;
    struct iovec local = {out, size}, remote = {(void *)address, size};
    const ssize_t n = process_vm_readv(getpid(), &local, 1, &remote, 1, 0);
    if (got) *got = n > 0 ? (SIZE_T)n : 0;
    return n == (ssize_t)size;
}

/* Standard error (one write per line, as the Windows code means it). */
#define STD_ERROR_HANDLE ((DWORD)-12)
static inline HANDLE GetStdHandle(DWORD which) { (void)which; return (HANDLE)(uintptr_t)2; }
static inline BOOL WriteFile(HANDLE file, const void *data, DWORD size, DWORD *written, void *overlapped) {
    (void)overlapped;
    const ssize_t n = write((int)(uintptr_t)file, data, size);
    if (written) *written = n > 0 ? (DWORD)n : 0;
    return n == (ssize_t)size;
}

/* LBPPC_PROCESS_AFFINITY (test): the cores of the calling thread here; threads started later inherit it. */
static inline BOOL SetProcessAffinityMask(HANDLE process, DWORD_PTR mask) {
    (void)process;
    cpu_set_t set;
    CPU_ZERO(&set);
    for (unsigned i = 0; i < 64 && i < CPU_SETSIZE; ++i) if (mask >> i & 1) CPU_SET(i, &set);
    return sched_setaffinity(0, sizeof(set), &set) == 0;
}
#endif
#endif
