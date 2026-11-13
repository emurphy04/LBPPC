#!/usr/bin/env python3
"""LBPPC launcher: pick the game folder (the player's own dump), choose the resolution, display mode and
frame generation, and play. Settings are kept in user/launcher.json; frame generation also goes to
lbppc.ini, which the renderer reads. The game runs through run.py with the same Python.

Its look follows LittleBigPlanet 3's menus (lbppc_theme.py draws it); the banner is the dump's own artwork
(sce_sys/pic1.png). Nothing of the game ships with the launcher.

usage: python launcher/lbppc_launcher.py
"""
import base64
import gc
import json
import math
import os
from pathlib import Path
import queue
import re
import struct
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, font as tkfont, messagebox, ttk

from PIL import Image, ImageTk

VERSION = '0.1.3'
DISCORD = 'https://discord.gg/YqfBQCU4vc'  # the permanent invite: help, bug reports, finding co-op partners
# Discord Rich Presence (scripts/discord_presence.py): LBPPC's application on the Discord Developer Portal, whose name
# Discord shows ("Playing LBPPC") and whose art asset "lbppc" is the picture. Empty: no presence.
DISCORD_APP_ID = '1569417825045712926'
BLURPLE = (88, 101, 242)  # Discord's own colour, for its button
# Packaged (frozen with PyInstaller): LBPPC.exe sits in the package folder and runs run.py itself (--script).
FROZEN = getattr(sys, 'frozen', False)
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
BIN = ROOT / 'bin' if (ROOT / 'bin' / 'LBPPC-Game.exe').is_file() else ROOT / 'out'
if not FROZEN:
    sys.path.insert(0, str(ROOT / 'scripts'))  # frozen, PyInstaller bundles the module
import online_accounts  # noqa: E402
import game_setup  # noqa: E402
import game_files  # noqa: E402
import save_import  # noqa: E402
import log_privacy  # noqa: E402
import discord_presence  # noqa: E402
import updater  # noqa: E402
import ui_scale  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lbppc_theme as theme  # noqa: E402
from i18n import N_, t  # noqa: E402
import i18n  # noqa: E402
import input_settings  # noqa: E402
USER = ROOT / 'user'
SETTINGS = USER / 'launcher.json'
INI = ROOT / 'lbppc.ini'
if not INI.exists() and (ROOT / 'bbport.ini').is_file():  # its name before 0.1.1 (the Bloodborne port's): kept
    try:
        (ROOT / 'bbport.ini').rename(INI)
    except OSError:
        pass
INPUT_INI = ROOT / 'input.ini'  # the game's controls (runtime_input_config.c), the Controls page's
LOG = ROOT / 'out' / 'last_run.log'

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


def windows_language():
    """The game language for Windows' display language (Automatic), English (US) when the game doesn't have it."""
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
DEFAULTS = {'game': '', 'language': 'auto', 'logs_scrubbed': False, 'resolution': '1920x1080', 'display': 'borderless', 'frame_gen': 'dlss', 'latency': 'on',
            'upscaling': 'off', 'sharpening': 'medium', 'beta_notice_seen': False,
            'online': False, 'online_name': '', 'password': '', 'discord_presence': True, 'discord_app_id': '',
            'diagnostics': {key: on for key, _env, _title, _hint, on in DIAGNOSTICS},
            # Not shown: the LBPPC server unless launcher.json names another (a test server on this PC).
            'server': online_accounts.OFFICIAL_SERVER}
DLSS_FILES = ('lbppc_dlss.dll', 'nvngx_dlss.dll', 'nvngx_dlssg.dll')

TEXT, MUTED, GOOD, ERROR, WARN = theme.TEXT, theme.MUTED, theme.GOOD, theme.ERROR, theme.WARN
BG = theme.hex_colour(theme.NIGHT)
# Windows: the launcher draws its own title bar (CUSTOM_FRAME); elsewhere the window manager's stays.
CUSTOM_FRAME = os.name == 'nt'
# The window: the settings in a column on the left, the friends list on the right.
WIDTH, MAIN_WIDTH, TITLE_HEIGHT, BANNER_HEIGHT = 1180, 860, 38 if os.name == 'nt' else 0, 176
LOG_HEIGHT = 220
UI_FONT = ('Segoe UI', 10)
HINT_FONT = ('Segoe UI', 9)


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


def quick_files(root):
    root = str(root)
    if root not in _FILES_CHECKED:
        try:
            _FILES_CHECKED[root] = game_files.check(root)
        except (OSError, ValueError):
            _FILES_CHECKED[root] = None  # no file list (a development checkout without it): not checked
    return _FILES_CHECKED[root]


def check_game(folder):
    """(state, description) of a game folder: 'ready', 'warn' (playable, but files are missing or the wrong size),
    'setup' (Set up... fixes it) or 'missing' (scripts/game_setup.py, scripts/game_files.py)."""
    if not folder:
        return 'missing', t("Choose your game's folder, or click Set up... to be walked through it.")
    state, text = game_setup.status(folder)
    if state == 'ready':
        files = quick_files(game_setup.find_root(folder) or folder)
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
        settings = {**DEFAULTS, **{k: v for k, v in values.items() if k in DEFAULTS}}
        settings['diagnostics'] = {**DEFAULTS['diagnostics'], **(values.get('diagnostics') or {})}
    except (OSError, ValueError, AttributeError, TypeError):
        settings = dict(DEFAULTS)
        settings['diagnostics'] = dict(DEFAULTS['diagnostics'])
    settings['frame_gen'] = ini_frame_gen() or settings['frame_gen']
    return settings


def save_settings(values):
    USER.mkdir(exist_ok=True)
    # The password is kept locked with DPAPI, not as text; plain text only where DPAPI is missing.
    stored = dict(values)
    stored.pop('shadnet_password', None)
    locked = _dpapi(values.get('password', '').encode('utf-8'), protect=True) if values.get('password') else None
    if locked is not None:
        stored['password'] = ''
        stored['password_protected'] = base64.b64encode(locked).decode('ascii')
    SETTINGS.write_text(json.dumps(stored, indent=2), encoding='utf-8')
    # lbppc.ini: frame_gen set, its other lines kept.
    lines = INI.read_text(encoding='utf-8').splitlines() if INI.is_file() else []
    lines = [line for line in lines if not line.startswith('frame_gen=')]
    lines.append(f"frame_gen={values['frame_gen']}")
    INI.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def open_path(path):
    if not path.suffix:
        path.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        os.startfile(str(path))  # noqa: S606 - opens the folder or log in Explorer / the text editor
    else:
        subprocess.Popen(['xdg-open', str(path)])


def known_players(friends):
    """Every other player's name the launcher knows: friends, requests both ways, blocked players."""
    if not friends:
        return []
    names = [entry[0] if isinstance(entry, tuple) else entry for entry in friends.friends]
    return [n for n in names + friends.sent + friends.received + friends.blocked if n]


def report_scrubber(settings, friends=None):
    me = [settings.get('online_name', '')]
    return log_privacy.Scrubber(me=[m for m in me if m], names=known_players(friends),
                                secrets=[settings.get('password', '')])


def scrub_old_logs(settings, friends=None):
    """Once: the logs kept from before 0.1.1, written with the player's details in them, rewritten without."""
    scrub = report_scrubber(settings, friends)
    for log in [*sorted((ROOT / 'out' / 'logs').glob('run-*.log')), LOG]:
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
    reports = ROOT / 'out' / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / time.strftime('LBPPC-report-%Y%m%d-%H%M%S.zip')
    # The same replacements as the logs get while the game runs (scripts/log_privacy.py), one scrubber for the
    # whole report so a label means the same thing in every file. Logs from before 0.1.1 were written unscrubbed.
    settings = load_settings()
    scrub = report_scrubber(settings, friends)

    logs = sorted((ROOT / 'out' / 'logs').glob('run-*.log'))[-3:]
    files = [(log, f'logs/{log.name}') for log in logs] + [(LOG, 'last_run.log'), (INI, 'lbppc.ini'),
                                                           (ROOT / 'input.ini', 'input.ini')]
    settings = {k: v for k, v in settings.items() if k != 'password'}
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for source, name in files:
            if source.is_file():
                archive.writestr(name, scrub.text(source.read_text(encoding='utf-8', errors='replace')))
        archive.writestr('launcher.json', scrub.text(json.dumps(settings, indent=2)))
    return path


class Scene(tk.Canvas):
    """A canvas the launcher's look is drawn on: images placed at fixed spots (Tk keeps a reference to each),
    tabs that act as buttons, text, and real widgets (fields, dropdowns) on top."""

    def __init__(self, parent, width, height):
        super().__init__(parent, width=width, height=height, bg=BG, highlightthickness=0, bd=0)
        self.images = {}

    def image(self, key, pil, x, y, anchor='nw', tags=()):
        """Places (or replaces) the image named `key`."""
        photo = ImageTk.PhotoImage(ui_scale.image(pil))
        old = self.images.get(key)
        if old:
            self.itemconfigure(old[0], image=photo)
            self.coords(old[0], x, y)
            self.images[key] = (old[0], photo)
        else:
            self.images[key] = (self.create_image(x, y, image=photo, anchor=anchor, tags=tags), photo)
        return self.images[key][0]

    def widget(self, widget, x, y, width, height):
        return self.create_window(x, y, window=widget, anchor='nw', width=width, height=height)


class Tab:
    """A hand-cut LBP3 tab on a Scene used as a button. Blue; under the pointer it turns green and pops (grows past
    its hover size, then settles); grey while disabled. Placed by one edge ('w' left, 'e' right) and its vertical
    centre, and grows from its centre."""

    count = 0
    # The pop: (scale, colour) frames, 35 ms apart, into and out of the hover state.
    POP_IN = ((1.06, 'green'), (1.14, 'green'), (1.10, 'green'))
    POP_OUT = ((1.04, 'blue'), (1.0, 'blue'))
    FRAME_MS = 35

    def __init__(self, scene, text, command, x, y, anchor='w', size=15, pad=(12, 9), tilt=0.0, weight='Bold',
                 fill=theme.BLUE):
        self.scene, self.command, self.enabled, self.hovered = scene, command, True, False
        Tab.count += 1
        self.key = f'tab{Tab.count}'
        colours = {'blue': fill, 'green': theme.GREEN, 'grey': theme.GREY}  # 'blue' is the resting colour

        def frame(scale, colour):
            return theme.tab(text, colours[colour], size, pad, tilt, weight, scale=scale)[0]

        self.frames = {frame_key: frame(*frame_key) for frame_key in
                       {(1.0, 'blue'), (1.0, 'grey'), *self.POP_IN, *self.POP_OUT}}
        normal, margin = theme.tab(text, fill, size, pad, tilt, weight)
        self.width, self.height = normal.width - 2 * margin, normal.height - 2 * margin
        self.pending = []
        self.item = scene.image(self.key, normal, 0, 0, anchor='center')
        self.move(x, y, anchor)
        scene.tag_bind(self.item, '<Enter>', lambda _e: self.hover(True))
        scene.tag_bind(self.item, '<Leave>', lambda _e: self.hover(False))
        scene.tag_bind(self.item, '<Button-1>', lambda _e: self.enabled and self.command())

    @property
    def right(self):
        return self.cx + self.width / 2

    def move(self, x, y, anchor='w'):
        self.cx = x + self.width / 2 if anchor == 'w' else x - self.width / 2
        self.cy = y
        self.show((1.0, 'blue' if self.enabled else 'grey'))

    def show(self, frame_key):
        self.scene.image(self.key, self.frames[frame_key], self.cx, self.cy, anchor='center')

    def play_frames(self, frames):
        for after_id in self.pending:
            self.scene.after_cancel(after_id)
        self.pending = [self.scene.after(i * self.FRAME_MS, self.show, frame_key)
                        for i, frame_key in enumerate(frames)]

    def hover(self, inside):
        self.hovered = inside
        if getattr(self, 'selected', False):
            return
        self.scene.configure(cursor='hand2' if inside and self.enabled else '')
        if self.enabled:
            self.play_frames(self.POP_IN if inside else self.POP_OUT)

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.play_frames(((1.0, 'blue' if enabled else 'grey'),))

    def select(self, selected):
        """A page tab: the current page's stays green and popped (LBP3's highlighted item), the others behave as
        buttons."""
        self.selected = selected
        self.play_frames(((1.10, 'green'),) if selected else ((1.0, 'blue'),))

    def forget(self):
        """Before its canvas is cleared: no animation frame may draw it again."""
        for after_id in self.pending:
            self.scene.after_cancel(after_id)
        self.pending = []


class BetaNotice:
    """Shown at every start until "Don't show this again" is ticked: the build is a beta, and how to report a
    problem. A card like the launcher's
    panels over the launcher, which dims behind it; it fades in rising a little, and fades out."""

    W = 600
    KEY = '#010203'  # the card's window is see-through where it has this colour: its rounded corners
    DIM = 0.6
    INTRO = N_('LBPPC {version} is a beta. Most of LittleBigPlanet 3 plays well, but you may run into effects that draw '
             'wrongly in some levels, the odd crash, or rough edges online.')
    STEPS = (N_('Close the game.'),
             N_('Click Make a report at the bottom of the launcher. It saves a zip of the game\'s logs and opens its '
                'folder. Your password and Windows user name are left out.'),
             N_('Post the zip in the bug reports forum on our Discord, with the level and what you were doing.'))
    THANKS = N_('Every report helps us fix what\'s broken. Thanks for testing!')

    def __init__(self, launcher, on_closed=None):
        self.launcher, self.closing, self.dont_show, self.on_closed = launcher, False, False, on_closed
        card, buttons_y = self.draw()
        self.buttons_y = buttons_y
        scene = self.open_window(card)
        self.ok = Tab(scene, t('Got it'), self.close, self.W - 30, buttons_y, 'e', size=15)
        self.discord = Tab(scene, t('Join the Discord'), lambda: webbrowser.open(DISCORD),
                           self.ok.cx - self.ok.width / 2 - 16, buttons_y, 'e', size=15, fill=BLURPLE)
        box = scene.image('dont:box', theme.checkbox(False), 30, buttons_y - 11)
        label = scene.image('dont:label', theme.text_image(t("Don't show this again"), 13, TEXT, 'Medium'), 60,
                            buttons_y - 10)
        for item in (box, label):
            scene.tag_bind(item, '<Button-1>', lambda _e: self.toggle_dont_show())
            scene.tag_bind(item, '<Enter>', lambda _e: scene.configure(cursor='hand2'))
            scene.tag_bind(item, '<Leave>', lambda _e: scene.configure(cursor=''))
        self.window.bind('<Return>', lambda _e: self.close())
        self.present()

    def open_window(self, card):
        """The dimmed launcher and the card's window over it (not shown yet: present()). Returns the card's Scene."""
        launcher = self.launcher
        launcher.update_idletasks()
        lx, ly = launcher.winfo_rootx(), launcher.winfo_rooty()
        lw, lh = launcher.winfo_width(), launcher.winfo_height()
        self.H = card.height
        # The launcher dimmed: a black window over it, faded in.
        self.shade = shade = tk.Toplevel(launcher)
        shade.withdraw()
        shade.overrideredirect(True)
        shade.configure(bg='#000000')
        shade.attributes('-alpha', 0.0)
        shade.geometry(f'{lw}x{lh}+{lx}+{ly}')
        shade.transient(launcher)
        self.window = window = tk.Toplevel(launcher)
        window.withdraw()
        window.overrideredirect(True)
        window.configure(bg=self.KEY)
        window.attributes('-transparentcolor', self.KEY)
        window.attributes('-alpha', 0.0)
        window.transient(launcher)
        self.scene = scene = Scene(window, self.W, self.H)
        scene.configure(bg=self.KEY)
        scene.pack(fill='both', expand=True)
        scene.image('card', card, 0, 0)
        self.x = lx + (lw - ui_scale.s(self.W)) // 2
        self.y = ly + max(0, (lh - ui_scale.s(self.H)) // 2 - 20)
        window.geometry(f'{ui_scale.s(self.W)}x{ui_scale.s(self.H)}+{self.x}+{self.y + 18}')
        window.protocol('WM_DELETE_WINDOW', self.close)
        window.bind('<Escape>', lambda _e: self.close())
        shade.bind('<Button-1>', lambda _e: self.nudge())
        launcher.card = self  # one card at a time (Launcher.show_update waits for it)
        return scene

    def present(self):
        shade, window, launcher = self.shade, self.window, self.launcher
        shade.deiconify()
        self.round(shade)
        # Over the launcher explicitly: its title bar shows it again at start (TitleBar.show_in_taskbar), which
        # can leave a window it owns underneath it.
        shade.tk.call('raise', shade._w, launcher._w)
        window.deiconify()
        window.tk.call('raise', window._w, shade._w)
        window.focus_force()
        window.grab_set()
        self.animate(0, opening=True)
        self.window.after(400, self.keep_on_top)

    def keep_on_top(self):
        """While the card is open: if the launcher has come over it (Windows sends the launcher no event for that
        every time), the card is raised again."""
        if self.closing or not CUSTOM_FRAME:
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            launcher, card = int(self.launcher.wm_frame(), 16), int(self.window.wm_frame(), 16)
            h = user32.GetTopWindow(None)
            while h and h not in (launcher, card):
                h = user32.GetWindow(h, 2)  # GW_HWNDNEXT: down the z-order
            if h == launcher:
                self.bring_to_front()
            self.window.after(300, self.keep_on_top)
        except (OSError, ValueError, tk.TclError):
            pass

    def bring_to_front(self):
        """The backdrop and the card over the launcher again. Windows brings the launcher to the front when a folder
        or message dialog closes, or when it is clicked in the taskbar: the card, which keeps the input, was left
        underneath it and the launcher looked frozen."""
        if self.closing:
            return
        try:
            self.shade.tk.call('raise', self.shade._w, self.launcher._w)
            self.window.tk.call('raise', self.window._w, self.shade._w)
            self.window.focus_force()
            self.window.grab_set()
        except tk.TclError:
            pass

    def draw(self):
        """The card, and where its buttons go."""
        w, pad = self.W, 30
        width = w - 2 * pad
        title = theme.text_image(t('Welcome to the beta!'), 26, theme.hex_colour(theme.ORANGE), 'Bold',
                                 outline=theme.CREAM)
        title = theme.shadowed(title.rotate(1.5, resample=Image.BICUBIC, expand=True), 4, (0, 2))[0]
        intro = theme.paragraph(t(self.INTRO, version=VERSION), 14, width, TEXT, 'Regular')
        heading = theme.text_image(t('Found a problem?'), 15, theme.hex_colour(theme.LIME), 'SemiBold')
        steps = [theme.paragraph(t(text), 14, width - 42, TEXT, 'Regular') for text in self.STEPS]
        thanks = theme.paragraph(t(self.THANKS), 13, width, MUTED, 'Regular')
        y = 44
        parts = [(title, pad - 10, y - 14)]
        y += title.height - 14
        parts.append((intro, pad, y))
        y += intro.height + 14
        parts.append((heading, pad, y))
        y += heading.height + 10
        for number, step in enumerate(steps, 1):
            parts.append((theme.badge(number), pad, y - 2))
            parts.append((step, pad + 42, y))
            y += max(step.height, 26) + 8
        y += 4
        parts.append((thanks, pad, y))
        y += thanks.height + 18
        buttons_y = y + 18
        height = buttons_y + 40
        card = theme.notice_card(w, height, 'Beta')
        for image, x, top in parts:
            card.alpha_composite(image, (int(x), int(top)))
        return card, buttons_y

    def round(self, window):
        """Rounded corners like the launcher's (Windows 11 draws them; elsewhere the window is cut)."""
        if not CUSTOM_FRAME:
            return
        import ctypes
        try:
            hwnd = int(window.wm_frame(), 16)
            preference = ctypes.c_int(2)  # DWMWCP_ROUND
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(preference),
                                                          ctypes.sizeof(preference)) != 0:
                w, h = window.winfo_width(), window.winfo_height()
                ctypes.windll.user32.SetWindowRgn(hwnd, ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1,
                                                                                              16, 16), True)
        except (OSError, ValueError, tk.TclError):
            pass

    FRAMES = 12
    FRAME_MS = 16

    def animate(self, frame, opening):
        """Opening: the shade darkens and the card fades in rising 18 px, eased out. Closing: both fade out."""
        t = frame / self.FRAMES
        ease = 1 - (1 - t) ** 3
        amount = ease if opening else 1 - ease
        try:
            self.shade.attributes('-alpha', self.DIM * amount)
            self.window.attributes('-alpha', amount)
            if opening:
                self.window.geometry(f'+{self.x}+{self.y + round(18 * (1 - ease))}')
        except tk.TclError:
            return
        if frame < self.FRAMES:
            self.window.after(self.FRAME_MS, self.animate, frame + 1, opening)
        elif not opening:
            self.shade.destroy()
            self.window.destroy()
            if getattr(self.launcher, 'card', None) is self:
                self.launcher.card = None
            if getattr(self, 'on_closed', None):
                self.on_closed()

    def nudge(self, step=0):
        """A click on the dimmed launcher: the card shakes a little (it wants an answer first)."""
        offsets = (-8, 8, -6, 6, -3, 3, 0)
        if step < len(offsets) and not self.closing:
            self.window.geometry(f'+{self.x + offsets[step]}+{self.y}')
            self.window.after(30, self.nudge, step + 1)

    def toggle_dont_show(self):
        self.dont_show = not self.dont_show
        self.scene.image('dont:box', theme.checkbox(self.dont_show), 30, self.buttons_y - 11)

    def close(self):
        if self.closing:
            return
        self.closing = True
        if self.dont_show:
            self.launcher.settings['beta_notice_seen'] = True
            self.launcher.store()
        for tab in (self.ok, self.discord):
            tab.forget()
        self.window.grab_release()
        self.animate(0, opening=False)


class SetupWizard(BetaNotice):
    """Set up...: the player's game folder and its 1.28 update made into the one folder LBPPC runs
    (scripts/game_setup.py). Both folders are checked as they're chosen, in plain words; Set up stays grey until the
    plan is sound. The work runs on a thread with a progress bar; Cancel stops between files (running setup again
    finishes the job). A card over the dimmed launcher, as the beta notice."""

    W = 660
    INTRO = N_('LBPPC runs LittleBigPlanet 3 version 1.28 (US, CUSA00473) from one folder. Choose your game and its '
             '1.28 update: LBPPC puts the update into your game folder for you, and adds any DLC you have. Choose '
             'folders you extracted yourself (a .pkg must be extracted first).')
    FIELD_W = 440

    def __init__(self, launcher, on_closed=None):
        self.launcher, self.closing, self.on_closed = launcher, False, on_closed
        self.running, self.stop_requested, self.delete_update, self.plan = False, False, False, None
        self.events = queue.Queue()
        card, self.rows, self.buttons_y = self.draw()
        scene = self.open_window(card)
        window = self.window

        # The folders: a field, Browse..., and a line saying what was found. DLC is optional.
        game = launcher.game.get().strip()
        root = game_setup.find_root(game) if game else None
        update = game_setup.find_update(root)
        self.folders, self.notes, self.tabs = {}, {}, []
        for key, y, value, title in (('game', self.rows[0], game, t('Game folder (holding eboot.bin)')),
                                     ('update', self.rows[1], str(update or ''), t('Update folder (the 1.28 update)')),
                                     ('dlc', self.rows[2], '', t('DLC folder (one extracted DLC, or a folder of them)'))):
            var = tk.StringVar(value=value)
            var.trace_add('write', lambda *_a: self.schedule_check())
            entry = field(window, var)
            scene.widget(entry, 30, y, self.FIELD_W, 30)
            self.tabs.append(Tab(scene, t('Browse...'), lambda k=key, title=title: self.browse(k, title), self.W - 30, y + 15,
                                 'e', size=14, tilt={'game': 3, 'update': -3, 'dlc': 2}[key]))
            self.folders[key] = var
            self.notes[key] = scene.create_text(32, y + 38, text='', font=HINT_FONT, fill=MUTED, anchor='nw',
                                                width=self.W - 60)
        self.entries = [w for w in window.winfo_children() if isinstance(w, tk.Entry)]

        # Delete the update files afterwards: then they are moved (instant on the same drive, no extra space).
        y = self.rows[3]
        box = scene.image('delete:box', theme.checkbox(False), 30, y)
        label = scene.image('delete:label', theme.text_image(t('Delete the update files when done'), 13, TEXT,
                                                             'Medium'), 60, y + 1)
        self.delete_hint = scene.create_text(62, y + 26, text=t('Faster, and needs no extra space. Off: the update '
                                                                'folder is copied and left as it is.'),
                                             font=HINT_FONT, fill=MUTED,
                                             anchor='nw')
        for item in (box, label):
            scene.tag_bind(item, '<Button-1>', lambda _e: self.toggle_delete())
            scene.tag_bind(item, '<Enter>', lambda _e: scene.configure(cursor='hand2'))
            scene.tag_bind(item, '<Leave>', lambda _e: scene.configure(cursor=''))

        # What will happen (or what's wrong), and the progress bar while it runs.
        y = self.rows[4]
        self.summary = scene.create_text(30, y, text='', font=('Segoe UI Semibold', 10), fill=TEXT, anchor='nw',
                                         width=self.W - 60)
        self.trough = scene.create_rectangle(30, y + 44, self.W - 30, y + 58, fill=theme.FIELD,
                                             outline=theme.FIELD_EDGE, state='hidden')
        self.bar = scene.create_rectangle(30, y + 44, 30, y + 58, fill=theme.hex_colour(theme.LIME), outline='',
                                          state='hidden')

        self.go = Tab(scene, t('Set up'), self.start, self.W - 30, self.buttons_y, 'e', size=16)
        self.files_button = Tab(scene, t('Check files'), self.check_files, 30, self.buttons_y, 'w', size=14, tilt=-2,
                                fill=BLURPLE)
        self.cancel = Tab(scene, t('Cancel'), self.close, self.go.cx - self.go.width / 2 - 16, self.buttons_y, 'e',
                          size=15, fill=theme.GREY)
        self.tabs += [self.go, self.cancel, self.files_button]
        self.check_after = None
        self.check()
        self.present()

    def draw(self):
        """The card, and the y of each part: the three folders, the checkbox, the summary; then the buttons'."""
        w, pad = self.W, 30
        title = theme.text_image(t('Set up your game'), 26, theme.hex_colour(theme.ORANGE), 'Bold', outline=theme.CREAM)
        title = theme.shadowed(title.rotate(1.5, resample=Image.BICUBIC, expand=True), 4, (0, 2))[0]
        intro = theme.paragraph(t(self.INTRO), 14, w - 2 * pad, TEXT, 'Regular')
        y = 44
        parts = [(title, pad - 10, y - 14)]
        y += title.height - 14
        parts.append((intro, pad, y))
        y += intro.height + 18
        rows = []
        for number, heading in ((1, t('Your game folder')), (2, t('The 1.28 update folder')),
                                (3, t('Your DLC (optional)'))):
            parts.append((theme.badge(number), pad, y - 3))
            parts.append((theme.text_image(heading, 15, theme.hex_colour(theme.LIME), 'SemiBold'), pad + 36, y))
            y += 30
            rows.append(y)
            y += 30 + 52  # the field, then the line under it
        rows.append(y)  # the checkbox
        y += 58
        rows.append(y)  # the summary and progress bar
        y += 76
        buttons_y = y
        card = theme.notice_card(w, buttons_y + 40, 'Setup')
        for image, x, top in parts:
            card.alpha_composite(image, (int(x), int(top)))
        return card, rows, buttons_y

    def browse(self, key, title):
        if self.running:
            return
        start = self.folders[key].get().strip() or self.folders['game'].get().strip() or None
        # The window's grab is let go while the folder dialog is open (it belongs to the launcher).
        self.window.grab_release()
        folder = filedialog.askdirectory(title=title, initialdir=start, parent=self.window)
        self.bring_to_front()
        if folder:
            self.folders[key].set(folder)
            if key == 'game' and not self.folders['update'].get().strip():
                update = game_setup.find_update(game_setup.find_root(folder))
                if update:
                    self.folders['update'].set(str(update))

    def toggle_delete(self):
        if self.running:
            return
        self.delete_update = not self.delete_update
        self.scene.image('delete:box', theme.checkbox(self.delete_update), 30, self.rows[3])
        self.schedule_check()

    def schedule_check(self):
        if self.check_after:
            self.window.after_cancel(self.check_after)
        self.check_after = self.window.after(300, self.check)

    def note(self, key, text, colour):
        self.scene.itemconfigure(self.notes[key], text=text, fill=colour)

    def check(self):
        """Each folder's line, then the plan (or why there can't be one), and Set up on only when it's sound."""
        self.check_after = None
        if self.running:
            return
        game_text, update_text, dlc_text = (self.folders[k].get().strip() for k in ('game', 'update', 'dlc'))
        game = game_setup.Folder(game_text) if game_text else None
        update = game_setup.Folder(update_text) if update_text else None
        for key, folder in (('game', game), ('update', update)):
            if not folder:
                self.note(key, t('Not chosen yet.') if key == 'game' else
                          t('Not needed if your game is already version 1.28.'), MUTED)
                continue
            problem = folder.problem(key)
            if problem and not (key == 'update' and folder.root):
                self.note(key, problem, ERROR)
            else:
                inner = folder.root.relative_to(folder.chosen).as_posix() if folder.root != folder.chosen else ''
                note, colour = t('Found: {what}', what=folder.describe()) + (
                    t(' (in {folder})', folder=inner) if inner else ''), GOOD
                if key == 'game' and folder.version == game_setup.VERSION:  # set up already: its files checked
                    files = quick_files(folder.root)
                    if files and not files.ok:
                        note, colour = files.summary(False), WARN
                self.note(key, note, colour)
        # DLC: each one found by its label (LBPDLCSONYCO0001), or why the folder isn't DLC.
        if not dlc_text:
            self.note('dlc', t('Optional. Choose an extracted DLC, or a folder holding several.'), MUTED)
        else:
            try:
                found = game_setup.find_dlc(dlc_text)
                names = ', '.join(dlc.label for dlc in found[:4]) + (
                    t(' and {count} more', count=len(found) - 4) if len(found) > 4 else '')
                self.note('dlc', t('Found {count} DLC: {names}', count=len(found), names=names), GOOD)
            except game_setup.SetupError as error:
                self.note('dlc', str(error), ERROR)
            except OSError as error:
                self.note('dlc', t('Can\'t read that folder: {error}', error=error), ERROR)
        for entry in self.entries:  # the end of a long path: the folder's own name
            if entry is not self.window.focus_get():
                entry.xview_moveto(1)
        folder_problem = any(self.scene.itemcget(self.notes[k], 'fill') == ERROR for k in self.notes)
        self.plan = None
        try:
            plan = game_setup.Plan(game_text, update_text or None, move=self.delete_update,
                                   dlc_folder=dlc_text or None, user_dir=USER) if game_text else None
            if plan:
                plan.check_space()
                self.plan = plan
                self.scene.itemconfigure(self.summary, text=plan.summary() + ('.' if plan.steps else ''),
                                         fill=TEXT if plan.steps else GOOD)
            else:
                self.scene.itemconfigure(self.summary, text=t('Choose your game folder to begin.'), fill=MUTED)
        except game_setup.SetupError as error:
            # A folder's own problem is already on its line; the summary doesn't say it again.
            self.scene.itemconfigure(self.summary, text=t('Fix the folder marked in red above.') if folder_problem
                                     else str(error), fill=WARN)
        except OSError as error:
            self.scene.itemconfigure(self.summary, text=t('Can\'t read the folders: {error}', error=error), fill=ERROR)
        ready = bool(self.plan)
        self.go.set_enabled(ready)
        if self.plan and not self.plan.steps:
            self.go_text(t('Use this folder'))
        else:
            self.go_text(t('Set up'))

    def check_files(self):
        """The full file check (every byte, about a minute) of the game folder, on a thread with the progress bar."""
        if self.running:
            return
        game = game_setup.Folder(self.folders['game'].get().strip())
        if not game.root:
            self.scene.itemconfigure(self.summary, text=t('Choose your game folder to begin.'), fill=WARN)
            return
        self.running, self.stop_requested = True, False
        for tab in self.tabs:
            if tab is not self.cancel:
                tab.set_enabled(False)
        self.scene.itemconfigure(self.trough, state='normal')
        self.scene.itemconfigure(self.bar, state='normal')
        events = queue.Queue()

        def work():
            try:
                result = game_files.check(game.root, full=True, progress=lambda d, total: events.put((d, total)),
                                          cancelled=lambda: self.stop_requested)
                events.put(('done', result))
            except (OSError, ValueError) as error:
                events.put(('done', error))
        threading.Thread(target=work, daemon=True).start()

        def pump():
            last = None
            while True:
                try:
                    event = events.get_nowait()
                except queue.Empty:
                    break
                if event[0] == 'done':
                    self.running = False
                    for tab in self.tabs:
                        tab.set_enabled(True)
                    self.scene.itemconfigure(self.trough, state='hidden')
                    self.scene.itemconfigure(self.bar, state='hidden')
                    self.check()
                    result = event[1]
                    if result is None:
                        text, colour = t('File check stopped.'), WARN
                    elif isinstance(result, Exception):
                        text, colour = t('The files could not be checked: {error}', error=result), ERROR
                    else:
                        _FILES_CHECKED[str(game.root)] = result
                        text, colour = result.summary(True), GOOD if result.ok else ERROR
                    self.scene.itemconfigure(self.summary, text=text, fill=colour)
                    return
                last = event
            if last:
                done, total = last
                self.scene.coords(self.bar, 30, self.rows[4] + 44, 30 + (self.W - 60) * done / max(total, 1),
                                  self.rows[4] + 58)
                self.scene.itemconfigure(self.summary, fill=TEXT, text=t('Checking the game files... {percent}%',
                                                                         percent=done * 100 // max(total, 1)))
            self.window.after(100, pump)
        self.window.after(100, pump)

    def go_text(self, text):
        """The main button's label: Set up, Use this folder (nothing to do), or Done."""
        if getattr(self, 'go_label', t('Set up')) == text:
            return
        self.go_label = text
        self.go.forget()
        self.scene.delete(self.go.item)
        self.scene.images.pop(self.go.key, None)
        self.tabs.remove(self.go)
        enabled = self.go.enabled
        self.go = Tab(self.scene, text, self.start, self.W - 30, self.buttons_y, 'e', size=16)
        self.go.set_enabled(enabled)
        self.tabs.append(self.go)
        # Cancel beside the new label, which can be longer ("Use this folder", and in other languages)
        self.cancel.move(self.go.cx - self.go.width / 2 - 16, self.buttons_y, 'e')

    def start(self):
        if self.running or not self.plan:
            return
        if not self.plan.steps:
            self.finish(True, '')
            return
        if self.delete_update and self.plan.update:
            sure = messagebox.askyesno(
                t('Delete the update files?'),
                t('When setup is done, this folder is deleted:\n\n{folder}\n\nYour game folder keeps everything it '
                  'needs. Go ahead?', folder=self.plan.update.root), parent=self.window)
            self.bring_to_front()
            if not sure:
                return
        self.running, self.stop_requested = True, False
        for entry in self.entries:
            entry.configure(state='disabled')
        for tab in self.tabs:
            if tab is not self.cancel:
                tab.set_enabled(False)
        self.scene.itemconfigure(self.trough, state='normal')
        self.scene.itemconfigure(self.bar, state='normal')
        plan, delete = self.plan, self.delete_update

        def work():
            try:
                done = game_setup.run(plan, lambda d, t, name: self.events.put(('progress', d, t, name)),
                                      lambda: self.stop_requested)
                if done and delete:
                    self.events.put(('progress', 1, 1, t('Deleting the update folder...')))
                    game_setup.remove_update(plan)
                self.events.put(('done', done, ''))
            except game_setup.SetupError as error:
                self.events.put(('done', False, str(error)))
            except OSError as error:
                self.events.put(('done', False, t('Setup stopped: {error} ({file}). Close any program using the game '
                                                  'files and try again.', error=error.strerror or error,
                                                  file=error.filename or '')))
        threading.Thread(target=work, daemon=True).start()
        self.window.after(50, self.pump)

    def pump(self):
        last = None
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == 'done':
                self.finish(event[1], event[2])
                return
            last = event
        if last:
            _kind, done, total, name = last
            self.scene.coords(self.bar, 30, self.rows[4] + 44, 30 + (self.W - 60) * done / max(total, 1),
                              self.rows[4] + 58)
            self.scene.itemconfigure(self.summary, text=f'{done * 100 // max(total, 1)}%  {name}', fill=TEXT)
        self.window.after(50, self.pump)

    def finish(self, ok, message):
        self.running = False
        if ok:
            # The launcher takes the folder that was set up (the one holding eboot.bin).
            _FILES_CHECKED.pop(str(self.plan.root), None)  # its files changed: checked again
            self.launcher.game.set(str(self.plan.root))
            self.launcher.refresh_game()
            self.close(force=True)
            return
        for entry in self.entries:
            entry.configure(state='normal')
        for tab in self.tabs:
            tab.set_enabled(True)
        self.scene.itemconfigure(self.trough, state='hidden')
        self.scene.itemconfigure(self.bar, state='hidden')
        self.check()
        self.scene.itemconfigure(self.summary, fill=ERROR if message else WARN, text=message or
                                 t('Stopped. Your game still reads as its old version: click Set up to finish.'))

    def close(self, force=False):
        if self.running and not force:
            self.stop_requested = True  # stops between files; finish() then shows where it got to
            return
        if self.closing:
            return
        self.closing = True
        for tab in self.tabs:
            tab.forget()
        self.window.grab_release()
        self.animate(0, opening=False)


class UpdateNotice(BetaNotice):
    """A newer LBPPC on GitHub (scripts/updater.py): what's new, and Update, which downloads it (its SHA-256 checked),
    unpacks it, and has the new version install itself once the launcher has closed. Later asks again next start.
    Saves, settings, controls and the prepared game stay."""

    W = 600

    def __init__(self, launcher, release):
        self.launcher, self.closing, self.on_closed, self.release = launcher, False, None, release
        self.busy, self.stop_requested = False, False
        self.events = queue.Queue()
        card, self.status_y, self.buttons_y = self.draw()
        scene = self.open_window(card)
        self.status = scene.create_text(30, self.status_y, text=t('Your saves, settings and controls are kept.'),
                                        font=HINT_FONT, fill=MUTED, anchor='nw', width=self.W - 60)
        self.trough = scene.create_rectangle(30, self.status_y + 24, self.W - 30, self.status_y + 36,
                                             fill=theme.FIELD, outline=theme.FIELD_EDGE, state='hidden')
        self.bar = scene.create_rectangle(30, self.status_y + 24, 30, self.status_y + 36,
                                          fill=theme.hex_colour(theme.LIME), outline='', state='hidden')
        self.go = Tab(scene, t('Update'), self.start, self.W - 30, self.buttons_y, 'e', size=16)
        self.later = Tab(scene, t('Later'), self.close, self.go.cx - self.go.width / 2 - 16, self.buttons_y, 'e',
                         size=15, fill=theme.GREY)
        self.news = Tab(scene, t("What's new"), lambda: webbrowser.open(release.page), 30, self.buttons_y, 'w', size=14,
                        tilt=-2, fill=BLURPLE)
        self.tabs = [self.go, self.later, self.news]
        self.window.bind('<Return>', lambda _e: self.start())
        self.present()

    @staticmethod
    def plain(notes, limit=520):
        """Release notes (Markdown) as a few plain lines for the card."""
        lines = []
        for line in notes.splitlines():
            if line.lstrip().startswith('#'):
                continue  # a heading ("What's new" is the card's own)
            line = re.sub(r'[*_`>]+', '', re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', line)).strip()
            line = re.sub(r'^[-+]\s*', '', line)
            if line:
                lines.append(line.rstrip('.'))
        text = '  •  '.join(lines)
        return text if len(text) <= limit else text[:limit].rsplit(' ', 1)[0] + '...'

    def draw(self):
        w, pad = self.W, 30
        title = theme.text_image(t('LBPPC {version} is out!', version=self.release.version), 26, theme.hex_colour(theme.ORANGE), 'Bold',
                                 outline=theme.CREAM)
        title = theme.shadowed(title.rotate(1.5, resample=Image.BICUBIC, expand=True), 4, (0, 2))[0]
        intro = theme.paragraph(t('You have {version}. Updating takes a minute or two: LBPPC downloads it, closes, '
                                  'and opens again on the new version.', version=VERSION), 14, w - 2 * pad, TEXT,
                                'Regular')
        y = 44
        parts = [(title, pad - 10, y - 14)]
        y += title.height - 14
        parts.append((intro, pad, y))
        y += intro.height + 14
        notes = self.plain(self.release.notes)
        if notes:
            heading = theme.text_image(t("What's new"), 15, theme.hex_colour(theme.LIME), 'SemiBold')
            parts.append((heading, pad, y))
            y += heading.height + 8
            body = theme.paragraph(notes, 13, w - 2 * pad, MUTED, 'Regular')
            parts.append((body, pad, y))
            y += body.height + 16
        status_y = y
        buttons_y = status_y + 70
        card = theme.notice_card(w, buttons_y + 40, 'Update')
        for image, x, top in parts:
            card.alpha_composite(image, (int(x), int(top)))
        return card, status_y, buttons_y

    def say(self, text, colour=MUTED):
        self.scene.itemconfigure(self.status, text=text, fill=colour)

    def start(self):
        if self.busy:
            return
        if self.launcher.process:
            self.say(t('Close the game first: the update replaces its files.'), WARN)
            return
        self.busy, self.stop_requested = True, False
        self.go.set_enabled(False)
        self.news.set_enabled(False)
        self.scene.itemconfigure(self.trough, state='normal')
        self.scene.itemconfigure(self.bar, state='normal')
        self.say(t('Downloading LBPPC {version}...', version=self.release.version), TEXT)
        folder = ROOT / 'out' / 'update'

        def work():
            try:
                path = updater.download(self.release, folder,
                                        lambda done, total: self.events.put(('progress', done, total)),
                                        lambda: self.stop_requested)
                if not path:
                    self.events.put(('stopped',))
                    return
                self.events.put(('unpacking',))
                staging = updater.stage(path, folder / 'staging')
                self.events.put(('ready', staging))
            except (updater.UpdateError, OSError, zipfile_errors()) as error:
                self.events.put(('failed', str(error)))
        threading.Thread(target=work, daemon=True).start()
        self.window.after(100, self.pump)

    def pump(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == 'progress':
                done, total = event[1], max(event[2], 1)
                self.scene.coords(self.bar, 30, self.status_y + 24, 30 + (self.W - 60) * min(done / total, 1),
                                  self.status_y + 36)
                self.say(t('Downloading LBPPC {version}... {done} of {total} MB', version=self.release.version,
                           done=done >> 20, total=total >> 20), TEXT)
            elif kind == 'unpacking':
                self.say(t('Checked. Unpacking...'), TEXT)
            elif kind == 'ready':
                self.say(t('Installing: LBPPC closes and opens again in a moment.'), GOOD)
                try:
                    updater.start_apply(event[1], ROOT)
                except OSError as error:
                    self.fail(t('The new version could not be started: {error}', error=error))
                    return
                self.window.after(300, self.launcher.close)
                return
            elif kind in ('failed', 'stopped'):
                self.fail(event[1] if kind == 'failed' else t('Update cancelled.'))
                return
        self.window.after(100, self.pump)

    def fail(self, message):
        self.busy = False
        self.go.set_enabled(True)
        self.news.set_enabled(True)
        self.scene.itemconfigure(self.trough, state='hidden')
        self.scene.itemconfigure(self.bar, state='hidden')
        self.say(message, ERROR)

    def close(self, force=False):
        if self.busy and not force:
            self.stop_requested = True  # the download stops; fail() then says so
            return
        if self.closing:
            return
        self.closing = True
        for tab in self.tabs:
            tab.forget()
        self.window.grab_release()
        self.animate(0, opening=False)


def zipfile_errors():
    import zipfile
    return zipfile.BadZipFile


class TitleBar:
    """Windows: the window's own title bar (drag to move, minimise, close), the system's taken away. The window keeps
    its taskbar button (WS_EX_APPWINDOW)."""

    def __init__(self, window, scene, subtitle, on_close, minimise=True):
        self.window = window
        width = int(scene['width'])
        scene.image('titlebar', theme.title_bar(width, TITLE_HEIGHT, subtitle), 0, 0, tags=('titlebar',))
        close, margin = theme.window_button('close', theme.RED)
        x = width - 12 - (close.width - 2 * margin)
        item = scene.image('title:close', close, x - margin, (TITLE_HEIGHT - close.height) // 2)
        self.button(scene, item, on_close)
        if minimise:
            low, margin = theme.window_button('min', theme.BLUE)
            x -= 8 + (low.width - 2 * margin)
            item = scene.image('title:min', low, x - margin, (TITLE_HEIGHT - low.height) // 2)
            self.button(scene, item, self.minimise)
        scene.tag_bind('titlebar', '<ButtonPress-1>', self.grab)
        scene.tag_bind('titlebar', '<B1-Motion>', self.drag)
        window.overrideredirect(True)
        window.after(50, self.show_in_taskbar)

    @staticmethod
    def button(scene, item, command):
        scene.tag_bind(item, '<Button-1>', lambda _e: command())
        scene.tag_bind(item, '<Enter>', lambda _e: scene.configure(cursor='hand2'))
        scene.tag_bind(item, '<Leave>', lambda _e: scene.configure(cursor=''))

    def hwnd(self):
        """The outer window Windows manages (Tk's frame around the toplevel)."""
        return int(self.window.wm_frame(), 16)

    def show_in_taskbar(self):
        import ctypes
        if not self.window.winfo_ismapped():  # Tk sets its own styles when it first shows the window
            self.window.after(50, self.show_in_taskbar)
            return
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(self.hwnd(), -20)  # GWL_EXSTYLE
        user32.SetWindowLongW(self.hwnd(), -20, (style & ~0x80) | 0x40000)  # not WS_EX_TOOLWINDOW, WS_EX_APPWINDOW
        self.animate_minimise()
        # Shown again, so the taskbar picks the changed style up.
        self.window.withdraw()
        self.window.after(10, self.window.deiconify)
        self.window.after(20, self.round_corners)

    def animate_minimise(self):
        """Windows animates minimising and restoring only for a window with a caption (WS_CAPTION, WS_SYSMENU,
        WS_MINIMIZEBOX), not a borderless popup. The window gets those styles, and its own window procedure
        answers WM_NCCALCSIZE with \"no frame\", so the caption takes no room and nothing of it is drawn."""
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        result = ctypes.c_ssize_t
        procedure = ctypes.WINFUNCTYPE(result, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                           wintypes.LPARAM]
        user32.CallWindowProcW.restype = result
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        hwnd = self.hwnd()

        def window_procedure(window, message, wparam, lparam):
            if message == 0x83 and wparam:  # WM_NCCALCSIZE: all of the window is its client area
                return 0
            return user32.CallWindowProcW(self.tk_procedure, window, message, wparam, lparam)

        self.procedure = procedure(window_procedure)  # kept: Windows calls it as long as the window lives
        self.tk_procedure = user32.SetWindowLongPtrW(hwnd, -4, ctypes.cast(self.procedure, ctypes.c_void_p))
        style = user32.GetWindowLongW(hwnd, -16) & 0xffffffff  # GWL_STYLE
        style = (style & ~0x80000000) | 0xC00000 | 0x80000 | 0x20000  # not WS_POPUP; caption, menu, minimise box
        user32.SetWindowLongW(hwnd, -16, ctypes.c_long(style - (1 << 32) if style >= 1 << 31 else style))
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x27)  # SWP_FRAMECHANGED, nothing moved

    def round_corners(self):
        """Rounded corners: Windows 11 draws them itself (smooth, with its border and shadow); on Windows 10 the
        window is cut to a rounded shape, again each time its size changes (the log shown or hidden)."""
        import ctypes
        preference = ctypes.c_int(2)  # DWMWCP_ROUND
        if ctypes.windll.dwmapi.DwmSetWindowAttribute(self.hwnd(), 33, ctypes.byref(preference),  # CORNER_PREFERENCE
                                                      ctypes.sizeof(preference)) == 0:
            return
        cut_size = None
        def cut(_event=None):
            # Only for a new size: setting the region sends the window a <Configure> of its own, and cutting
            # again for that one never ends (the launcher froze on Windows 10).
            nonlocal cut_size
            w, h = self.window.winfo_width(), self.window.winfo_height()
            if (w, h) == cut_size:
                return
            cut_size = (w, h)
            region = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, 16, 16)
            ctypes.windll.user32.SetWindowRgn(self.hwnd(), region, True)
        cut()
        self.window.bind('<Configure>', lambda e: e.widget is self.window and cut(), add='+')

    def minimise(self):
        import ctypes
        ctypes.windll.user32.ShowWindow(self.hwnd(), 6)  # SW_MINIMIZE

    def grab(self, event):
        self.offset = (event.x_root - self.window.winfo_x(), event.y_root - self.window.winfo_y())

    def drag(self, event):
        self.window.geometry(f'+{event.x_root - self.offset[0]}+{event.y_root - self.offset[1]}')


def style_widgets(root):
    """The fields and dropdowns: dark, light text, a lime edge on the focused one."""
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('Night.TCombobox', padding=(ui_scale.s(4), max(1, ui_scale.s(4) - 2)), arrowcolor=theme.hex_colour(theme.LIME), foreground=TEXT,
                    background=theme.FIELD, fieldbackground=theme.FIELD, bordercolor=theme.FIELD_EDGE,
                    lightcolor=theme.FIELD, darkcolor=theme.FIELD, selectbackground=theme.FIELD,
                    selectforeground=TEXT, arrowsize=ui_scale.s(14))
    style.map('Night.TCombobox', fieldbackground=[('readonly', theme.FIELD)], foreground=[('readonly', TEXT)],
              background=[('active', theme.FIELD_EDGE)], bordercolor=[('focus', theme.hex_colour(theme.LIME))])
    style.configure('Night.Vertical.TScrollbar', background=theme.FIELD, troughcolor=BG,
                    arrowcolor=MUTED, borderwidth=0)
    root.option_add('*TCombobox*Listbox.background', theme.FIELD)
    root.option_add('*TCombobox*Listbox.foreground', TEXT)
    root.option_add('*TCombobox*Listbox.selectBackground', theme.hex_colour(theme.BLUE))
    root.option_add('*TCombobox*Listbox.selectForeground', '#ffffff')
    root.option_add('*TCombobox*Listbox.font', UI_FONT)


def field(parent, variable, hidden=False):
    return tk.Entry(parent, textvariable=variable, show='*' if hidden else '', font=UI_FONT, bg=theme.FIELD,
                    fg=TEXT, insertbackground=TEXT, relief='flat', highlightthickness=1,
                    highlightbackground=theme.FIELD_EDGE, highlightcolor=theme.hex_colour(theme.LIME),
                    disabledbackground=theme.FIELD)


class Launcher(tk.Tk):
    # Layout: the title bar, the banner, three panels and the friends list beside them, the bar with Play.
    PANEL_X, PANEL_W = 28, MAIN_WIDTH - 56
    SIDE_X = PANEL_X + PANEL_W + 20
    SIDE_W = WIDTH - 28 - SIDE_X
    GAME_Y = TITLE_HEIGHT + BANNER_HEIGHT + 12
    GRAPHICS_Y = GAME_Y + 118
    ONLINE_Y = GRAPHICS_Y + 380  # the settings panel: four rows
    BAR_Y = ONLINE_Y + 216
    HEIGHT = BAR_Y + 92

    def __init__(self):
        super().__init__()
        ui_scale.fonts(self)
        self.title('LBPPC')
        self.set_icon()
        self.configure(bg=BG)
        self.resizable(False, False)
        self.settings = load_settings()
        # The launcher in the game's language (i18n.py): set before anything is drawn.
        i18n.set_language(self.game_language())
        self.process = None
        self.card = None  # the notice, wizard or update card on screen
        # The launcher brought to the front (a dialog closing, its taskbar button) while a card is open: the card
        # over it again, or the launcher would sit on top of the card that has the input and look frozen.
        for event in ('<Activate>', '<FocusIn>'):
            self.bind(event, lambda e: self.card and e.widget is self and self.after_idle(self.card.bring_to_front),
                      add='+')
        self.lines = queue.Queue()
        self.log_shown = False
        style_widgets(self)
        self.scene = Scene(self, WIDTH, self.HEIGHT)
        self.scene.pack(fill='both', expand=True)
        self.scene.image('felt', theme.felt(WIDTH, self.HEIGHT + LOG_HEIGHT), 0, 0)
        self.build()
        if CUSTOM_FRAME:
            TitleBar(self, self.scene, t('{version} beta', version=VERSION), self.close)
        self.friends = FriendsPanel(self, self.SIDE_X, self.GAME_Y, self.SIDE_W, self.ONLINE_Y + 202 - self.GAME_Y)
        self.controls = ControlsPage(self, self.PANEL_X, self.GAME_Y, self.PANEL_W, self.ONLINE_Y + 202 - self.GAME_Y)
        self.debug = DebugPage(self, self.PANEL_X, self.GAME_Y, self.PANEL_W, self.ONLINE_Y + 202 - self.GAME_Y)
        # The pages: Game (the panels built above), Controls and Debug, switched by the tabs on the banner.
        tab_y = TITLE_HEIGHT + BANNER_HEIGHT - 16
        self.page_tabs = {'debug': Tab(self.scene, t('Debug'), lambda: self.show_page('debug'),
                                       self.PANEL_X + self.PANEL_W - 4, tab_y, 'e', size=14, tilt=-3)}
        self.page_tabs['controls'] = Tab(self.scene, t('Controls'), lambda: self.show_page('controls'),
                                         self.page_tabs['debug'].cx - self.page_tabs['debug'].width / 2 - 14,
                                         tab_y, 'e', size=14, tilt=3)
        self.page_tabs['game'] = Tab(self.scene, t('Game'), lambda: self.show_page('game'),
                                     self.page_tabs['controls'].cx - self.page_tabs['controls'].width / 2 - 14,
                                     tab_y, 'e', size=14, tilt=-3)
        self.show_page('game')
        self.refresh_game()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.pump)
        # Python's garbage collection runs on whichever thread happens to allocate. On a background thread (the
        # game's log reader, the friends list) it could free Tk images there, and Tcl's memory then gets
        # corrupted ("alloc: invalid block", the launcher closing). Collected here, on Tk's thread, instead.
        gc.disable()
        self.after(5000, self.collect_garbage)
        self.after(600, self.first_start)
        self.after_update()
        # A newer LBPPC on GitHub (scripts/updater.py): asked in the background, offered once nothing else is open.
        self.update_found = None
        if FROZEN or os.environ.get('LBPPC_UPDATE_CHECK') == '1':
            threading.Thread(target=self.check_for_update, daemon=True).start()
            self.after(1500, self.offer_update)
        self.scrub = log_privacy.Scrubber()
        self.log_cleaner = None
        if not self.settings.get('logs_scrubbed') and not log_privacy.raw_logs():
            # Logs from before 0.1.1 were written with names and IP addresses in them: cleaned once, in the background.
            self.log_cleaner = threading.Thread(target=scrub_old_logs, args=(dict(self.settings),), daemon=True)
            self.log_cleaner.start()

            def cleaned():  # on Tk's thread
                if self.log_cleaner.is_alive():
                    self.after(500, cleaned)
                else:
                    self.settings['logs_scrubbed'] = True
                    self.store()
            self.after(500, cleaned)

    def check_for_update(self):
        """On a thread: the newest release, when newer (offline or GitHub refusing: nothing is said)."""
        try:
            self.update_found = updater.check(VERSION) or False
        except updater.UpdateError:
            self.update_found = False

    def offer_update(self):
        """The update card once the check has answered, no other card is open and the game isn't running."""
        cleaning = self.update_cleaner and self.update_cleaner.is_alive()  # it empties out/update/, the download's
        if self.update_found is None or self.card or self.process or cleaning:
            self.after(1000, self.offer_update)
        elif self.update_found:
            UpdateNotice(self, self.update_found)

    def after_update(self):
        """An update just installed (or failed and put the old version back): said in the bar; its leftovers go."""
        self.update_cleaner = None
        if FROZEN:
            self.update_cleaner = threading.Thread(target=updater.clean_up, args=(ROOT,), daemon=True)
            self.update_cleaner.start()
        if os.environ.pop('LBPPC_UPDATED', None):
            self.set_state(t('Updated to LBPPC {version}', version=VERSION), GOOD)
        failed = os.environ.pop('LBPPC_UPDATE_FAILED', None)
        if failed:
            self.set_state(t('The update failed; still on {version}', version=VERSION), ERROR)
            self.append([f'Launcher: the update failed: {failed}'])

    def collect_garbage(self):
        gc.collect()
        self.after(5000, self.collect_garbage)

    def fit(self, text, width, font=HINT_FONT):
        """`text` cut with an ellipsis to `width` pixels (a hint that must stay on one line, in any language)."""
        measure = tkfont.Font(root=self, font=font).measure
        limit = ui_scale.s(width)
        if measure(text) <= limit:
            return text
        while text and measure(text + '\u2026') > limit:
            text = text[:-1]
        return text.rstrip() + '\u2026'

    def text(self, x, y, text='', font=HINT_FONT, fill=MUTED, anchor='nw', width=0):
        options = {'width': width} if width else {}
        return self.scene.create_text(x, y, text=text, font=font, fill=fill, anchor=anchor, **options)

    def label(self, key, x, y, text):
        self.scene.image(key, theme.text_image(text, 13, TEXT, 'Medium'), x, y)

    def build(self):
        scene, x, w = self.scene, self.PANEL_X, self.PANEL_W
        half = (w - 40) // 2 - 10
        right = x + 20 + half + 20

        before = set(scene.find_all())
        # Game folder
        y = self.GAME_Y
        scene.image('panel:game', theme.panel(w, 104, t('Game folder')), x, y)
        self.game = tk.StringVar(value=self.settings['game'])
        entry = field(self, self.game)
        entry.bind('<FocusOut>', lambda _e: self.refresh_game())
        entry.bind('<Return>', lambda _e: self.refresh_game())
        scene.widget(entry, x + 20, y + 46, w - 300, 30)
        browse = Tab(scene, t('Browse...'), self.browse, x + w - 20, y + 61, 'e', size=14, tilt=3)
        # The wizard: the game and its 1.28 update made into one folder (SetupWizard, scripts/game_setup.py).
        Tab(scene, t('Set up...'), self.open_setup, browse.cx - browse.width / 2 - 12, y + 61, 'e', size=14, tilt=-2,
            fill=theme.GREEN)
        self.status_tag_y = y + 84

        # Graphics: the choices in two columns
        y = self.GRAPHICS_Y
        scene.image('panel:graphics', theme.panel(w, 366, t('Settings')), x, y)
        self.choices = {}
        for index, (key, title, values, hint) in enumerate(OPTIONS):
            cx, cy = (x + 20, right)[index % 2], y + 44 + (index // 2) * 76
            self.label(f'label:{key}', cx, cy, t(title))
            values = [(t(name), value) for name, value in values]  # shown in the launcher's language
            names = [name for name, _value in values]
            current = next((name for name, value in values if value == self.settings[key]), names[0])
            var = tk.StringVar(value=current)
            box = ttk.Combobox(self, textvariable=var, values=names, state='readonly', font=UI_FONT,
                               style='Night.TCombobox')
            box.bind('<<ComboboxSelected>>', lambda _e, k=key: (self.store(), self.selection_clear(),
                                                                k == 'language' and self.language_changed()))
            scene.widget(box, cx, cy + 22, half, 28)
            self.text(cx + 2, cy + 54, self.fit(t(hint), half))
            self.choices[key] = (var, values)

        # Online: one account (name and password) on the LBPPC server's shadNet (sign-in, friends, multiplayer)
        # and Refresh (levels, profiles).
        y = self.ONLINE_Y
        scene.image('panel:online', theme.panel(w, 202, t('Online')), x, y)
        self.online = tk.BooleanVar(value=bool(self.settings['online']))
        box = scene.image('online:box', theme.checkbox(self.online.get()), x + 20, y + 44)
        scene.tag_bind(box, '<Button-1>', lambda _e: self.toggle_online())
        self.label('label:online', x + 50, y + 45, t('Play online on the LBPPC server'))
        # What you're playing on your Discord profile, while the game runs.
        self.presence_on = tk.BooleanVar(value=bool(self.settings.get('discord_presence', True)))
        self.presence_x = right
        box = scene.image('presence:box', theme.checkbox(self.presence_on.get()), right, y + 44)
        scene.tag_bind(box, '<Button-1>', lambda _e: self.toggle_presence())
        self.label('label:presence', right + 30, y + 45, t('Show what I\'m playing on Discord'))
        self.label('label:name', x + 20, y + 76, t('Online name'))
        self.label('label:password', right, y + 76, t('Password'))
        self.online_name = tk.StringVar(value=self.settings['online_name'])
        self.password = tk.StringVar(value=self.settings['password'])
        for cx, var, hidden in ((x + 20, self.online_name, False), (right, self.password, True)):
            entry = field(self, var, hidden)
            entry.bind('<FocusOut>', lambda _e: (self.store(), self.friends.account_changed()))
            scene.widget(entry, cx, y + 98, half, 30)
        self.text(x + 22, y + 134, t('3 to 16 letters, digits, - or _, and a password of 8 or more characters. '
                                     'Only your friends can join your game.'))
        self.account_button = Tab(scene, t('Create account'), self.create_account, x + 20, y + 174, size=15, tilt=-3)
        self.account_status = self.text(self.account_button.right + 22, y + 174, t('New here? Choose a name and '
                                        'password, then Create account.'), anchor='w', width=560)

        self.game_items = [item for item in scene.find_all() if item not in before]

        # The bar: Play (the lime tab, as LBP3's selected item), Stop, the state, the links
        y = self.BAR_Y
        self.play_button = Tab(scene, t('Play'), self.play, x + 6, y + 30, size=30, pad=(30, 16), tilt=-4,
                               weight='Bold')
        self.stop_button = Tab(scene, t('Stop'), self.stop, self.play_button.right + 24, y + 30, size=17, pad=(15, 10),
                               tilt=3)
        self.stop_button.set_enabled(False)
        dot = self.stop_button.right + 26
        self.state_dot = scene.create_oval(dot, y + 24, dot + 12, y + 36, fill=MUTED, outline='')
        self.state_label = self.text(dot + 20, y + 30, t('Ready'), ('Segoe UI Semibold', 11), TEXT, 'w', width=200)
        lx = WIDTH - 28
        for text, action in reversed((('Discord', lambda: webbrowser.open(DISCORD)),
                                      (N_('Show log'), self.toggle_log), (N_('Make a report'), self.report),
                                      (N_('Log file'), lambda: LOG.is_file() and open_path(LOG)),
                                      (N_('Import save'), self.import_save),
                                      (N_('Saves'), lambda: open_path(USER)))):
            colour = '#8c9eff' if text == 'Discord' else theme.hex_colour((200, 225, 150))  # Discord's blurple, lighter
            item = self.text(lx, y + 28, t(text), ('Segoe UI Semibold', 10), colour, 'e')
            scene.tag_bind(item, '<Button-1>', lambda _e, act=action: act())
            scene.tag_bind(item, '<Enter>', lambda _e: scene.configure(cursor='hand2'))
            scene.tag_bind(item, '<Leave>', lambda _e: scene.configure(cursor=''))
            if text == 'Show log':
                self.log_link = item
            box = scene.bbox(item)
            lx = box[0] - 18
        self.text(WIDTH // 2, self.HEIGHT - 16, t('Play your own copy. LBPPC includes no game files. '
                                                  'Help, news and co-op partners on the Discord.'),
                  ('Segoe UI', 8), '#6c7690', 'center')

        # The game's output, under the bar while shown
        self.log = tk.Text(self, bg='#0e111c', fg=MUTED, insertbackground=TEXT, relief='flat', font=('Consolas', 9),
                           wrap='none', state='disabled', padx=10, pady=8, highlightthickness=1,
                           highlightbackground=theme.FIELD_EDGE)
        self.log.tag_configure('warn', foreground=WARN)
        self.log.tag_configure('bad', foreground=ERROR)
        self.log_window = None

    def show_page(self, page):
        self.page = page
        tag = self.scene.images.get('game:tag')  # drawn later than the panels (refresh_game)
        for item in self.game_items + ([tag[0]] if tag else []):
            self.scene.itemconfigure(item, state='normal' if page == 'game' else 'hidden')
        for item in self.controls.items:
            self.scene.itemconfigure(item, state='normal' if page == 'controls' else 'hidden')
        for item in self.debug.items:
            self.scene.itemconfigure(item, state='normal' if page == 'debug' else 'hidden')
        for name, tab in self.page_tabs.items():
            tab.select(name == page)
            self.scene.tag_raise(tab.item)

    def draw_banner(self):
        game = self.settings['game']
        art = Path(game) / 'sce_sys' / 'pic1.png' if game else None
        self.scene.image('banner', theme.banner(WIDTH, BANNER_HEIGHT, art), 0, TITLE_HEIGHT)
        # The game's own name (its param.sfo) as the banner's title; LBPPC is the title bar's.
        sfo = read_sfo(Path(game) / 'sce_sys' / 'param.sfo') if game else {}
        # The trademark sign is left out (it joins "LittleBigPlanet" and "3" in the game's own title).
        name = ' '.join(re.sub('[\u2122\u00ae]', ' ', sfo.get('TITLE', '')).split()) or t('Choose your game')
        title = theme.text_image(name, 32, theme.hex_colour(theme.ORANGE), 'Bold', outline=theme.CREAM)
        title = title.rotate(2, resample=Image.BICUBIC, expand=True)
        self.scene.image('banner:title', theme.shadowed(title, 5, (0, 3))[0], 14, TITLE_HEIGHT + BANNER_HEIGHT - 90)
        self.scene.tag_raise('banner:title')
        for tab in getattr(self, 'page_tabs', {}).values():  # the page tabs sit on the banner
            self.scene.tag_raise(tab.item)

    def import_save(self):
        """A decrypted PS4 save into LBPPC (scripts/save_import.py); LBPPC's own save of that kind backed up first."""
        if self.process:
            self.set_state(t('Close the game before importing a save'), WARN)
            return
        folder = filedialog.askdirectory(title=t('Your decrypted LittleBigPlanet 3 save (or a folder of them)'),
                                         parent=self)
        if not folder:
            return
        try:
            saves = save_import.find_saves(folder)
        except save_import.ImportError_ as error:
            messagebox.showerror(t('Import save'), str(error), parent=self)
            return
        what = save_import.describe(saves)
        if not messagebox.askyesno(t('Import save'), t('Import {what} from this folder?\n\nYour current LBPPC save of '
                                                       'the same kind is kept first, in user\\save_backups.',
                                                       what=what), parent=self):
            return
        try:
            backup = save_import.import_saves(saves, USER)
        except OSError as error:
            self.set_state(t('The save could not be imported: {error}', error=error), ERROR)
            return
        self.set_state(t('Save imported: {what}', what=what) + (t(' (the old one is in {folder})', folder=backup.name)
                                                                 if backup else ''), GOOD)

    def game_language(self):
        """The PS4 language number the game gets (and the launcher's language follows): the setting, or Windows'."""
        language = self.settings.get('language', 'auto')
        if language not in {code for _name, code in LANGUAGES}:  # Arabic (21), offered in 0.1.1 only, among others
            language = self.settings['language'] = 'auto'
        return windows_language() if language == 'auto' else language

    def language_changed(self):
        """The launcher draws its text once: a new language takes a restart, done here unless the game is running."""
        if i18n.LOCALES.get(self.game_language(), 'en') == i18n.locale:
            return  # the same launcher language (English US to UK)
        if self.process:
            self.set_state(t('The launcher changes language the next time it starts.'), MUTED)
            return
        command = [sys.executable] if FROZEN else [sys.executable, str(Path(__file__).resolve())]
        try:
            subprocess.Popen(command, cwd=ROOT, close_fds=True)
        except OSError:
            self.set_state(t('The launcher changes language the next time it starts.'), MUTED)
            return
        self.after(200, self.close)

    def toggle_presence(self):
        self.presence_on.set(not self.presence_on.get())
        self.scene.image('presence:box', theme.checkbox(self.presence_on.get()), self.presence_x, self.ONLINE_Y + 44)
        self.settings['discord_presence'] = bool(self.presence_on.get())
        self.store()

    def start_presence(self):
        """On a thread: the game shown on the player's Discord profile (nothing when Discord isn't running)."""
        app_id = self.settings.get('discord_app_id') or DISCORD_APP_ID
        if not (app_id and self.settings.get('discord_presence', True)):
            return
        presence = discord_presence.Presence(app_id)

        def work():
            if presence.connect():
                online = bool(self.settings.get('online'))
                presence.set('LittleBigPlanet 3', t('Online on LBPPC') if online else t('Playing offline'),
                             button=(t('Get LBPPC'), 'https://lbppc.com'))
                self.presence = presence
        threading.Thread(target=work, daemon=True).start()

    def stop_presence(self):
        presence, self.presence = getattr(self, 'presence', None), None
        if presence:
            threading.Thread(target=lambda: (presence.clear(), presence.close()), daemon=True).start()

    def toggle_online(self):
        self.online.set(not self.online.get())
        self.scene.image('online:box', theme.checkbox(self.online.get()), self.PANEL_X + 20, self.ONLINE_Y + 44)
        self.store()

    def set_account_status(self, text, colour):
        self.scene.itemconfigure(self.account_status, text=text, fill=colour)

    def browse(self):
        folder = filedialog.askdirectory(title=t('Game folder (holding eboot.bin)'), initialdir=self.game.get() or None)
        if folder:
            # The folder holding eboot.bin, when a folder above it was chosen (an extraction's Image0 and the like).
            root = game_setup.find_root(folder)
            self.game.set(str(root) if root and root.parent != root else folder)
            if self.refresh_game() == 'setup':
                self.open_setup()

    def open_setup(self):
        if self.process:
            self.set_state(t('Stop the game before setting up its files'), WARN)
            return
        self.store()
        SetupWizard(self)

    def first_start(self):
        """The beta notice until it's turned off, then the setup wizard while there's no game ready to play."""
        def offer_setup():
            if self.winfo_exists() and check_game(self.game.get().strip())[0] in ('setup', 'missing'):
                SetupWizard(self)
        if not self.settings.get('beta_notice_seen'):
            BetaNotice(self, on_closed=offer_setup)
        else:
            offer_setup()

    def refresh_game(self):
        state, text = check_game(self.game.get().strip())
        ok = state in ('ready', 'warn')
        image, margin = theme.tag(text, (70, 140, 40) if state == 'ready' else (190, 110, 20) if state in ('setup', 'warn') else (190, 60, 50))
        item = self.scene.image('game:tag', image, self.PANEL_X + 16 - margin, self.status_tag_y - margin)
        self.scene.itemconfigure(item, state='normal' if getattr(self, 'page', 'game') == 'game' else 'hidden')
        self.play_button.set_enabled(ok and not self.process)
        changed = self.settings['game'] != self.game.get().strip()
        self.store()
        if changed or 'banner' not in self.scene.images:
            self.draw_banner()
        return state

    def store(self):
        self.settings['game'] = self.game.get().strip()
        for key, (var, values) in self.choices.items() if hasattr(self, 'choices') else ():
            self.settings[key] = next(value for name, value in values if name == var.get())
        if hasattr(self, 'password'):
            self.settings['online'] = bool(self.online.get())
            self.settings['online_name'] = self.online_name.get().strip()
            self.settings['password'] = self.password.get()
        save_settings(self.settings)

    def set_state(self, text, colour):
        self.scene.itemconfigure(self.state_label, text=text)
        self.scene.itemconfigure(self.state_dot, fill=colour)

    def report(self):
        try:
            path = make_report(self.friends.friends)
        except OSError as error:
            self.set_state(t('The report could not be made: {error}', error=error), ERROR)
            return
        self.set_state(t('Report saved: {file}', file=path.name), GOOD)
        open_path(path.parent)

    def create_account(self):
        """Both accounts with the name and password above, off the window's thread (the servers may be slow)."""
        self.store()
        values = self.settings
        try:
            online_accounts.check_name_and_password(values['online_name'], values['password'])
            servers = online_accounts.server_endpoints(values['server'])
        except online_accounts.AccountError as error:
            self.set_account_status(str(error), ERROR)
            return
        self.account_button.set_enabled(False)
        self.set_account_status(t('Creating the account...'), MUTED)
        result = {}

        def work():
            try:
                online_accounts.create_account(servers['shadnet'], servers['refresh'], values['online_name'],
                                               values['password'])
                result['ok'] = t('Account {name} created: press Play.', name=values['online_name'])
            except online_accounts.AccountError as error:
                result['error'] = str(error)

        def finished():
            if thread.is_alive():
                self.after(200, finished)
                return
            self.account_button.set_enabled(True)
            ok = 'ok' in result
            self.set_account_status(result.get('ok') or result.get('error'), GOOD if ok else ERROR)
            if ok and not self.online.get():
                self.toggle_online()
            if ok:
                self.friends.account_changed(force=True)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self.after(200, finished)

    def toggle_log(self):
        self.log_shown = not self.log_shown
        height = self.HEIGHT + (LOG_HEIGHT if self.log_shown else 0)
        self.scene.configure(height=height)
        if self.log_shown:
            self.log_window = self.scene.widget(self.log, self.PANEL_X, self.HEIGHT - 4, WIDTH - 56, LOG_HEIGHT - 12)
            self.log.see('end')
        elif self.log_window:
            self.scene.delete(self.log_window)
            self.log_window = None
        self.scene.itemconfigure(self.log_link, text=t('Hide log') if self.log_shown else t('Show log'))
        self.geometry(f'{ui_scale.s(WIDTH)}x{ui_scale.s(height)}')

    def play(self):
        if self.process or self.refresh_game() not in ('ready', 'warn'):
            return
        if self.log_cleaner and self.log_cleaner.is_alive():
            self.log_cleaner.join()  # run.py moves last_run.log as it starts: the clean-up finishes first
        if self.friends.busy:
            # shadNet takes one sign-in per account: the game's would fail while the friends list is signed in.
            self.set_state(t('Starting...'), WARN)
            self.after(150, self.play)
            return
        self.store()
        env = dict(os.environ)
        env['LBPPC_RENDER_RES'] = self.settings['resolution']
        if self.settings['display'] != 'windowed':
            env['BB_WINDOW_MODE'] = self.settings['display']
        else:
            env.pop('BB_WINDOW_MODE', None)
        env['BB_FSR1'] = '1' if self.settings['upscaling'] == 'fsr1' else '0'
        env['BB_LANGUAGE'] = self.game_language()
        env['BB_RCAS'] = '0' if self.settings['sharpening'] == 'off' else '1'
        env['BB_RCAS_ATTENUATION'] = str(SHARPENING_ATTENUATION.get(self.settings['sharpening'], 250))
        if self.settings['latency'] == 'off':
            env.update(BB_LOW_LATENCY='off', BB_VBLANK_HZ='60', BB_FPS_LIMIT='-1')
        else:
            env.update(BB_LOW_LATENCY=self.settings['latency'], BB_VBLANK_HZ='0', BB_FPS_LIMIT='30')
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
                self.append([f'Launcher: input.ini could not be saved with the mouse off: {error}'])
        # The player's name in the game (pressing the right stick shows it), offline too.
        if re.fullmatch(online_accounts.NAME_PATTERN, self.settings['online_name']):
            env['BB_USER_NAME'] = self.settings['online_name']
        if self.settings['online']:
            name, password = self.settings['online_name'], self.settings['password']
            try:
                online_accounts.check_name_and_password(name, password)
                servers = online_accounts.server_endpoints(self.settings['server'])
            except online_accounts.AccountError as error:
                self.set_state(t('Online: {error}', error=error), ERROR)
                return
            host, port, tls = online_accounts.shadnet_address(servers['shadnet'])
            env.update(LBPPC_SERVER=servers['refresh'], BB_SHADNET_NPID=name, BB_SHADNET_PASSWORD=password,
                       BB_SHADNET_SERVER=f'{host}:{port}', BB_SHADNET_WEBAPI=servers['webapi'])
            if tls:
                env['BB_SHADNET_TLS'] = '1'
        for key, variable, _title, _hint, _on in DIAGNOSTICS:
            env[variable] = '1' if self.settings['diagnostics'].get(key) else '0'
        env['PYTHONUNBUFFERED'] = '1'
        env['LBPPC_VERSION'] = VERSION
        # Other players' names the launcher knows, left out of the log (scripts/log_privacy.py, run.py).
        env['LBPPC_LOG_NAMES'] = ','.join(known_players(self.friends.friends))
        self.scrub = log_privacy.from_environment(env)
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        self.clear_log()
        # The bridge's name before 0.1.1 (bbport_dlss.dll) is still loaded.
        dlss_files = [name if name != 'lbppc_dlss.dll' or (BIN / name).is_file() else 'bbport_dlss.dll'
                      for name in DLSS_FILES]
        if self.settings['frame_gen'] == 'dlss' and not all((BIN / name).is_file() for name in dlss_files):
            # The game would quietly use FSR 3.1 instead (packaging/windows/import_dlss.py installs them).
            self.append(['Launcher: the DLSS files are missing next to the game; frame generation uses FSR 3.1.'])
        command = [sys.executable, '--script'] if FROZEN else [sys.executable]
        self.process = subprocess.Popen(command + [str(ROOT / 'run.py'), self.settings['game']], cwd=ROOT,
                                        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, creationflags=flags)
        threading.Thread(target=self.read_output, args=(self.process,), daemon=True).start()
        self.play_button.set_enabled(False)
        self.stop_button.set_enabled(True)
        self.set_state(t('Starting...'), WARN)
        self.friends.game_running(True)
        self.start_presence()

    def read_output(self, process):
        for raw in process.stdout:
            # run.py scrubs the game's lines; its own and the preparation scripts' are scrubbed here.
            self.lines.put(self.scrub(raw.decode('utf-8', 'replace').rstrip('\r\n')))
        self.lines.put((process.wait(),))

    def pump(self):
        shown = []
        try:
            while len(shown) < 400:
                item = self.lines.get_nowait()
                if isinstance(item, tuple):
                    self.finished(item[0])
                    break
                shown.append(item)
                progress = SHADER_PROGRESS.match(item)
                if progress:
                    # The shader list prepared before play (vk_pipeline_serialization.cpp): two passes, the
                    # shaders and then the pipelines, shown as one.
                    done, total = int(progress.group(2)), max(int(progress.group(3)), 1)
                    percent = done * 50 // total + (50 if progress.group(1) == 'building' else 0)
                    self.set_state(t('Preparing shaders ({percent}%)', percent=min(percent, 100)), WARN)
                elif 'Frame rate' in item or 'Pipeline cache: preload took' in item:
                    self.set_state(t('Running'), GOOD)
        except queue.Empty:
            pass
        if shown:
            self.append(shown)
        self.after(100, self.pump)

    def append(self, lines):
        self.log.configure(state='normal')
        for line in lines:
            tag = 'bad' if line.startswith(('Fault', 'STOP', 'Game exited')) else \
                'warn' if line.startswith(('Launcher:', 'LBPPC_')) else ()
            self.log.insert('end', line + '\n', tag)
        excess = int(self.log.index('end-1c').split('.')[0]) - 2000
        if excess > 0:
            self.log.delete('1.0', f'{excess}.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def clear_log(self):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def finished(self, code):
        self.process = None
        self.stop_button.set_enabled(False)
        self.friends.game_running(False)
        self.stop_presence()
        if code == 0:
            self.set_state(t('Closed'), MUTED)
        else:
            self.set_state(t('Ended with an error: see the log'), ERROR)
            if not self.log_shown:
                self.toggle_log()
        self.refresh_game()

    def stop(self):
        if self.process:
            # run.py's game process is its child: the whole tree ends (on Windows before run.py itself,
            # whose children taskkill finds through it).
            if os.name == 'nt':
                subprocess.run(['taskkill', '/T', '/F', '/PID', str(self.process.pid)], capture_output=True)
            self.process.terminate()

    def set_icon(self):
        """The logo as the window's and taskbar button's icon. Its own app ID keeps the button from grouping under
        Python's when run from source."""
        if os.name == 'nt':
            import ctypes
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('LBPPC.Launcher')
            except (AttributeError, OSError):
                pass
            if (theme.ASSETS / 'lbppc.ico').is_file():
                self.iconbitmap(default=str(theme.ASSETS / 'lbppc.ico'))
        globe = theme.logo_image(64)
        if globe:
            self._icon = ImageTk.PhotoImage(globe)  # kept: Tk does not hold its own reference
            self.iconphoto(True, self._icon)

    def close(self):
        self.stop()
        try:
            self.store()  # a field still being typed in has not lost focus, which is when it saves
        except Exception:  # noqa: BLE001 - closing must not fail
            pass
        self.destroy()
        # Leave without Python's shutdown: it freed the remaining Tk images after Tcl had gone, and with the log
        # reader and friends threads still running, which ended in "alloc: invalid block" on every close.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)


class Slider:
    """A value dragged along a lime track on a Scene (log scale: equal steps double and halve), its value beside it.
    `on_change(value)` runs when the knob is let go."""

    def __init__(self, scene, x, y, w, lo, hi, value, on_change, tags=()):
        self.scene, self.x, self.y, self.w, self.lo, self.hi = scene, x, y, w - 60, lo, hi
        self.on_change, self.value = on_change, value
        self.track = scene.create_line(x, y, x + self.w, y, width=8, capstyle='round', fill=theme.FIELD_EDGE, tags=tags)
        self.fill = scene.create_line(x, y, x, y, width=8, capstyle='round', fill=theme.hex_colour(theme.LIME),
                                      tags=tags)
        self.knob = scene.create_oval(0, 0, 0, 0, fill='#ffffff', outline=theme.hex_colour(theme.LIME), width=3,
                                      tags=tags)
        self.label = scene.create_text(x + self.w + 18, y, anchor='w', font=('Segoe UI Semibold', 10), fill=TEXT,
                                       tags=tags)
        for item in (self.track, self.fill, self.knob):
            scene.tag_bind(item, '<Button-1>', self.drag)
            scene.tag_bind(item, '<B1-Motion>', self.drag)
            scene.tag_bind(item, '<ButtonRelease-1>', lambda _e: self.on_change(self.value))
            scene.tag_bind(item, '<Enter>', lambda _e: scene.configure(cursor='hand2'))
            scene.tag_bind(item, '<Leave>', lambda _e: scene.configure(cursor=''))
        self.draw()

    def items(self):
        return [self.track, self.fill, self.knob, self.label]

    def draw(self):
        t = math.log(self.value / self.lo) / math.log(self.hi / self.lo)
        kx = self.x + max(0.0, min(1.0, t)) * self.w
        self.scene.coords(self.fill, self.x, self.y, kx, self.y)
        self.scene.coords(self.knob, kx - 9, self.y - 9, kx + 9, self.y + 9)
        self.scene.itemconfigure(self.label, text=f'{self.value:.2f}x')

    def drag(self, event):
        t = max(0.0, min(1.0, (event.x - self.x) / self.w))
        self.value = round(self.lo * (self.hi / self.lo) ** t, 2)
        self.draw()


# Tk key names (event.keysym) to input.ini's; keypad digits come by Windows virtual key code instead (Tk gives
# them the same keysym as the top row).
TK_KEYS = {
    'space': 'space', 'Tab': 'tab', 'Return': 'enter', 'BackSpace': 'backspace', 'Delete': 'delete',
    'Insert': 'insert', 'Home': 'home', 'End': 'end', 'Prior': 'pgup', 'Next': 'pgdown', 'Caps_Lock': 'capslock',
    'Shift_L': 'lshift', 'Shift_R': 'rshift', 'Control_L': 'lctrl', 'Control_R': 'rctrl', 'Alt_L': 'lalt',
    'Alt_R': 'ralt', 'Win_L': 'lwin', 'Win_R': 'rwin', 'Up': 'up', 'Down': 'down', 'Left': 'left', 'Right': 'right',
    'grave': 'grave', 'quoteleft': 'grave', 'minus': 'minus', 'equal': 'equals', 'bracketleft': 'lbracket',
    'bracketright': 'rbracket', 'backslash': 'backslash', 'semicolon': 'semicolon', 'apostrophe': 'apostrophe',
    'quoteright': 'apostrophe', 'comma': 'comma', 'period': 'period', 'slash': 'slash', 'Print': 'printscreen',
    'Scroll_Lock': 'scrolllock', 'Pause': 'pausebreak', 'KP_Enter': 'kpenter', 'KP_Add': 'kpplus',
    'KP_Subtract': 'kpminus', 'KP_Multiply': 'kpasterisk', 'KP_Divide': 'kpslash', 'KP_Decimal': 'kpperiod',
}
WINDOWS_KEYPAD = {**{96 + d: f'kp{d}' for d in range(10)}, 106: 'kpasterisk', 107: 'kpplus', 109: 'kpminus',
                  110: 'kpperiod', 111: 'kpslash'}
TK_MOUSE = {1: 'leftbutton', 2: 'middlebutton', 3: 'rightbutton', 4: 'sidebuttonback', 5: 'sidebuttonforward',
            8: 'sidebuttonback', 9: 'sidebuttonforward'}


class DebugPage:
    """The Debug page (in place of the Game page's panels): the in-game performance overlay's switches, written to
    lbppc.ini at once (the game reads it at start; Insert in the game changes them live), and the diagnostics the
    game logs, passed to it on Play."""

    ROW = 34

    def __init__(self, launcher, x, y, w, h):
        self.launcher, self.scene = launcher, launcher.scene
        self.items = []
        scene = self.scene
        before = set(scene.find_all())
        half = (w - 40) // 2 - 10
        right = x + 20 + half + 20
        rows = (len(OVERLAY) + 1) // 2
        overlay_h = 44 + rows * self.ROW + 30

        # Performance overlay: two columns of switches
        scene.image('panel:overlay', theme.panel(w, overlay_h, t('Performance overlay')), x, y)
        values = ini_values()
        self.overlay = {key: values.get(key, '1' if on else '0') == '1' for key, _title, on in OVERLAY}
        for index, (key, title, _on) in enumerate(OVERLAY):
            cx = (x + 20, right)[index // rows]
            cy = y + 44 + (index % rows) * self.ROW
            self.switch(f'overlay:{key}', cx, cy, t(title), self.overlay[key], lambda k=key: self.toggle_overlay(k))
        self.overlay_status = launcher.text(x + 22, y + overlay_h - 24, t('Shown in the top right of the game. '
                                            'Insert in the game changes these while you play.'), HINT_FONT, MUTED,
                                            'nw', width=w - 44)

        # Diagnostics: a switch and what it does
        dy = y + overlay_h + 12
        scene.image('panel:diagnostics', theme.panel(w, h - overlay_h - 12, t('Diagnostics')), x, dy)
        for index, (key, _variable, title, hint, _on) in enumerate(DIAGNOSTICS):
            cy = dy + 44 + index * 46
            self.switch(f'diag:{key}', x + 20, cy, t(title), launcher.settings['diagnostics'].get(key),
                        lambda k=key: self.toggle_diagnostic(k))
            launcher.text(x + 52, cy + 22, t(hint), HINT_FONT, MUTED, 'nw', width=w - 74)
        self.diag_status = launcher.text(x + 22, dy + h - overlay_h - 12 - 24, t('Used the next time you press Play. '
                                         'Logs go in the report from Make a report.'), HINT_FONT, MUTED, 'nw',
                                         width=w - 44)
        self.items = [item for item in scene.find_all() if item not in before]

    def switch(self, key, x, y, title, on, command):
        box = self.scene.image(f'{key}:box', theme.checkbox(on), x, y)
        label = self.scene.image(f'{key}:label', theme.text_image(title, 13, TEXT, 'Medium'), x + 30, y + 1)
        for item in (box, label):
            self.scene.tag_bind(item, '<Button-1>', lambda _e: command())
            self.scene.tag_bind(item, '<Enter>', lambda _e: self.scene.configure(cursor='hand2'))
            self.scene.tag_bind(item, '<Leave>', lambda _e: self.scene.configure(cursor=''))

    def redraw(self, key, x_y_on):
        x, y, on = x_y_on
        self.scene.image(f'{key}:box', theme.checkbox(on), x, y)

    def box_position(self, key):
        return self.scene.coords(self.scene.images[f'{key}:box'][0])

    def toggle_overlay(self, key):
        self.overlay[key] = not self.overlay[key]
        x, y = self.box_position(f'overlay:{key}')
        self.redraw(f'overlay:{key}', (x, y, self.overlay[key]))
        try:
            ini_set(key, '1' if self.overlay[key] else '0')
        except OSError as error:
            self.scene.itemconfigure(self.overlay_status, text=t('lbppc.ini could not be saved: {error}', error=error), fill=ERROR)
            return
        text = t('Saved. Used the next time the game starts, or press Insert in the game.') if self.launcher.process \
            else t('Saved.')
        self.scene.itemconfigure(self.overlay_status, text=text, fill=GOOD)

    def toggle_diagnostic(self, key):
        diagnostics = self.launcher.settings['diagnostics']
        diagnostics[key] = not diagnostics.get(key)
        x, y = self.box_position(f'diag:{key}')
        self.redraw(f'diag:{key}', (x, y, diagnostics[key]))
        save_settings(self.launcher.settings)
        text = t('Saved. Used the next time you press Play.') if self.launcher.process else t('Saved.')
        self.scene.itemconfigure(self.diag_status, text=text, fill=GOOD)


def tk_key_name(event):
    """input.ini's name for a Tk key press, or None."""
    if os.name == 'nt' and event.keycode in WINDOWS_KEYPAD:
        return WINDOWS_KEYPAD[event.keycode]
    keysym = event.keysym
    if keysym in TK_KEYS:
        return TK_KEYS[keysym]
    if keysym.startswith('KP_') and keysym[3:].isdigit():
        return f'kp{keysym[3:]}'
    if re.fullmatch(r'F([1-9]|1[0-2])', keysym):
        return keysym.lower()
    if len(keysym) == 1 and keysym.isascii() and keysym.isalnum():
        return keysym.lower()
    return None


class ControlsPage:
    """The Controls page (in place of the Game page's panels): how the game uses the mouse, and the keys, mouse
    buttons and controller buttons of each game button. Every change is written to input.ini at once
    (input_settings.py); F8 in the game reloads it."""

    ROW = 36
    COLUMNS = (250, 410, 580)  # second key, controller: x of each column in the list
    CELL_W = 150

    def __init__(self, launcher, x, y, w, h):
        self.launcher, self.scene = launcher, launcher.scene
        self.model = input_settings.InputSettings.load(INPUT_INI)
        self.capturing = None   # (output, slot) while waiting for a key
        self.confirm_reset = False
        self.items = []         # the page's canvas items, hidden with the page
        scene = self.scene
        before = set(scene.find_all())
        mouse_h = 176 if input_settings.MOUSE_SUPPORTED else -12
        half = (w - 40) // 2 - 10
        right = x + 20 + half + 20
        if input_settings.MOUSE_SUPPORTED:
            self.mouse_panel(x, y, w, mouse_h, half, right)

        # Buttons
        by = y + mouse_h + 12
        bh = h - mouse_h - 12
        scene.image('panel:buttons', theme.panel(w, bh, t('Buttons')), x, by)
        self.reset_link = launcher.text(x + w - 20, by + 21, t('Reset to defaults'), ('Segoe UI Semibold', 10),
                                        theme.hex_colour((200, 225, 150)), 'e')
        scene.tag_bind(self.reset_link, '<Button-1>', lambda _e: self.reset())
        scene.tag_bind(self.reset_link, '<Enter>', lambda _e: scene.configure(cursor='hand2'))
        scene.tag_bind(self.reset_link, '<Leave>', lambda _e: scene.configure(cursor=''))
        mouse = input_settings.MOUSE_SUPPORTED
        for column, title in zip((x + 14 + 12,) + tuple(x + 14 + c for c in self.COLUMNS),
                                 (t('Game button'), t('Key or mouse') if mouse else t('Key'), t('Second key'),
                                  t('Controller'))):
            launcher.text(column, by + 46, title, ('Segoe UI Semibold', 9), MUTED)
        self.status = launcher.text(x + 20, by + bh - 22,
                                    t('Click a key, then press the new key or mouse button. Right-click a key to clear '
                                      'it.') if mouse else
                                    t('Click a key, then press the new key. Right-click a key to clear it.'),
                                    HINT_FONT, MUTED, 'w', width=w - 40)
        self.list = Scene(launcher, w - 28, bh - 104)
        self.list.configure(bg=FriendsPanel.LIST_BG)
        scene.widget(self.list, x + 14, by + 66, w - 28, bh - 104)
        self.list.bind('<Enter>', lambda _e: self.list.bind_all('<MouseWheel>', self.wheel))
        self.list.bind('<Leave>', lambda _e: self.list.unbind_all('<MouseWheel>'))
        self.pad_boxes = {}
        self.draw_list()
        self.update_mouse_hint()
        self.items = [item for item in scene.find_all() if item not in before]

    # Mouse

    def mouse_panel(self, x, y, w, mouse_h, half, right):
        launcher, scene = self.launcher, self.scene
        scene.image('panel:mouse', theme.panel(w, mouse_h, t('Mouse')), x, y)
        launcher.label('label:mouse_mode', x + 20, y + 44, t('Mouse in the game'))
        names = [t(name) for _value, name in input_settings.MOUSE_MODES]
        self.mouse_mode = tk.StringVar(value=t(dict(input_settings.MOUSE_MODES)[self.model.mouse_mode]))
        box = ttk.Combobox(launcher, textvariable=self.mouse_mode, values=names, state='readonly', font=UI_FONT,
                           style='Night.TCombobox')
        box.bind('<<ComboboxSelected>>', lambda _e: (self.mouse_changed(), launcher.selection_clear()))
        scene.widget(box, x + 20, y + 66, half, 28)
        launcher.label('label:speed', right, y + 44, t('Sensitivity'))
        self.speed = Slider(scene, right + 8, y + 80, half - 8, *input_settings.SPEED_RANGE, self.model.speed,
                            self.speed_changed)
        launcher.label('label:vertical', x + 20, y + 106, t('Up and down, compared with sideways'))
        self.vertical = Slider(scene, x + 28, y + 142, half - 8, *input_settings.VERTICAL_RANGE, self.model.vertical,
                               self.vertical_changed)
        self.invert_at = (right, y + 130)
        self.invert_box = scene.image('invert:box', theme.checkbox(self.model.invert_y), *self.invert_at)
        scene.tag_bind(self.invert_box, '<Button-1>', lambda _e: self.toggle_invert())
        launcher.label('label:invert', right + 30, y + 131, t('Invert up and down'))
        self.mouse_hint = launcher.text(right + 2, y + 156, '', HINT_FONT, MUTED, 'nw', width=half)

    def update_mouse_hint(self):
        if not input_settings.MOUSE_SUPPORTED:
            return
        mode = self.model.mouse_mode
        text = {'off': t('The game does not use the mouse.'),
                'touchpad': t('F7 captures it. Wheel resizes, Ctrl + wheel rotates.')}.get(
                    mode, t('F7 in the game captures the mouse and frees it.'))
        self.scene.itemconfigure(self.mouse_hint, text=text)

    def mouse_changed(self):
        self.model.mouse_mode = next(value for value, name in input_settings.MOUSE_MODES
                                     if t(name) == self.mouse_mode.get())
        self.update_mouse_hint()
        self.save(t('Mouse: {mode}.', mode=self.mouse_mode.get()))

    def speed_changed(self, value):
        self.model.speed = value
        self.save(t('Sensitivity {value}x.', value=f'{self.model.speed:.2f}'))

    def vertical_changed(self, value):
        self.model.vertical = value
        self.save(t('Up and down sensitivity {value}x of sideways.', value=f'{value:.2f}'))

    def toggle_invert(self):
        self.model.invert_y = not self.model.invert_y
        self.scene.image('invert:box', theme.checkbox(self.model.invert_y), *self.invert_at)
        self.save(t('Up and down inverted.') if self.model.invert_y else t('Up and down not inverted.'))

    # Saving

    def set_status(self, text, colour=MUTED):
        self.scene.itemconfigure(self.status, text=text, fill=colour)

    def save(self, done):
        try:
            self.model.save(INPUT_INI)
        except OSError as error:
            self.set_status(t('input.ini could not be saved: {error}', error=error), ERROR)
            return
        if self.launcher.process:
            done += ' ' + t('Press F8 in the game to use it now.')
        self.set_status(done, GOOD)

    def reset(self):
        if not self.confirm_reset:
            self.confirm_reset = True
            self.scene.itemconfigure(self.reset_link, text=t('Reset everything? Click again'), fill=ERROR)
            self.launcher.after(4000, self.cancel_reset)
            return
        self.cancel_reset()
        self.model = input_settings.InputSettings()
        if input_settings.MOUSE_SUPPORTED:
            self.mouse_mode.set(dict(input_settings.MOUSE_MODES)[self.model.mouse_mode])
            self.speed.value, self.vertical.value = self.model.speed, self.model.vertical
            self.speed.draw()
            self.vertical.draw()
            self.scene.image('invert:box', theme.checkbox(self.model.invert_y), *self.invert_at)
            self.update_mouse_hint()
        self.draw_list()
        self.save(t('Controls reset to the defaults.'))

    def cancel_reset(self):
        self.confirm_reset = False
        self.scene.itemconfigure(self.reset_link, text=t('Reset to defaults'), fill=theme.hex_colour((200, 225, 150)))

    # The list of buttons

    def wheel(self, event):
        if not self.capturing:
            self.list.yview_scroll(-event.delta // 120, 'units')

    def draw_list(self):
        view = self.list
        view.delete('all')
        for box in self.pad_boxes.values():
            box.destroy()
        self.pad_boxes = {}
        pad_labels = [t('None')] + [t(input_settings.PAD_LABELS[name]) for name in input_settings.PAD_BUTTONS +
                                    input_settings.PAD_HALF_AXES]
        pad_names = [None] + input_settings.PAD_BUTTONS + input_settings.PAD_HALF_AXES
        for row, (output, label, hint) in enumerate(input_settings.ACTIONS):
            y = 6 + row * self.ROW
            cy = y + self.ROW // 2 - 3
            if row % 2:
                view.create_rectangle(0, y - 2, int(view['width']), y + self.ROW - 4, fill='#141a2b', outline='')
            item = view.create_text(12, cy, text=t(label), anchor='w', font=('Segoe UI Semibold', 10), fill=TEXT)
            if hint:
                view.create_text(view.bbox(item)[2] + 8, cy, text=t(hint), anchor='w', font=HINT_FONT, fill=MUTED)
            for slot in (0, 1):
                self.draw_cell(output, slot, y)
            if output in input_settings.STICK_OUTPUTS:
                view.create_text(self.COLUMNS[2] + 6, cy, text=t('The stick itself'), anchor='w', font=HINT_FONT,
                                 fill=MUTED)
                continue
            current = self.model.pad[output][0] if self.model.pad[output] else None
            var = tk.StringVar(value=pad_labels[pad_names.index(current)] if current in pad_names else current)
            box = ttk.Combobox(self.list, textvariable=var, values=pad_labels, state='readonly', font=UI_FONT,
                               style='Night.TCombobox', height=16)
            box.bind('<<ComboboxSelected>>', lambda _e, o=output, v=var: self.pad_changed(o, pad_names[
                pad_labels.index(v.get())], v.get()))
            view.create_window(self.COLUMNS[2], y + 2, window=box, anchor='nw', width=self.CELL_W + 20, height=26)
            self.pad_boxes[output] = box
        view.configure(scrollregion=(0, 0, int(view['width']), 12 + len(input_settings.ACTIONS) * self.ROW))

    def draw_cell(self, output, slot, y):
        view = self.list
        tag = f'cell:{output}:{slot}'
        view.delete(tag)
        names = self.model.keys[output]
        capturing = self.capturing == (output, slot)
        name = names[slot] if slot < len(names) else None
        if slot == 1 and len(names) == 0 and not capturing:
            return  # a second key only once there is a first
        x = self.COLUMNS[slot]
        view.create_rectangle(x, y + 2, x + self.CELL_W, y + 28, fill=theme.FIELD, width=2 if capturing else 1,
                              outline=theme.hex_colour(theme.ORANGE) if capturing else theme.FIELD_EDGE, tags=(tag,))
        text = t('Press a key...') if capturing else input_settings.key_label(name) if name else t('Add')
        view.create_text(x + self.CELL_W // 2, y + 15, text=text, font=UI_FONT, tags=(tag,),
                         fill=theme.hex_colour(theme.ORANGE) if capturing else TEXT if name else MUTED)
        view.tag_bind(tag, '<ButtonRelease-1>', lambda _e: self.start_capture(output, slot))
        view.tag_bind(tag, '<Button-3>', lambda _e: self.clear(output, slot))
        view.tag_bind(tag, '<Enter>', lambda _e: view.configure(cursor='hand2'))
        view.tag_bind(tag, '<Leave>', lambda _e: view.configure(cursor=''))

    def row_y(self, output):
        return 6 + [o for o, _l, _h in input_settings.ACTIONS].index(output) * self.ROW

    def label_of(self, output):
        return t(next(label for o, label, _h in input_settings.ACTIONS if o == output))

    def pad_changed(self, output, name, shown):
        self.model.set_pad(output, name)
        self.launcher.selection_clear()
        self.save(t('{action}: {button} on the controller.', action=self.label_of(output), button=shown))

    def clear(self, output, slot):
        if self.capturing:
            return
        self.model.clear_key(output, slot)
        self.draw_list_row(output)
        self.save(f'{self.label_of(output)}: key cleared.')

    def draw_list_row(self, output):
        for slot in (0, 1):
            self.draw_cell(output, slot, self.row_y(output))

    # Waiting for the new key

    def start_capture(self, output, slot):
        if self.capturing:
            return
        self.capturing = (output, slot)
        self.draw_list_row(output)
        self.set_status(t('Press the new key or mouse button for {action} (Esc cancels).', action=self.label_of(output)),
                        theme.hex_colour(theme.ORANGE))
        root = self.launcher
        root.after(80, lambda: (root.bind_all('<KeyPress>', self.key_pressed), root.bind_all(
            '<ButtonPress>', self.mouse_pressed), root.bind_all('<MouseWheel>', self.wheel_pressed)))

    def stop_capture(self):
        output = self.capturing[0]
        self.capturing = None
        for sequence in ('<KeyPress>', '<ButtonPress>', '<MouseWheel>'):
            self.launcher.unbind_all(sequence)
        self.draw_list_row(output)

    def key_pressed(self, event):
        if event.keysym == 'Escape':
            self.stop_capture()
            self.set_status(t('Not changed.'))
            return 'break'
        name = tk_key_name(event)
        if not name:
            self.set_status(t('That key ({key}) cannot be used. Press another, or Esc.', key=event.keysym), ERROR)
            return 'break'
        self.assign(name)
        return 'break'

    def mouse_pressed(self, event):
        name = TK_MOUSE.get(event.num) if input_settings.MOUSE_SUPPORTED else None
        if name:
            self.assign(name)
        return 'break'

    def wheel_pressed(self, event):
        if input_settings.MOUSE_SUPPORTED:
            self.assign('mousewheelup' if event.delta > 0 else 'mousewheeldown')
        return 'break'

    def assign(self, name):
        output, slot = self.capturing
        if name in input_settings.RESERVED_KEYS:
            self.set_status(t('{key} {what} in the game. Press another key, or Esc.', key=input_settings.key_label(name),
                              what=t(input_settings.RESERVED_KEYS[name])), ERROR)
            return
        self.stop_capture()
        moved = self.model.bind_key(output, slot, name)
        self.draw_list_row(output)
        if moved:
            self.draw_list_row(moved)
        label = input_settings.key_label(name)
        self.save(t('{key} moved from {old} to {new}.', key=label, old=self.label_of(moved), new=self.label_of(output))
                  if moved else t('{action}: {key}.', action=self.label_of(output), key=label))


class FriendsPanel:
    """The player's shadNet friends, beside the settings: a name to send a request to, then the requests to answer,
    the friends (online first), and the requests sent, each in its own card. Friends are the players who may join
    this player's game (signaling_handler.cpp's join protection).

    shadNet takes one sign-in per account, so the list signs in only for a moment (to read or change it) and never
    while the game runs; Play waits for a sign-in in progress."""

    REFRESH_MS = 60000
    RETRY_MS = 20000  # after a failed sign-in, before any succeeded
    LIST_BG = '#0f1320'  # the panel's colour over the felt
    SECTIONS = (('received', N_('Friend requests'), theme.ORANGE), ('friends', N_('Friends'), theme.GREEN),
                ('sent', N_('Sent requests'), theme.GREY))

    def __init__(self, launcher, x, y, w, h):
        self.launcher, self.scene = launcher, launcher.scene
        self.busy = self.paused = False
        self.friends = None
        self.signed_in_as = None
        self.tabs, self.confirm = [], None
        self.scene.image('panel:friends', theme.panel(w, h, t('Friends')), x, y)
        self.add_name = tk.StringVar()
        self.add_button = Tab(self.scene, t('Add'), self.send_request, x + w - 18, y + 61, 'e', size=14, tilt=3)
        entry = field(launcher, self.add_name)
        entry.bind('<Return>', lambda _e: self.send_request())
        self.scene.widget(entry, x + 20, y + 46, w - 40 - self.add_button.width - 14, 30)
        self.status = self.scene.create_text(x + 21, y + 84, anchor='nw', font=HINT_FONT, fill=MUTED, width=w - 42,
                                             text=t('Add a friend by their online name.'))
        self.list = Scene(launcher, w - 28, h - 206)
        self.list.configure(bg=self.LIST_BG)
        self.list_w = w - 28
        self.scene.widget(self.list, x + 14, y + 112, w - 28, h - 206)
        # Under the list, for players with nobody to add yet
        self.scene.create_text(x + 21, y + h - 72, anchor='w', font=HINT_FONT, fill=MUTED, width=w - 42,
                               text=t('Find people to play with, get help and hear about updates:'))
        self.discord = Tab(self.scene, t('Join the Discord'), lambda: webbrowser.open(DISCORD), x + w / 2, y + h - 34,
                           size=17, pad=(18, 10), tilt=-3, fill=BLURPLE)
        self.discord.move(x + w / 2 - self.discord.width / 2, y + h - 34)
        self.list.bind('<Enter>', lambda _e: self.list.bind_all('<MouseWheel>', self.wheel))
        self.list.bind('<Leave>', lambda _e: self.list.unbind_all('<MouseWheel>'))
        self.draw()
        launcher.after(300, self.account_changed)
        launcher.after(self.REFRESH_MS, self.tick)

    # The account and when to sign in

    def account(self):
        """(shadNet server, name, password), or None with the reason shown."""
        settings = self.launcher.settings
        try:
            online_accounts.check_name_and_password(settings['online_name'], settings['password'])
            server = online_accounts.server_endpoints(settings['server'])['shadnet']
        except online_accounts.AccountError:
            return None
        return server, settings['online_name'], settings['password']

    def account_changed(self, force=False):
        account = self.account()
        if not account:
            self.friends, self.signed_in_as = None, None
            self.draw()
            self.set_status(t('Add a friend by their online name.'), MUTED)
            return
        if force or account != self.signed_in_as:
            self.reload()

    def tick(self):
        if self.account() and not self.paused:  # signed in or not yet: a failed first sign-in is tried again too
            self.run(None, quiet=True)
        self.launcher.after(self.REFRESH_MS, self.tick)

    def game_running(self, running):
        self.paused = running
        if running:
            self.set_status(t('The list updates again when the game closes.'), MUTED)
        self.draw()
        if not running:
            self.reload()

    def set_status(self, text, colour):
        self.scene.itemconfigure(self.status, text=text, fill=colour)

    def run(self, action, done_text=None, quiet=False):
        """`action(session)` on a fresh signed-in session, off the window's thread; the list is redrawn after."""
        account = self.account()
        if self.busy or self.paused or not account:
            if not account and action:
                self.set_status(t('Create an account (Online, on the left) to add friends.'), ERROR)
            return
        self.busy = True
        if not quiet:
            self.set_status(t('Working...') if action else t('Signing in...'), MUTED)
        result = {}

        def work():
            client = f'LBPPC {VERSION} launcher'
            try:
                if action:
                    with online_accounts.ShadnetSession(*account, client) as session:
                        action(session)
                with online_accounts.ShadnetSession(*account, client) as session:
                    result['friends'] = session.friends
            except online_accounts.AccountError as error:
                result['error'] = str(error)
            except Exception as error:  # noqa: BLE001 - anything else must not leave the list stuck "Signing in..."
                result['error'] = t('Signing in failed: {error}', error=error or type(error).__name__)

        def finished():
            if thread.is_alive():
                self.launcher.after(150, finished)
                return
            self.busy = False
            if 'error' in result or 'friends' not in result:
                self.set_status(result.get('error', t('Signing in failed.')), ERROR)
                if not self.signed_in_as:  # never signed in yet: tried again shortly, not only at the next refresh
                    self.launcher.after(self.RETRY_MS, lambda: self.signed_in_as or self.run(None, quiet=True))
                return
            self.friends, self.signed_in_as = result['friends'], account
            self.draw()
            if done_text:
                self.set_status(done_text, GOOD)
            elif not quiet or self.scene.itemcget(self.status, 'fill') == ERROR:
                self.set_status(t('Signed in as {name}.', name=account[1]), MUTED)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self.launcher.after(150, finished)

    def reload(self):
        self.run(None)

    def send_request(self):
        other = self.add_name.get().strip()
        account = self.account()
        if not re.fullmatch(online_accounts.NAME_PATTERN, other) or (account and other.lower() == account[1].lower()):
            self.set_status(t('Type another player\'s online name.'), ERROR)
            return
        self.add_name.set('')
        self.run(lambda session: session.add(other), t('Request sent to {name}. They show as a friend once they '
                                                       'accept.', name=other))

    # The list

    def wheel(self, event):
        if self.list.bbox('all') and self.list.bbox('all')[3] > self.list.winfo_height():
            self.list.yview_scroll(-event.delta // 120, 'units')

    def link(self, x, y, text, command, colour=MUTED):
        """A quiet text button (Decline, Cancel, Remove), right-aligned at x."""
        item = self.list.create_text(x, y, text=text, anchor='e', font=('Segoe UI Semibold', 9), fill=colour)
        self.list.tag_bind(item, '<Enter>', lambda _e: (self.list.itemconfigure(item, fill=TEXT),
                                                        self.list.configure(cursor='hand2')))
        self.list.tag_bind(item, '<Leave>', lambda _e: (self.list.itemconfigure(item, fill=colour),
                                                        self.list.configure(cursor='')))
        self.list.tag_bind(item, '<Button-1>', lambda _e: command())
        return self.list.bbox(item)[0]

    def remove_friend(self, other):
        """Removing a friend asks once more: the first click turns the link into "Remove?"."""
        if self.confirm == other:
            self.confirm = None
            self.run(lambda session: session.remove(other), t('Removed {name}.', name=other))
        else:
            self.confirm = other
            self.draw()
            self.launcher.after(4000, lambda: self.confirm == other and (setattr(self, 'confirm', None), self.draw()))

    def draw(self):
        view = self.list
        for tab in self.tabs:
            tab.forget()
        self.tabs = []
        view.delete('all')
        view.images.clear()
        view.configure(cursor='')
        w, y = self.list_w, 0
        if self.friends is None:
            message = t('Create an account (Online, on the left) and your friends show here: requests to answer, '
                        'friends, and requests you sent.') if not self.account() else t('Signing in...')
            view.create_text(w // 2, 40, text=message, width=w - 40, justify='center', font=HINT_FONT, fill=MUTED)
            return
        friends = self.friends
        rows = {'received': [(name, None) for name in friends.received],
                'friends': sorted(friends.friends, key=lambda f: (not f[1], f[0].lower())),
                'sent': [(name, None) for name in friends.sent]}
        for key, title, accent in self.SECTIONS:
            items = rows[key]
            if not items and key != 'friends':
                continue  # requests show only when there are some
            empty = not items
            height = 38 + (62 if empty else 36 * len(items)) + 6
            view.image(f'card:{key}', theme.card(w, height, accent), 0, y)
            heading = f'{t(title).upper()}  ({len(items)})'
            if key == 'friends' and items:
                heading += '   ' + t('{count} online', count=sum(1 for _n, online in items if online))
            view.create_text(18, y + 20, text=heading, anchor='w', font=('Segoe UI Semibold', 10),
                             fill=theme.hex_colour(accent) if key != 'sent' else MUTED)
            row_y = y + 38
            if empty:
                view.create_text(18, row_y + 26, anchor='w', width=w - 34, font=HINT_FONT, fill=MUTED,
                                 text=t('No friends yet. Type a friend\'s online name above and press Add; once '
                                        'they accept, they show here and can join your game.'))
            for name, online in items:
                cy = row_y + 16
                right = w - 14
                if key == 'received':
                    accept = Tab(view, t('Accept'), lambda o=name: self.run(lambda s: s.add(o),
                                                                            t('You and {name} are friends.', name=o)),
                                 right, cy, 'e', size=12, pad=(9, 7))
                    self.tabs.append(accept)
                    self.link(accept.cx - accept.width / 2 - 12, cy, t('Decline'),
                              lambda o=name: self.run(lambda s: s.remove(o), t('Declined {name}.', name=o)))
                    view.create_text(18, cy, text=name, anchor='w', font=('Segoe UI Semibold', 10), fill=TEXT)
                elif key == 'friends':
                    dot = GOOD if online else '#5a6278'
                    view.create_oval(18, cy - 5, 28, cy + 5, fill=dot, outline='')
                    label = view.create_text(36, cy - 1, text=name, anchor='w', font=('Segoe UI Semibold', 10),
                                             fill=TEXT)
                    view.create_text(view.bbox(label)[2] + 8, cy, text=t('online') if online else t('offline'), anchor='w',
                                     font=('Segoe UI', 8), fill=GOOD if online else MUTED)
                    if self.confirm == name:
                        self.link(right, cy, t('Remove?'), lambda o=name: self.remove_friend(o), ERROR)
                    else:
                        self.link(right, cy, t('Remove'), lambda o=name: self.remove_friend(o))
                else:
                    view.create_text(18, cy, text=name, anchor='w', font=UI_FONT, fill=MUTED)
                    self.link(right, cy, t('Cancel'), lambda o=name: self.run(
                        lambda s: s.remove(o), t('Cancelled the request to {name}.', name=o)))
                row_y += 36
            y += height + 12
        view.configure(scrollregion=(0, 0, w, max(y, 1)))
        if self.paused:
            for tab in self.tabs:
                tab.set_enabled(False)


def run_script(argv):
    """LBPPC.exe --script <file.py> [args]: runs a Python script of the package (run.py, scripts/, tools/) with the
    Python frozen into LBPPC.exe, so players need no Python of their own."""
    import runpy
    path = Path(argv[0]).resolve()
    sys.argv = [str(path), *argv[1:]]
    sys.path.insert(0, str(path.parent))
    runpy.run_path(str(path), run_name='__main__')


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--script':
        run_script(sys.argv[2:])
    elif len(sys.argv) > 3 and sys.argv[1] == '--apply-update':
        updater.run_apply(sys.argv[2:])  # the new version, from out/update/staging, installing itself
    else:
        # The launcher at the screen's size (ui_scale.py), and on a short screen (the Steam Deck's) a shorter log.
        ui_scale.install(ui_scale.choose(WIDTH, Launcher.HEIGHT))
        room = ui_scale.work_area()[1] * 0.96 / ui_scale.U - Launcher.HEIGHT
        LOG_HEIGHT = max(100, min(LOG_HEIGHT, int(room)))
        Launcher().mainloop()
