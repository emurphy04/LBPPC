// bbport: optimizations that can be switched off while the game runs (BB_TOGGLE_FILE,
// see runtime_memory.c), to find which one changes rendering without restarting.
#pragma once
#include <atomic>
#include <csetjmp>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <mutex>
#ifdef _WIN32
// bbport (Windows): no signals. The loader's exception handler resumes the faulting thread
// in bb_longjmp (src/compat_win.c: no unwinding through the faulting frames).
typedef unsigned long long sigjmp_buf[32];
extern "C" __attribute__((returns_twice)) int bb_setjmp(sigjmp_buf buffer);
#define sigsetjmp(buffer, save) bb_setjmp(buffer)
#endif

extern "C" std::uint64_t runtime_disabled_optimizations;
/// Recovery point for speculative guest memory reads on this thread (runtime_memory.c).
extern "C" __thread sigjmp_buf* runtime_fault_recover;

namespace BbToggle {
enum : std::uint64_t {
    RegionCache = 1,
    FetchShaderCache = 2,
    PageTrackingEarlyExit = 4,
    PendingPollLimit = 8,
    ThreadedRecording = 16,
    ImageDescCache = 32,
    LockFreeUploadCheck = 64,
    FindImageCache = 128,
    DeferredUploads = 256,
    AccessMemo = 512,
    TextureBindingMemo = 1024,
    CoarseReadTracking = 2048,
    PreparedResources = 4096,
    DrawPreparation = 8192,
    DeferredStreamCopies = 16384,
    HotPages = 32768,
    FaultWindow = 65536,
    ParallelCopies = 131072,
    AsyncFences = 262144,
    PoolSmallCopies = 524288,
    RecordPrefetch = 1ull << 32,
    TextureViewMemo = 1ull << 33,
    TextureBindHelper = 1ull << 34,
    EarlyDrawInputs = 1ull << 35,
    ConstantRing = 1ull << 36,
    DrawPipeline = 1ull << 37,
    PipelinedTasks = 1ull << 38,
    PendingFenceWaits = 1ull << 39,
    PipelinedDispatch = 1ull << 40,
    RecorderFences = 1ull << 41,
    PipelinedMemoryWrites = 1ull << 42,
    MultiCopyShader = 1ull << 43,
    RenderStateMemo = 1ull << 44,
    TextureSetMemo = 1ull << 45,
    PipelinedIndirectDraws = 1ull << 46,
    SceneAttachmentsOnly = 1ull << 47,
    SampleSceneProxies = 1ull << 48,
    OrderedGuestWrites = 1ull << 49,
    GcRelievable = 1ull << 59, ///< eviction marks count only where evicting textures can relieve them
    SceneHalfRes = 1ull << 50, ///< live scaling also reduces the 960x540 post targets
    UpdateImageFastPath = 1u << 30,
    // TAA A/B in one run: optional techniques, off by default (no measured gain, 2026-10-02).
    TaaTonemapBlend = 1ull << 51,
    TaaClip = 1ull << 52,
    TaaVariance = 1ull << 53,
    TaaFilter = 1ull << 54,
    // On by default (bit set: off): history of a thin feature this jitter phase missed is kept
    // when nothing moves. Static-camera flicker of railings/window bars p99.9 -45% (2026-10-03).
    TaaKeepNearerHistory = 1ull << 55,
    // lbppc: render targets stacked back to back in memory and sampled as one taller texture reach it
    // (TextureCache::GatherStackedTargets, LBP3's lights, #9/#32). Set: the texture is read from memory only, as
    // before. In user/toggles.txt: 72057594037927936.
    StackedTargets = 1ull << 56,
    SceneMipBias = 1ull << 57, ///< negative LOD bias of G-buffer samplers at reduced scene sizes
    ZpassInOrder = 1ull << 58, ///< occlusion counter dumps in order with the guest's own writes
    // Game memory backing the GPU has not used for a while is released when VRAM runs short
    // (BufferCache::RunArenaCollector); set: it is kept for good, as before.
    ArenaRelease = 1ull << 60,
    // lbppc: the orange flare fix (vk_rasterizer.cpp): a compute shader's write over a render target taken as a
    // clear of it. Set: never (as before the fix). In user/toggles.txt: 2305843009213693952.
    TargetClears = 1ull << 61,
    // lbppc: a shader's write to buffer memory under cached images reaches them (vk_rasterizer.cpp
    // SyncImagesForBufferWrite): their GPU-drawn data is written back to the buffer first, and they reload
    // from it after, as on the PS4, where both are the same memory. Set: the write is ignored as before
    // (LBPPC_NO_BUFFER_IMAGE_INVALIDATE) and only the TargetClears guess applies. In user/toggles.txt:
    // 4611686018427387904.
    BufferWriteImageSync = 1ull << 62,
    // lbppc: set: the LBP server's empty /npdata reply is left as it is (http.cpp AnswerNpData), so friends are not
    // LBP friends (the Pod's friends list is empty). For co-op A/B tests. In user/toggles.txt: 9223372036854775808.
    NpDataFriends = 1ull << 63,
    // Bits 20-29 are used as raw debug toggles by the camera/object motion and the upscaler.
};
inline bool Disabled(std::uint64_t bit) {
    return (__atomic_load_n(&runtime_disabled_optimizations, __ATOMIC_RELAXED) & bit) != 0;
}
} // namespace BbToggle

namespace BbStats {
/// Guest writes caught by page protection, and pages currently left unprotected as hot.
inline std::atomic<std::uint64_t> tracker_faults{0};
inline std::atomic<std::int64_t> hot_pages{0};
/// Stall diagnostics (BB_FRAME_STATS): per-frame deltas printed for frames over 40 ms.
inline std::atomic<std::uint64_t> images_registered{0};
inline std::atomic<std::uint64_t> image_upload_bytes{0};
inline std::atomic<std::uint64_t> buffer_upload_bytes{0};
inline std::atomic<int> gpu_thread_clock{-1}; ///< clockid_t of the GPU command thread
inline std::atomic<std::uint64_t> draws{0}, dispatches{0}, submissions{0};
/// Frames the GPU command thread has started (display pass), for per-frame diagnostics.
inline std::atomic<std::uint64_t> gpu_frames{0};
/// Wall time spent in operations suspected of stalls (ns, all threads).
inline std::atomic<std::uint64_t> t_resident{0}, t_protect{0}, t_image_create{0}, t_refresh{0},
    t_read_faults{0}, read_faults{0}, t_write_faults{0};
/// Faults of guest threads on GPU-written memory: each waits for the GPU thread to read it back.
inline std::atomic<std::uint64_t> fault_readbacks{0}, t_fault_readbacks{0};
/// Diagnostics are collected only with BB_FRAME_STATS=1.
inline const bool enabled = [] {
    const char* env = std::getenv("BB_FRAME_STATS");
    return env && env[0] == '1';
}();
struct Timer {
    std::atomic<std::uint64_t>& total;
    std::chrono::steady_clock::time_point start =
        enabled ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
    ~Timer() {
        if (!enabled) {
            return;
        }
        total.fetch_add(std::chrono::duration_cast<std::chrono::nanoseconds>(
                            std::chrono::steady_clock::now() - start)
                            .count(),
                        std::memory_order_relaxed);
    }
};
/// The longest single protection change and guest write fault since the last frame (ns): Windows
/// sometimes holds one for ~30 ms, and every game thread that faults meanwhile waits.
inline std::atomic<std::uint64_t> max_protect_ns{0}, max_write_fault_ns{0};
struct MaxTimer {
    std::atomic<std::uint64_t>& total;
    std::atomic<std::uint64_t>& longest;
    std::chrono::steady_clock::time_point start =
        enabled ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
    ~MaxTimer() {
        if (!enabled) {
            return;
        }
        const std::uint64_t ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
                                     std::chrono::steady_clock::now() - start)
                                     .count();
        total.fetch_add(ns, std::memory_order_relaxed);
        std::uint64_t seen = longest.load(std::memory_order_relaxed);
        while (ns > seen && !longest.compare_exchange_weak(seen, ns, std::memory_order_relaxed)) {
        }
    }
};
/// Wall time the GPU command thread waited for guest submissions (ns).
inline std::atomic<std::uint64_t> gpu_idle_ns{0};
/// Draws recorded into the reduced scene targets, and draws after the scene started.
inline std::atomic<std::uint64_t> reduced_draws{0}, scene_draws{0};
/// Wall time spent blocked in the scheduler (ns): waiting for the recording thread to drain,
/// for host copies before a submission or fence, and for GPU ticks.
inline std::atomic<std::uint64_t> sync_recording_ns{0}, host_copies_wait_ns{0}, tick_wait_ns{0},
    copy_threads_wait_ns{0}, host_copy_waits{0};
struct WaitTimer {
    std::atomic<std::uint64_t>& total;
    std::chrono::steady_clock::time_point start = std::chrono::steady_clock::now();
    ~WaitTimer() {
        total.fetch_add(std::chrono::duration_cast<std::chrono::nanoseconds>(
                            std::chrono::steady_clock::now() - start).count(),
                        std::memory_order_relaxed);
    }
};
/// Waits for GPU ticks by the code that asked for them, so a stall names where it waited.
struct WaitSite {
    const char* file = nullptr;
    std::uint32_t line = 0;
    std::uint64_t ns = 0;
};
inline std::mutex wait_sites_mutex;
inline WaitSite wait_sites[16];
inline void NoteWaitSite(const char* file, std::uint32_t line, std::uint64_t ns) {
    std::scoped_lock lock{wait_sites_mutex};
    for (auto& site : wait_sites) {
        if (site.file == nullptr || (site.line == line && site.file == file)) {
            site = {file, line, site.ns + ns};
            return;
        }
    }
}
/// The site that waited longest since the last call (file null if none), and resets them.
inline WaitSite TakeLongestWaitSite() {
    std::scoped_lock lock{wait_sites_mutex};
    WaitSite longest{};
    for (auto& site : wait_sites) {
        if (site.file && site.ns > longest.ns) {
            longest = site;
        }
        site = {};
    }
    return longest;
}
/// GPU thread rusage, refreshed after each graphics submission.
inline std::atomic<std::uint64_t> gpu_sys_us{0}, gpu_user_us{0}, gpu_invol_switches{0},
    gpu_vol_switches{0}, gpu_minor_faults{0};
/// Protection faults (signals) taken by the GPU thread itself.
inline std::atomic<std::uint64_t> gpu_signal_faults{0};
/// Protection changes: calls and pages, those removing write access (TLB shootdowns) apart.
inline std::atomic<std::uint64_t> protect_calls{0}, protect_pages{0}, protect_revoke_calls{0},
    protect_revoke_pages{0};
} // namespace BbStats
