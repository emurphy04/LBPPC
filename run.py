#!/usr/bin/env python3
"""Start the game: read its eboot, link it with its bundled modules, run the game (LBPPC-Game).

usage: python run.py <game folder> [--timeout seconds] [more LBPPC-Game options]
The game folder is the player's own dump (eboot.bin, sce_module, sce_sys). Generated files go to out/,
saves to user/. The log goes to the console and out/last_run.log.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
OUT = HERE / 'out'
sys.path.insert(0, str(HERE / 'scripts'))
import prepare as dump  # noqa: E402  (param.sfo)
import log_privacy  # noqa: E402
import game_files  # noqa: E402
import game_setup  # noqa: E402
import game_view  # noqa: E402

# title ID: the version LBPPC's patches are made for. The EU 1.28 (CUSA00063) has the US 1.28's very eboot.bin.
SUPPORTED = {'CUSA00473': '01.28', 'CUSA00063': '01.28'}
MSYS2_BIN = Path(r'C:\msys64\clang64\bin')  # development builds: SDL3 and the other CLANG64 DLLs
RESTART_EXIT_CODE = 75  # the in-game menu's Apply and restart (probe.c runtime_restart)


def script(name, *args):
    # Packaged, sys.executable is LBPPC.exe (its Python frozen in), which runs scripts with --script.
    python = [sys.executable, '--script'] if getattr(sys, 'frozen', False) else [sys.executable]
    result = subprocess.run([*python, str(HERE / 'scripts' / name), *map(str, args)], stdin=subprocess.DEVNULL)
    if result.returncode:
        sys.exit(f'{name} failed ({result.returncode})')


def keep_previous_log(keep=10):
    # The last run's log moves to out/logs/run-<when it ended>.log, so a play session's log survives later starts.
    last = OUT / 'last_run.log'
    if not last.is_file():
        return
    logs = OUT / 'logs'
    logs.mkdir(exist_ok=True)
    when = time.strftime('%Y%m%d-%H%M%S', time.localtime(last.stat().st_mtime))
    try:
        last.replace(logs / f'run-{when}.log')
    except OSError:
        return
    for old in sorted(logs.glob('run-*.log'))[:-keep]:
        old.unlink(missing_ok=True)


def system_summary():
    """One line for the top of the log: the port's version and the PC it runs on (the GPU and its driver follow
    from the renderer), so a player's log says what it came from."""
    import platform
    cpu, ram = platform.processor() or '?', '?'
    if os.name == 'nt':
        import ctypes
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0')
            cpu = winreg.QueryValueEx(key, 'ProcessorNameString')[0].strip()
        except OSError:
            pass

        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + \
                       [(name, ctypes.c_ulonglong) for name in ('total', 'avail', 'page_total', 'page_avail',
                                                                'virtual_total', 'virtual_avail', 'extended')]
        status = MemoryStatus(length=ctypes.sizeof(MemoryStatus))
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            ram = f'{status.total / 2**30:.0f} GB'
    version = os.environ.get('LBPPC_VERSION', 'development build')
    return (f'System: LBPPC {version}; {platform.system()} {platform.release()} ({platform.version()}); {cpu}, '
            f'{os.cpu_count()} threads; {ram} RAM\n')


KEY_NAMES = {'space': 'SPACE', 'lshift': 'SHIFT', 'rshift': 'SHIFT', 'lctrl': 'CTRL', 'rctrl': 'CTRL',
             'lalt': 'ALT', 'ralt': 'ALT', 'enter': 'ENTER', 'backspace': 'BKSP', 'tab': 'TAB', 'escape': 'ESC',
             'up': 'UP', 'down': 'DOWN', 'left': 'LEFT', 'right': 'RIGHT', 'leftbutton': 'LMB',
             'rightbutton': 'RMB', 'middlebutton': 'MMB', 'sidebuttonback': 'M4', 'sidebuttonforward': 'M5'}
MOUSE_KEYS = {'leftbutton', 'rightbutton', 'middlebutton', 'sidebuttonback', 'sidebuttonforward'}


def key_labels(input_ini):
    """LBPPC_KEY_LABELS for the keyboard button prompts (key_prompts.cpp): each face button's first keyboard key
    in input.ini, or its first mouse button, as a short label: "cross=SPACE;square=E;..."."""
    keys = {}
    try:
        lines = input_ini.read_text(encoding='utf-8').splitlines()
    except OSError:
        return ''
    for line in lines:
        button, sep, key = (part.strip().lower() for part in line.partition('='))
        if not sep or button not in ('cross', 'circle', 'square', 'triangle') or key == button:
            continue
        keys.setdefault(button, []).append(key)
    labels = []
    for button, bound in keys.items():
        key = next((k for k in bound if k not in MOUSE_KEYS), bound[0])
        label = KEY_NAMES.get(key, key.upper().removeprefix('KP'))
        if label.isascii() and len(label) <= 5:
            labels.append(f'{button}={label}')
    return ';'.join(labels)


# The game keeps what it downloads (levels, their pictures and sounds) in its temporary data, and reads it from
# there again without downloading it. When the check on downloads changes (http.cpp, RefusedAsset), what an earlier
# build let in must not stay: the cache is cleared once per value of CACHE_EPOCH. The game rebuilds it, as it does
# on a PS4, where the system may clear temporary data at any time.
CACHE_EPOCH = '1'  # 1: 0.1.0, downloads checked for their hash and type


def clear_download_cache(user):
    marker = user / 'temp0' / 'lbppc-cache-epoch'
    try:
        if marker.read_text(encoding='ascii').strip() == CACHE_EPOCH:
            return
    except OSError:
        pass
    for drive in ('hdd0', 'hdd1'):
        for title in (user / 'temp0' / drive).glob('*'):
            for name in ('dl_orbis', 'temp_orbis'):
                cache = title / 'output' / name
                try:
                    cache.unlink(missing_ok=True)
                except OSError as error:  # in use or read-only: the next start tries again
                    print(f'LBPPC: could not clear {cache}: {error}', file=sys.stderr)
                    return
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(CACHE_EPOCH, encoding='ascii')
    print('LBPPC: download cache cleared (downloads are checked again)')


def main(argv):
    if not argv or argv[0].startswith('-'):
        sys.exit(__doc__)
    game = Path(argv[0]).resolve()
    argv = argv[:1] + list(argv[1:])
    # The update in a folder of its own, as shadPS4 keeps it: --update <folder>, or the CUSA…-UPDATE / -patch folder
    # beside the game. Its files are read over the game's (scripts/game_view.py, the runtime's --update); the game
    # folder itself is never changed.
    update = None
    if '--update' in argv:
        at = argv.index('--update')
        if at + 1 >= len(argv):
            sys.exit('LBPPC: --update needs a folder')
        update = Path(argv[at + 1]).resolve()
        del argv[at:at + 2]
    elif not os.environ.get('LBPPC_NO_UPDATE_FOLDER'):
        update = game_setup.find_update(game)
        update = game_setup.find_root(update) if update else None
    if update and not game_view.system_folder(update):
        sys.exit(f'LBPPC: {update} has no param.sfo (sce_sys or Sc0): it is not an extracted update')
    view = game_view.open_view(game, update)
    if not (view / 'eboot.bin').is_file():
        sys.exit(f'No eboot.bin in {game}')
    # The system files (param.sfo, npbind.dat...) where the extraction put them: an Sc0 folder is given to the runtime
    # as /app0/sce_sys (--system), read in place as everything else.
    system = game_view.system_folder(game, update)
    natural = {(root / 'sce_sys').resolve() for root in (game, update) if root}
    system_args = ('--system', str(system)) if system and system.resolve() not in natural else ()
    # Only the version the patches are made for runs: another one has different code at every patched spot.
    try:
        info = dump.sfo((system / 'param.sfo').read_bytes()) if system else {}
        base_system = game_view.system_folder(game) if update else None
        base_title = dump.sfo((base_system / 'param.sfo').read_bytes()).get('TITLE_ID') if base_system else None
    except (OSError, ValueError):
        info, base_title = {}, None
    if base_title and base_title != info.get('TITLE_ID'):
        sys.exit(f'LBPPC: the update ({info.get("TITLE_ID")}) is not for this game ({base_title}).')
    if update:
        print(f'LBPPC: update {update} read over the game')
    title, version = info.get('TITLE_ID', '?'), info.get('APP_VER', '?')
    if title.startswith('CUSA') and title not in SUPPORTED:
        sys.exit(f'LBPPC: this copy is {title}. LBPPC needs LittleBigPlanet 3 {" or ".join(SUPPORTED)}: '
                 'other versions won\'t run.')
    if title in SUPPORTED and version != SUPPORTED[title]:
        sys.exit(f'LBPPC: this copy of LittleBigPlanet 3 is version {version}; LBPPC needs version {SUPPORTED[title]}. '
                 'Choose the 1.28 update folder in the launcher (or pass --update <folder>).')
    # Every file of the game at the right size (scripts/game_files.py): a broken or incomplete copy is said in the log.
    try:
        files = game_files.check(game, update=update)
        files_line = 'Game files: ' + ('all present' if files.ok else files.summary(False) + ' ' +
                                       ', '.join((files.missing + files.wrong_size)[:10]))
    except (OSError, ValueError):
        files_line = 'Game files: not checked (no file list)'
    # Packaged releases keep the runtime in bin/; development builds in out/.
    # A release ships the game as bin/LBPPC-Game.exe; a checkout builds out/LBPPC-Game.exe (out/LBPPC-Game elsewhere).
    probe = HERE / 'bin' / 'LBPPC-Game.exe' if (HERE / 'bin' / 'LBPPC-Game.exe').is_file() else OUT / ('LBPPC-Game.exe' if os.name == 'nt' else 'LBPPC-Game')
    if not probe.is_file():
        sys.exit(f'{probe.relative_to(HERE).as_posix()} not found: build it with build.sh (MSYS2 CLANG64 on Windows)')
    # LBPPC_USER_DIR (tests): saves and settings somewhere other than user/.
    user = Path(os.environ['LBPPC_USER_DIR']) if os.environ.get('LBPPC_USER_DIR') else HERE / 'user'
    user.mkdir(exist_ok=True)
    clear_download_cache(user)
    with_update = ('--update', update) if update else ()
    script('prepare.py', game, '--out', OUT, *with_update)
    # The game's own modules (LBP3's FMOD plugins) are linked too; sceKernelLoadStartModule hands them out.
    plugins = sorted(p.name for p in (view / 'gamedata_orbis/spu').glob('*.prx')) if (view / 'gamedata_orbis/spu').is_dir() else []
    plugins = [f'gamedata_orbis/spu/{name}' for name in plugins]
    script('link_modules.py', game, '--out', OUT, *with_update, '--modules', 'libc.prx', 'libSceFios2.prx', *plugins)
    script('content_profile.py', game, '--out', OUT, *with_update)
    # Quit Game in the pause menu: the game files it changes are shown changed by the runtime (out/file_edits.bin).
    script('quit_menu.py', game, '--out', OUT, *with_update)
    env = dict(os.environ)
    if (OUT / 'file_edits.bin').is_file():
        env.setdefault('LBPPC_FILE_EDITS', str(OUT / 'file_edits.bin'))
    # The logo on the game window (launcher/assets in a checkout, assets/ in a release).
    for icon in (HERE / 'launcher' / 'assets' / 'logo-128.rgba', HERE / 'assets' / 'logo-128.rgba'):
        if icon.is_file():
            env.setdefault('LBPPC_ICON', str(icon))
            break
    # Bloodborne's parallel draw preparation (the draw pipeline and its workers) corrupts memory under LBP3
    # (random crashes in Rasterizer::RetireSubmission and heap frees); off until that is fixed.
    env.setdefault('BB_DRAW_PIPE', '0')
    env.setdefault('BB_PREP_WORKERS', '0')
    # The executable is loaded below 4 GiB, as the PS4 does: LBP3's SPU job code keeps pointers to the
    # executable's globals in 32 bits (a job counter at 0x8012a4d00 was truncated, its jobs' output grew
    # without bound, and every increment corrupted host memory).
    env.setdefault('LBPPC_LOW_BASE', '0x400000')
    # A compute shader of LBP3's overwrites render targets' memory as a storage buffer (clears, scratch memory
    # reused for other pictures). By default such a write reaches the images there as on the PS4: what the GPU
    # drew into them is written back to the buffer first, and they reload from it after (BufferWriteImageSync
    # in gpu/shim/bbport_toggles.h; fixed the orange flares and the scene turning into a streaky mesh on
    # pickups). This variable is the old way, used only with that switch's bit set in user/toggles.txt: the
    # images are kept and the write counts as a clear only for targets drawn additively next.
    env.setdefault('LBPPC_NO_BUFFER_IMAGE_INVALIDATE', '1')
    # LBP3 steps its world once per frame (30 per second), so 60 FPS comes from frame generation; its
    # camera, which frame generation needs, is recovered from the draws' matrices (lbp_camera.h).
    env.setdefault('LBPPC_LBP_CAMERA', '1')
    # Lower input latency: NVIDIA Reflex (AMD Anti-Lag 2 on AMD) with the 30 FPS limit kept before each game frame
    # instead of the game waiting on 60 Hz vblanks: from 84 to 29 ms between a frame's start and its present.
    env.setdefault('BB_LOW_LATENCY', 'on')
    env.setdefault('BB_VBLANK_HZ', '0')
    env.setdefault('BB_FPS_LIMIT', '30')
    # Beta: frame rate, pacing and where each frame's time went, every 5 s in the log (a few KB a minute), so a
    # player's slow level can be told apart (the game's own work, the GPU, shader compiles, stalls).
    env.setdefault('BB_FRAME_STATS', '1')
    # LBPPC_PROFILE=1 (Debug page: "Slow-frame profiler", off by default): where the game's main thread was during
    # each slow frame, and CPU per thread ("Profile:" and "Threads:" lines). Measured to cost nothing visible.
    # Guest writes into GPU-written memory skip the copy back (buffer_cache.cpp): faster in busy levels, nothing
    # found wrong in testing. Debug page: "Skip GPU readbacks".
    env.setdefault('LBPPC_WRITE_FAULT_NO_READBACK', '1')
    # LBPPC_TOGGLES=1 (Debug page: "Optimization switches", off by default): optimizations switched off while the
    # game runs, by writing their mask (gpu/shim/bbport_toggles.h, src/runtime_memory.c) to user/toggles.txt; read 4
    # times a second. Found the climbing teleports.
    if env.get('LBPPC_TOGGLES') == '1':
        env.setdefault('BB_TOGGLE_FILE', str(user / 'toggles.txt'))
    # LBPPC_KEY_PROMPTS=1 (unfinished, off; key_prompts.h): the face button icons show the keys bound to them in
    # input.ini.
    if env.get('LBPPC_KEY_PROMPTS') == '1':
        env.setdefault('LBPPC_KEY_LABELS', key_labels(HERE / 'input.ini'))
    # Online: LBPPC_NP_USER=<name> signs in locally as that player (no PSN or shadNet account) and
    # LBPPC_SERVER=<url> sends the game's server requests (Sony's, now closed) to a custom server, such as a
    # Refresh instance.
    # Or BB_SHADNET_SERVER=<host[:port]>, BB_SHADNET_NPID and BB_SHADNET_PASSWORD: signed in to a shadNet server (friends,
    # and the signaling that connects players to each other for multiplayer); its WebAPI (port 31315) answers the
    # game's PSN Web API calls.
    if env.get('BB_SHADNET_NPID'):
        env.setdefault('BB_ONLINE', '1')
        # Beta: sign-in, sessions, invitations and joins in the log (the online classes from info).
        env.setdefault('BB_ONLINE_LOG', '1')
        # What the server's clientversions.cfg checks (it can turn old LBPPC builds away).
        env.setdefault('BB_CLIENT_VERSION', f"LBPPC {env.get('LBPPC_VERSION', 'development build')}")
        env.setdefault('BB_P2P_PORT', '0')
        # The router forwards the P2P port to this PC (UPnP), so a joining player can reach the host: without
        # it their packets stop at the host's router. BB_UPNP=0 turns it off.
        env.setdefault('BB_UPNP', '1')
        host = env.get('BB_SHADNET_SERVER', '127.0.0.1').rsplit(':', 1)[0]
        env.setdefault('BB_SHADNET_WEBAPI', f'http://{host}:31315')
    elif env.get('LBPPC_NP_USER'):
        env.setdefault('BB_ONLINE', '1')
        # LBP3 binds its own P2P socket to the PS4's port, 3658 (its transport, which every multiplayer message,
        # its own party's included, goes through): the PSN signaling library takes another free port.
        env.setdefault('BB_P2P_PORT', '0')
    if env.get('LBPPC_SERVER') and not env.get('SHADPS4_HTTP_HOST_OVERRIDES_JSON'):
        overrides = OUT / 'host_overrides.json'
        # Levels, icons and photos come from res0..resf.littlebigplanetps3.online.scee.com (picked by the file's
        # hash): those go to the same server.
        hosts = ['littlebigplanetps3.online.scee.com'] + [f'res{d}.littlebigplanetps3.online.scee.com'
                                                          for d in '0123456789abcdef']
        overrides.write_text(json.dumps({host: env['LBPPC_SERVER'] for host in hosts}), encoding='utf-8')
        env['SHADPS4_HTTP_HOST_OVERRIDES_JSON'] = str(overrides)
    if MSYS2_BIN.is_dir():
        env['PATH'] = f'{MSYS2_BIN}{os.pathsep}{env.get("PATH", "")}'
    command = [str(probe), str(OUT / 'boot-linked.bin'), '--content-profile', str(OUT / 'content.bin'),
               '--app0', str(game), *(('--update', str(update)) if update else ()), *system_args,
               '--user', str(user), '--timeout', '0', *argv[1:]]
    keep_previous_log()
    # Players post their logs: every line loses the player's own details (IP addresses, names, the Windows user)
    # before it is written or shown (scripts/log_privacy.py).
    scrub = log_privacy.from_environment(env)
    with (OUT / 'last_run.log').open('wb') as log:
        summary = scrub(system_summary() + files_line + '\n')
        log.write(summary.encode())
        sys.stdout.write(summary)
        while True:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, env=env, cwd=HERE)
            for raw in process.stdout:
                line = scrub(raw.decode('utf-8', 'replace')).encode('utf-8')
                log.write(line)
                sys.stdout.buffer.write(line)
                sys.stdout.flush()
            code = process.wait()
            if code != RESTART_EXIT_CODE:
                break
            print('Restarting the game (settings menu)', flush=True)
    if code:
        # Windows status codes say how it ended: 0xC00000FD stack overflow, 0xC0000374 heap corruption,
        # 0xC0000409 fast fail (abort, a security check), 0xC0000005 access violation.
        print(f'Game exited with 0x{code & 0xffffffff:08X}', file=sys.stderr)
    return 0 if code == 0 else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
