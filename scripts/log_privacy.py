"""Logs without personal details, so a player can post one anywhere: run.py passes every line the game prints through
a Scrubber before it reaches out/last_run.log or the launcher's log view, and the launcher's Make a report and its
clean-up of older logs use the same one.

Replaced, each with one label for the whole log so the log still tells things apart:
- the Windows home folder and user name (<home>, <user>), any other profile folder (Users\\<user>), the PC's name (<pc>);
- public IP addresses, IPv4 and IPv6 (<ip-1>, <ip-2>, ...; LAN and loopback addresses name no one and stay);
- the player's online name (<me>) and every other player's (<player-1>, ...): the names the launcher knows (friends,
  requests, blocked players) and any the log shows in a form that is always a player name (peer='x', onlineId='x',
  "npId":"x", /users/x/, ?u=x, the Friends menu's list, "Online: x is online"), from then on wherever they appear;
- the account password, should it ever be printed, and shadNet account numbers ("accountId").

usage: python scripts/log_privacy.py <log> [...]   (rewrites the files in place)
"""
import ipaddress
import os
from pathlib import Path
import re
import sys

NAME = r'[A-Za-z0-9_-]{3,16}'  # online names (online_accounts.NAME_PATTERN)
# Forms in which a word is always a player's name: group 1 is the name.
NAME_FORMS = [re.compile(pattern, re.IGNORECASE) for pattern in (
    rf"\b(?:handle|peer|onlineId|npid|canonical npid|from_npid|to_npid|owner|host_npid)\s*[=:]\s*'({NAME})'",
    rf'"(?:onlineId|npId|displayName|handle)"\s*:\s*"({NAME})"',
    rf'/users/({NAME})(?=[/?\s,]|$)',
    rf'[?&]u=({NAME})(?=[&\s,]|$)',
    rf"(?<=[;:] )'({NAME})' state \d",
    rf'\bOnline: ({NAME}) is (?:online|offline)',
)]
# Words a learned "name" must not be: they would mangle the log (and are no one's name in practice).
NOT_NAMES = {'me', 'self', 'user', 'users', 'info', 'error', 'warning', 'debug', 'gpu', 'http', 'https', 'true',
             'false', 'null', 'none', 'online', 'offline', 'friend', 'friends', 'player', 'host', 'guest', 'unknown',
             'local', 'lbppc', 'ethernet'}
IPV4 = re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])')
IPV6 = re.compile(r'(?<![\w:])[0-9a-fA-F]{0,4}(?::[0-9a-fA-F]{0,4}){2,7}(?![\w:])')
PROFILE = re.compile(r'(?i)(\b[A-Z]:(?:\\\\|\\|/)+Users(?:\\\\|\\|/)+)(?!<)([^\\/\s\'"<>:]+)')
UNIX_HOME = re.compile(r'(/home/|/Users/)(?!<)([^/\s\'"<>]+)')
VERSION_BEFORE = re.compile(r'(?i)(?:version|ver|driver|build|sdk|api|v)[\s:=]*$')
ACCOUNT_ID = re.compile(r'(?i)("accountId"\s*:\s*"?|accountID=)(\d+)')


class Scrubber:
    """One log's worth of replacements: call it on each line, in order (names it learns apply from then on)."""

    def __init__(self, me=(), names=(), secrets=(), home=None, user=None, computer=None):
        home = str(home if home is not None else Path.home())
        user = user if user is not None else os.environ.get('USERNAME') or os.environ.get('USER') or ''
        computer = computer if computer is not None else os.environ.get('COMPUTERNAME', '')
        self.fixed = []  # (pattern, label), tried in this order
        for secret in secrets:
            if secret and len(secret) >= 4:
                self.fixed.append((re.compile(re.escape(secret)), '<password>'))
        if len(home) > 3:
            variants = {home, home.replace('\\', '/'), home.replace('\\', '\\\\')}
            for variant in sorted(variants, key=len, reverse=True):
                self.fixed.append((re.compile(re.escape(variant), re.IGNORECASE), '<home>'))
        self.ips, self.players = {}, {}
        self.me = {name.lower() for name in me if name}
        self.name_pattern = None
        for name in [*me, *names]:
            self.add_name(name)
        # The names known before the run (the player's, the launcher's friends) also inside other text: two threads
        # printing at once can interleave a line's letters ("from 'atuethan'"). Learned names stay whole words.
        known = sorted({n.lower() for n in [*me, *names] if n and len(n) >= 4 and n.lower() in self.players},
                       key=len, reverse=True)
        self.known_pattern = re.compile('|'.join(map(re.escape, known)), re.IGNORECASE) if known else None
        self.words = []  # (pattern, label): the user and PC names, as whole words
        for word, label in ((user, '<user>'), (computer, '<pc>')):
            if word and len(word) > 2:
                self.words.append((re.compile(rf'(?<![\w-]){re.escape(word)}(?![\w-])', re.IGNORECASE), label))

    def add_name(self, name):
        if not name or not re.fullmatch(NAME, name) or name.lower() in NOT_NAMES or name.lower() in self.players:
            return
        key = name.lower()
        self.players[key] = '<me>' if key in self.me else f'<player-{sum(1 for v in self.players.values() if v != "<me>") + 1}>'
        names = sorted(self.players, key=len, reverse=True)
        self.name_pattern = re.compile(r'(?<![A-Za-z0-9_-])(' + '|'.join(map(re.escape, names)) +
                                       r')(?![A-Za-z0-9_-])', re.IGNORECASE)

    def learn(self, line):
        for form in NAME_FORMS:
            for match in form.finditer(line):
                self.add_name(match.group(1))

    def ip(self, match):
        text = match.group(0)
        if VERSION_BEFORE.search(match.string[max(0, match.start() - 16):match.start()]):
            return text  # "version 1.28.0.4", "driver 32.0.15.6094": a version, not an address
        try:
            address = ipaddress.ip_address(text)
        except ValueError:
            return text  # a version number, a time
        if not address.is_global:
            return text
        return self.ips.setdefault(str(address), f'<ip-{len(self.ips) + 1}>')

    def __call__(self, line):
        for pattern, label in self.fixed:
            line = pattern.sub(label, line)
        line = PROFILE.sub(lambda m: m.group(1) + '<user>', line)
        line = UNIX_HOME.sub(lambda m: m.group(1) + '<user>', line)
        self.learn(line)
        if self.known_pattern:
            line = self.known_pattern.sub(lambda m: self.players[m.group(0).lower()], line)
        if self.name_pattern:
            line = self.name_pattern.sub(lambda m: self.players[m.group(1).lower()], line)
        for pattern, label in self.words:
            line = pattern.sub(label, line)
        line = ACCOUNT_ID.sub(lambda m: m.group(1) + '<account>', line)
        line = IPV4.sub(self.ip, line)
        return IPV6.sub(self.ip, line)

    def text(self, text):
        """A whole log: names learned anywhere in it are replaced everywhere, earlier lines too."""
        for line in text.splitlines():
            self.learn(line)
        return '\n'.join(self(line) for line in text.split('\n'))


def raw_logs(env=None):
    """LBPPC_RAW_LOGS=1: logs as printed, for a developer chasing a network bug on their own PC. Reports are
    scrubbed all the same."""
    return (os.environ if env is None else env).get('LBPPC_RAW_LOGS') == '1'


def from_environment(env=None):
    """The Scrubber for a game run: the player's name and password and the names the launcher passed
    (LBPPC_LOG_NAMES, comma-separated)."""
    env = os.environ if env is None else env
    if raw_logs(env):
        return lambda line: line
    me = [env.get('BB_SHADNET_NPID', ''), env.get('BB_USER_NAME', ''), env.get('LBPPC_NP_USER', '')]
    names = [n.strip() for n in env.get('LBPPC_LOG_NAMES', '').split(',')]
    return Scrubber(me=[m for m in me if m], names=[n for n in names if n],
                    secrets=[env.get('BB_SHADNET_PASSWORD', '')])


def scrub_file(path, scrubber=None):
    """Rewrites a log in place without personal details. True when it changed."""
    path = Path(path)
    original = path.read_text(encoding='utf-8', errors='replace')
    cleaned = (scrubber or Scrubber()).text(original)
    if cleaned != original:
        temporary = path.with_name(path.name + '.scrub')
        temporary.write_text(cleaned, encoding='utf-8')
        os.replace(temporary, path)
        return True
    return False


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for name in sys.argv[1:]:
        print(f'{name}: {"cleaned" if scrub_file(name, from_environment()) else "nothing to change"}')
