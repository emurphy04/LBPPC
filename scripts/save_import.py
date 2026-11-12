"""Import save: a LittleBigPlanet 3 save from a PS4 into LBPPC. Only decrypted saves: the files the game wrote, as
in its save folder (00000001/bigfart..., littlefart..., playfart_...), which tools that back up PS4 saves can
export. An encrypted backup (sdimg_ files) can't be used.

LBP3 keeps three save folders, each found by what it holds:
    LBPxSAVE            00000001/bigfart*         your profile and story
    LBPxSAVExLITTLE     00000001/littlefart*      settings
    LBPxSAVEx<n>x<m>    00000001/playfart_<n>_<m>_*   a play session
A folder chosen can be one save, or a folder holding several (an export of all of them). LBPPC's save of that kind
is moved to user/save_backups/<date and time>/ first, so an import can be undone.

usage: python scripts/save_import.py <save folder> <LBPPC user folder> [--check]
"""
from datetime import datetime
from pathlib import Path
import re
import shutil
import struct
import sys

try:  # the launcher's language (launcher/i18n.py); run on its own, English
    from i18n import t
except ImportError:
    def t(text, **fields):
        return text.format(**fields) if fields else text

TITLE_ID = 'CUSA00473'
LBP3 = {'CUSA00473', 'CUSA00063', 'CUSA00693', 'CUSA00810', 'CUSA00762', 'CUSA01077'}
KINDS = ((re.compile(r'^bigfart'), 'LBPxSAVE'), (re.compile(r'^littlefart'), 'LBPxSAVExLITTLE'))
PLAY = re.compile(r'^playfart_(\d+)_(\d+)_')


class ImportError_(Exception):
    """A problem the player can fix, in words for them."""


def read_sfo(path):
    try:
        data = Path(path).read_bytes()
        magic, _v, keys, values, count = struct.unpack_from('<4sIIII', data, 0)
        if magic != b'\0PSF':
            return {}
        out = {}
        for i in range(count):
            key, fmt, size, _cap, off = struct.unpack_from('<HHIII', data, 20 + i * 16)
            name = data[keys + key:data.index(b'\0', keys + key)].decode('ascii', 'replace')
            raw = data[values + off:values + off + size]
            out[name] = struct.unpack('<I', raw)[0] if fmt == 0x404 and size == 4 else \
                raw.rstrip(b'\0').decode('utf-8', 'replace')
        return out
    except (OSError, ValueError, struct.error):
        return {}


class Save:
    """One save folder: `content` holds its files (00000001/...), `icon` its picture when there is one."""

    def __init__(self, content, name, icon, title):
        self.content, self.name, self.icon, self.title = Path(content), name, icon, title

    def files(self):
        return sorted(p for p in self.content.rglob('*') if p.is_file()
                      and 'sce_sys' not in [part.lower() for part in p.relative_to(self.content).parts])


def save_at(folder):
    """(Save or None, problem or None) for one folder."""
    folder = Path(folder)
    content = folder / 'savedata0' if (folder / 'savedata0').is_dir() else folder
    try:
        names = [p.name for p in folder.iterdir()]
    except OSError:
        return None, None
    if any(n.lower().startswith('sdimg_') for n in names):
        return None, t('This save is still encrypted (sdimg_ files). LBPPC needs a decrypted save: the files the game '
                       'wrote, such as 00000001/bigfart.')
    sfo = read_sfo(folder / 'sce_sys' / 'param.sfo') or read_sfo(content / 'sce_sys' / 'param.sfo')
    title = str(sfo.get('TITLE_ID', ''))
    if title and title not in LBP3:
        return None, t('This is a save for another game ({id}), not LittleBigPlanet 3.', id=title)
    data = content / '00000001'
    files = [p.name for p in data.iterdir()] if data.is_dir() else []
    name = str(sfo.get('SAVEDATA_DIRECTORY', '')) or None
    if not name:
        for f in files:
            for pattern, kind in KINDS:
                if pattern.match(f):
                    name = kind
            play = PLAY.match(f)
            if play:
                name = f'LBPxSAVEx{play.group(1)}x{play.group(2)}'
    if not name or not files:
        return None, None
    if not re.fullmatch(r'LBPxSAVE[A-Za-z0-9x]*', name):
        return None, t('{folder}: this doesn\'t look like a LittleBigPlanet 3 save ({name}).', folder=folder.name,
                       name=name)
    icon = next((p for p in (folder / 'sce_sys' / 'icon0.png', content / 'sce_sys' / 'icon0.png',
                             content / 'ICON0.PNG') if p.is_file()), None)
    return Save(content, name, icon, title), None


def find_saves(folder):
    """The saves in `folder`: itself, or each one up to two levels inside. Raises ImportError_ when there are none."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ImportError_(t('That folder doesn\'t exist.'))
    save, problem = save_at(folder)
    if save:
        return [save]
    if problem:
        raise ImportError_(problem)
    found, problems = {}, []
    for child in sorted(p for p in folder.iterdir() if p.is_dir()):
        for candidate in [child] + sorted(p for p in child.iterdir() if p.is_dir()):
            save, problem = save_at(candidate)
            if save:
                found.setdefault(save.name, save)
                break
            if problem:
                problems.append(problem)
    if not found:
        raise ImportError_(problems[0] if problems else
                           t('No LittleBigPlanet 3 save in this folder. Choose a decrypted save (with a 00000001 folder '
                             'holding bigfart, littlefart or playfart files), or a folder holding several.'))
    return list(found.values())


def save_root(user_dir):
    return Path(user_dir) / 'savedata' / '1' / TITLE_ID


def import_saves(saves, user_dir):
    """Copies `saves` in, LBPPC's own saves of the same kind moved to a backup first. Returns the backup folder."""
    root = save_root(user_dir)
    root.mkdir(parents=True, exist_ok=True)
    backup = Path(user_dir) / 'save_backups' / datetime.now().strftime('%Y%m%d-%H%M%S')
    for save in saves:
        for existing in (root / save.name, root / f'{save.name}.sce_sys'):
            if existing.exists():
                backup.mkdir(parents=True, exist_ok=True)
                shutil.move(str(existing), str(backup / existing.name))
        target = root / save.name
        for source in save.files():
            dest = target / source.relative_to(save.content)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        meta = root / f'{save.name}.sce_sys'
        meta.mkdir(exist_ok=True)
        if save.icon:
            shutil.copy2(save.icon, meta / 'icon0.png')
    return backup if backup.exists() else None


def describe(saves):
    kinds = {'LBPxSAVE': t('profile and story'), 'LBPxSAVExLITTLE': t('settings')}
    return ', '.join(kinds.get(s.name, t('play session')) for s in saves)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    try:
        found = find_saves(sys.argv[1])
        print('Found:', ', '.join(s.name for s in found), '-', describe(found))
        if '--check' not in sys.argv:
            print('Backup:', import_saves(found, sys.argv[2]))
    except ImportError_ as error:
        sys.exit(str(error))
