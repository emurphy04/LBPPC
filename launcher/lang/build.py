"""Builds launcher/lang/<code>.json (what the launcher loads) from the translators' files, lang/source/<code>.txt:
one line per text of strings.json, "number|translation" (\\n for a line break; a leading space is kept). Checks that
every {field} of the English is in its translation and that no line is missing.

usage: python launcher/lang/build.py
"""
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
FIELD = re.compile(r'\{(\w+)\}')


def build(code):
    english = json.loads((HERE / 'strings.json').read_text(encoding='utf-8'))
    lines = (HERE / 'source' / f'{code}.txt').read_text(encoding='utf-8').splitlines()
    table, problems = {}, []
    for line in lines:
        if not line.strip() or line.startswith('#'):
            continue
        number, sep, text = line.partition('|')
        if not sep or not number.strip().isdigit():
            problems.append(f'not "number|text": {line[:60]}')
            continue
        index = int(number)
        if index >= len(english):
            problems.append(f'{index}: no such text')
            continue
        text = text.replace('\\n', '\n')
        source = english[index]
        if sorted(FIELD.findall(text)) != sorted(FIELD.findall(source)):
            problems.append(f'{index}: fields {FIELD.findall(source)} became {FIELD.findall(text)}')
            continue
        if source.startswith(' ') != text.startswith(' '):
            problems.append(f'{index}: the leading space')
        table[source] = text
    missing = [i for i, source in enumerate(english) if source not in table]
    (HERE / f'{code}.json').write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding='utf-8')
    return len(table), missing, problems


if __name__ == '__main__':
    failed = False
    for source in sorted((HERE / 'source').glob('*.txt')):
        done, missing, problems = build(source.stem)
        print(f'{source.stem}: {done} texts' + (f', missing {missing}' if missing else ''))
        for problem in problems:
            print('   ', problem)
        failed |= bool(problems)
    sys.exit(1 if failed else 0)
