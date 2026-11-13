#!/usr/bin/env python3
"""Delete players' accounts from both LBPPC servers (Refresh and shadNet). Run it on the server itself.

    python3 delete_account.py NAME [NAME ...] --shadnet-admin YOUR_NPID --refresh-email YOUR_EMAIL

It signs in to each server's admin API as you (an admin on each), finds every NAME, lists what it found, and deletes
only after you type "delete". Deleting can't be undone. shadNet removes the account with its friends, scores and
trophies. Refresh wipes it in place (role -127, no email or password, "This user's account has been deleted.") with
its content, but keeps the name: then this script renames the wiped account in Refresh's database to !del-<its id>
(players can't register names starting with !), so the player can make a new account with the same name and email.
--keep-names leaves the names reserved instead.

shadNet's admin API (127.0.0.1:31350) takes the AdminApiKey from shadnet.cfg when it has one (read from
--shadnet-config). Refresh's API is reached at 127.0.0.1:10061. Passwords are asked for, never passed on the command
line. The rename runs psql as the postgres user (--psql to change it).
"""
import argparse
import getpass
import hashlib
import json
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

DELETED = -127  # Refresh's role for a deleted account


def call(url, body=None, method=None, headers=None):
    """(status, JSON reply or {}) for one request."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method or ('POST' if data else 'GET'),
                                     headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:
            raw = reply.read()
            return reply.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw) if raw.strip() else {}
        except ValueError:
            return error.code, {'error': raw.decode('utf-8', 'replace')[:300]}


def config_value(path, key):
    try:
        for line in open(path, encoding='utf-8'):
            name, _, value = line.partition('=')
            if name.strip() == key:
                return value.strip()
    except FileNotFoundError:
        sys.exit(f'No shadNet config at {path}. Find it (systemctl cat shadnet, or: sudo find / -name shadnet.cfg) and '
                 'pass it with --shadnet-config, or pass the AdminApiKey with --shadnet-key.')
    except OSError as error:
        sys.exit(f'Can\'t read {path} ({error}). Run with sudo, or pass --shadnet-key.')
    return ''


def free_name(psql, user_id):
    """Renames a wiped Refresh account to !del-<id>, so its name can be registered again: ' (name freed)', or why not."""
    if not re.fullmatch(r'[0-9a-f]{24}', user_id):
        return f' (but the name stays reserved: unexpected user id {user_id!r})'
    sql = (f'update "GameUsers" set "Username" = \'!del-{user_id[:11]}\' '
           f'where "UserId"::text = \'{user_id}\' and "Role" = {DELETED} returning "Username"')
    try:
        result = subprocess.run(shlex.split(psql) + ['-Atc', sql], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        return f' (but the name stays reserved: {error})'
    if result.returncode or not result.stdout.strip().startswith('!del-'):
        return f' (but the name stays reserved: {(result.stderr or result.stdout).strip()[:200]})'
    return ' (name freed)'


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('names', nargs='+', help='the accounts to delete (same name on both servers)')
    parser.add_argument('--shadnet-admin', required=True, help='your shadNet admin npid')
    parser.add_argument('--refresh-email', help='your Refresh admin account\'s email address')
    parser.add_argument('--shadnet-only', action='store_true',
                        help='only shadNet (an account the launcher made on shadNet but not on Refresh)')
    parser.add_argument('--shadnet-api', default='http://127.0.0.1:31350')
    parser.add_argument('--refresh-api', default='http://127.0.0.1:10061')
    parser.add_argument('--shadnet-config', default='/opt/lbppc/shadnet/shadnet.cfg')
    parser.add_argument('--shadnet-key', help='shadNet\'s AdminApiKey (default: read from --shadnet-config)')
    parser.add_argument('--keep-names', action='store_true', help='leave the names reserved on Refresh')
    parser.add_argument('--psql', default='sudo -u postgres psql refresh',
                        help='the command that opens Refresh\'s database')
    args = parser.parse_args()
    if not args.shadnet_only and not args.refresh_email:
        parser.error('--refresh-email is needed (or --shadnet-only)')

    # shadNet: sign in, then find each name exactly (the search matches parts of names too).
    key = args.shadnet_key if args.shadnet_key is not None else config_value(args.shadnet_config, 'AdminApiKey')
    key_header = {'X-Admin-Api-Key': key} if key else {}
    status, reply = call(f'{args.shadnet_api}/admin/v1/login',
                         {'npid': args.shadnet_admin, 'password': getpass.getpass('shadNet admin password: ')},
                         headers=key_header)
    if status != 200 or 'token' not in reply:
        sys.exit(f'shadNet sign-in failed ({status}): {reply}')
    shadnet = {**key_header, 'Authorization': f'Bearer {reply["token"]}'}

    # Refresh: sign in with the password's SHA-512, as the game's website does.
    refresh = {}
    if not args.shadnet_only:
        password = getpass.getpass('Refresh admin password: ')
        status, reply = call(f'{args.refresh_api}/api/v3/login',
                             {'emailAddress': args.refresh_email,
                              'passwordSha512': hashlib.sha512(password.encode()).hexdigest()})
        token = (reply.get('data') or {}).get('tokenData') if isinstance(reply, dict) else None
        if not token:
            sys.exit(f'Refresh sign-in failed ({status}): {reply.get("error", reply)}')
        refresh = {'Authorization': token}

    found = []  # (server, name, detail, delete URL)
    refresh_ids = {}  # delete URL: Refresh's user id, for freeing the name
    for name in args.names:
        query = urllib.parse.urlencode({'search': name, 'limit': 50})
        status, reply = call(f'{args.shadnet_api}/admin/v1/users?{query}', headers=shadnet)
        users = [u for u in reply.get('users', []) if u.get('npid', '').lower() == name.lower()] if status == 200 else []
        if status != 200:
            print(f'shadNet: couldn\'t search for {name} ({status}): {reply}')
        elif not users:
            print(f'shadNet: no account named {name}')
        for user in users:
            found.append(('shadNet', user['npid'], f'id {user["userId"]}, email {user.get("email") or "-"}'
                          + (', ADMIN: remove admin rights first' if user.get('admin') else ''),
                          f'{args.shadnet_api}/admin/v1/users/{user["userId"]}'))
        if args.shadnet_only:
            continue
        url = f'{args.refresh_api}/api/v3/admin/users/name/{urllib.parse.quote(name)}'
        status, reply = call(url, headers=refresh)
        data = (reply.get('data') or {}) if isinstance(reply, dict) else {}
        if status == 200 and data.get('role') == DELETED:
            print(f'Refresh: {data.get("username", name)} is already deleted')
        elif status == 200 and reply.get('success', True):
            found.append(('Refresh', data.get('username', name), f'id {data.get("userId", "?")}', url))
            refresh_ids[url] = str(data.get('userId', ''))
        else:
            print(f'Refresh: no account named {name} ({status}: {reply.get("error", "") if isinstance(reply, dict) else reply})')

    if not found:
        sys.exit('Nothing to delete.')
    print('\nTo be deleted:')
    for server, name, detail, _url in found:
        print(f'  {server:8} {name:20} {detail}')
    if input('\nType delete to delete these accounts and their content for good: ').strip() != 'delete':
        sys.exit('Nothing deleted.')
    failed = False
    for server, name, _detail, url in found:
        status, reply = call(url, method='DELETE', headers=shadnet if server == 'shadNet' else refresh)
        ok = 200 <= status < 300 and (reply.get('success', True) if isinstance(reply, dict) else True)
        if ok and server == 'Refresh':  # Refresh answers success either way: check the account is wiped
            status, check = call(url, headers=refresh)
            ok = ((check.get('data') or {}).get('role') == DELETED) if isinstance(check, dict) else False
        failed |= not ok
        note = ''
        if ok and server == 'Refresh':
            note = ' (the name stays reserved)' if args.keep_names else free_name(args.psql, refresh_ids.get(url, ''))
            failed |= note.startswith(' (but')
        print(f'{server:8} {name:20} ' + ('deleted' + note if ok else f'NOT deleted ({status}): {reply}'))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
