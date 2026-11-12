#!/usr/bin/env python3
"""LBPPC launcher: pick the game folder (the player's own dump), choose the resolution, display mode and
frame generation, and play. Settings are kept in user/launcher.json; frame generation also goes to
bbport.ini, which the renderer reads. The game runs through run.py with the same Python.

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

VERSION = '0.1.0'
DISCORD = 'https://discord.gg/YqfBQCU4vc'  # the permanent invite: help, bug reports, finding co-op partners
BLURPLE = (88, 101, 242)  # Discord's own colour, for its button
# Packaged (frozen with PyInstaller): LBPPC.exe sits in the package folder and runs run.py itself (--script).
FROZEN = getattr(sys, 'frozen', False)
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
BIN = ROOT / 'bin' if (ROOT / 'bin' / 'LBPPC-Game.exe').is_file() else ROOT / 'out'
if not FROZEN:
    sys.path.insert(0, str(ROOT / 'scripts'))  # frozen, PyInstaller bundles the module
import online_accounts  # noqa: E402
import gameview  # noqa: E402
import ui_scale  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lbppc_theme as theme  # noqa: E402
import input_settings  # noqa: E402
USER = ROOT / 'user'
SETTINGS = USER / 'launcher.json'
INI = ROOT / 'bbport.ini'
INPUT_INI = ROOT / 'input.ini'  # the game's controls (runtime_input_config.c), the Controls page's
LOG = ROOT / 'out' / 'last_run.log'

# The game renders at these sizes natively (LBPPC_RENDER_RES: its render targets are made that large).
# Other aspects widen the view (ultrawide: more of the level at the sides, the menus laid out for it). Wider than
# 4480 the game does not survive yet (probe.c render_res_install); the window scales the picture up.
RESOLUTIONS = [('1280 x 720 (lighter on the GPU)', '1280x720'), ('1920 x 1080 (original)', '1920x1080'), ('2560 x 1440', '2560x1440'), ('3840 x 2160 (4K)', '3840x2160'),
               ('2560 x 1080 (21:9)', '2560x1080'), ('3440 x 1440 (21:9)', '3440x1440'),
               ('3840 x 1600 (21:9)', '3840x1600'), ('3840 x 1080 (32:9)', '3840x1080'),
               ('4480 x 1260 (32:9)', '4480x1260')]
# BB_WINDOW_MODE: borderless covers the desktop; exclusive takes the display mode.
DISPLAY_MODES = [('Windowed', 'windowed'), ('Borderless fullscreen', 'borderless'), ('Exclusive fullscreen', 'fullscreen')]
# bbport.ini frame_gen: the game steps 30 times a second; frame generation shows 60.
FRAME_GENS = [('Off (30 FPS)', 'off'), ('FSR 3.1 (any GPU)', 'fsr3'), ('DLSS (GeForce RTX 40/50)', 'dlss')]
# BB_FSR1: the finished picture scaled to the window with AMD FSR 1 instead of a plain stretch. It only does something
# when the resolution is below the window's (full screen at a lower resolution): that is upscaling.
UPSCALERS = [('Off (plain scaling)', 'off'), ('FSR 1 (any GPU)', 'fsr1')]
# BB_RCAS / BB_RCAS_ATTENUATION: FSR 1's sharpening (RCAS), in thousandths of a stop: 0 is the sharpest.
SHARPENING = [('Off', 'off'), ('Low', 'low'), ('Medium', 'medium'), ('High', 'high'), ('Maximum', 'max')]
SHARPENING_ATTENUATION = {'low': 800, 'medium': 250, 'high': 100, 'max': 0}
# BB_LOW_LATENCY: NVIDIA Reflex (AMD Anti-Lag 2 on AMD), with the 30 FPS limit kept before each game frame.
LATENCY = [('On (NVIDIA Reflex / AMD Anti-Lag 2)', 'on'), ('On + Boost (NVIDIA)', 'boost'), ('Off', 'off')]
OPTIONS = [('resolution', 'Resolution', RESOLUTIONS, 'Wider than 16:9 shows more of the level.'),
           ('display', 'Display', DISPLAY_MODES, 'Borderless suits most screens; a full-size window is shrunk.'),
           ('frame_gen', 'Frame generation', FRAME_GENS, 'The game runs 30 steps a second; this shows 60.'),
           ('latency', 'Low latency', LATENCY, 'Less delay between your input and the screen.'),
           ('upscaling', 'Upscaling', UPSCALERS, 'Sharper when the resolution is below your screen\'s.'),
           ('sharpening', 'Sharpening', SHARPENING, 'How much FSR 1 sharpens the scaled picture.')]
# The Debug page's Diagnostics: (key, environment variable, title, hint, default). Each is passed to the game on
# Play as 1 or 0.
DIAGNOSTICS = [('frame_stats', 'BB_FRAME_STATS', 'Frame log', 'Frame rate, pacing and every slow frame in the log.',
                True),
               ('profile', 'LBPPC_PROFILE', 'Slow-frame profiler',
                'What the game waited for in each slow frame, and the CPU each thread used.', False),
               ('positions', 'LBPPC_POS_LOG', 'Player positions',
                'Each player\'s position and buttons at every step. Large logs: for chasing a bug.', False),
               ('no_readback', 'LBPPC_WRITE_FAULT_NO_READBACK', 'Skip GPU readbacks',
                'Faster in busy levels. Turn it off if something flickers or looks wrong.', True),
               ('toggles', 'LBPPC_TOGGLES', 'Optimization switches',
                'Turns optimizations off while the game runs, from user/toggles.txt. For chasing a bug.', False),
               ('friends_log', 'LBPPC_FRIENDS_LOG', 'Friends menu log',
                'What the game\'s Friends menu holds, every 5 seconds while online.', False)]
# The Debug page's Performance overlay: bbport.ini switches, which the in-game menu (Insert) also changes.
OVERLAY = [('show_fps', 'Frame rate', False), ('show_frame_times', 'Frame times', False),
           ('show_latency', 'Render latency', False), ('show_vram', 'Video memory', False),
           ('show_gpu', 'GPU usage', False), ('show_cpu', 'CPU usage', False), ('show_ram', 'RAM usage', False),
           ('show_fps_graph', 'Frame time graph', False), ('graph_game_frames', 'Graph the game\'s own frames', True),
           ('counter_colors', 'Colour-coded values', True)]
DEFAULTS = {'game': '', 'update': '', 'resolution': '1920x1080', 'display': 'borderless', 'frame_gen': 'dlss', 'latency': 'on',
            'upscaling': 'off', 'sharpening': 'medium', 'beta_notice_seen': False,
            'online': False, 'online_name': '', 'password': '',
            'diagnostics': {key: on for key, _env, _title, _hint, on in DIAGNOSTICS},
            # Not shown: the LBPPC server unless launcher.json names another (a test server on this PC).
            'server': online_accounts.OFFICIAL_SERVER}
DLSS_FILES = ('bbport_dlss.dll', 'nvngx_dlss.dll', 'nvngx_dlssg.dll')

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


def check_game(folder, update=''):
    """(ok, description) of a game folder and its update (chosen, or found beside it), read as run.py will: the update
    over the game, each with its Sc0 (scripts/gameview.py)."""
    if not folder:
        return False, "Choose your game's folder (the one holding eboot.bin)."
    game = Path(folder)
    update = Path(update) if update else gameview.find_update(game)
    view = gameview.GameView(game, update)
    if not (view / 'eboot.bin').is_file():
        return False, 'No eboot.bin in this folder' + (' or its update.' if update else '.')
    sfo = read_sfo(view / 'sce_sys/param.sfo')
    title_id, version = sfo.get('TITLE_ID', '?'), sfo.get('APP_VER', '?')
    with_update = f' with its update' if update else ''
    if title_id == 'CUSA00473' and version != '01.28':
        return False, f'Version {version}: LBPPC needs the 1.28 update. Choose it with Update...'
    if title_id not in ('CUSA00473', '?'):
        return True, f'{title_id} version {version}: other regions or editions are untested.'
    missing = [name for name in ('npbind.dat', 'playgo-chunk.dat') if not (view / f'sce_sys/{name}').is_file()]
    if missing:
        return True, f'{title_id} {version}{with_update}, ready. Missing {" and ".join(missing)}: online co-op won\'t work'
    return True, f'{title_id} version {version}{with_update}, ready to play'


def ini_frame_gen():
    """bbport.ini's frame_gen when the launcher offers it: the in-game menu (Insert) changes it there too."""
    try:
        lines = INI.read_text(encoding='utf-8').splitlines()
    except OSError:
        return None
    value = next((line.split('=', 1)[1].strip() for line in lines if line.startswith('frame_gen=')), None)
    return value if value in {v for _name, v in FRAME_GENS} else None


def ini_values():
    """bbport.ini as key -> value (empty when it does not exist yet)."""
    try:
        lines = INI.read_text(encoding='utf-8').splitlines()
    except OSError:
        return {}
    return {line.split('=', 1)[0].strip(): line.split('=', 1)[1].strip() for line in lines
            if '=' in line and not line.lstrip().startswith('#')}


def ini_set(key, value):
    """One bbport.ini line set, the others kept."""
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
    # bbport.ini: frame_gen set, its other lines kept.
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


def make_report():
    """A zip for a bug report: the last game logs, the settings and input files. The Windows user name and
    every public IP address (the player's own, the other players', the server's) are replaced in every file
    and the shadNet password left out; no game files. Returns its path."""
    import ipaddress
    import time
    import zipfile
    reports = ROOT / 'out' / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / time.strftime('LBPPC-report-%Y%m%d-%H%M%S.zip')
    home = str(Path.home())
    names = {home: '<home>', home.replace('\\', '/'): '<home>', os.environ.get('USERNAME', '') or '<none>': '<user>'}

    # Each public address gets one label across the whole report (<ip-1>, <ip-2>, ...), so the logs still
    # tell the peers apart; LAN and loopback addresses stay, they name no one.
    labels = {}

    def label_address(match):
        try:
            address = ipaddress.ip_address(match.group(0))
        except ValueError:
            return match.group(0)  # a version number such as a driver's
        if not address.is_global:
            return match.group(0)
        return labels.setdefault(str(address), f'<ip-{len(labels) + 1}>')

    def scrub(text):
        for name, replacement in names.items():
            if len(name) > 2:
                text = re.sub(re.escape(name), replacement, text, flags=re.IGNORECASE)
        text = re.sub(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])', label_address, text)
        return re.sub(r'(?<![\w:])[0-9a-fA-F]{0,4}(?::[0-9a-fA-F]{0,4}){2,7}(?![\w:])', label_address, text)

    logs = sorted((ROOT / 'out' / 'logs').glob('run-*.log'))[-3:]
    files = [(log, f'logs/{log.name}') for log in logs] + [(LOG, 'last_run.log'), (INI, 'bbport.ini'),
                                                           (ROOT / 'input.ini', 'input.ini')]
    settings = {k: v for k, v in load_settings().items() if k != 'password'}
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for source, name in files:
            if source.is_file():
                archive.writestr(name, scrub(source.read_text(encoding='utf-8', errors='replace')))
        archive.writestr('launcher.json', scrub(json.dumps(settings, indent=2)))
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
    INTRO = ('LBPPC 0.1.0 is a beta. Most of LittleBigPlanet 3 plays well, but you may run into effects that draw '
             'wrongly in some levels, the odd crash, or rough edges online.')
    STEPS = ('Close the game.',
             'Click Make a report at the bottom of the launcher. It saves a zip of the game\'s logs and opens its '
             'folder. Your password and Windows user name are left out.',
             'Post the zip in the bug reports forum on our Discord, with the level and what you were doing.')
    THANKS = 'Every report helps us fix what\'s broken. Thanks for testing!'

    def __init__(self, launcher):
        self.launcher, self.closing, self.dont_show = launcher, False, False
        launcher.update_idletasks()
        lx, ly = launcher.winfo_rootx(), launcher.winfo_rooty()
        lw, lh = launcher.winfo_width(), launcher.winfo_height()
        card, buttons_y = self.draw()
        self.buttons_y = buttons_y
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
        scene = Scene(window, self.W, self.H)
        scene.configure(bg=self.KEY)
        scene.pack(fill='both', expand=True)
        scene.image('card', card, 0, 0)
        self.ok = Tab(scene, 'Got it', self.close, self.W - 30, buttons_y, 'e', size=15)
        self.discord = Tab(scene, 'Join the Discord', lambda: webbrowser.open(DISCORD),
                           self.ok.cx - self.ok.width / 2 - 16, buttons_y, 'e', size=15, fill=BLURPLE)
        self.scene = scene
        box = scene.image('dont:box', theme.checkbox(False), 30, buttons_y - 11)
        label = scene.image('dont:label', theme.text_image("Don't show this again", 13, TEXT, 'Medium'), 60,
                            buttons_y - 10)
        for item in (box, label):
            scene.tag_bind(item, '<Button-1>', lambda _e: self.toggle_dont_show())
            scene.tag_bind(item, '<Enter>', lambda _e: scene.configure(cursor='hand2'))
            scene.tag_bind(item, '<Leave>', lambda _e: scene.configure(cursor=''))
        self.x = lx + (lw - ui_scale.s(self.W)) // 2
        self.y = ly + max(0, (lh - ui_scale.s(self.H)) // 2 - 20)
        window.geometry(f'{ui_scale.s(self.W)}x{ui_scale.s(self.H)}+{self.x}+{self.y + 18}')
        window.protocol('WM_DELETE_WINDOW', self.close)
        window.bind('<Escape>', lambda _e: self.close())
        window.bind('<Return>', lambda _e: self.close())
        shade.bind('<Button-1>', lambda _e: self.nudge())

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

    def draw(self):
        """The card, and where its buttons go."""
        w, pad = self.W, 30
        width = w - 2 * pad
        title = theme.text_image('Welcome to the beta!', 26, theme.hex_colour(theme.ORANGE), 'Bold',
                                 outline=theme.CREAM)
        title = theme.shadowed(title.rotate(1.5, resample=Image.BICUBIC, expand=True), 4, (0, 2))[0]
        intro = theme.paragraph(self.INTRO, 14, width, TEXT, 'Regular')
        heading = theme.text_image('Found a problem?', 15, theme.hex_colour(theme.LIME), 'SemiBold')
        steps = [theme.paragraph(text, 14, width - 42, TEXT, 'Regular') for text in self.STEPS]
        thanks = theme.paragraph(self.THANKS, 13, width, MUTED, 'Regular')
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
    ONLINE_Y = GRAPHICS_Y + 304
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
        self.process = None
        self.lines = queue.Queue()
        self.log_shown = False
        style_widgets(self)
        self.scene = Scene(self, WIDTH, self.HEIGHT)
        self.scene.pack(fill='both', expand=True)
        self.scene.image('felt', theme.felt(WIDTH, self.HEIGHT + LOG_HEIGHT), 0, 0)
        self.build()
        if CUSTOM_FRAME:
            TitleBar(self, self.scene, f'{VERSION} beta', self.close)
        self.friends = FriendsPanel(self, self.SIDE_X, self.GAME_Y, self.SIDE_W, self.ONLINE_Y + 202 - self.GAME_Y)
        self.controls = ControlsPage(self, self.PANEL_X, self.GAME_Y, self.PANEL_W, self.ONLINE_Y + 202 - self.GAME_Y)
        self.debug = DebugPage(self, self.PANEL_X, self.GAME_Y, self.PANEL_W, self.ONLINE_Y + 202 - self.GAME_Y)
        # The pages: Game (the panels built above), Controls and Debug, switched by the tabs on the banner.
        tab_y = TITLE_HEIGHT + BANNER_HEIGHT - 16
        self.page_tabs = {'debug': Tab(self.scene, 'Debug', lambda: self.show_page('debug'),
                                       self.PANEL_X + self.PANEL_W - 4, tab_y, 'e', size=14, tilt=-3)}
        self.page_tabs['controls'] = Tab(self.scene, 'Controls', lambda: self.show_page('controls'),
                                         self.page_tabs['debug'].cx - self.page_tabs['debug'].width / 2 - 14,
                                         tab_y, 'e', size=14, tilt=3)
        self.page_tabs['game'] = Tab(self.scene, 'Game', lambda: self.show_page('game'),
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
        if not self.settings.get('beta_notice_seen'):
            self.after(600, lambda: BetaNotice(self))

    def collect_garbage(self):
        gc.collect()
        self.after(5000, self.collect_garbage)

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
        scene.image('panel:game', theme.panel(w, 104, 'Game folder'), x, y)
        self.game = tk.StringVar(value=self.settings['game'])
        entry = field(self, self.game)
        entry.bind('<FocusOut>', lambda _e: self.refresh_game())
        entry.bind('<Return>', lambda _e: self.refresh_game())
        scene.widget(entry, x + 20, y + 46, w - 300, 30)
        browse = Tab(scene, 'Browse...', self.browse, x + w - 20, y + 61, 'e', size=14, tilt=3)
        # The 1.28 update, when it is a folder of its own (CUSA00473-UPDATE beside the game is found without this).
        Tab(scene, 'Update...', self.choose_update, browse.cx - browse.width / 2 - 12, y + 61, 'e', size=14, tilt=-2)
        self.update_folder = self.settings.get('update', '')
        self.status_tag_y = y + 84

        # Graphics: the choices in two columns
        y = self.GRAPHICS_Y
        scene.image('panel:graphics', theme.panel(w, 290, 'Graphics'), x, y)
        self.choices = {}
        for index, (key, title, values, hint) in enumerate(OPTIONS):
            cx, cy = (x + 20, right)[index % 2], y + 44 + (index // 2) * 76
            self.label(f'label:{key}', cx, cy, title)
            names = [name for name, _value in values]
            current = next((name for name, value in values if value == self.settings[key]), names[0])
            var = tk.StringVar(value=current)
            box = ttk.Combobox(self, textvariable=var, values=names, state='readonly', font=UI_FONT,
                               style='Night.TCombobox')
            box.bind('<<ComboboxSelected>>', lambda _e: (self.store(), self.selection_clear()))
            scene.widget(box, cx, cy + 22, half, 28)
            self.text(cx + 2, cy + 54, hint)
            self.choices[key] = (var, values)

        # Online: one account (name and password) on the LBPPC server's shadNet (sign-in, friends, multiplayer)
        # and Refresh (levels, profiles).
        y = self.ONLINE_Y
        scene.image('panel:online', theme.panel(w, 202, 'Online'), x, y)
        self.online = tk.BooleanVar(value=bool(self.settings['online']))
        box = scene.image('online:box', theme.checkbox(self.online.get()), x + 20, y + 44)
        scene.tag_bind(box, '<Button-1>', lambda _e: self.toggle_online())
        self.label('label:online', x + 50, y + 45, 'Play online on the LBPPC server')
        self.label('label:name', x + 20, y + 76, 'Online name')
        self.label('label:password', right, y + 76, 'Password')
        self.online_name = tk.StringVar(value=self.settings['online_name'])
        self.password = tk.StringVar(value=self.settings['password'])
        for cx, var, hidden in ((x + 20, self.online_name, False), (right, self.password, True)):
            entry = field(self, var, hidden)
            entry.bind('<FocusOut>', lambda _e: (self.store(), self.friends.account_changed()))
            scene.widget(entry, cx, y + 98, half, 30)
        self.text(x + 22, y + 134, '3 to 16 letters, digits, - or _, and a password of 8 or more characters. '
                                   'Only your friends can join your game.')
        self.account_button = Tab(scene, 'Create account', self.create_account, x + 20, y + 174, size=15, tilt=-3)
        self.account_status = self.text(self.account_button.right + 22, y + 174, 'New here? Choose a name and '
                                        'password, then Create account.', anchor='w', width=560)

        self.game_items = [item for item in scene.find_all() if item not in before]

        # The bar: Play (the lime tab, as LBP3's selected item), Stop, the state, the links
        y = self.BAR_Y
        self.play_button = Tab(scene, 'Play', self.play, x + 6, y + 30, size=30, pad=(30, 16), tilt=-4,
                               weight='Bold')
        self.stop_button = Tab(scene, 'Stop', self.stop, self.play_button.right + 24, y + 30, size=17, pad=(15, 10),
                               tilt=3)
        self.stop_button.set_enabled(False)
        dot = self.stop_button.right + 26
        self.state_dot = scene.create_oval(dot, y + 24, dot + 12, y + 36, fill=MUTED, outline='')
        self.state_label = self.text(dot + 20, y + 30, 'Ready', ('Segoe UI Semibold', 11), TEXT, 'w', width=200)
        lx = WIDTH - 28
        for text, action in reversed((('Discord', lambda: webbrowser.open(DISCORD)),
                                      ('Show log', self.toggle_log), ('Make a report', self.report),
                                      ('Log file', lambda: LOG.is_file() and open_path(LOG)),
                                      ('Saves', lambda: open_path(USER)))):
            colour = '#8c9eff' if text == 'Discord' else theme.hex_colour((200, 225, 150))  # Discord's blurple, lighter
            item = self.text(lx, y + 28, text, ('Segoe UI Semibold', 10), colour, 'e')
            scene.tag_bind(item, '<Button-1>', lambda _e, act=action: act())
            scene.tag_bind(item, '<Enter>', lambda _e: scene.configure(cursor='hand2'))
            scene.tag_bind(item, '<Leave>', lambda _e: scene.configure(cursor=''))
            if text == 'Show log':
                self.log_link = item
            box = scene.bbox(item)
            lx = box[0] - 18
        self.text(WIDTH // 2, self.HEIGHT - 16, 'Play your own copy. LBPPC includes no game files. '
                  'Help, news and co-op partners on the Discord.', ('Segoe UI', 8), '#6c7690', 'center')

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
        # Through the update and Sc0 folders, as the game reads them (scripts/gameview.py).
        view = gameview.GameView(game, self.settings.get('update') or gameview.find_update(game)) if game else None
        art = view / 'sce_sys/pic1.png' if view else None
        self.scene.image('banner', theme.banner(WIDTH, BANNER_HEIGHT, art), 0, TITLE_HEIGHT)
        # The game's own name (its param.sfo) as the banner's title; LBPPC is the title bar's.
        sfo = read_sfo(view / 'sce_sys/param.sfo') if view else {}
        # The trademark sign is left out (it joins "LittleBigPlanet" and "3" in the game's own title).
        name = ' '.join(re.sub('[\u2122\u00ae]', ' ', sfo.get('TITLE', '')).split()) or 'Choose your game'
        title = theme.text_image(name, 32, theme.hex_colour(theme.ORANGE), 'Bold', outline=theme.CREAM)
        title = title.rotate(2, resample=Image.BICUBIC, expand=True)
        self.scene.image('banner:title', theme.shadowed(title, 5, (0, 3))[0], 14, TITLE_HEIGHT + BANNER_HEIGHT - 90)
        self.scene.tag_raise('banner:title')
        for tab in getattr(self, 'page_tabs', {}).values():  # the page tabs sit on the banner
            self.scene.tag_raise(tab.item)

    def toggle_online(self):
        self.online.set(not self.online.get())
        self.scene.image('online:box', theme.checkbox(self.online.get()), self.PANEL_X + 20, self.ONLINE_Y + 44)
        self.store()

    def set_account_status(self, text, colour):
        self.scene.itemconfigure(self.account_status, text=text, fill=colour)

    def browse(self):
        folder = filedialog.askdirectory(title='Game folder (holding eboot.bin)', initialdir=self.game.get() or None)
        if folder:
            self.game.set(folder)
            self.refresh_game()

    def choose_update(self):
        if self.update_folder:
            keep = messagebox.askyesnocancel(
                'Update folder', f'The update is read from:\n{self.update_folder}\n\nChoose another folder? '
                '(No stops using it.)', parent=self)
            if keep is None:
                return
            if not keep:
                self.update_folder = ''
                self.refresh_game()
                return
        folder = filedialog.askdirectory(title='Update folder (the 1.28 update, holding eboot.bin)',
                                         initialdir=self.update_folder or self.game.get() or None)
        if folder:
            self.update_folder = folder
            self.refresh_game()

    def refresh_game(self):
        ok, text = check_game(self.game.get().strip(), getattr(self, 'update_folder', ''))
        image, margin = theme.tag(text, (70, 140, 40) if ok else (190, 60, 50))
        item = self.scene.image('game:tag', image, self.PANEL_X + 16 - margin, self.status_tag_y - margin)
        self.scene.itemconfigure(item, state='normal' if getattr(self, 'page', 'game') == 'game' else 'hidden')
        self.play_button.set_enabled(ok and not self.process)
        changed = self.settings['game'] != self.game.get().strip()
        self.store()
        if changed or 'banner' not in self.scene.images:
            self.draw_banner()
        return ok

    def store(self):
        self.settings['game'] = self.game.get().strip()
        if hasattr(self, 'update_folder'):
            self.settings['update'] = self.update_folder
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
            path = make_report()
        except OSError as error:
            self.set_state(f'The report could not be made: {error}', ERROR)
            return
        self.set_state(f'Report saved: {path.name}', GOOD)
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
        self.set_account_status('Creating the account...', MUTED)
        result = {}

        def work():
            try:
                online_accounts.create_account(servers['shadnet'], servers['refresh'], values['online_name'],
                                               values['password'])
                result['ok'] = f'Account {values["online_name"]} created: press Play.'
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
        self.scene.itemconfigure(self.log_link, text='Hide log' if self.log_shown else 'Show log')
        self.geometry(f'{ui_scale.s(WIDTH)}x{ui_scale.s(height)}')

    def play(self):
        if self.process or not self.refresh_game():
            return
        if self.friends.busy:
            # shadNet takes one sign-in per account: the game's would fail while the friends list is signed in.
            self.set_state('Starting...', WARN)
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
        if self.settings.get('update'):
            env['LBPPC_UPDATE'] = self.settings['update']  # run.py reads the game through it (scripts/gameview.py)
        # The player's name in the game (pressing the right stick shows it), offline too.
        if re.fullmatch(online_accounts.NAME_PATTERN, self.settings['online_name']):
            env['BB_USER_NAME'] = self.settings['online_name']
        if self.settings['online']:
            name, password = self.settings['online_name'], self.settings['password']
            try:
                online_accounts.check_name_and_password(name, password)
                servers = online_accounts.server_endpoints(self.settings['server'])
            except online_accounts.AccountError as error:
                self.set_state(f'Online: {error}', ERROR)
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
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        self.clear_log()
        if self.settings['frame_gen'] == 'dlss' and not all((BIN / name).is_file() for name in DLSS_FILES):
            # The game would quietly use FSR 3.1 instead (packaging/windows/import_dlss.py installs them).
            self.append(['Launcher: the DLSS files are missing next to the game; frame generation uses FSR 3.1.'])
        command = [sys.executable, '--script'] if FROZEN else [sys.executable]
        self.process = subprocess.Popen(command + [str(ROOT / 'run.py'), self.settings['game']], cwd=ROOT,
                                        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, creationflags=flags)
        threading.Thread(target=self.read_output, args=(self.process,), daemon=True).start()
        self.play_button.set_enabled(False)
        self.stop_button.set_enabled(True)
        self.set_state('Starting...', WARN)
        self.friends.game_running(True)

    def read_output(self, process):
        for raw in process.stdout:
            self.lines.put(raw.decode('utf-8', 'replace').rstrip('\r\n'))
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
                if 'Frame rate' in item or 'Pipeline cache' in item:
                    self.set_state('Running', GOOD)
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
        if code == 0:
            self.set_state('Closed', MUTED)
        else:
            self.set_state('Ended with an error: see the log', ERROR)
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
    bbport.ini at once (the game reads it at start; Insert in the game changes them live), and the diagnostics the
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
        scene.image('panel:overlay', theme.panel(w, overlay_h, 'Performance overlay'), x, y)
        values = ini_values()
        self.overlay = {key: values.get(key, '1' if on else '0') == '1' for key, _title, on in OVERLAY}
        for index, (key, title, _on) in enumerate(OVERLAY):
            cx = (x + 20, right)[index // rows]
            cy = y + 44 + (index % rows) * self.ROW
            self.switch(f'overlay:{key}', cx, cy, title, self.overlay[key], lambda k=key: self.toggle_overlay(k))
        self.overlay_status = launcher.text(x + 22, y + overlay_h - 24, 'Shown in the top right of the game. '
                                            'Insert in the game changes these while you play.', HINT_FONT, MUTED,
                                            'nw', width=w - 44)

        # Diagnostics: a switch and what it does
        dy = y + overlay_h + 12
        scene.image('panel:diagnostics', theme.panel(w, h - overlay_h - 12, 'Diagnostics'), x, dy)
        for index, (key, _variable, title, hint, _on) in enumerate(DIAGNOSTICS):
            cy = dy + 44 + index * 46
            self.switch(f'diag:{key}', x + 20, cy, title, launcher.settings['diagnostics'].get(key),
                        lambda k=key: self.toggle_diagnostic(k))
            launcher.text(x + 52, cy + 22, hint, HINT_FONT, MUTED, 'nw', width=w - 74)
        self.diag_status = launcher.text(x + 22, dy + h - overlay_h - 12 - 24, 'Used the next time you press Play. '
                                         'Logs go in the report from Make a report.', HINT_FONT, MUTED, 'nw',
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
            self.scene.itemconfigure(self.overlay_status, text=f'bbport.ini could not be saved: {error}', fill=ERROR)
            return
        text = 'Saved.' + (' Used the next time the game starts, or press Insert in the game.'
                           if self.launcher.process else '')
        self.scene.itemconfigure(self.overlay_status, text=text, fill=GOOD)

    def toggle_diagnostic(self, key):
        diagnostics = self.launcher.settings['diagnostics']
        diagnostics[key] = not diagnostics.get(key)
        x, y = self.box_position(f'diag:{key}')
        self.redraw(f'diag:{key}', (x, y, diagnostics[key]))
        save_settings(self.launcher.settings)
        text = 'Saved.' + (' Used the next time you press Play.' if self.launcher.process else '')
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
        scene.image('panel:buttons', theme.panel(w, bh, 'Buttons'), x, by)
        self.reset_link = launcher.text(x + w - 20, by + 21, 'Reset to defaults', ('Segoe UI Semibold', 10),
                                        theme.hex_colour((200, 225, 150)), 'e')
        scene.tag_bind(self.reset_link, '<Button-1>', lambda _e: self.reset())
        scene.tag_bind(self.reset_link, '<Enter>', lambda _e: scene.configure(cursor='hand2'))
        scene.tag_bind(self.reset_link, '<Leave>', lambda _e: scene.configure(cursor=''))
        mouse = input_settings.MOUSE_SUPPORTED
        for column, title in zip((x + 14 + 12,) + tuple(x + 14 + c for c in self.COLUMNS),
                                 ('Game button', 'Key or mouse' if mouse else 'Key', 'Second key', 'Controller')):
            launcher.text(column, by + 46, title, ('Segoe UI Semibold', 9), MUTED)
        self.status = launcher.text(x + 20, by + bh - 22, f'Click a key, then press the new key'
                                    f'{" or mouse button" if mouse else ""}. Right-click a key to clear it.',
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
        scene.image('panel:mouse', theme.panel(w, mouse_h, 'Mouse'), x, y)
        launcher.label('label:mouse_mode', x + 20, y + 44, 'Mouse in the game')
        names = [name for _value, name in input_settings.MOUSE_MODES]
        self.mouse_mode = tk.StringVar(value=dict(input_settings.MOUSE_MODES)[self.model.mouse_mode])
        box = ttk.Combobox(launcher, textvariable=self.mouse_mode, values=names, state='readonly', font=UI_FONT,
                           style='Night.TCombobox')
        box.bind('<<ComboboxSelected>>', lambda _e: (self.mouse_changed(), launcher.selection_clear()))
        scene.widget(box, x + 20, y + 66, half, 28)
        launcher.label('label:speed', right, y + 44, 'Sensitivity')
        self.speed = Slider(scene, right + 8, y + 80, half - 8, *input_settings.SPEED_RANGE, self.model.speed,
                            self.speed_changed)
        launcher.label('label:vertical', x + 20, y + 106, 'Up and down, compared with sideways')
        self.vertical = Slider(scene, x + 28, y + 142, half - 8, *input_settings.VERTICAL_RANGE, self.model.vertical,
                               self.vertical_changed)
        self.invert_at = (right, y + 130)
        self.invert_box = scene.image('invert:box', theme.checkbox(self.model.invert_y), *self.invert_at)
        scene.tag_bind(self.invert_box, '<Button-1>', lambda _e: self.toggle_invert())
        launcher.label('label:invert', right + 30, y + 131, 'Invert up and down')
        self.mouse_hint = launcher.text(right + 2, y + 156, '', HINT_FONT, MUTED, 'nw', width=half)

    def update_mouse_hint(self):
        if not input_settings.MOUSE_SUPPORTED:
            return
        mode = self.model.mouse_mode
        text = {'off': 'The game does not use the mouse.',
                'touchpad': 'F7 captures it. Wheel resizes, Ctrl + wheel rotates.'}.get(
                    mode, 'F7 in the game captures the mouse and frees it.')
        self.scene.itemconfigure(self.mouse_hint, text=text)

    def mouse_changed(self):
        self.model.mouse_mode = next(value for value, name in input_settings.MOUSE_MODES
                                     if name == self.mouse_mode.get())
        self.update_mouse_hint()
        self.save('Mouse: ' + self.mouse_mode.get() + '.')

    def speed_changed(self, value):
        self.model.speed = value
        self.save(f'Sensitivity {self.model.speed:.2f}x.')

    def vertical_changed(self, value):
        self.model.vertical = value
        self.save(f'Up and down sensitivity {value:.2f}x of sideways.')

    def toggle_invert(self):
        self.model.invert_y = not self.model.invert_y
        self.scene.image('invert:box', theme.checkbox(self.model.invert_y), *self.invert_at)
        self.save('Up and down inverted.' if self.model.invert_y else 'Up and down not inverted.')

    # Saving

    def set_status(self, text, colour=MUTED):
        self.scene.itemconfigure(self.status, text=text, fill=colour)

    def save(self, done):
        try:
            self.model.save(INPUT_INI)
        except OSError as error:
            self.set_status(f'input.ini could not be saved: {error}', ERROR)
            return
        if self.launcher.process:
            done += ' Press F8 in the game to use it now.'
        self.set_status(done, GOOD)

    def reset(self):
        if not self.confirm_reset:
            self.confirm_reset = True
            self.scene.itemconfigure(self.reset_link, text='Reset everything? Click again', fill=ERROR)
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
        self.save('Controls reset to the defaults.')

    def cancel_reset(self):
        self.confirm_reset = False
        self.scene.itemconfigure(self.reset_link, text='Reset to defaults', fill=theme.hex_colour((200, 225, 150)))

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
        pad_labels = ['None'] + [input_settings.PAD_LABELS[name] for name in input_settings.PAD_BUTTONS +
                                 input_settings.PAD_HALF_AXES]
        pad_names = [None] + input_settings.PAD_BUTTONS + input_settings.PAD_HALF_AXES
        for row, (output, label, hint) in enumerate(input_settings.ACTIONS):
            y = 6 + row * self.ROW
            cy = y + self.ROW // 2 - 3
            if row % 2:
                view.create_rectangle(0, y - 2, int(view['width']), y + self.ROW - 4, fill='#141a2b', outline='')
            item = view.create_text(12, cy, text=label, anchor='w', font=('Segoe UI Semibold', 10), fill=TEXT)
            if hint:
                view.create_text(view.bbox(item)[2] + 8, cy, text=hint, anchor='w', font=HINT_FONT, fill=MUTED)
            for slot in (0, 1):
                self.draw_cell(output, slot, y)
            if output in input_settings.STICK_OUTPUTS:
                view.create_text(self.COLUMNS[2] + 6, cy, text='The stick itself', anchor='w', font=HINT_FONT,
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
        text = 'Press a key...' if capturing else input_settings.key_label(name) if name else 'Add'
        view.create_text(x + self.CELL_W // 2, y + 15, text=text, font=UI_FONT, tags=(tag,),
                         fill=theme.hex_colour(theme.ORANGE) if capturing else TEXT if name else MUTED)
        view.tag_bind(tag, '<ButtonRelease-1>', lambda _e: self.start_capture(output, slot))
        view.tag_bind(tag, '<Button-3>', lambda _e: self.clear(output, slot))
        view.tag_bind(tag, '<Enter>', lambda _e: view.configure(cursor='hand2'))
        view.tag_bind(tag, '<Leave>', lambda _e: view.configure(cursor=''))

    def row_y(self, output):
        return 6 + [o for o, _l, _h in input_settings.ACTIONS].index(output) * self.ROW

    def label_of(self, output):
        return next(label for o, label, _h in input_settings.ACTIONS if o == output)

    def pad_changed(self, output, name, shown):
        self.model.set_pad(output, name)
        self.launcher.selection_clear()
        self.save(f'{self.label_of(output)}: {shown} on the controller.')

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
        self.set_status('Press the new key or mouse button for ' + self.label_of(output) + ' (Esc cancels).',
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
            self.set_status('Not changed.')
            return 'break'
        name = tk_key_name(event)
        if not name:
            self.set_status(f'That key ({event.keysym}) cannot be used. Press another, or Esc.', ERROR)
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
            self.set_status(f'{input_settings.key_label(name)} {input_settings.RESERVED_KEYS[name]} in the game. '
                            'Press another key, or Esc.', ERROR)
            return
        self.stop_capture()
        moved = self.model.bind_key(output, slot, name)
        self.draw_list_row(output)
        if moved:
            self.draw_list_row(moved)
        label = input_settings.key_label(name)
        self.save(f'{label} moved from {self.label_of(moved)} to {self.label_of(output)}.' if moved else
                  f'{self.label_of(output)}: {label}.')


class FriendsPanel:
    """The player's shadNet friends, beside the settings: a name to send a request to, then the requests to answer,
    the friends (online first), and the requests sent, each in its own card. Friends are the players who may join
    this player's game (signaling_handler.cpp's join protection).

    shadNet takes one sign-in per account, so the list signs in only for a moment (to read or change it) and never
    while the game runs; Play waits for a sign-in in progress."""

    REFRESH_MS = 60000
    LIST_BG = '#0f1320'  # the panel's colour over the felt
    SECTIONS = (('received', 'Friend requests', theme.ORANGE), ('friends', 'Friends', theme.GREEN),
                ('sent', 'Sent requests', theme.GREY))

    def __init__(self, launcher, x, y, w, h):
        self.launcher, self.scene = launcher, launcher.scene
        self.busy = self.paused = False
        self.friends = None
        self.signed_in_as = None
        self.tabs, self.confirm = [], None
        self.scene.image('panel:friends', theme.panel(w, h, 'Friends'), x, y)
        self.add_name = tk.StringVar()
        self.add_button = Tab(self.scene, 'Add', self.send_request, x + w - 18, y + 61, 'e', size=14, tilt=3)
        entry = field(launcher, self.add_name)
        entry.bind('<Return>', lambda _e: self.send_request())
        self.scene.widget(entry, x + 20, y + 46, w - 40 - self.add_button.width - 14, 30)
        self.status = self.scene.create_text(x + 21, y + 84, anchor='nw', font=HINT_FONT, fill=MUTED, width=w - 42,
                                             text='Add a friend by their online name.')
        self.list = Scene(launcher, w - 28, h - 206)
        self.list.configure(bg=self.LIST_BG)
        self.list_w = w - 28
        self.scene.widget(self.list, x + 14, y + 112, w - 28, h - 206)
        # Under the list, for players with nobody to add yet
        self.scene.create_text(x + 21, y + h - 72, anchor='w', font=HINT_FONT, fill=MUTED, width=w - 42,
                               text='Find people to play with, get help and hear about updates:')
        self.discord = Tab(self.scene, 'Join the Discord', lambda: webbrowser.open(DISCORD), x + w / 2, y + h - 34,
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
            self.set_status('Add a friend by their online name.', MUTED)
            return
        if force or account != self.signed_in_as:
            self.reload()

    def tick(self):
        if self.signed_in_as and not self.paused:
            self.run(None, quiet=True)
        self.launcher.after(self.REFRESH_MS, self.tick)

    def game_running(self, running):
        self.paused = running
        if running:
            self.set_status('The list updates again when the game closes.', MUTED)
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
                self.set_status('Create an account (Online, on the left) to add friends.', ERROR)
            return
        self.busy = True
        if not quiet:
            self.set_status('Working...' if action else 'Signing in...', MUTED)
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

        def finished():
            if thread.is_alive():
                self.launcher.after(150, finished)
                return
            self.busy = False
            if 'error' in result:
                self.set_status(result['error'], ERROR)
                return
            self.friends, self.signed_in_as = result['friends'], account
            self.draw()
            if done_text:
                self.set_status(done_text, GOOD)
            elif not quiet or self.scene.itemcget(self.status, 'fill') == ERROR:
                self.set_status(f'Signed in as {account[1]}.', MUTED)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self.launcher.after(150, finished)

    def reload(self):
        self.run(None)

    def send_request(self):
        other = self.add_name.get().strip()
        account = self.account()
        if not re.fullmatch(online_accounts.NAME_PATTERN, other) or (account and other.lower() == account[1].lower()):
            self.set_status('Type another player\'s online name.', ERROR)
            return
        self.add_name.set('')
        self.run(lambda session: session.add(other), f'Request sent to {other}. They show as a friend once they '
                                                      'accept.')

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
            self.run(lambda session: session.remove(other), f'Removed {other}.')
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
            message = ('Create an account (Online, on the left) and your friends show here: requests to answer, '
                       'friends, and requests you sent.') if not self.account() else 'Signing in...'
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
            heading = f'{title.upper()}  ({len(items)})'
            if key == 'friends' and items:
                heading += f'   {sum(1 for _n, online in items if online)} online'
            view.create_text(18, y + 20, text=heading, anchor='w', font=('Segoe UI Semibold', 10),
                             fill=theme.hex_colour(accent) if key != 'sent' else MUTED)
            row_y = y + 38
            if empty:
                view.create_text(18, row_y + 26, anchor='w', width=w - 34, font=HINT_FONT, fill=MUTED,
                                 text='No friends yet. Type a friend\'s online name above and press Add; once they '
                                      'accept, they show here and can join your game.')
            for name, online in items:
                cy = row_y + 16
                right = w - 14
                if key == 'received':
                    accept = Tab(view, 'Accept', lambda o=name: self.run(lambda s: s.add(o), f'You and {o} are friends.'),
                                 right, cy, 'e', size=12, pad=(9, 7))
                    self.tabs.append(accept)
                    self.link(accept.cx - accept.width / 2 - 12, cy, 'Decline',
                              lambda o=name: self.run(lambda s: s.remove(o), f'Declined {o}.'))
                    view.create_text(18, cy, text=name, anchor='w', font=('Segoe UI Semibold', 10), fill=TEXT)
                elif key == 'friends':
                    dot = GOOD if online else '#5a6278'
                    view.create_oval(18, cy - 5, 28, cy + 5, fill=dot, outline='')
                    label = view.create_text(36, cy - 1, text=name, anchor='w', font=('Segoe UI Semibold', 10),
                                             fill=TEXT)
                    view.create_text(view.bbox(label)[2] + 8, cy, text='online' if online else 'offline', anchor='w',
                                     font=('Segoe UI', 8), fill=GOOD if online else MUTED)
                    if self.confirm == name:
                        self.link(right, cy, 'Remove?', lambda o=name: self.remove_friend(o), ERROR)
                    else:
                        self.link(right, cy, 'Remove', lambda o=name: self.remove_friend(o))
                else:
                    view.create_text(18, cy, text=name, anchor='w', font=UI_FONT, fill=MUTED)
                    self.link(right, cy, 'Cancel', lambda o=name: self.run(lambda s: s.remove(o),
                                                                           f'Cancelled the request to {o}.'))
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
    else:
        # The launcher at the screen's size (ui_scale.py), and on a short screen (the Steam Deck's) a shorter log.
        ui_scale.install(ui_scale.choose(WIDTH, Launcher.HEIGHT))
        room = ui_scale.work_area()[1] * 0.96 / ui_scale.U - Launcher.HEIGHT
        LOG_HEIGHT = max(100, min(LOG_HEIGHT, int(room)))
        Launcher().mainloop()
