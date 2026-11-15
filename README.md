# LBPPC

<a href="https://recomplauncher.com/game/lbppc"><picture><source media="(prefers-color-scheme: dark)" srcset="https://recomplauncher.com/assets/press/launcher/badge-light.svg"><img alt="Get it on RecompLauncher" src="https://recomplauncher.com/assets/press/launcher/badge-dark.svg" height="48"></picture></a>

**LittleBigPlanet 3, running natively on PC, with its online world back.**

LBPPC runs the PS4 game's own code directly on your PC's processor. It is a native port, not an emulator. The
community servers bring back creating, sharing and playing levels together, which ended when the original servers
shut down.

**Status:** 0.1.3, a public beta. It supports LittleBigPlanet 3 US (CUSA00473) and European (CUSA00063), version
1.28. You need your own copy and its 1.28 update, extracted to folders on your PC.

[Download and guide](https://lbppc.com/download) · [Discord](https://discord.gg/YqfBQCU4vc) ·
[Release notes](docs/releases/0.1.1.md)

## What you get

**The game, on PC**
- **60 FPS** with frame generation: DLSS on GeForce RTX 40 and 50 series cards, FSR 3.1 on any other. The game still
  steps 30 times a second, as on PS4, so physics and timing feel the same.
- **720p to 4K, and ultrawide.** The menus lay themselves out for wide screens, online too. Movies and loading screens
  stay 16:9.
- **FSR 1 upscaling** with adjustable sharpening, for playing below your screen's resolution.
- **NVIDIA Reflex and AMD Anti-Lag 2** for lower input latency.
- **Controllers out of the box**, motion sensors included. Keyboard controls can be rebound in the launcher.
- **Quit Game** in the pause menu.

**Online**
- **One account** for everything, created in the launcher. The server is `play.lbppc.com`.
- **Ten million archived levels.** The community levels saved from LittleBigPlanet's original servers can be played
  again.
- **Online co-op** with your friends, through a relay when your connections can't reach each other directly.
- **Join protection.** Only your friends can join your game. This closes the "force join" exploit that ended
  LittleBigPlanet's community servers.
- **Friends in the launcher:** send and accept requests, and see who's online. Friends playing LBPPC show in the
  Pod's friends list too.

**The launcher**
- Settings for resolution, frame generation, upscaling, controls and online play, all in one window.
- **Make a report** packs everything a bug report needs into one zip, without your password or Windows user name.
- **An in-game menu** (Insert, or L3 + R3 on a controller): the frame rate counter and other overlays, and frame
  generation, without leaving the game.

## Getting started

You need 64-bit Windows 10 (1903 or later) or Windows 11, a graphics card with Vulkan support, and your own dump of
LittleBigPlanet 3 (US CUSA00473 or European CUSA00063) with its 1.28 update.

1. Unzip a release anywhere except Program Files, for example `C:\Games\LBPPC`.
2. Start `LBPPC.exe`. If Windows SmartScreen warns about a new app, click **More info**, then **Run anyway**.
3. The launcher opens **Set up your game**: choose your game's folder and its 1.28 update's folder (both must be
   extracted), and LBPPC puts the update into the game folder for you. Tick **Delete the update files when done** to
   move them instead of copying. Already have one folder at version 1.28? Just choose it. Then press **Play**.
   The first start prepares the game, which takes about a minute.
   The first time you see something new, it may hitch once while its shaders are built.
4. To play online, choose a name and password under Online, press **Create account**, tick **Play online on the LBPPC
   server**, and add your friends by their online names.

The full guide is at [lbppc.com/getting-started](https://lbppc.com/getting-started), and online play is covered in
[docs/online.md](docs/online.md).

### Controls

- **Controller:** works out of the box.
- **Keyboard:** the launcher's **Controls** page sets keys and controller buttons. It writes `input.ini` next to
  `LBPPC.exe`, and F8 reloads it in game.
- **Motion:** a DualShock 4, DualSense or Switch Pro controller tilts Sackboy's head as on PS4. Without one, the
  number pad tilts it (4 left, 6 right, 8 forward, 2 back).
- **Mouse:** off in 0.1.0. Using the mouse as the touchpad, for touch Create Mode, is planned.

## Known issues

- **Online co-op is new.** If the game says the players' worlds diverged, make a report on both PCs.
- **Some effects or materials draw wrongly in some levels.**
- **With a keyboard, the game shows PlayStation buttons.** The launcher's Controls page shows which key is which.
- **An archived level's first load can be slow**, while the server fetches it for the first time.

## What's coming

Being worked on for later updates, with no dates yet:

- **Native 60 and 120 FPS:** the game itself running faster, rather than frame generation filling in between 30.
- **Better upscaling:** DLSS and FSR temporal upscaling fitted to LBP3.
- **Mouse and keyboard:** the mouse as the PS4's touchpad (for Create mode), and keyboard keys shown in place of
  PlayStation buttons.
- **Linux and Steam Deck**, once tested on more hardware.

## Found a problem?

1. Close the game after the problem happens.
2. In the launcher, click **Make a report**.
3. Attach the zip to a [GitHub issue](../../issues) or post it on Discord. Say what you were doing and which level you
   were in.

[docs/troubleshooting.md](docs/troubleshooting.md) explains what the log says.

## You need your own copy of the game

This repository contains no game files and never will. To play, dump LittleBigPlanet 3 from your own PS4. Please
don't post game files in issues or pull requests, and don't ask for them.

## How it works

- **The game's code runs natively.** The PS4 and the PC share the same processor architecture (x86-64), so the game's
  executable runs on the CPU as it is, with no translation.
- **A runtime stands in for the PS4 system.** It provides the system libraries the game calls: files, threads,
  memory, audio, input, save data and the network.
- **A graphics layer translates PS4 GPU work to Vulkan.** It is derived from shadPS4.
- **Online play goes to community servers.**
  - [Refresh](https://github.com/LittleBigRefresh/Refresh) handles levels, profiles and photos.
  - [shadNet](https://github.com/shadps4-emu/shadNet), with LBPPC's patch, handles sign-in, friends, presence, the
    co-op relay and invites.
- **Online stays in sync.** Both players' worlds step in lockstep, so CPU-specific maths shortcuts are replaced with
  exact versions that give the same results on every PC.
- **Small game changes are made in memory.** Changes such as Quit Game edit the game's files as they're read; the
  files on disk are never touched.

## Building

Windows, with MSYS2 CLANG64: `./build.sh`. The packages it needs are listed in
[packaging/windows/README.md](packaging/windows/README.md). The launcher is Python; `run.py` starts the game from a
checkout.

| Folder | What's there |
|---|---|
| `src/` | The runtime: loads and runs the game's code and stands in for the PS4 system libraries |
| `gpu/` | The graphics layer (PS4 GPU commands and shaders to Vulkan) and the online layer |
| `launcher/` | The launcher: settings, controls, accounts, friends and reports |
| `scripts/` | Prepares the game before a start: links its modules and makes the in-memory game changes |
| `server/` | Building and running the community servers, with LBPPC's shadNet patch |
| `packaging/` | Release packaging |
| `tools/` | Development tools |
| `tests/` | Tests of the runtime, graphics layer and input |
| `docs/` | Player guides, research notes and design documents |

Useful reading: [docs/troubleshooting.md](docs/troubleshooting.md) (the log, line by line),
[docs/rendering-fixes.md](docs/rendering-fixes.md) (how graphics bugs were tracked down) and
[docs/release-checklist.md](docs/release-checklist.md).

## Credits

LBPPC is built on the work of the Bloodborne PC port and the projects behind it. Its runtime and graphics layer were
imported from version 1.9.5 of the [Bloodborne PC port](https://github.com/Supermedo/bloodborne_pc), at commit
`ff42e28`, where the full history is.

- [bbport](https://github.com/deadinside28/bloodborne_pc), the native port of Bloodborne by deadinside28, and the [Bloodborne PC port](https://github.com/Supermedo/bloodborne_pc) by Supermedo, nick24051998 and their contributors.
- [shadPS4](https://github.com/shadps4-emu/shadPS4), whose renderer and network, NP and shadNet code are at the heart of the graphics and online layers, and the [shadNet](https://github.com/shadps4-emu/shadNet) server by the shadPS4 team.
- **Awaken** ([BloodBorneButPc](https://github.com/Awakenchan/BloodBorneButPc)), who put together the online code, and Wozzardman's [shadp2p](https://github.com/Wozzardman/shadp2p), which the first online version was built on.
- **fixdag** and **bashiku**, for the low-VRAM profile.
- [Refresh](https://github.com/LittleBigRefresh/Refresh), the community server for levels and profiles.

Contributors to the Bloodborne PC port's history: Aljoscha-Ide, 𝗔ᴍʙʀᴏsᴇ, andrecabralgit, Awaken, Axel, ayozetr, Berleis, DarkSpartan, deadinside28, devilismyfriend, DUDUYG, Eric DaRosa, FIocker, fliperama86, gStone, JackRushante, Jgamrot, Joao Ramos, João Pedro Valadares, lewiwiii, Macypor, Maurdekye, Mitra, nick24051998, noatdk, oat018, parsifal295, Pirvondal, RafaelHGOliveira, rattensuppe, Sergey Leonov, Stefano Scalfari, Supermedo, T0ug and yshtonytc.

Also used: [FSR-Vulkan](https://github.com/FireBurn/FSR-Vulkan) and the AMD FidelityFX SDK, the DLSS bridge adapted from [IFreemz/shadPS4-Bloodborne-DLSS-FSR](https://github.com/IFreemz/shadPS4-Bloodborne-DLSS-FSR), the [NVIDIA DLSS SDK](https://github.com/NVIDIA/DLSS), [LibAtrac9](https://github.com/Thealexbarney/LibAtrac9), [SDL3](https://github.com/libsdl-org/SDL), [FFmpeg](https://ffmpeg.org), [Dear ImGui](https://github.com/ocornut/imgui), [sirit](https://github.com/shadps4-emu/sirit), [magic_enum](https://github.com/Neargye/magic_enum), [miniz](https://github.com/richgel999/miniz), [xbyak](https://github.com/herumi/xbyak), [wepoll](https://github.com/piscisaureus/wepoll), [cpp-httplib](https://github.com/yhirose/cpp-httplib), [Protocol Buffers](https://protobuf.dev), [OpenSSL](https://www.openssl.org), [miniupnpc](https://miniupnp.tuxfamily.org), [JSON for Modern C++](https://github.com/nlohmann/json), [Vulkan Memory Allocator](https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator), [MSYS2](https://www.msys2.org), [LLVM](https://llvm.org), [PyInstaller](https://pyinstaller.org) and [Pillow](https://python-pillow.org).

## Licence

GPL-3.0-or-later; see [LICENSE](LICENSE). Code taken from shadPS4 and the Bloodborne PC port keeps its own notices.

This is an unofficial fan project, not affiliated with or endorsed by Sony Interactive Entertainment or Sumo Digital.
LittleBigPlanet is a trademark of its owners.
