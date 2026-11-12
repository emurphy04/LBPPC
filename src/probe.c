/* Minimal x86-64 native loader experiment. Not a PS4 emulator or game port. */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <ctype.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>
#include <math.h>
#include <cpuid.h>
#include "runtime.h"
#include "gpu/bbgpu.h"
#if !defined(__x86_64__) || !defined(__GNUC__)
#error This prototype requires x86-64 GCC or Clang (including MinGW).
#endif
#ifdef _WIN32
#include <windows.h>
#include <tlhelp32.h>
#include <psapi.h>
#include <timeapi.h>
#include <x86intrin.h>
#else
#include <sys/mman.h>
#include <malloc.h>
#include <unistd.h>
#include <signal.h>
#include <ucontext.h>
#include <fcntl.h>
#include <dlfcn.h>
#include <execinfo.h>
#include <sys/syscall.h>
#include <sys/uio.h>
#endif

typedef struct { uint64_t address, size, flags; } Segment;
typedef struct { uint64_t target, kind, value, addend; } Reloc;
static char (*names)[128];
static uint64_t import_count;
static unsigned char *image;
static uint64_t image_size_hint;
static size_t page_size;
typedef struct { uint64_t base, size, init, tls_address, tls_memsz, tls_filesz, tls_module; } LinkedModule;
static LinkedModule modules[16];
static uint64_t module_count;
static int entered_game;
static int gpu_enabled;
int vulkan_smoke(void);

static void fail(const char *message) { fprintf(stderr, "ERROR: %s\n", message); exit(1); }
static uint64_t read64(FILE *f) {
    unsigned char b[8];
    if (fread(b, 1, 8, f) != 8) fail("truncated boot file");
    uint64_t n = 0;
    for (int i = 7; i >= 0; --i) n = (n << 8) | b[i];
    return n;
}
static size_t round_page(size_t size) { return (size + page_size - 1) & ~(page_size - 1); }
/* BB_TRACE=prefix,prefix: imports whose names start with one of them log each call (name, first three
 * arguments; the first 30 calls of each, then every 1000th) before going on to their implementation. */
typedef struct { const char *name; unsigned long calls; } TraceTarget;
static ABI void trace_call(TraceTarget *t, uint64_t a, uint64_t b, uint64_t c) {
    unsigned long n=__atomic_add_fetch(&t->calls,1,__ATOMIC_RELAXED);
    static FILE *out;
    if (!out) { const char *f=getenv("BB_TRACE_FILE"); out=f ? fopen(f,"w") : stderr; if (!out) out=stderr; }
    /* The guest's return address: the first image address above this frame (the wrapper saved seven registers). */
    uint64_t from=(uint64_t)(uintptr_t)image;
    {
        const uint64_t *f=(const uint64_t *)__builtin_frame_address(0);
        for (int k=1;k<32;++k) if (f[k]-(uint64_t)(uintptr_t)image<0xe00000) { from=f[k]; break; } /* eboot code, not data */
    }
    if (n<=30 || n%1000==0)
        fprintf(out,"TRACE %s(0x%" PRIx64 ", 0x%" PRIx64 ", 0x%" PRIx64 ") #%lu from g%llx thread %lu\n",t->name,a,b,c,n,
                (unsigned long long)(from-(uint64_t)(uintptr_t)image),(unsigned long)GetCurrentThreadId());
}
static void *allocate(size_t size);
static uintptr_t trace_wrap(uintptr_t target, const char *name) {
    static const char *prefixes;
    static unsigned char *code;
    static size_t used, capacity;
    if (!prefixes) prefixes=getenv("BB_TRACE") ? getenv("BB_TRACE") : "";
    if (!*prefixes || !name) return target;
    int match=0;
    for (const char *p=prefixes; *p && !match;) {
        const char *end=strchr(p,',');
        size_t n=end ? (size_t)(end-p) : strlen(p);
        match=n && !strncmp(name,p,n);
        p=end ? end+1 : p+n;
    }
    if (!match) return target;
    if (!code || used+96>capacity) {
        capacity=1<<20; used=0;
#ifdef _WIN32
        code=VirtualAlloc(NULL,capacity,MEM_COMMIT|MEM_RESERVE,PAGE_EXECUTE_READWRITE);
#else
        code=mmap(NULL,capacity,PROT_READ|PROT_WRITE|PROT_EXEC,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
#endif
    }
    TraceTarget *t=calloc(1,sizeof(*t));
    t->name=name;
    unsigned char *s=code+used, *o=s;
    /* push rdi,rsi,rdx,rcx,r8,r9,rax (rsp 16-aligned again); rcx=rdx; rdx=rsi; rsi=rdi; rdi=t;
       call trace_call; pop in reverse; jmp target (r11 is scratch at a call). */
    static const unsigned char push[]={0x57,0x56,0x52,0x51,0x41,0x50,0x41,0x51,0x50};
    memcpy(o,push,sizeof(push)); o+=sizeof(push);
    *o++=0x48; *o++=0x89; *o++=0xd1; /* mov rcx,rdx */
    *o++=0x48; *o++=0x89; *o++=0xf2; /* mov rdx,rsi */
    *o++=0x48; *o++=0x89; *o++=0xfe; /* mov rsi,rdi */
    *o++=0x48; *o++=0xbf; memcpy(o,&t,8); o+=8; /* mov rdi,t */
    uintptr_t fn=(uintptr_t)trace_call;
    *o++=0x48; *o++=0xb8; memcpy(o,&fn,8); o+=8; /* mov rax,trace_call */
    *o++=0xff; *o++=0xd0; /* call rax */
    static const unsigned char pop[]={0x58,0x41,0x59,0x41,0x58,0x59,0x5a,0x5e,0x5f};
    memcpy(o,pop,sizeof(pop)); o+=sizeof(pop);
    *o++=0x49; *o++=0xbb; memcpy(o,&target,8); o+=8; /* mov r11,target */
    *o++=0x41; *o++=0xff; *o++=0xe3; /* jmp r11 */
    used+=(size_t)((o-s+15)&~15);
    return (uintptr_t)s;
}
/* BB_HOOK=offset:length,... (diagnostics): at those guest offsets the first `length` bytes (whole
 * instructions, at least 5, no rip-relative ones) become a jump to a stub that logs the first two arguments,
 * the int each points to (when readable) and the return address, runs the moved bytes and jumps back. */
static volatile LONG watch_hits;
/* LBPPC_PROFILE=start,seconds (diagnostics): from `start` seconds after launch, for `seconds`, every thread is
 * sampled about once a millisecond; samples in the image walk the guest frame-pointer chain. At the end, per thread
 * with samples: the call sites (return addresses, image offsets) by inclusive count, deepest frame first per line. */
typedef struct { DWORD tid; uint64_t samples; uint32_t nsites; uint64_t site[4096]; uint32_t hits[4096]; } ProfThread;
static void prof_add(ProfThread *t, uint64_t site) {
    for (uint32_t i=0;i<t->nsites;++i) if (t->site[i]==site) { ++t->hits[i]; return; }
    if (t->nsites<4096) { t->site[t->nsites]=site; t->hits[t->nsites++]=1; }
}
static DWORD WINAPI profile_threads(LPVOID arg) {
    (void)arg;
    const char *spec=getenv("LBPPC_PROFILE");
    char *e; unsigned start=(unsigned)strtoul(spec,&e,0), span=*e==',' ? (unsigned)strtoul(e+1,NULL,0) : 10;
    Sleep(start*1000);
    static ProfThread threads[64], waits[64]; int nthreads=0, nwaits=0;
    const uint64_t lo=(uint64_t)(uintptr_t)image, hi=lo+image_size_hint;
    fprintf(stderr,"PROFILE: sampling %u s\n",span);
    /* The process's threads are listed (and their handles opened) twice a second, not on every pass: listing
       and opening ~100 threads each time held sampling to about 12 passes a second. */
    static struct { DWORD tid; HANDLE handle; } known[256]; int nknown=0;
    ULONGLONG end=GetTickCount64()+span*1000ull, listed=0; uint64_t passes=0;
    while (GetTickCount64()<end) {
        ++passes;
        if (GetTickCount64()-listed>=500) {
            listed=GetTickCount64();
            for (int k=0;k<nknown;++k) CloseHandle(known[k].handle);
            nknown=0;
            HANDLE snapshot=CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD,0);
            THREADENTRY32 entry={.dwSize=sizeof(entry)};
            for (BOOL more=Thread32First(snapshot,&entry); more && nknown<256; more=Thread32Next(snapshot,&entry)) {
                if (entry.th32OwnerProcessID!=GetCurrentProcessId() || entry.th32ThreadID==GetCurrentThreadId()) continue;
                HANDLE h=OpenThread(THREAD_SUSPEND_RESUME|THREAD_GET_CONTEXT,FALSE,entry.th32ThreadID);
                if (h) { known[nknown].tid=entry.th32ThreadID; known[nknown++].handle=h; }
            }
            CloseHandle(snapshot);
        }
        for (int k=0;k<nknown;++k) {
            HANDLE thread=known[k].handle;
            const DWORD tid=known[k].tid;
            if (SuspendThread(thread)!=(DWORD)-1) {
                CONTEXT c={.ContextFlags=CONTEXT_CONTROL|CONTEXT_INTEGER};
                /* LBPPC_PROFILE_HOST=1: also samples taken in the runtime (a guest loop around a host call): the
                   frame-pointer chain is followed through the runtime's frames into the guest's. */
                static int host_frames=-1;
                if (host_frames<0) host_frames=getenv("LBPPC_PROFILE_HOST")!=NULL;
                int in_image=0;
                if (host_frames && GetThreadContext(thread,&c) && (c.Rip<lo || c.Rip>=hi)) {
                    uint64_t rbp=c.Rbp;
                    for (int d=0; d<16 && rbp>c.Rsp && rbp-c.Rsp<(16u<<20); ++d) {
                        uint64_t frame[2]; SIZE_T got;
                        if (!ReadProcessMemory(GetCurrentProcess(),(void *)(uintptr_t)rbp,frame,16,&got)) break;
                        if (frame[1]>=lo && frame[1]<hi) { in_image=1; break; }
                        if (frame[0]<=rbp) break;
                        rbp=frame[0];
                    }
                }
                /* LBPPC_PROFILE_WAIT=1: a sample outside the game's code (waiting in the runtime or in Windows,
                   where frame pointers are not kept) counts for the first game address found on the stack, the
                   game function that made the call. */
                static int wait_sites=-1;
                if (wait_sites<0) wait_sites=getenv("LBPPC_PROFILE_WAIT")!=NULL;
                if (wait_sites && !in_image && GetThreadContext(thread,&c) && (c.Rip<lo || c.Rip>=hi)) {
                    /* Windows' and the runtime's code have unwind tables: unwound frame by frame until the return
                       address is in the game's code (which has none). */
                    CONTEXT u=c;
                    uint64_t site=0;
                    for (int d=0; d<64; ++d) {
                        if (u.Rip>=lo && u.Rip<hi) { site=u.Rip; break; }
                        DWORD64 base=0;
                        PRUNTIME_FUNCTION fn=RtlLookupFunctionEntry(u.Rip,&base,NULL);
                        if (!fn) { /* a leaf: the return address is on top */
                            if (IsBadReadPtr((void *)(uintptr_t)u.Rsp,8)) break;
                            u.Rip=*(DWORD64 *)(uintptr_t)u.Rsp; u.Rsp+=8;
                        } else {
                            PVOID data=NULL; DWORD64 frame=0;
                            RtlVirtualUnwind(UNW_FLAG_NHANDLER,base,u.Rip,fn,&u,&data,&frame,NULL);
                        }
                        if (!u.Rip) break;
                    }
                    int wi=0;
                    while (wi<nwaits && waits[wi].tid!=tid) ++wi;
                    if (wi==nwaits && nwaits<64) { waits[nwaits].tid=tid; ++nwaits; }
                    if (wi<64) { ++waits[wi].samples; prof_add(&waits[wi],site ? site-lo : 0); }
                }
                if (GetThreadContext(thread,&c) && ((c.Rip>=lo && c.Rip<hi) || in_image)) {
                    int ti=0;
                    while (ti<nthreads && threads[ti].tid!=tid) ++ti;
                    if (ti==nthreads && nthreads<64) { threads[nthreads].tid=tid; ++nthreads; }
                    if (ti<64) {
                        ProfThread *t=&threads[ti];
                        ++t->samples;
                        if (c.Rip>=lo && c.Rip<hi) prof_add(t,c.Rip-lo);
                        uint64_t rbp=c.Rbp, seen[48]; int depth=0, host=!(c.Rip>=lo && c.Rip<hi);
                        while (depth<48 && rbp>c.Rsp && rbp-c.Rsp<(16u<<20)) {
                            uint64_t frame[2]; SIZE_T got;
                            if (!ReadProcessMemory(GetCurrentProcess(),(void *)(uintptr_t)rbp,frame,16,&got)) break;
                            if (frame[1]<lo || frame[1]>=hi) {
                                if (!host || frame[0]<=rbp) break; /* runtime frames before the first guest one */
                                rbp=frame[0]; continue;
                            }
                            host=0;
                            int dup=0;
                            for (int k=0;k<depth;++k) dup|=seen[k]==frame[1];
                            seen[depth++]=frame[1];
                            if (!dup) prof_add(t,frame[1]-lo);
                            if (frame[0]<=rbp) break;
                            rbp=frame[0];
                        }
                    }
                }
                ResumeThread(thread);
            }
        }
        Sleep(1);
    }
    for (int k=0;k<nknown;++k) CloseHandle(known[k].handle);
    fprintf(stderr,"PROFILE: done, %llu passes, %d threads in the image (image %llx size %llx)\n",
            (unsigned long long)passes,nthreads,(unsigned long long)lo,(unsigned long long)image_size_hint);
    for (int ti=0;ti<nthreads;++ti) {
        ProfThread *t=&threads[ti];
        if (t->samples<5) continue;
        typedef HRESULT (WINAPI *GetDescription)(HANDLE,PWSTR *);
        GetDescription get=(GetDescription)(void *)GetProcAddress(GetModuleHandleA("kernel32.dll"),"GetThreadDescription");
        char name[96]="";
        HANDLE h=OpenThread(THREAD_QUERY_LIMITED_INFORMATION,FALSE,t->tid);
        PWSTR wide=NULL;
        if (h && get && SUCCEEDED(get(h,&wide)) && wide) {
            WideCharToMultiByte(CP_UTF8,0,wide,-1,name,sizeof(name),NULL,NULL);
            LocalFree(wide);
        }
        if (h) CloseHandle(h);
        fprintf(stderr,"PROFILE thread %lu '%s': %llu samples (%.1f%% of passes)\n",t->tid,name,
                (unsigned long long)t->samples,passes ? 100.0*t->samples/passes : 0.0);
        for (int shown=0;shown<60;++shown) {
            uint32_t best=0; int bi=-1;
            for (uint32_t i=0;i<t->nsites;++i) if (t->hits[i]>best) { best=t->hits[i]; bi=(int)i; }
            if (bi<0) break;
            fprintf(stderr,"  %5.1f%%  0x%llx\n",100.0*best/t->samples,(unsigned long long)t->site[bi]);
            t->hits[bi]=0;
        }
    }
    for (int wi=0;wi<nwaits;++wi) {
        ProfThread *w=&waits[wi];
        if (w->samples<20) continue;
        fprintf(stderr,"PROFILE thread %lu outside the game's code: %llu samples (%.1f%% of passes), called from\n",
                w->tid,(unsigned long long)w->samples,passes ? 100.0*w->samples/passes : 0.0);
        for (int shown=0;shown<12;++shown) {
            uint32_t best=0; int bi=-1;
            for (uint32_t i=0;i<w->nsites;++i) if (w->hits[i]>best) { best=w->hits[i]; bi=(int)i; }
            if (bi<0) break;
            fprintf(stderr,"  %5.1f%%  0x%llx\n",100.0*best/w->samples,(unsigned long long)w->site[bi]);
            w->hits[bi]=0;
        }
    }
    return 0;
}
/* LBPPC_FPTRAP=seconds (diagnostics): after that delay, the SSE invalid-operation and divide-by-zero exceptions are
 * unmasked on every thread, again every 100 ms; vectored_fault logs each instruction that raises one (once per
 * site), masks them on that thread and lets the instruction run again (its usual NaN or infinity). */
static DWORD WINAPI fptrap_threads(LPVOID arg) {
    Sleep((DWORD)(uintptr_t)arg*1000);
    fputs("FPTRAP: armed\n",stderr);
    for (;;) {
        HANDLE snapshot=CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD,0);
        THREADENTRY32 entry={.dwSize=sizeof(entry)};
        for (BOOL more=Thread32First(snapshot,&entry); more; more=Thread32Next(snapshot,&entry)) {
            if (entry.th32OwnerProcessID!=GetCurrentProcessId() || entry.th32ThreadID==GetCurrentThreadId()) continue;
            HANDLE thread=OpenThread(THREAD_SUSPEND_RESUME|THREAD_GET_CONTEXT|THREAD_SET_CONTEXT,FALSE,entry.th32ThreadID);
            if (!thread) continue;
            if (SuspendThread(thread)!=(DWORD)-1) {
                CONTEXT c={.ContextFlags=CONTEXT_CONTROL|CONTEXT_FLOATING_POINT};
                if (GetThreadContext(thread,&c) && (c.MxCsr&0x280)) {
                    c.MxCsr&=~(DWORD)0x2bf; c.FltSave.MxCsr=c.MxCsr;
                    static unsigned armed;
                    if (SetThreadContext(thread,&c) && ++armed<=3) {
                        CONTEXT v={.ContextFlags=CONTEXT_FLOATING_POINT};
                        GetThreadContext(thread,&v);
                        fprintf(stderr,"FPTRAP: thread %lu mxcsr now %08lx\n",entry.th32ThreadID,(unsigned long)v.MxCsr);
                    }
                }
                ResumeThread(thread);
            }
            CloseHandle(thread);
        }
        CloseHandle(snapshot);
        Sleep(100);
    }
    return 0;
}
static void describe_address(char *out, size_t size, uintptr_t address);
static int fptrap_report(EXCEPTION_POINTERS *info) {
    static uintptr_t sites[1024]; static unsigned hits[1024]; static unsigned char learnt[1024]; static long nsites;
    CONTEXT *c=info->ContextRecord;
    uintptr_t rip=(uintptr_t)c->Rip;
    /* With LBPPC_UNCAP_TRIGGER: sites trapping before the game goes to 60 FPS are learnt silently; after it,
       only new ones print, with the guest return addresses on the stack. */
    extern volatile int lbppc_nan_armed;
    const int learning=getenv("LBPPC_UNCAP_TRIGGER") && !lbppc_nan_armed;
    long i;
    for (i=0;i<nsites && sites[i]!=rip;++i) {}
    if (i==nsites && nsites<1024) { sites[i]=rip; nsites=i+1; hits[i]=0; learnt[i]=(unsigned char)learning; }
    if (i<1024 && !learnt[i] && ++hits[i]<=2) {
        char where[MAX_PATH+64], line[768];
        describe_address(where,sizeof(where),rip);
        int n=snprintf(line,sizeof(line),"FPTRAP %08lx at %s (hit %u, thread %lu) mxcsr %08lx xmm0 %08x xmm1 %08x xmm2 %08x",
                       info->ExceptionRecord->ExceptionCode,where,hits[i],GetCurrentThreadId(),(unsigned long)c->MxCsr,
                       *(unsigned *)&c->Xmm0,*(unsigned *)&c->Xmm1,*(unsigned *)&c->Xmm2);
        const uint64_t *sp=(const uint64_t *)(uintptr_t)c->Rsp;
        for (int k=0,found=0; k<512 && found<6 && n<(int)sizeof(line)-24; ++k) {
            uint64_t v; SIZE_T got;
            if (!ReadProcessMemory(GetCurrentProcess(),sp+k,&v,8,&got)) break;
            if (image && v-(uintptr_t)image<image_size_hint) { n+=snprintf(line+n,sizeof(line)-n," g%llx",(unsigned long long)(v-(uintptr_t)image)); ++found; }
        }
        line[n++]='\n';
        DWORD w; WriteFile(GetStdHandle(STD_ERROR_HANDLE),line,(DWORD)n,&w,NULL);
    }
    c->MxCsr|=0x280; c->FltSave.MxCsr=c->MxCsr;
    return 1;
}
/* BB_WATCH_ADDR=address: hardware write watchpoints on its first 32 bytes (four 8-byte slots) on every
 * thread, re-applied every 50 ms for new threads; each write is logged (vectored_fault, first 40). */
static uint64_t watch_address;
static DWORD WINAPI watch_all_threads(LPVOID arg) {
    (void)arg;
    /* BB_WATCH_DELAY=seconds: armed only after that long. */
    if (getenv("BB_WATCH_DELAY")) Sleep((DWORD)strtoul(getenv("BB_WATCH_DELAY"),NULL,0)*1000);
    for (;;) {
        /* BB_WATCH_IMAGE=offset: the watched address is that image offset (the image's base differs between runs). */
        if (getenv("BB_WATCH_IMAGE")) {
            if (!image) { Sleep(50); continue; }
            watch_address=(uint64_t)(uintptr_t)image+strtoull(getenv("BB_WATCH_IMAGE"),NULL,0);
        }
        /* Each thread is stopped only when it is new or the address changed: stopping every thread 20 times a second
           changed the game's timing enough to hide a race being watched for. */
        enum { ARMED_MAX=1024 };
        static struct { DWORD tid; uint64_t address; } armed[ARMED_MAX];
        static int armed_count;
        HANDLE snapshot=CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD,0);
        THREADENTRY32 entry={.dwSize=sizeof(entry)};
        for (BOOL more=Thread32First(snapshot,&entry); more; more=Thread32Next(snapshot,&entry)) {
            if (entry.th32OwnerProcessID!=GetCurrentProcessId() || entry.th32ThreadID==GetCurrentThreadId()) continue;
            int at=0;
            while (at<armed_count && armed[at].tid!=entry.th32ThreadID) ++at;
            if (at<armed_count && armed[at].address==watch_address) continue;
            if (at==armed_count && armed_count<ARMED_MAX) armed[armed_count++].tid=entry.th32ThreadID;
            if (at<armed_count) armed[at].address=watch_address;
            HANDLE thread=OpenThread(THREAD_SUSPEND_RESUME|THREAD_GET_CONTEXT|THREAD_SET_CONTEXT,FALSE,entry.th32ThreadID);
            if (!thread) continue;
            if (SuspendThread(thread)!=(DWORD)-1) {
                CONTEXT c={.ContextFlags=CONTEXT_DEBUG_REGISTERS};
                if (GetThreadContext(thread,&c) && c.Dr0!=watch_address) {
                    c.Dr0=watch_address; c.Dr1=watch_address+8; c.Dr2=watch_address+16; c.Dr3=watch_address+24;
                    /* BB_WATCH_ADDR2=address: the last two slots watch its first 16 bytes instead (two buffers). */
                    static uint64_t second=1;
                    if (second==1) second=getenv("BB_WATCH_ADDR2") ? strtoull(getenv("BB_WATCH_ADDR2"),NULL,0) : 0;
                    if (second) { c.Dr2=second; c.Dr3=second+8; }
                    /* L0-L3, each: break on write (01), 8 bytes (10); BB_WATCH_RW=1: on reads too (11). */
                    static int rw=-1;
                    if (rw<0) rw=getenv("BB_WATCH_RW")!=NULL;
                    const unsigned slot=rw ? 0xbu : 0x9u;
                    c.Dr7=0x55 | (slot<<16) | (slot<<20) | (slot<<24) | (slot<<28);
                    SetThreadContext(thread,&c);
                }
                ResumeThread(thread);
            }
            CloseHandle(thread);
        }
        CloseHandle(snapshot);
        Sleep(50);
    }
    return 0;
}
/* BB_WATCH=offset (with BB_HOOK at the same function): a hardware watchpoint on the 8 bytes at entry rsp -
 * offset (a stack canary slot: entry rsp - 0x38 for push rbp + canary at rbp-0x30), reporting every write. */
static void watch_slot(uint64_t entry_rsp) {
    const char *spec=getenv("BB_WATCH");
    if (!spec) return;
    CONTEXT c={.ContextFlags=CONTEXT_DEBUG_REGISTERS};
    c.Dr0=entry_rsp-strtoull(spec,NULL,0);
    c.Dr7=1 | (1u<<16) | (2u<<18); /* L0, break on write, 8 bytes */
    SetThreadContext(GetCurrentThread(),&c);
    char line[96];
    int n=snprintf(line,sizeof(line),"WATCH %p (thread %lu)\n",(void *)(uintptr_t)c.Dr0,GetCurrentThreadId());
    DWORD written;
    WriteFile(GetStdHandle(STD_ERROR_HANDLE),line,(DWORD)n,&written,NULL);
}
/* lbppc: LBP3's online sync check. Every 60 world frames each game hashes its players' matrices (0x1acaa0: world+0x120,
 * up to 4 players, ->8 ->0x40 ->0x10, matrix at +0x40) and sends frame and hash to the others (0x256cc0); the receiver
 * (0x269eb0) compares it with its own record of that frame and ends the game on a difference. Both are logged, to
 * compare the two players' logs. LBPPC_DIVERGE_KICK=0 (diagnostics) keeps the game going after a difference, with the
 * flag at +0x5e8 the game has for this, so that a test collects more than one. */
enum { SYNC_SEND_SITE=0x256d9a, SYNC_RECEIVE_SITE=0x269ed6 };
typedef struct { unsigned char id[0x24]; uint32_t where_a, where_b, frame, hash, session; } SyncRecord;
static volatile uint64_t sync_last_frame; /* the world frame of the last sync check (net log) */
static void sync_log_send(uint64_t world, uint64_t frame) {
    sync_last_frame=frame;
    const uint32_t count=*(uint32_t *)(uintptr_t)(world+0x128);
    const uint64_t *players=*(const uint64_t **)(uintptr_t)(world+0x120);
    printf("Sync: frame %llu, %u players\n",(unsigned long long)frame,count);
    for (uint32_t i=0;i<count && i<4;++i) {
        uint64_t p=*(uint64_t *)(uintptr_t)(players[i]+8);
        p=*(uint64_t *)(uintptr_t)(p+0x40);
        p=*(uint64_t *)(uintptr_t)(p+0x10);
        const uint32_t *m=(const uint32_t *)(uintptr_t)(p+0x40);
        const float *f=(const float *)m;
        printf("Sync:  player %u at %.6f %.6f %.6f;",i,(double)f[12],(double)f[13],(double)f[14]);
        for (int k=0;k<16;++k) printf(" %08x",m[k]);
        printf("\n");
    }
}
/* lbppc: the game ends the session on the first difference. One right after a level change can be a moment where the
 * worlds aren't aligned yet, so it takes SYNC_DIFFERENCES_TO_END checks in a row that differ (one every 2 s). Worlds that
 * have really split keep differing and end the session as before. Counted per check (frame), not per message: each game
 * also receives a report holding its own state, which always matches, and counting that as "match again" kept two split
 * worlds frozen in one session (2026-11-11). A check counts as matching only once a later check arrives with no
 * difference at it. Returns the flag the game reads: nonzero carries on past a difference. */
enum { SYNC_DIFFERENCES_TO_END=3 };
typedef struct { unsigned differences; int set_flag; unsigned frame; int frame_seen, frame_differs; } SyncTolerance;
/* outcome: 1 the same frame differs, 0 it matches, -1 no record of that frame yet. */
static unsigned char sync_tolerance_step(SyncTolerance *t, int outcome, unsigned char flag, unsigned frame) {
    if (outcome<0) return flag;
    if (!t->frame_seen || frame!=t->frame) {
        /* A new check: the last one, if nothing differed at it, means the worlds match again. */
        if (t->frame_seen && !t->frame_differs && t->differences) {
            printf("Sync: worlds match again after %u difference(s), at frame %u\n",t->differences,t->frame);
            t->differences=0;
            if (t->set_flag) { flag=0; t->set_flag=0; }
        }
        t->frame=frame; t->frame_seen=1; t->frame_differs=0;
    }
    if (outcome>0) {
        if (!t->frame_differs) {
            t->frame_differs=1;
            ++t->differences;
            printf("Sync: DIFFERENCE %u of %u at frame %u\n",t->differences,(unsigned)SYNC_DIFFERENCES_TO_END,frame);
            if (t->differences>=SYNC_DIFFERENCES_TO_END)
                printf("Sync: worlds still differ after %u checks, ending the session\n",t->differences);
        }
        if (t->differences<SYNC_DIFFERENCES_TO_END) {
            if (!flag) { flag=1; t->set_flag=1; }
        } else if (t->set_flag) { flag=0; t->set_flag=0; }
    }
    return flag;
}
static void sync_log_receive(uint64_t game, const SyncRecord *message) {
    static int kick=-1;
    static SyncTolerance tolerance;
    if (kick<0) { const char *v=getenv("LBPPC_DIVERGE_KICK"); kick=!(v && v[0]=='0'); }
    if (!kick) *(unsigned char *)(uintptr_t)(game+0x5e8)=1;
    else {
        /* As the game compares (0x269f10): the same frame, player and place, in this session. */
        const SyncRecord *records=*(const SyncRecord **)(uintptr_t)(game+0x550);
        const uint32_t records_count=*(uint32_t *)(uintptr_t)(game+0x558), session=*(uint32_t *)(uintptr_t)(game+0x564);
        static uint32_t last_session;
        if (session!=last_session) { tolerance=(SyncTolerance){0}; last_session=session; }
        int outcome=-1;
        if (!message->session || !session || message->session==session)
            for (uint32_t i=0;i<records_count && records;++i)
                if (records[i].frame==message->frame && !strncmp((const char *)records[i].id,(const char *)message->id,16) &&
                    records[i].where_a==message->where_a && records[i].where_b==message->where_b)
                    outcome=records[i].hash!=message->hash;
        unsigned char *flag=(unsigned char *)(uintptr_t)(game+0x5e8);
        *flag=sync_tolerance_step(&tolerance,outcome,*flag,message->frame);
    }
    printf("Sync: received frame %u hash %08x (where %x %x, session %u, id %.16s); ours:",message->frame,message->hash,
           message->where_a,message->where_b,message->session,(const char *)message->id);
    const SyncRecord *held=*(const SyncRecord **)(uintptr_t)(game+0x550);
    const uint32_t count=*(uint32_t *)(uintptr_t)(game+0x558);
    for (uint32_t i=0;i<count && held;++i)
        printf(" frame %u hash %08x%s",held[i].frame,held[i].hash,held[i].frame==message->frame ? (held[i].hash==message->hash ? " (same)" : " (DIFFERENT)") : "");
    printf("\n");
}
/* lbppc: LBPPC_POS_LOG=1 (test): at each world update (0x254450, the game in rdi) every player's position, how far
 * it moved since the last update and the real time between the two, to tell a jump in the simulation from a jump in
 * timing. The world is found from the game as the sync check finds it (0x256ce6-0x256d4c); read through
 * ReadProcessMemory, as the game may not hold a world yet. */
enum { WORLD_UPDATE=0x254450 };
static int read_guest(uint64_t address,void *out,size_t size) {
    SIZE_T read=0;
    return ReadProcessMemory(GetCurrentProcess(),(const void *)(uintptr_t)address,out,size,&read) && read==size;
}
static uint64_t world_of(uint64_t game) {
    uint64_t object=0, handle=0, bits18=0, bits20=0, base=0, world=0;
    uint32_t bits28=0;
    unsigned char kind=0;
    if (!read_guest(game+0x70,&object,8) || !object || !read_guest(object+0x45,&kind,1) || (kind&0xfe)!=4) return 0;
    if (!read_guest(object+0x68,&handle,8) || !handle) return 0;
    if (!read_guest(handle+0x18,&bits18,8) || !read_guest(handle+0x20,&bits20,8) || !read_guest(handle+0x28,&bits28,4) ||
        !read_guest(handle+0x50,&base,8)) return 0;
    if (!(bits18&4)) return 0;
    const uint64_t slot=(bits28&2) ? handle+0x58+(bits20&0xc)*2 : base+0x10;
    return read_guest(slot,&world,8) ? world : 0;
}
static void position_log(uint64_t game) {
    const uint64_t world=world_of(game);
    if (!world) return;
    static float last[4][3], last_step[4];
    static int have[4];
    static LARGE_INTEGER previous, hz;
    static unsigned long long step;
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    if (!hz.QuadPart) QueryPerformanceFrequency(&hz);
    const double ms=previous.QuadPart ? (double)(now.QuadPart-previous.QuadPart)*1000.0/(double)hz.QuadPart : 0.0;
    previous=now;
    ++step;
    /* The pad (player 0's controller): buttons held at any read since the last step, and the left stick. */
    extern uint32_t runtime_pad_buttons_seen;
    extern uint8_t runtime_pad_left_stick[2];
    const uint32_t buttons=__atomic_exchange_n(&runtime_pad_buttons_seen,0,__ATOMIC_RELAXED);
    const uint8_t stick[2]={runtime_pad_left_stick[0],runtime_pad_left_stick[1]};
    uint32_t count=0;
    uint64_t players=0;
    if (!read_guest(world+0x128,&count,4) || !read_guest(world+0x120,&players,8) || !count || count>4) return;
    for (uint32_t i=0;i<count;++i) {
        uint64_t p=0;
        float f[3];
        if (!read_guest(players+8*i,&p,8) || !read_guest(p+8,&p,8) || !read_guest(p+0x40,&p,8) || !read_guest(p+0x10,&p,8) ||
            !read_guest(p+0x40+12*4,f,sizeof(f))) continue;
        const float dx=f[0]-last[i][0], dy=f[1]-last[i][1], dz=f[2]-last[i][2];
        const float moved=have[i] ? sqrtf(dx*dx+dy*dy+dz*dz) : 0.0f;
        /* A jump: a step over 30 units and three times the one before (a fall speeds up gradually). */
        printf("Pos: step %llu, %.1f ms, player %u at %.1f %.1f %.1f, moved %.1f %.1f %.1f; pad 0x%x, stick %d %d%s\n",step,ms,
               i,(double)f[0],(double)f[1],(double)f[2],have[i] ? (double)dx : 0.0,have[i] ? (double)dy : 0.0,
               have[i] ? (double)dz : 0.0,buttons,stick[0]-128,stick[1]-128,moved>30.0f && moved>3.0f*last_step[i] ? " JUMP" : "");
        last_step[i]=moved;
        memcpy(last[i],f,sizeof(f));
        have[i]=1;
    }
}
/* lbppc: an SPU job in spu.prx (0x8f30) sorts a draw list: float sort keys with object indices, sorted in place by
 * 0x4a60 (a sorting network of vcmpltps, padded with FLT_MAX), then padded to a multiple of 32 with the key 1e20 and
 * the index 0x66666666 (0x9540). A comparison with NaN is false, so a NaN key leaves the order broken and a padding
 * index takes a real place: the render preparation (0x2a972e) and another job (spu.prx 0x668ff) then use
 * 0x66666666 as an object and fault. Above 30 FPS NaN keys turn up (LBPPC_SIM_DIVIDE). Just before the sort (0x9475:
 * keys in r14, count in r12d) each NaN key becomes the largest float below FLT_MAX: those objects sort last, before
 * the padding. LBPPC_SORT_KEYS_FIX=1, or with LBPPC_SIM_DIVIDE. */
enum { SORT_KEYS_SITE=0x16c9475 };
/* lbppc: the NaN keys' source. spu.prx's sweep job (0x95b0) reads each collision shape's 2D bounds (record fields
 * 0x18-0x2c, record in rax at 0xa145) into sort keys; on frames without a world step some shapes' bounds are still
 * unset (NaN). Each NaN bound becomes 0 there, so the keys stay numbers and the sort stays whole.
 * LBPPC_SHAPE_FIX=1, or with LBPPC_SIM_DIVIDE. */
enum { SHAPE_SITE=0x16ca145 };
static void shape_fix(uint64_t record) {
    uint32_t *f=(uint32_t *)(uintptr_t)(record+0x18);
    int fixed=0;
    for (int k=0;k<6;++k) if ((f[k]&0x7fffffffu)>0x7f800000u) { f[k]=0; fixed=1; }
    static volatile LONG shown;
    if (fixed && InterlockedIncrement(&shown)<=12) printf("Shape: unset bounds in record %p set to 0\n",(void *)(uintptr_t)record);
}
static void sort_keys_fix(uint64_t keys,uint64_t values,uint32_t count) {
    static volatile LONG fixed;
    uint32_t *key=(uint32_t *)(uintptr_t)keys;
    const uint32_t *value=(const uint32_t *)(uintptr_t)values;
    for (uint32_t i=0;i<count;++i)
        if ((key[i]&0x7fffffffu)>0x7f800000u) {
            if (InterlockedIncrement(&fixed)<=40)
                printf("Sort: NaN key %u of %u (value 0x%x)\n",i,count,value[i]);
            key[i]=0x7f7ffffeu;
        }
}
static ABI void hook_log(uint64_t offset, uint64_t a, uint64_t b, uint64_t slot, uint64_t c, uint64_t *frame) {
    /* frame: the stub's saved registers, below its rbp: flags, rax rcx rdx rsi rdi r8-r11 rbx r12-r15. */
    if (offset==WORLD_UPDATE) { position_log(frame[-6]); return; }
    if (offset==SORT_KEYS_SITE) { sort_keys_fix(frame[-14],frame[-15],(uint32_t)frame[-12]); return; }
    if (offset==SHAPE_SITE) { shape_fix(frame[-2]); return; }
    if (offset==SYNC_SEND_SITE) { sync_log_send(frame[-11],frame[-14]); return; }
    if (offset==SYNC_RECEIVE_SITE) { sync_log_receive(frame[-14],(const SyncRecord *)(uintptr_t)frame[-15]); return; }
    uint64_t rax=frame[-2];
    /* BB_HOOK_RET_SLOT=n: the return address is n slots up (a hook in an epilogue, before its pops). */
    static long ret_slot=-1;
    if (ret_slot<0) ret_slot=getenv("BB_HOOK_RET_SLOT") ? strtol(getenv("BB_HOOK_RET_SLOT"),NULL,0) : 0;
    uint64_t ret=*(uint64_t *)(uintptr_t)(slot+8*(uint64_t)ret_slot);
    watch_slot(slot);
    /* BB_HOOK_SLEEP_US=n (test): every hit sleeps n microseconds first (timing experiments). */
    static long sleep_us=-1;
    if (sleep_us<0) sleep_us=getenv("BB_HOOK_SLEEP_US") ? strtol(getenv("BB_HOOK_SLEEP_US"),NULL,0) : 0;
    if (sleep_us>0) {
        LARGE_INTEGER f, s, n; QueryPerformanceFrequency(&f); QueryPerformanceCounter(&s);
        if (sleep_us>=2000) Sleep((DWORD)(sleep_us/1000));
        else do { YieldProcessor(); QueryPerformanceCounter(&n); } while ((n.QuadPart-s.QuadPart)*1000000/f.QuadPart<sleep_us);
        if (getenv("BB_HOOK_QUIET")) return;
    }
    /* BB_HOOK_RANGE=lo:hi: only calls with one of the first three arguments in [lo, hi). */
    static uint64_t range_lo, range_hi;
    static int range_read;
    if (!range_read) {
        const char *r=getenv("BB_HOOK_RANGE");
        if (r) { char *end; range_lo=strtoull(r,&end,0); range_hi=*end==':' ? strtoull(end+1,NULL,0) : 0; }
        range_read=1;
    }
    if (range_hi && !((a>=range_lo && a<range_hi) || (b>=range_lo && b<range_hi) || (c>=range_lo && c<range_hi) ||
                      (rax>=range_lo && rax<range_hi))) return;
    /* BB_HOOK_FILTER=reg,offset,lo,hi (saved-register slot as in BB_HOOK_ARRAY: rax=1 ... rbx=10 ... r15=14,
       rbp=-1 for the stub's frame pointer, i.e. the guest rbp): only events where the qword at reg+offset is in [lo,hi). */
    if (getenv("BB_HOOK_FILTER")) {
        static int freg; static long long foff; static uint64_t flo, fhi; static int fread_done;
        if (!fread_done) {
            char *e=(char *)getenv("BB_HOOK_FILTER");
            freg=(int)strtol(e,&e,0); foff=strtoll(e+1,&e,0); flo=strtoull(e+1,&e,0); fhi=strtoull(e+1,NULL,0);
            fread_done=1;
        }
        uint64_t base=freg<0 ? frame[0] : frame[-(1+freg)];
        uint64_t v=0; SIZE_T fgot;
        if (!ReadProcessMemory(GetCurrentProcess(),(void *)(uintptr_t)(base+foff),&v,8,&fgot) || v<flo || v>=fhi) return;
    }
    /* BB_HOOK_FIXNAN=array_slot,count_slot (register slots as in BB_HOOK_FILTER): non-finite floats in the array
       become 1e19 (LBP3's sort keys: below the 1e20 of the padding, so a NaN key no longer sorts the padding's
       indices in front of real ones). Counted, the first few logged. */
    if (getenv("BB_HOOK_FIXNAN")) {
        char *e=(char *)getenv("BB_HOOK_FIXNAN");
        int ar=(int)strtol(e,&e,0), cr=(int)strtol(e+1,NULL,0);
        float *list=(float *)(uintptr_t)frame[-(1+ar)];
        uint32_t count=(uint32_t)frame[-(1+cr)];
        if (!list || count>1000000) return;
        static long fixed;
        for (uint32_t i=0;i<count;++i) {
            if (__builtin_isfinite(list[i])) continue;
            list[i]=1e19f;
            if (++fixed<=20 || fixed%1000==0) {
                char l[128]; int n=snprintf(l,sizeof(l),"FIXNAN #%ld: key %u of %u\n",fixed,i,count);
                DWORD w; WriteFile(GetStdHandle(STD_ERROR_HANDLE),l,(DWORD)n,&w,NULL);
            }
        }
        return;
    }
    /* BB_HOOK_SET_WATCH=reg,offset,add (register slot as in BB_HOOK_FILTER; with BB_WATCH_ADDR=0): each hit points
       the hardware watchpoints at *(reg+offset)+add (a field of an object found at run time), and logs nothing else. */
    if (getenv("BB_HOOK_SET_WATCH")) {
        char *e=(char *)getenv("BB_HOOK_SET_WATCH");
        int wr=(int)strtol(e,&e,0); long long woff=strtoll(e+1,&e,0); long long wadd=strtoll(e+1,NULL,0);
        uint64_t q=0; SIZE_T wgot;
        uint64_t holder=(wr<0 ? frame[0] : frame[-(1+wr)])+woff;
        if (ReadProcessMemory(GetCurrentProcess(),(void *)(uintptr_t)holder,&q,8,&wgot) && q) {
            uint64_t want=(q+wadd)&~(uint64_t)7;
            if (want!=watch_address) {
                watch_address=want;
                char l[128]; int n=snprintf(l,sizeof(l),"SET_WATCH 0x%llx\n",(unsigned long long)want);
                DWORD w; WriteFile(GetStdHandle(STD_ERROR_HANDLE),l,(DWORD)n,&w,NULL);
            }
        }
        return;
    }
    /* BB_HOOK_FLOATS=array_slot,count_slot[,count_offset] (register slots as in BB_HOOK_FILTER; count_slot -1: the
       u32 at guest rbp+count_offset): reports only hits whose float array has a NaN, an infinity or a value of 1e20
       or more (a sort key past the padding), with the first such entries. */
    if (getenv("BB_HOOK_FLOATS")) {
        char *e=(char *)getenv("BB_HOOK_FLOATS");
        int ar=(int)strtol(e,&e,0), cr=(int)strtol(e+1,&e,0);
        long long coff=*e==',' ? strtoll(e+1,NULL,0) : 0;
        const float *list=(const float *)(uintptr_t)frame[-(1+ar)];
        uint32_t count=cr==-1 ? *(const uint32_t *)(uintptr_t)(frame[0]+coff) : (uint32_t)frame[-(1+cr)];
        /* BB_HOOK_FLOATS_DEREF=off: the array is *(array_reg+off) and, with count_slot -2, the count is the u32 at
           array_reg+count_offset in bytes / 4 (a DMA list entry: size, then address). */
        if (getenv("BB_HOOK_FLOATS_DEREF")) {
            const uint64_t entry=frame[-(1+ar)];
            if (!entry) return;
            list=*(const float *const *)(uintptr_t)(entry+strtoll(getenv("BB_HOOK_FLOATS_DEREF"),NULL,0));
            if (cr==-2) count=*(const uint32_t *)(uintptr_t)(entry+coff)/4;
        }
        if (!list || count>1000000) return;
        char l[512]; int n=0, bad=0;
        for (uint32_t i=0; i<count; ++i) {
            const float v=list[i];
            /* FLT_MAX is the jobs' own padding: only NaN and infinities count (BB_HOOK_FLOATS_BIG=1: 1e20 and up too). */
            if (__builtin_isfinite(v) && (!getenv("BB_HOOK_FLOATS_BIG") || v<1e20f)) continue;
            if (!bad) n=snprintf(l,sizeof(l),"FLOATS 0x%llx: %u entries at %p, bad:",(unsigned long long)offset,count,(const void *)list);
            /* BB_HOOK_FLOATS_WATCH=1 (with BB_WATCH_ADDR=0): the first bad entry's address becomes the watched one. */
            if (!bad && getenv("BB_HOOK_FLOATS_WATCH") && !watch_address) watch_address=(uint64_t)(uintptr_t)&list[i]&~(uint64_t)7;
            if (++bad<=8) n+=snprintf(l+n,sizeof(l)-n," [%u]=%g(%08x)",i,v,*(const uint32_t *)&list[i]);
        }
        if (!bad) return;
        n+=snprintf(l+n,sizeof(l)-n," (%d bad)\n",bad);
        DWORD w; WriteFile(GetStdHandle(STD_ERROR_HANDLE),l,(DWORD)n,&w,NULL);
        return;
    }
    /* BB_HOOK_UNIQUE=array_slot,count_slot (register slots as in BB_HOOK_FILTER): the array's length and its number
       of distinct entries, one line per hit. */
    if (getenv("BB_HOOK_UNIQUE")) {
        char *e=(char *)getenv("BB_HOOK_UNIQUE");
        int ar=(int)strtol(e,&e,0), cr=(int)strtol(e+1,NULL,0);
        const uint64_t *list=(const uint64_t *)(uintptr_t)frame[-(1+ar)];
        uint32_t count=(uint32_t)frame[-(1+cr)];
        if (!list || count>200000) return;
        uint64_t *copy=malloc(count*sizeof(uint64_t)+8);
        if (!copy) return;
        memcpy(copy,list,count*sizeof(uint64_t));
        for (uint32_t i=1; i<count; ++i) { uint64_t v=copy[i]; uint32_t j=i; while (j && copy[j-1]>v) { copy[j]=copy[j-1]; --j; } copy[j]=v; }
        uint32_t unique=count ? 1 : 0;
        for (uint32_t i=1; i<count; ++i) unique+=copy[i]!=copy[i-1];
        free(copy);
        char l[160]; struct timespec now; clock_gettime(CLOCK_MONOTONIC,&now);
        int n=snprintf(l,sizeof(l),"UNIQUE [t=%lld] 0x%llx: %u entries, %u distinct\n",(long long)now.tv_sec,(unsigned long long)offset,count,unique);
        DWORD w; WriteFile(GetStdHandle(STD_ERROR_HANDLE),l,(DWORD)n,&w,NULL);
        return;
    }
    /* BB_HOOK_ARRAY=array_reg:count_reg (saved-register names, e.g. rbx:r15): checks every entry of that
       pointer array and reports only calls with an entry outside guest memory, with the array's contents. */
    static int array_reg=-1, count_reg=-1;
    if (array_reg==-1) {
        static const char *names[]={"flags","rax","rcx","rdx","rsi","rdi","r8","r9","r10","r11","rbx","r12","r13","r14","r15"};
        const char *spec=getenv("BB_HOOK_ARRAY");
        array_reg=count_reg=-2;
        if (spec) for (int i=0; i<15; ++i) {
            size_t n=strlen(names[i]);
            if (!strncmp(spec,names[i],n) && spec[n]==':') array_reg=i+1;
            const char *colon=strchr(spec,':');
            if (colon && !strcmp(colon+1,names[i])) count_reg=i+1;
        }
    }
    if (array_reg>0 && count_reg>0) {
        const uint64_t *list=(const uint64_t *)(uintptr_t)frame[-array_reg];
        /* BB_HOOK_ARRAY_DEREF=offset: the array's address is stored at register+offset;
           BB_HOOK_ARRAY_INDEX=n (saved-register slot as in BB_HOOK_ARRAY's names: rax=1 ... r15=14): it starts at that index. */
        if (getenv("BB_HOOK_ARRAY_DEREF")) {
            uint64_t holder=frame[-array_reg]+strtoull(getenv("BB_HOOK_ARRAY_DEREF"),NULL,0);
            list=*(const uint64_t **)(uintptr_t)holder;
            if (getenv("BB_HOOK_ARRAY_INDEX")) list+=(uint32_t)frame[-(1+atoi(getenv("BB_HOOK_ARRAY_INDEX")))];
        }
        uint64_t count=frame[-count_reg]&0xffffffff, bad=UINT64_MAX;
        static volatile long checked;
        InterlockedIncrement(&checked);
        static __thread int dr_reported;
        if (getenv("BB_WATCH_ADDR") && !dr_reported++) {
            CONTEXT dc={.ContextFlags=CONTEXT_DEBUG_REGISTERS};
            GetThreadContext(GetCurrentThread(),&dc);
            char l[160];
            int n=snprintf(l,sizeof(l),"DR check (thread %lu): Dr0=%llx Dr7=%llx\n",GetCurrentThreadId(),(unsigned long long)dc.Dr0,(unsigned long long)dc.Dr7);
            DWORD w; WriteFile(GetStdHandle(STD_ERROR_HANDLE),l,(DWORD)n,&w,NULL);
        }
        if (!list || count>4096) return;
        for (uint64_t i=0; i<count && bad==UINT64_MAX; ++i)
            if (list[i]<0x800000000ull || list[i]>=0xfc00000000ull) bad=i;
        if (bad==UINT64_MAX) return;
        { extern void runtime_memory_check_view(uintptr_t address); runtime_memory_check_view((uintptr_t)list); }
        char line[1024];
        int len=snprintf(line,sizeof(line),"ARRAY at 0x%llx (call %ld): %llu entries at %p, entry %llu = %llx (thread %lu):",
                         (unsigned long long)offset,checked,(unsigned long long)count,(void *)list,(unsigned long long)bad,
                         (unsigned long long)list[bad],GetCurrentThreadId());
        for (uint64_t i=0; i<count && i<48 && len<(int)sizeof(line)-20; ++i) len+=snprintf(line+len,sizeof(line)-len," %llx",(unsigned long long)list[i]);
        len+=snprintf(line+len,sizeof(line)-len,"\n");
        DWORD written;
        WriteFile(GetStdHandle(STD_ERROR_HANDLE),line,(DWORD)len,&written,NULL);
        return;
    }
    static volatile long count;
    long n=InterlockedIncrement(&count);
    static long max_lines=-1;
    if (max_lines<0) max_lines=getenv("BB_HOOK_MAX") ? strtol(getenv("BB_HOOK_MAX"),NULL,0) : 200;
    if (n>max_lines) return;
    char line[1024];
    int32_t va=0, vb=0;
    SIZE_T got;
    /* BB_HOOK_FIELD=offset: the int at that offset in the first argument instead of at its start. */
    static const char *field;
    if (!field) field=getenv("BB_HOOK_FIELD") ? getenv("BB_HOOK_FIELD") : "0";
    uint64_t at=a ? a+strtoull(field,NULL,0) : 0;
    int ra=at && ReadProcessMemory(GetCurrentProcess(),(void *)(uintptr_t)at,&va,4,&got) && got==4;
    int rb=b && ReadProcessMemory(GetCurrentProcess(),(void *)(uintptr_t)b,&vb,4,&got) && got==4;
    /* One write per event, the registers on the same line: lines of different threads cannot interleave. */
    struct timespec now; clock_gettime(CLOCK_MONOTONIC,&now);
    int len=snprintf(line,sizeof(line),"[t=%lld.%06ld] ",(long long)now.tv_sec,now.tv_nsec/1000);
    len+=snprintf(line+len,sizeof(line)-len,"HOOK #%ld 0x%llx(0x%llx [%s0x%x], 0x%llx [%s0x%x], 0x%llx) rax 0x%llx from guest 0x%llx (thread %lu)",
                     n,(unsigned long long)offset,(unsigned long long)a,ra?"":"?",(unsigned)va,(unsigned long long)b,
                     rb?"":"?",(unsigned)vb,(unsigned long long)c,(unsigned long long)rax,
                     (unsigned long long)(ret-(uintptr_t)image),GetCurrentThreadId());
    if (getenv("BB_HOOK_REGS"))
        len+=snprintf(line+len,sizeof(line)-len," | rbx=%llx rcx=%llx rdx=%llx rsi=%llx rdi=%llx r8=%llx r9=%llx r10=%llx r11=%llx r12=%llx r13=%llx r14=%llx r15=%llx",
                      (unsigned long long)frame[-11],(unsigned long long)frame[-3],(unsigned long long)frame[-4],
                      (unsigned long long)frame[-5],(unsigned long long)frame[-6],(unsigned long long)frame[-7],
                      (unsigned long long)frame[-8],(unsigned long long)frame[-9],(unsigned long long)frame[-10],
                      (unsigned long long)frame[-12],(unsigned long long)frame[-13],(unsigned long long)frame[-14],
                      (unsigned long long)frame[-15]);
    /* BB_HOOK_PEEK=reg,offset,index_reg,n (register slots as in BB_HOOK_FILTER): q=*(reg+offset); prints q+index*8
       and the n qwords there. */
    if (getenv("BB_HOOK_PEEK")) {
        char *e=(char *)getenv("BB_HOOK_PEEK");
        int preg=(int)strtol(e,&e,0); long long poff=strtoll(e+1,&e,0); int pidx=(int)strtol(e+1,&e,0); int pn=(int)strtol(e+1,NULL,0);
        uint64_t q=0; SIZE_T pgot;
        uint64_t holder=(preg<0 ? frame[0] : frame[-(1+preg)])+poff;
        /* BB_HOOK_PEEK_DIRECT=1: q is register+offset itself. */
        if (getenv("BB_HOOK_PEEK_DIRECT") || ReadProcessMemory(GetCurrentProcess(),(void *)(uintptr_t)holder,&q,8,&pgot)) {
            if (getenv("BB_HOOK_PEEK_DIRECT")) q=holder;
            uint64_t at=q+(pidx<0 ? 0 : 8*(uint64_t)(uint32_t)frame[-(1+pidx)]);
            len+=snprintf(line+len,sizeof(line)-len," | peek %llx->%llx:",(unsigned long long)holder,(unsigned long long)at);
            /* BB_HOOK_PEEK_DEREF=1: the qwords of the object the first entry points to instead. */
            if (getenv("BB_HOOK_PEEK_DEREF")) {
                uint64_t first=0;
                ReadProcessMemory(GetCurrentProcess(),(void *)(uintptr_t)at,&first,8,&pgot);
                len+=snprintf(line+len,sizeof(line)-len," *%llx:",(unsigned long long)first);
                at=first;
            }
            for (int i=0; i<pn && i<24 && len<(int)sizeof(line)-24; ++i) {
                uint64_t v=0;
                ReadProcessMemory(GetCurrentProcess(),(void *)(uintptr_t)(at+8*i),&v,8,&pgot);
                len+=snprintf(line+len,sizeof(line)-len," %llx",(unsigned long long)v);
            }
        }
    }
    /* BB_HOOK_STACK=n: the first n stack slots at the hook site ([rsp] is a function's return address). */
    static long stack_slots=-1;
    if (stack_slots<0) stack_slots=getenv("BB_HOOK_STACK") ? strtol(getenv("BB_HOOK_STACK"),NULL,0) : 0;
    for (long s=0; s<stack_slots && s<12 && len<(int)sizeof(line)-24; ++s)
        len+=snprintf(line+len,sizeof(line)-len,"%s%llx",s ? " " : " | stack ",(unsigned long long)((uint64_t *)(uintptr_t)slot)[s]);
    len+=snprintf(line+len,sizeof(line)-len,"\n");
    DWORD written;
    WriteFile(GetStdHandle(STD_ERROR_HANDLE),line,(DWORD)len,&written,NULL);
    /* BB_HOOK_DUMP=bytes: a hex dump of the first argument's object, once. */
    static int dumped;
    const char *dump=getenv("BB_HOOK_DUMP");
    /* BB_HOOK_DUMP_ARG=3: dump the third argument's object instead. */
    uint64_t dump_at=getenv("BB_HOOK_DUMP_ADDR") ? strtoull(getenv("BB_HOOK_DUMP_ADDR"),NULL,0) :
                     getenv("BB_HOOK_DUMP_ARG") && atoi(getenv("BB_HOOK_DUMP_ARG"))==3 ? c : a;
    if (dump && !dumped && dump_at) {
        dumped=1;
        size_t bytes=strtoul(dump,NULL,0);
        for (size_t at=0; at<bytes; at+=32) {
            uint64_t row[4]={0};
            if (!ReadProcessMemory(GetCurrentProcess(),(void *)(uintptr_t)(dump_at+at),row,sizeof(row),&got)) break;
            len=snprintf(line,sizeof(line),"  +%04zx: %016llx %016llx %016llx %016llx\n",at,(unsigned long long)row[0],
                         (unsigned long long)row[1],(unsigned long long)row[2],(unsigned long long)row[3]);
            WriteFile(GetStdHandle(STD_ERROR_HANDLE),line,(DWORD)len,&written,NULL);
        }
    }
    /* With BB_HOOK_RANGE: the 16 bytes before and after the third argument (an allocator's header). */
    uint64_t around[4];
    if (range_hi && c>=16 && ReadProcessMemory(GetCurrentProcess(),(void *)(uintptr_t)(c-16),around,sizeof(around),&got) &&
        got==sizeof(around)) {
        len=snprintf(line,sizeof(line),"  around 0x%llx: %016llx %016llx | %016llx %016llx\n",(unsigned long long)c,
                     (unsigned long long)around[0],(unsigned long long)around[1],(unsigned long long)around[2],
                     (unsigned long long)around[3]);
        WriteFile(GetStdHandle(STD_ERROR_HANDLE),line,(DWORD)len,&written,NULL);
    }
}
/* lbppc: LBPPC_RENDER_RES=WxH: LBP3 renders at that size instead of 1920x1080. Its video setup (0x5bed0) stores
 * the display size and pitch, and its render target setup (0x424790, 0x426dc0, 0x427940) creates every
 * screen-sized target with immediate sizes: full, double height, 2/3, 1/3, 1/4 and 1/6 of 1920x1080. Each is
 * scaled to the requested size (CUSA00473 1.28; checked against the original bytes, else nothing changes). */
static unsigned char *near_image_alloc(void);
static void render_res_install(void) {
    const char *spec=getenv("LBPPC_RENDER_RES");
    if (!spec || !*spec) return;
    unsigned w=0, h=0;
    if (sscanf(spec,"%ux%u",&w,&h)!=2 || w<640 || h<360 || w>7680 || h>4320) {
        fprintf(stderr,"LBPPC_RENDER_RES: '%s' is not WxH\n",spec); return;
    }
    /* 5120 wide stops the game (a corrupted shader or a fault while loading, every time); 4480 works. */
    if (w>4480) { fprintf(stderr,"LBPPC_RENDER_RES: %ux%u is wider than 4480, which LBP3 does not survive yet\n",w,h); return; }
    static const struct { uint32_t w_at, h_at; uint16_t w, h; } targets[]={
    {0x424bf6,0x424bfb,1920,1080}, {0x424c46,0x424c4b,1920,1080}, {0x4270ad,0x4270b2,1920,1080},
    {0x42711a,0x42711f,1920,1080}, {0x427a14,0x427a19,1920,1080}, {0x427a3f,0x427a44,1920,1080},
    {0x427a67,0x427a6c,480,270}, {0x427a8f,0x427a94,480,270}, {0x427b83,0x427b88,1920,1080},
    {0x427bab,0x427bb0,1920,1080}, {0x427bd3,0x427bd8,1920,1080}, {0x427bfe,0x427c03,1920,1080},
    {0x427c29,0x427c2e,1920,1080}, {0x427c51,0x427c56,1920,1080}, {0x427c79,0x427c7e,1920,1080},
    {0x427ca1,0x427ca6,1920,1080}, {0x427d1d,0x427d22,1280,720}, {0x427d3e,0x427d43,640,360},
    {0x427d62,0x427d67,320,180}, {0x427d8d,0x427d92,1920,1080}, {0x427db5,0x427dba,1920,1080},
    {0x427e91,0x427e96,1920,1080}, {0x427ebc,0x427ec1,1920,1080}, {0x427ef1,0x427ef6,1920,2160},
    {0x427f19,0x427f1e,1920,1080}, {0x427f4b,0x427f50,1920,2160}, {0x427f73,0x427f78,1920,1080},
    {0x427fce,0x427fd3,1920,1080}, {0x427ff6,0x427ffb,1920,1080}, {0x42809f,0x4280a4,1920,1080}
    };
    static const struct { uint32_t at, value; } display[]={{0x5bf85,0x780},{0x5bf93,0x438},{0x5bfa0,0x1e00}};
    /* The same setup also describes some of those targets as textures, to read them back (the screen copies the pod
       computer and pause menu fade from): 16-bit width and height, a 32-bit row pitch (1920*4) and one 32-bit
       height. Left at 1080p, they show a zoomed corner of the screen (a grey wash with a giant Sackboy). */
    static const struct { uint32_t at; uint16_t value; } texture16[]={
    {0x424c94,1920}, {0x424c9a,1080}, {0x424ce0,1920}, {0x424ce9,1080},
    {0x42704d,1920}, {0x427053,1080}, {0x4271ba,1920}, {0x4271c3,1080}
    };
    static const struct { uint32_t at, value; } texture32[]={{0x424c8c,0x1e00},{0x4271cb,0x1e00},{0x424bf1,0x438}};
    for (size_t i=0;i<sizeof(display)/sizeof(*display);++i) {
        uint32_t v; memcpy(&v,image+display[i].at,4);
        if (v!=display[i].value) { fprintf(stderr,"LBPPC_RENDER_RES: unexpected game code, not applied\n"); return; }
    }
    for (size_t i=0;i<sizeof(targets)/sizeof(*targets);++i) {
        uint32_t a, b; memcpy(&a,image+targets[i].w_at,4); memcpy(&b,image+targets[i].h_at,4);
        if (a!=targets[i].w || b!=targets[i].h) { fprintf(stderr,"LBPPC_RENDER_RES: unexpected game code, not applied\n"); return; }
    }
    for (size_t i=0;i<sizeof(texture16)/sizeof(*texture16);++i) {
        uint16_t v; memcpy(&v,image+texture16[i].at,2);
        if (v!=texture16[i].value) { fprintf(stderr,"LBPPC_RENDER_RES: unexpected game code, not applied\n"); return; }
    }
    for (size_t i=0;i<sizeof(texture32)/sizeof(*texture32);++i) {
        uint32_t v; memcpy(&v,image+texture32[i].at,4);
        if (v!=texture32[i].value) { fprintf(stderr,"LBPPC_RENDER_RES: unexpected game code, not applied\n"); return; }
    }
    /* The render targets of the second memory type come out of a region of fixed size (0x2441400 bytes, set at
       0x1517d): past it the game stops with a fatal error (0xa0020006). It grows with the pixel count. */
    uint32_t region; memcpy(&region,image+0x15180,4);
    if (region!=0x2441400) { fprintf(stderr,"LBPPC_RENDER_RES: unexpected game code, not applied\n"); return; }
    const double area=(double)w*h/(1920.0*1080.0);
    const uint64_t grown=((uint64_t)(0x2441400*(area>1.0 ? area : 1.0))+0xfffff)&~(uint64_t)0xfffff;
    if (grown>0xffffffffull) { fprintf(stderr,"LBPPC_RENDER_RES: %ux%u is too large\n",w,h); return; }
    /* The camera's constructor (0x3fdba0) stores its aspect, 16:9, beside its 60 degree vertical field of view: with the
       size's aspect the view widens (ultrawide) instead of stretching. The menus and HUD lay themselves out by it. */
    uint32_t aspect_bits; memcpy(&aspect_bits,image+0x3fdbc3,4);
    if (aspect_bits!=0x3fe38e39) { fprintf(stderr,"LBPPC_RENDER_RES: unexpected game code, not applied\n"); return; }
    const float aspect=(float)w/(float)h;
    /* Online the game also steps by the camera's aspect (who is on screen, pulling in a player left behind), and players'
       worlds must step alike ("The game diverged" when one was ultrawide). There the camera keeps 16:9 and only its
       projection (0x3fe160, which reads the aspect at 0x3fe17f) gets the screen's, from a stub that also redoes the
       comparison after the read. LBPPC_ASPECT_SPLIT=1/0 forces it on or off (tests). */
    const char *split_env=getenv("LBPPC_ASPECT_SPLIT"), *npid=getenv("BB_SHADNET_NPID");
    const int split=split_env ? split_env[0]=='1' : npid && *npid;
    static const unsigned char aspect_read[]={0xc5,0xfa,0x10,0x53,0x08, 0xc5,0xf8,0x2e,0xd1}; /* vmovss xmm2,[rbx+8]; vucomiss xmm2,xmm1 */
    unsigned char *stub=NULL;
    if (split && aspect>16.0f/9.0f+0.01f) {
        if (memcmp(image+0x3fe17f,aspect_read,sizeof(aspect_read))) fputs("LBPPC_RENDER_RES: unexpected camera code, the view stays 16:9 online\n",stderr);
        else if (!(stub=near_image_alloc())) fputs("LBPPC_RENDER_RES: no memory near the image, the view stays 16:9 online\n",stderr);
    }
    if (stub) {
        /* stub: vmovss xmm2,[rip+aspect]; vucomiss xmm2,xmm1; ret. Called in place of the read; ret leaves the flags. */
        unsigned char *o=stub;
        float *wide=(float *)(stub+32);
        *wide=aspect;
        *o++=0xc5; *o++=0xfa; *o++=0x10; *o++=0x15; { int32_t d=(int32_t)((unsigned char *)wide-(o+4)); memcpy(o,&d,4); o+=4; }
        memcpy(o,aspect_read+5,4); o+=4;
        *o++=0xc3;
        const int32_t rel=(int32_t)((intptr_t)stub-(intptr_t)(image+0x3fe17f+5));
        image[0x3fe17f]=0xe8; memcpy(image+0x3fe180,&rel,4);
        static const unsigned char nop4[]={0x0f,0x1f,0x40,0x00};
        memcpy(image+0x3fe184,nop4,4);
        printf("Runtime: online, the game steps with a 16:9 camera and the view is drawn at %.3f\n",aspect);
    } else {
        memcpy(image+0x3fdbc3,&aspect,4);
    }
    /* The 2D layer's canvas (menus, button prompts, subtitles), 1280x720 at 0x1060a04/0x1060a08: its projection
       (0x40ff8b) and the layout read it, so at the screen's aspect the layer widens instead of stretching. */
    /* LBPPC_UI_STRETCH=1 keeps the stretched layer. */
    const char *stretch=getenv("LBPPC_UI_STRETCH");
    if (aspect>16.0f/9.0f+0.01f && !(stretch && stretch[0]=='1')) {
        int32_t canvas[2]; memcpy(canvas,image+0x1060a04,8);
        if (canvas[0]!=1280 || canvas[1]!=720) fprintf(stderr,"2D canvas: unexpected game data, not widened\n");
        else {
            canvas[0]=(int32_t)lroundf(720.0f*aspect);
            memcpy(image+0x1060a04,canvas,4);
            printf("Runtime: 2D canvas widened to %dx%d\n",canvas[0],canvas[1]);
            /* Some of the layout doesn't read the canvas but has the 1280-wide screen's centre (640) or right edge
               built in: the loading screen's text, button prompts. They move to the wide canvas's.
               LBPPC_UI_CENTRE_FIX=0 leaves them (diagnostics). */
            const char *fix=getenv("LBPPC_UI_CENTRE_FIX");
            static const struct { uint32_t at; float value; } fixed[]={
                {0xe675c0,640.0f},  /* the screen's centre, returned by 0x27eca0 */
                {0xe8f668,640.0f},  /* a 1200-wide text box's centre (0x50879d) */
                {0xe90394,640.0f},  /* 0x513298 and three more */
                {0xe903b8,1280.0f}, /* 0x513831 */
                {0xe90448,640.0f},  /* 0x5192c1 */
                {0xe9046c,640.0f},  /* 0x5197a1 */
            };
            int ok=1;
            for (size_t i=0;i<sizeof(fixed)/sizeof(*fixed);++i) if (memcmp(image+fixed[i].at,&fixed[i].value,4)) ok=0;
            if (!ok) fprintf(stderr,"2D canvas: unexpected game data, centred layout not moved\n");
            else if (!(fix && fix[0]=='0')) {
                const float wide=(float)canvas[0];
                for (size_t i=0;i<sizeof(fixed)/sizeof(*fixed);++i) {
                    const float v=fixed[i].value*wide/1280.0f;
                    memcpy(image+fixed[i].at,&v,4);
                }
            }
        }
    }
    region=(uint32_t)grown;
    memcpy(image+0x15180,&region,4);
    const uint32_t pitch=w*4;
    memcpy(image+0x5bf85,&w,4); memcpy(image+0x5bf93,&h,4); memcpy(image+0x5bfa0,&pitch,4);
    for (size_t i=0;i<sizeof(texture16)/sizeof(*texture16);++i) {
        const uint16_t v=(uint16_t)(texture16[i].value==1920 ? w : h);
        memcpy(image+texture16[i].at,&v,2);
    }
    memcpy(image+0x424c8c,&pitch,4); memcpy(image+0x4271cb,&pitch,4); memcpy(image+0x424bf1,&h,4);
    /* LBPPC_RENDER_RES_MASK=bits (diagnostics): only the targets with their bit set are scaled. */
    const uint64_t mask=getenv("LBPPC_RENDER_RES_MASK") ? strtoull(getenv("LBPPC_RENDER_RES_MASK"),NULL,0) : ~0ull;
    for (size_t i=0;i<sizeof(targets)/sizeof(*targets);++i) {
        if (!(mask>>i&1)) continue;
        uint32_t tw=(uint32_t)(((uint64_t)targets[i].w*w+960)/1920), th=(uint32_t)(((uint64_t)targets[i].h*h+540)/1080);
        /* The smaller targets (1280x720 and below at 1080p) grow with the size but never shrink below it: the game
           cuts a centred 256x256 square from one of them (0x4014f0, picked by the screen's height), and at 720p
           the 640x360 one became 427x240, the offset went negative and the copy read past it. */
        if (targets[i].w<1920) { if (tw<targets[i].w) tw=targets[i].w; if (th<targets[i].h) th=targets[i].h; }
        memcpy(image+targets[i].w_at,&tw,4); memcpy(image+targets[i].h_at,&th,4);
    }
    printf("Runtime: rendering at %ux%u (aspect %.3f)\n",w,h,aspect);
}
/* lbppc: with a custom server (LBPPC_SERVER), the key LBP3 signs its requests and checks the server's replies with
 * (HMAC-SHA1, the X-Digest-A/B headers) becomes "CustomServerDigest", the one custom servers (Refresh, Lighthouse)
 * use, as PS3 patchers do. The game builds the 18-character key from three immediates in 0x1ec60 (CUSA00473 1.28;
 * checked against the original bytes, else nothing changes). */
static void custom_server_install(void) {
    const char *server=getenv("LBPPC_SERVER");
    if (!server || !*server) return;
    /* The replies are checked (0x222a0, X-Digest-A) with the same key kept as a string, at 0xe4aff1. */
    static const struct { uint32_t at; const char *original, *custom; } key[]={
        {0x1edb9,"C5S^FWw*","CustomSe"}, {0x1eda8,"8yIp3O%M","rverDige"}, {0x1edd6,"e!","st"},
        {0xe4aff1,"C5S^FWw*8yIp3O%Me!","CustomServerDigest"}
    };
    for (size_t i=0;i<sizeof(key)/sizeof(*key);++i)
        if (memcmp(image+key[i].at,key[i].original,strlen(key[i].original))) {
            fprintf(stderr,"LBPPC_SERVER: unexpected game code, the server's replies will not be accepted\n"); return;
        }
    for (size_t i=0;i<sizeof(key)/sizeof(*key);++i) memcpy(image+key[i].at,key[i].custom,strlen(key[i].custom));
    printf("Online: custom server %s\n",server);
}
/* Executable memory within 2 GiB of the image (for rel32 jumps and calls into stubs): any free 64 KiB, the space
   around the image being cut up by the GPU and network libraries. */
/* Stub memory reserved at start (stub_pool_reserve), below the image's load address (LBPPC_LOW_BASE) while that
   space is still free: later the graphics driver and other DLLs can take it, and the patches that found no block
   were left out (LBPPC_SIM_DIVIDE among them: the game then ran 60 world steps a second). */
enum { STUB_POOL=2u<<20 };
static unsigned char *stub_pool;
static size_t stub_pool_used;
static void stub_pool_reserve(void) {
    const char *base=getenv("LBPPC_LOW_BASE");
    const uintptr_t load=base ? (uintptr_t)strtoull(base,NULL,0) : 0;
    if (!load) return;
    for (uintptr_t at=load>STUB_POOL ? load-STUB_POOL : 0x10000; !stub_pool && at>=0x10000; at-=0x10000)
        stub_pool=VirtualAlloc((void *)at,STUB_POOL,MEM_COMMIT|MEM_RESERVE,PAGE_EXECUTE_READWRITE);
    /* Security: no access until a block is handed out (near_image_alloc). */
    DWORD was;
    if (stub_pool) VirtualProtect(stub_pool,STUB_POOL,PAGE_NOACCESS,&was);
}
/* Security: patch code is written once, while the game is set up, and is then made read-only and executable
 * (stub_memory_seal), so nothing can write code there later. A block whose stub keeps data it changes while the
 * game runs (LBPPC_SIM_DIVIDE, research) stays writable (stub_keep_writable). */
enum { MAX_STUB_BLOCKS=64 };
static unsigned char *stub_blocks[MAX_STUB_BLOCKS];
static unsigned char stub_block_writable[MAX_STUB_BLOCKS];
static int stub_block_count, stub_memory_sealed;
static unsigned char *near_image_alloc_raw(void);
static unsigned char *near_image_alloc(void) {
    unsigned char *p=near_image_alloc_raw();
    if (!p) return NULL;
    if (stub_block_count<MAX_STUB_BLOCKS) stub_blocks[stub_block_count++]=p;
    if (stub_memory_sealed) fputs("Patches: code memory allocated after it was made read-only (left writable)\n",stderr);
    return p;
}
static void stub_keep_writable(const unsigned char *p) {
    for (int i=0;i<stub_block_count;++i) if (stub_blocks[i]==p) stub_block_writable[i]=1;
}
static void stub_memory_seal(void) {
    int sealed=0, kept=0;
    for (int i=0;i<stub_block_count;++i) {
        DWORD was;
        if (stub_block_writable[i]) { ++kept; continue; }
        if (VirtualProtect(stub_blocks[i],65536,PAGE_EXECUTE_READ,&was)) ++sealed;
        else fprintf(stderr,"Patches: could not make code memory at %p read-only (%lu)\n",(void *)stub_blocks[i],
                     (unsigned long)GetLastError());
    }
    FlushInstructionCache(GetCurrentProcess(),NULL,0);
    stub_memory_sealed=1;
    printf("Patches: %d blocks of patch code read-only%s\n",sealed,kept ? ", research switches keep theirs writable" : "");
}
static unsigned char *near_image_alloc_raw(void) {
    unsigned char *p=NULL;
    if (stub_pool && stub_pool_used+65536<=STUB_POOL && (uintptr_t)stub_pool-(uintptr_t)image+0x70000000u<0xe0000000u) {
        p=stub_pool+stub_pool_used;
        DWORD was;
        if (!VirtualProtect(p,65536,PAGE_EXECUTE_READWRITE,&was)) return NULL;
        stub_pool_used+=65536;
        return p;
    }
    /* Then the runtime's low memory: it hands out the space above the image (LBPPC_LOW_BASE). */
    void *low=runtime_low_map(65536,PROT_READ|PROT_WRITE|PROT_EXEC);
    if (low && (uintptr_t)low-(uintptr_t)image<0x70000000) return (unsigned char *)low;
    const uintptr_t past=((uintptr_t)image+image_size_hint+0xffff)&~(uintptr_t)0xffff;
    for (uintptr_t at=past; !p && at<(uintptr_t)image+0x70000000; at+=0x10000)
        p=VirtualAlloc((void *)at,65536,MEM_COMMIT|MEM_RESERVE,PAGE_EXECUTE_READWRITE);
    if (p) return p;
    for (uintptr_t at=((uintptr_t)image-0x10000000)&~(uintptr_t)0xffff;
         !p && (uintptr_t)image>0x70000000 && at>(uintptr_t)image-0x70000000; at-=0x10000000)
        p=VirtualAlloc((void *)at,65536,MEM_COMMIT|MEM_RESERVE,PAGE_EXECUTE_READWRITE);
    for (uintptr_t at=((uintptr_t)image+0x08000000)&~(uintptr_t)0xffff; !p && at<(uintptr_t)image+0x70000000; at+=0x01000000)
        p=VirtualAlloc((void *)at,65536,MEM_COMMIT|MEM_RESERVE,PAGE_EXECUTE_READWRITE);
    for (uintptr_t at=0x10000; !p && at+65536<=(uintptr_t)image; at+=0x10000)
        p=VirtualAlloc((void *)at,65536,MEM_COMMIT|MEM_RESERVE,PAGE_EXECUTE_READWRITE);
    return p;
}
/* lbppc: LBPPC_SIM_DIVIDE=n: LBP3's main loop calls the world update (one simulation step, 0x254450) at 0xb2aa
 * once a frame. With n, a stub calls it once every n frames: the loop may run at 60 FPS while the world keeps
 * its 30 steps a second. The counter is lbppc_sim_phase (0 on the frames that step). */
volatile int32_t *lbppc_sim_phase;
volatile unsigned char *lbppc_sim_active;
/* LBPPC_UNCAP_TRIGGER=file (with LBPPC_SIM_DIVIDE): when the file appears (removed then), the game goes to one
 * frame per vblank: its own vsync interval (0x1060a10) to 0, the port's flip rate to 0, and the world update to
 * every n frames. Creating it again goes back to 30 FPS. */
static DWORD WINAPI uncap_thread(LPVOID arg) {
    (void)arg;
    const char *trigger=getenv("LBPPC_UNCAP_TRIGGER");
    for (int uncapped=0;;) {
        while (remove(trigger)!=0) Sleep(50);
        uncapped=!uncapped;
        *(volatile int32_t *)(image+0x1060a10)=uncapped ? 0 : 2;
        bbgpu_set_flip_rate(uncapped ? 0 : 1);
        /* LBPPC_UNCAP_LIMIT=fps (default 60): the frame limit once uncapped; 30 before and after. */
        bbgpu_set_frame_limit(uncapped ? (getenv("LBPPC_UNCAP_LIMIT") ? (unsigned)atoi(getenv("LBPPC_UNCAP_LIMIT")) : 60u) : 30u);
        if (lbppc_sim_phase) *lbppc_sim_phase=1;
        if (lbppc_sim_active) *lbppc_sim_active=(unsigned char)uncapped;
        { extern volatile int lbppc_nan_armed; lbppc_nan_armed=uncapped; }
        printf("Runtime: %s\n",uncapped ? "60 FPS (world update every other frame)" : "30 FPS");
    }
    return 0;
}
/* LBPPC_ARENA_MON=1 (diagnostics): LBP3's per-frame arena (used at 0x118ffc8, capacity at 0x118ffd0), sampled
 * every millisecond: the peak and the number of resets each second. */
static DWORD WINAPI arena_monitor(LPVOID arg) {
    (void)arg;
    volatile uint32_t *used=(volatile uint32_t *)(image+0x118ffc8), *cap=(volatile uint32_t *)(image+0x118ffd0);
    for (;;) {
        uint32_t peak=0, last=0, resets=0;
        ULONGLONG end=GetTickCount64()+1000;
        while (GetTickCount64()<end) {
            uint32_t u=*used;
            if (u>peak) peak=u;
            if (u<last) ++resets;
            last=u;
            Sleep(1);
        }
        printf("ARENA peak %u of %u (%.0f%%), %u resets\n",peak,*cap,*cap ? 100.0*peak/ *cap : 0.0,resets);
    }
    return 0;
}
/* lbppc: spu.prx's job at 0x47960 places each point as its rest position plus a weighted average of up to four
 * influences, divided by the sum of their weights (vrcpps at 0x481d4). On a frame without a world step
 * (LBPPC_SIM_DIVIDE) a point can have no weight: 1/0, and the point, its object's sort key and then the draw list
 * go NaN. As the game already does for NaN influences (0x4817e), a NaN result keeps the rest position (xmm6):
 * 0x481fc vaddps xmm0,xmm6,xmm0 and 0x48200 vblendps $8,xmm6,xmm0,xmm0 move to a stub that adds
 * vcmpunordps xmm1,xmm0,xmm0; vblendvps xmm0,xmm0,xmm6,xmm1 between them (xmm1 is free there).
 * LBPPC_SPU_NAN_GUARD=1, or with LBPPC_SIM_DIVIDE. */
static void spu_nan_guard_install(void) {
    const char *spec=getenv("LBPPC_SPU_NAN_GUARD");
    if (spec ? spec[0]!='1' : !getenv("LBPPC_SIM_DIVIDE")) return;
    enum { SITE=0x17081fc, BACK=0x1708206 };
    static const unsigned char original[]={0xc5,0xc8,0x58,0xc0, 0xc4,0xe3,0x79,0x0c,0xc6,0x08};
    if (memcmp(image+SITE,original,sizeof(original))) { fprintf(stderr,"SPU NaN guard: unexpected game code, not applied\n"); return; }
    unsigned char *s=near_image_alloc();
    if (!s) { fputs("SPU NaN guard: no memory near the image\n",stderr); return; }
    static const unsigned char guard[]={
        0xc5,0xc8,0x58,0xc0,                    /* vaddps xmm0,xmm6,xmm0 */
        0xc5,0xf8,0xc2,0xc8,0x03,               /* vcmpunordps xmm1,xmm0,xmm0 */
        0xc4,0xe3,0x79,0x4a,0xc6,0x10,          /* vblendvps xmm0,xmm0,xmm6,xmm1 */
        0xc4,0xe3,0x79,0x0c,0xc6,0x08,          /* vblendps xmm0,xmm0,xmm6,8 */
        0xff,0x25,0x00,0x00,0x00,0x00};         /* jmp [rip+0] */
    memcpy(s,guard,sizeof(guard));
    const uint64_t back=(uint64_t)(uintptr_t)(image+BACK);
    memcpy(s+sizeof(guard),&back,8);
    const int32_t rel=(int32_t)((intptr_t)s-(intptr_t)(image+SITE+5));
    image[SITE]=0xe9; memcpy(image+SITE+1,&rel,4);
    memset(image+SITE+5,0x90,sizeof(original)-5);
    printf("SPU NaN guard: on (stub at %p)\n",(void *)s);
}
/* lbppc: with LBPPC_SIM_DIVIDE=n the main loop runs n times per world step, but LBP3's animation clock (0x3e2970,
 * reached once a frame) adds a fixed 1/30 s per frame: idle animations and most others ran n times too fast while
 * the world kept its speed. Its step, the constant at 0xe7d3cc and the two stores of it (0x3e19d0, 0x3e2a8b),
 * becomes 1/(30n). LBPPC_ANIM_STEP_FIX=0 leaves it. */
static void anim_step_install(int32_t n) {
    const char *spec=getenv("LBPPC_ANIM_STEP_FIX");
    if (n<2 || (spec && spec[0]=='0')) return;
    static const uint32_t at[]={0xe7d3cc,0x3e19d2,0x3e2a8f};
    const uint32_t thirtieth=0x3d088889;
    for (size_t i=0;i<sizeof(at)/sizeof(*at);++i)
        if (memcmp(image+at[i],&thirtieth,4)) { fprintf(stderr,"Animation step: unexpected game code, not applied\n"); return; }
    const float step=1.0f/(30.0f*(float)n);
    for (size_t i=0;i<sizeof(at)/sizeof(*at);++i) memcpy(image+at[i],&step,4);
    printf("Animation step: 1/%d s per frame\n",30*n);
}
static void sim_divide_install(void) {
    spu_nan_guard_install();
    if (getenv("LBPPC_ARENA_MON")) CloseHandle(CreateThread(NULL,0,arena_monitor,NULL,0,NULL));
    const char *spec=getenv("LBPPC_SIM_DIVIDE");
    if (!spec) return;
    const int32_t n=(int32_t)strtol(spec,NULL,0);
    const uint64_t site=0xb2aa, update=0x254450;
    if (n<1 || image[site]!=0xe8) { fprintf(stderr,"LBPPC_SIM_DIVIDE: not applied\n"); return; }
    unsigned char *s=near_image_alloc();
    if (!s) { fputs("LBPPC_SIM_DIVIDE: no memory near the image\n",stderr); return; }
    stub_keep_writable(s); /* its counter and switch are in the stub */
    /* stub: cmp byte [rip+active],0; je direct; sub dword [rip+counter],1; jg skip;
     *       mov dword [rip+counter],n; direct: jmp [rip+target]; skip: ret */
    unsigned char *o=s;
    int32_t *counter=(int32_t *)(s+64);
    uint64_t *target=(uint64_t *)(s+72);
    unsigned char *active=s+80;
    *counter=1; *target=(uint64_t)(uintptr_t)(image+update);
    /* LBPPC_UNCAP_TRIGGER: inactive (every frame) until the trigger file appears. */
    *active=getenv("LBPPC_UNCAP_TRIGGER") ? 0 : 1;
    *o++=0x80; *o++=0x3d; { int32_t d=(int32_t)(active-(o+5)); memcpy(o,&d,4); o+=4; } *o++=0x00;
    unsigned char *je=o; *o++=0x74; *o++=0x00;
    *o++=0x83; *o++=0x2d; { int32_t d=(int32_t)((unsigned char *)counter-(o+5)); memcpy(o,&d,4); o+=4; } *o++=0x01;
    unsigned char *jg=o; *o++=0x7f; *o++=0x00;
    *o++=0xc7; *o++=0x05; { int32_t d=(int32_t)((unsigned char *)counter-(o+8)); memcpy(o,&d,4); o+=4; } memcpy(o,&n,4); o+=4;
    je[1]=(unsigned char)(o-(je+2));
    *o++=0xff; *o++=0x25; { int32_t d=(int32_t)((unsigned char *)target-(o+4)); memcpy(o,&d,4); o+=4; }
    jg[1]=(unsigned char)(o-(jg+2));
    *o++=0xc3;
    lbppc_sim_phase=counter;
    lbppc_sim_active=active;
    const int32_t rel=(int32_t)((intptr_t)s-(intptr_t)(image+site+5));
    memcpy(image+site+1,&rel,4);
    printf("Runtime: world update every %d frames (stub at %p)\n",n,(void *)s);
    if (!getenv("LBPPC_UNCAP_TRIGGER")) anim_step_install(n);
    if (getenv("LBPPC_UNCAP_TRIGGER")) CloseHandle(CreateThread(NULL,0,uncap_thread,NULL,0,NULL));
}
/* lbppc: Quit Game in the pause menu. scripts/quit_menu.py makes the menu's Good Grief! button ask the script native
 * LAMS.Translate (0x839aa0) for two keys of ours; its text lookup (0x7e3490, a key to UTF-16 text) answers them here.
 * QUIT_LABEL is the button's text. QUIT_ACTION is a press: the first arms it ("Press again to quit", for 3 seconds), the
 * second closes the game as the window's close button does, once no save data is mounted (a save being written is
 * finished first, for up to 15 seconds). */
#define QUIT_LABEL 0x4C425051
#define QUIT_ACTION 0x4C425058
static ABI const uint16_t *(*quit_menu_lookup)(int32_t key);
static volatile LONG64 quit_armed_until;
static volatile LONG quit_started;
static DWORD WINAPI quit_menu_thread(void *unused) {
    (void)unused;
    for (int waited=0; runtime_savedata_mounted() && waited<1500; ++waited) Sleep(10);
    puts("Runtime: Quit Game chosen in the pause menu");
    fflush(stdout);
    bbgpu_request_quit();
    return 0;
}
static ABI const uint16_t *quit_menu_translate(int32_t key) {
    static const uint16_t label[]=u"Quit Game", confirm[]=u"Press again to quit", empty[]=u"";
    const LONG64 now=(LONG64)GetTickCount64();
    if (key==QUIT_LABEL) return quit_armed_until>now ? confirm : label;
    if (key!=QUIT_ACTION) return quit_menu_lookup(key);
    if (quit_armed_until<=now) quit_armed_until=now+3000;
    else if (!InterlockedExchange(&quit_started,1)) CloseHandle(CreateThread(NULL,0,quit_menu_thread,NULL,0,NULL));
    return empty;
}
static void quit_menu_install(void) {
    const uint64_t site=0x839ac3, lookup=0x7e3490;
    int32_t rel;
    memcpy(&rel,image+site+1,4);
    if (image[site]!=0xe8 || site+5+(int64_t)rel!=lookup) {
        /* The executable isn't LittleBigPlanet 3 1.28 (US), which every patch here is made for: stop with a message the
         * player can act on, rather than at the first system call that version makes and this one doesn't. Often a
         * merge that took the update's param.sfo but kept the base game's eboot.bin. */
        fputs("LBPPC: this copy's eboot.bin isn't LittleBigPlanet 3 version 1.28 (US). LBPPC runs only that version: "
              "open the launcher and use Set up... to add the 1.28 update to your game folder.\n",stderr);
        exit(3);
    }
    unsigned char *s=near_image_alloc();
    if (!s) { fputs("Quit Game: no memory near the image\n",stderr); return; }
    /* jmp [rip+0] to quit_menu_translate */
    const uint64_t target=(uint64_t)(uintptr_t)quit_menu_translate;
    s[0]=0xff; s[1]=0x25; memset(s+2,0,4); memcpy(s+6,&target,8);
    quit_menu_lookup=(ABI const uint16_t *(*)(int32_t))(uintptr_t)(image+lookup);
    rel=(int32_t)((intptr_t)s-(intptr_t)(image+site+5));
    memcpy(image+site+1,&rel,4);
}
/* lbppc (diagnostics, online): the friends the pause menu's Friends page lists (PartyManagement.GatherFriendData): the
 * game's friend array, 0x184-byte entries at [[0x130d5b8+0x38]+0x70], their count at +0x78. An entry is listed unless
 * its state (+0xa4) is 3. The Friends page (Start_Menu.GetFriendStatus) shows only state 2 (in LBP3) and 1 (online), and
 * hides state 2 when the wrong-version byte (+0xfc) is set: 0x38b9d0 sets it when the friend's presence gameData (copied
 * to +0x104, 128 bytes) does not start with 0x9?. Logged whenever it changes: each entry's leading text, state, the
 * wrong-version byte and gameData's first bytes. */
static DWORD WINAPI friend_log_thread(void *unused) {
    (void)unused;
    char last[2048]="";
    for (;;) {
        Sleep(5000);
        uint64_t manager=0, array=0; int32_t count=0;
        SIZE_T got=0;
        if (!ReadProcessMemory(GetCurrentProcess(),image+0x130d5b8+0x38,&manager,8,&got) || !manager) continue;
        if (!ReadProcessMemory(GetCurrentProcess(),(void *)(uintptr_t)(manager+0x70),&array,8,&got) ||
            !ReadProcessMemory(GetCurrentProcess(),(void *)(uintptr_t)(manager+0x78),&count,4,&got)) continue;
        char line[2048];
        int n=snprintf(line,sizeof(line),"Friends (menu): %d", count);
        for (int32_t i=0; i<count && i<16 && array && n<(int)sizeof(line)-80; ++i) {
            unsigned char entry[0x184];
            if (!ReadProcessMemory(GetCurrentProcess(),(void *)(uintptr_t)(array+(uint64_t)i*0x184),entry,sizeof(entry),&got)) break;
            char text[24]; int t=0;
            for (int k=0; k<64 && t<20; ++k) if (entry[k]>=0x20 && entry[k]<0x7f) text[t++]=(char)entry[k]; else if (t) break;
            text[t]=0;
            int32_t state; memcpy(&state,entry+0xa4,4);
            n+=snprintf(line+n,sizeof(line)-n,"; '%s' state %d flag %u data %02x%02x%02x%02x",text,state,entry[0xfc],
                        entry[0x104],entry[0x105],entry[0x106],entry[0x107]);
        }
        if (strcmp(line,last)) { puts(line); fflush(stdout); snprintf(last,sizeof(last),"%s",line); }
    }
    return 0;
}
static void friend_log_install(void) {
    /* LBPPC_FRIENDS_LOG=1 (launcher Debug page: "Friends menu log", off by default), online only. */
    const char *npid=getenv("BB_SHADNET_NPID"), *wanted=getenv("LBPPC_FRIENDS_LOG");
    if (npid && *npid && wanted && wanted[0]=='1') CloseHandle(CreateThread(NULL,0,friend_log_thread,NULL,0,NULL));
}
/* lbppc (diagnostics, online, with LBPPC_POS_LOG=1): the game's network messages, by type, every 5 s. The dispatcher
 * (0x3add00) first checks a message (0x379ab0, called at 0x3add96; it drops the message when that fails), then decodes
 * its header (0x3ac550 at 0x3addec; the type is the first dword) and calls the handler registered for that type
 * (0x247a04 on: 0x3d the sync check, 0x39, 0x36, 0x35, 0x2c, 0x2e, 0x4d, 0x55, 0x6d). Logged: messages passing and
 * failing the check, and each type's count, to tell what a co-op guest stops receiving after a Pod load. */
enum { NET_CHECK_SITE=0x3add96, NET_CHECK=0x379ab0, NET_HEADER_SITE=0x3addec, NET_HEADER=0x3ac550 };
static ABI uint64_t (*net_check)(uint64_t,uint64_t,uint64_t,uint64_t,uint64_t,uint64_t);
static ABI uint64_t (*net_header)(uint64_t,uint64_t);
static volatile LONG net_passed, net_failed, net_types[128];
static volatile LONG64 net_last_log;
/* The dispatcher's r13 (who sent the message), stored by the hook's stub before it jumps to net_header_hook. */
static volatile uint64_t net_sender;
static void net_log_maybe(void) {
    const LONG64 now=(LONG64)GetTickCount64();
    LONG64 last=net_last_log;
    if (now-last<5000 || InterlockedCompareExchange64(&net_last_log,now,last)!=last) return;
    char line[1024];
    int n=snprintf(line,sizeof(line),"Net: last 5 s: %ld messages passed the check, %ld failed; by type:",
                   InterlockedExchange(&net_passed,0),InterlockedExchange(&net_failed,0));
    for (int i=0;i<128 && n<(int)sizeof(line)-16;++i) {
        const LONG c=InterlockedExchange(&net_types[i],0);
        if (c) n+=snprintf(line+n,sizeof(line)-n," %02x:%ld",i,c);
    }
    puts(line);
    fflush(stdout);
}
static ABI uint64_t net_check_hook(uint64_t a,uint64_t b,uint64_t c,uint64_t d,uint64_t e,uint64_t f) {
    const uint64_t r=net_check(a,b,c,d,e,f);
    InterlockedIncrement((r&0xff) ? &net_passed : &net_failed);
    net_log_maybe();
    return r;
}
static ABI uint64_t net_header_hook(uint64_t header,uint64_t b) {
    const uint64_t r=net_header(header,b);
    const uint32_t type=*(const uint32_t *)(uintptr_t)header;
    InterlockedIncrement(&net_types[type&127]);
    /* The one-off messages (not 0x0f, sent every frame, nor 0x3d, the sync check), each: time, world frame, sender,
     * size and first bytes of the message (the dispatcher's buffer: pointer at header-0x38, size at header-0x30). */
    if (type!=0x0f && type!=0x3d) {
        const unsigned char *data=*(const unsigned char *const *)(uintptr_t)(header-0x38);
        const uint32_t size=*(const uint32_t *)(uintptr_t)(header-0x30);
        char from[24]={0}, hex[3*48+1]={0};
        const char *sender=(const char *)(uintptr_t)net_sender;
        for (int i=0,k=0;i<20 && sender;++i) {
            char c;
            if (!read_guest((uint64_t)(uintptr_t)(sender+i),&c,1)) break;
            if (c>=0x21 && c<0x7f) from[k++]=c; else if (k) break;
        }
        for (uint32_t i=0;i<size && i<48 && data;++i) {
            unsigned char b;
            if (!read_guest((uint64_t)(uintptr_t)(data+i),&b,1)) break;
            snprintf(hex+3*i,4,"%02x ",b);
        }
        printf("Net: %llu ms, frame %llu: type %02x from '%s', %u bytes: %s\n",(unsigned long long)GetTickCount64(),
               (unsigned long long)sync_last_frame,type,from,size,hex);
    }
    return r;
}
static int net_hook_call(unsigned char *stub,uint64_t site,uint64_t callee,void *hook) {
    int32_t rel;
    memcpy(&rel,image+site+1,4);
    if (image[site]!=0xe8 || site+5+(int64_t)rel!=callee) return 0;
    const uint64_t target=(uint64_t)(uintptr_t)hook;
    stub[0]=0xff; stub[1]=0x25; memset(stub+2,0,4); memcpy(stub+6,&target,8);
    rel=(int32_t)((intptr_t)stub-(intptr_t)(image+site+5));
    memcpy(image+site+1,&rel,4);
    return 1;
}
static void net_log_install(void) {
    const char *npid=getenv("BB_SHADNET_NPID"), *wanted=getenv("LBPPC_POS_LOG");
    if (!npid || !*npid || !wanted || wanted[0]!='1') return;
    unsigned char *s=near_image_alloc();
    if (!s) return;
    net_check=(ABI uint64_t (*)(uint64_t,uint64_t,uint64_t,uint64_t,uint64_t,uint64_t))(uintptr_t)(image+NET_CHECK);
    net_header=(ABI uint64_t (*)(uint64_t,uint64_t))(uintptr_t)(image+NET_HEADER);
    /* The header hook's stub first stores r13 (the sender) in net_sender: mov r11, &net_sender; mov [r11], r13. */
    unsigned char *h=s+16;
    const uint64_t slot=(uint64_t)(uintptr_t)&net_sender;
    h[0]=0x49; h[1]=0xbb; memcpy(h+2,&slot,8); h[10]=0x4d; h[11]=0x89; h[12]=0x2b;
    const int ok=net_hook_call(s,NET_CHECK_SITE,NET_CHECK,(void *)net_check_hook) &&
                 net_hook_call(h+13,NET_HEADER_SITE,NET_HEADER,(void *)net_header_hook);
    if (ok) { /* the call goes to the r13 store, which falls through to the jump */
        int32_t rel=(int32_t)((intptr_t)h-(intptr_t)(image+NET_HEADER_SITE+5));
        memcpy(image+NET_HEADER_SITE+1,&rel,4);
    }
    printf("Net: message log %s\n",ok ? "on" : "not added (unexpected game code)");
}
/* lbppc: online play runs both players' worlds in lockstep and compares them ("The game diverged"). rcpps and rsqrtps
 * are approximations whose results differ between CPU makers, harmless on a console where every machine is alike:
 * online, each site in the game and its spu.prx (exact_math_sites.inc, CUSA00473 1.28; checked first, else nothing
 * changes) jumps to a stub computing 1/x or 1/sqrt(x) exactly. dpps goes the same way, as a fixed sequence of additions.
 * LBPPC_EXACT_MATH=0 leaves them, =1 applies them offline too. */
static const struct { uint32_t offset; uint8_t site_length, moved, rip_at, rip_next; } exact_math_sites[]={
#include "exact_math_sites.inc"
};
/* A 3-byte VEX instruction on registers: map 1 (0F) or 3 (0F3A), prefix pp, 128 bits. */
static unsigned char *vex_op(unsigned char *o,unsigned map,unsigned pp,unsigned opcode,unsigned reg,unsigned first,unsigned rm) {
    *o++=0xc4;
    *o++=(unsigned char)((reg<8 ? 0x80 : 0)|0x40|(rm<8 ? 0x20 : 0)|map);
    *o++=(unsigned char)(((~first&15)<<3)|pp);
    *o++=(unsigned char)opcode;
    *o++=(unsigned char)(0xc0|((reg&7)<<3)|(rm&7));
    return o;
}
/* dpps as Intel documents it: (x0*y0+x1*y1)+(x2*y2+x3*y3), the elements not in the mask +0. */
static unsigned char *exact_dpps(unsigned char *o,const unsigned char *site) {
    const unsigned dst=((site[4]>>3)&7)|(site[1]&0x80 ? 0 : 8), rm=(site[4]&7)|(site[1]&0x20 ? 0 : 8);
    const unsigned first=(~(site[2]>>3))&15, mask=site[5];
    unsigned t=0, z;
    while (t==dst || t==first || t==rm) ++t;
    z=t+1;
    while (z==dst || z==first || z==rm) ++z;
    static const unsigned char below[]={0x48,0x8d,0xa4,0x24,0x40,0xff,0xff,0xff}; /* lea rsp,[rsp-192]: past the red zone */
    static const unsigned char above[]={0x48,0x8d,0xa4,0x24,0xc0,0x00,0x00,0x00}; /* lea rsp,[rsp+192] */
    memcpy(o,below,8); o+=8;
    *o++=0xc5; *o++=0xfc; *o++=0x11; *o++=(unsigned char)(0x04|(t<<3)); *o++=0x24;   /* vmovups [rsp],ymmT */
    *o++=0xc5; *o++=0xfc; *o++=0x11; *o++=(unsigned char)(0x44|(z<<3)); *o++=0x24; *o++=0x20; /* vmovups [rsp+32],ymmZ */
    o=vex_op(o,1,0,0x59,t,first,rm);                                                 /* vmulps t,first,rm */
    o=vex_op(o,1,0,0x57,z,z,z);                                                      /* vxorps z,z,z */
    o=vex_op(o,3,1,0x0c,t,z,t); *o++=(unsigned char)(mask>>4);                       /* vblendps t,z,t,mask>>4 */
    o=vex_op(o,1,3,0x7c,t,t,t);                                                      /* vhaddps t,t,t */
    o=vex_op(o,1,3,0x7c,t,t,t);                                                      /* vhaddps t,t,t */
    o=vex_op(o,1,0,0xc6,t,t,t); *o++=0;                                              /* vshufps t,t,t,0 */
    o=vex_op(o,3,1,0x0c,dst,z,t); *o++=(unsigned char)(mask&15);                     /* vblendps dst,z,t,mask&15 */
    *o++=0xc5; *o++=0xfc; *o++=0x10; *o++=(unsigned char)(0x44|(z<<3)); *o++=0x24; *o++=0x20; /* vmovups ymmZ,[rsp+32] */
    *o++=0xc5; *o++=0xfc; *o++=0x10; *o++=(unsigned char)(0x04|(t<<3)); *o++=0x24;   /* vmovups ymmT,[rsp] */
    memcpy(o,above,8); o+=8;
    return o;
}
static void exact_math_install(void) {
    const char *setting=getenv("LBPPC_EXACT_MATH"), *online=getenv("BB_ONLINE");
    if (setting ? setting[0]=='0' : !(online && online[0]=='1')) return;
    const size_t count=sizeof(exact_math_sites)/sizeof(*exact_math_sites);
    for (size_t i=0;i<count;++i) {
        const unsigned char *site=image+exact_math_sites[i].offset;
        const unsigned char op=site[0]==0xc5 ? site[2] : site[3];
        const int is_dpps=site[0]==0xc4 && (site[1]&0x1f)==3 && op==0x40 && (site[4]>>6)==3 && exact_math_sites[i].site_length==6;
        if ((site[0]!=0xc5 && site[0]!=0xc4) || (op!=0x52 && op!=0x53 && !is_dpps)) {
            fprintf(stderr,"Exact math: unexpected game code at 0x%x, not applied (online play may diverge)\n",exact_math_sites[i].offset);
            return;
        }
    }
    const float one=1.0f;
    unsigned char *stubs=NULL, *o=NULL;
    size_t chunks=0;
    for (size_t i=0;i<count;++i) {
        if (!stubs || (size_t)(o-stubs)+96>65536) {
            stubs=near_image_alloc();
            if (!stubs) { fprintf(stderr,"Exact math: no memory near the image after %zu sites (online play may diverge)\n",i); return; }
            memcpy(stubs,&one,4);
            o=stubs+16;
            ++chunks;
        }
        unsigned char *site=image+exact_math_sites[i].offset;
        const unsigned length=exact_math_sites[i].site_length, moved=exact_math_sites[i].moved;
        if (site[0]==0xc4 && (site[1]&0x1f)==3) {
            unsigned char *dot=o;
            o=exact_dpps(o,site);
            *o++=0xe9; { int32_t d=(int32_t)((site+moved)-(o+4)); memcpy(o,&d,4); o+=4; }
            const int32_t jump=(int32_t)(dot-(site+5));
            DWORD was;
            VirtualProtect(site,moved,PAGE_EXECUTE_READWRITE,&was);
            site[0]=0xe9; memcpy(site+1,&jump,4);
            for (unsigned k=5;k<moved;++k) site[k]=0x90;
            VirtualProtect(site,moved,was,&was);
            continue;
        }
        const unsigned rip_at=exact_math_sites[i].rip_at, rip_next=exact_math_sites[i].rip_next;
        const int vex2=site[0]==0xc5;
        const unsigned op_at=vex2 ? 2 : 3, modrm=site[op_at+1];
        const int is_sqrt=site[op_at]==0x52;
        const unsigned dst=((modrm>>3)&7)|(site[1]&0x80 ? 0 : 8);
        const unsigned src=(modrm>>6)==3 ? (unsigned)((modrm&7)|(!vex2 && !(site[1]&0x20) ? 8 : 0)) : 16;
        unsigned t=0;
        while (t==dst || t==src) ++t;
        unsigned char *stub=o;
        static const unsigned char below[]={0x48,0x8d,0xa4,0x24,0x60,0xff,0xff,0xff}; /* lea rsp,[rsp-160]: past the red zone */
        static const unsigned char above[]={0x48,0x8d,0xa4,0x24,0xa0,0x00,0x00,0x00}; /* lea rsp,[rsp+160] */
        memcpy(o,below,8); o+=8;
        *o++=0xc5; *o++=0xfc; *o++=0x11; *o++=(unsigned char)(0x04|(t<<3)); *o++=0x24;   /* vmovups [rsp],ymmT */
        *o++=0xc4; *o++=0xe2; *o++=0x79; *o++=0x18; *o++=(unsigned char)(0x05|(t<<3));   /* vbroadcastss xmmT,[rip+one] */
        { int32_t d=(int32_t)(stubs-(o+4)); memcpy(o,&d,4); o+=4; }
        if (is_sqrt) {
            memcpy(o,site,length); o[op_at]=0x51; o+=length;                             /* vsqrtps dst,src */
            *o++=0xc4; *o++=(unsigned char)(0x41|(dst<8 ? 0xa0 : 0));                    /* vdivps dst,xmmT,dst */
            *o++=(unsigned char)((~t&15)<<3); *o++=0x5e; *o++=(unsigned char)(0xc0|((dst&7)<<3)|(dst&7));
        } else {
            /* The site itself as vdivps dst,xmmT,src: same operands, another opcode and first source. */
            memcpy(o,site,length);
            o[op_at]=0x5e;
            o[op_at-1]=(unsigned char)((o[op_at-1]&0x87)|((~t&15)<<3));
            if (rip_at && rip_at<length) {
                int32_t d;
                memcpy(&d,site+rip_at,4);
                d=(int32_t)((site+rip_next+d)-(o+rip_next));
                memcpy(o+rip_at,&d,4);
            }
            o+=length;
        }
        *o++=0xc5; *o++=0xfc; *o++=0x10; *o++=(unsigned char)(0x04|(t<<3)); *o++=0x24;   /* vmovups ymmT,[rsp] */
        memcpy(o,above,8); o+=8;
        if (moved>length) {
            memcpy(o,site+length,moved-length);
            if (rip_at>=length) {
                int32_t d;
                memcpy(&d,site+rip_at,4);
                d=(int32_t)((site+rip_next+d)-(o+rip_next-length));
                memcpy(o+rip_at-length,&d,4);
            }
            o+=moved-length;
        }
        *o++=0xe9; { int32_t d=(int32_t)((site+moved)-(o+4)); memcpy(o,&d,4); o+=4; }
        const int32_t rel=(int32_t)(stub-(site+5));
        DWORD old;
        VirtualProtect(site,moved,PAGE_EXECUTE_READWRITE,&old);
        site[0]=0xe9; memcpy(site+1,&rel,4);
        for (unsigned k=5;k<moved;++k) site[k]=0x90;
        VirtualProtect(site,moved,old,&old);
    }
    printf("Online: exact math at %zu sites, %zu blocks of stubs\n",count,chunks);
}
static void hooks_install(const char *spec) {
    if (!spec || !*spec) return;
    unsigned char *stubs=near_image_alloc();
    if (!stubs) { fprintf(stderr,"Hook: no memory near the image\n"); return; }
    unsigned char *o=stubs;
    for (const char *p=spec; *p;) {
        char *end;
        uint64_t offset=strtoull(p,&end,0);
        unsigned length=*end==':' ? (unsigned)strtoul(end+1,&end,0) : 0;
        p=*end==',' ? end+1 : end+strlen(end);
        if (length<5 || length>32) { fprintf(stderr,"Hook: %llx needs a length of 5-32\n",(unsigned long long)offset); continue; }
        unsigned char *site=image+offset, *s=o;
        /* Safe in the middle of a function too: below the red zone, the flags, every scratch register and
           xmm0-15 saved on an aligned stack, and back through a memory jump (no register touched). */
        static const unsigned char enter[]={
            0x48,0x8d,0x64,0x24,0x80,                   /* lea rsp,[rsp-128]: past the red zone */
            0x55, 0x48,0x89,0xe5, 0x9c,                 /* push rbp; mov rbp,rsp; pushfq */
            0x50,0x51,0x52,0x56,0x57,0x41,0x50,0x41,0x51,0x41,0x52,0x41,0x53, /* rax rcx rdx rsi rdi r8-r11 */
            0x53,0x41,0x54,0x41,0x55,0x41,0x56,0x41,0x57,                     /* rbx r12-r15 */
            0x48,0x83,0xe4,0xf0, 0x48,0x81,0xec,0x00,0x01,0x00,0x00};          /* and rsp,-16; sub rsp,256 */
        memcpy(o,enter,sizeof(enter)); o+=sizeof(enter);
        for (int x=0;x<16;++x) {                                /* movups [rsp+16x],xmmx */
            if (x>=8) *o++=0x44;
            *o++=0x0f; *o++=0x11; *o++=(unsigned char)(0x84|((x&7)<<3)); *o++=0x24;
            int32_t disp=16*x; memcpy(o,&disp,4); o+=4;
        }
        *o++=0x49; *o++=0x89; *o++=0xe9;                       /* mov r9,rbp: the saved registers */
        *o++=0x49; *o++=0x89; *o++=0xd0;                       /* mov r8,rdx: the third argument */
        *o++=0x48; *o++=0x8d; *o++=0x8d; { int32_t d=136; memcpy(o,&d,4); o+=4; } /* lea rcx,[rbp+136]: the site's rsp */
        *o++=0x48; *o++=0x89; *o++=0xf2;                       /* mov rdx,rsi */
        *o++=0x48; *o++=0x89; *o++=0xfe;                       /* mov rsi,rdi */
        *o++=0x48; *o++=0xbf; memcpy(o,&offset,8); o+=8;       /* mov rdi,offset */
        uintptr_t fn=(uintptr_t)hook_log;
        *o++=0x48; *o++=0xb8; memcpy(o,&fn,8); o+=8;           /* mov rax,hook_log */
        *o++=0xff; *o++=0xd0;                                  /* call rax */
        for (int x=0;x<16;++x) {                                /* movups xmmx,[rsp+16x] */
            if (x>=8) *o++=0x44;
            *o++=0x0f; *o++=0x10; *o++=(unsigned char)(0x84|((x&7)<<3)); *o++=0x24;
            int32_t disp=16*x; memcpy(o,&disp,4); o+=4;
        }
        static const unsigned char leave[]={
            0x48,0x8d,0x65,0x88,                                /* lea rsp,[rbp-120] */
            0x41,0x5f,0x41,0x5e,0x41,0x5d,0x41,0x5c,0x5b,       /* r15-r12 rbx */
            0x41,0x5b,0x41,0x5a,0x41,0x59,0x41,0x58,0x5f,0x5e,0x5a,0x59,0x58, /* r11-r8 rdi rsi rdx rcx rax */
            0x9d, 0x5d,                                         /* popfq; pop rbp */
            0x48,0x8d,0xa4,0x24,0x80,0x00,0x00,0x00};            /* lea rsp,[rsp+128] */
        memcpy(o,leave,sizeof(leave)); o+=sizeof(leave);
        memcpy(o,site,length); o+=length;
        uintptr_t back=(uintptr_t)site+length;
        *o++=0xff; *o++=0x25; *o++=0; *o++=0; *o++=0; *o++=0;   /* jmp [rip+0] */
        memcpy(o,&back,8); o+=8;
        int32_t rel=(int32_t)((intptr_t)s-((intptr_t)site+5));
        DWORD old;
        VirtualProtect(site,length,PAGE_EXECUTE_READWRITE,&old);
        site[0]=0xe9; memcpy(site+1,&rel,4);
        for (unsigned i=5;i<length;++i) site[i]=0x90;
        VirtualProtect(site,length,old,&old);
        o=(unsigned char *)(((uintptr_t)o+15)&~(uintptr_t)15);
        fprintf(stderr,"Hook: guest 0x%llx hooked (%u bytes moved)\n",(unsigned long long)offset,length);
    }
}
/* lbppc: performance diagnostics (LBPPC_PROFILE=1; the thread lines also online). The frame lines say a frame waited
 * for the game, not why: "Threads:" is the CPU each thread used every 5 seconds, from cycle counts (100% is one core),
 * and "Profile:" is where the game's main thread was during each slow frame's game time, sampled about 1000 times a
 * second. Every 30 seconds "Profile: slow frames" compares each place's share of slow frames with its share overall. */
typedef struct { DWORD tid; uint64_t cycles; } ThreadCycles;
static const char *thread_label(HANDLE thread,DWORD tid,char *out,size_t size) {
    extern const char *runtime_thread_name_of(unsigned long tid);
    const char *guest=runtime_thread_name_of(tid);
    if (guest && *guest) return guest;
    typedef HRESULT (WINAPI *Describe)(HANDLE,PWSTR *);
    static Describe describe;
    if (!describe) describe=(Describe)(void *)GetProcAddress(GetModuleHandleA("kernel32.dll"),"GetThreadDescription");
    PWSTR wide=NULL;
    snprintf(out,size,"thread %lu",tid);
    if (describe && SUCCEEDED(describe(thread,&wide)) && wide) {
        if (*wide) WideCharToMultiByte(CP_UTF8,0,wide,-1,out,(int)size,NULL,NULL);
        LocalFree(wide);
    }
    return out;
}
static DWORD WINAPI thread_load_monitor(LPVOID arg) {
    (void)arg;
    enum { MAX_THREADS=512, SHOWN=14 };
    static ThreadCycles last[MAX_THREADS], next[MAX_THREADS];
    static struct { uint64_t used; char name[32]; } now[MAX_THREADS];
    size_t last_count=0;
    uint64_t last_tsc=0;
    const DWORD self=GetCurrentProcessId();
    for (;;) {
        Sleep(5000);
        HANDLE snapshot=CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD,0);
        if (snapshot==INVALID_HANDLE_VALUE) continue;
        /* Thread cycles count at the time stamp counter's rate, so the counter's own advance is one core's worth. */
        const uint64_t tsc=__rdtsc(), elapsed=tsc-last_tsc;
        THREADENTRY32 entry;
        entry.dwSize=sizeof(entry);
        size_t count=0;
        uint64_t total=0;
        for (BOOL more=Thread32First(snapshot,&entry); more && count<MAX_THREADS; more=Thread32Next(snapshot,&entry)) {
            if (entry.th32OwnerProcessID!=self) continue;
            HANDLE thread=OpenThread(THREAD_QUERY_LIMITED_INFORMATION,FALSE,entry.th32ThreadID);
            if (!thread) continue;
            ULONG64 cycles=0;
            QueryThreadCycleTime(thread,&cycles);
            uint64_t before=cycles;
            for (size_t i=0;i<last_count;++i) if (last[i].tid==entry.th32ThreadID) { before=last[i].cycles; break; }
            next[count]=(ThreadCycles){entry.th32ThreadID,cycles};
            now[count].used=cycles-before;
            total+=now[count].used;
            char label[32];
            snprintf(now[count].name,sizeof(now[count].name),"%s",thread_label(thread,entry.th32ThreadID,label,sizeof(label)));
            CloseHandle(thread);
            ++count;
        }
        CloseHandle(snapshot);
        memcpy(last,next,count*sizeof(*last));
        const int first=last_tsc==0;
        last_count=count;
        last_tsc=tsc;
        if (first || !elapsed) continue;
        char line[1024];
        int n=snprintf(line,sizeof(line),"Threads: %.0f%% of one core over 5 s:",(double)total*100.0/(double)elapsed);
        for (int shown=0;shown<SHOWN;++shown) {
            size_t best=count;
            for (size_t i=0;i<count;++i) if (now[i].used && (best==count || now[i].used>now[best].used)) best=i;
            if (best==count || (double)now[best].used*100.0/(double)elapsed<1.0) break;
            n+=snprintf(line+n,sizeof(line)-(size_t)n,"%s %s %.0f%%",shown ? "," : "",now[best].name,(double)now[best].used*100.0/(double)elapsed);
            now[best].used=0;
        }
        puts(line);
    }
    return 0;
}
/* A place is the host module the main thread was in (or guest code), the nearest return address into the port's own
 * code (LBPPC-Game.exe, offset from its base: llvm-symbolizer names it) and the three nearest guest return addresses. */
typedef struct { int64_t qpc; uint64_t where; uint32_t port; uint32_t guest[3]; } ProfileSample;
typedef struct { uint64_t where; uint32_t port; uint32_t guest[3]; uint32_t slow, all; } ProfilePlace;
typedef struct { int64_t from, to; float frame_ms, game_ms; } SlowFrame;
enum { PROFILE_RING=32768, SLOW_FRAMES=64, GUEST_CODE=1 };
static ProfileSample profile_ring[PROFILE_RING];
static SlowFrame slow_frames[SLOW_FRAMES];
static volatile LONG slow_frames_written;
static int64_t profile_qpc_hz;
static DWORD profile_thread_id;
/* Called by the frame timing (vk_low_latency.cpp) on its own thread for each slow frame: the game's part of it ended
 * game_end_ago_ms before now. Only the timing thread writes, so publishing the count after the entry is enough. */
static void slow_frame_seen(double frame_ms,double game_start_ago_ms,double game_end_ago_ms) {
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    const LONG at=slow_frames_written;
    SlowFrame *frame=&slow_frames[at%SLOW_FRAMES];
    frame->from=now.QuadPart-(int64_t)(game_start_ago_ms*(double)profile_qpc_hz/1000.0);
    frame->to=now.QuadPart-(int64_t)(game_end_ago_ms*(double)profile_qpc_hz/1000.0);
    frame->frame_ms=(float)frame_ms;
    frame->game_ms=(float)(game_start_ago_ms-game_end_ago_ms);
    InterlockedExchange(&slow_frames_written,at+1);
}
static int return_address(uint64_t value,uint64_t lo,uint64_t hi) {
    if (value<lo+8 || value>=hi) return 0;
    /* The image has unmapped gaps and this thread must not fault: the port treats that as a crash. */
    unsigned char before[7];
    SIZE_T read=0;
    if (!ReadProcessMemory(GetCurrentProcess(),(const void *)(uintptr_t)(value-7),before,7,&read) || read!=7) return 0;
    return before[2]==0xe8 || before[5]==0xff || before[4]==0xff || before[1]==0xff || before[0]==0xff;
}
static const char *place_name(uint64_t where,char *out,size_t size) {
    snprintf(out,size,"?");
    if (where==GUEST_CODE) snprintf(out,size,"guest code");
    else if (where && !GetModuleBaseNameA(GetCurrentProcess(),(HMODULE)(uintptr_t)where,out,(DWORD)size)) snprintf(out,size,"?");
    return out;
}
static int same_place(const ProfileSample *s,const ProfilePlace *p) {
    return s->where==p->where && s->port==p->port && !memcmp(s->guest,p->guest,sizeof(p->guest));
}
static size_t place_of(ProfilePlace *places,size_t *used,size_t max,const ProfileSample *s) {
    size_t at=0;
    while (at<*used && !same_place(s,&places[at])) ++at;
    if (at==*used && *used<max) {
        places[at]=(ProfilePlace){s->where,s->port,{s->guest[0],s->guest[1],s->guest[2]},0,0};
        ++*used;
    }
    return at;
}
static DWORD WINAPI main_thread_profiler(LPVOID arg) {
    (void)arg;
    enum { MAX_PLACES=512, SHOWN=4, SUMMARY_SHOWN=10, STACK_WORDS=4096, SUMMARY_SECONDS=30 };
    static ProfilePlace frame_places[MAX_PLACES], summary[MAX_PLACES];
    static uint64_t words[STACK_WORDS];
    const uint64_t lo=(uint64_t)(uintptr_t)image, hi=lo+image_size_hint;
    MODULEINFO exe={0};
    GetModuleInformation(GetCurrentProcess(),GetModuleHandleA(NULL),&exe,sizeof(exe));
    const uint64_t port_lo=(uint64_t)(uintptr_t)exe.lpBaseOfDll, port_hi=port_lo+exe.SizeOfImage;
    HANDLE thread=OpenThread(THREAD_SUSPEND_RESUME|THREAD_GET_CONTEXT|THREAD_QUERY_INFORMATION,FALSE,profile_thread_id);
    if (!thread) return 0;
    timeBeginPeriod(1);
    uint64_t written=0;
    LONG frames_done=0;
    size_t summary_used=0;
    uint32_t summary_frames=0;
    LARGE_INTEGER started;
    QueryPerformanceCounter(&started);
    int64_t next_summary=started.QuadPart+SUMMARY_SECONDS*profile_qpc_hz;
    for (;;) {
        Sleep(1);
        CONTEXT c;
        memset(&c,0,sizeof(c));
        c.ContextFlags=CONTEXT_CONTROL;
        size_t got=0;
        if (SuspendThread(thread)==(DWORD)-1) return 0;
        int have=GetThreadContext(thread,&c);
        if (have) {
            /* Only memory of the stack's own region: no allocation or lock while the thread is stopped. */
            MEMORY_BASIC_INFORMATION region;
            if (VirtualQuery((void *)(uintptr_t)c.Rsp,&region,sizeof(region))) {
                const uint64_t end=(uint64_t)(uintptr_t)region.BaseAddress+region.RegionSize;
                got=(size_t)((end-c.Rsp)/8);
                if (got>STACK_WORDS) got=STACK_WORDS;
                SIZE_T read=0;
                if (!ReadProcessMemory(GetCurrentProcess(),(const void *)(uintptr_t)c.Rsp,words,got*8,&read)) read=0;
                got=(size_t)read/8;
            }
        }
        ResumeThread(thread);
        LARGE_INTEGER now;
        QueryPerformanceCounter(&now);
        if (have) {
            ProfileSample *sample=&profile_ring[written%PROFILE_RING];
            memset(sample,0,sizeof(*sample));
            sample->qpc=now.QuadPart;
            if (c.Rip>=lo && c.Rip<hi) sample->where=GUEST_CODE;
            else {
                HMODULE module=NULL;
                GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,(const char *)(uintptr_t)c.Rip,&module);
                sample->where=(uint64_t)(uintptr_t)module;
            }
            int found=0;
            for (size_t i=0;i<got && found<3;++i) {
                if (return_address(words[i],lo,hi)) sample->guest[found++]=(uint32_t)(words[i]-lo);
                else if (!found && !sample->port && return_address(words[i],port_lo,port_hi)) sample->port=(uint32_t)(words[i]-port_lo);
            }
            ++written;
        }
        /* Each slow frame whose game time has passed: its samples, newest first, while the ring still holds them. */
        const LONG frames_written=slow_frames_written;
        if (frames_written-frames_done>SLOW_FRAMES) frames_done=frames_written-SLOW_FRAMES;
        while (frames_done<frames_written && slow_frames[frames_done%SLOW_FRAMES].to<now.QuadPart) {
            const SlowFrame frame=slow_frames[frames_done%SLOW_FRAMES];
            ++frames_done;
            size_t used=0;
            uint32_t samples=0;
            for (uint64_t i=written; i>0 && written-i<PROFILE_RING; --i) {
                const ProfileSample *s=&profile_ring[(i-1)%PROFILE_RING];
                if (s->qpc<frame.from) break;
                if (s->qpc>frame.to) continue;
                const size_t at=place_of(frame_places,&used,MAX_PLACES,s);
                if (at<used) ++frame_places[at].slow;
                const size_t total_at=place_of(summary,&summary_used,MAX_PLACES,s);
                if (total_at<summary_used) ++summary[total_at].slow;
                ++samples;
            }
            ++summary_frames;
            char line[1024];
            int n=snprintf(line,sizeof(line),"Profile: slow frame %.1f ms, game %.1f ms, %u samples:",frame.frame_ms,frame.game_ms,samples);
            for (int shown=0;shown<SHOWN && n<(int)sizeof(line)-160;++shown) {
                size_t best=used;
                for (size_t i=0;i<used;++i) if (frame_places[i].slow && (best==used || frame_places[i].slow>frame_places[best].slow)) best=i;
                if (best==used) break;
                char name[64];
                n+=snprintf(line+n,sizeof(line)-(size_t)n,"%s %u%% %s < port 0x%x < 0x%x < 0x%x < 0x%x",shown ? "," : "",
                            frame_places[best].slow*100/(samples ? samples : 1),place_name(frame_places[best].where,name,sizeof(name)),
                            frame_places[best].port,frame_places[best].guest[0],frame_places[best].guest[1],frame_places[best].guest[2]);
                frame_places[best].slow=0;
            }
            puts(line);
        }
        if (now.QuadPart<next_summary) continue;
        next_summary+=SUMMARY_SECONDS*profile_qpc_hz;
        if (!summary_frames) continue;
        /* The same places' share of every sample over the period, so a place common in slow frames but no more common
         * than usual stands out as not the cause. */
        uint32_t slow_total=0, all_total=0;
        for (size_t i=0;i<summary_used;++i) slow_total+=summary[i].slow;
        for (uint64_t i=written; i>0 && written-i<PROFILE_RING; --i) {
            const ProfileSample *s=&profile_ring[(i-1)%PROFILE_RING];
            if (s->qpc<now.QuadPart-SUMMARY_SECONDS*profile_qpc_hz) break;
            ++all_total;
            for (size_t p=0;p<summary_used;++p) if (same_place(s,&summary[p])) { ++summary[p].all; break; }
        }
        printf("Profile: slow frames over %d s: %u frames, %u samples in their game time, %u samples in all\n",
               SUMMARY_SECONDS,summary_frames,slow_total,all_total);
        for (int shown=0;shown<SUMMARY_SHOWN;++shown) {
            size_t best=summary_used;
            for (size_t i=0;i<summary_used;++i) if (summary[i].slow && (best==summary_used || summary[i].slow>summary[best].slow)) best=i;
            if (best==summary_used) break;
            char name[64];
            printf("Profile:  %5.1f%% of slow, %5.1f%% of all: %s < port 0x%x < 0x%x < 0x%x < 0x%x\n",
                   summary[best].slow*100.0/(slow_total ? slow_total : 1),summary[best].all*100.0/(all_total ? all_total : 1),
                   place_name(summary[best].where,name,sizeof(name)),summary[best].port,summary[best].guest[0],summary[best].guest[1],summary[best].guest[2]);
            summary[best].slow=0;
        }
        summary_used=0;
        summary_frames=0;
    }
}
static void sync_log_install(void) {
    const char *online=getenv("BB_ONLINE"), *profile=getenv("LBPPC_PROFILE");
    const int profiling=profile && profile[0]=='1';
    if (profiling || (online && online[0]=='1')) CloseHandle(CreateThread(NULL,0,thread_load_monitor,NULL,0,NULL));
    if (profiling) {
        extern void (*lbppc_slow_frame_hook)(double,double,double);
        LARGE_INTEGER hz;
        QueryPerformanceFrequency(&hz);
        profile_qpc_hz=hz.QuadPart;
        profile_thread_id=GetCurrentThreadId();
        lbppc_slow_frame_hook=slow_frame_seen;
        CloseHandle(CreateThread(NULL,0,main_thread_profiler,NULL,0,NULL));
    }
    const char *sort_fix=getenv("LBPPC_SORT_KEYS_FIX");
    if (sort_fix ? sort_fix[0]=='1' : getenv("LBPPC_SIM_DIVIDE")!=NULL) {
        static const unsigned char site[]={0x4c,0x89,0xf7,0x4c,0x89,0xfe,0x44,0x89,0xe1};
        if (memcmp(image+SORT_KEYS_SITE,site,sizeof(site))) fprintf(stderr,"Sort: unexpected game code, not applied\n");
        else hooks_install("0x16c9475:9");
    }
    const char *shape_spec=getenv("LBPPC_SHAPE_FIX");
    if (shape_spec ? shape_spec[0]=='1' : getenv("LBPPC_SIM_DIVIDE")!=NULL) {
        static const unsigned char shape[]={0xc5,0xfa,0x10,0x40,0x18};
        if (memcmp(image+SHAPE_SITE,shape,sizeof(shape))) fprintf(stderr,"Shape: unexpected game code, not applied\n");
        else hooks_install("0x16ca145:5");
    }
    const char *positions=getenv("LBPPC_POS_LOG");
    static const unsigned char update[]={0x55,0x48,0x89,0xe5,0x41,0x57};
    if (positions && positions[0]=='1') {
        if (memcmp(image+WORLD_UPDATE,update,sizeof(update))) fprintf(stderr,"Pos: unexpected game code, not logged\n");
        else hooks_install("0x254450:6");
    }
    if (!online || online[0]!='1') return;
    static const unsigned char send[]={0x41,0x8b,0x87,0x60,0x05,0x00,0x00}, receive[]={0x41,0x8b,0x47,0x34,0x85,0xc0};
    if (memcmp(image+SYNC_SEND_SITE,send,7) || memcmp(image+SYNC_RECEIVE_SITE,receive,6)) { fprintf(stderr,"Sync: unexpected game code, not logged\n"); return; }
    hooks_install("0x256d9a:7,0x269ed6:6");
}
static void *allocate(size_t size) {
    void *low=runtime_low_map(size,PROT_READ|PROT_WRITE);
    if (low) return low;
#ifdef _WIN32
    void *p = VirtualAlloc(NULL, size, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!p) fail("VirtualAlloc failed");
#else
    void *p = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) fail("mmap failed");
#endif
    return p;
}
static void protect(void *p, size_t size, unsigned flags) {
#ifdef _WIN32
    DWORD old, mode = PAGE_NOACCESS;
    if (flags & 1) mode = (flags & 2) ? PAGE_EXECUTE_READWRITE : PAGE_EXECUTE_READ;
    else if (flags & 2) mode = PAGE_READWRITE;
    else if (flags & 4) mode = PAGE_READONLY;
    if (!VirtualProtect(p, size, mode, &old)) fail("VirtualProtect failed");
    FlushInstructionCache(GetCurrentProcess(), p, size);
#else
    int mode = ((flags & 4) ? PROT_READ : 0) | ((flags & 2) ? PROT_WRITE : 0) | ((flags & 1) ? PROT_EXEC : 0);
    if (mprotect(p, size, mode)) fail("mprotect failed");
#endif
}
static ABI __attribute__((noreturn)) void unresolved(uint32_t id, uintptr_t argument) {
    if (id >= import_count) fail("bad import trap index");
    printf("STOP: first unsupported PS4 import: %s (index %u)\n", names[id], id);
    printf("API: %s\n", runtime_import_name(names[id]));
    printf("Caller return offset: 0x%" PRIxPTR "; first argument: 0x%" PRIxPTR "\n",
           (uintptr_t)__builtin_return_address(0) - (uintptr_t)image, argument);
    uintptr_t caller=(uintptr_t)__builtin_return_address(0)-(uintptr_t)image;
    for (uint64_t m=0;m<module_count;++m)
        if (caller>=modules[m].base && caller-modules[m].base<modules[m].size)
            printf("Caller in linked module %" PRIu64 " (%s): +0x%" PRIxPTR "\n",m,m==0 ? "libc.prx" : "system module",caller-modules[m].base);
    runtime_report();
    puts(entered_game ? "Original guest entry instructions executed; game initialization is incomplete." :
                        "Native libc initialization is incomplete; game entry has not run.");
    fflush(NULL);
    _exit(20); /* no destructors: GPU, audio and guest threads are still running */
}
#ifndef _WIN32
/* enter_on_stack(entry, arg0, arg1, stack_top): call entry(arg0,arg1) on a new stack. */
void enter_on_stack(void *entry, void *arg0, void *arg1, void *top);
__asm__(".text\n.globl enter_on_stack\nenter_on_stack:\n"
        " push %rbp\n mov %rsp,%rbp\n and $-16,%rcx\n mov %rcx,%rsp\n"
        " mov %rdi,%rax\n mov %rsi,%rdi\n mov %rdx,%rsi\n call *%rax\n"
        " mov %rbp,%rsp\n pop %rbp\n ret\n");
#endif
static ABI void guest_exit(void) { puts("Runtime: process finalizer callback reached"); }
/* The game is built for the PS4's AMD Jaguar CPU: AVX throughout, and BMI1 (andn, bextr, blsi, blsr,
 * tzcnt), MOVBE, LZCNT and POPCNT in thousands of places, its libc's string functions among them.
 * Without them it stopped at the first one with an illegal instruction (0xc000001d in libc.prx
 * while module 0 initialized), except LZCNT, which older Intel CPUs run as BSR (other results).
 * Exit code 24 with a message instead; BB_SKIP_CPU_CHECK=1 starts anyway. The Legacy CPU profile
 * (scripts/cpu_compat.py; run.py sets BB_CPU_COMPAT=1) translates BMI1, LZCNT and MOVBE in the
 * linked image, so only the others stop the game then. */
static void check_cpu(void) {
    if (getenv("BB_SKIP_CPU_CHECK")) return;
    const int translated=getenv("BB_CPU_COMPAT")!=NULL;
    unsigned a=0, b=0, c=0, d=0, basic=0, leaf7=0, extended=0;
    if (__get_cpuid(1,&a,&b,&c,&d)) basic=c;
    if (__get_cpuid_max(0,NULL)>=7) { __cpuid_count(7,0,a,b,c,d); leaf7=b; }
    if (__get_cpuid(0x80000001,&a,&b,&c,&d)) extended=c;
    int avx=0;
    if (((basic>>27)&1) && ((basic>>28)&1)) { /* OSXSAVE and AVX: the OS also has to save YMM state */
        unsigned low, high;
        __asm__ volatile("xgetbv" : "=a"(low), "=d"(high) : "c"(0));
        avx=(low&6)==6;
    }
    const struct { int present, translatable; const char *name; } features[]={
        {avx,0,"AVX"}, {(leaf7>>3)&1,1,"BMI1"}, {(basic>>22)&1,1,"MOVBE"}, {(extended>>5)&1,1,"LZCNT"},
        {(basic>>23)&1,0,"POPCNT"}, {(basic>>20)&1,0,"SSE4.2"},
    };
    char missing[64]="", translations[64]="";
    for (size_t i=0;i<sizeof(features)/sizeof(*features);++i) {
        if (features[i].present) continue;
        char *list=translated && features[i].translatable ? translations : missing;
        if (*list) strcat(list,", ");
        strcat(list,features[i].name);
    }
    if (*translations) printf("CPU: no %s; the Legacy CPU translations run instead (best effort)\n",translations);
    if (!*missing) return;
    unsigned words[12]={0};
    char brand[49]="";
    if (__get_cpuid_max(0x80000000,NULL)>=0x80000004)
        for (unsigned i=0;i<3;++i) __get_cpuid(0x80000002+i,&words[i*4],&words[i*4+1],&words[i*4+2],&words[i*4+3]);
    memcpy(brand,words,48);
    for (size_t n=strlen(brand); n && brand[n-1]==' '; --n) brand[n-1]=0;
    const char *name=brand;
    while (*name==' ') ++name;
    if (!*name) name="unknown processor";
    fprintf(stderr,"STOP: unsupported CPU %s: missing %s (needs Intel Core 4th generation or newer, or AMD Ryzen)\n",name,missing);
#ifdef _WIN32
    char text[512];
    snprintf(text,sizeof(text),"This processor cannot run the game:\n%s\n\nMissing instructions: %s\n\n"
             "The game uses instructions of the PS4's processor. They need an Intel Core of the 4th "
             "generation (Haswell, 2013) or newer, or an AMD Ryzen.",name,missing);
    MessageBoxA(NULL,text,"LBPPC",MB_OK|MB_ICONERROR|MB_SETFOREGROUND);
#endif
    exit(24);
}
#ifdef _WIN32
static void describe_address(char *out, size_t size, uintptr_t address) {
    HMODULE module=NULL;
    char name[MAX_PATH]="?";
    if (image && address-(uintptr_t)image<0x20000000) { snprintf(out,size,"guest offset 0x%" PRIxPTR,address-(uintptr_t)image); return; }
    if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,(LPCSTR)address,&module) && module) {
        GetModuleFileNameA(module,name,sizeof(name));
        const char *base=strrchr(name,'\\');
        snprintf(out,size,"%s+0x%" PRIxPTR,base ? base+1 : name,address-(uintptr_t)module);
        return;
    }
    snprintf(out,size,"%p",(void *)address);
}
static int fatal_reported;
static LONG fatal_exception(EXCEPTION_POINTERS *info) {
    if (__atomic_exchange_n(&fatal_reported,1,__ATOMIC_ACQ_REL)) { Sleep(INFINITE); }
    const EXCEPTION_RECORD *record=info->ExceptionRecord;
    const CONTEXT *c=info->ContextRecord;
    char where[MAX_PATH+64];
    describe_address(where,sizeof(where),(uintptr_t)c->Rip);
    if (record->ExceptionCode==EXCEPTION_ACCESS_VIOLATION && record->NumberParameters>=2)
        fprintf(stderr,"Fault: %s access to %p at %s (thread %lu)\n",
                record->ExceptionInformation[0]==1 ? "write" : record->ExceptionInformation[0]==8 ? "execute" : "read",
                (void *)record->ExceptionInformation[1],where,GetCurrentThreadId());
    else
        fprintf(stderr,"Fault: exception 0x%08lx at %s (thread %lu)\n",record->ExceptionCode,where,GetCurrentThreadId());
    fprintf(stderr,"  rax=%016llx rbx=%016llx rcx=%016llx rdx=%016llx\n  rsi=%016llx rdi=%016llx rbp=%016llx rsp=%016llx\n",
            c->Rax,c->Rbx,c->Rcx,c->Rdx,c->Rsi,c->Rdi,c->Rbp,c->Rsp);
    fprintf(stderr,"  r8=%016llx  r9=%016llx  r10=%016llx r11=%016llx\n  r12=%016llx r13=%016llx r14=%016llx r15=%016llx\n",
            c->R8,c->R9,c->R10,c->R11,c->R12,c->R13,c->R14,c->R15);
    {
        typedef HRESULT (WINAPI *GetDescription)(HANDLE,PWSTR *);
        GetDescription get=(GetDescription)(void *)GetProcAddress(GetModuleHandleA("kernel32.dll"),"GetThreadDescription");
        PWSTR wide=NULL;
        char name[96]="";
        if (get && SUCCEEDED(get(GetCurrentThread(),&wide)) && wide) {
            WideCharToMultiByte(CP_UTF8,0,wide,-1,name,sizeof(name),NULL,NULL);
            LocalFree(wide);
        }
        fprintf(stderr,"  thread name '%s'\n",name);
    }
    {   /* lbppc diagnostics: physical aliases of the registers' targets */
        extern void runtime_memory_report_aliases(uintptr_t address);
        const CONTEXT *c=info->ContextRecord;
        const DWORD64 regs[]={c->Rbx,c->Rsi,c->Rdi,c->R11,c->R14};
        for (int i=0; i<5; ++i) if (regs[i]>=0x1000000000ull && regs[i]<0xfc00000000ull) runtime_memory_report_aliases((uintptr_t)regs[i]);
    }
    if (gpu_enabled) bbgpu_dump_guest_writes(info);
    /* Return addresses near the top of the stack: system libraries (the C runtime's memcpy)
     * keep no frame pointers, so the rbp chain below can skip their caller. */
    for (int slot=0, shown=0; slot<64 && shown<6; ++slot) {
        uintptr_t value=0;
        SIZE_T got=0;
        if (!ReadProcessMemory(GetCurrentProcess(),(void *)(uintptr_t)(c->Rsp+slot*8),&value,sizeof(value),&got) || got!=sizeof(value)) break;
        HMODULE owner=NULL;
        int code_ref=(image && value-(uintptr_t)image<0x20000000) ||
                     (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,(LPCSTR)value,&owner) && owner==GetModuleHandleA(NULL));
        if (!code_ref) continue;
        describe_address(where,sizeof(where),value);
        fprintf(stderr,"  [rsp+0x%x] %s\n",slot*8,where);
        ++shown;
    }
    /* rbp chain (guest and host code keep frame pointers). */
    uintptr_t rbp=(uintptr_t)c->Rbp;
    for (int depth=0; depth<24 && rbp; ++depth) {
        uintptr_t frame[2];
        SIZE_T got=0;
        if (!ReadProcessMemory(GetCurrentProcess(),(void *)rbp,frame,sizeof(frame),&got) || got!=sizeof(frame) || frame[0]<=rbp) break;
        describe_address(where,sizeof(where),frame[1]);
        fprintf(stderr,"  #%d %s\n",depth,where);
        rbp=frame[0];
    }
    fflush(NULL);
    TerminateProcess(GetCurrentProcess(),3);
    return EXCEPTION_CONTINUE_SEARCH;
}
/* --timeout seconds: where every thread is (name, instruction, return addresses on its stack), then exit 4.
 * For a game that hangs instead of stopping. */
static DWORD WINAPI watchdog_thread(LPVOID arg) {
    Sleep((DWORD)(uintptr_t)arg*1000);
    typedef HRESULT (WINAPI *GetDescription)(HANDLE,PWSTR *);
    GetDescription describe=(GetDescription)(void *)GetProcAddress(GetModuleHandleA("kernel32.dll"),"GetThreadDescription");
    fprintf(stderr,"Watchdog: %lu s passed; threads:\n",(unsigned long)(uintptr_t)arg);
    HANDLE snapshot=CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD,0);
    THREADENTRY32 entry={.dwSize=sizeof(entry)};
    for (BOOL more=Thread32First(snapshot,&entry); more; more=Thread32Next(snapshot,&entry)) {
        if (entry.th32OwnerProcessID!=GetCurrentProcessId() || entry.th32ThreadID==GetCurrentThreadId()) continue;
        HANDLE thread=OpenThread(THREAD_SUSPEND_RESUME|THREAD_GET_CONTEXT|THREAD_QUERY_LIMITED_INFORMATION,FALSE,entry.th32ThreadID);
        if (!thread) continue;
        char name[96]="";
        PWSTR wide=NULL;
        if (describe && SUCCEEDED(describe(thread,&wide)) && wide) {
            WideCharToMultiByte(CP_UTF8,0,wide,-1,name,sizeof(name),NULL,NULL);
            LocalFree(wide);
        }
        /* Copy the registers and the top of the stack, then let the thread go at once: a suspended thread
           can hold a lock that printing needs (stdio's, the loader's). */
        CONTEXT c={.ContextFlags=CONTEXT_FULL};
        static uintptr_t stack[512];
        size_t words=0;
        int captured=0;
        if (SuspendThread(thread)!=(DWORD)-1) {
            captured=GetThreadContext(thread,&c);
            for (; captured && words<512; ++words) {
                SIZE_T got=0;
                if (!ReadProcessMemory(GetCurrentProcess(),(void *)(uintptr_t)(c.Rsp+words*8),&stack[words],8,&got) || got!=8) break;
            }
            ResumeThread(thread);
        }
        if (captured) {
            char where[MAX_PATH+64];
            describe_address(where,sizeof(where),(uintptr_t)c.Rip);
            fprintf(stderr,"  thread %lu '%s' at %s\n",entry.th32ThreadID,name,where);
            for (size_t slot=0, shown=0; slot<words && shown<8; ++slot) {
                uintptr_t value=stack[slot];
                HMODULE owner=NULL;
                int code_ref=(image && value-(uintptr_t)image<0x20000000) ||
                             (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,(LPCSTR)value,&owner) && owner==GetModuleHandleA(NULL));
                if (!code_ref) continue;
                describe_address(where,sizeof(where),value);
                fprintf(stderr,"    [rsp+0x%zx] %s\n",slot*8,where);
                ++shown;
            }
        }
        CloseHandle(thread);
    }
    CloseHandle(snapshot);
    fflush(NULL);
    TerminateProcess(GetCurrentProcess(),4);
    return 0;
}
static LONG CALLBACK vectored_fault(EXCEPTION_POINTERS *info) {
    const EXCEPTION_RECORD *record=info->ExceptionRecord;
    DWORD code=record->ExceptionCode;
    if ((code==STATUS_FLOAT_MULTIPLE_TRAPS || code==EXCEPTION_FLT_INVALID_OPERATION ||
         code==EXCEPTION_FLT_DIVIDE_BY_ZERO) && getenv("LBPPC_FPTRAP") && fptrap_report(info))
        return EXCEPTION_CONTINUE_EXECUTION;
    if (code==EXCEPTION_SINGLE_STEP && (info->ContextRecord->Dr6&15)) { /* BB_WATCH / BB_WATCH_ADDR: a write */
        /* BB_WATCH_MAX=n: hits printed (default 40). */
        static long watch_max=-1;
        if (watch_max<0) watch_max=getenv("BB_WATCH_MAX") ? strtol(getenv("BB_WATCH_MAX"),NULL,0) : 40;
        /* BB_WATCH_VALUE=u32: only writes leaving that value in either half of the watched 8 bytes. */
        int wanted=1;
        if (getenv("BB_WATCH_VALUE")) {
            static uint32_t want; static int want_read;
            if (!want_read) { want=(uint32_t)strtoul(getenv("BB_WATCH_VALUE"),NULL,0); want_read=1; }
            DWORD64 d6=info->ContextRecord->Dr6;
            DWORD64 s=d6&1 ? info->ContextRecord->Dr0 : d6&2 ? info->ContextRecord->Dr1 :
                      d6&4 ? info->ContextRecord->Dr2 : info->ContextRecord->Dr3;
            const uint32_t *h=(const uint32_t *)(uintptr_t)s;
            wanted=h[0]==want || h[1]==want;
        }
        if (wanted && InterlockedIncrement(&watch_hits)<=watch_max) {
            char line[512], where[MAX_PATH+64], from[MAX_PATH+64];
            describe_address(where,sizeof(where),(uintptr_t)info->ContextRecord->Rip);
            DWORD64 dr6=info->ContextRecord->Dr6;
            DWORD64 slot=dr6&1 ? info->ContextRecord->Dr0 : dr6&2 ? info->ContextRecord->Dr1 :
                         dr6&4 ? info->ContextRecord->Dr2 : info->ContextRecord->Dr3;
            uint64_t value=*(uint64_t *)(uintptr_t)slot;
            uint64_t top=*(uint64_t *)(uintptr_t)info->ContextRecord->Rsp;
            describe_address(from,sizeof(from),(uintptr_t)top);
            const CONTEXT *cx=info->ContextRecord;
            int n=snprintf(line,sizeof(line),"WATCH write to %p before %s, value %016llx, [rsp] %s (thread %lu)"
                           " rsi %llx rdi %llx rcx %llx rdx %llx\n",
                           (void *)(uintptr_t)slot,where,(unsigned long long)value,from,GetCurrentThreadId(),
                           (unsigned long long)cx->Rsi,(unsigned long long)cx->Rdi,(unsigned long long)cx->Rcx,
                           (unsigned long long)cx->Rdx);
            /* The guest code that led here: the first image addresses on the stack (BB_WATCH_STACK=1). */
            if (getenv("BB_WATCH_STACK") && n>0 && n<(int)sizeof(line)) {
                --n;
                const uint64_t *sp=(const uint64_t *)(uintptr_t)cx->Rsp;
                int found=0;
                for (int k=0; k<512 && found<6; ++k) {
                    uint64_t v; SIZE_T got;
                    if (!ReadProcessMemory(GetCurrentProcess(),sp+k,&v,8,&got)) break;
                    if (image && v-(uintptr_t)image<image_size_hint && n<(int)sizeof(line)-24) {
                        n+=snprintf(line+n,sizeof(line)-n," g%llx",(unsigned long long)(v-(uintptr_t)image)); ++found;
                    }
                }
                if (n<(int)sizeof(line)-1) line[n++]='\n';
            }
            DWORD written;
            WriteFile(GetStdHandle(STD_ERROR_HANDLE),line,(DWORD)n,&written,NULL);
        }
        info->ContextRecord->Dr6=0;
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    if (code==EXCEPTION_ACCESS_VIOLATION && record->NumberParameters>=2) {
        /* GPU page tracking (write-protected guest pages) is resolved first. */
        if (gpu_enabled && bbgpu_handle_fault(info,(void *)record->ExceptionInformation[1])) return EXCEPTION_CONTINUE_EXECUTION;
        if (runtime_fault_recover) {
            /* A speculative guest memory read (GPU draw preparation) faulted: the thread resumes
             * in bb_longjmp, outside the exception dispatcher, back at its recovery point. */
            unsigned long long *buffer=*runtime_fault_recover;
            runtime_fault_recover=NULL;
            CONTEXT *c=info->ContextRecord;
            c->Rsp=((c->Rsp-128)&~(DWORD64)15)-40;
            c->Rcx=(DWORD64)(uintptr_t)buffer;
            c->Rdx=1;
            c->Rip=(DWORD64)(uintptr_t)bb_longjmp;
            return EXCEPTION_CONTINUE_EXECUTION;
        }
    }
    if (code==EXCEPTION_ACCESS_VIOLATION || code==EXCEPTION_ILLEGAL_INSTRUCTION || code==EXCEPTION_PRIV_INSTRUCTION ||
        code==EXCEPTION_STACK_OVERFLOW || code==EXCEPTION_INT_DIVIDE_BY_ZERO || code==EXCEPTION_DATATYPE_MISALIGNMENT) {
        /* Faults in guest code, the loader or the runtime end the process with a report; drivers
         * and system libraries may handle their own exceptions. */
        uintptr_t rip=(uintptr_t)info->ContextRecord->Rip;
        HMODULE self=GetModuleHandleA(NULL), module=NULL;
        int ours=(image && rip-(uintptr_t)image<0x20000000) ||
                 (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,(LPCSTR)rip,&module) && module==self) ||
                 !module;
        /* bbport: the C runtime handles none of its own faults (a bad pointer to memcpy or
         * free from the port or the game): reported with the call chain into them, instead of
         * Windows ending the process without a word (crashes after 1-2 hours in ucrtbase.dll). */
        if (!ours && module && (module==GetModuleHandleA("ucrtbase.dll") || module==GetModuleHandleA("msvcrt.dll") ||
                                module==GetModuleHandleA("vcruntime140.dll")))
            ours=1;
        if (ours) return fatal_exception(info);
        /* Not ours (a driver, SDL, Windows): its owner may handle it. The first few are noted with WriteFile
         * alone (no locks), since a process that then ends with no report died of one of them. */
        static volatile long noted;
        if (InterlockedIncrement(&noted)<=8) {
            char line[256], where[MAX_PATH+64];
            describe_address(where,sizeof(where),rip);
            int n=snprintf(line,sizeof(line),"Exception 0x%08lx in %s, address %p, rsp %p (thread %lu), passed on\n",
                           code,where,record->NumberParameters>=2 ? (void *)record->ExceptionInformation[1] : NULL,
                           (void *)(uintptr_t)info->ContextRecord->Rsp,GetCurrentThreadId());
            DWORD written;
            if (n>0) WriteFile(GetStdHandle(STD_ERROR_HANDLE),line,(DWORD)n,&written,NULL);
        }
    }
    /* BB_STRICT_HANDLES=1: a closed or invalid handle used anywhere is reported (diagnostics). */
    if (code==0xC0000008 /* STATUS_INVALID_HANDLE */) return fatal_exception(info);
    return EXCEPTION_CONTINUE_SEARCH;
}
static LONG WINAPI unhandled_fault(EXCEPTION_POINTERS *info) { return fatal_exception(info); }
/* Guest code reads its TCB with `mov rax, gs:[0]` (link_*.py rewrote fs:[0]); on Windows
 * the displacement becomes the TEB TLS slot that holds the guest TCB (runtime_thread.c). */
uint32_t runtime_thread_tcb_offset(void);
static uint64_t patch_tcb_loads(const Segment *segments, uint64_t count) {
    static const unsigned char load[]={0x65,0x48,0x8b,0x04,0x25,0,0,0,0};
    uint32_t displacement=runtime_thread_tcb_offset();
    uint64_t patched=0;
    for (uint64_t s=0;s<count;++s) {
        if (!(segments[s].flags&1) || segments[s].size<sizeof(load)) continue;
        unsigned char *at=image+segments[s].address, *end=at+segments[s].size-sizeof(load);
        for (; at<=end; ++at)
            if (at[0]==0x65 && !memcmp(at,load,sizeof(load))) { memcpy(at+5,&displacement,4); ++patched; at+=sizeof(load)-1; }
    }
    return patched;
}
#endif
#ifndef _WIN32
static void fault(int sig, siginfo_t *info, void *context) {
    /* GPU page tracking (write-protected guest pages) is resolved first. */
    if (gpu_enabled && sig == SIGSEGV && bbgpu_handle_fault(context, info->si_addr)) return;
    /* A speculative guest memory read (runtime_memory.c) failed: resume its recovery point. */
    if ((sig == SIGSEGV || sig == SIGBUS) && runtime_fault_recover) {
        sigjmp_buf *recover = runtime_fault_recover;
        runtime_fault_recover = NULL;
        sigset_t unblock;
        sigemptyset(&unblock);
        sigaddset(&unblock, sig);
        pthread_sigmask(SIG_UNBLOCK, &unblock, NULL);
        siglongjmp(*recover, 1);
    }
    /* The process is terminating: dladdr/snprintf are acceptable here. */
    ucontext_t *uc = context;
    uintptr_t rip = (uintptr_t)uc->uc_mcontext.gregs[REG_RIP];
    char line[512];
    Dl_info where;
    if (rip - (uintptr_t)image < 0x10000000)
        snprintf(line, sizeof(line), "Guest fault (signal %d) at guest offset 0x%lx, address %p\n",
                 sig, (unsigned long)(rip - (uintptr_t)image), info->si_addr);
    else if (dladdr((void *)rip, &where) && where.dli_fname)
        snprintf(line, sizeof(line), "Host fault (signal %d) in %s+0x%lx (%s), address %p\n", sig, where.dli_fname,
                 (unsigned long)(rip - (uintptr_t)where.dli_fbase), where.dli_sname ? where.dli_sname : "?", info->si_addr);
    else
        snprintf(line, sizeof(line), "Fault (signal %d) at RIP %p, address %p\n", sig, (void *)rip, info->si_addr);
    { ssize_t written_=write(2, line, strlen(line)); (void)written_; }
    if (gpu_enabled) bbgpu_dump_guest_writes(context);
    /* Host call chain (frames with unwind info; guest frames end it). */
    void *frames[32];
    int depth = backtrace(frames, 32);
    for (int i = 2; i < depth; ++i) {
        if (dladdr(frames[i], &where) && where.dli_fname)
            snprintf(line, sizeof(line), "  #%d %s+0x%lx (%s)\n", i, where.dli_fname,
                     (unsigned long)((uintptr_t)frames[i] - (uintptr_t)where.dli_fbase), where.dli_sname ? where.dli_sname : "?");
        else
            snprintf(line, sizeof(line), "  #%d %p\n", i, frames[i]);
        ssize_t written_=write(2, line, strlen(line)); (void)written_;
    }
    _exit(128 + sig);
}
#endif
#ifndef _WIN32
/* Watchdog: dump RIP and the rbp frame chain of every thread (guest offsets
 * when inside the image). Reads use process_vm_readv so bad frames cannot fault. */
static uintptr_t exe_base;
static void write_hex(char *out, uint64_t v) {
    const char digits[] = "0123456789abcdef";
    for (int i = 0; i < 16; ++i) out[i] = digits[(v >> (60 - i * 4)) & 15];
}
static void dump_frames(ucontext_t *uc) {
    char line[] = "  tid=0000000000000000 rip=0000000000000000 image-relative=0000000000000000 host-relative=0000000000000000\n";
    uintptr_t rip=(uintptr_t)uc->uc_mcontext.gregs[REG_RIP], rbp=(uintptr_t)uc->uc_mcontext.gregs[REG_RBP];
    uint64_t tid=(uint64_t)gettid();
    /* First argument register: the lock address when a thread waits on a futex. */
    char arg[]="  tid=0000000000000000 rdi=0000000000000000\n";
    write_hex(arg+6,tid); write_hex(arg+27,(uint64_t)uc->uc_mcontext.gregs[REG_RDI]);
    { ssize_t written_=write(2,arg,sizeof(arg)-1); (void)written_; }
    for (int depth=0; depth<24; ++depth) {
        write_hex(line+6,tid); write_hex(line+27,rip); write_hex(line+59,rip-(uintptr_t)image); write_hex(line+90,rip-exe_base);
        { ssize_t written_=write(2,line,sizeof(line)-1); (void)written_; }
        uintptr_t frame[2];
        struct iovec local={frame,sizeof(frame)}, remote={(void *)rbp,sizeof(frame)};
        if (!rbp || process_vm_readv(getpid(),&local,1,&remote,1,0)!=(ssize_t)sizeof(frame)) break;
        if (frame[0]<=rbp) break;
        rbp=frame[0]; rip=frame[1];
    }
}
static void thread_dump(int sig, siginfo_t *info, void *context) { (void)sig; (void)info; dump_frames(context); }
static void watchdog(int sig, siginfo_t *info, void *context) {
    (void)info;
    const char head[]="STOP: watchdog timeout; thread stacks:\n";
    { ssize_t written_=write(2,head,sizeof(head)-1); (void)written_; }
    dump_frames(context);
    int dir=open("/proc/self/task",O_RDONLY|O_DIRECTORY);
    char buffer[4096];
    long n;
    pid_t self=gettid();
    while (dir>=0 && (n=syscall(SYS_getdents64,dir,buffer,sizeof(buffer)))>0)
        for (long at=0; at<n;) {
            struct { uint64_t ino; int64_t off; unsigned short reclen; unsigned char type; char name[]; } *d=(void *)(buffer+at);
            pid_t tid=(pid_t)strtol(d->name,NULL,10);
            if (tid>0 && tid!=self) { syscall(SYS_tgkill,getpid(),tid,SIGUSR2); usleep(20000); }
            at+=d->reclen;
        }
    usleep(100000);
    _exit(128 + sig);
}
#endif
/* param.sfo lookup: string or integer value of key, 0 when absent. */
int runtime_sfo_value(const char *path, const char *key, char *text, size_t text_size, uint32_t *number) {
    FILE *f=fopen(path,"rb");
    if (!f) return 0;
    unsigned char data[65536];
    size_t n=fread(data,1,sizeof(data),f); fclose(f);
    if (n<20 || memcmp(data,"\0PSF",4)) return 0;
    uint32_t keys, values, count;
    memcpy(&keys,data+8,4); memcpy(&values,data+12,4); memcpy(&count,data+16,4);
    for (uint32_t i=0;i<count && 20+i*16+16<=n;++i) {
        const unsigned char *e=data+20+i*16;
        uint16_t key_offset, format; uint32_t length, offset;
        memcpy(&key_offset,e,2); memcpy(&format,e+2,2); memcpy(&length,e+4,4); memcpy(&offset,e+12,4);
        if (keys+key_offset>=n || values+offset+length>n || strcmp((const char *)data+keys+key_offset,key)) continue;
        if (format==0x0404 && number && length>=4) { memcpy(number,data+values+offset,4); return 1; }
        if (text && text_size) {
            size_t copy=length<text_size-1 ? length : text_size-1;
            memcpy(text,data+values+offset,copy); text[copy]=0;
        }
        return 1;
    }
    return 0;
}
static int mapped(Segment *segments, uint64_t count, uint64_t address, uint64_t bytes) {
    for (uint64_t i = 0; i < count; ++i)
        if (address >= segments[i].address && bytes <= segments[i].size &&
            address - segments[i].address <= segments[i].size - bytes) return 1;
    return 0;
}
/* Patches may also write past an executable segment's end up to its 4 KiB page boundary: those
 * bytes are mapped with the segment on every host (and PS4) and unused by the game, so they hold
 * patch code that does not fit in place (quit_menu.py). */
static int patchable(Segment *segments, uint64_t count, uint64_t address, uint64_t bytes) {
    if (mapped(segments, count, address, bytes)) return 1;
    for (uint64_t i = 0; i < count; ++i) {
        const uint64_t end = segments[i].address + segments[i].size, tail = (end + 4095) & ~(uint64_t)4095;
        if ((segments[i].flags & 1) && address >= end && bytes <= tail - end && address - end <= tail - end - bytes)
            return 1;
    }
    return 0;
}
/* LBPPC_POKE / LBPPC_POKE_LATE (test): off:hex[,off:hex...] byte writes at image offsets; `live` while the game
 * runs (code made writable for the write). `saved` keeps the bytes replaced, for undoing them. */
static unsigned char poke_saved[4096];
static size_t poke_saved_used;
static void apply_pokes(const char *p, Segment *segments, uint64_t ns, int live) {
    while (p && *p) {
        char *end; uint64_t off=strtoull(p,&end,16), n=0;
        if (*end!=':') break;
        unsigned char bytes[256];
        for (p=end+1; isxdigit((unsigned char)p[0]) && isxdigit((unsigned char)p[1]) && n<sizeof(bytes); p+=2, ++n) {
            char byte[3]={p[0],p[1],0};
            bytes[n]=(unsigned char)strtoul(byte,NULL,16);
        }
        if (n && patchable(segments,ns,off,n)) {
            DWORD old=0;
            if (live) VirtualProtect(image+off,n,PAGE_EXECUTE_READWRITE,&old);
            if (poke_saved_used+n<=sizeof(poke_saved)) { memcpy(poke_saved+poke_saved_used,image+off,n); poke_saved_used+=n; }
            memcpy(image+off,bytes,n);
            if (live) { VirtualProtect(image+off,n,old,&old); FlushInstructionCache(GetCurrentProcess(),image+off,n); }
            printf("Runtime: poked %" PRIu64 " bytes at image offset %#" PRIx64 "%s\n",n,off,live ? " (live)" : "");
        }
        if (*p==',') ++p;
    }
}
static struct { Segment *segments; uint64_t count; } late_pokes;
static DWORD WINAPI late_poke_thread(LPVOID arg) {
    (void)arg;
    const char *at=getenv("LBPPC_POKE_AT"), *span=getenv("LBPPC_POKE_FOR"), *trigger=getenv("LBPPC_POKE_TRIGGER");
    /* LBPPC_POKE_TRIGGER=file: applied when the file appears (and removed), instead of after LBPPC_POKE_AT. */
    if (trigger) { while (remove(trigger)!=0) Sleep(50); }
    else Sleep((DWORD)(at ? strtoul(at,NULL,0) : 60)*1000);
    size_t first=poke_saved_used;
    apply_pokes(getenv("LBPPC_POKE_LATE"),late_pokes.segments,late_pokes.count,1);
    if (!span) return 0;
    Sleep((DWORD)strtoul(span,NULL,0)*1000);
    /* Undo: the saved bytes go back in the order they were taken. */
    size_t used=first;
    for (const char *p=getenv("LBPPC_POKE_LATE"); p && *p; ) {
        char *end; uint64_t off=strtoull(p,&end,16), n=0;
        if (*end!=':') break;
        for (p=end+1; isxdigit((unsigned char)p[0]) && isxdigit((unsigned char)p[1]); p+=2) ++n;
        DWORD old=0;
        VirtualProtect(image+off,n,PAGE_EXECUTE_READWRITE,&old);
        memcpy(image+off,poke_saved+used,n); used+=n;
        VirtualProtect(image+off,n,old,&old); FlushInstructionCache(GetCurrentProcess(),image+off,n);
        printf("Runtime: unpoked %" PRIu64 " bytes at image offset %#" PRIx64 "\n",n,off);
        if (*p==',') ++p;
    }
    return 0;
}
/* BBPATCH2 (patches.py): the patches' image base, then byte writes at image offsets, applied
 * after relocation. A write may replace a whole base-relative pointer slot (60/90 FPS++ swap
 * function pointers): the patch holds the address at the patches' base, rebased here. */
static void apply_patches(const char *path, Segment *segments, uint64_t ns, const Reloc *relocs, uint64_t nr) {
    FILE *f=fopen(path,"rb");
    char magic[8];
    if (!f || fread(magic,1,8,f)!=8 || memcmp(magic,"BBPATCH2",8)) fail("invalid patch file");
    uint64_t base=read64(f), count=read64(f), bytes=0, rebased=0;
    unsigned char data[4096];
    for (uint64_t i=0;i<count;++i) {
        uint64_t offset=read64(f), length=read64(f);
        if (!length || length>sizeof(data) || !patchable(segments,ns,offset,length) || fread(data,1,length,f)!=length)
            fail("bad patch entry");
        uint64_t slots[sizeof(data)/8]; size_t nslots=0;
        for (uint64_t r=0;r<nr;++r) {
            if (!(relocs[r].target<offset+length && offset<relocs[r].target+8)) continue;
            uint64_t target=relocs[r].target, value;
            if (relocs[r].kind || target<offset || target+8>offset+length) fail("patch overlaps a relocation");
            memcpy(&value,data+(target-offset),8);
            if (value<base || !patchable(segments,ns,value-base,1)) fail("patch writes a pointer outside the image");
            slots[nslots++]=target;
        }
        memcpy(image+offset,data,length);
        for (size_t s=0;s<nslots;++s) {
            uint64_t value;
            memcpy(&value,image+slots[s],8);
            value=(uint64_t)(uintptr_t)image+(value-base);
            memcpy(image+slots[s],&value,8);
        }
        rebased+=nslots;
        bytes+=length;
    }
    if (fgetc(f)!=EOF) fail("trailing data in patch file");
    fclose(f);
    printf("Patches: %" PRIu64 " writes, %" PRIu64 " bytes applied, %" PRIu64 " pointers rebased\n",count,bytes,rebased);
}
/* Restarts the game through run.sh (the settings menu: a new render resolution is a patch
 * applied at start). Descriptors are closed first so the old GPU device and its memory are
 * released before the new process opens its own. */
void runtime_restart(void) {
    fflush(NULL);
#ifdef _WIN32
    /* lbppc: run.py starts the game again when it ends with this code (the menu's Apply and restart). */
    puts("Runtime: restart requested");
    fflush(NULL);
    TerminateProcess(GetCurrentProcess(),75);
#else
    puts("Runtime: restarting through run.sh");
    syscall(SYS_close_range, 3u, ~0u, 0u);
    execlp("bash", "bash", "run.sh", (char *)NULL);
    perror("runtime_restart: exec");
    _exit(1);
#endif
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
#ifndef _WIN32
    /* Keep host heap objects handed to the guest (thread handles, TLS) in the
       non-PIE brk heap, i.e. below 1 TiB: the guest packs pointers into 40 bits. */
    mallopt(M_ARENA_MAX,1);
    mallopt(M_MMAP_THRESHOLD,32*1024*1024);
#endif
    if (argc == 2 && !strcmp(argv[1], "--vulkan-only")) return vulkan_smoke();
#ifdef _WIN32
    stub_pool_reserve();
#endif
    check_cpu();
#ifdef _WIN32
    /* The PS4 user range must stay free of host allocations (GPU driver, heaps), and the guest
     * TCB needs one of the 64 TEB TLS slots before DLLs (Vulkan drivers) take them. */
    runtime_memory_reserve();
    runtime_thread_tcb_offset();
    /* Windows 11 throttles a process whose window is not in front (EcoQoS: lower clocks, and
     * the 1 ms timer resolution ignored): the guest's timed waits then round to 15.6 ms. LBP3
     * fell from 30 to 15 FPS a few minutes into every unattended session. */
    PROCESS_POWER_THROTTLING_STATE throttling={
        .Version=PROCESS_POWER_THROTTLING_CURRENT_VERSION,
        .ControlMask=PROCESS_POWER_THROTTLING_EXECUTION_SPEED|PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION,
        .StateMask=0};
    SetProcessInformation(GetCurrentProcess(),ProcessPowerThrottling,&throttling,sizeof(throttling));
    timeBeginPeriod(1);
#endif
    int cpu_only = 0, strict_imports = 0;
    unsigned timeout_seconds = 10;
    const char *content_profile=NULL, *app0=NULL, *user_dir=NULL, *patch_file=NULL;
    for (int i = 2; i < argc; ++i) {
        if (!strcmp(argv[i], "--cpu-only")) cpu_only = 1;
        else if (!strcmp(argv[i], "--strict-imports")) strict_imports = 1;
        else if (!strcmp(argv[i], "--content-profile") && i+1<argc) content_profile=argv[++i];
        else if (!strcmp(argv[i], "--app0") && i+1<argc) app0=argv[++i];
        else if (!strcmp(argv[i], "--user") && i+1<argc) user_dir=argv[++i];
        else if (!strcmp(argv[i], "--patches") && i+1<argc) patch_file=argv[++i];
        else if (!strcmp(argv[i], "--timeout") && i+1<argc) timeout_seconds=(unsigned)strtoul(argv[++i],NULL,10);
        else { fprintf(stderr, "Unknown option: %s\n", argv[i]); return 1; }
    }
    if (argc < 2) {
        fprintf(stderr, "Usage: %s boot.bin [--cpu-only] [--strict-imports] [--content-profile file] [--app0 dir] [--user dir] [--patches file] [--timeout seconds] | --vulkan-only\n", argv[0]);
        return 1;
    }
    if (content_profile) {
        FILE *profile=fopen(content_profile,"rb");
        unsigned char data[28];
        if (!profile) fail("cannot open content profile");
        if (fread(data,1,sizeof(data),profile)!=sizeof(data) || fgetc(profile)!=EOF || memcmp(data,"BBCONT01",8)) fail("invalid content profile");
        fclose(profile);
        uint32_t values[5];
        for (unsigned i=0;i<5;++i) {
            unsigned char *p=data+8+i*4;
            values[i]=(uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;
        }
        runtime_content_configure(values);
    }
    if (app0) {
        runtime_file_configure(app0, user_dir ? user_dir : "user");
        char sfo[4096], id[16]="";
        snprintf(sfo,sizeof(sfo),"%s/sce_sys/param.sfo",app0);
        if (runtime_sfo_value(sfo,"INSTALL_DIR_SAVEDATA",id,sizeof(id),NULL) || runtime_sfo_value(sfo,"TITLE_ID",id,sizeof(id),NULL))
            runtime_savedata_configure(id);
        char serial[16]="", title[128]="", version[16]="";
        runtime_sfo_value(sfo,"TITLE_ID",serial,sizeof(serial),NULL);
        runtime_sfo_value(sfo,"TITLE",title,sizeof(title),NULL);
        runtime_sfo_value(sfo,"APP_VER",version,sizeof(version),NULL);
        bbnet_configure(app0,serial,title,version);
    }
    bbgpu_register_kernel();
#ifdef _WIN32
    SYSTEM_INFO system_info; GetSystemInfo(&system_info); page_size = system_info.dwPageSize;
    AddVectoredExceptionHandler(1, vectored_fault);
    SetUnhandledExceptionFilter(unhandled_fault);
    if (getenv("BB_STRICT_HANDLES")) {
        PROCESS_MITIGATION_STRICT_HANDLE_CHECK_POLICY strict={0};
        strict.RaiseExceptionOnInvalidHandleReference=1;
        strict.HandleExceptionsPermanentlyEnabled=1;
        if (!SetProcessMitigationPolicy(ProcessStrictHandleCheckPolicy,&strict,sizeof(strict)))
            fprintf(stderr,"BB_STRICT_HANDLES: policy not set (%lu)\n",GetLastError());
    }
    if (timeout_seconds) CloseHandle(CreateThread(NULL,0,watchdog_thread,(LPVOID)(uintptr_t)timeout_seconds,0,NULL));
    if (getenv("BB_WATCH_ADDR") || getenv("BB_WATCH_IMAGE")) {
        watch_address=getenv("BB_WATCH_ADDR") ? strtoull(getenv("BB_WATCH_ADDR"),NULL,0) : 0;
        CloseHandle(CreateThread(NULL,0,watch_all_threads,NULL,0,NULL));
    }
    /* The launcher passes 0 when its Debug switch is off. */
    if (getenv("LBPPC_PROFILE") && strcmp(getenv("LBPPC_PROFILE"),"0")) CloseHandle(CreateThread(NULL,0,profile_threads,NULL,0,NULL));
    if (getenv("LBPPC_FPTRAP"))
        CloseHandle(CreateThread(NULL,0,fptrap_threads,(LPVOID)(uintptr_t)strtoul(getenv("LBPPC_FPTRAP"),NULL,0),0,NULL));
#else
    page_size = (size_t)sysconf(_SC_PAGESIZE);
    struct sigaction sa = {0}; sa.sa_sigaction = fault; sa.sa_flags = SA_SIGINFO;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGSEGV, &sa, NULL); sigaction(SIGILL, &sa, NULL); sigaction(SIGBUS, &sa, NULL);
    Dl_info self_info;
    if (dladdr((void *)main,&self_info)) exe_base=(uintptr_t)self_info.dli_fbase;
    struct sigaction dump = {0}; dump.sa_sigaction = thread_dump; dump.sa_flags = SA_SIGINFO|SA_RESTART;
    sigemptyset(&dump.sa_mask); sigaction(SIGUSR2, &dump, NULL);
    struct sigaction alarm_action = {0}; alarm_action.sa_sigaction = watchdog; alarm_action.sa_flags = SA_SIGINFO;
    sigemptyset(&alarm_action.sa_mask); sigaction(SIGALRM, &alarm_action, NULL);
    alarm(timeout_seconds); /* 0 disables the watchdog */
#endif
    FILE *f = fopen(argv[1], "rb");
    if (!f) fail("cannot open boot file; run prepare.py first");
    char magic[8];
    if (fread(magic, 1, 8, f) != 8 || (memcmp(magic, "BBPROBE1", 8) && memcmp(magic, "BBPROBE2", 8) && memcmp(magic,"BBPROBE3",8) && memcmp(magic,"BBPROBE4",8) && memcmp(magic,"BBPROBE5",8))) fail("bad boot file signature");
    uint64_t size = read64(f), entry = read64(f), ns = read64(f), nr = read64(f);
#ifdef _WIN32
    image_size_hint=size;
#endif
    import_count = read64(f);
    uint64_t capabilities = memcmp(magic, "BBPROBE1", 8) ? read64(f) : 0;
    if (capabilities & ~UINT64_C(1)) fail("unknown runtime capabilities");
    runtime_start(strict_imports ? 0 : capabilities);
    if (!size || size > 512*1024*1024 || entry >= size || !ns || ns > 64 || nr > 1000000 || import_count > 100000)
        fail("boot file limits exceeded");
    int multi=!memcmp(magic,"BBPROBE5",8);
    int linked=!memcmp(magic,"BBPROBE3",8) || !memcmp(magic,"BBPROBE4",8) || multi;
    int native_libc=linked && !strict_imports && (capabilities&1);
    uint64_t main_tls[4]={0};
    uint64_t nb=0,procparam=0;
    uint64_t *bindings=calloc(import_count ? import_count : 1,sizeof(*bindings));
    uint64_t *binding_kinds=calloc(import_count ? import_count : 1,sizeof(*binding_kinds));
    if (!bindings || !binding_kinds) fail("allocation failed");
    if (multi) {
        /* BBPROBE5: procparam, eboot TLS, module table, bindings (link_modules.py). */
        procparam=read64(f);
        for (int i=0;i<4;++i) main_tls[i]=read64(f);
        module_count=read64(f);
        if (!module_count || module_count>sizeof(modules)/sizeof(*modules)) fail("invalid module count");
        for (uint64_t m=0;m<module_count;++m) {
            LinkedModule *x=&modules[m];
            x->base=read64(f); x->size=read64(f); x->init=read64(f); x->tls_address=read64(f);
            x->tls_memsz=read64(f); x->tls_filesz=read64(f); x->tls_module=read64(f);
        }
        nb=read64(f);
    } else if (linked) {
        LinkedModule *x=&modules[0];
        module_count=1;
        x->base=read64(f); x->size=read64(f); x->init=read64(f);
        x->tls_address=read64(f); x->tls_memsz=read64(f); x->tls_filesz=read64(f); x->tls_module=2; nb=read64(f);
        procparam=read64(f);
        if (!memcmp(magic,"BBPROBE4",8)) for (int i=0;i<4;++i) main_tls[i]=read64(f);
    }
    if (linked) {
        if (main_tls[1]>main_tls[2] || main_tls[2]>1024*1024 || main_tls[0]>size ||
            main_tls[1]>size-main_tls[0] || (main_tls[3] & (main_tls[3]-1)) || main_tls[3]>4096)
            fail("invalid eboot TLS metadata");
        for (uint64_t m=0;m<module_count;++m) {
            LinkedModule *x=&modules[m];
            if (x->base>=size || !x->size || x->size>size-x->base || (x->init && (x->init<x->base || x->init-x->base>=x->size)) ||
                (x->tls_module && (x->tls_address>=size || x->tls_memsz>size-x->tls_address || x->tls_memsz>1024*1024 ||
                                   x->tls_filesz>x->tls_memsz || x->tls_module<2 || x->tls_module>7)))
                fail("invalid linked module metadata");
        }
        if (nb>import_count) fail("invalid binding count");
        for (uint64_t i=0;i<nb;++i) {
            uint64_t index=read64(f),address=read64(f),kind=read64(f);
            int inside=0;
            for (uint64_t m=0;m<module_count;++m)
                if (address>=modules[m].base && address-modules[m].base<modules[m].size) inside=1;
            if (index>=import_count || !inside || (kind!=1 && kind!=2) || bindings[index]) fail("invalid native binding");
            bindings[index]=address; binding_kinds[index]=kind;
        }
    }
    Segment *segments = calloc(ns, sizeof(*segments));
    Reloc *relocs = calloc(nr ? nr : 1, sizeof(*relocs));
    names = calloc(import_count ? import_count : 1, sizeof(*names));
    if (!segments || !relocs || !names) fail("allocation failed");
    for (uint64_t i = 0; i < ns; ++i) {
        segments[i].address = read64(f);
        segments[i].size = read64(f);
        segments[i].flags = read64(f);
        if (segments[i].address > size || segments[i].size > size - segments[i].address ||
            segments[i].address % page_size || segments[i].flags > 7) fail("bad segment");
    }
    if (fread(names, 128, import_count, f) != import_count) fail("truncated import names");
    for (uint64_t i = 0; i < import_count; ++i)
        if (!memchr(names[i], 0, 128)) fail("unterminated import name");
    for (uint64_t i = 0; i < nr; ++i) {
        relocs[i].target = read64(f);
        relocs[i].kind = read64(f);
        relocs[i].value = read64(f);
        relocs[i].addend = read64(f);
        if (!mapped(segments, ns, relocs[i].target, 8) || relocs[i].kind > 2 ||
            (relocs[i].kind && relocs[i].value >= import_count) ||
            (relocs[i].kind != 2 && relocs[i].addend) || relocs[i].addend >= page_size) fail("bad relocation");
    }
    for (uint64_t m=0;m<module_count;++m)
        if (!mapped(segments,ns,modules[m].init,1) || (modules[m].tls_module && !mapped(segments,ns,modules[m].tls_address,modules[m].tls_memsz)))
            fail("unmapped module metadata");
    if (module_count && !mapped(segments,ns,procparam,64)) fail("unmapped procparam");
    for (uint64_t i=0;i<import_count;++i) {
        if (!bindings[i]) continue;
        if (!mapped(segments,ns,bindings[i],1)) fail("unmapped native export");
        if (binding_kinds[i]==1) {
            int executable=0;
            for (uint64_t s=0;s<ns;++s)
                if ((segments[s].flags&1) && bindings[i]>=segments[s].address &&
                    bindings[i]-segments[s].address<segments[s].size) executable=1;
            if (!executable) fail("native function is not executable");
        }
    }
    char title[128]="LBPPC", serial[16]="UNKNOWN", sfo[4096];
    uint32_t attributes=0;
    snprintf(sfo,sizeof(sfo),"%s/sce_sys/param.sfo",app0 ? app0 : ".");
    runtime_sfo_value(sfo,"TITLE",title,sizeof(title),NULL);
    runtime_sfo_value(sfo,"TITLE_ID",serial,sizeof(serial),NULL);
    runtime_sfo_value(sfo,"ATTRIBUTE",NULL,0,&attributes);
    image = allocate(round_page(size));
    if (fread(image, 1, size, f) != size || fgetc(f) != EOF) fail("incorrect memory image size");
    fclose(f);
    if (!cpu_only) {
        uint64_t sdk=0;
        if (procparam) memcpy(&sdk,image+procparam+16,8); /* procparam: size, magic, count, sdk_version */
        BbGpuConfig gpu={title,serial,user_dir ? user_dir : "user",(uint32_t)sdk,attributes,1920,1080};
        /* lbppc: a window opens at LBPPC_RENDER_RES (its aspect: ultrawide is not letterboxed into 16:9); the
           GPU layer fits it to the screen. */
        unsigned render_w=0, render_h=0;
        if (getenv("LBPPC_RENDER_RES") && sscanf(getenv("LBPPC_RENDER_RES"),"%ux%u",&render_w,&render_h)==2 &&
            render_w>=640 && render_h>=360 && render_w<=4480 && render_h<=4320) {
            gpu.width=render_w; gpu.height=render_h;
        }
        gpu_enabled=1; /* page tracking starts while the rasterizer registers guest memory */
        if (bbgpu_init(&gpu)) fail("GPU initialization failed");
        printf("GPU: window and Vulkan presenter ready; SDK 0x%08x, %u HLE symbols\n",(unsigned)sdk,bbgpu_symbol_count());
    }
    unsigned char *traps = allocate(round_page((import_count + 1) * 32));
    unsigned char *data_traps = allocate((import_count + 1) * page_size);
    protect(data_traps, (import_count + 1) * page_size, 0);
    for (uint64_t i = 0; i < import_count; ++i) {
        unsigned char *t = traps + i * 32;
        /* SysV: mov edi, index; movabs rax, handler; jmp rax. No fake return values. */
        uint32_t index = (uint32_t)i;
        uintptr_t handler = (uintptr_t)unresolved;
        t[0] = 0x48; t[1] = 0x89; t[2] = 0xfe; /* mov rsi,rdi: preserve arg0 */
        t[3] = 0xbf; memcpy(t + 4, &index, 4);
        t[8] = 0x48; t[9] = 0xb8; memcpy(t + 10, &handler, 8);
        t[18] = 0xff; t[19] = 0xe0;
    }
    for (uint64_t i = 0; i < nr; ++i) {
        uintptr_t value = relocs[i].kind == 2 ? (uintptr_t)(data_traps + page_size * relocs[i].value + relocs[i].addend)
                        : relocs[i].kind == 1 ? (uintptr_t)(traps + 32 * relocs[i].value)
                        : (uintptr_t)image + relocs[i].value;
        if (relocs[i].kind) {
            uintptr_t resolved = runtime_resolve(names[relocs[i].value], relocs[i].kind == 2);
            if (resolved && relocs[i].kind == 1) resolved = trace_wrap(resolved, runtime_import_name(names[relocs[i].value]));
            if (resolved) value = resolved + relocs[i].addend;
            else if (native_libc && bindings[relocs[i].value]) {
                uint64_t address=bindings[relocs[i].value];
                if (binding_kinds[relocs[i].value]!=relocs[i].kind || !mapped(segments,ns,address,relocs[i].addend+1)) fail("native export kind/range mismatch");
                value=(uintptr_t)image+address+relocs[i].addend;
            }
        }
        memcpy(image + relocs[i].target, &value, 8);
    }
    /* BB_LIST_UNRESOLVED=1: every import still bound to a trap (it would stop the game when called), then exit. */
    if (getenv("BB_LIST_UNRESOLVED")) {
        unsigned char *listed=calloc(import_count ? import_count : 1,1);
        unsigned count=0;
        for (uint64_t i = 0; listed && i < nr; ++i) {
            uint64_t index=relocs[i].value;
            if (!relocs[i].kind || listed[index] || runtime_resolve(names[index], relocs[i].kind == 2)) continue;
            if (native_libc && bindings[index]) continue;
            listed[index]=1; ++count;
            printf("UNRESOLVED %s %s\n", names[index], runtime_import_name(names[index]));
        }
        printf("UNRESOLVED total %u\n", count);
        fflush(stdout);
        _Exit(0);
    }
    if (patch_file) apply_patches(patch_file, segments, ns, relocs, nr);
    /* LBPPC_POKE=off:hex[,off:hex...] (test): byte writes at image offsets, after the patches. */
    apply_pokes(getenv("LBPPC_POKE"),segments,ns,0);
    /* LBPPC_POKE_LATE=spec with LBPPC_POKE_AT=seconds (test): the same, that long after the start (in a level),
       and undone LBPPC_POKE_FOR seconds later if given. */
    if (getenv("LBPPC_POKE_LATE")) {
        late_pokes.segments=segments; late_pokes.count=ns;
        CloseHandle(CreateThread(NULL,0,late_poke_thread,NULL,0,NULL));
    }
    /* LBPPC_PROCESS_AFFINITY=mask (test): every thread of the process on those host cores. */
    if (getenv("LBPPC_PROCESS_AFFINITY")) SetProcessAffinityMask(GetCurrentProcess(),(DWORD_PTR)strtoull(getenv("LBPPC_PROCESS_AFFINITY"),NULL,0));
    { extern uintptr_t lbppc_image_base; lbppc_image_base=(uintptr_t)image; }
    render_res_install();
    custom_server_install();
    quit_menu_install();
    friend_log_install();
    net_log_install();
    sim_divide_install();
    hooks_install(getenv("BB_HOOK"));
    sync_log_install();
#ifdef _WIN32
    printf("Guest TCB loads redirected to the TEB TLS slot: %" PRIu64 "\n", patch_tcb_loads(segments, ns));
#endif
    exact_math_install();
    stub_memory_seal();
    protect(traps, round_page((import_count + 1) * 32), 5);
    /* Each page goes straight to its final protection (a later segment wins, as before), never
       through no access: another thread that already found the image (a mod DLL's memory
       watcher) faulted when it read it between the two steps. */
    size_t page_count = round_page(size) / page_size;
    unsigned char *page_flags = calloc(page_count ? page_count : 1, 1);
    if (!page_flags) fail("out of memory");
    int executable_entry = 0;
    for (uint64_t i = 0; i < ns; ++i) {
        size_t first = segments[i].address / page_size;
        size_t end = (segments[i].address + round_page(segments[i].size) + page_size - 1) / page_size;
        for (size_t p = first; p < end && p < page_count; ++p) page_flags[p] = (unsigned char)segments[i].flags;
        if ((segments[i].flags & 1) && entry >= segments[i].address && entry - segments[i].address < segments[i].size)
            executable_entry = 1;
    }
    for (size_t p = 0; p < page_count;) {
        size_t run = p;
        while (run < page_count && page_flags[run] == page_flags[p]) ++run;
        protect(image + p * page_size, (run - p) * page_size, page_flags[p]);
        p = run;
    }
    free(page_flags);
    if (!executable_entry) fail("entry is not executable");
    printf("Mapped %" PRIu64 " bytes, %" PRIu64 " segments; applied %" PRIu64 " relocations\n", size, ns, nr);
    if (native_libc) {
        for (uint64_t m=0;m<module_count;++m) {
            int init_executable=!modules[m].init; /* 0: no initializer */
            for (uint64_t i=0;i<ns;++i)
                if ((segments[i].flags&1) && modules[m].init>=segments[i].address && modules[m].init-segments[i].address<segments[i].size) init_executable=1;
            if (!init_executable) fail("module init is not executable");
            if (modules[m].tls_module)
                runtime_set_module_tls(modules[m].tls_module,image+modules[m].tls_address,modules[m].tls_filesz,modules[m].tls_memsz);
        }
        runtime_set_main_tls(image+main_tls[0],main_tls[1],main_tls[2],main_tls[3]);
        runtime_thread_attach_main();
        runtime_set_procparam(image+procparam);
        /* Dependencies start in link order (libc first), as the PS4 dynamic linker does. */
        for (uint64_t m=0;m<module_count;++m) {
            if (!modules[m].init) continue;
            printf("Starting linked module %" PRIu64 " at image offset 0x%" PRIx64 "; native bindings=%" PRIu64 "\n",m,modules[m].init,nb);
            typedef int (ABI *ModuleInit)(uint64_t,void *,void *);
            int result=((ModuleInit)(image+modules[m].init))(0,NULL,NULL);
            printf("Module %" PRIu64 " initializer returned %d\n",m,result);
            if (result) fail("module initializer failed");
        }
    }
    /* The online session starts with the game (lbppc: LBP3's when signed in locally, LBPPC_NP_USER). */
    if ((getenv("LBPPC_NP_USER") && *getenv("LBPPC_NP_USER")) ||
        (getenv("BB_SHADNET_NPID") && *getenv("BB_SHADNET_NPID"))) bbnet_initialize();
    printf("Entering original x86-64 code at guest offset 0x%" PRIx64 "\n", entry);
    entered_game=1;
    struct { uint64_t argc; const char *argv[2]; } params = {1, {"/app0/eboot.bin", NULL}};
#ifdef _WIN32
    typedef void (ABI *Entry)(void *, void (ABI *)(void));
    ((Entry)(image + entry))(&params, guest_exit);
#else
    /* The guest main thread runs on a stack below 1 TiB like PS4 stacks. */
    enum { MAIN_STACK=8*1024*1024 };
    unsigned char *stack=runtime_low_map(MAIN_STACK,PROT_READ|PROT_WRITE);
    if (!stack) fail("cannot allocate guest main stack");
    enter_on_stack(image+entry,&params,(void *)guest_exit,stack+MAIN_STACK-64);
#endif
    fail("entry unexpectedly returned");
}
