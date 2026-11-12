// SPDX-License-Identifier: GPL-2.0-or-later
// bbport: user settings changed at run time from the in-game menu (bbport_overlay.h) and kept
// in lbppc.ini (BB_CONFIG overrides the path). Environment variables override the file at
// start. Readers load the atomics every frame; writers are the menu and Load().

#pragma once

#include <atomic>

namespace BbSettings {

enum Upscaler : int { UpscalerOff = 0, UpscalerFsr3 = 1, UpscalerFsr4 = 2, UpscalerFsr411 = 3,
                      UpscalerTaa = 4, UpscalerDlss = 5, UpscalerFsr412 = 6, UpscalerCount };
/// FSR 4 v07 or FSR 4.1.1: the same inputs, settings and placement in the frame.
inline bool IsFsr4(int upscaler) {
    return upscaler == UpscalerFsr4 || upscaler == UpscalerFsr411 || upscaler == UpscalerFsr412;
}
/// Every FSR 4.1 provider delegates RCAS to the host, independently of other providers.
inline bool UsesHostSharpen(int upscaler, bool active) {
    return active && (upscaler == UpscalerFsr411 || upscaler == UpscalerFsr412);
}
enum Preset : int { NativeAA = 0, Quality, Balanced, Performance, UltraPerformance, PresetCount };
enum DebugView : int { DebugNone = 0, DebugReactive = 1, DebugMotion = 2, DebugViewCount };
/// Frame generation (vk_frame_generation.h): frames generated between two of the game's.
enum FrameGen : int { FrameGenOff = 0, FrameGenFsr3 = 1, FrameGenDlss = 2, FrameGenFsr401 = 3,
                      FrameGenFsr402 = 4, FrameGenCount };
inline bool IsMlFrameGen(int provider) {
    return provider == FrameGenFsr401 || provider == FrameGenFsr402;
}
enum Fsr411Backend : int { Fsr411Vulkan = 0, Fsr411Dx12 = 1 };

/// Output resolutions: the upscaler's output and the UI host targets. The 16:9 ones change
/// live; the ultrawide ones need the startup patch (patches.py ultrawide_writes, run.py).
inline constexpr int OutputWidths[] = {1280, 1920, 2560, 3840, 2560, 3440, 3840, 5120, 3840, 5120};
inline constexpr int OutputHeights[] = {720, 1080, 1440, 2160, 1080, 1440, 1600, 2160, 1080, 1440};
inline constexpr int OutputCount = 10;
inline constexpr int OutputDefault = 1; ///< 1920x1080, the game's own size
/// Wider than 16:9: camera and HUD patched at startup.
inline bool Ultrawide(int output) {
    return OutputWidths[output] * 9 > OutputHeights[output] * 16;
}

struct Values {
    std::atomic<int> upscaler{UpscalerFsr3};
    std::atomic<int> preset{NativeAA};
    std::atomic<bool> sharpen{true};
    std::atomic<float> sharpness{0.3f};
    std::atomic<bool> jitter{true};
    std::atomic<bool> reactive{false};
    std::atomic<bool> object_motion{true};
    /// Shared launcher/overlay preference; the renderer applies it at a frame boundary.
    std::atomic<int> fsr411_backend{Fsr411Vulkan};
    std::atomic<int> fsr41_version{UpscalerFsr412};
    std::atomic<int> active_fsr411_backend{Fsr411Vulkan};
    int ActiveFsr411Backend() const { return active_fsr411_backend.load(); }
    std::atomic<bool> fsr41_backend_supported[2][2]{};
    /// FSR41 UI status codes shared with the launcher (bbport_fsr41.py).
    std::atomic<int> fsr41_status[2][2]{{5, 5}, {5, 5}};
    bool FsrMethodSupported(int version, int method) const {
        return fsr41_backend_supported[version == UpscalerFsr412][method].load();
    }
    std::atomic<const char*> fsr4_method_problem{nullptr};
    std::atomic<bool> object_motion_cache{false};
    /// UI visibility only; saved compatibility selections remain visible.
    std::atomic<bool> fsr_compatibility_options{false};
    std::atomic<float> reactive_scale{1.0f};
    std::atomic<float> reactive_threshold{0.2f};
    std::atomic<float> reactive_max{0.9f};
    std::atomic<int> debug_view{DebugNone};
    /// The FPS overlay's readouts, one toggle each (lbppc.ini: show_fps, show_frame_times,
    /// show_latency, show_vram, show_gpu, show_cpu, show_ram, show_fps_graph).
    std::atomic<bool> show_fps{false};
    std::atomic<bool> show_frame_times{false};
    std::atomic<bool> show_latency{false}; ///< render latency (vk_low_latency.cpp), not frame time
    /// The counter's values green / amber / red by how they stand (lbppc.ini counter_colors=1),
    /// or all in plain grey (0).
    std::atomic<bool> counter_colors{true};
    std::atomic<bool> show_vram{false};
    std::atomic<bool> show_gpu{false};
    std::atomic<bool> show_cpu{false};
    std::atomic<bool> show_ram{false};
    std::atomic<bool> show_fps_graph{false};
    /// lbppc: the graph plots the game's own frames (its flips: 30 a second in LBP3) instead of every
    /// presented image, generated ones included, which hide the game's stutter (lbppc.ini graph_game_frames).
    std::atomic<bool> graph_game_frames{true};
    /// Silence the game while its window does not have focus (bbgpu_audio_muted).
    std::atomic<bool> mute_unfocused{false};
    // FSR 4 checks (menu): the provider's auto exposure, the jitter sign it is given.
    std::atomic<bool> fsr4_auto_exposure{true};
    std::atomic<bool> fsr4_invert_jitter{false};
    std::atomic<int> active_render_width{1920}, active_render_height{1080};
    std::atomic<int> output_res{OutputDefault}; ///< index into OutputWidths
    /// Sharp UI: menus and HUD drawn at the output's size (patches.py ui_viewport at start). Set in
    /// the launcher; kept here so the menu's Save does not drop it.
    std::atomic<bool> sharp_ui{false};
    /// Why FSR 4 cannot run (assets, device features), or null. Set by the renderer.
    std::atomic<const char*> fsr4_problem{nullptr};
    std::atomic<bool> fsr4_supported{false}, fsr411_supported{false};
    std::atomic<const char*> fsr411_problem{nullptr};
    /// DLSS (bbport_dlss.dll, NVIDIA RTX) is ready, or why not (null before the device exists).
    std::atomic<bool> dlss_supported{false};
    std::atomic<const char*> dlss_problem{nullptr};
    /// Frame generation and the frames per game frame (2x with FSR 3.1, up to 4x with DLSS on
    /// RTX 50). dlssg_max_multiplier: 1 without DLSS Frame Generation.
    std::atomic<int> frame_gen{FrameGenOff};
    /// Session fallback is separate from the saved ML-FG selection.
    std::atomic<int> fg401_fallback{-1};
    std::atomic<int> fg401_fallback_source{-1};
    int ActiveFrameGen() const {
        const int selected = frame_gen.load();
        const int fallback = fg401_fallback.load();
        return IsMlFrameGen(selected) && fg401_fallback_source.load() == selected && fallback >= 0
                   ? fallback : selected;
    }
    std::atomic<int> frame_gen_multiplier{2};
    /// At most the frames the display shows between two of the game's (an uncapped game then
    /// gets fewer generated frames); off: the multiplier as chosen.
    std::atomic<bool> frame_gen_fit_display{false};
    std::atomic<bool> fg401_supported{false};
    std::atomic<const char*> fg401_problem{nullptr};
    std::atomic<bool> fg402_supported{false};
    std::atomic<const char*> fg402_problem{nullptr};
    bool MlFgSupported(int provider) const {
        return provider == FrameGenFsr402 ? fg402_supported.load() : fg401_supported.load();
    }
    const char* MlFgProblem(int provider) const {
        return provider == FrameGenFsr402 ? fg402_problem.load() : fg401_problem.load();
    }
    std::atomic<bool> frame_gen_hdr{false};
    std::atomic<int> dlssg_max_multiplier{1};
    std::atomic<const char*> frame_gen_problem{nullptr};
    /// Generated frames per game frame the display has room for now (0: paused), and its
    /// refresh rate (the presenter's measure, for the menu).
    std::atomic<int> frame_gen_now{0};
    std::atomic<int> frame_gen_display_hz{0};
    /// The game's own frame rate (presenter, game frames only), shown beside the total when
    /// frame generation adds frames.
    std::atomic<float> game_fps{0.0f};
    /// The time from the game starting a frame to its present, smoothed (vk_low_latency.cpp),
    /// and the low latency mode: 0 off, 1 Reflex, 2 Reflex + Boost, 3 Anti-Lag 2,
    /// 4 pacing only.
    std::atomic<float> latency_ms{0.0f};
    std::atomic<int> latency_mode{0};
    /// The game's frame rate cap frame generation needs (VideoOut applies it), 0 for none: with
    /// FIFO presentation (V-Sync; AMD has no Mailbox) at most refresh / (generated + 1), or each
    /// extra frame waits for the display and generation pauses.
    std::atomic<int> frame_gen_flip_limit{0};
    /// Why this GPU runs without object motion vectors whatever the setting says (vk_object_motion.cpp:
    /// RX 6000 and older, under 6 GB of VRAM), nullptr when the setting decides.
    std::atomic<const char*> object_motion_unavailable{nullptr};

    /// Startup settings for the explicit BB_RENDER_RES compatibility patch only.
    int startup_preset = NativeAA;
    int startup_upscaler = UpscalerFsr3;
    bool startup_object_motion = true;
    int startup_fsr411_backend = Fsr411Vulkan;
    bool startup_object_motion_cache = false;
    int startup_output_res = OutputDefault;
};

Values& Get();

/// Reads the file, then the environment overrides. Called once at start.
void Load();
/// Checks the loaded choice before the first frame; unsupported FSR 4 uses FSR 3.1.
void ConfigureUpscalerSupport(bool fsr4, bool fsr411, const char* fsr411_problem = nullptr);
/// After device creation: DLSS availability; a DLSS setting falls back to FSR 3.1 without it.
void ConfigureDlssSupport(bool available, const char* problem);
void ConfigureFg401Support(bool available, const char* problem, int provider = FrameGenFsr401);
void SelectFrameGen(int provider);
void FrameGenFailure(int provider, const char* problem);
/// Startup-patched scene dimensions cannot change until run.sh prepares a new image.
bool FixedRenderSession();
int RenderPreset();
bool ResolutionNeedsRestart();
/// The cache keeps its startup behavior; provider changes apply live.
bool FsrSettingsNeedRestart();
/// Writes the file (menu changes).
void Save();

/// Render resolution divisor of a preset (1.0 native, 1.5 quality, ...).
float PresetScale(int preset);
const char* PresetName(int preset);
const char* UpscalerName(int upscaler);
const char* FrameGenName(int frame_gen);

} // namespace BbSettings
