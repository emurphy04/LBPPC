"""The launcher in the player's language: t('English text', name=value) is the text in the chosen language, its
{name} fields filled in. The English text is the key; each language is launcher/lang/<code>.json, {English: text}.
Anything a language doesn't have stays English, so a missing or partial translation never breaks the launcher.

The language follows the game's (the launcher's Language setting, or Windows' when Automatic).
Translations are first drafts, checked by players who speak each language (launcher/lang/README.md).

tools: python launcher/i18n.py --extract   (lists every t() text in the launcher's code: launcher/lang/strings.json)
"""
import ast
import json
import os
from pathlib import Path
import sys

# The launcher language for each game language (the PS4 numbers in lbppc_launcher.LANGUAGES).
LOCALES = {'1': 'en', '18': 'en', '2': 'fr', '3': 'es', '20': 'es', '4': 'de', '5': 'it', '6': 'nl', '7': 'pt',
           '17': 'pt-BR', '8': 'ru', '12': 'fi', '13': 'sv', '14': 'da', '15': 'nb', '16': 'pl'}
FOLDER = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'lang'
_table = {}
locale = 'en'


def set_language(game_language):
    """The launcher's language for a game language number ('1' English US, '2' French, ...)."""
    global _table, locale
    locale = LOCALES.get(str(game_language), 'en')
    _table = {}
    if locale != 'en':
        try:
            _table = json.loads((FOLDER / f'{locale}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            _table = {}
    return locale


def t(text, **fields):
    """`text` in the launcher's language, {name} fields filled in from `fields`."""
    out = _table.get(text) or text
    if fields:
        try:
            return out.format(**fields)
        except (KeyError, IndexError, ValueError):
            return text.format(**fields)  # a translation with a broken field: the English
    return out


def N_(text):
    """Marks a text for translation where it is defined (a list made before the language is chosen); t() translates
    it where it is shown."""
    return text


# The launcher's code that holds t() texts (relative to the LBPPC checkout).
SOURCES = ['launcher/lbppc_launcher.py', 'launcher/input_settings.py', 'scripts/game_setup.py', 'scripts/updater.py',
           'scripts/online_accounts.py', 'scripts/game_files.py', 'scripts/save_import.py']


def extract(root):
    """Every t('...') text in the sources, in order of first use."""
    found = []
    for name in SOURCES:
        tree = ast.parse((Path(root) / name).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, 'id', getattr(node.func, 'attr', None)) in ('t', 'N_') \
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                if node.args[0].value not in found:
                    found.append(node.args[0].value)
    return found


if __name__ == '__main__':
    if '--extract' in sys.argv:
        # strings.json keeps its order (translations in lang/source/<code>.txt are numbered by it): new texts are
        # added at the end, and texts the code no longer has stay (harmless).
        root = Path(__file__).resolve().parent.parent
        listing = Path(__file__).resolve().parent / 'lang' / 'strings.json'
        try:
            texts = json.loads(listing.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            texts = []
        added = [text for text in extract(root) if text not in texts]
        listing.write_text(json.dumps(texts + added, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'{len(texts) + len(added)} texts, {len(added)} new')
    else:
        sys.exit(__doc__)
