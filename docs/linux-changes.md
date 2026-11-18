# Linux support: guest fibers, a native Qt launcher, and reproducible builds

These changes make LBPPC playable on Linux without changing the Windows build. Linux uses a new Qt launcher that is
native on Wayland and X11; Windows keeps the Tk launcher. The two launchers share toolkit-free code in
`launcher/lbppc_core.py`, so neither has to build or import the other's UI toolkit.

The notes from the debugging session that produced these changes — machine details, logs, measured VRAM use and
artifact hashes — are in [linux-testing.md](linux-testing.md).

---

## 1. Summary

| Area | Change |
|---|---|
| Runtime | Guest fibers (`libSceFiber`) implemented on Linux with POSIX `ucontext`, replacing the Windows-only path that called `exit(21)`. Windows is unchanged. |
| Game files | Read-only game paths resolve ignoring case when the exact spelling is missing, because the PS4 mounts are case-insensitive and Linux is not. Saves and writable paths keep exact names. |
| File tracing | `LBPPC_FILE_TRACE=1` logs every open, stat and access with the PS4 path and the host file and folder; `reads` adds every read and pread. |
| Linux launcher | New PySide6/Qt launcher (`launcher/lbppc_qt.py`), native Wayland with an X11 fallback, with the same settings, game check and options as the Windows launcher. |
| Why a new launcher | The Tk launcher is an X11 application: on Wayland it needs XWayland, its Windows-only window attributes and screen sizing fail on X11, and its fullscreen modes can change the desktop resolution. |
| Shared launcher code | `launcher/lbppc_core.py` holds the settings, game check, game environment, process stopping and report code used by both launchers. It imports neither Tk nor Qt. |
| Linux packaging | The AppImage freezes the Qt launcher, bundles PySide6-Essentials, exports a `wayland;xcb` platform fallback and no longer collects the dynamic loader as a library. |
| Docker build | `packaging/linux/Dockerfile` reproduces the Ubuntu 24.04 CI toolchain so anyone can build the game and the AppImage from source on Linux. |
| i18n | The Qt launcher's texts are included in `python3 launcher/i18n.py --extract`. |
| Windows | The Tk launcher keeps its previous behavior; only the shared code and the `VERSION` constant moved out of it. |

The case-insensitive game-file diagnosis and the successful run are in
[linux-testing.md](linux-testing.md#34-game-files-and-case-insensitive-paths). The trace switch is in
[troubleshooting.md](troubleshooting.md#tracing-file-access).

---

## 2. Blocker: guest fibers were Windows-only

### Symptom

The game started, rendered, and then stopped mid-frame with:

```
STOP: fibers are implemented on Windows only
```

The process exited with code 21. This happened as soon as the game initialised one of its `libSceFiber` fibers, so
the game could not get past the first frame that used one.

### Cause

`src/runtime_extra.c` had a Win32 implementation of the fiber API and this fallback for every other platform:

```c
#else
    (void)arg_on_initialize;
    fputs("STOP: fibers are implemented on Windows only\n", stderr);
    exit(21);
#endif
```

`fiber_run` and `fiber_return_to_thread` returned `FIBER_PERMISSION` on non-Windows, but `fiber_init` exited before
they could be reached. The port had never needed a Linux implementation until now.

### Fix

The Linux path now uses POSIX `ucontext` (`getcontext`, `makecontext`, `swapcontext`). Windows keeps its Win32
fibers, unchanged.

`HostFiber` gains two POSIX-only fields:

```c
struct HostFiber {
    GuestFiber *guest;
#ifdef _WIN32
    void *native;
    void *return_to;
#else
    ucontext_t context;  /* where the fiber is when it is not running */
    void *stack;         /* its stack (the game's context memory is only recorded) */
#endif
    uint64_t arg_on_run, arg_on_return;
};
static __thread HostFiber *current_fiber;
#ifndef _WIN32
static __thread ucontext_t fiber_thread_context; /* where the thread is when its fiber runs */
#endif
```

* **`fiber_prepare`** allocates `size_context + 2 MiB` (the game's context size plus room for the runtime, the GPU
  layer and glibc), aligns it to 16 bytes, calls `getcontext`, sets `uc_stack`, sets `uc_link = NULL` and calls
  `makecontext(&host->context, fiber_start, 0)`. The fiber never returns from its entry, so `fiber_start` stops the
  process with a clear message if it does.
* **`fiber_run`** stores the argument, marks the fiber running, sets `current_fiber`, saves the thread's context in
  `fiber_thread_context`, and swaps to the fiber. When `sceFiberReturnToThread` comes back, it clears the state and
  returns the fiber's `arg_on_return`.
* **`fiber_return_to_thread`** swaps from the fiber back to `fiber_thread_context`; when the thread runs the fiber
  again through `fiber_run`, it resumes after the swap and returns the new `arg_on_run`.
* **`fiber_finalize`** frees the stack.

The guest-visible layout is unchanged: the game's `OrbisFiber` (256 bytes) still keeps upstream shadPS4's layout,
with LBPPC's host pointer where shadPS4 keeps its context pointer. The game's own context memory is still only
recorded, not used as the host stack.

### Why `ucontext` rather than another approach

* The fiber stays on the same thread, so TLS, signal state and the GPU layer's page tracking stay valid. The GPU
  layer faults on protected pages; moving fibers to another thread would change which thread owns those faults.
* `swapcontext` saves and restores the register state and the stack pointer, which is exactly the guest ABI the
  fiber API needs.
* Win32 fibers use `CreateFiberEx`/`SwitchToFiber` because the Windows GPU layer's exception dispatch needs the
  thread's TEB stack bounds to match the running stack. On Linux the equivalent stack is a real allocated stack, so
  `ucontext` is enough.

`getcontext` makes GCC's `-Wclobbered` warn about locals around a returns-twice call, although control returns to
the same point and none of them is written between the two returns. Since the game is built with `-Wextra -Werror`,
the warning is suppressed narrowly around `fiber_prepare`:

```c
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wclobbered"
static int fiber_prepare(HostFiber *host, uint64_t size_context) { ... }
#pragma GCC diagnostic pop
```

The game reaching gameplay on Linux after the fix is confirmed in [linux-testing.md](linux-testing.md).

---

## 3. A separate native Qt launcher for Linux

### Why the Tk launcher was not used on Linux

The Tk launcher draws its own interface and is an X11 application. Running it on a Wayland desktop requires
XWayland, with the desktop's resolution changes that come with fullscreen X11 modes. It also hit Tk bugs on X11:

* `-transparentcolor` is a Windows-only Tk attribute; setting it on X11 raises `TclError: bad attribute
  "-transparentcolor"`, so notice and dialog windows never appeared.
* Tk's screen size spans every monitor on X11, so the launcher was sized for the whole desktop rather than the
  primary monitor.
* The old `AppRun` required `DISPLAY` even on a Wayland desktop, because the Tk window needed XWayland.

Rather than carry those fixes into a launcher Windows still uses unchanged, Linux now uses `launcher/lbppc_qt.py`.
Qt's own widgets make a native Wayland window on KDE or GNOME and a native X11 one elsewhere, without changing the
desktop resolution. The Tk launcher remains the Windows launcher.

Both launchers read and write the same files (`launcher.json`, `lbppc.ini`, `input.ini`, the save folders) and start
the game through `run.py` with the same environment from `lbppc_core.py`.

### Layout

```
LBPPC                         header: logo, title, version
├─ Game                       game folder, settings, save file, online, tools
├─ Friends                    shadNet requests, friends, sent requests, Discord
├─ Controls                   keys, second keys, controller buttons, reset
└─ Debug                      performance overlay, diagnostics
Play / Stop                   status line, optional log pane
```

The Game and Debug pages are scrollable; the window opens at 980x820 and can be resized.

### Option parity

Every option the Tk launcher offers is present. (Mouse-to-joystick is hidden in both launchers while
`MOUSE_SUPPORTED` is `False`; see follow-ups.)

**Game**

* Game folder entry, **Browse...**, **Set up game files...** (the update/DLC wizard from `scripts/game_setup.py`)
* **Settings:** resolution, display mode, frame generation, low latency, upscaling, sharpening, language — the same
  option lists as `lbppc_core.OPTIONS`
* **Save file:** choose, **Rename / delete**, **Import save...**
* **Online:** play online, online name, password, **Create account**, **Sign in with lbppc.com**, **Sign out**,
  Discord presence
* **Tools:** Show log, Open log folder, Log file, Saves folder, Make a report, Desktop shortcut, Discord

**Friends**

* Add by online name
* Friend requests (accept/decline), friends with online/offline state, sent requests (cancel)
* Remove with two clicks (the first asks **Remove?**, the second removes; the question clears after 4 s)
* Refresh every 60 s; retry after 20 s when sign-in fails
* Sign-in pauses while the game runs, and resumes when it closes
* Join the Discord

**Controls**

* Every action from `input_settings.ACTIONS`, with the first key, second key and controller button
* Click a key and press the new key; right-click clears it; Esc cancels
* Keypad, function keys, modifiers and punctuation mapped to `input.ini`'s parser names
* Reserved keys rejected with the game's own explanation
* Writes `input.ini` immediately; F8 in the game reloads it
* **Reset to defaults**

**Debug**

* The 10 performance-overlay switches, written to `lbppc.ini` (the game also changes them with Insert)
* The 7 diagnostics, saved in `launcher.json` and passed to the game on Play

**Common**

* Play / Stop, status line with ready/warn/error states
* Optional game log, shader-progress parsing (`Preparing shaders (n%)`), frame-rate line detection
* Update dialog with download progress, SHA-256 check, staging and self-install (`scripts/updater.py`)
* `--play` direct start; the update offer is deferred to the next normal start
* `--script` and `--apply-update` handling, matching the frozen-package layout

Behaviour kept from the Tk launcher:

* Changing the language restarts the launcher (the language of all texts is fixed at start-up).
* Save files cannot be changed, renamed, deleted or imported while the game runs.
* Friends sign-in pauses while the game runs.
* Updates are offered except in `--play` mode.
* Online name/password are validated before Play; an lbppc.com sign-in refreshes its token instead, and errors from
  either are shown in the status line.
* The log is scrubbed of names and addresses by `scripts/log_privacy.py`, as before.

### Native Wayland

`AppRun` and the launcher both default to:

```sh
QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland;xcb}"
```

Qt tries native Wayland first and falls back to X11 (XWayland) if the desktop has no Wayland. A player's own
`QT_QPA_PLATFORM` wins, so `QT_QPA_PLATFORM=xcb` still forces XWayland. `AppRun` only warns when neither `DISPLAY`
nor `WAYLAND_DISPLAY` is set, instead of requiring X11.

---

## 4. Shared code, without either toolkit

`launcher/lbppc_core.py` is a plain Python module: it imports neither `tkinter` nor PySide6, and neither launcher
imports the other. The Windows package freezes `lbppc_launcher.py` (Tk) and the Linux package freezes
`lbppc_qt.py` (Qt); both include the same core module. The core holds:

* paths, `VERSION`, the option lists (`OPTIONS`, `LANGUAGES`, `DIAGNOSTICS`, `OVERLAY`) and `DEFAULTS`;
* `load_settings()` / `save_settings()`: `launcher.json`, the DPAPI-locked password and lbppc.com sign-in, and
  `lbppc.ini`'s frame generation setting;
* `check_game()` and the `scripts/game_setup.py` / `scripts/game_files.py` checks;
* **`game_environment(settings, friends=None, warn=None, auth=None)`**: the environment `run.py` gets — render
  resolution, save folder, window mode, FSR 1, language, sharpening, low latency, online account and shadNet
  endpoints, diagnostics, version and log-privacy names. It also saves `input.ini` again with the mouse off when an
  older file still enables it. It raises `online_accounts.AccountError` for an unusable name/password and
  `lbppc_signin.SignInError` when an lbppc.com sign-in cannot produce a token. With `auth` signed in, the game gets
  a fresh `lbppc-token:` access token as `BB_SHADNET_PASSWORD`; otherwise the name and password from the settings
  are used.
* **`end_process(process)`**: stops `run.py` and the game — `taskkill /T /F` on Windows; on Linux, `SIGTERM` to the
  process group, then `SIGKILL` after 10 s.
* environment restoration for launched system programs, log scrubbing and bug reports.

`VERSION` lives in the core module, and both package scripts read the AppImage/zip version from there. Keeping one
copy of the environment list is what stops the two launchers drifting apart as game settings change.

The Linux package no longer needs `python3-tk` or Pillow's `PIL._tkinter_finder` hidden import; Pillow is still used
by `packaging/linux/package.sh` for the AppImage icon. CI's frozen-script check now imports Qt and asserts that Tk and
Pillow are absent. The Windows package has no Qt dependency.

---

## 5. Linux build and packaging fixes

### 5.1 AppImage: `ldd` can name the dynamic loader by path

`packaging/linux/package.sh` collects the game's non-system libraries into `lib/`. The loop assumed `ldd` always
prints a bare soname, but it can also list the loader as `/lib64/ld-linux-x86-64.so.2`. `cp` would then try to write
`$dest/lib//lib64/ld-linux-x86-64.so.2`, whose directory does not exist, stopping the build. Fixed with:

```sh
[[ $name == */* ]] && continue  # ldd can name the loader by path (/lib64/ld-linux-x86-64.so.2)
```

### 5.2 The Linux package uses the Qt launcher

`packaging/linux/package.sh` now installs `PySide6-Essentials` (QtCore, QtGui, QtWidgets; not the whole PySide6),
adds `--paths "$PWD/launcher"` and freezes `launcher/lbppc_qt.py` as the entry point. `packaging/linux/AppRun`
exports the Wayland/X11 platform fallback and describes a Qt window.

### 5.3 glibc baseline

Release AppImages should be built on Ubuntu 24.04 (glibc 2.39), the oldest system the AppImage targets. Building on
a rolling-release distribution can make the AppImage require a much newer glibc; the Docker environment below
avoids that.

### 5.4 Ubuntu 24.04 Docker build environment

`packaging/linux/Dockerfile` reproduces `.github/workflows/linux-appimage.yml` on Ubuntu 24.04: the same packages,
GCC 14, the same from-source Vulkan headers/VMA/glslang/SDL3/FFmpeg versions, and appimagetool 1.9.1. It is a Linux
build image only. The checkout is mounted at `/src` rather than copied, so the build's `out/` and `dist/` stay on the
host and later builds reuse them. `packaging/linux/README.md` has the full instructions:

```sh
docker build -t lbppc-linux --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" \
  -f packaging/linux/Dockerfile packaging/linux
docker run --rm -v "$PWD":/src lbppc-linux \
  bash -lc 'bash build.sh && bash packaging/linux/package.sh'
```

---

## 6. Known gaps and follow-ups

1. **`--play` shows the launcher for a moment.** The Tk launcher withdraws its window in `--play` mode; the Qt
   launcher starts the game after 500 ms but leaves the window visible. Revealing it on errors is useful, but it
   should start hidden to match the Tk shortcut.
2. **No first-run beta notice/setup prompt in the Qt launcher.** The Tk launcher shows `BetaNotice` and then offers
   the setup wizard on first start. The Qt launcher has the setup wizard behind **Set up game files...** but does
   not prompt automatically.
3. **Mouse-to-joystick settings are not in the Qt Controls page.** `MOUSE_SUPPORTED` is currently `False`, so the
   Tk page hides them too; if support returns, the Qt page needs the mouse mode, speed, vertical scale and invert
   controls.
4. **FSR 3.1 frame generation can turn itself off at 4K on low-VRAM GPUs.** The render targets and FSR 3's internal
   images do not fit; use a lower render resolution on such cards. The logs behind this finding are in
   [linux-testing.md](linux-testing.md).
5. **New translations.** The Qt launcher's texts are in `launcher/lang/strings.json`; the
   `launcher/lang/source/*.txt` files still need translations for them.

---

## 7. Files changed

Working tree against `cb4cbe7` (0.1.5):

```
 .github/workflows/linux-appimage.yml |    8 +-
 README.md                            |    5 +-
 docs/linux-changes.md                |  347 +++++++
 docs/linux-testing.md                |  232 +++++
 docs/release-checklist.md            |    4 +-
 launcher/i18n.py                     |    6 +-
 launcher/lang/strings.json           |   28 +-
 launcher/lbppc_core.py               |  510 +++++++++
 launcher/lbppc_launcher.py           |  507 +--------
 launcher/lbppc_qt.py                 | 2042 ++++++++++++++++++++++++++++++++++
 packaging/linux/AppRun               |   12 +-
 packaging/linux/Dockerfile           |  119 +++
 packaging/linux/README.md            |   76 ++
 packaging/linux/package.sh           |   22 +-
 packaging/windows/package.sh         |    6 +-
 src/runtime_extra.c                  |   88 +-
 16 files changed, 3490 insertions(+), 522 deletions(-)
```

This checkout also has follow-up fixes:

* `src/runtime_file.c`: read-only game paths resolve ignoring case, and `LBPPC_FILE_TRACE` logs path lookups and,
  optionally, reads.
* `tests/test_file_edits.c`: the case-insensitive file-name and folder-name cases.
* `gpu/shadps4/core/libraries/videoout/`: `sceVideoOutSetWindowModeMargins` validates and records the margins
  instead of logging the call as stubbed. Presentation stays full-screen, so the picture does not change.
* `gpu/shadps4/video_core/renderer_vulkan/vk_runtime.cpp`: only warn when a copy drops source layers; a destination
  with more layers is the normal case and is logged at debug level.
* `docs/troubleshooting.md`: how to read the file trace, the case-insensitive match note and the graphics messages.

The `gpu/third_party/fsr-vulkan` submodule shows as dirty only because `build.sh` applies the tracked patches in
`gpu/patches/fsr-vulkan/` to its working tree; there are no new submodule changes in this work.

Suggested commit split:

1. `runtime: implement guest fibers on POSIX with ucontext`
2. `launcher: extract toolkit-free shared code into lbppc_core.py`
3. `launcher: add a native Qt launcher for Linux`
4. `packaging: build the Qt launcher into the Linux AppImage`
5. `packaging: add an Ubuntu 24.04 Docker build environment`
6. `i18n: extract the Qt launcher's texts`
7. `runtime: resolve read-only game paths ignoring case`
8. `runtime: add LBPPC_FILE_TRACE file access tracing`
9. `videoout: record window mode margins`
10. `vulkan: quiet expected copy-layer coercion`
11. `docs: describe the Linux support changes and the debugging record`
