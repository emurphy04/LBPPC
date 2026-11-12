"""LBPPC's own updater: the launcher finds a newer release on GitHub, the player clicks Update, and it is installed in
place with the player's files kept. Standard library only (it runs inside the frozen LBPPC.exe).

1. check(): the newest release of github.com/emurphy04/LBPPC (drafts and pre-releases left out) and its Windows zip,
   when it is newer than this version.
2. download(): the zip into out/update/, checked against its SHA-256 (GitHub's own digest of the uploaded file, or a
   <zip>.sha256 uploaded beside it): a zip without one, or one that doesn't match, is never installed.
3. stage(): unpacked into out/update/staging/ (its top LBPPC/ folder removed), every path checked to stay inside.
4. The launcher starts the NEW LBPPC.exe from the staging folder with --apply-update <install folder> <launcher pid>
   and closes. apply() (run by that new copy) waits for the old launcher to close, moves each program file and folder
   of the install aside to out/update/old/, copies the new one in, and starts the updated launcher. Any failure puts
   everything back as it was. The new version's own code does the installing, so an update can fix the updater.

Never replaced: the player's files (KEEP): user/ (saves, launcher settings), out/ (the prepared game, logs, the update
itself), lbppc.ini, input.ini.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

try:  # the launcher's language (launcher/i18n.py); run on its own, English
    from i18n import N_, t
except ImportError:
    def N_(text):
        return text

    def t(text, **fields):
        return text.format(**fields) if fields else text

REPO = 'emurphy04/LBPPC'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
API = os.environ.get('LBPPC_UPDATE_API') or API  # tests: a release served on this PC
ASSET = re.compile(r'^LBPPC-(\d+\.\d+\.\d+)-windows\.zip$')
KEEP = {'user', 'out', 'lbppc.ini', 'bbport.ini', 'input.ini', 'input.ini.bak'}
USER_AGENT = 'LBPPC-launcher'  # GitHub's API wants one; nothing about the player
MAX_ZIP = 2 << 30


class UpdateError(Exception):
    """Why an update can't go ahead, in words for the player."""


def version_tuple(text):
    match = re.search(r'(\d+)\.(\d+)\.(\d+)', text or '')
    return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)


def _get(url, timeout=10, accept='application/vnd.github+json'):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': accept})
    return urllib.request.urlopen(request, timeout=timeout)


class Release:
    def __init__(self, version, notes, url, size, sha256, page):
        self.version, self.notes, self.url, self.size, self.sha256, self.page = version, notes, url, size, sha256, page


def check(current, timeout=10):
    """The newest release when it is newer than `current`, else None. Raises UpdateError when GitHub can't be asked
    (offline, rate-limited): the launcher stays quiet about that."""
    try:
        with _get(API, timeout) as reply:
            data = json.load(reply)
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise UpdateError(t('Could not check for updates: {error}', error=error)) from error
    if data.get('draft') or data.get('prerelease'):
        return None
    zips = [a for a in data.get('assets', []) if ASSET.match(a.get('name', ''))]
    if not zips:
        return None
    asset = zips[0]
    version = ASSET.match(asset['name']).group(1)
    if version_tuple(version) <= version_tuple(current):
        return None
    # The file's SHA-256: GitHub's digest of the upload, or a .sha256 file uploaded with it.
    sha256 = (asset.get('digest') or '').removeprefix('sha256:').lower()
    if not re.fullmatch(r'[0-9a-f]{64}', sha256):
        sha256 = ''
        side = next((a for a in data.get('assets', []) if a.get('name') == asset['name'] + '.sha256'), None)
        if side:
            try:
                with _get(side['browser_download_url'], timeout, 'application/octet-stream') as reply:
                    found = re.search(rb'\b[0-9a-fA-F]{64}\b', reply.read(4096))
                sha256 = found.group(0).decode().lower() if found else ''
            except (OSError, urllib.error.URLError):
                sha256 = ''
    if not sha256:
        return None  # nothing to check the download against: not offered
    return Release(version, (data.get('body') or '').strip(), asset['browser_download_url'], asset.get('size', 0),
                   sha256, data.get('html_url', f'https://github.com/{REPO}/releases/latest'))


def download(release, folder, progress=None, cancelled=lambda: False):
    """The release's zip in `folder`, its SHA-256 checked. Returns its path, or None when cancelled."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'LBPPC-{release.version}-windows.zip'
    part = target.with_suffix('.part')
    digest = hashlib.sha256()
    done = 0
    try:
        with _get(release.url, 30, 'application/octet-stream') as reply, open(part, 'wb') as out:
            total = int(reply.headers.get('Content-Length') or release.size or 0)
            while True:
                if cancelled():
                    out.close()
                    part.unlink(missing_ok=True)
                    return None
                block = reply.read(1 << 20)
                if not block:
                    break
                done += len(block)
                if done > MAX_ZIP:
                    raise UpdateError(t('The download is far larger than an LBPPC release: stopped.'))
                digest.update(block)
                out.write(block)
                if progress:
                    progress(done, total)
    except (OSError, urllib.error.URLError) as error:
        part.unlink(missing_ok=True)
        raise UpdateError(t('The download failed: {error}', error=error)) from error
    if digest.hexdigest() != release.sha256:
        part.unlink(missing_ok=True)
        raise UpdateError(t('The download doesn\'t match the release\'s checksum: not installed. Try again later.'))
    os.replace(part, target)
    return target


def stage(zip_path, staging):
    """The zip unpacked into `staging` (emptied first), without its top LBPPC/ folder. Returns `staging`."""
    staging = Path(staging)
    remove(staging)
    staging.mkdir(parents=True)
    root = staging.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        prefix = 'LBPPC/' if names and all(n.startswith('LBPPC/') for n in names) else ''
        for info in archive.infolist():
            name = info.filename[len(prefix):]
            if not name:
                continue
            target = (staging / name).resolve()
            if root not in target.parents and target != root:
                raise UpdateError(t('The update contains a file outside its folder: not installed.'))
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, open(target, 'wb') as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    if not (staging / 'LBPPC.exe').is_file():
        raise UpdateError(t('The update has no LBPPC.exe: not installed.'))
    return staging


def remove(path):
    path = Path(path)
    if path.is_dir():
        shutil.rmtree(path, onerror=lambda func, p, _info: (os.chmod(p, 0o666), func(p)))
    elif path.exists():
        path.unlink()


def start_apply(staging, install):
    """Starts the new launcher from `staging` to install itself over `install` once this process has closed."""
    flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    subprocess.Popen([str(Path(staging) / 'LBPPC.exe'), '--apply-update', str(install), str(os.getpid())],
                     cwd=str(staging), creationflags=flags, close_fds=True)


def wait_for_exit(pid, timeout=60):
    """Until process `pid` (the old launcher) has closed."""
    if os.name != 'nt':
        return
    import ctypes
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
    if handle:
        kernel.WaitForSingleObject(handle, int(timeout * 1000))
        kernel.CloseHandle(handle)


def apply(staging, install, log=print):
    """Puts the program files of `staging` into `install`; the player's files (KEEP) stay. All or nothing."""
    staging, install = Path(staging).resolve(), Path(install).resolve()
    old = install / 'out' / 'update' / 'old'
    remove(old)
    old.mkdir(parents=True)
    entries = sorted(p.name for p in staging.iterdir() if p.name not in KEEP)
    moved, added = [], []
    try:
        for name in entries:
            current = install / name
            if current.exists():
                for attempt in range(20):  # the old launcher's files can stay locked for a moment after it closes
                    try:
                        os.replace(current, old / name)
                        break
                    except PermissionError:
                        if attempt == 19:
                            raise UpdateError(t('{name} is in use (is the game still running?). Close it and try '
                                                'the update again.', name=name))
                        time.sleep(0.5)
                moved.append(name)
            added.append(name)  # before the copy: a half-copied one is removed too
            source = staging / name
            if source.is_dir():
                shutil.copytree(source, current)
            else:
                shutil.copy2(source, current)
        # input.ini: the new default only when the player has none.
        if (staging / 'input.ini').is_file() and not (install / 'input.ini').exists():
            shutil.copy2(staging / 'input.ini', install / 'input.ini')
    except (OSError, UpdateError) as error:
        log(f'Update failed, putting the old version back: {error}')
        for name in added:
            try:
                remove(install / name)
            except OSError as problem:
                log(f'  could not remove the new {name}: {problem}')
        for name in moved:
            os.replace(old / name, install / name)
        raise UpdateError(str(error)) from error


def clean_up(install):
    """At the next start: what an update left behind (the old version, the staging copy, the download)."""
    folder = Path(install) / 'out' / 'update'
    running = Path(sys.executable).resolve()
    if folder.is_dir() and folder.resolve() not in running.parents:
        for path in folder.iterdir():
            try:
                remove(path)
            except OSError:
                pass  # still locked: next time


def run_apply(argv):
    """LBPPC.exe --apply-update <install> <pid>: run by the new version from the staging folder."""
    install, pid = Path(argv[0]), int(argv[1])
    wait_for_exit(pid)
    message = None
    try:
        apply(Path(sys.executable).resolve().parent, install)
    except UpdateError as error:
        message = str(error)
    # The (updated, or on failure the old) launcher, which says how it went.
    env = dict(os.environ)
    if message:
        env['LBPPC_UPDATE_FAILED'] = message
    else:
        env['LBPPC_UPDATED'] = '1'
    subprocess.Popen([str(install / 'LBPPC.exe')], cwd=str(install), env=env, close_fds=True,
                     creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0))
