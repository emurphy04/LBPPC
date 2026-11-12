# LBPPC on Windows

The port builds and runs natively on 64-bit Windows 10 (1903+) / 11.

## Build

Install [MSYS2](https://www.msys2.org) (`winget install MSYS2.MSYS2`), open the
**MSYS2 CLANG64** shell and install the toolchain and libraries:

```bash
pacman -S --needed git patch \
  mingw-w64-clang-x86_64-{toolchain,cmake,ninja,pkgconf,python} \
  mingw-w64-clang-x86_64-{vulkan-headers,vulkan-loader,vulkan-memory-allocator,glslang} \
  mingw-w64-clang-x86_64-{sdl3,fmt,boost,robin-map,xxhash,ffmpeg,zydis} \
  mingw-w64-clang-x86_64-{spirv-tools,spirv-headers,spirv-cross} \
  mingw-w64-clang-x86_64-{protobuf,openssl,zlib,miniupnpc,nlohmann-json}
```

magic_enum, miniz and xbyak (not packaged by MSYS2) are submodules under `gpu/third_party/`.
Then, in the repository:

```bash
git submodule update --init --recursive
bash build.sh                      # out/LBPPC-Game.exe, out/bb-gpu-capabilities.exe
python run.py F:/path/to/CUSA00473 # the game, without the launcher
python launcher/lbppc_launcher.py  # the launcher
bash packaging/windows/package.sh  # dist/LBPPC-<version>-windows.zip
```

From a source tree `run.py` adds `C:\msys64\clang64\bin` to `PATH` for the DLLs. The package
freezes the launcher with PyInstaller (a private venv in `out/pyenv`, from a python.org Python)
into `LBPPC.exe`, which also runs `run.py` and the preparation scripts (`--script`): players need
no Python.

DLSS (optional): `packaging/windows/import_dlss.py` puts `bbport_dlss.dll` (the bridge in
`gpu/dlss_bridge`, the only code that uses the NVIDIA SDK) and NVIDIA's `nvngx_dlss.dll` /
`nvngx_dlssg.dll` next to `out/LBPPC-Game.exe`; `build_dlss.sh` builds the bridge with MSVC 2022
instead. `package.sh` ships them when present. `BB_DLSS=0` turns DLSS off.

## What is different from Linux

| Linux | Windows |
|---|---|
| `mov rax, fs:[0]` rewritten to `gs:[0]`, GS base set with `arch_prctl` | GS is the TEB and cannot move. The loader rewrites the displacement to `gs:[0x1480 + 8*slot]` (the TEB's `TlsSlots[slot]`, same 9-byte instruction) and each guest thread keeps its TCB in that TLS slot. The slot is claimed first thing in `main` (Vulkan drivers take many). |
| One memfd for direct + flexible memory, `mmap(MAP_FIXED)` views | A pagefile-backed section; the PS4 range 64 GiB–1 TiB is reserved as one placeholder and views replace placeholders (`VirtualAlloc2`/`MapViewOfFile3`, as in shadPS4). Partial unmaps split views and restore page protections (GPU tracking). |
| `-no-pie`, heap below 1 TiB | `--disable-high-entropy-va`: heaps and thread stacks stay low and out of the PS4 range. |
| SIGSEGV handler, `sigsetjmp` recovery | Vectored exception handler; `bb_setjmp`/`bb_longjmp` (`src/compat_win.c`) restore all Win64 callee-saved registers without SEH unwinding (guest frames have no unwind data; clang's `__builtin_longjmp` restores a wrong frame pointer on Win64). |
| Guest thread stacks from `runtime_low_map` | Windows allocates them, fully committed (`pthread_attr_setstacksize`): guest code has no stack probes and would skip the guard page. The main thread stack is 16 MiB committed. |
| glibc `nanosleep`, monotonic condition variables | High-resolution waitable timers (`compat_sleep_ns`; winpthreads' sleep rounds to 15.6 ms); semaphore deadlines use `CLOCK_REALTIME` (winpthreads rejects monotonic condition variables). |
| `libbbgpu.so` | `libbbgpu.a`, linked into `LBPPC-Game.exe` by CMake (`gpu/CMakeLists.txt`), with a manifest for UTF-8 paths and long paths. |

Not ported: userfaultfd tracking (`BB_UFFD`), PGO (GCC flags) and the `tests/` programs (several
use Linux-only APIs).
