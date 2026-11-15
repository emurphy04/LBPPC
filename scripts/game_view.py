"""The game as the PS4 sees it with its update installed: the update's files over the base game's, and the system
files (sce_sys) wherever the extraction put them. Nothing is ever written to the player's folders.

shadPS4 keeps the base game and its update in separate folders (CUSA00473 and CUSA00473-UPDATE, or -patch) and reads
each file from the update when it has one. PKG tools such as PkgEditor put a package's sce_sys in a separate Sc0
folder, inside it or beside its Image0. The runtime reads /app0 the same way (runtime_file.c: --update, and --system
for an Sc0); these scripts read the eboot, its modules and param.sfo through View, so they see the same files.

    game = game_view.View(base, update)    # update may be None: then just the base folder
    game / 'eboot.bin'                     # a Path: the update's when it has one, else the base game's
    game / 'sce_sys/param.sfo'             # the update's sce_sys or Sc0, else the game's
    (game / 'sce_module').iterdir()        # both folders' entries, the update's first
"""
from pathlib import Path, PurePosixPath


def sc0_of(root):
    """The Sc0 of a game or update folder: inside it, or beside it (an extraction's Image0 and Sc0), or None."""
    root = Path(root)
    for sc0 in (root / 'Sc0', root / 'sc0', root.parent / 'Sc0', root.parent / 'sc0'):
        if sc0.is_dir() and sc0.resolve() != root.resolve():
            return sc0
    return None


def system_folders(base, update=None):
    """Where sce_sys's files are looked for, in order: the update's sce_sys, its Sc0, the game's sce_sys, its Sc0."""
    folders = []
    for root in (update, base):
        if root:
            folders.append(Path(root) / 'sce_sys')
            sc0 = sc0_of(root)
            if sc0:
                folders.append(sc0)
    return folders


def system_folder(base, update=None):
    """The folder holding the param.sfo the game runs with (the update's when it has one), or None."""
    return next((f for f in system_folders(base, update) if (f / 'param.sfo').is_file()), None)


class View:
    def __init__(self, base, update=None, rel=PurePosixPath()):
        self.roots = [Path(r) for r in (update, base) if r]
        self.base = Path(base)
        self.update = Path(update) if update else None
        self.rel = PurePosixPath(rel)

    def _paths(self, rel):
        if rel.parts and rel.parts[0].lower() == 'sce_sys':
            return [folder.joinpath(*rel.parts[1:]) for folder in system_folders(self.base, self.update)]
        return [root.joinpath(*rel.parts) for root in self.roots]

    def __truediv__(self, part):
        rel = self.rel / str(part).replace('\\', '/')
        paths = self._paths(rel)
        for path in paths:
            if path.is_file():
                return path
        dirs = [p for p in paths if p.is_dir()]
        if len(dirs) > 1:
            return View(self.base, self.update, rel)
        return dirs[0] if dirs else paths[-1]  # a missing file: the base game's path, for the error message

    @property
    def name(self):
        return self.rel.name or self.base.name

    @property
    def parent(self):
        return self.base.parent if not self.rel.parts else self.base.joinpath(*self.rel.parent.parts)

    def is_dir(self):
        return any(p.is_dir() for p in self._paths(self.rel))

    def exists(self):
        return self.is_dir()

    def resolve(self):
        return self

    def iterdir(self):
        seen = set()
        for folder in self._paths(self.rel):
            if folder.is_dir():
                for entry in sorted(folder.iterdir()):
                    if entry.name.lower() not in seen:
                        seen.add(entry.name.lower())
                        yield self / entry.name

    def glob(self, pattern):
        seen = set()
        for folder in self._paths(self.rel):
            if folder.is_dir():
                for entry in sorted(folder.glob(pattern)):
                    rel = entry.relative_to(folder).as_posix()
                    if rel.lower() not in seen:
                        seen.add(rel.lower())
                        yield self / rel

    def __str__(self):
        return str(self.base.joinpath(*self.rel.parts)) + (f' (update: {self.update})' if self.update else '')

    __repr__ = __str__


def open_view(base, update=None):
    """The game at `base`, with `update` over it and its system files wherever they are."""
    return View(base, update)
