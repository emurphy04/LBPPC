"""input.ini for the launcher's Controls page: the bindings and mouse settings the page edits, read from and written
back to the file the game reads (src/runtime_input_config.c parses it; shadPS4 syntax, "output = input", one input
per line).

Only plain one-input lines of the actions below and the mouse settings are edited here. Everything else (combos,
analog_deadzone, stick_* settings, hotkeys, outputs the page does not show) is kept as it was, in its own section.
The names are the parser's own (tests/test_launcher_input.py checks them against runtime_input_config.c).
"""
from pathlib import Path

# Keyboard keys, as runtime_input_config.c's kKeyNames.
KEY_NAMES = (
    [chr(c) for c in range(ord('a'), ord('z') + 1)] + [str(d) for d in range(10)] + [f'f{n}' for n in range(1, 13)] +
    ['escape', 'backspace', 'delete', 'insert', 'home', 'end', 'pgup', 'pgdown', 'tab', 'capslock', 'enter',
     'lshift', 'rshift', 'lctrl', 'rctrl', 'lalt', 'ralt', 'lmeta', 'rmeta', 'lwin', 'rwin',
     'space', 'up', 'down', 'left', 'right'] +
    [f'kp{d}' for d in range(10)] +
    ['kpperiod', 'kpcomma', 'kpslash', 'kpasterisk', 'kpminus', 'kpplus', 'kpequals', 'kpenter',
     'grave', 'minus', 'equals', 'lbracket', 'rbracket', 'backslash', 'semicolon', 'apostrophe',
     'comma', 'period', 'slash', 'printscreen', 'scrolllock', 'pausebreak'])
# The mouse isn't offered yet: as the touchpad it stutters (the finger runs out of pad), and the buttons do nothing
# without it. Off, the page has no mouse settings, mouse buttons aren't bound and input.ini is saved with the mouse off.
MOUSE_SUPPORTED = False
MOUSE_NAMES = ['leftbutton', 'middlebutton', 'rightbutton', 'sidebuttonback', 'sidebuttonforward',
               'mousewheelup', 'mousewheeldown', 'mousewheelleft', 'mousewheelright']
# Controller inputs a button-like action can take: kCButtonNames, the triggers, and the half sticks.
PAD_BUTTONS = ['cross', 'circle', 'square', 'triangle', 'l1', 'r1', 'l2', 'r2', 'l3', 'r3', 'options', 'back',
               'pad_up', 'pad_down', 'pad_left', 'pad_right', 'lpaddle_high', 'lpaddle_low', 'rpaddle_high',
               'rpaddle_low', 'qam']
PAD_HALF_AXES = ['axis_left_x_minus', 'axis_left_x_plus', 'axis_left_y_minus', 'axis_left_y_plus',
                 'axis_right_x_minus', 'axis_right_x_plus', 'axis_right_y_minus', 'axis_right_y_plus']
PAD_NAMES = PAD_BUTTONS + PAD_HALF_AXES + ['axis_left_x', 'axis_left_y', 'axis_right_x', 'axis_right_y']
from i18n import N_  # noqa: E402

# Keys the game keeps for itself (runtime_input_config.c: the menu key, F9 recording) and the hotkeys' defaults.
RESERVED_KEYS = {'insert': N_('opens the in-game menu'), 'f9': N_('records input'), 'f7': N_('captures the mouse'),
                 'f8': N_('reloads the controls')}

# The actions the page shows: (output, label, what it does in LittleBigPlanet 3 where known).
ACTIONS = [
    ('cross', N_('Cross'), N_('jump')), ('circle', N_('Circle'), ''), ('square', N_('Square'), 'Popit'),
    ('triangle', N_('Triangle'), ''),
    ('l1', 'L1', ''), ('r1', 'R1', N_('grab')), ('l2', 'L2', ''), ('r2', 'R2', ''), ('l3', 'L3', ''), ('r3', 'R3', ''),
    ('options', 'Options', N_('pause')), ('touchpad_left', N_('Touchpad (left)'), ''),
    ('touchpad_right', N_('Touchpad (right)'), ''),
    ('pad_up', N_('D-pad up'), ''), ('pad_down', N_('D-pad down'), ''), ('pad_left', N_('D-pad left'), ''),
    ('pad_right', N_('D-pad right'), ''),
    ('axis_left_y_minus', N_('Left stick up'), N_('move')), ('axis_left_y_plus', N_('Left stick down'), N_('move')),
    ('axis_left_x_minus', N_('Left stick left'), N_('move')), ('axis_left_x_plus', N_('Left stick right'), N_('move')),
    ('axis_right_y_minus', N_('Right stick up'), ''), ('axis_right_y_plus', N_('Right stick down'), ''),
    ('axis_right_x_minus', N_('Right stick left'), ''), ('axis_right_x_plus', N_('Right stick right'), ''),
    ('tilt_left', N_('Tilt left'), N_('head')), ('tilt_right', N_('Tilt right'), N_('head')),
    ('tilt_forward', N_('Tilt forward'), N_('head')), ('tilt_back', N_('Tilt back'), N_('head')),
    ('leftjoystick_halfmode', N_('Walk (half left stick)'), ''),
]
ACTION_OUTPUTS = {output for output, _label, _hint in ACTIONS}
# Stick directions take their controller binding from the whole stick (axis_left_x = axis_left_x and so on), so the
# page edits only their keys.
STICK_OUTPUTS = {output for output in ACTION_OUTPUTS if output.startswith('axis_')}

MOUSE_MODES = [('off', N_('Off')), ('right', N_('Look around (right stick)')), ('left', N_('Move (left stick)')),
               ('touchpad', N_('Touchpad (Create Mode)'))]
SPEED_RANGE = (0.2, 5.0)
VERTICAL_RANGE = (0.25, 4.0)

# runtime_input_config.c's default text (DEFAULT_HEADER .. DEFAULT_HOTKEYS); tests/test_launcher_input.py keeps
# the two the same.
DEFAULT_TEXT = """\
# bbport input (shadPS4 syntax: output = input, one input per line).
# A shadPS4 input config (user/input_config/CUSA03173.ini) can be copied over this file.
# F7 toggles the mouse, F8 reloads this file.

# Keyboard
cross = space
circle = lshift
square = e
triangle = v
l1 = 1
r1 = 3
l2 = r
r2 = f
l3 = z
r3 = q
r3 = c
options = enter
pad_up = i
pad_down = k
pad_left = j
pad_right = l
touchpad_left = tab
touchpad_right = backspace

axis_left_x_minus = a
axis_left_x_plus = d
axis_left_y_minus = w
axis_left_y_plus = s
axis_right_x_minus = left
axis_right_x_plus = right
axis_right_y_minus = up
axis_right_y_plus = down

# Tilt the pad (Sackboy's head, tilt sensors) without a motion-sensing controller
tilt_left = kp4
tilt_right = kp6
tilt_forward = kp8
tilt_back = kp2

# Hold to halve the left stick (walk): uncomment and pick a key
# leftjoystick_halfmode = lalt

# Mouse (uncomment mouse_to_joystick to turn it on; buttons work only while captured)
# mouse_to_joystick = right
# Or the mouse as the touchpad, for touch Create Mode: move = finger, left button = press,
# wheel = spread/pinch (size), ctrl + wheel = twist (rotate)
# mouse_to_joystick = touchpad
mouse_movement_params = 0.5, 1, 0.125
r1 = leftbutton
r2 = rightbutton
circle = sidebuttonback
square = sidebuttonforward

# Controller
cross = cross
circle = circle
square = square
triangle = triangle
l1 = l1
r1 = r1
l2 = l2
r2 = r2
l3 = l3
r3 = r3
options = options
touchpad_left = back
pad_up = pad_up
pad_down = pad_down
pad_left = pad_left
pad_right = pad_right
axis_left_x = axis_left_x
axis_left_y = axis_left_y
axis_right_x = axis_right_x
axis_right_y = axis_right_y

# Hotkeys
hotkey_toggle_mouse_to_joystick = f7
hotkey_reload_inputs = f8
"""

KEY_LABELS = {
    'space': 'Space', 'lshift': 'Left Shift', 'rshift': 'Right Shift', 'lctrl': 'Left Ctrl', 'rctrl': 'Right Ctrl',
    'lalt': 'Left Alt', 'ralt': 'Right Alt', 'lmeta': 'Left Win', 'rmeta': 'Right Win', 'lwin': 'Left Win',
    'rwin': 'Right Win', 'enter': 'Enter', 'tab': 'Tab', 'backspace': 'Backspace', 'escape': 'Esc',
    'delete': 'Delete', 'insert': 'Insert', 'home': 'Home', 'end': 'End', 'pgup': 'Page Up', 'pgdown': 'Page Down',
    'capslock': 'Caps Lock', 'up': 'Up', 'down': 'Down', 'left': 'Left', 'right': 'Right',
    'kpperiod': 'Num .', 'kpcomma': 'Num ,', 'kpslash': 'Num /', 'kpasterisk': 'Num *', 'kpminus': 'Num -',
    'kpplus': 'Num +', 'kpequals': 'Num =', 'kpenter': 'Num Enter', 'grave': '`', 'minus': '-', 'equals': '=',
    'lbracket': '[', 'rbracket': ']', 'backslash': '\\', 'semicolon': ';', 'apostrophe': "'", 'comma': ',',
    'period': '.', 'slash': '/', 'printscreen': 'Print Screen', 'scrolllock': 'Scroll Lock', 'pausebreak': 'Pause',
    'leftbutton': 'Left mouse', 'middlebutton': 'Middle mouse', 'rightbutton': 'Right mouse',
    'sidebuttonback': 'Mouse back', 'sidebuttonforward': 'Mouse forward', 'mousewheelup': 'Wheel up',
    'mousewheeldown': 'Wheel down', 'mousewheelleft': 'Wheel left', 'mousewheelright': 'Wheel right',
}
PAD_LABELS = {
    'cross': N_('Cross'), 'circle': N_('Circle'), 'square': N_('Square'), 'triangle': N_('Triangle'), 'l1': 'L1', 'r1': 'R1',
    'l2': 'L2', 'r2': 'R2', 'l3': 'L3', 'r3': 'R3', 'options': N_('Options'), 'back': N_('Share / Back'),
    'pad_up': N_('D-pad up'), 'pad_down': N_('D-pad down'), 'pad_left': N_('D-pad left'), 'pad_right': N_('D-pad right'),
    'lpaddle_high': N_('Left paddle 1'), 'lpaddle_low': N_('Left paddle 2'), 'rpaddle_high': N_('Right paddle 1'),
    'rpaddle_low': N_('Right paddle 2'), 'qam': N_('Extra button'),
    'axis_left_x_minus': N_('Left stick left'), 'axis_left_x_plus': N_('Left stick right'),
    'axis_left_y_minus': N_('Left stick up'), 'axis_left_y_plus': N_('Left stick down'),
    'axis_right_x_minus': N_('Right stick left'), 'axis_right_x_plus': N_('Right stick right'),
    'axis_right_y_minus': N_('Right stick up'), 'axis_right_y_plus': N_('Right stick down'),
}


def key_label(name):
    if name in KEY_LABELS:
        return KEY_LABELS[name]
    if name.startswith('kp') and name[2:].isdigit():
        return f'Num {name[2:]}'
    return name.upper()


def _split(line):
    """(output, input) of a plain binding or setting line, or None for comments, blanks and anything odd."""
    text = line.split('#', 1)[0].strip()
    if '=' not in text:
        return None
    output, value = (part.strip().lower() for part in text.split('=', 1))
    return (output, value) if output and value else None


class InputSettings:
    """The page's view of input.ini: per action, its keyboard/mouse inputs and controller inputs; the mouse mode and
    sensitivity; and every other line, kept."""

    def __init__(self, text=DEFAULT_TEXT):
        self.keys = {output: [] for output in ACTION_OUTPUTS}
        self.pad = {output: [] for output in ACTION_OUTPUTS}
        self.mouse_mode = 'off'
        self.movement = [0.5, 1.0, 0.125]  # mouse_movement_params: deadzone offset, speed, speed offset
        self.invert_y = False
        self.vertical = 1.0
        self.other = []  # lines kept as they were
        for line in text.splitlines():
            parsed = _split(line)
            if not parsed:
                continue
            output, value = parsed
            if output == 'mouse_to_joystick':
                self.mouse_mode = value if value in {'left', 'right', 'touchpad'} else 'off'
            elif output == 'mouse_movement_params':
                try:
                    numbers = [float(v) for v in value.split(',')]
                    if len(numbers) == 3:
                        self.movement = numbers
                except ValueError:
                    self.other.append(line.strip())
            elif output == 'mouse_invert_y':
                self.invert_y = value == 'on'
            elif output == 'mouse_sensitivity_y':
                try:
                    self.vertical = float(value)
                except ValueError:
                    self.other.append(line.strip())
            elif output in ACTION_OUTPUTS and ',' not in value and value in KEY_NAMES + MOUSE_NAMES:
                self.keys[output].append(value)
            elif output in ACTION_OUTPUTS and ',' not in value and value in PAD_NAMES and output not in STICK_OUTPUTS:
                self.pad[output].append(value)
            else:
                self.other.append(line.strip())
        if not MOUSE_SUPPORTED:
            self.mouse_mode = 'off'
            self.keys = {output: [key for key in keys if key not in MOUSE_NAMES] for output, keys in self.keys.items()}

    @classmethod
    def load(cls, path):
        try:
            return cls(Path(path).read_text(encoding='utf-8'))
        except OSError:
            return cls()

    @property
    def speed(self):
        return self.movement[1]

    @speed.setter
    def speed(self, value):
        self.movement[1] = round(min(max(value, SPEED_RANGE[0]), SPEED_RANGE[1]), 2)

    def owner(self, name):
        """The action a keyboard/mouse input is bound to, or None."""
        return next((output for output, names in self.keys.items() if name in names), None)

    def bind_key(self, output, slot, name):
        """`name` as the action's key in `slot` (0 first, 1 second), taken from any action that had it. Returns that
        action, or None."""
        previous = self.owner(name)
        if previous:
            self.keys[previous].remove(name)
        names = self.keys[output]
        if slot < len(names):
            names[slot] = name
        else:
            names.append(name)
        return previous if previous != output else None

    def clear_key(self, output, slot):
        if slot < len(self.keys[output]):
            del self.keys[output][slot]

    def set_pad(self, output, name):
        """The action's controller input (None: none). Further controller inputs of the action are kept."""
        names = self.pad[output]
        if name is None:
            if names:
                del names[0]
        elif names:
            names[0] = name
        else:
            names.append(name)

    def text(self):
        """input.ini as the page writes it: keyboard and mouse, mouse settings, controller, then the kept lines."""
        def number(value):
            return f'{value:g}'
        lines = ['# LBPPC input (shadPS4 syntax: output = input, one input per line). The launcher\'s Controls',
                 '# page writes this file; F7 captures the mouse in game, F8 reloads this file.', '',
                 '# Keyboard and mouse']
        for output, _label, _hint in ACTIONS:
            lines += [f'{output} = {name}' for name in self.keys[output]]
        lines += ['', '# Mouse']
        lines.append(f'mouse_to_joystick = {self.mouse_mode}' if self.mouse_mode != 'off' else
                     '# mouse_to_joystick = right')
        lines.append('mouse_movement_params = ' + ', '.join(number(v) for v in self.movement))
        if self.invert_y:
            lines.append('mouse_invert_y = on')
        if self.vertical != 1.0:
            lines.append(f'mouse_sensitivity_y = {number(self.vertical)}')
        lines += ['', '# Controller']
        for output, _label, _hint in ACTIONS:
            lines += [f'{output} = {name}' for name in self.pad[output]]
        if self.other:
            lines += ['', '# Other settings (kept as they were)'] + self.other
        return '\n'.join(lines) + '\n'

    def save(self, path):
        """Writes input.ini; the first time, the file as it was is kept as input.ini.bak."""
        path = Path(path)
        backup = path.with_name(path.name + '.bak')
        if path.is_file() and not backup.exists():
            backup.write_bytes(path.read_bytes())
        temporary = path.with_name(path.name + '.tmp')
        temporary.write_text(self.text(), encoding='utf-8')
        temporary.replace(path)
