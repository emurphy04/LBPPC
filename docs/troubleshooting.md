# Troubleshooting and reading the log

## Reporting a problem

1. Play until the problem happens, then close the game.
2. In the launcher, click **Make a report**. It saves `out/reports/LBPPC-report-<date>.zip` and opens
   the folder.
3. Attach the zip to your report and say what you were doing, which level, and roughly when it went
   wrong.

The report holds the last game logs, `lbppc.ini`, `input.ini` and the launcher's settings, and no
game files.

The launcher keeps the last ten logs in `out/logs/` (`run-<date>-<time>.log`). The current one is
`out/last_run.log`.

Logs are safe to share as they are, report or not: personal details are left out while the log is
written. In their place you'll see labels, the same one every time for the same thing:

| Label | Stands for |
|---|---|
| `<me>` | your online name |
| `<player-1>`, `<player-2>`, ... | other players' names |
| `<ip-1>`, `<ip-2>`, ... | internet (IP) addresses: yours, other players', the server's |
| `<home>`, `<user>`, `<pc>` | your Windows user folder, user name and PC name |
| `<account>`, `<password>` | account numbers, and your password if it ever showed up |

Addresses on your own network (192.168..., 10...) stay: they say nothing about who you are. Logs
from before 0.1.1 are cleaned the first time the new launcher starts.

## The log, line by line

The first line says which LBPPC build ran on which PC:

```
System: LBPPC 0.0.1; Windows 11 (10.0.26200); AMD Ryzen 7 7800X3D 8-Core Processor, 8 threads; 31 GB RAM
GPU: NVIDIA GeForce RTX 4090, 24138 MiB VRAM, driver NVIDIA 617.14.0.0 (617.14), Vulkan 1.4.351
```

Every 5 seconds the game writes how it is running:

| Line | What it says |
|---|---|
| `Frame stats: 30.0 FPS, worst frame 37.8 ms` | The game's frame rate (it runs at 30; frame generation shows 60) and the longest frame. `shader/pipeline compiles` above 0 means new shaders were built, which can cause a short hitch the first time you see something. |
| `Frame pacing: median 33.33 ms ... p99 ...` | How even the frames are. `frames over 1.5x median` counts visible stutters. |
| `Latency: 23.9 ms from the game starting a frame to its present` | The delay between the game reading your controller and the picture being shown, and where it went. |
| `Input: 83 pad reads/s` | How often the controller is read. |
| `Memory: VRAM ...` | Graphics memory in use. |

For every frame longer than 40 ms there are two more lines:

```
Slow frame: 62.2 ms after the last one; ...; game 28.4, GPU thread 6.3, waiting to present 21.2, present 0.2 ms
Stall: 62.2 ms frame; GPU thread on CPU 15.6 ms, waited for the game 59.4, recorder 0.0, host copies 0.2, ...
```

`Slow frame` splits the frame into its stages:

- **game**: the game's own work. A large value with "waited for the game" in the `Stall` line means
  the game itself was busy (loading, or a heavy moment). Loading screens normally run slower.
- **GPU thread**: translating the game's graphics commands. Large values point at the port; the
  `Stall` lines below it say what that thread did: textures created (`image create`), uploads
  (`MB image uploads`), memory protection changes (`protect`) and waits for the graphics card
  (`GPU ticks`, with `longest GPU tick wait:` naming the code that waited).
- **waiting to present / present**: the frame waiting for its turn on screen. About 20 ms of
  waiting is normal with frame generation.

A single long frame when a level starts is normal: the level's textures are uploaded at once.

## Common messages

| Message | Meaning |
|---|---|
| `Game exited with 0xC0000005` | The game crashed (access violation). Please send a report. |
| `Watchdog: N s passed; threads:` | A test run reached its time limit (`--timeout`), or the game hung. |
| `(STUBBED) called` | The game used a PS4 function the port does not implement yet. One-off lines are harmless; send a report if one repeats constantly. |
| `DLSS: ... missing` / `frame generation uses FSR 3.1` | The DLSS files are not installed; FSR 3.1 frame generation is used instead. |
| `Quit Game: not added (...)` | The pause menu's Quit Game couldn't be added to this copy of the game. Everything else works; close the window to quit. |
| `Online: refused a connection from <name>` | Someone who isn't your friend tried to join (see [join protection](online.md#join-protection)). |
| `Sync: DIFFERENCE 1 of 3` | Online, the two players' worlds didn't match at one check (every 2 seconds). The game carries on; `worlds match again` means it was only a moment. |
| `Sync: worlds still differ after 3 checks` | The worlds have really split, and the game says it diverged. Please send reports from **both** players, made right after it happens. |
| `Image sync in the last 30 s: ...` | Normal. The game wrote over memory that also holds pictures, and the pictures were updated to match, as on the PS4. |
| `Clear: target ... dropped (blended: check this one)` | Only appears with the image sync switched off for testing. If effects build up or leave trails, send a report. |
| `Clear warning: shader ...` | The same: only worth a look if something draws wrongly. |

## Debug page switches

The launcher's Debug page has diagnostics that add to the log. Only switch one on when asked to: they make logs
larger.

| Switch | Adds |
|---|---|
| Frame log (on) | The 5-second lines above. |
| Slow-frame profiler | `Profile:` and `Threads:` lines: where the game waited in each slow frame, CPU per thread. |
| Player positions | Every player's position and buttons at every step, and online the game's network messages (`Net:` lines). Large. |
| Skip GPU readbacks (on) | Not a log: faster busy levels. Turn it off if something flickers or draws wrongly. |
| Optimization switches | Reads `user/toggles.txt` while the game runs; each bit turns an optimization off. |
| Friends menu log | `Friends (menu):` lines while online: what the game's Friends page holds. |

## Tips

- **Quitting:** the pause menu has **Quit Game**; press it twice. It waits for a save in progress to finish.
- **Display: Borderless.** In windowed mode at your screen's own size, Windows shrinks the window
  slightly and the picture is scaled down.
- **The first time you see something new** the game may hitch briefly while its shaders are built;
  the next time they come from the cache.
