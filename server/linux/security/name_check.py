"""The blocked-names rule (the same as shadNet's, lbppc_names.h in lbppc-shadnet-security.patch), to test a list
against names before putting it on the server.
usage: python name_check.py blocked-names.txt < names (one per line)

A name is lower-cased, leetspeak undone and reduced to letters. A list word matches when its letters appear in
order, each run of a letter at least as long as in the word: "faggot" matches faaaggot and fagggot, while "nigg"
needs two g's, so it doesn't match knight. (Collapsing every repeat to one letter, the first version, turned "nigg"
into "nig", which is in every "knight" and "night".)"""
import itertools
import re
import sys

LEET = str.maketrans('013457' + '8@$!|', 'oieast' + 'basii')


def squash(name):
    """Lower case, leetspeak undone, letters only."""
    return re.sub('[^a-z]', '', name.lower().translate(LEET))


def pattern(word):
    """Each run of a letter in word as that letter at least as many times (fagg -> f{1,}a{1,}g{2,})."""
    return ''.join(f'{re.escape(c)}{{{len(list(run))},}}' for c, run in itertools.groupby(word))


def load(path):
    """(entries, allowed): entries are (exact, label, compiled pattern or digits); allowed are squashed words."""
    entries, allowed = [], []
    for line in open(path, encoding='utf-8'):
        line = line.strip().lower()
        if not line or line.startswith('#'):
            continue
        if line.startswith('+'):
            word = squash(line[1:])
            if word:
                allowed.append(word)
            continue
        exact = line.startswith('=')
        word = line[1:] if exact else line
        if word.isdigit():
            entries.append((exact, word, word))
            continue
        word = squash(word)
        if word:
            entries.append((exact, word, re.compile(pattern(word))))
    return entries, allowed


def blocked(name, rules):
    """The entry that blocks name ('=word' for a whole-name entry), or None."""
    entries, allowed = rules
    squashed = squash(name)
    for word in allowed:
        squashed = squashed.replace(word, '')
    raw = name.lower()
    for exact, word, rule in entries:
        if isinstance(rule, str):
            if rule in raw:
                return word
        elif exact:
            if rule.fullmatch(squashed):
                return '=' + word
        elif rule.search(squashed):
            return word
    return None


if __name__ == '__main__':
    rules = load(sys.argv[1])
    for name in sys.stdin.read().split():
        hit = blocked(name, rules)
        print(f'{"BLOCKED" if hit else "ok     "} {name:22} {hit or ""}')
