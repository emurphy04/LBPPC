#!/usr/bin/env python3
"""Copies shadNet's accounts into Kratos (docs/accounts/DESIGN.md, "Existing accounts keep working").

Each shadNet account becomes a Kratos identity with the same online name and, in metadata_admin, its shadNet user
ID (the permanent link to shadNet and Refresh). No password hash is copied: the identity is marked to use the password
migration hook, which checks the player's password against shadNet the first time they sign in. No email is copied
either (shadNet's are placeholders, @lbppc.invalid): players add and verify one themselves.

Safe to run again: accounts already in Kratos (by shadNet user ID) are skipped, so it also brings over accounts made
in the launcher since the last run. Banned shadNet accounts are skipped. Prints counts only.

usage (on the VPS, as root): python3 import_shadnet_accounts.py [--dry-run]
"""
import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

DB = '/srv/lbppc/shadnet/db/shadnet.db'
ADMIN = 'http://127.0.0.1:4434'


def call(method, path, body=None):
    """(status, decoded JSON, headers) from Kratos's admin API."""
    request = urllib.request.Request(ADMIN + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b'null'), response.headers
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b'null'), error.headers


def existing_ids():
    """shadNet user IDs already in Kratos (following the admin API's Link: <...>; rel="next" pages)."""
    found, path = set(), '/admin/identities?page_size=500'
    while path:
        status, page, headers = call('GET', path)
        if status != 200:
            sys.exit(f'Listing identities failed: HTTP {status}')
        for identity in page or []:
            sid = (identity.get('metadata_admin') or {}).get('shadnet_user_id')
            if isinstance(sid, int):
                found.add(sid)
        path = None
        for part in (headers.get('Link') or '').split(','):
            if 'rel="next"' in part and '<' in part:
                url = urllib.parse.urlsplit(part[part.index('<') + 1:part.index('>')])
                path = url.path + '?' + url.query
    return found


def backfill_public():
    """Copies metadata_admin.shadnet_user_id to metadata_public for identities that lack it. Returns the count."""
    fixed, path = 0, '/admin/identities?page_size=500'
    while path:
        status, page, headers = call('GET', path)
        if status != 200:
            sys.exit(f'Listing identities failed: HTTP {status}')
        for identity in page or []:
            sid = (identity.get('metadata_admin') or {}).get('shadnet_user_id')
            if isinstance(sid, int) and (identity.get('metadata_public') or {}).get('shadnet_user_id') != sid:
                status, _, _ = call('PATCH', f'/admin/identities/{identity["id"]}', [
                    {'op': 'add', 'path': '/metadata_public', 'value': {'shadnet_user_id': sid}}])
                if status == 200:
                    fixed += 1
                else:
                    print(f'identity for shadNet user {sid}: metadata not updated (HTTP {status})', file=sys.stderr)
        path = None
        for part in (headers.get('Link') or '').split(','):
            if 'rel="next"' in part and '<' in part:
                url = urllib.parse.urlsplit(part[part.index('<') + 1:part.index('>')])
                path = url.path + '?' + url.query
    return fixed


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    with sqlite3.connect(f'file:{DB}?mode=ro', uri=True) as db:
        accounts = db.execute('SELECT user_id, username, banned, admin FROM account ORDER BY user_id').fetchall()
    have = existing_ids()
    made = skipped = banned = failed = 0
    for user_id, username, is_banned, is_admin in accounts:
        if user_id in have:
            skipped += 1
            continue
        if is_banned:
            banned += 1
            continue
        if args.dry_run:
            made += 1
            continue
        status, result, _ = call('POST', '/admin/identities', {
            'schema_id': 'lbppc',
            'state': 'active',
            'traits': {'username': username},
            'metadata_admin': {'shadnet_user_id': user_id, 'imported_from': 'shadnet'},
            # Also public (read-only to the player): lbppc.com passes it on in tokens without Kratos's admin API.
            'metadata_public': {'shadnet_user_id': user_id},
            'credentials': {'password': {'config': {'use_password_migration_hook': True}}},
        })
        if status == 201:
            made += 1
        else:
            failed += 1
            reason = (result or {}).get('error', {}).get('reason') or (result or {}).get('error', {}).get('message')
            print(f'shadNet user {user_id}: not imported (HTTP {status}: {reason})', file=sys.stderr)
    if not args.dry_run:
        print(f'Public shadNet IDs added to {backfill_public()} existing accounts')
    print(f'{"Would import" if args.dry_run else "Imported"} {made}; already there {skipped}; banned (left out) '
          f'{banned}; failed {failed}; shadNet accounts {len(accounts)}')
    # Staff roles are not copied: they are given in the account service on purpose, with two-factor required.


if __name__ == '__main__':
    main()
