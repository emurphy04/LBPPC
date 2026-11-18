# Linux port: local testing and debugging record

This is the supporting record for [linux-changes.md](linux-changes.md). It keeps the details that only apply to the
machine used to develop the Linux port: the machine itself, the logs seen there, measured VRAM use, local artifact
hashes and the build errors that were hit while preparing the reproducible build. These are observations on one PC,
not requirements or recommended settings for every Linux system.

---

## 1. Test machine

| Item | Value |
|---|---|
| Distribution | CachyOS |
| Kernel | `7.3.3-1-cachyos` |
| Desktop | KDE Plasma on Wayland |
| CPU | Intel Core i5-9500 |
| RAM | 16 GB |
| GPU | NVIDIA GeForce RTX 2060, 6144 MiB VRAM |
| Driver | NVIDIA 620.70.06 (reported by Vulkan as `620.70.6.0`) |
| Vulkan | 1.4.357 |

The playable configuration used for testing was 2560x1440, FSR 3.1 frame generation, medium sharpening, low latency
on and background shader builds on. With FSR 3 enabled the game rendered 30 simulation steps per second and showed
60 FPS.

---

## 2. Artifacts built on this machine

| Artifact | Size | SHA-256 | Environment |
|---|---:|---|---|
| `dist/LBPPC-0.1.4-linux-x86_64.AppImage` | 166,369,784 bytes | `e108fe91e8eef0c56b43d049f436404938203177c559d466584984268996666d` | Built directly on CachyOS with glibc 2.44. Local test build only. |
| `dist/LBPPC-0.1.5-linux-x86_64.AppImage` (before the shared-core refactor) | 74,275,320 bytes | `cf740a14ec120e6885d69f8c2e875ec4df99c187012e2e7c02eb2ea75ac20028` | Built in the Ubuntu 24.04 Docker image from `packaging/linux/Dockerfile`. |
| `dist/LBPPC-0.1.5-linux-x86_64.AppImage` (after the shared-core refactor) | 63,801,848 bytes | `42eaecb617b90e7ec9d6ae7f775c101c3c1ac716c5aa50ef72838f1b098aa78d` | Same Ubuntu 24.04 Docker image; contains `lbppc_qt.py` + `lbppc_core.py` with no Tk launcher. |
| `dist/LBPPC-0.1.5-linux-x86_64.AppImage` (with case-insensitive game paths) | 63,810,040 bytes | `93f4810225969a0d28ea1faf7f7e55f800b19e43d4d159045903067713ca0b07` | Same Docker image; contains the `Chunk1_001.farc` path fix. |

The build commands were:

```sh
docker build -t lbppc-linux --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" \
  -f packaging/linux/Dockerfile packaging/linux
docker run --rm -v "$PWD":/src lbppc-linux \
  bash -lc 'bash build.sh && bash packaging/linux/package.sh'
```

The host's existing `out/` from the 0.1.4 build was moved aside to `/tmp/opencode/LBPPC-out-host-0.1.4` so the
container builds began from a clean `out/`. The checkout was mounted at `/src`, so `out/` and `dist/` stayed on the
host rather than being written into the image.

`appimagetool` reported the following for the 0.1.5 builds:

```
Needs glibc: GLIBC_2.38
C++ library needed: GLIBCXX_3.4.32
```

That is within Ubuntu 24.04's glibc 2.39. The older CachyOS build needed `GLIBC_2.44` and would not run on older
distributions. Both builds also printed `No shader list in this package`; this is a packaging-input note, not a
runtime failure.

---

## 3. Runtime debugging

### 3.1 Fibers

Before the POSIX fiber implementation, every run that reached a fiber stopped with:

```
STOP: fibers are implemented on Windows only
```

The process exited with code 21. This message appeared five times in the logs kept from this machine.

After the fix:

* unattended 720p and 1080p runs reached gameplay;
* the runs held 30 FPS with roughly 900–1500 draws per frame;
* no pipeline failures or NVVM/driver faults appeared;
* real gameplay was confirmed on the test machine, including getting in and playing.

### 3.2 FSR 3.1 at 4K on a 6 GB card

At 3840x2160 the log showed:

```
Frame generation: FSR 3.1, 2x
FSR3.1.6 shared image allocation/bind failed: format=98 bits=3
FSR3.1.6 FI/OF public context failed at allocate dilated motion vectors (FFX 2147483655)
Frame generation: FSR 3.1 frame generation context creation failed; active=off, selected=off
Frame generation: off, its memory freed
```

At 2560x1440 the same build succeeded:

```
Frame generation: FSR 3.1 2259x1270 (depth and motion 2560x1440), 165.9 MB
```

At the 4K failure the log also showed:

```
Texture cache: VRAM budget 2911 MiB; marks: collect 0, pressure 1746, critical 3072 MiB
VRAM: 198 MiB free of 2911 MiB (2712 MiB in use), warned the player to save
```

Only about 200 MiB was free when FSR 3 tried to create its context. The 1440p context needs about 166 MB, and 4K
needs more; the shared-image allocation/bind fails first, then context creation fails while allocating the dilated
motion vectors. This is memory pressure, not a wrong-format bug in the game's frame generation.

The workaround used on this machine was 2560x1440 with desktop applications closed. Keeping them closed freed about
1–2 GB of VRAM and made the 1440p run noticeably more stable.

### 3.3 VRAM budget notes

These notes are from the 6 GB card and are deliberately kept out of the universal review document.

The driver and cache reported:

```
GPU: NVIDIA GeForce RTX 2060, 6144 MiB VRAM, driver NVIDIA 620.70.6.0 (620.70.06), Vulkan 1.4.357
Texture cache: VRAM budget 2900 MiB; marks: collect 0, pressure 1740, critical 3072 MiB
VRAM: 100 MiB free of 2900 MiB (2800 MiB in use), warned the player to save
```

The budget is roughly the sum of the driver's `heapBudget` entries minus `min(total / 8, 1 GB)`. On this machine it
was about 2900 MiB; it comes from the driver's heap budgets, not the chosen render resolution. The warning is
conservative by design: `BB_VRAM_WARN_MB` defaults to 1.5 GB below the limit and `BB_GC_BUDGET_MB` can force a fixed
budget for testing. Living slightly above 2900 MiB is therefore expected and is not itself an error.

Observed usage:

* **4K:** about 3.3 GB, using part of the 512 MiB reserve; FPS was GPU- and VRAM-bound.
* **1440p:** about 3.0 GB, 30 simulation steps per second and 60 FPS displayed with FSR 3, with stable memory.

### 3.4 Game files and case-insensitive paths

The costume screen probed thousands of palette plans, and the `chunk1_001.farc` content was never used. The trace
showed LBP3 asking for `/app0/Chunk1_001.farc` (capital `C`) while the extracted file is `chunk1_001.farc`
(lowercase). The PS4's mounts ignore case; Linux does not, so the archive was never opened. The game then tried
4,152 `.plan` palette, costume and related plan files as loose files under `/temp0` first and `/app0` second, and
every probe missed. Those plans and `sackboy_stitch_diffuse.tex` were present in `chunk1_001.farc`: 4,153 of the
4,281 unique missing paths the log recorded.

The stat loop was not the multi-second stall. The 8,304 missing stat calls took about 14 ms on this btrfs volume, or
about 85 ms through the new fallback. The 3.0 s and 2.0 s frames immediately after the scan were Bink video startup.

The fix in `src/runtime_file.c` retries a missing read-only game path one component at a time, matching each name
against the real folder spelling and differing only by case. It applies to `/app0` and `/hostapp`, checks the update
folder first to keep update precedence, and leaves saves and other writable mounts on exact names.
`tests/test_file_edits.c` covers both a mismatched file name and a mismatched folder name.

After the fix, the trace and open succeeded:

```
Runtime: file trace: /app0/Chunk1_001.farc matched <home>/Documents/Games/LBP3/chunk1_001.farc (case differs)
Runtime: file trace: open /app0/Chunk1_001.farc -> <home>/Documents/Games/LBP3/chunk1_001.farc [game folder <home>/Documents/Games/LBP3]
```

The real file opened with a size of 3,344,395,907 bytes, and costume loading worked in the rebuilt AppImage. The
path fix is in `src/runtime_file.c`; `LBPPC_FILE_TRACE` and the trace output are described in
[troubleshooting.md](troubleshooting.md#tracing-file-access).

---

## 4. Launcher debugging

### 4.1 Why the Tk launcher was not used on Linux

These were the reasons a separate Qt launcher was written:

* On Wayland, Tk is an X11 application and needs XWayland. The old `AppRun` required `DISPLAY` even on a Wayland
  desktop, and fullscreen X11 modes can change the desktop resolution.
* `-transparentcolor` is Windows-only. On X11 it raises `TclError: bad attribute "-transparentcolor"`, so the
  notice/dialog windows (`BetaNotice`, `SetupWizard`, `UpdateNotice`, `SaveFileCard`) did not appear.
* Tk's `winfo_screenwidth()` / `winfo_screenheight()` span every monitor on X11. A local patch used `xrandr --query`
  to size the window for the primary monitor, but that was a Tk-side fix for a launcher Windows still uses.
* The "mouse off" warning was appended to the log before `clear_log()`, so it was cleared immediately and never
  shown. The warning ordering was fixed while extracting the shared `game_environment()` code.

The Linux fixes were then removed from the Tk launcher: `launcher/ui_scale.py` is unchanged from upstream and the
`-transparentcolor` call is again unconditional. Linux uses `launcher/lbppc_qt.py`; Windows keeps the Tk launcher.

### 4.2 Offscreen Qt checks

The Qt launcher was exercised offscreen on this machine:

* all four tabs render;
* 7 setting combos, the save-file list, 60 control cells, 22 controller combos, 10 overlay switches and 7 diagnostics
  are present;
* the Friends page initialises;
* settings persist to a temporary `launcher.json`, with frame generation also written to a temporary `lbppc.ini`;
* F5 was bound to Cross and keypad `8` mapped to `kp8`; right-click/clear removed a binding and the result was
  written and re-read from `input.ini`;
* save-file create and rename worked against a temporary user folder, the names list updated, and Main could not be
  renamed;
* toggles wrote `show_fps=1` to `lbppc.ini` and `profile: true` to `launcher.json`;
* an offscreen launch stayed running for the whole timeout with no immediate crash.

---

## 5. Build and packaging issues hit locally

| Symptom | Cause | Fix |
|---|---|---|
| `STOP: fibers are implemented on Windows only`, exit 21 | Linux fiber path was a stub | POSIX `ucontext` implementation in `src/runtime_extra.c` |
| GCC `-Wclobbered` broke the `-Werror` build around `getcontext` | Returns-twice function marks locals clobbered | Scoped `#pragma GCC diagnostic ignored "-Wclobbered"` |
| AppImage build stopped while collecting the game's libraries | `ldd` listed the loader by path (`/lib64/ld-linux-x86-64.so.2`, resolving to `/usr/lib64/ld-linux-x86-64.so.2`), so `cp` tried to write through `lib/lib64` | Skip names containing `/` |
| Frozen package might miss Pillow's dynamically imported `PIL._tkinter_finder` | `PIL.features` imports it by name, which PyInstaller cannot see | Added as a hidden import while the Linux launcher still imported the Tk module; the shared-core refactor removed the need on Linux |
| Tk notice/dialog windows did not open on X11 | `-transparentcolor` is Windows-only and raises `TclError` | A local patch set it only on Windows; later removed because Linux uses Qt |
| Launcher sized for the whole multi-monitor desktop | Tk's screen size spans every monitor on X11 | A local patch queried `xrandr` for the primary monitor; later reverted because Linux uses Qt |
| AppRun required XWayland (`DISPLAY`) for the Tk launcher | The Tk launcher is an X11 application | Qt launcher with `wayland;xcb`; warn only when no display exists at all |
| AppImage built directly on CachyOS needed glibc 2.44 | Built with the host's newer glibc | Ubuntu 24.04 Docker build; the 0.1.5 artifact reports `Needs glibc: GLIBC_2.38` |
| `curl: (22) The requested URL returned error: 404` for `download//opt/appimagetool/...` | `ENV APPIMAGETOOL=/opt/appimagetool` shadowed the build argument holding the version | Renamed the argument to `APPIMAGETOOL_VERSION` |
| `docker: Error response from daemon: unable to find user builder: no matching entries in passwd file` | Ubuntu 24.04 already has UID/GID 1000 as `ubuntu`, so creating `builder` with those IDs failed | Create the user/group only when the IDs are unused; enter the container by numeric `USER $UID:$GID` |
| Linux package still pulled in Tk/Pillow `ImageTk` | `lbppc_qt.py` imported `lbppc_launcher.py` for shared code | Extracted `launcher/lbppc_core.py`; Linux freezes only `lbppc_qt.py`, Windows freezes only `lbppc_launcher.py` |
| Tk launcher's "mouse off" warning never appeared in the log | Warning appended before `clear_log()` cleared it | `game_environment()` collects warnings first; each launcher appends them after clearing the log |
| FSR 3 frame generation turns itself off at 4K | 4K render targets plus FSR 3 images exceed the 6 GB card's budget | Use 1440p on this card and keep desktop VRAM free; open limitation |

---

## 6. Verification performed

### Source and launcher

* `python3 -m py_compile launcher/lbppc_core.py launcher/lbppc_launcher.py launcher/lbppc_qt.py`
* `out/file-edits-test` and `out/file-mods-test` pass, including case-mismatched file names and folder names.
* `python3 launcher/i18n.py --extract`: 464 texts, 0 new after the merge.
* A stub-auth check of `core.game_environment()` produced `BB_SHADNET_PASSWORD=lbppc-token:abc` from a signed-in
  `lbppc.com` session, and the Tk module still reported `0.1.5` and `Play` after the extraction.
* The offscreen Qt checks listed in section 4.2.
* `python3 tests/test_launcher_input.py` has one known pre-existing failure:
  `mouse settings survive writing and reading`. `input_settings.MOUSE_SUPPORTED = False` deliberately drops mouse
  settings when the page saves. Every other check passes.

### Frozen AppImage

The CI-style checks were run against the extracted AppImage:

* `ldd` on `LBPPC-Game` and `libbbgpu.so`, with only the package's `lib/` on `LD_LIBRARY_PATH`, found every library;
  no `not found` entries and no `/opt/deps` paths;
* `AppRun --version` printed `0.1.5` (the older 0.1.4 artifact printed `0.1.4`);
* `--script check.py` ran under the frozen Python;
* the packed launcher module was extracted and checked for `Performance overlay`, `Rename / delete`, `Friends`,
  `Diagnostics` and `Import save`.

Before the shared-core refactor, the frozen script check reported Python 3.12.3, Tk 8.6 and Pillow 13.0.0, confirming
the problem the refactor was meant to remove. After the refactor the same check ran under Python 3.12.3 with Qt 6.12.0
and core 0.1.5, and asserted that neither `tkinter` nor `PIL` is importable in the frozen package. No Tk, `_tkinter`
or `lbppc_launcher` files were found in the extracted AppImage. The final artifact is recorded in section 2.

---

## 7. Open observations

1. `--play` still shows the Qt window for about 500 ms before the game starts; the Tk launcher withdrew its window.
2. The Qt launcher has the setup wizard behind **Set up game files...** but no first-run beta notice prompt.
3. Mouse-to-joystick settings remain hidden in both launchers while `MOUSE_SUPPORTED` is `False`.
4. FSR 3 at 4K remains a limitation on the 6 GB card; see section 3.2.
5. The Qt launcher's new texts are in `launcher/lang/strings.json`; `launcher/lang/source/*.txt` still needs
   translations.
