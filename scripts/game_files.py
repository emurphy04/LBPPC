"""Checks a player's LittleBigPlanet 3 folder against a known-good copy: every file of the game and its 1.28 update by
name, size and SHA-256 (scripts/data/lbp3-1.28-us.json.gz, made from a complete, untouched copy). The list holds no
game content, only these fingerprints.

- quick check: every file is there at the right size (a second: a missing or cut-short file, a copy of another
  version);
- full check: every byte read and hashed (about a minute for the 14 GB: a damaged or modified file).

sce_sys is left out: its files depend on the account and the tool the game was extracted with.

Executables (eboot.bin, .prx, .sprx) are kept by dumps either in the PS4's own wrapper or as a plain ELF: the same
code, a different file size. Each one's list entry also has a fingerprint of the code the game loads (its loaded
segments, the same in both forms), so either form passes. The European edition has the same eboot.bin and modules.

usage: python scripts/game_files.py <game folder> [--full]
       python scripts/game_files.py --make <known-good game folder>     (writes the list)
       python scripts/game_files.py --add-code <the same folder>        (adds the executables' code fingerprints)
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

try:  # the launcher's language (launcher/i18n.py); run on its own, English
    from i18n import t
except ImportError:
    def t(text, **fields):
        return text.format(**fields) if fields else text

HERE = Path(sys.executable).resolve().parent / 'scripts' if getattr(sys, 'frozen', False) \
    else Path(__file__).resolve().parent
MANIFEST = HERE / 'data' / 'lbp3-1.28-us.json.gz'
SKIP = ('sce_sys', 'app/sce_sys')  # per account / per extraction tool
# Files that differ by edition. The list is the US copy's; the European one (CUSA00063 1.28) has the same eboot,
# modules and .farc files, but its own French (France) and some Portuguese voice files, sku.txt, FMOD SPU plugins,
# chunk maps and disc maps. These are checked only for being there.
REGION_FILES = {'CUSA00063': ('gamedata/audio/french/', 'gamedata/audio/dialogue/french/',
                              'gamedata/audio/dialogue/dlc/dccomics/french/',
                              'gamedata/audio/lbp3/dialogue/languages/french/',
                              'gamedata/audio/lbp3/dialogue/languages/portuguese/', 'gamedata_orbis/sku.txt',
                              'gamedata_orbis/spu/', 'output/', 'sce_discmap.plt', 'sce_discmap_patch.plt')}


def skipped(rel):
    rel = rel.lower()
    return any(rel == s or rel.startswith(s + '/') for s in SKIP)


def edition(folder):
    """The game folder's TITLE_ID (sce_sys/param.sfo), or ''."""
    try:
        data = (Path(folder) / 'sce_sys' / 'param.sfo').read_bytes()
        magic, _version, keys, values, count = struct.unpack_from('<4sIIII', data, 0)
        if magic != b'\0PSF':
            return ''
        for i in range(count):
            key, _fmt, size, _capacity, offset = struct.unpack_from('<HHIII', data, 20 + i * 16)
            if data[keys + key:data.index(b'\0', keys + key)] == b'TITLE_ID':
                return data[values + offset:values + offset + size].rstrip(b'\0').decode('ascii')
    except (OSError, ValueError, struct.error, UnicodeDecodeError):
        pass
    return ''


EXECUTABLE = ('eboot.bin', '.prx', '.sprx')
LOADED = (1, 2, 0x61000000, 0x61000010)  # PT_LOAD, PT_DYNAMIC, the PS4's dynamic and relro segments


def executable(rel):
    return rel.lower().endswith(EXECUTABLE)


def code_fingerprint(path):
    """SHA-256 of the segments the game loads (in program-header order, with their addresses), whether the file is
    the PS4 wrapper or a plain ELF; None if it is neither."""
    try:
        import prepare
        elf, _header, ph, _segments, _missing = prepare.parse_self(Path(path).read_bytes())
    except (OSError, ValueError, ImportError, struct.error):
        return None
    digest = hashlib.sha256()
    for p in ph:
        if p['type'] in LOADED and p['filesz']:
            digest.update(struct.pack('<IQQ', p['type'], p['vaddr'], p['filesz']))
            digest.update(bytes(elf[p['offset']:p['offset'] + p['filesz']]))
    return digest.hexdigest()


def add_code(folder):
    """Adds the code fingerprints to the list from the known-good folder it was made from (each executable's size
    and SHA-256 must still match its entry)."""
    folder = Path(folder)
    with gzip.open(MANIFEST, 'rt', encoding='utf-8') as f:
        data = json.load(f)
    added = 0
    for entry in data['files']:
        rel, size, sha = entry[:3]
        if not executable(rel):
            continue
        path = folder / rel
        if path.stat().st_size != size or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise SystemExit(f'{rel}: not the file the list was made from')
        code = code_fingerprint(path)
        if not code:
            raise SystemExit(f'{rel}: not a PS4 executable')
        del entry[3:]
        entry.append(code)
        added += 1
    with gzip.open(MANIFEST, 'wt', encoding='utf-8') as out:
        json.dump(data, out, separators=(',', ':'))
    return added


def regional(rel, title):
    return rel.lower().startswith(REGION_FILES.get(title, ()))


def make(folder):
    """The list from a known-good folder: {"files": [[path, size, sha256], ...]}."""
    folder = Path(folder)
    files = []
    for path in sorted(p for p in folder.rglob('*') if p.is_file()):
        rel = path.relative_to(folder).as_posix()
        if skipped(rel):
            continue
        digest = hashlib.sha256()
        with open(path, 'rb') as f:
            for block in iter(lambda: f.read(8 << 20), b''):
                digest.update(block)
        entry = [rel, path.stat().st_size, digest.hexdigest()]
        if executable(rel):
            entry.append(code_fingerprint(path))
        files.append(entry)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(MANIFEST, 'wt', encoding='utf-8') as out:
        json.dump({'game': 'CUSA00473', 'version': '01.28', 'files': files}, out, separators=(',', ':'))
    return len(files)


_LOADED = None


def manifest():
    global _LOADED
    if _LOADED is None:
        with gzip.open(MANIFEST, 'rt', encoding='utf-8') as f:
            _LOADED = json.load(f)['files']
    return _LOADED


UPDATE_FILE = 'patch_001.farc'  # the 1.28 update's own archive (1.2 GB): never in the game without the update


def gb(size):
    return f'{size / 1e9:.1f}'


class Result:
    def __init__(self):
        self.missing, self.wrong_size, self.damaged, self.checked = [], [], [], 0
        self.expected_bytes = self.present_bytes = 0  # the list's total; what of it is there at the right size

    @property
    def ok(self):
        return not (self.missing or self.wrong_size or self.damaged)

    def summary(self, full):
        """In words for the player."""
        if self.ok:
            return (t('All {count} game files checked: they match LittleBigPlanet 3 1.28.', count=self.checked) if full
                    else t('All {count} game files are there.', count=self.checked))
        # What went wrong, in one sentence, before the counts: the update not there at all, or a copy cut short.
        if UPDATE_FILE in self.missing:
            return t("The 1.28 update's files aren't in your game (missing: {count}, among them patch_001.farc, "
                     '1.2 GB). Choose the complete 1.28 update folder: about 1.3 GB, with eboot.bin and '
                     'patch_001.farc inside.', count=len(self.missing))
        if self.missing and self.present_bytes < self.expected_bytes * 0.97:
            return t('Your copy has {have} GB of the {want} GB LittleBigPlanet 3 1.28 has (missing: {count}, for '
                     'example {file}). The extraction stopped partway or skipped files: extract the game again, '
                     'and check there is enough free space for it.', have=gb(self.present_bytes),
                     want=gb(self.expected_bytes), count=len(self.missing), file=self.missing[0])
        parts = []
        if self.missing:
            parts.append(t('{count} missing', count=len(self.missing)))
        if self.wrong_size:
            parts.append(t('{count} the wrong size', count=len(self.wrong_size)))
        if self.damaged:
            parts.append(t('{count} damaged or changed', count=len(self.damaged)))
        example = (self.missing + self.wrong_size + self.damaged)[0]
        return t('Game files: {problems} (for example {file}). Extract the game and the 1.28 update again.',
                 problems=', '.join(parts), file=example)


def check(folder, full=False, progress=None, cancelled=lambda: False, update=None):
    """Result for `folder` (the one holding eboot.bin), with `update`'s files over it when the update is kept in a
    folder of its own (as shadPS4 keeps it). progress(done_bytes, total_bytes) during a full check.
    None when cancelled."""
    folder = Path(folder)
    update = Path(update) if update else None
    result = Result()
    entries = manifest()
    title = edition(update or folder)
    total = sum(entry[1] for entry in entries) if full else 0
    done = 0
    for entry in entries:
        rel, size, sha = entry[:3]
        code = entry[3] if len(entry) > 3 else None
        if cancelled():
            return None
        result.checked += 1
        result.expected_bytes += size
        path = update / rel if update and (update / rel).is_file() else folder / rel
        try:
            actual = path.stat().st_size
        except OSError:
            result.missing.append(rel)
            continue
        if regional(rel, title):  # this edition's own version of the file: only its presence is checked
            result.present_bytes += size
            done += size
            continue
        if code and actual != size:  # an executable in the other form: its code decides
            if code_fingerprint(path) != code:
                result.damaged.append(rel)
            else:
                result.present_bytes += size
            done += size
            continue
        if actual != size:
            result.wrong_size.append(rel)
            done += size
            continue
        result.present_bytes += size
        if full:
            digest = hashlib.sha256()
            try:
                with open(path, 'rb') as f:
                    for block in iter(lambda: f.read(8 << 20), b''):
                        digest.update(block)
                        done += len(block)
                        if progress:
                            progress(done, total)
                        if cancelled():
                            return None
            except OSError:
                result.damaged.append(rel)
                continue
            if digest.hexdigest() != sha and not (code and code_fingerprint(path) == code):
                result.damaged.append(rel)
    return result


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--add-code' in sys.argv and args:
        print(f'{add_code(args[0])} executables fingerprinted in {MANIFEST}')
    elif '--make' in sys.argv and args:
        print(f'{make(args[0])} files listed in {MANIFEST}')
    elif args:
        full = '--full' in sys.argv
        r = check(args[0], full, lambda d, total: print(f'\r{d * 100 // max(total, 1):3d}%', end=''))
        print('\n' + r.summary(full))
        for name in (r.missing + r.wrong_size + r.damaged)[:20]:
            print('  ', name)
    else:
        sys.exit(__doc__)
