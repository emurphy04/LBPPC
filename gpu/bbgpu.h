/* C interface of the GPU library (gpu/): shadPS4's Liverpool/Vulkan video core,
 * GnmDriver, VideoOut and kernel event queues, adapted to the native loader. */
#ifndef BBGPU_H
#define BBGPU_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    const char *title;          /* window title */
    const char *serial;         /* CUSA id, names the pipeline cache */
    const char *user_dir;       /* pipeline cache/logs directory */
    uint32_t sdk_version;       /* from the eboot's procparam */
    uint32_t psf_attributes;    /* param.sfo ATTRIBUTE */
    int32_t width, height;      /* initial window size */
} BbGpuConfig;
/* Registers kernel event queues (needed with or without graphics). */
void bbgpu_register_kernel(void);
/* Co-op: the game folder and param.sfo identity for the network and PSN libraries. */
/* system: the game's sce_sys folder (param.sfo, npbind.dat), wherever the extraction put it. */
void bbnet_configure(const char *system, const char *serial, const char *title, const char *app_ver);
/* The online session's start (bbnet/bbnet_glue.cpp). */
void bbnet_initialize(void);
/* Creates window, Vulkan device, presenter and GPU command processor. */
int bbgpu_init(const BbGpuConfig *config);
/* Function for an imported NID ("NID#lib#mod"), or 0 when the GPU library does not provide it. */
uintptr_t bbgpu_resolve(const char *scoped_nid);
/* Called first by the loader's SIGSEGV handler: 1 when a GPU page-tracking fault was handled. */
int bbgpu_handle_fault(void *ucontext, void *address);
/* BB_WRITE_LOG=1: prints the logged GPU-side writes to guest memory near the fault. */
void bbgpu_dump_guest_writes(void *ucontext);
/* lbppc: the main video port's flip rate (0: every vblank, 1: every other). */
void bbgpu_set_flip_rate(int rate);
/* lbppc: the frame limit (BB_FPS_LIMIT) while the game runs, in frames per second; 0 lifts it. */
void bbgpu_set_frame_limit(unsigned fps);
/* Keyboard text entry through the game window (IME dialog). begin returns 0 when
 * no window exists; poll returns 0 typing, 1 confirmed, 2 cancelled (UTF-8 text). */
int bbgpu_text_input_begin(const char *initial_utf8, const char *prompt_utf8);
int bbgpu_text_input_poll(char *out_utf8, uint64_t size);
/* 1 while the in-game settings menu is open: the game's pad input is held neutral. */
int bbgpu_overlay_captures_input(void);
/* lbppc: joins the newest invitation waiting (bbport_invite.h), from LBP3's own prompt; 0 when none. Any
 * thread. */
int bbgpu_invite_accept_shown(void);
/* 1 while the game should be silent: lbppc.ini mute_unfocused is on and the window does not
 * have focus. Safe from any thread (the audio ports read it for every buffer). */
int bbgpu_audio_muted(void);

/* Mouse input accumulated by the window thread since the last call; the call resets the
 * motion (dx/dy) but not held buttons. All zero (and captured=0) while capture is inactive
 * (mouse_to_joystick absent or off, no window focus, menu or text entry open). buttons uses
 * SDL_BUTTON_MASK() bits (bit 0 = SDL_BUTTON_LEFT, ...), NOT the SDL_BUTTON_* index runtime_
 * input_config.c stores for a mouse_left/mouse_right/... binding -- convert with
 * SDL_BUTTON_MASK(value) before comparing. wheel bit 0 up, 1 down, 2 left, 3 right: a 33 ms
 * pulse per step (MOU-007), already decayed by the time this call sees it.
 * See specs/keyboard-and-mouse/spec-design-keyboard-mouse-input.md section 4.5 (API-002). */
typedef struct {
    float dx, dy;
    uint32_t buttons;
    uint32_t wheel;
    int32_t captured;
} BbMouseInput;
void bbgpu_mouse_take(BbMouseInput *out);
/* The mouse's running totals as they were at the game's last frame (taken at each flip): the
 * mouse as the touchpad moves its finger once a game frame by the motion between two of these,
 * so every frame gets one frame's motion. Reading the motion at each pad read instead gave
 * frames 2 or 3 reads' worth (83 reads/s against 30 frames/s): a cursor speeding up and slowing
 * down. Totals only grow while capture is active; wheel counts up steps minus down steps. */
typedef struct {
    double dx, dy;
    int32_t wheel;
    uint32_t frame;
} BbMouseFrame;
void bbgpu_mouse_frame(BbMouseFrame *out);
void bbgpu_note_game_frame(void); /* the GPU thread, at each game flip */
/* Closes the game as the window's close button does (Quit Game in the pause menu). Any thread. */
void bbgpu_request_quit(void);
/* Applies a just-(re)loaded input.ini to the window thread: whether mouse-to-joystick mode
 * exists at all (mouse_mode_available; MOU-001), and the current toggle/reload hotkey
 * scancodes (SDL_SCANCODE_UNKNOWN = none bound; HOT-002). Safe from any thread; takes effect
 * on the window thread's next PollEvents. */
void bbgpu_input_configure(int mouse_mode_available, int32_t toggle_scancode, int32_t reload_scancode);
/* 1 once after the reload hotkey (default F8) was pressed, then clears back to 0 -- the pad
 * thread polls this once per sample() (CFG-008) to know when to re-read input.ini. 0 if no
 * window exists yet. */
int bbgpu_input_reload_requested(void);
/* Number of symbols registered by the vendored libraries (diagnostics). */
unsigned bbgpu_symbol_count(void);
#ifdef __cplusplus
}
#endif
#endif
