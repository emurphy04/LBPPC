"""Shared LBPPC launcher code: settings, the game check, the environment run.py gets, process stopping and reports.

Neither a Tk nor a Qt module: the Windows launcher (lbppc_launcher.py) and the Linux launcher (lbppc_qt.py) both
import this, so they keep the same settings and the same game environment without importing each other's toolkit.
"""
import base64
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import threading

VERSION = '0.1.5'
DISCORD = 'https://discord.gg/YqfBQCU4vc'  # the permanent invite: help, bug reports, finding co-op partners
# Discord Rich Presence (scripts/discord_presence.py): LBPPC's application on the Discord Developer Portal, whose name
# Discord shows ("Playing LBPPC") and whose art asset "lbppc" is the picture. Empty: no presence.
DISCORD_APP_ID = '1569417825045712926'
BLURPLE = (88, 101, 242)  # Discord's own colour, for its button
# Packaged (frozen with PyInstaller): LBPPC.exe sits in the package folder and runs run.py itself (--script).
FROZEN = getattr(sys, 'frozen', False)
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
BIN = ROOT / 'bin' if (ROOT / 'bin' / ('LBPPC-Game.exe' if os.name == 'nt' else 'LBPPC-Game')).is_file() else ROOT / 'out'
if not FROZEN:
    sys.path[:0] = [str(ROOT / 'scripts'), str(Path(__file__).resolve().parent)]  # frozen, PyInstaller bundles them
import online_accounts  # noqa: E402
import lbppc_signin  # noqa: E402
import game_setup  # noqa: E402
import game_files  # noqa: E402
import save_import  # noqa: E402
import save_files  # noqa: E402
import log_privacy  # noqa: E402
import discord_presence  # noqa: E402
import updater  # noqa: E402
from i18n import N_, t  # noqa: E402
import i18n  # noqa: E402
import input_settings  # noqa: E402

# What the two launchers import. Modules are here too: both use scripts.game_setup, i18n, input_settings, etc., and
# neither imports the other's toolkit to get them.
__all__ = ['VERSION', 'DISCORD', 'DISCORD_APP_ID', 'BLURPLE', 'FROZEN', 'ROOT', 'BIN', 'DATA', 'LINUX', 'APPIMAGE',
           'USER', 'SETTINGS', 'INI', 'INPUT_INI', 'LOG', 'ASSETS', 'RESOLUTIONS', 'DISPLAY_MODES', 'FRAME_GENS',
           'UPSCALERS', 'SHARPENING', 'SHARPENING_ATTENUATION', 'LATENCY', 'OPTIONS', 'LANGUAGES', 'DIAGNOSTICS',
           'OVERLAY', 'DEFAULTS', 'DLSS_FILES', 'SHADER_PROGRESS', 'read_sfo', 'quick_files', 'check_game',
           'ini_frame_gen', 'ini_values', 'ini_set', 'load_settings', 'save_settings', 'host_environment',
           'shutil_which', 'open_path', 'known_players', 'game_environment', 'end_process', 'report_scrubber',
           'scrub_old_logs', 'make_report', 'zipfile_errors', 't', 'N_', 'i18n', 'input_settings', 'online_accounts',
           'lbppc_signin', 'game_setup', 'game_files', 'save_import', 'save_files', 'log_privacy', 'discord_presence',
           'updater', 'windows_language', 'linux_language', '_FILES_CHECKED']
# The player's files (user/, out/, lbppc.ini, input.ini): next to the program, or in LBPPC_DATA_DIR, which the Linux
# AppImage's AppRun sets to ~/.local/share/LBPPC (its program files are read-only). run.py reads the same variable.
DATA = Path(os.environ['LBPPC_DATA_DIR']).expanduser() if os.environ.get('LBPPC_DATA_DIR') else ROOT
LINUX = sys.platform.startswith('linux')
# Running from the AppImage (Linux): the AppImage file itself, which is what starts the launcher again (its program
# files are a mount that goes away when this launcher closes).
APPIMAGE = Path(os.environ['APPIMAGE']) if LINUX and os.environ.get('APPIMAGE') else None
USER = DATA / 'user'
SETTINGS = USER / 'launcher.json'
INI = DATA / 'lbppc.ini'
if not INI.exists() and (ROOT / 'bbport.ini').is_file():  # its name before 0.1.1 (the Bloodborne port's): kept
    try:
        (ROOT / 'bbport.ini').rename(INI)
    except OSError:
        pass
INPUT_INI = DATA / 'input.ini'  # the game's controls (runtime_input_config.c), the Controls page's
LOG = DATA / 'out' / 'last_run.log'
# The window icons both launchers use. In a frozen package PyInstaller extracts the assets beside the program.
ASSETS = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets'

# The game renders at these sizes natively (LBPPC_RENDER_RES: its render targets are made that large).
# Other aspects widen the view (ultrawide: more of the level at the sides, the menus laid out for it). Wider than
# 4480 the game does not survive yet (probe.c render_res_install); the window scales the picture up.
RESOLUTIONS = [(N_('1280 x 720 (lighter on the GPU)'), '1280x720'), (N_('1920 x 1080 (original)'), '1920x1080'), ('2560 x 1440', '2560x1440'), ('3840 x 2160 (4K)', '3840x2160'),
               ('2560 x 1080 (21:9)', '2560x1080'), ('3440 x 1440 (21:9)', '3440x1440'),
               ('3840 x 1600 (21:9)', '3840x1600'), ('3840 x 1080 (32:9)', '3840x1080'),
               ('4480 x 1260 (32:9)', '4480x1260')]
# BB_WINDOW_MODE: borderless covers the desktop; exclusive takes the display mode.
DISPLAY_MODES = [(N_('Windowed'), 'windowed'), (N_('Borderless fullscreen'), 'borderless'),
                 (N_('Exclusive fullscreen'), 'fullscreen')]
# lbppc.ini frame_gen: the game steps 30 times a second; frame generation shows 60.
FRAME_GENS = [(N_('Off (30 FPS)'), 'off'), (N_('FSR 3.1 (any GPU)'), 'fsr3'), ('DLSS (GeForce RTX 40/50)', 'dlss')]
# BB_FSR1: the finished picture scaled to the window with AMD FSR 1 instead of a plain stretch. It only does something
# when the resolution is below the window's (full screen at a lower resolution): that is upscaling.
UPSCALERS = [(N_('Off (plain scaling)'), 'off'), (N_('FSR 1 (any GPU)'), 'fsr1')]
# BB_RCAS / BB_RCAS_ATTENUATION: FSR 1's sharpening (RCAS), in thousandths of a stop: 0 is the sharpest.
SHARPENING = [(N_('Off'), 'off'), (N_('Low'), 'low'), (N_('Medium'), 'medium'), (N_('High'), 'high'),
              (N_('Maximum'), 'max')]
SHARPENING_ATTENUATION = {'low': 800, 'medium': 250, 'high': 100, 'max': 0}
# BB_LOW_LATENCY: NVIDIA Reflex (AMD Anti-Lag 2 on AMD), with the 30 FPS limit kept before each game frame.
LATENCY = [(N_('On (NVIDIA Reflex / AMD Anti-Lag 2)'), 'on'), (N_('On + Boost (NVIDIA)'), 'boost'), (N_('Off'), 'off')]
OPTIONS = [('resolution', N_('Resolution'), RESOLUTIONS, N_('Wider than 16:9 shows more of the level.')),
           ('display', N_('Display'), DISPLAY_MODES, N_('Borderless suits most screens; a full-size window is shrunk.')),
           ('frame_gen', N_('Frame generation'), FRAME_GENS, N_('The game runs 30 steps a second; this shows 60.')),
           ('latency', N_('Low latency'), LATENCY, N_('Less delay between your input and the screen.')),
           ('upscaling', N_('Upscaling'), UPSCALERS, N_('Sharper when the resolution is below your screen\'s.')),
           ('sharpening', N_('Sharpening'), SHARPENING, N_('How much FSR 1 sharpens the scaled picture.')),
           ('language', N_('Language'), None, N_('The game and this launcher. Automatic follows Windows.'))]
# The game's language: the PS4 system language LittleBigPlanet 3 reads (BB_LANGUAGE, runtime_services.c), numbered as
# the game's own E_LANG list and the PS4's. The US edition allows only English and French by itself; LBPPC lifts that
# (language_install, probe.c), so every language with text in the copy works. Arabic has none (only a subtitle stub).
LANGUAGES = [(N_('Automatic'), 'auto'), ('English (US)', '1'), ('English (UK)', '18'), ('Français', '2'),
             ('Español (España)', '3'), ('Español (Latinoamérica)', '20'), ('Deutsch', '4'), ('Italiano', '5'),
             ('Nederlands', '6'), ('Português (Portugal)', '7'), ('Português (Brasil)', '17'), ('Русский', '8'),
             ('Suomi', '12'), ('Svenska', '13'), ('Dansk', '14'), ('Norsk', '15'), ('Polski', '16')]
OPTIONS[-1] = OPTIONS[-1][:2] + (LANGUAGES,) + OPTIONS[-1][3:]


def linux_language():
    """The game language for the desktop's language (LC_ALL, LC_MESSAGES, LANG: "fr_FR.UTF-8"), English (US) when
    the game doesn't have it."""
    name = next((os.environ[v] for v in ('LC_ALL', 'LC_MESSAGES', 'LANG') if os.environ.get(v)), '')
    language, _, country = name.split('.')[0].split('@')[0].partition('_')
    language, country = language.lower(), country.upper()
    if language == 'en':
        return '1' if country in ('', 'US', 'CA') else '18'
    if language == 'es':
        return '3' if country in ('', 'ES') else '20'
    if language == 'pt':
        return '17' if country == 'BR' else '7'
    return {'fr': '2', 'de': '4', 'it': '5', 'nl': '6', 'ru': '8', 'fi': '12', 'sv': '13', 'da': '14', 'nb': '15',
            'no': '15', 'nn': '15', 'pl': '16'}.get(language, '1')


def windows_language():
    """The game language for Windows' display language (Automatic), English (US) when the game doesn't have it."""
    if LINUX:
        return linux_language()
    try:
        import ctypes
        lang = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except (AttributeError, OSError):
        return '1'
    primary, full = lang & 0x3ff, lang
    if primary == 0x09:  # English: the UK, Ireland, Australia and the like get UK English
        return '1' if full in (0x0409, 0x1009, 0x2409) else '18'
    if primary == 0x0a:  # Spanish: Spain's, or Latin American
        return '3' if full in (0x0c0a, 0x040a) else '20'
    if primary == 0x16:  # Portuguese: Brazil's, or Portugal's
        return '17' if full == 0x0416 else '7'
    return {0x0c: '2', 0x07: '4', 0x10: '5', 0x13: '6', 0x19: '8', 0x0b: '12', 0x1d: '13', 0x06: '14',
            0x14: '15', 0x15: '16'}.get(primary, '1')
# The game's progress through the shader list at its start: 'Shaders: preparing 120 of 464'.
SHADER_PROGRESS = re.compile(r'^Shaders: (preparing|building) (\d+) of (\d+)$')
# The Debug page's Diagnostics: (key, environment variable, title, hint, default). Each is passed to the game on
# Play as 1 or 0.
DIAGNOSTICS = [('frame_stats', 'BB_FRAME_STATS', N_('Frame log'), N_('Frame rate, pacing and every slow frame in the log.'),
                True),
               ('profile', 'LBPPC_PROFILE', N_('Slow-frame profiler'),
                N_('What the game waited for in each slow frame, and the CPU each thread used.'), False),
               ('positions', 'LBPPC_POS_LOG', N_('Player positions'),
                N_('Each player\'s position and buttons at every step. Large logs: for chasing a bug.'), False),
               ('no_readback', 'LBPPC_WRITE_FAULT_NO_READBACK', N_('Skip GPU readbacks'),
                N_('Faster in busy levels. Turn it off if something flickers or looks wrong.'), True),
               ('toggles', 'LBPPC_TOGGLES', N_('Optimization switches'),
                N_('Turns optimizations off while the game runs, from user/toggles.txt. For chasing a bug.'), False),
               ('friends_log', 'LBPPC_FRIENDS_LOG', N_('Friends menu log'),
                N_('What the game\'s Friends menu holds, every 5 seconds while online.'), False),
               ('async_shaders', 'BB_ASYNC_PIPELINES', N_('Background shader builds'),
                N_('A shader the game hasn\'t used before is built without pausing the game. Off: no pop-in, but '
                   'the game can freeze for a moment the first time.'), True)]
# The Debug page's Performance overlay: lbppc.ini switches, which the in-game menu (Insert) also changes.
OVERLAY = [('show_fps', N_('Frame rate'), False), ('show_frame_times', N_('Frame times'), False),
           ('show_latency', N_('Render latency'), False), ('show_vram', N_('Video memory'), False),
           ('show_gpu', N_('GPU usage'), False), ('show_cpu', N_('CPU usage'), False), ('show_ram', N_('RAM usage'), False),
           ('show_fps_graph', N_('Frame time graph'), False), ('graph_game_frames', N_('Graph the game\'s own frames'), True),
           ('counter_colors', N_('Colour-coded values'), True)]
DEFAULTS = {'game': '', 'update': '', 'language': 'auto', 'logs_scrubbed': False, 'resolution': '1920x1080', 'display': 'borderless', 'frame_gen': 'dlss', 'latency': 'on',
            'upscaling': 'off', 'sharpening': 'medium', 'beta_notice_seen': False,
            'online': False, 'online_name': '', 'password': '', 'lbppc_signin': None, 'discord_presence': True, 'save_file': save_files.MAIN, 'discord_app_id': '',
            'diagnostics': {key: on for key, _env, _title, _hint, on in DIAGNOSTICS},
            # Not shown: the LBPPC server unless launcher.json names another (a test server on this PC).
            'server': online_accounts.OFFICIAL_SERVER}
DLSS_FILES = ('lbppc_dlss.dll', 'nvngx_dlss.dll', 'nvngx_dlssg.dll')

def read_sfo(path):
    """The strings and numbers of a param.sfo, by key; empty when it cannot be read."""
    try:
        data = Path(path).read_bytes()
        magic, _version, key_table, data_table, count = struct.unpack_from('<4sIIII', data, 0)
        if magic != b'\0PSF':
            return {}
        values = {}
        for i in range(count):
            key_offset, fmt, length, _max, data_offset = struct.unpack_from('<HHIII', data, 20 + i * 16)
            key = data[key_table + key_offset:data.index(b'\0', key_table + key_offset)].decode('ascii', 'replace')
            raw = data[data_table + data_offset:data_table + data_offset + length]
            values[key] = struct.unpack('<I', raw)[0] if fmt == 0x0404 and length == 4 else \
                raw.rstrip(b'\0').decode('utf-8', 'replace')
        return values
    except (OSError, ValueError, struct.error):
        return {}


_FILES_CHECKED = {}  # game folder: the quick file check's result (checked once a session, or after Set up)


def quick_files(root, update=None):
    key = f'{root}|{update or ""}'
    if key not in _FILES_CHECKED:
        try:
            _FILES_CHECKED[key] = game_files.check(root, update=update)
        except (OSError, ValueError):
            _FILES_CHECKED[key] = None  # no file list (a development checkout without it): not checked
    return _FILES_CHECKED[key]


def check_game(folder, update=None):
    """(state, description) of a game folder: 'ready', 'warn' (playable, but files are missing or the wrong size),
    'setup' (Set up... fixes it) or 'missing' (scripts/game_setup.py, scripts/game_files.py). `update`: the update's
    own folder (shadPS4's layout), read over the game; else the one beside the game is looked for."""
    if not folder:
        return 'missing', t("Choose your game's folder, or click Set up... to be walked through it.")
    state, text = game_setup.status(folder, update or None)
    if state == 'ready':
        game = game_setup.Folder(folder)
        extra = game_setup.separate_update(game, update or None) if game.version != game_setup.VERSION else None
        files = quick_files(game.root or folder, extra)
        if files and not files.ok:
            return 'warn', files.summary(False)
    dlc = len(game_setup.installed_dlc(USER)) if state == 'ready' else 0
    return state, text + (t(', with {count} DLC', count=dlc) if dlc else '')


def ini_frame_gen():
    """lbppc.ini's frame_gen when the launcher offers it: the in-game menu (Insert) changes it there too."""
    try:
        lines = INI.read_text(encoding='utf-8').splitlines()
    except OSError:
        return None
    value = next((line.split('=', 1)[1].strip() for line in lines if line.startswith('frame_gen=')), None)
    return value if value in {v for _name, v in FRAME_GENS} else None


def ini_values():
    """lbppc.ini as key -> value (empty when it does not exist yet)."""
    try:
        lines = INI.read_text(encoding='utf-8').splitlines()
    except OSError:
        return {}
    return {line.split('=', 1)[0].strip(): line.split('=', 1)[1].strip() for line in lines
            if '=' in line and not line.lstrip().startswith('#')}


def ini_set(key, value):
    """One lbppc.ini line set, the others kept."""
    lines = INI.read_text(encoding='utf-8').splitlines() if INI.is_file() else []
    lines = [line for line in lines if not line.startswith(f'{key}=')] + [f'{key}={value}']
    INI.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _dpapi(data, protect):
    """Windows DPAPI (also in Wine and Proton): data locked to, or unlocked by, this Windows account.
    None when it is not available or the data was locked by another account."""
    import ctypes
    from ctypes import wintypes
    if not hasattr(ctypes, 'windll'):
        return None

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    source, result = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), Blob()
    call = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not call(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)):  # no prompts
        return None
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)


def load_settings():
    try:
        values = json.loads(SETTINGS.read_text(encoding='utf-8'))
        values.setdefault('password', values.get('shadnet_password', ''))  # 0.0.1 betas' name for it
        if values.get('password_protected'):  # save_settings's DPAPI copy
            plain = _dpapi(base64.b64decode(values['password_protected']), protect=False)
            values['password'] = plain.decode('utf-8') if plain is not None else ''
        if values.get('lbppc_signin_protected'):  # the lbppc.com sign-in (lbppc_signin.py), locked the same way
            plain = _dpapi(base64.b64decode(values['lbppc_signin_protected']), protect=False)
            try:
                values['lbppc_signin'] = json.loads(plain) if plain is not None else None
            except ValueError:
                values['lbppc_signin'] = None
        settings = {**DEFAULTS, **{k: v for k, v in values.items() if k in DEFAULTS}}
        settings['diagnostics'] = {**DEFAULTS['diagnostics'], **(values.get('diagnostics') or {})}
    except (OSError, ValueError, AttributeError, TypeError):
        settings = dict(DEFAULTS)
        settings['diagnostics'] = dict(DEFAULTS['diagnostics'])
    settings['frame_gen'] = ini_frame_gen() or settings['frame_gen']
    return settings


_SETTINGS_LOCK = threading.Lock()  # the sign-in is saved from the friends list's thread too


def save_settings(values):
    with _SETTINGS_LOCK:
        _save_settings(values)


def _save_settings(values):
    USER.mkdir(parents=True, exist_ok=True)
    # The password is kept locked with DPAPI, not as text; plain text only where DPAPI is missing.
    stored = dict(values)
    stored.pop('shadnet_password', None)
    locked = _dpapi(values.get('password', '').encode('utf-8'), protect=True) if values.get('password') else None
    if locked is not None:
        stored['password'] = ''
        stored['password_protected'] = base64.b64encode(locked).decode('ascii')
    # The lbppc.com sign-in (its refresh token) too.
    signin = values.get('lbppc_signin')
    locked = _dpapi(json.dumps(signin).encode('utf-8'), protect=True) if signin else None
    if locked is not None:
        stored['lbppc_signin'] = None
        stored['lbppc_signin_protected'] = base64.b64encode(locked).decode('ascii')
    SETTINGS.write_text(json.dumps(stored, indent=2), encoding='utf-8')
    if os.name != 'nt':
        # No DPAPI here: the password and sign-in are in the file as text, so only this user may read it.
        try:
            os.chmod(SETTINGS, 0o600)
        except OSError:
            pass
    # lbppc.ini: frame_gen set, its other lines kept.
    lines = INI.read_text(encoding='utf-8').splitlines() if INI.is_file() else []
    lines = [line for line in lines if not line.startswith('frame_gen=')]
    lines.append(f"frame_gen={values['frame_gen']}")
    INI.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def host_environment():
    """The environment for programs of the system (the file manager, a new launcher from the AppImage): on Linux
    without what PyInstaller changed for the bundled Python (it keeps earlier values in <NAME>_ORIG)."""
    env = dict(os.environ)
    if LINUX and FROZEN:
        for name in ('LD_LIBRARY_PATH', 'TCL_LIBRARY', 'TK_LIBRARY', 'SSL_CERT_FILE', 'SSL_CERT_DIR'):
            original = env.pop(f'{name}_ORIG', None)
            if original:
                env[name] = original
            else:
                env.pop(name, None)
    return env


def shutil_which(name):
    import shutil
    return shutil.which(name)


def open_path(path):
    if not path.suffix:
        path.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        os.startfile(str(path))  # noqa: S606 - opens the folder or log in Explorer / the text editor
    else:
        subprocess.Popen(['xdg-open', str(path)], env=host_environment())


def known_players(friends):
    """Every other player's name the launcher knows: friends, requests both ways, blocked players."""
    if not friends:
        return []
    names = [entry[0] if isinstance(entry, tuple) else entry for entry in friends.friends]
    return [n for n in names + friends.sent + friends.received + friends.blocked if n]


def game_environment(settings, friends=None, warn=None, auth=None):
    """The environment run.py gets for these settings, and the scrubber for its output.

    Shared by the Tk launcher and the Qt one (launcher/lbppc_qt.py). Raises online_accounts.AccountError when the
    online name and password are not usable, and lbppc_signin.SignInError when the lbppc.com sign-in cannot produce
    a token. `auth` is the launcher's lbppc_signin.Account, when it has one: signed in through lbppc.com, the game
    gets an access token instead of the password. `warn(line)` gets a line for the launcher's log."""
    env = dict(os.environ)
    env['LBPPC_RENDER_RES'] = settings['resolution']
    env.pop('LBPPC_SAVE_DIR', None); env.pop('LBPPC_SAVE_NAME', None)
    env.update(save_files.environment(USER, settings.get('save_file')))
    if settings['display'] != 'windowed':
        env['BB_WINDOW_MODE'] = settings['display']
    else:
        env.pop('BB_WINDOW_MODE', None)
    env['BB_FSR1'] = '1' if settings['upscaling'] == 'fsr1' else '0'
    language = settings.get('language', 'auto')
    if language not in {code for _name, code in LANGUAGES}:
        language = 'auto'
    env['BB_LANGUAGE'] = windows_language() if language == 'auto' else language
    env['BB_RCAS'] = '0' if settings['sharpening'] == 'off' else '1'
    env['BB_RCAS_ATTENUATION'] = str(SHARPENING_ATTENUATION.get(settings['sharpening'], 250))
    if settings['latency'] == 'off':
        env.update(BB_LOW_LATENCY='off', BB_VBLANK_HZ='60', BB_FPS_LIMIT='-1')
    else:
        env.update(BB_LOW_LATENCY=settings['latency'], BB_VBLANK_HZ='0', BB_FPS_LIMIT='30')
    for name in ('LBPPC_NP_USER', 'LBPPC_SERVER', 'BB_SHADNET_NPID', 'BB_SHADNET_PASSWORD', 'BB_SHADNET_SERVER',
                 'LBPPC_JOIN_ALLOW', 'BB_SHADNET_WEBAPI', 'BB_SHADNET_TLS', 'BB_USER_NAME'):
        env.pop(name, None)
    # The mouse isn't offered yet (input_settings.MOUSE_SUPPORTED): input.ini from an earlier build that turned it
    # on is saved again with it off.
    try:
        mouse_on = re.search(r'(?m)^\s*mouse_to_joystick\s*=', INPUT_INI.read_text(encoding='utf-8'))
    except OSError:
        mouse_on = None
    if mouse_on and not input_settings.MOUSE_SUPPORTED:
        try:
            input_settings.InputSettings.load(INPUT_INI).save(INPUT_INI)
        except OSError as error:
            line = f'Launcher: input.ini could not be saved with the mouse off: {error}'
            if warn:
                warn(line)
            else:
                print(line, file=sys.stderr)
    # The player's name in the game (pressing the right stick shows it), offline too.
    if re.fullmatch(online_accounts.NAME_PATTERN, settings['online_name']):
        env['BB_USER_NAME'] = settings['online_name']
    if settings['online']:
        name, password = settings['online_name'], settings['password']
        if auth is not None and auth.signed_in:
            # Signed in through lbppc.com: the game gives shadNet a fresh access token, not a password.
            name, password = auth.username or '', auth.game_password()
        else:
            online_accounts.check_name_and_password(name, password)
        servers = online_accounts.server_endpoints(settings['server'])
        host, port, tls = online_accounts.shadnet_address(servers['shadnet'])
        env.update(LBPPC_SERVER=servers['refresh'], BB_SHADNET_NPID=name, BB_SHADNET_PASSWORD=password,
                   BB_SHADNET_SERVER=f'{host}:{port}', BB_SHADNET_WEBAPI=servers['webapi'])
        if tls:
            env['BB_SHADNET_TLS'] = '1'
    for key, variable, _title, _hint, _on in DIAGNOSTICS:
        env[variable] = '1' if settings['diagnostics'].get(key) else '0'
    env['PYTHONUNBUFFERED'] = '1'
    env['LBPPC_VERSION'] = VERSION
    # Other players' names the launcher knows, left out of the log (scripts/log_privacy.py, run.py).
    env['LBPPC_LOG_NAMES'] = ','.join(known_players(friends))
    return env, log_privacy.from_environment(env)


def end_process(process):
    """run.py's game process is its child: the whole tree ends (on Windows before run.py itself, whose children
    taskkill finds through it). taskkill returns once the game is gone: in a level, Windows and the driver take
    about 6 s to free its memory and GPU resources. Elsewhere run.py starts a process group of its own (play), which
    ends together: asked first, then made to after 10 s."""
    if os.name == 'nt':
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(process.pid)], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        process.terminate()
        return
    import signal
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    except OSError:
        process.terminate()


def report_scrubber(settings, friends=None):
    me = [settings.get('online_name', '')]
    return log_privacy.Scrubber(me=[m for m in me if m], names=known_players(friends),
                                secrets=[settings.get('password', ''),
                                         (settings.get('lbppc_signin') or {}).get('refresh_token', '')])


def scrub_old_logs(settings, friends=None):
    """Once: the logs kept from before 0.1.1, written with the player's details in them, rewritten without."""
    scrub = report_scrubber(settings, friends)
    for log in [*sorted((DATA / 'out' / 'logs').glob('run-*.log')), LOG]:
        try:
            if log.is_file():
                log_privacy.scrub_file(log, scrub)
        except OSError:
            pass


def make_report(friends=None):
    """A zip for a bug report: the last game logs, the settings and input files, without the player's details
    (scripts/log_privacy.py: names, IP addresses, the Windows user; the password left out); no game files. Returns
    its path."""
    import time
    import zipfile
    reports = DATA / 'out' / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / time.strftime('LBPPC-report-%Y%m%d-%H%M%S.zip')
    # The same replacements as the logs get while the game runs (scripts/log_privacy.py), one scrubber for the
    # whole report so a label means the same thing in every file. Logs from before 0.1.1 were written unscrubbed.
    settings = load_settings()
    scrub = report_scrubber(settings, friends)

    logs = sorted((DATA / 'out' / 'logs').glob('run-*.log'))[-3:]
    files = [(log, f'logs/{log.name}') for log in logs] + [(LOG, 'last_run.log'), (INI, 'lbppc.ini'),
                                                           (INPUT_INI, 'input.ini')]
    settings = {k: v for k, v in settings.items() if k not in ('password', 'lbppc_signin')}
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for source, name in files:
            if source.is_file():
                archive.writestr(name, scrub.text(source.read_text(encoding='utf-8', errors='replace')))
        archive.writestr('launcher.json', scrub.text(json.dumps(settings, indent=2)))
    return path



def zipfile_errors():
    """The exception a bad AppImage download raises, for both launchers' update dialogs."""
    import zipfile
    return zipfile.BadZipFile
