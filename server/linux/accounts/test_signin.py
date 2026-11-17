#!/usr/bin/env python3
"""Signs in to LBPPC accounts (Kratos, on this machine) with a name and password, to check that sign-in and the
password migration work, then signs out again. The password is asked for and never shown or stored.

The first sign-in of an imported shadNet account goes through the migration hook (its log says "user N: migrated");
later ones are checked by Kratos alone.

usage (on the VPS): python3 test_signin.py NAME
"""
import getpass
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

PUBLIC = 'http://127.0.0.1:4433'


def call(method, url, body=None, headers=None):
    request = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                                              **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b'null')
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b'null')


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    name = sys.argv[1]
    password = getpass.getpass(f'Password for {name}: ')
    status, flow = call('GET', PUBLIC + '/self-service/login/api')
    if status != 200:
        sys.exit(f'Could not start a sign-in: HTTP {status}')
    # The flow's action points at the public address (lbppc.com/.ory/); send it to this machine's port instead.
    action = urllib.parse.urlsplit(flow['ui']['action'])
    status, result = call('POST', f'{PUBLIC}/self-service/login?{action.query}',
                          {'method': 'password', 'identifier': name, 'password': password})
    password = None
    if status == 200 and result.get('session_token'):
        session = result['session']
        print(f'Signed in as {session["identity"]["traits"]["username"]} '
              f'(shadNet user {session["identity"].get("metadata_admin") and "linked" or "?"}; '
              f'assurance {session.get("authenticator_assurance_level")})')
        status, _ = call('DELETE', PUBLIC + '/self-service/logout/api', {'session_token': result['session_token']})
        print('Signed out again.' if status == 204 else f'Sign-out answered HTTP {status}')
    else:
        messages = [m.get('text') for m in (result.get('ui') or {}).get('messages', [])] if isinstance(result, dict) \
            else []
        for node in ((result or {}).get('ui') or {}).get('nodes', []):
            messages += [m.get('text') for m in node.get('messages', [])]
        print(f'Not signed in (HTTP {status}): {"; ".join(filter(None, messages)) or result}')


if __name__ == '__main__':
    main()
