/* System functions Bloodborne never imported, for other games (LittleBigPlanet 3 first):
 *   - no PlayStation Camera, no Move controllers, no keyboard attached;
 *   - no live streaming, companion app, core dumps or backup saves;
 *   - dialogs that only report finish at once (nothing is drawn yet);
 *   - kernel event flags, sleep and POSIX semaphores, implemented on the host.
 * Every entry here is an explicit contract; unknown functions still stop. */
#define _GNU_SOURCE
#include "runtime.h"
#include "gpu/bbgpu.h"
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
typedef SOCKET HostSocket;
#define socket_error(s) ((s)==INVALID_SOCKET)
#define last_socket_error() WSAGetLastError()
#define WOULD_BLOCK WSAEWOULDBLOCK
#else
#include <unistd.h>
#include <fcntl.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
typedef int HostSocket;
#define socket_error(s) ((s)<0)
#define closesocket close
#define last_socket_error() errno
#define WOULD_BLOCK EWOULDBLOCK
#endif

#define ORBIS_OK 0
#define KERNEL_EINVAL ((int32_t)0x80020016)
#define KERNEL_ENOMEM ((int32_t)0x8002000C)
#define KERNEL_ETIMEDOUT ((int32_t)0x8002003C)
#define KERNEL_EBUSY ((int32_t)0x80020010)
#define SYSTEM_PARAMETER ((int32_t)0x80A10003)
#define CAMERA_PARAM ((int32_t)0x802E0001)
#define CAMERA_NOT_INIT ((int32_t)0x802E0002)
#define CAMERA_NOT_CONNECTED ((int32_t)0x802E0010)
#define MOVE_NO_CONTROLLER 1
#define MOVE_INVALID_ARG ((int32_t)0x80EE0003)
#define SAVE_DATA_NOT_FOUND ((int32_t)0x809F0008)
#define APP_CONTENT_NOT_FOUND ((int32_t)0x80D90005)
#define APP_CONTENT_PARAMETER ((int32_t)0x80D90002)
#define RTC_INVALID_POINTER ((int32_t)0x80B50002)
#define RTC_INVALID_VALUE ((int32_t)0x80B50003)
#define IME_NOT_CONNECTED ((int32_t)0x80BC0004)
#define DIALOG_FINISHED 3

static void note_once(int *seen, const char *what) {
    if (!*seen) { *seen=1; printf("Runtime: %s\n",what); }
}

/* System service: the whole screen is safe (no TV overscan). */
typedef struct { float ratio; uint8_t reserved[128]; } SafeArea;
static ABI int32_t safe_area(SafeArea *info) {
    if (!info) return SYSTEM_PARAMETER;
    memset(info,0,sizeof(*info));
    info->ratio=1.0f;
    return ORBIS_OK;
}
static ABI int32_t pad_light_bar(int32_t handle,const void *color) { (void)handle; (void)color; return ORBIS_OK; }
static ABI int32_t voice_port_attr(uint32_t port,int32_t attr,void *value,uint32_t size) {
    (void)port; (void)attr;
    if (value && size) memset(value,0,size);
    return ORBIS_OK;
}

/* Rtc: dates as SceRtcDateTime (year, month, day, hour, minute, second, microsecond), ticks in microseconds. */
typedef struct { uint16_t year, month, day, hour, minute, second; uint32_t microsecond; } RtcDate;
static ABI int32_t rtc_check_valid(const RtcDate *d) {
    static const uint8_t days[]={31,28,31,30,31,30,31,31,30,31,30,31};
    if (!d) return RTC_INVALID_POINTER;
    if (d->year<1 || d->year>9999 || d->month<1 || d->month>12) return RTC_INVALID_VALUE;
    int leap=(d->year%4==0 && d->year%100) || d->year%400==0;
    if (d->day<1 || d->day>days[d->month-1]+(d->month==2 && leap)) return RTC_INVALID_VALUE;
    if (d->hour>23 || d->minute>59 || d->second>59 || d->microsecond>999999) return RTC_INVALID_VALUE;
    return ORBIS_OK;
}
static ABI int32_t rtc_add_minutes(uint64_t *out,const uint64_t *in,int64_t minutes) {
    if (!out || !in) return RTC_INVALID_POINTER;
    *out=*in+(uint64_t)(minutes*60000000LL);
    return ORBIS_OK;
}

/* PlayStation Camera: never attached. */
static int camera_seen;
static ABI int32_t camera_attached(int32_t index) {
    note_once(&camera_seen,"Camera: none attached");
    return index ? CAMERA_PARAM : 0;
}
static ABI int32_t camera_open(int32_t user,int32_t type,int32_t index,void *param) {
    (void)user; (void)type; (void)index; (void)param;
    return CAMERA_NOT_CONNECTED;
}
static ABI int32_t camera_not_open(void) { return CAMERA_NOT_INIT; }

/* Move: the library works, no controller is ever connected. */
static ABI int32_t move_init(void) { return ORBIS_OK; }
static ABI int32_t move_open(int32_t user,int32_t type,int32_t index) {
    (void)user; (void)type;
    if (index<0 || index>1) return MOVE_INVALID_ARG;
    return 0x30b0000+index;
}
static ABI int32_t move_close(int32_t handle) { (void)handle; return ORBIS_OK; }
static ABI int32_t move_device_info(int32_t handle,void *info) {
    (void)handle;
    if (info) memset(info,0,16);
    return MOVE_NO_CONTROLLER;
}
static ABI int32_t move_read_recent(int32_t handle,int64_t timestamp,void *states,int32_t *count) {
    (void)handle; (void)timestamp; (void)states;
    if (count) *count=0;
    return MOVE_NO_CONTROLLER;
}

/* Move tracker (positions from the camera image): nothing to track without a camera or controller. */
static ABI int32_t tracker_memory(uint32_t *size) { if (size) *size=0x10000; return ORBIS_OK; }
#define MOVE_INVALID_HANDLE ((int32_t)0x80EE0004)
static ABI int32_t tracker_no_state(void) { return MOVE_INVALID_HANDLE; }

/* Live streaming: never broadcasting, no viewers' messages. */
static ABI int32_t ok_void(void) { return ORBIS_OK; }
/* Game custom data dialog (param: the common dialog header, 0x30 bytes, then size and mode). LBP3 opens mode 1
 * to send its data to friends and mode 2 to look at what it received: its Triangle prompt for an invitation.
 * Mode 2 with an invitation on screen joins it (bbport_invite.h), as the system dialog's choice would. Either
 * way the dialog finishes at once with nothing chosen. */
enum { CUSTOM_DATA_SEND=1, CUSTOM_DATA_RECEIVED=2 };
static unsigned custom_dialogs;
static ABI int32_t custom_dialog_open(const uint8_t *param) {
    int32_t mode=0;
    if (param) memcpy(&mode,param+0x34,4);
    int joined=mode==CUSTOM_DATA_RECEIVED && bbgpu_invite_accept_shown();
    if (++custom_dialogs<=20)
        printf("Runtime: game custom data dialog opened (mode %d%s)\n",mode,
               joined ? ": joining the invitation on screen" : "");
    return ORBIS_OK;
}
static ABI int32_t ok_zero_out(void *out) { if (out) memset(out,0,64); return ORBIS_OK; }
static ABI int32_t stream_messages(int32_t type,void *messages,uint32_t max,uint32_t *count) {
    (void)type; (void)messages; (void)max;
    if (count) *count=0;
    return ORBIS_OK;
}
static ABI int32_t stream_message_count(int32_t type,uint32_t *count) { (void)type; if (count) *count=0; return ORBIS_OK; }

/* Common dialogs this runtime does not draw yet: opening succeeds and they finish at once. */
static ABI int32_t dialog_finished(void) { return DIALOG_FINISHED; }
/* The error dialog: the system's message for an error code (param: size, error code, user). Logged, since a
 * game shows it when something it needs failed. */
static unsigned error_dialogs;
static ABI int32_t error_dialog_open(const int32_t *param) {
    if (++error_dialogs<=20) printf("Runtime: error dialog for 0x%08x\n",param ? (uint32_t)param[1] : 0);
    return ORBIS_OK;
}
static ABI int32_t dialog_ready(void) { return 1; }
typedef struct { int32_t mode, result, button; uint8_t rest[60]; } SaveDialogResult;
static ABI int32_t save_dialog_result(SaveDialogResult *result) {
    if (result) { result->result=0; result->button=0; }
    return ORBIS_OK;
}

/* Save data: there is never a backup copy. Unmounting with a backup unmounts. */
static ABI int32_t save_no_backup(const void *param) { (void)param; return SAVE_DATA_NOT_FOUND; }
static ABI int32_t save_umount_with_backup(const void *mount) {
    typedef int32_t (ABI *Umount)(const void *);
    Umount umount=(Umount)runtime_savedata_resolve("BMR4F-Uek3E#O#P"); /* sceSaveDataUmount */
    return umount ? umount(mount) : ORBIS_OK;
}

/* PlayGo: everything is installed, at full speed. */
static ABI int32_t playgo_todo(int32_t handle,void *list,uint32_t max,uint32_t *count) {
    (void)handle; (void)list; (void)max;
    if (count) *count=0;
    return ORBIS_OK;
}
static ABI int32_t playgo_progress(int32_t handle,const uint16_t *chunks,uint32_t count,uint64_t *progress) {
    (void)handle; (void)chunks; (void)count;
    if (progress) { progress[0]=1; progress[1]=1; } /* progressSize, totalSize */
    return ORBIS_OK;
}
static ABI int32_t playgo_speed(int32_t handle,int32_t *speed) { (void)handle; if (speed) *speed=2; return ORBIS_OK; }

/* AppContent: the temporary data is a folder of the user directory (/temp0, runtime_file.c). */
typedef struct { char data[16]; } MountPoint;
static ABI int32_t temp_mount(int32_t option,MountPoint *point) {
    (void)option;
    if (!point) return APP_CONTENT_PARAMETER;
    memset(point,0,sizeof(*point));
    strcpy(point->data,"/temp0");
    return ORBIS_OK;
}
static ABI int32_t temp_format(const MountPoint *point) { (void)point; return ORBIS_OK; }
static ABI int32_t temp_space(const MountPoint *point,uint64_t *kb) {
    (void)point;
    if (!kb) return APP_CONTENT_PARAMETER;
    *kb=1024*1024;
    return ORBIS_OK;
}
/* sceAppContentGetEntitlementKey(service label, entitlement label, key): an add-on's 16-byte key. LittleBigPlanet
 * 3 (eboot 0x2463d0) grants a DLC only when this succeeds, and never reads the key itself: an installed DLC (the
 * player's own dump, set up by the launcher; runtime_content.c) gets an all-zero key, any other "not found". The
 * label is a SceNpUnifiedEntitlementLabel (16 characters and a NUL); the service label comes first (an earlier
 * two-argument version read it as the label's address and crashed on service 1). */
static ABI int32_t entitlement_key(uint32_t service,const char *label,void *key) {
    char name[17]="";
    if (label) { memcpy(name,label,16); name[16]=0; }
    const int installed=runtime_content_addon(name,NULL,0);
    static unsigned logged;
    if (logged<64) {
        ++logged;
        printf("Runtime: AppContent entitlement key for %s (service %u): %s\n",name[0] ? name : "(none)",service,
               installed ? "granted (installed)" : "not found (not installed)");
    }
    if (!installed) return APP_CONTENT_NOT_FOUND;
    if (!key) return APP_CONTENT_PARAMETER;
    memset(key,0,16);
    return ORBIS_OK;
}

/* IME: no hardware keyboard. */
static ABI int32_t keyboard_open(int32_t user,const void *param) { (void)user; (void)param; return IME_NOT_CONNECTED; }

/* Kernel event flags: a 64-bit pattern that threads wait on (all or any bits, clearing all or the waited
 * bits when the wait ends). */
enum { WAIT_AND=1, WAIT_OR=2, CLEAR_ALL=0x10, CLEAR_PAT=0x20 };
typedef struct { pthread_mutex_t lock; pthread_cond_t changed; uint64_t pattern; char name[32]; } EventFlag;
static ABI int32_t flag_create(EventFlag **out,const char *name,uint32_t attr,uint64_t pattern,const void *opt) {
    (void)attr; (void)opt;
    if (!out) return KERNEL_EINVAL;
    EventFlag *f=calloc(1,sizeof(*f));
    if (!f) return KERNEL_ENOMEM;
    pthread_mutex_init(&f->lock,NULL);
    pthread_cond_init(&f->changed,NULL);
    f->pattern=pattern;
    if (name) snprintf(f->name,sizeof(f->name),"%s",name);
    *out=f;
    return ORBIS_OK;
}
/* LBPPC_FLAG_LOG (diagnostics): every set and wait, one line each, with the pattern before and after. */
static int flag_log(void) { static int on=-1; if (on<0) on=getenv("LBPPC_FLAG_LOG")!=NULL; return on; }
static void flag_note(const char *what,EventFlag *f,uint64_t bits,uint32_t mode,uint64_t before,uint64_t after,int32_t status) {
    struct timespec now; clock_gettime(CLOCK_MONOTONIC,&now);
    char line[256];
    int n=snprintf(line,sizeof(line),"FLAG [t=%lld.%06ld] %s '%s' %p bits %llx mode %x pattern %llx -> %llx status %x (thread %lu)%s",
                   (long long)now.tv_sec,now.tv_nsec/1000,what,f->name,(void *)f,(unsigned long long)bits,mode,
                   (unsigned long long)before,(unsigned long long)after,(unsigned)status,(unsigned long)GetCurrentThreadId(),"\n");
    fwrite(line,1,(size_t)n,stderr);
}
static ABI int32_t flag_set(EventFlag *f,uint64_t bits) {
    if (!f) { fprintf(stderr,"Runtime: event flag set on a null flag\n"); return KERNEL_EINVAL; }
    pthread_mutex_lock(&f->lock);
    uint64_t before=f->pattern;
    f->pattern|=bits;
    if (flag_log()) flag_note("set",f,bits,0,before,f->pattern,0);
    pthread_cond_broadcast(&f->changed);
    pthread_mutex_unlock(&f->lock);
    return ORBIS_OK;
}
/* lbppc: LBP3's SPU threads block in sceKernelWaitEventFlag on 'SpuEventFlag', each on its own bit. While one is
 * out of its wait it runs jobs; a set bit is work queued. lbppc_spu_barrier (LBPPC_SPU_BARRIER, at each flip)
 * waits until none runs and none has work: a frame's SPU jobs cannot spill into the next. */
static volatile int spu_waiters;
static EventFlag *spu_flag;
static uint64_t spu_bits;
void lbppc_spu_barrier(void) {
    static int on=-1;
    if (on<0) on=getenv("LBPPC_SPU_BARRIER")!=NULL;
    static long calls, waited;
    if (!on) return;
    if (!spu_flag) { if (!calls++) fputs("SPU barrier: no SpuEventFlag wait seen yet\n",stderr); return; }
    for (int spins=0;;++spins) {
        pthread_mutex_lock(&spu_flag->lock);
        const int idle=spu_waiters>=5 && !(spu_flag->pattern&spu_bits);
        const int waiters=spu_waiters; const uint64_t pending=spu_flag->pattern&spu_bits;
        pthread_mutex_unlock(&spu_flag->lock);
        if (spins==1) ++waited;
        if (idle || spins>200000) {
            if (++calls%300==0)
                fprintf(stderr,"SPU barrier: %ld flips, %ld waited; now %d waiters, pending %llx, bits %llx%s\n",calls,waited,
                        waiters,(unsigned long long)pending,(unsigned long long)spu_bits,idle ? "" : " (gave up)");
            return;
        }
        SwitchToThread();
    }
}
static int flag_met(uint64_t pattern,uint64_t want,uint32_t mode) {
    return mode&WAIT_AND ? (pattern&want)==want : (pattern&want)!=0;
}
static ABI int32_t flag_wait(EventFlag *f,uint64_t want,uint32_t mode,uint64_t *result,uint32_t *timeout_us) {
    if (!f || !want || !(mode&(WAIT_AND|WAIT_OR))) {
        fprintf(stderr,"Runtime: event flag wait refused (flag %p, bits 0x%llx, mode 0x%x)\n",(void *)f,(unsigned long long)want,mode);
        return KERNEL_EINVAL;
    }
    struct timespec deadline;
    if (timeout_us) {
        clock_gettime(CLOCK_REALTIME,&deadline);
        uint64_t ns=(uint64_t)deadline.tv_nsec+(uint64_t)*timeout_us*1000ULL;
        deadline.tv_sec+=(time_t)(ns/1000000000ULL);
        deadline.tv_nsec=(long)(ns%1000000000ULL);
    }
    int32_t status=ORBIS_OK;
    pthread_mutex_lock(&f->lock);
    uint64_t entry=f->pattern;
    if (flag_log()) flag_note("wait-begin",f,want,mode,entry,entry,0);
    const int spu=!timeout_us && !strcmp(f->name,"SpuEventFlag");
    if (spu) { spu_flag=f; spu_bits|=want; ++spu_waiters; }
    while (!flag_met(f->pattern,want,mode)) {
        int e=timeout_us ? runtime_cond_timedwait(&f->changed,&f->lock,&deadline) : pthread_cond_wait(&f->changed,&f->lock);
        if (e==ETIMEDOUT) { status=KERNEL_ETIMEDOUT; break; }
    }
    if (spu) --spu_waiters;
    if (result) *result=f->pattern;
    if (status==ORBIS_OK) {
        if (mode&CLEAR_ALL) f->pattern=0;
        else if (mode&CLEAR_PAT) f->pattern&=~want;
    } else if (timeout_us) *timeout_us=0;
    if (flag_log()) flag_note("wait-end",f,want,mode,result ? *result : 0,f->pattern,status);
    pthread_mutex_unlock(&f->lock);
    return status;
}
static ABI int32_t kernel_sleep(uint32_t seconds) {
#ifdef _WIN32
    Sleep(seconds*1000);
#else
    sleep(seconds);
#endif
    return ORBIS_OK;
}

/* POSIX semaphores: the guest's sem_t holds a pointer to a host semaphore made of a count, a lock and a
 * condition. Errors go to the guest errno (FreeBSD numbers) with -1, as POSIX does. */
typedef struct { pthread_mutex_t lock; pthread_cond_t posted; int64_t count; unsigned long poster; } Sem;
static int32_t sem_fail(int host_errno) {
    static int logged;
    if (host_errno!=ETIMEDOUT && logged++<10)
        fprintf(stderr,"Runtime: semaphore call failed (errno %d)\n",host_errno);
    *runtime_errno()=runtime_guest_errno(host_errno); return -1;
}
static ABI int32_t sem_init_guest(Sem **sem,int32_t shared,uint32_t value) {
    (void)shared;
    if (!sem) return sem_fail(EINVAL);
    Sem *s=calloc(1,sizeof(*s));
    if (!s) return sem_fail(ENOMEM);
    pthread_mutex_init(&s->lock,NULL);
    pthread_cond_init(&s->posted,NULL);
    s->count=value;
    *sem=s;
    return 0;
}
static ABI int32_t sem_destroy_guest(Sem **sem) {
    if (!sem || !*sem) return sem_fail(EINVAL);
    pthread_cond_destroy(&(*sem)->posted);
    pthread_mutex_destroy(&(*sem)->lock);
    free(*sem);
    *sem=NULL;
    return 0;
}
static ABI int32_t sem_post_guest(Sem **sem) {
    if (!sem || !*sem) return sem_fail(EINVAL);
    Sem *s=*sem;
    pthread_mutex_lock(&s->lock);
    ++s->count;
#ifdef _WIN32
    s->poster=GetCurrentThreadId();
#endif
    pthread_cond_signal(&s->posted);
    pthread_mutex_unlock(&s->lock);
    return 0;
}
typedef struct { int64_t sec, nsec; } GuestTimespec;
/* LBPPC_SEM_LOG=1 (test): each wait of 5 ms or more, with how it ended, the timeout it was given (from the guest's
 * clock_gettime(CLOCK_REALTIME), compared with the host's) and the thread that last posted. */
static void sem_log_wait(Sem *s,const GuestTimespec *abstime,const struct timespec *start,int timed_out) {
#ifdef _WIN32
    static int log=-1;
    if (log<0) log=getenv("LBPPC_SEM_LOG") && getenv("LBPPC_SEM_LOG")[0]=='1';
    if (!log) return;
    struct timespec now;
    clock_gettime(CLOCK_REALTIME,&now);
    const double waited=(double)(now.tv_sec-start->tv_sec)*1e3+(double)(now.tv_nsec-start->tv_nsec)/1e6;
    if (waited<5.0) return;
    const double timeout=abstime ? (double)(abstime->sec-start->tv_sec)*1e3+(double)(abstime->nsec-start->tv_nsec)/1e6 : -1.0;
    extern const char *runtime_thread_name_of(unsigned long tid);
    const char *me=runtime_thread_name_of(GetCurrentThreadId()), *by=runtime_thread_name_of(s->poster);
    printf("Sem: %s waited %.1f ms (timeout %.1f ms), %s; last post by %s\n",me && *me ? me : "thread",waited,timeout,
           timed_out ? "timed out" : "posted",by && *by ? by : "a host thread");
#else
    (void)s; (void)abstime; (void)start; (void)timed_out;
#endif
}
static int32_t sem_take(Sem **sem,const GuestTimespec *abstime) {
    if (!sem || !*sem) return sem_fail(EINVAL);
    Sem *s=*sem;
    struct timespec deadline, start;
    clock_gettime(CLOCK_REALTIME,&start);
    if (abstime) { deadline.tv_sec=(time_t)abstime->sec; deadline.tv_nsec=(long)abstime->nsec; }
    pthread_mutex_lock(&s->lock);
    while (s->count<=0) {
        int e=abstime ? runtime_cond_timedwait(&s->posted,&s->lock,&deadline) : pthread_cond_wait(&s->posted,&s->lock);
        if (e==ETIMEDOUT) { pthread_mutex_unlock(&s->lock); sem_log_wait(s,abstime,&start,1); return sem_fail(ETIMEDOUT); }
    }
    --s->count;
    pthread_mutex_unlock(&s->lock);
    sem_log_wait(s,abstime,&start,0);
    return 0;
}
static ABI int32_t sem_wait_guest(Sem **sem) { return sem_take(sem,NULL); }
static ABI int32_t sem_timedwait_guest(Sem **sem,const GuestTimespec *abstime) {
    return abstime ? sem_take(sem,abstime) : sem_fail(EINVAL);
}
static ABI int32_t thread_set_param(void *thread,int32_t policy,const void *param) {
    (void)thread; (void)policy; (void)param;
    return 0; /* the host scheduler decides */
}

/* Trophies Bloodborne never used: nothing is unlocked yet. */
static ABI int32_t trophy_unlock_state(int32_t ctx,int32_t handle,uint32_t *flags,uint32_t *count) {
    (void)ctx; (void)handle;
    if (flags) memset(flags,0,16); /* SceNpTrophyFlagArray: 128 bits */
    if (count) *count=0;
    return ORBIS_OK;
}
/* Store icon, commerce dialog and in-game messages: offline, nothing to show or send. */
static ABI int32_t zero_result(void *result) { if (result) memset(result,0,32); return ORBIS_OK; }
static ABI int32_t np_new_id(void) { static int32_t next=1; return next++; }
#define NP_SIGNED_OUT ((int32_t)0x80550006)
/* lbppc: signed in (LBPPC_NP_USER, or shadNet: BB_SHADNET_NPID), in-game messages (between players in a session) are accepted and go
   nowhere: there is no PSN to carry them; LBP3 gives up on its messaging otherwise and waits at "Connecting". */
static ABI int32_t np_message_local(void) {
    const char *user=getenv("LBPPC_NP_USER"), *shadnet=getenv("BB_SHADNET_NPID");
    return (user && *user) || (shadnet && *shadnet) ? ORBIS_OK : NP_SIGNED_OUT;
}
static ABI int32_t coredump_write(const void *data,uint64_t size) { (void)data; return (int32_t)size; }
/* Content search (other installed content): nothing besides this game. */
static ABI int32_t search_index(int64_t *index) { if (index) *index=0; return ORBIS_OK; }
static ABI int32_t search_update_id(uint64_t *id) { if (id) *id=0; return ORBIS_OK; }
#define CONTENT_SEARCH_NOT_FOUND ((int32_t)0x80F30001)
static ABI int32_t search_nothing(void) { return CONTENT_SEARCH_NOT_FOUND; }

/* JPEG: handles work; decoding and encoding fail until a codec is added, as for a damaged image. */
#define JPEG_DEC_FAILED ((int32_t)0x80650008)
#define JPEG_ENC_FAILED ((int32_t)0x80650108)
static ABI int32_t jpeg_memory_size(const void *param) { (void)param; return 0x10000; }
typedef struct { uint64_t magic; } JpegHandle;
static ABI int32_t jpeg_create(const void *param,void *memory,uint32_t size,void **handle) {
    (void)param; (void)size;
    if (!memory || !handle) return (int32_t)0x80650002;
    *handle=memory;
    return ORBIS_OK;
}
static ABI int32_t jpeg_delete(void *handle) { (void)handle; return ORBIS_OK; }
static int jpeg_seen;
static ABI int32_t jpeg_decode_failed(void) { note_once(&jpeg_seen,"JPEG: decoding not implemented yet"); return JPEG_DEC_FAILED; }
static ABI int32_t jpeg_encode_failed(void) { note_once(&jpeg_seen,"JPEG: encoding not implemented yet"); return JPEG_ENC_FAILED; }

/* Sockets: real host sockets on this PC only. A game may send to itself on 127.0.0.1 to wake its own threads
 * (LBP3 does, on port 3658: with sockets that dropped every packet it stayed on "Connecting to LittleBigPlanet
 * 3 online" for ever). Binding is to 127.0.0.1 whatever the game asks, so nothing listens on the network,
 * and sending anywhere else fails as unreachable: offline, as with the cable unplugged. Sockets never block
 * (games set that anyway); sceNetEpollWait waits for them with select. */
/* Small numbers, as on PS4: games index fd_set bit arrays (1024 bits) with them. LBP3's set a bit 2 KiB past its
   set with 0x4000, on another function's stack canary. Files take 3-959 (runtime_file.c). */
enum { SOCKET_BASE=960, MAX_SOCKETS=64 };
static struct { HostSocket host; int used, type; } sockets[MAX_SOCKETS];
static pthread_mutex_t socket_lock=PTHREAD_MUTEX_INITIALIZER;
static int socket_slot(int32_t fd) {
    int i=fd-SOCKET_BASE;
    return i>=0 && i<MAX_SOCKETS && sockets[i].used ? i : -1;
}
static int32_t socket_fail(int host_errno) { *runtime_errno()=runtime_guest_errno(host_errno); return -1; }
static int32_t socket_host_fail(void) {
    int e=last_socket_error();
    return socket_fail(e==WOULD_BLOCK || e==EAGAIN ? EAGAIN : ECONNREFUSED);
}
/* PS4 (BSD) sockaddr_in: length, family, port (network order), address, zero. */
typedef struct { uint8_t len, family; uint16_t port; uint32_t addr; uint8_t zero[8]; } GuestAddr;
static int loopback(uint32_t addr) { return (ntohl(addr)>>24)==127; }
/* Online (BB_ONLINE=1) the game's own sockets are the network library's, where sceNetSetsockopt and sceNetEpoll*
 * look for them: here its P2P socket could not leave this PC, so joined players never exchanged a game message
 * and the joiner gave up ten seconds after connecting. Only the raw ping socket stays below (it needs
 * administrator rights there); its ids are SOCKET_BASE and up. */
uintptr_t bbgpu_resolve(const char *name);
uint16_t bbnet_p2p_bound_port(void);
enum { P2P_DEFAULT_PORT=3658, MAX_NET_LOGS=300, MAX_LOCAL_NET_LOGS=6 };
static int socket_online(void) {
    static int online=-1;
    if (online<0) { const char *v=getenv("BB_ONLINE"); online=v && v[0]=='1'; }
    return online;
}
static int net_socket(int32_t fd) { return socket_online() && fd<SOCKET_BASE; }
/* sceNet calls answer 0x804101xx; these POSIX names answer -1 with the code in errno. */
static int64_t net_result(int64_t r) {
    if (r>=0 || ((uint32_t)r&0xffffff00)!=0x80410100) return r;
    *runtime_errno()=(int32_t)(r&0xff);
    return -1;
}
static int net_log_turn(int64_t r,const GuestAddr *addr) {
    static int local, remote;
    if (r<0 && *runtime_errno()==35) return 0; /* EAGAIN: nothing waiting, every poll */
    if (addr && loopback(addr->addr)) return __atomic_add_fetch(&local,1,__ATOMIC_RELAXED)<=MAX_LOCAL_NET_LOGS;
    return __atomic_add_fetch(&remote,1,__ATOMIC_RELAXED)<=MAX_NET_LOGS;
}
static void net_log_call(const char *what,int32_t fd,const GuestAddr *addr,uint64_t size,int64_t r) {
    if (!net_log_turn(r,addr)) return;
    uint32_t ip=addr ? ntohl(addr->addr) : 0;
    printf("Runtime: net %s socket %d, %llu bytes, %u.%u.%u.%u port %u vport %u -> %lld (errno %d)\n",what,fd,(unsigned long long)size,
           ip>>24,(ip>>16)&255,(ip>>8)&255,ip&255,addr ? ntohs(addr->port) : 0,addr ? ntohs(*(const uint16_t *)addr->zero) : 0,
           (long long)r,r<0 ? *runtime_errno() : 0);
}
#define NET_FUNCTION(type,variable,nid) static type variable; if (!variable) variable=(type)bbgpu_resolve(nid)
static ABI int32_t socket_open(int32_t family,int32_t type,int32_t protocol) {
    if (socket_online() && type!=3) {
        typedef ABI int32_t (*Create)(const char *,int32_t,int32_t,int32_t);
        NET_FUNCTION(Create,create,"Q4qBuN-c0ZM");
        int32_t r=(int32_t)net_result(create("game",family,type,protocol));
        printf("Runtime: game socket (family %d, type %d, protocol %d) -> %d, online\n",family,type,protocol,r);
        return r;
    }
#ifdef _WIN32
    static int started;
    if (!started) { WSADATA data; WSAStartup(MAKEWORD(2,2),&data); started=1; }
#endif
    if (family!=2) return socket_fail(EAFNOSUPPORT);
    int stream=type==1 || type==10; /* SOCK_STREAM, PS4 SOCK_STREAM_P2P */
    /* SOCK_RAW (3, LBP3's ICMP pings for latency): raw sockets need administrator rights on Windows; an ordinary
       datagram socket stands in, its pings unanswered (the latency stays unknown) instead of the socket failing. */
    if (!stream && type!=2 && type!=6 && type!=3) return socket_fail(EPROTONOSUPPORT); /* SOCK_DGRAM(_P2P) */
    HostSocket s=socket(AF_INET,stream ? SOCK_STREAM : SOCK_DGRAM,0);
    if (socket_error(s)) return socket_fail(EMFILE);
#ifdef _WIN32
    u_long on=1;
    ioctlsocket(s,FIONBIO,&on);
#else
    fcntl(s,F_SETFL,fcntl(s,F_GETFL)|O_NONBLOCK);
#endif
    pthread_mutex_lock(&socket_lock);
    int i=0;
    while (i<MAX_SOCKETS && sockets[i].used) ++i;
    if (i<MAX_SOCKETS) { sockets[i].host=s; sockets[i].used=1; sockets[i].type=type; }
    pthread_mutex_unlock(&socket_lock);
    if (i==MAX_SOCKETS) { closesocket(s); return socket_fail(EMFILE); }
    return SOCKET_BASE+i;
}
static ABI int32_t socket_bind(int32_t fd,const GuestAddr *addr,uint32_t size) {
    if (net_socket(fd)) {
        typedef ABI int32_t (*Bind)(int32_t,const GuestAddr *,uint32_t);
        NET_FUNCTION(Bind,net_bind,"bErx49PgxyY");
        int32_t r=(int32_t)net_result(net_bind(fd,addr,size));
        net_log_call("bind",fd,addr,0,r);
        return r;
    }
    int i=socket_slot(fd);
    if (i<0) return socket_fail(EBADF);
    if (!addr || size<8) return socket_fail(EINVAL);
    struct sockaddr_in host={0};
    host.sin_family=AF_INET;
    host.sin_port=addr->port;
    host.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    if (bind(sockets[i].host,(struct sockaddr *)&host,sizeof(host))) return socket_fail(EADDRINUSE);
    return 0;
}
static ABI int64_t socket_send(int32_t fd,const void *data,uint64_t size,int32_t flags,const GuestAddr *to,uint32_t to_size) {
    if (net_socket(fd)) {
        typedef ABI int32_t (*Send)(int32_t,const void *,uint64_t,int32_t,const GuestAddr *,uint32_t);
        NET_FUNCTION(Send,net_send,"gvD1greCu0A");
        /* The game wakes its own threads through 127.0.0.1:3658; the P2P port here is another one. */
        GuestAddr self;
        if (to && to_size>=sizeof(self) && loopback(to->addr) && ntohs(to->port)==P2P_DEFAULT_PORT) {
            self=*to;
            self.port=htons(bbnet_p2p_bound_port());
            to=&self;
        }
        int64_t r=net_result(net_send(fd,data,size,flags,to,to_size));
        net_log_call("send",fd,to,size,r);
        return r;
    }
    (void)flags;
    int i=socket_slot(fd);
    if (i<0) return socket_fail(EBADF);
    if (!to || to_size<8 || !loopback(to->addr)) return socket_fail(ENETUNREACH);
    struct sockaddr_in host={0};
    host.sin_family=AF_INET;
    host.sin_port=to->port;
    host.sin_addr.s_addr=to->addr;
    int64_t n=sendto(sockets[i].host,data,(int)size,0,(struct sockaddr *)&host,sizeof(host));
    return n<0 ? socket_host_fail() : n;
}
static ABI int64_t socket_receive_from(int32_t fd,void *data,uint64_t size,int32_t flags,GuestAddr *from,uint32_t *from_size) {
    if (net_socket(fd)) {
        typedef ABI int32_t (*Receive)(int32_t,void *,uint64_t,int32_t,GuestAddr *,uint32_t *);
        NET_FUNCTION(Receive,net_receive,"304ooNZxWDY");
        int64_t r=net_result(net_receive(fd,data,size,flags,from,from_size));
        net_log_call("receive",fd,from,size,r);
        return r;
    }
    (void)flags;
    int i=socket_slot(fd);
    if (i<0) return socket_fail(EBADF);
    struct sockaddr_in host={0};
    socklen_t host_size=sizeof(host);
    int64_t n=recvfrom(sockets[i].host,data,(int)size,0,(struct sockaddr *)&host,&host_size);
    if (getenv("LBPPC_SOCK_LOG") && n>=0) {
        uint64_t first=0; memcpy(&first,data,n<8 ? (size_t)n : 8);
        fprintf(stderr,"Runtime: socket %d received %lld of %llu bytes into %p, first bytes %016llx (thread %lu)\n",
                fd,(long long)n,(unsigned long long)size,data,(unsigned long long)first,(unsigned long)GetCurrentThreadId());
    }
    if (n<0) return socket_host_fail();
    if (from && from_size && *from_size>=sizeof(GuestAddr)) {
        memset(from,0,sizeof(*from));
        from->len=sizeof(GuestAddr);
        from->family=2;
        from->port=host.sin_port;
        from->addr=host.sin_addr.s_addr;
        *from_size=sizeof(GuestAddr);
    }
    return n;
}
static ABI int64_t socket_receive(int32_t fd,void *data,uint64_t size,int32_t flags) {
    return socket_receive_from(fd,data,size,flags,NULL,NULL);
}
static ABI int32_t socket_close(int32_t fd) {
    int i=socket_slot(fd);
    if (i<0) return socket_fail(EBADF);
    pthread_mutex_lock(&socket_lock);
    closesocket(sockets[i].host);
    sockets[i].used=0;
    pthread_mutex_unlock(&socket_lock);
    return 0;
}
static ABI int32_t socket_option(int32_t fd,int32_t level,int32_t name,const void *value,uint32_t size) {
    (void)level; (void)name; (void)value; (void)size;
    return socket_slot(fd)<0 ? (int32_t)0x80410109 /* EBADF */ : 0; /* the sockets never block already */
}
/* Epoll: which sockets each epoll id watches, with the game's event mask and data. */
typedef struct { uint32_t events, reserved; uint64_t ident, data; } EpollEvent;
enum { EPOLL_IN=1, EPOLL_DEL=3, MAX_WATCHES=64 };
static struct { int32_t epoll, fd; uint32_t events; uint64_t data; } watches[MAX_WATCHES];
static ABI int32_t epoll_control(int32_t epoll,int32_t op,int32_t fd,const EpollEvent *event) {
    pthread_mutex_lock(&socket_lock);
    int found=-1, empty=-1;
    for (int i=0;i<MAX_WATCHES;++i) {
        if (watches[i].epoll && watches[i].epoll==epoll && watches[i].fd==fd) found=i;
        if (!watches[i].epoll && empty<0) empty=i;
    }
    int32_t result=0;
    if (op==EPOLL_DEL) {
        if (found>=0) watches[found].epoll=0;
    } else {
        int i=found>=0 ? found : empty;
        if (i<0) result=(int32_t)0x8041010c; /* ENOMEM */
        else {
            watches[i].epoll=epoll;
            watches[i].fd=fd;
            watches[i].events=event ? event->events : EPOLL_IN;
            watches[i].data=event ? event->data : 0;
        }
    }
    pthread_mutex_unlock(&socket_lock);
    return result;
}
static ABI int32_t epoll_wait(int32_t epoll,EpollEvent *events,int32_t max,int32_t timeout_us) {
    fd_set read;
    FD_ZERO(&read);
    int watched=0;
    HostSocket highest=0;
    pthread_mutex_lock(&socket_lock);
    for (int i=0;i<MAX_WATCHES;++i) {
        int s=watches[i].epoll==epoll ? socket_slot(watches[i].fd) : -1;
        if (s<0 || !(watches[i].events&EPOLL_IN)) continue;
        FD_SET(sockets[s].host,&read);
        ++watched;
        if (sockets[s].host>highest) highest=sockets[s].host;
    }
    pthread_mutex_unlock(&socket_lock);
    struct timeval wait={timeout_us<0 ? 1 : timeout_us/1000000, timeout_us<0 ? 0 : timeout_us%1000000};
    if (!watched) {
#ifdef _WIN32
        Sleep((DWORD)(wait.tv_sec*1000+wait.tv_usec/1000));
#else
        select(0,NULL,NULL,NULL,&wait);
#endif
        return 0;
    }
    if (select((int)highest+1,&read,NULL,NULL,&wait)<=0) return 0;
    int32_t count=0;
    pthread_mutex_lock(&socket_lock);
    for (int i=0;i<MAX_WATCHES && count<max;++i) {
        int s=watches[i].epoll==epoll ? socket_slot(watches[i].fd) : -1;
        if (s<0 || !FD_ISSET(sockets[s].host,&read)) continue;
        events[count].events=EPOLL_IN;
        events[count].reserved=0;
        events[count].ident=(uint64_t)watches[i].fd;
        events[count].data=watches[i].data;
        ++count;
    }
    pthread_mutex_unlock(&socket_lock);
    return count;
}

/* Fibers (libSceFiber): guest code switching between its own execution contexts on one thread. On Windows
 * each guest fiber is a Win32 fiber: SwitchToFiber keeps the thread's stack bounds in its TEB right, which
 * exception dispatch needs (the GPU layer's page tracking faults on every thread; a stack outside those
 * bounds ends the process without a word). The fiber runs on a stack of its own with room for host code,
 * not on the game's context memory, which is only recorded. The game's OrbisFiber (256 bytes) keeps
 * upstream shadPS4's layout, with the host fiber where shadPS4 keeps its context pointer. */
#define FIBER_NULL ((int32_t)0x80590001)
#define FIBER_ALIGNMENT ((int32_t)0x80590002)
#define FIBER_RANGE ((int32_t)0x80590003)
#define FIBER_INVALID ((int32_t)0x80590004)
#define FIBER_PERMISSION ((int32_t)0x80590005)
#define FIBER_STATE ((int32_t)0x80590006)
enum { FIBER_SIGNATURE0=0xdef1649c, FIBER_SIGNATURE1=0xb37592a0, FIBER_OPT_SIGNATURE=0xbb40e64d };
enum { FIBER_RUN=1, FIBER_IDLE=2, FIBER_TERMINATED=3 };
typedef void (ABI *FiberEntry)(uint64_t arg_on_initialize,uint64_t arg_on_run);
typedef struct HostFiber HostFiber;
typedef struct {
    uint32_t magic_start, state;
    FiberEntry entry;
    uint64_t arg_on_initialize;
    void *addr_context;
    uint64_t size_context;
    char name[32];
    HostFiber *host;
    uint32_t flags;
    void *context_start, *context_end;
    uint32_t magic_end;
} GuestFiber;
typedef struct {
    uint64_t size;
    FiberEntry entry;
    uint64_t arg_on_initialize;
    void *addr_context;
    uint64_t size_context;
    char name[32];
    uint64_t size_context_margin;
    uint8_t pad[48];
} FiberInfo;
struct HostFiber {
    GuestFiber *guest;
    void *native;      /* the Win32 fiber */
    void *return_to;   /* the thread's own fiber that ran it, for sceFiberReturnToThread */
    uint64_t arg_on_run, arg_on_return;
};
static __thread HostFiber *current_fiber;
static int fiber_valid(const GuestFiber *f) {
    return f->magic_start==FIBER_SIGNATURE0 && f->magic_end==FIBER_SIGNATURE1 && f->host;
}
static ABI int32_t fiber_opt_init(uint32_t *opt) {
    if (!opt) return FIBER_NULL;
    if ((uintptr_t)opt&7) return FIBER_ALIGNMENT;
    *opt=FIBER_OPT_SIGNATURE;
    return ORBIS_OK;
}
#ifdef _WIN32
static void WINAPI fiber_start(void *param) {
    HostFiber *f=param;
    f->guest->entry(f->guest->arg_on_initialize,f->arg_on_run);
    /* An entry must end with sceFiberReturnToThread or a switch; returning has nowhere to go. */
    fprintf(stderr,"STOP: fiber '%s' returned from its entry function\n",f->guest->name);
    fflush(stderr);
    _Exit(23);
}
#endif
static ABI int32_t fiber_init(GuestFiber *fiber,const char *name,FiberEntry entry,uint64_t arg_on_initialize,
                              void *addr_context,uint64_t size_context,const uint32_t *opt,uint32_t build_version) {
    (void)build_version;
    if (!fiber || !name || !entry) return FIBER_NULL;
    if ((uintptr_t)fiber&7 || (uintptr_t)addr_context&15) return FIBER_ALIGNMENT;
    if (opt && *opt!=FIBER_OPT_SIGNATURE) return FIBER_INVALID;
    if (addr_context && size_context<512) return FIBER_RANGE;
#ifdef _WIN32
    HostFiber *host=calloc(1,sizeof(*host));
    if (!host) return FIBER_RANGE;
    host->guest=fiber;
    /* The game's context size plus room for host code (the runtime, the GPU layer, Windows). */
    host->native=CreateFiberEx(0,(SIZE_T)size_context+2*1024*1024,FIBER_FLAG_FLOAT_SWITCH,fiber_start,host);
    if (!host->native) { free(host); return FIBER_RANGE; }
    memset(fiber,0,sizeof(*fiber));
    fiber->magic_start=FIBER_SIGNATURE0;
    fiber->magic_end=FIBER_SIGNATURE1;
    fiber->state=FIBER_IDLE;
    fiber->entry=entry;
    fiber->arg_on_initialize=arg_on_initialize;
    fiber->addr_context=addr_context;
    fiber->size_context=size_context;
    snprintf(fiber->name,sizeof(fiber->name),"%s",name);
    fiber->host=host;
    fiber->context_start=addr_context;
    fiber->context_end=addr_context ? (uint8_t *)addr_context+size_context : NULL;
    return ORBIS_OK;
#else
    (void)arg_on_initialize;
    fputs("STOP: fibers are implemented on Windows only\n",stderr);
    exit(21);
#endif
}
static ABI int32_t fiber_finalize(GuestFiber *fiber) {
    if (!fiber) return FIBER_NULL;
    if ((uintptr_t)fiber&7) return FIBER_ALIGNMENT;
    if (!fiber_valid(fiber)) return FIBER_INVALID;
    if (fiber->state!=FIBER_IDLE) return FIBER_STATE;
#ifdef _WIN32
    DeleteFiber(fiber->host->native);
#endif
    free(fiber->host);
    fiber->host=NULL;
    fiber->state=FIBER_TERMINATED;
    fiber->magic_start=0;
    fiber->magic_end=0;
    return ORBIS_OK;
}
/* From a thread (not a fiber): runs the fiber until it returns to the thread. */
static ABI int32_t fiber_run(GuestFiber *fiber,uint64_t arg_on_run_to,uint64_t *arg_on_return) {
    if (!fiber) return FIBER_NULL;
    if ((uintptr_t)fiber&7) return FIBER_ALIGNMENT;
    if (!fiber_valid(fiber)) return FIBER_INVALID;
    if (current_fiber) return FIBER_PERMISSION;
    if (fiber->state!=FIBER_IDLE) return FIBER_STATE;
#ifdef _WIN32
    if (!IsThreadAFiber() && !ConvertThreadToFiberEx(NULL,FIBER_FLAG_FLOAT_SWITCH)) return FIBER_PERMISSION;
    HostFiber *f=fiber->host;
    f->return_to=GetCurrentFiber();
    f->arg_on_run=arg_on_run_to;
    fiber->state=FIBER_RUN;
    current_fiber=f;
    SwitchToFiber(f->native);
    /* Back from sceFiberReturnToThread. */
    current_fiber=NULL;
    fiber->state=FIBER_IDLE;
    if (arg_on_return) *arg_on_return=f->arg_on_return;
    return ORBIS_OK;
#else
    (void)arg_on_run_to; (void)arg_on_return;
    return FIBER_PERMISSION;
#endif
}
/* From a fiber: back to the thread that ran it; returns when a thread runs the fiber again. */
static ABI int32_t fiber_return_to_thread(uint64_t arg_on_return,uint64_t *arg_on_run) {
    HostFiber *f=current_fiber;
    if (!f) return FIBER_PERMISSION;
#ifdef _WIN32
    f->arg_on_return=arg_on_return;
    SwitchToFiber(f->return_to);
    /* Run again (fiber_run set current_fiber and the argument on that thread). */
    if (arg_on_run) *arg_on_run=f->arg_on_run;
    return ORBIS_OK;
#else
    (void)arg_on_return; (void)arg_on_run;
    return FIBER_PERMISSION;
#endif
}
static ABI int32_t fiber_get_self(GuestFiber **fiber) {
    if (!fiber) return FIBER_NULL;
    if (!current_fiber) return FIBER_PERMISSION;
    *fiber=current_fiber->guest;
    return ORBIS_OK;
}
static ABI int32_t fiber_get_info(GuestFiber *fiber,FiberInfo *info) {
    if (!fiber || !info) return FIBER_NULL;
    if ((uintptr_t)fiber&7 || (uintptr_t)info&7) return FIBER_ALIGNMENT;
    if (info->size!=sizeof(FiberInfo)) return FIBER_INVALID;
    if (!fiber_valid(fiber)) return FIBER_INVALID;
    info->entry=fiber->entry;
    info->arg_on_initialize=fiber->arg_on_initialize;
    info->addr_context=fiber->addr_context;
    info->size_context=fiber->size_context;
    memcpy(info->name,fiber->name,sizeof(info->name));
    info->size_context_margin=(uint64_t)-1;
    return ORBIS_OK;
}

/* Modules the game loads itself (LBP3's FMOD plugins): run.py links them into the image at start, and they
 * have no initializer or module_start, so loading one only hands out a handle. A module that is not there
 * is missing, as on the console. */
#define KERNEL_ENOENT ((int32_t)0x80020002)
static ABI int32_t load_start_module(const char *path,uint64_t argc,const void *argv,uint32_t flags,
                                     const void *opt,int32_t *result) {
    (void)argc; (void)argv; (void)flags; (void)opt;
    char host[4096];
    FILE *file=path && !runtime_file_translate(path,host,sizeof(host)) ? fopen(host,"rb") : NULL;
    if (!file) {
        printf("Runtime: module %s not found\n",path ? path : "(null)");
        return KERNEL_ENOENT;
    }
    fclose(file);
    if (result) *result=0;
    int32_t handle=np_new_id()+0x100;
    printf("Runtime: module %s started (linked at start), handle %d\n",path,handle);
    return handle;
}

/* The rest of HTTP, SSL and WebApi, offline like runtime_services.c's part (one state for all of it: the GPU
 * layer's own HTTP library keeps another, "not initialized" here): settings are accepted, objects are made
 * so setup proceeds, and any transfer fails as unplugged. */
#define HTTP_NETWORK ((int32_t)0x80431063)
#define SSL_NOT_FOUND ((int32_t)0x8095F00A)
static ABI int32_t new_object(void) { static int32_t next=0x1000; return __atomic_add_fetch(&next,1,__ATOMIC_RELAXED); }
static ABI int32_t http_offline(void) { return HTTP_NETWORK; }
static ABI int32_t http_get_flag(int32_t id,int32_t *enabled) { (void)id; if (enabled) *enabled=0; return ORBIS_OK; }
static ABI int32_t http_last_errno(int32_t id,int32_t *error) { (void)id; if (error) *error=0; return ORBIS_OK; }
static ABI int32_t ssl_none(void) { return SSL_NOT_FOUND; }
static ABI int32_t webapi_error(int32_t *error) { if (error) *error=0; return ORBIS_OK; }

static const RuntimeExport exports[]={
    {"sceHttpsLoadCert",ok_void}, {"sceHttpsUnloadCert",ok_void}, {"sceHttpsSetSslCallback",ok_void},
    {"sceHttpSetAutoRedirect",ok_void}, {"sceHttpSetCookieEnabled",ok_void}, {"sceHttpSetSendTimeOut",ok_void},
    {"sceHttpSetRecvTimeOut",ok_void}, {"sceHttpSetAuthEnabled",ok_void}, {"sceHttpSetRedirectCallback",ok_void},
    {"sceHttpSetCookieSendCallback",ok_void}, {"sceHttpSetCookieRecvCallback",ok_void},
    {"sceHttpGetAuthEnabled",http_get_flag}, {"sceHttpGetAutoRedirect",http_get_flag},
    {"sceHttpGetCookieEnabled",http_get_flag}, {"sceHttpGetLastErrno",http_last_errno},
    {"sceHttpCreateConnection",new_object}, {"sceHttpCreateRequest",new_object}, {"sceHttpAbortRequest",ok_void},
    {"sceHttpRemoveRequestHeader",ok_void}, {"sceHttpGetAllResponseHeaders",http_offline},
    {"sceHttpParseResponseHeader",http_offline}, {"sceHttpAddCookie",ok_void}, {"sceHttpCookieFlush",ok_void},
    {"sceSslGetIssuerName",ssl_none}, {"sceSslGetNameEntryInfo",ssl_none}, {"sceSslFreeSslCertName",ok_void},
    {"sceSslGetSerialNumber",ssl_none},
    {"sceNpWebApiCreateHandle",new_object}, {"sceNpWebApiDeleteHandle",ok_void},
    {"sceNpWebApiCreateServicePushEventFilter",new_object}, {"sceNpWebApiDeleteServicePushEventFilter",ok_void},
    {"sceNpWebApiRegisterServicePushEventCallback",new_object}, {"sceNpWebApiGetErrorCode",webapi_error},
    {"sceNpTrophyDestroyHandle",ok_void}, {"sceNpTrophyDestroyContext",ok_void},
    {"sceNpTrophyGetTrophyUnlockState",trophy_unlock_state},
    {"sceNpCommerceDialogGetResult",zero_result}, {"sceNpCommerceShowPsStoreIcon",ok_void},
    {"sceNpCommerceHidePsStoreIcon",ok_void},
    {"sceNpInGameMessageInitialize",np_new_id}, {"sceNpInGameMessageTerminate",ok_void},
    {"sceNpInGameMessageCreateHandle",np_new_id}, {"sceNpInGameMessageDeleteHandle",ok_void},
    {"sceNpInGameMessagePrepare",np_message_local}, {"sceNpInGameMessageSendData",np_message_local},
    {"sceCoredumpWriteUserData",coredump_write},
    {"sceGameCustomDataDialogInitialize",ok_void}, {"sceGameCustomDataDialogTerminate",ok_void},
    {"sceGameCustomDataDialogOpen",custom_dialog_open}, {"sceGameCustomDataDialogClose",ok_void},
    {"sceGameCustomDataDialogUpdateStatus",dialog_finished}, {"sceGameCustomDataDialogGetResult",zero_result},
    {"sceContentSearchInit",ok_void}, {"sceContentSearchTerm",ok_void},
    {"sceContentSearchGetMyApplicationIndex",search_index}, {"sceContentSearchGetContentLastUpdateId",search_update_id},
    {"sceContentSearchSearchContent",search_nothing},
    {"sceJpegDecQueryMemorySize",jpeg_memory_size}, {"sceJpegDecCreate",jpeg_create}, {"sceJpegDecDelete",jpeg_delete},
    {"sceJpegDecParseHeader",jpeg_decode_failed}, {"sceJpegDecDecode",jpeg_decode_failed},
    {"sceJpegEncQueryMemorySize",jpeg_memory_size}, {"sceJpegEncCreate",jpeg_create}, {"sceJpegEncDelete",jpeg_delete},
    {"sceJpegEncEncode",jpeg_encode_failed},
    {"socket",socket_open}, {"bind",socket_bind}, {"recv",socket_receive}, {"recvfrom",socket_receive_from},
    {"sendto",socket_send}, {"sceNetSetsockopt",socket_option}, {"sceNetSocketClose",socket_close},
    {"sceNetEpollControl",epoll_control}, {"sceNetEpollWait",epoll_wait},
    {"sceKernelLoadStartModule",load_start_module},
    {"sceFiberOptParamInitialize",fiber_opt_init}, {"_sceFiberInitializeImpl",fiber_init},
    {"sceFiberFinalize",fiber_finalize}, {"sceFiberRun",fiber_run}, {"sceFiberReturnToThread",fiber_return_to_thread},
    {"sceFiberGetSelf",fiber_get_self}, {"sceFiberGetInfo",fiber_get_info},
    {"sceSystemServiceGetDisplaySafeAreaInfo",safe_area}, {"scePadSetLightBar",pad_light_bar},
    {"sceVoiceGetPortAttr",voice_port_attr},
    {"sceRtcCheckValid",rtc_check_valid}, {"sceRtcTickAddMinutes",rtc_add_minutes},
    {"sceCameraIsAttached",camera_attached}, {"sceCameraOpen",camera_open}, {"sceCameraClose",camera_not_open},
    {"sceCameraGetConfig",camera_not_open}, {"sceCameraSetConfig",camera_not_open},
    {"sceCameraGetFrameData",camera_not_open}, {"sceCameraSetAttribute",camera_not_open},
    {"sceCameraStart",camera_not_open}, {"sceCameraStop",camera_not_open},
    {"sceMoveInit",move_init}, {"sceMoveOpen",move_open}, {"sceMoveClose",move_close},
    {"sceMoveGetDeviceInfo",move_device_info}, {"sceMoveReadStateRecent",move_read_recent},
    {"sceMoveTrackerGetWorkingMemorySize",tracker_memory}, {"sceMoveTrackerInit",ok_void},
    {"sceMoveTrackerTerm",ok_void}, {"sceMoveTrackerControllersUpdate",ok_void},
    {"sceMoveTrackerCameraUpdate",ok_void}, {"sceMoveTrackerGetState",tracker_no_state},
    {"sceGameLiveStreamingInitialize",ok_void}, {"sceGameLiveStreamingTerminate",ok_void},
    {"sceGameLiveStreamingEnableLiveStreaming",ok_void}, {"sceGameLiveStreamingGetCurrentStatus",ok_zero_out},
    {"sceGameLiveStreamingGetProgramInfo",ok_zero_out},
    {"sceGameLiveStreamingGetSocialFeedbackMessages",stream_messages},
    {"sceGameLiveStreamingGetSocialFeedbackMessagesCount",stream_message_count},
    {"sceGameLiveStreamingClearSocialFeedbackMessages",ok_void},
    {"sceGameLiveStreamingSetPresetSocialFeedbackCommands",ok_void},
    {"sceGameLiveStreamingSetPresetSocialFeedbackCommandsDescription",ok_void},
    {"sceCompanionHttpdInitialize2",ok_void}, {"sceCompanionHttpdStart",ok_void}, {"sceCompanionHttpdStop",ok_void},
    {"sceCompanionHttpdAddHeader",ok_void}, {"sceCompanionHttpdSetStatus",ok_void},
    {"sceCompanionHttpdRegisterRequestCallback2",ok_void},
    {"sceErrorDialogInitialize",ok_void}, {"sceErrorDialogOpen",error_dialog_open}, {"sceErrorDialogTerminate",ok_void},
    {"sceErrorDialogUpdateStatus",dialog_finished},
    {"sceSaveDataDialogClose",ok_void}, {"sceSaveDataDialogGetResult",save_dialog_result},
    {"sceSaveDataDialogIsReadyToDisplay",dialog_ready}, {"sceSaveDataDialogProgressBarSetValue",ok_void},
    {"sceSaveDataCheckBackupData",save_no_backup}, {"sceSaveDataRestoreBackupData",save_no_backup},
    {"sceSaveDataUmountWithBackup",save_umount_with_backup},
    {"scePlayGoClose",ok_void}, {"scePlayGoGetToDoList",playgo_todo}, {"scePlayGoGetProgress",playgo_progress},
    {"scePlayGoGetInstallSpeed",playgo_speed},
    {"sceAppContentTemporaryDataMount2",temp_mount}, {"sceAppContentTemporaryDataFormat",temp_format},
    {"sceAppContentTemporaryDataGetAvailableSpaceKb",temp_space}, {"sceAppContentGetEntitlementKey",entitlement_key},
    {"sceCoredumpRegisterCoredumpHandler",ok_void}, {"sceCoredumpUnregisterCoredumpHandler",ok_void},
    {"sceImeKeyboardOpen",keyboard_open}, {"sceImeKeyboardClose",ok_void}, {"sceImeUpdate",ok_void},
    {"sceKernelCreateEventFlag",flag_create}, {"sceKernelSetEventFlag",flag_set},
    {"sceKernelWaitEventFlag",flag_wait}, {"sceKernelSleep",kernel_sleep},
    {"sem_init",sem_init_guest}, {"sem_destroy",sem_destroy_guest}, {"sem_post",sem_post_guest},
    {"sem_wait",sem_wait_guest}, {"sem_timedwait",sem_timedwait_guest},
    {"pthread_setschedparam",thread_set_param},
};

uintptr_t runtime_extra_resolve(const char *name) { return RUNTIME_LOOKUP(exports,name); }
