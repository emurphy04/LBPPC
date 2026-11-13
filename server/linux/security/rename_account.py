#!/usr/bin/env python3
"""Rename an LBPPC player on both servers at once (shadNet and Refresh must keep the same name: the game signs in to
shadNet, and Refresh lets a game in only while that same name is signed in to shadNet). Run on the server as root.

    python3 rename_account.py OLD NEW

Checks before changing anything: the player is not signed in to shadNet right now (sign them out first: an online
session keeps the old name), NEW is 3 to 16 letters, digits, - or _, isn't taken on either server and isn't on the
blocked-names list. Then shadNet's account row is renamed (and its placeholder email, <name>@lbppc.invalid, follows
the name), and Refresh renames its account itself (it keeps the old name in the account's history, signs the player
out and tells them in a notification). Asks for "rename" before changing anything.

Tell the player their new name: the launcher signs in with the name it has, so they type the new one in Online.
"""
import argparse
import json
import re
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request

import os

# The server kit's layout (/opt/lbppc), or play.lbppc.com's (/srv/lbppc); LBPPC_HOME overrides.
HOME = os.environ.get('LBPPC_HOME') or next((p for p in ('/srv/lbppc', '/opt/lbppc') if os.path.isdir(p)), '/opt/lbppc')
SHADNET_DB = f'{HOME}/shadnet/db/shadnet.db'
SHADNET_CFG = f'{HOME}/shadnet/shadnet.cfg'
SHADNET_API = 'http://127.0.0.1:31350/admin/v1'
REFRESH_DIR = f'{HOME}/refresh'
PSQL = ['sudo', '-u', 'postgres', 'psql', 'refresh', '-At', '-F', '|']
NAME = re.compile(r'^[A-Za-z0-9_-]{3,16}$')


def admin_key():
    for line in open(SHADNET_CFG, encoding='utf-8'):
        key, _, value = line.partition('=')
        if key.strip() == 'AdminApiKey':
            return value.strip()
    sys.exit(f'No AdminApiKey in {SHADNET_CFG}')


def shadnet(path, **query):
    request = urllib.request.Request(f'{SHADNET_API}/{path}?{urllib.parse.urlencode(query)}',
                                     headers={'X-Admin-Api-Key': admin_key()})
    with urllib.request.urlopen(request, timeout=10) as reply:
        return json.loads(reply.read())


def psql(sql, *params):
    """psql with values passed as variables (no quoting into the SQL)."""
    args = PSQL[:]
    for i, value in enumerate(params):
        args += ['-v', f'p{i}={value}']
    result = subprocess.run(args, input=sql, capture_output=True, text=True)
    if result.returncode:
        sys.exit(f'Refresh database: {result.stderr.strip()}')
    return [line.split('|') for line in result.stdout.splitlines() if line]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('old')
    parser.add_argument('new')
    args = parser.parse_args()
    old, new = args.old, args.new

    if not NAME.match(new):
        sys.exit(f'"{new}" is not a valid name: 3 to 16 letters, digits, - or _.')
    check = shadnet('names/check', npid=new)
    if check.get('blocked'):
        sys.exit(f'"{new}" is on the blocked-names list ({check["blocked"]}).')
    if check.get('exists') and new.lower() != old.lower():
        sys.exit(f'"{new}" is already a shadNet account.')

    db = sqlite3.connect(SHADNET_DB, timeout=10)
    row = db.execute('select user_id, username, email from account where username = ? collate nocase', (old,)).fetchone()
    if not row:
        sys.exit(f'No shadNet account named "{old}".')
    user_id, old_exact, email = row
    if shadnet('sessions/check', npid=old_exact, ip='').get('signedIn'):
        sys.exit(f'{old_exact} is signed in to shadNet right now: ask them to close the game and launcher (or wait), then '
                 'run this again.')

    refresh = psql("select \"UserId\", \"Username\", coalesce(\"EmailAddress\", '') from \"GameUsers\" "
                   "where lower(\"Username\") = lower(:'p0');\n", old)
    taken = psql("select count(*) from \"GameUsers\" where lower(\"Username\") = lower(:'p0') "
                 "and lower(\"Username\") <> lower(:'p1');\n", new, old)
    if taken and taken[0][0] != '0':
        sys.exit(f'"{new}" is already a Refresh account.')
    if len(refresh) > 1:
        sys.exit(f'Refresh has {len(refresh)} accounts named {old}: sort that out first.')

    placeholder = f'{old_exact.lower()}@lbppc.invalid'
    new_email = f'{new.lower()}@lbppc.invalid' if email.lower() == placeholder else email
    print(f'shadNet: account {user_id} "{old_exact}" -> "{new}"' + (f', email -> {new_email}' if new_email != email else ''))
    print(f'Refresh: ' + (f'account {refresh[0][0]} "{refresh[0][1]}" -> "{new}"' if refresh else 'no account (only shadNet is renamed)'))
    if input('Type rename to rename this player on both servers: ').strip() != 'rename':
        sys.exit('Nothing renamed.')

    with db:
        if new_email != email:
            db.execute('update account set username = ?, email = ?, email_check = ? where user_id = ?',
                       (new, new_email, new_email, user_id))
        else:
            db.execute('update account set username = ? where user_id = ?', (new, user_id))
    print(f'shadNet: renamed to {new}')

    if refresh:
        result = subprocess.run(['sudo', '-u', 'lbppc', './Refresh.GameServer', '--rename-user', new, '--username',
                                 refresh[0][1], '--force'], cwd=REFRESH_DIR, capture_output=True, text=True, timeout=300)
        after = psql("select \"Username\" from \"GameUsers\" where \"UserId\" = :'p0';\n", refresh[0][0])
        if not after or after[0][0] != new:
            print(result.stdout[-2000:], result.stderr[-2000:])
            sys.exit(f'Refresh did NOT rename the account (shadNet already did): fix it with '
                     f'"cd {REFRESH_DIR} && sudo -u lbppc ./Refresh.GameServer --rename-user {new} --username '
                     f'{refresh[0][1]} --force", or rename shadNet back.')
        if refresh[0][2].lower() == placeholder:
            psql("update \"GameUsers\" set \"EmailAddress\" = :'p1' where \"UserId\" = :'p0';\n",
                 refresh[0][0], f'{new.lower()}@lbppc.invalid')
        print(f'Refresh: renamed to {new} (the player was signed out and notified)')
    print(f'Done. Tell the player their new name is {new}: they type it in the launcher\'s Online page.')


if __name__ == '__main__':
    main()
