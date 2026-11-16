"""Save files: several sets of LittleBigPlanet 3 progress in one LBPPC, chosen in the launcher before Play.

- "Main" is <user>/savedata, the one save file of 0.1.3 and before, so existing progress is the Main save file.
- Each other save file is <user>/saves/<name>/savedata, with the same layout (<user id>/<title id>/<save>).
- The game is told the chosen one's folder in LBPPC_SAVE_DIR (runtime_savedata.c) and its name in LBPPC_SAVE_NAME.

Only progress is per save file: settings, controls, DLC, the online account and caches are shared.
"""
import re
import shutil
from pathlib import Path

try:  # the launcher's language (launcher/i18n.py); run on its own, English
    from i18n import t
except ImportError:
    def t(text, **fields):
        return text.format(**fields) if fields else text

MAIN = 'Main'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9 _-]{0,23}')  # what a save file may be called (also its folder's name)
RESERVED = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(10)), *(f'lpt{i}' for i in range(10))}


class SaveFileError(Exception):
    """A problem the player can fix, in words for them."""


def folder(user_dir, name):
    """The savedata folder of save file `name`."""
    if not name or name == MAIN:
        return Path(user_dir) / 'savedata'
    return Path(user_dir) / 'saves' / name / 'savedata'


def names(user_dir):
    """Main first, then the others in alphabetical order."""
    others = []
    saves = Path(user_dir) / 'saves'
    try:
        others = sorted((p.name for p in saves.iterdir() if p.is_dir() and NAME.fullmatch(p.name)),
                        key=str.lower)
    except OSError:
        pass
    return [MAIN] + [n for n in others if n.lower() != MAIN.lower()]


def check_name(user_dir, name, existing=None):
    """The cleaned-up name, or SaveFileError. `existing`: the save file being renamed (may keep its own name)."""
    name = (name or '').strip()
    if not NAME.fullmatch(name):
        raise SaveFileError(t('A save file name has 1 to 24 letters, digits, spaces, - or _, starting with a letter '
                              'or digit.'))
    if name.lower() in RESERVED:
        raise SaveFileError(t('Windows doesn\'t allow that name. Choose another.'))
    taken = {n.lower() for n in names(user_dir)} - ({existing.lower()} if existing else set())
    if name.lower() in taken:
        raise SaveFileError(t('There is already a save file called {name}.', name=name))
    return name


def create(user_dir, name):
    """A new, empty save file: the game starts it from the beginning."""
    name = check_name(user_dir, name)
    folder(user_dir, name).mkdir(parents=True)
    return name


def rename(user_dir, old, new):
    if old == MAIN:
        raise SaveFileError(t('The Main save file can\'t be renamed.'))
    new = check_name(user_dir, new, existing=old)
    if new == old:
        return new
    (Path(user_dir) / 'saves' / old).rename(Path(user_dir) / 'saves' / new)
    return new


def delete(user_dir, name):
    """Moves the save file to <user>/save_backups (never deleted outright: a mistake can be undone by hand)."""
    if name == MAIN:
        raise SaveFileError(t('The Main save file can\'t be deleted.'))
    source = Path(user_dir) / 'saves' / name
    if not source.is_dir():
        raise SaveFileError(t('There is no save file called {name}.', name=name))
    from datetime import datetime
    backup = Path(user_dir) / 'save_backups' / f'deleted-{name}-{datetime.now():%Y%m%d-%H%M%S}'
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(backup))
    return backup


def environment(user_dir, name):
    """What the game is started with for save file `name` (nothing for Main: its folder is the default)."""
    if not name or name == MAIN or name not in names(user_dir):
        return {}
    return {'LBPPC_SAVE_DIR': str(folder(user_dir, name)), 'LBPPC_SAVE_NAME': name}
