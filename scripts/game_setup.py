"""Game setup: the player's LittleBigPlanet 3 and its 1.28 update, checked and chosen, each left where it is.

The launcher's Set up... wizard runs this. Players' copies come in a few shapes, all handled here:
- the game folder itself (eboot.bin, sce_module, sce_sys), or a folder above it (an extraction's Image0 inside, or one
  more folder level);
- system files (param.sfo, npbind.dat, playgo-chunk.dat) in a separate Sc0 folder, inside the game folder or beside
  its Image0;
- the 1.28 update in a folder of its own (shadPS4's layout, CUSA00473-UPDATE and the like), with or without its own Sc0.

Nothing is written to the game's folder or the update's: as in shadPS4, the update's files are read over the game's
when it runs, and an Sc0 is read as the game's sce_sys (run.py, scripts/game_view.py, the runtime's --update and
--system). The same folders keep working in shadPS4. Only DLC is copied, into LBPPC's own user folder, where the game
looks for it.

Only extracted folders: a .pkg must be extracted first (LBPPC doesn't do that).

usage: python scripts/game_setup.py <game folder> [<update folder>] [<DLC folder> <LBPPC user folder>] [--check]
"""
import os
import re
from pathlib import Path
import shutil
import stat
import struct
import sys

import game_view  # where the game's files are read from (the update over the game, the system files in an Sc0)

try:  # the launcher's language (launcher/i18n.py); run on its own, English
    from i18n import N_, t
except ImportError:
    def N_(text):
        return text

    def t(text, **fields):
        return text.format(**fields) if fields else text

TITLE_ID, VERSION = 'CUSA00473', '01.28'  # the game and version LBPPC's patches are made for
# Editions whose 1.28 runs as it is: the European CUSA00063 1.28 has the very same eboot.bin, sce_module and .farc
# files as the US one (byte for byte); only some French and Portuguese voice files, sku.txt, the FMOD SPU plugins
# and the chunk maps differ (game_files.REGION_FILES). The eboot finds its edition from param.sfo at run time.
SUPPORTED_IDS = {TITLE_ID: N_('US'), 'CUSA00063': N_('European (PAL)')}
# Its DLC is sold under the add-on service CUSA00762 (param.sfo SERVICE_ID_ADDCONT_ADD_1 = EP9000-CUSA00762_00).
DLC_TITLE_IDS = {TITLE_ID, 'CUSA00063', 'CUSA00762'}
# LittleBigPlanet 3's other editions, by the region the game's own table (eboot 0x12c8718) gives each: A the Americas,
# E Europe, J Japan, K and C Asia. CUSA00810 sold with Latin American Spanish and Brazilian Portuguese.
OTHER_LBP3 = {'CUSA00762': N_('European (PAL)'), 'CUSA01072': N_('European (PAL)'),
              'CUSA00810': N_('Latin American'), 'CUSA01077': N_('North American'), 'CUSA00601': N_('Japanese'),
              'CUSA00693': N_('Asian'), 'CUSA00738': N_('Asian')}
# The update folder beside the game when the player didn't choose one (shadPS4's and PS4 dump tools' names).
UPDATE_SUFFIXES = ('-UPDATE', '-update', '-Update', '-patch', '-PATCH', '-Patch', '_patch', '_update', '_UPDATE')
SYSTEM_FILES = ('npbind.dat', 'playgo-chunk.dat')  # needed by online co-op (NP sign-in, the game's chunks)
FULL_GAME = 'base_001.farc'  # in the game, never in an update (which brings patch_001.farc)
PART = '.lbppc-part'  # a DLC file being copied
SPACE_MARGIN = 256 << 20


class SetupError(Exception):
    """A problem the player can fix, in words for them."""


def read_sfo(path):
    """param.sfo's values by key; {} when it can't be read."""
    try:
        data = Path(path).read_bytes()
        magic, _version, keys, values, count = struct.unpack_from('<4sIIII', data, 0)
        if magic != b'\0PSF':
            return {}
        out = {}
        for i in range(count):
            key, fmt, size, _capacity, offset = struct.unpack_from('<HHIII', data, 20 + i * 16)
            name = data[keys + key:data.index(b'\0', keys + key)].decode('ascii', 'replace')
            raw = data[values + offset:values + offset + size]
            out[name] = struct.unpack('<I', raw)[0] if fmt == 0x404 and size == 4 else \
                raw.rstrip(b'\0').decode('utf-8', 'replace')
        return out
    except (OSError, ValueError, struct.error):
        return {}


def find_root(folder):
    """The folder holding eboot.bin (or, for an update without one, sce_sys/param.sfo): `folder` itself, its
    Image0, or one or two levels down when there is exactly one such folder (a zip's or an extraction tool's extra
    level). None when there isn't one."""
    folder = Path(folder)
    if is_root(folder):
        return folder
    found = candidates(folder)
    return found[0] if len(found) == 1 else None


def candidates(folder):
    """Game or update folders one or two levels inside `folder`."""
    found = []
    try:
        for child in sorted(Path(folder).iterdir()):
            if child.is_dir() and child.name.lower() != 'sc0':
                if is_root(child):
                    found.append(child)
                else:
                    found += [grand for grand in sorted(child.iterdir()) if grand.is_dir() and
                              grand.name.lower() != 'sc0' and is_root(grand)]
    except OSError:
        pass
    return found


def is_root(folder):
    return (folder / 'eboot.bin').is_file() or (folder / 'sce_sys' / 'param.sfo').is_file()


def system_folder(root):
    """The Sc0 of a game or update folder: inside it, or beside it when the folder is an extraction's Image0."""
    root = Path(root)
    for sc0 in (root / 'Sc0', root / 'sc0', root.parent / 'Sc0', root.parent / 'sc0'):
        if sc0.is_dir() and sc0.resolve() != root.resolve():
            return sc0
    return None


def package_files(folder):
    """.pkg files in or right inside `folder` (a player who chose the download, not its extraction)."""
    folder = Path(folder)
    try:
        return [p for p in folder.iterdir() if p.suffix.lower() == '.pkg' and p.is_file()]
    except OSError:
        return []


class Folder:
    """One of the player's folders, read: where its files are, which game and version it is."""

    def __init__(self, chosen):
        self.chosen = Path(chosen) if chosen else None
        self.root = find_root(self.chosen) if self.chosen and self.chosen.is_dir() else None
        self.many = len(candidates(self.chosen)) if self.chosen and not self.root and self.chosen.is_dir() else 0
        self.sc0 = system_folder(self.root) if self.root else None
        sfo = {}
        for candidate in ((self.root / 'sce_sys' / 'param.sfo') if self.root else None,
                          (self.sc0 / 'param.sfo') if self.sc0 else None):
            if candidate and not sfo:
                sfo = read_sfo(candidate)
        self.title_id, self.version = sfo.get('TITLE_ID', ''), sfo.get('APP_VER', '')
        # The full game has base_001.farc; an update doesn't. (param.sfo's CATEGORY can't tell: a game the update
        # was put into has the update's, gp.)
        self.full = bool(self.root) and (self.root / FULL_GAME).is_file()
        self.is_update = bool(self.root) and not self.full

    def problem(self, what):
        """Why this folder can't be used as `what` ('game' or 'update'), or None."""
        if not self.chosen:
            return t('Choose your game folder.') if what == 'game' else t('Choose your update folder.')
        if not self.chosen.is_dir():
            return t('That folder doesn\'t exist.')
        if not self.root or (what == 'game' and not (self.root / 'eboot.bin').is_file()):
            if package_files(self.chosen):
                return t('That\'s a .pkg file. It must be extracted first: choose the folder it was extracted to.')
            if self.many:
                return (t('This folder holds {count} game folders. Choose the game folder itself.', count=self.many)
                        if what == 'game' else
                        t('This folder holds {count} game folders. Choose the update folder itself.', count=self.many))
            return t('No eboot.bin in this folder. Choose the folder it was extracted to (with eboot.bin inside).')
        if not self.title_id:
            return t('No param.sfo found (sce_sys or Sc0). The extraction looks incomplete.')
        if self.title_id not in SUPPORTED_IDS:
            if self.title_id in OTHER_LBP3:
                return t('This is the {region} version of LittleBigPlanet 3 ({id}). LBPPC needs the US version of '
                         'the game ({us}): other versions won\'t run.', region=t(OTHER_LBP3[self.title_id]),
                         id=self.title_id, us=TITLE_ID)
            return t('This isn\'t LittleBigPlanet 3 ({id}). LBPPC needs {us}.', id=self.title_id, us=TITLE_ID)
        return None

    def describe(self):
        version = self.version.lstrip('0')
        return (t('LittleBigPlanet 3 update, version {version}', version=version) if self.is_update else
                t('LittleBigPlanet 3 game, version {version}', version=version))


def find_update(game_root):
    """The update folder beside the game (`CUSA00473-UPDATE` and the like), or None."""
    if not game_root:
        return None
    game_root = Path(game_root)
    bases = [game_root] + ([game_root.parent] if game_root.name.lower() == 'image0' else [])
    for base in bases:
        for suffix in UPDATE_SUFFIXES:
            candidate = base.parent / (base.name + suffix)
            if candidate.is_dir() and find_root(candidate):
                return candidate
    return None


def inside(a, b):
    """Is `a` the same as or inside `b`?"""
    a, b = Path(a).resolve(), Path(b).resolve()
    return a == b or b in a.parents


class Plan:
    """What setup will do: the game and its update checked (the update stays in its own folder, read over the game
    when it runs), and (source, target, 'dlc') steps: the player's DLC copied into LBPPC's user/addcont. Nothing is
    written to the game's folder or the update's."""

    def __init__(self, game_folder, update_folder=None, dlc_folder=None, user_dir=None):
        game, update = Folder(game_folder), Folder(update_folder) if update_folder else None
        # The two chosen the wrong way round: swapped (what each one is, is in its param.sfo).
        if update and game.is_update and update.full:
            game, update = update, game
        self.game, self.update = game, update
        problem = game.problem('game')
        if problem:
            raise SetupError(problem)
        if game.is_update:
            raise SetupError(t('That\'s the update, not the game (it has no base_001.farc). Choose the base game '
                               'folder first, then the update.'))
        self.steps = []
        # A game already at 1.28 (its update put into it some other way) needs none: an unusable one is left out.
        if game.version == VERSION and update and (update.problem('update') or not update.is_update or
                                                   update.version != VERSION):
            update = None
        if game.version != VERSION or update:
            if not update:
                raise SetupError(t('Your game is version {version}. Choose the 1.28 update folder too.',
                                   version=game.version.lstrip('0')))
            problem = update.problem('update')
            if problem:
                raise SetupError(problem)
            if inside(update.root, game.root) or inside(game.root, update.root):
                raise SetupError(t('Choose two separate folders: the game, and the update beside it (not inside it).'))
            if not update.is_update:
                raise SetupError(t('That\'s a game, not an update. Choose the folder the 1.28 update was extracted to.'))
            if update.version != VERSION:
                raise SetupError(t('That update is version {version}. LBPPC needs the 1.28 update.',
                                   version=update.version.lstrip('0')))
            if update.title_id != game.title_id:
                raise SetupError(t('That update is for {update}, but the game is {game}. Choose the update of the '
                                   'same edition.', update=update.title_id, game=game.title_id))
        self.update = update
        # The player's DLC, copied (never moved) into <user>/addcont/CUSA00473/<label>, where the game finds it.
        self.dlc = []
        if dlc_folder:
            if not user_dir:
                raise SetupError(t('No LBPPC folder to put the DLC in.'))
            self.dlc = find_dlc(dlc_folder)
            dlc_steps = []
            for dlc in self.dlc:
                base = Path(user_dir) / 'addcont' / TITLE_ID / dlc.label
                for source, rel in dlc.files():
                    target = base / rel
                    if target.is_file() and target.stat().st_size == source.stat().st_size:
                        continue  # already in place (set up before)
                    dlc_steps.append((source, target, 'dlc'))
            self.steps = dlc_steps

    @property
    def root(self):
        return self.game.root

    def bytes_needed(self):
        """{folder on a drive: free space the DLC copies take there}, less what they replace."""
        need = {}
        for source, target, _kind in self.steps:
            where = existing_parent(target)
            device = os.stat(where).st_dev
            key = next((k for k in need if os.stat(k).st_dev == device), where)
            need[key] = need.get(key, 0) + source.stat().st_size - (target.stat().st_size if target.is_file() else 0)
        return {k: max(0, v) for k, v in need.items()}

    def check_space(self):
        for where, need in self.bytes_needed().items():
            free = shutil.disk_usage(where).free
            if need and need + SPACE_MARGIN > free:
                raise SetupError(t('Not enough space on LBPPC\'s drive (for the DLC): {needed} needed, {free} free. '
                                   'Free some space.', needed=gb(need + SPACE_MARGIN), free=gb(free)))

    def total_bytes(self):
        return sum(source.stat().st_size for source, _target, _kind in self.steps)

    def summary(self):
        dlc_bytes = sum(step[0].stat().st_size for step in self.steps)
        parts = []
        if self.update:
            parts.append(t('The update is read from its own folder: your game and update folders are left as '
                           'they are'))
        if self.dlc:
            names = ', '.join(dlc.label for dlc in self.dlc[:3]) + (', ...' if len(self.dlc) > 3 else '')
            parts.append(t('{count} DLC ({names}) copied ({size})', count=len(self.dlc), names=names, size=gb(dlc_bytes))
                         if dlc_bytes else t('{count} DLC ({names}) already set up', count=len(self.dlc), names=names))
        return ', '.join(parts) or t('Nothing to do: your game folder is ready.')

    @property
    def has_work(self):
        return bool(self.steps)


def existing_parent(path):
    path = Path(path)
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


class Dlc:
    """One extracted DLC: its files (`root`, and its system files in sce_sys or a separate Sc0) and its param.sfo's
    CONTENT_ID, such as UP9000-CUSA00473_00-LBPDLCSONYCO0001, whose last part is the entitlement label the game
    asks for."""

    def __init__(self, root, sc0, sfo):
        self.root, self.sc0 = Path(root), Path(sc0) if sc0 else None
        self.content_id = sfo.get('CONTENT_ID', '')
        self.label = self.content_id.rsplit('-', 1)[-1]
        self.title_id = self.content_id[7:16] if len(self.content_id) >= 16 else ''
        self.name = sfo.get('TITLE', self.label)

    def files(self):
        """(source, path inside the installed DLC folder) for every file."""
        out = {}
        if self.sc0:
            for path in files(self.sc0):
                out[f'sce_sys/{path.relative_to(self.sc0).as_posix()}'] = path
        for path in files(self.root):
            rel = path.relative_to(self.root)
            if rel.parts and rel.parts[0].lower() == 'sc0':
                continue  # its Sc0 inside it: taken above, as sce_sys
            out[rel.as_posix()] = path
        return [(source, rel) for rel, source in sorted(out.items())]


def dlc_at(folder):
    """(Dlc or None, problem or None) for one folder: an extracted DLC, with its param.sfo in sce_sys, in an Sc0
    inside it, or (an Image0 folder) in the Sc0 beside it."""
    folder = Path(folder)
    root, sc0 = folder, None
    if (folder / 'Image0').is_dir():
        root = folder / 'Image0'
    for candidate in (root / 'Sc0', root / 'sc0', folder / 'Sc0', folder / 'sc0', root.parent / 'Sc0'):
        if (candidate / 'param.sfo').is_file() and candidate.resolve() != root.resolve():
            sc0 = candidate
            break
    sfo_path = root / 'sce_sys' / 'param.sfo'
    if not sfo_path.is_file():
        sfo_path = sc0 / 'param.sfo' if sc0 else None
    if not sfo_path:
        return None, None
    sfo = read_sfo(sfo_path)
    category = str(sfo.get('CATEGORY', ''))
    if not category.startswith('ac'):
        return None, 'game' if category in ('gd', 'gp') else None
    dlc = Dlc(root, sc0, sfo)
    if not re.fullmatch(r'[A-Z0-9]{16}', dlc.label):
        return None, t('{folder}: its param.sfo has no DLC label (CONTENT_ID {id}).', folder=folder.name,
                       id=dlc.content_id or '-')
    if dlc.title_id not in DLC_TITLE_IDS:
        region = OTHER_LBP3.get(dlc.title_id)
        return None, (t('{folder} is DLC for the {region} version of LittleBigPlanet 3 ({id}). LBPPC needs the US '
                        'version ({us}), and its DLC.', folder=folder.name, region=t(region), id=dlc.title_id,
                        us=TITLE_ID) if region else
                      t('{folder} is DLC for another game ({id}).', folder=folder.name, id=dlc.title_id or '?'))
    return dlc, None


def find_dlc(folder):
    """The DLC in `folder`: the folder itself, or every DLC up to two levels inside it (a folder of DLC folders).
    Raises SetupError when there's none, or one that can't be used."""
    folder = Path(folder)
    if not folder.is_dir():
        raise SetupError(t('That DLC folder doesn\'t exist.'))
    dlc, own = dlc_at(folder)
    if dlc:
        return [dlc]
    if own and own != 'game':
        raise SetupError(own)
    found, problems, games = {}, [], 0
    try:
        children = [c for c in sorted(folder.iterdir()) if c.is_dir() and c.name.lower() not in ('sc0', 'sce_sys')]
    except OSError as error:
        raise SetupError(t('Can\'t read that DLC folder: {error}', error=error)) from error
    for child in children:
        for candidate in [child] + [g for g in sorted(child.iterdir()) if g.is_dir() and
                                    g.name.lower() not in ('sc0', 'sce_sys', 'image0')]:
            dlc, problem = dlc_at(candidate)
            if dlc:
                found.setdefault(dlc.label, dlc)
                break
            if problem == 'game':
                games += 1
            elif problem:
                problems.append(problem)
    if problems:
        raise SetupError(problems[0])
    if not found:
        if own == 'game' or games:
            raise SetupError(t('That\'s the game or its update, not DLC. Choose the folder your DLC was extracted to.'))
        packages = len(package_files(folder)) + sum(len(package_files(child)) for child in children)
        if packages:
            raise SetupError(t('This folder holds {count} .pkg files. DLC must be extracted first: choose the folder '
                               'it was extracted to.', count=packages))
        raise SetupError(t('No DLC in this folder. Choose an extracted DLC (with sce_sys/param.sfo inside), or a '
                           'folder holding several.'))
    return list(found.values())


def installed_dlc(user_dir):
    """The DLC set up in LBPPC: [label] of <user>/addcont/CUSA00473/<label> folders that are real DLC."""
    root = Path(user_dir) / 'addcont' / TITLE_ID
    labels = []
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            sfo = read_sfo(folder / 'sce_sys' / 'param.sfo') if folder.is_dir() else {}
            if str(sfo.get('CATEGORY', '')).startswith('ac') and str(sfo.get('CONTENT_ID', '')).endswith(
                    '-' + folder.name):
                labels.append(folder.name)
    return labels


def files(folder):
    """Every file under `folder` (no leftovers of a stopped setup)."""
    return sorted(p for p in Path(folder).rglob('*') if p.is_file() and not p.name.endswith(PART))


def gb(count):
    return f'{count / (1 << 30):.1f} GB' if count >= 1 << 30 else f'{max(1, count >> 20)} MB'


def writable(path):
    """Extractions often leave files read-only; Windows won't replace or delete those."""
    try:
        mode = os.stat(path).st_mode
        if not mode & stat.S_IWRITE:
            os.chmod(path, mode | stat.S_IWRITE)
    except OSError:
        pass


def place(source, target, progress=None):
    """`source` copied to `target` (in LBPPC's user folder), which it replaces, landing whole (no half-written file
    if stopped). The source is only read."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        writable(target)
    part = target.with_name(target.name + PART)
    with open(source, 'rb') as src, open(part, 'wb') as dst:
        while True:
            block = src.read(8 << 20)
            if not block:
                break
            dst.write(block)
            if progress:
                progress(len(block))
    shutil.copystat(source, part)
    writable(part)
    os.replace(part, target)


def run(plan, progress=None, cancelled=lambda: False):
    """Carries out `plan` (its DLC copies). progress(done_bytes, total_bytes, name) as it goes. Returns False when
    cancelled (running setup again finishes it)."""
    plan.check_space()
    total, done = plan.total_bytes() or 1, 0
    for source, target, _kind in plan.steps:
        if cancelled():
            return False
        name = f'DLC {target.parent.name}/{target.name}'
        size = source.stat().st_size
        copied = [0]

        def advance(count):
            copied[0] += count
            if progress:
                progress(done + copied[0], total, name)

        if progress:
            progress(done, total, name)
        place(source, target, advance)
        done += size
    if progress:
        progress(total, total, '')
    verify(plan.update.root if plan.update else plan.root)
    return True


def verify(root):
    """The folder the game runs as (the update's, or a game already at 1.28) reads as LittleBigPlanet 3 1.28."""
    folder = Folder(root)
    if folder.title_id not in SUPPORTED_IDS or folder.version != VERSION or not (Path(root) / 'eboot.bin').is_file():
        raise SetupError(t('The game reads as version {version}, not 1.28. Choose the 1.28 update folder; if it '
                           'keeps happening, ask on the Discord.', version=folder.version or '?'))


def separate_update(game, update=None):
    """The 1.28 update kept in its own folder for this game: `update` (chosen in the launcher), else the folder beside
    the game (CUSA00063-patch and the like). Its root, or None when there's no usable one."""
    candidate = Folder(update) if update else Folder(find_update(game.root)) if game.root and find_update(game.root) else None
    if (candidate and candidate.root and candidate.is_update and candidate.version == VERSION
            and candidate.title_id == game.title_id):
        return candidate.root
    return None


def status(folder, update=None):
    """(state, text) of a game folder for the launcher: 'ready', 'setup' (the wizard fixes it), or 'missing'.
    `update`: the update's own folder, read over the game when it runs (else the one beside the game is looked for)."""
    game = Folder(folder)
    problem = game.problem('game')
    if problem:
        return ('setup' if game.root else 'missing'), problem
    if game.is_update:
        return 'setup', t('That\'s the update only, not the game. Click Set up... to choose both.')
    extra = separate_update(game, update) if game.version != VERSION else None
    if game.version != VERSION and not extra:
        return 'setup', t('Version {version}: LBPPC needs the 1.28 update. Click Set up... to choose its folder.',
                          version=game.version.lstrip('0'))
    # The system files wherever the game reads them (sce_sys or an Sc0, the update's or the game's: game_view.py).
    folders = game_view.system_folders(game.root, extra)
    missing = [name for name in SYSTEM_FILES if not any((folder / name).is_file() for folder in folders)]
    if missing:
        return 'ready', t('Ready. Missing {files}: online co-op won\'t work.', files=', '.join(missing))
    if extra:
        return 'ready', t('LittleBigPlanet 3 version 1.28 (update in its own folder), ready to play')
    return 'ready', t('LittleBigPlanet 3 version 1.28, ready to play')


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        sys.exit(__doc__)
    try:
        plan = Plan(args[0], args[1] if len(args) > 1 else find_update(find_root(args[0])),
                    args[2] if len(args) > 3 else None, args[3] if len(args) > 3 else None)
        print(f'Game: {plan.root}' + (f'\nUpdate: {plan.update.root}' if plan.update else ''))
        print(plan.summary())
        if '--check' not in sys.argv:
            run(plan, lambda done, total, name: print(f'\r{done * 100 // total:3d}% {name[-60:]:<60}', end=''))
            print('\nDone.')
    except SetupError as error:
        sys.exit(str(error))
