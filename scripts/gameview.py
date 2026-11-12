"""The game as the PS4 sees it, from the folders a player has: the game, optionally its update, and each one's Sc0.

A PS4 package extracts into two halves: the game's files (PkgEditor and similar tools call it Image0) and its system
files (Sc0: param.sfo, npbind.dat, playgo-chunk.dat, ...). An update is a second package whose files replace the game's.
LBPPC reads a file from the first of these that has it, as the PS4 does:

    the update folder, the update's Sc0 (for sce_sys/...), the game folder, the game's Sc0 (for sce_sys/...)

Nothing is copied. The runtime does the same for the game's own reads (LBPPC_APP0_LAYERS, src/runtime_file.c).
"""
import os
from pathlib import Path

# The update folder beside the game when the player didn't choose one (shadPS4's and PS4 dump tools' names).
UPDATE_SUFFIXES = ('-UPDATE', '-update', '-Update', '-patch', '-PATCH', '-Patch', '_patch', '_update')
SEPARATOR, FIELD = '*', '|'  # neither can be in a Windows path


def system_folder(root):
    """A folder's Sc0: inside it, or beside it when the folder is an extraction's Image0."""
    root = Path(root)
    for sc0 in (root / 'Sc0', root.parent / 'Sc0'):
        if sc0.is_dir() and sc0 != root:
            return sc0
    return None


def find_update(game):
    """The update folder beside `game` (`CUSA00473-UPDATE` and the like), or None."""
    game = Path(game)
    for suffix in UPDATE_SUFFIXES:
        candidate = game.parent / (game.name + suffix)
        if (candidate / 'eboot.bin').is_file() or (candidate / 'Image0' / 'eboot.bin').is_file():
            return candidate / 'Image0' if (candidate / 'Image0' / 'eboot.bin').is_file() else candidate
    return None


def layers(game, update=None):
    """[(prefix, folder)], first match wins: '' for any path, 'sce_sys' for paths under sce_sys/."""
    result = []
    for root in (update, game):
        if not root:
            continue
        root = Path(root)
        result.append(('', root))
        sc0 = system_folder(root)
        if sc0:
            result.append(('sce_sys', sc0))
    return result


def encode(layer_list):
    return SEPARATOR.join(f'{prefix}{FIELD}{folder}' for prefix, folder in layer_list)


def decode(text):
    out = []
    for item in filter(None, (text or '').split(SEPARATOR)):
        prefix, _, folder = item.partition(FIELD)
        out.append((prefix, Path(folder)))
    return out


class GameView:
    """`view / 'eboot.bin'` is the real path of that file through the layers (the game's when no layer has it)."""

    def __init__(self, game, update=None, layer_list=None):
        self.game = Path(game)
        self.layers = layer_list if layer_list is not None else layers(self.game, update)

    def resolve(self, relative):
        relative = str(relative).replace('\\', '/').strip('/')
        for prefix, folder in self.layers:
            if not prefix:
                candidate = folder / relative
            elif relative == prefix or relative.startswith(prefix + '/'):
                candidate = folder / relative[len(prefix) + 1:]
            else:
                continue
            if candidate.exists():
                return candidate
        return self.game / relative

    def __truediv__(self, relative):
        return self.resolve(relative)

    def glob(self, pattern):
        """Relative paths matching `pattern` in any layer (each once)."""
        seen = {}
        for prefix, folder in self.layers:
            if prefix:
                continue
            for path in folder.glob(pattern):
                seen.setdefault(path.relative_to(folder).as_posix(), path)
        return sorted(seen)

    @property
    def parent(self):
        return self.game.parent

    def __fspath__(self):
        return str(self.game)

    def __str__(self):
        return str(self.game)


def view(game):
    """The view the runtime will use: LBPPC_APP0_LAYERS when run.py set it, else the game folder and its Sc0."""
    encoded = os.environ.get('LBPPC_APP0_LAYERS')
    return GameView(game, layer_list=decode(encoded) if encoded else None)
