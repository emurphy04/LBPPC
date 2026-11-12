"""Checks a player's LittleBigPlanet 3 folder against a known-good copy: every file of the game and its 1.28 update by
name, size and SHA-256 (scripts/data/lbp3-1.28-us.json.gz, made from a complete, untouched copy). The list holds no
game content, only these fingerprints.

- quick check: every file is there at the right size (a second: a missing or cut-short file, a copy of another
  version);
- full check: every byte read and hashed (about a minute for the 14 GB: a damaged or modified file).

sce_sys is left out: its files depend on the account and the tool the game was extracted with.

usage: python scripts/game_files.py <game folder> [--full]
       python scripts/game_files.py --make <known-good game folder>     (writes the list)
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
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


def skipped(rel):
    rel = rel.lower()
    return any(rel == s or rel.startswith(s + '/') for s in SKIP)


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
        files.append([rel, path.stat().st_size, digest.hexdigest()])
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


class Result:
    def __init__(self):
        self.missing, self.wrong_size, self.damaged, self.checked = [], [], [], 0

    @property
    def ok(self):
        return not (self.missing or self.wrong_size or self.damaged)

    def summary(self, full):
        """In words for the player."""
        if self.ok:
            return (t('All {count} game files checked: they match LittleBigPlanet 3 1.28.', count=self.checked) if full
                    else t('All {count} game files are there.', count=self.checked))
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


def check(folder, full=False, progress=None, cancelled=lambda: False):
    """Result for `folder` (the one holding eboot.bin). progress(done_bytes, total_bytes) during a full check.
    None when cancelled."""
    folder = Path(folder)
    result = Result()
    entries = manifest()
    total = sum(size for _rel, size, _sha in entries) if full else 0
    done = 0
    for rel, size, sha in entries:
        if cancelled():
            return None
        result.checked += 1
        path = folder / rel
        try:
            actual = path.stat().st_size
        except OSError:
            result.missing.append(rel)
            continue
        if actual != size:
            result.wrong_size.append(rel)
            done += size
            continue
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
            if digest.hexdigest() != sha:
                result.damaged.append(rel)
    return result


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--make' in sys.argv and args:
        print(f'{make(args[0])} files listed in {MANIFEST}')
    elif args:
        full = '--full' in sys.argv
        r = check(args[0], full, lambda d, total: print(f'\r{d * 100 // max(total, 1):3d}%', end=''))
        print('\n' + r.summary(full))
        for name in (r.missing + r.wrong_size + r.damaged)[:20]:
            print('  ', name)
    else:
        sys.exit(__doc__)
