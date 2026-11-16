// SPDX-License-Identifier: GPL-2.0-or-later
#include <cstdlib>
#include <chrono>
#include <cstdint>
#include <cstddef>
#include "bbport_toggles.h"
// Renderer tests have no guest process. Clock/host-thread services work; guest accesses abort.
extern "C" {
__thread sigjmp_buf* runtime_fault_recover = nullptr;
uint64_t runtime_disabled_optimizations = 0;
uint64_t runtime_tsc_frequency() { return 1000000000; }
int runtime_file_translate(const char*, char*, size_t) { std::abort(); }
uint64_t runtime_memory_clamp(uintptr_t, uint64_t) { std::abort(); }
uint64_t runtime_process_time_us() { return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
int runtime_memory_region(uintptr_t, uintptr_t*, uintptr_t*, int*) { std::abort(); }
void runtime_memory_set_gpu_hooks(void (*)(uintptr_t, uint64_t),
    void (*)(uintptr_t, uint64_t), void (*)(uintptr_t, uint64_t)) { std::abort(); }
void runtime_thread_attach_host(const char*) {}
void runtime_memory_gpu_protect(uintptr_t, uint64_t, int, int) { std::abort(); }
void runtime_restart() { std::abort(); }
// Added since 0.1.1 (memory reads, threads, system events, SPU barrier, running report): guest work aborts.
int runtime_memory_is_mapped(uintptr_t, uint64_t) { return 0; }
void runtime_memory_read_backing(uintptr_t, void*, uint64_t) { std::abort(); }
void runtime_system_event_push(const void*, uint64_t) {}
int32_t runtime_thread_spawn(void**, void* (*)(void*), void*, uint64_t, const char*) { std::abort(); }
int32_t runtime_thread_join_spawned(void*, void**) { std::abort(); }
[[noreturn]] void runtime_thread_exit(void*) { std::abort(); }
void lbppc_spu_barrier() {}
void lbppc_running_report() {}
#ifdef _WIN32
int bb_setjmp(unsigned long long*) { std::abort(); }
#endif
uint64_t runtime_process_time_counter() { return runtime_process_time_us() * 1000; }
int32_t* runtime_errno() { std::abort(); }
int runtime_memory_write_backing(uintptr_t, const void*, uint64_t) { std::abort(); }
}

