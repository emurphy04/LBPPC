#!/usr/bin/env python3
"""Kratos's hooks into shadNet for LBPPC accounts (docs/accounts/DESIGN.md, Phase 2). Listens on 127.0.0.1:4481 and
answers only Kratos (HOOKS_KEY); talks to shadNet's admin API on this machine (SHADNET_ADMIN_KEY, the AdminApiKey,
for the key-only endpoints in lbppc-shadnet-accounts.patch). Both keys are in /etc/lbppc/accounts/hooks.env.

POST /registration  {"username": name}
    Kratos, before saving a new account (sign-up on lbppc.com, also with Discord): makes the shadNet account, so the
    name is the player's everywhere at once, and answers with its ID for the account's metadata. A taken or blocked
    name stops the sign-up with a message on the name field. (If Kratos then fails to save, the shadNet account stays
    reserved with a password nobody knows; staff can delete it.)
POST /password-changed  {"shadnet_user_id": id}
    Kratos, after the player changed or recovered their password on lbppc.com: the old shadNet password stops
    working (replaced with one nobody knows), so a leaked one can't be used from an old launcher.

Nothing secret is logged: names and IDs and the outcome only.
"""
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOOKS_KEY = os.environ.get('HOOKS_KEY', '')
SHADNET_KEY = os.environ.get('SHADNET_ADMIN_KEY', '')
SHADNET = os.environ.get('SHADNET_ADMIN_URL', 'http://127.0.0.1:31350')
HOST, PORT = '127.0.0.1', int(os.environ.get('HOOKS_PORT', '4481'))
MAX_BODY = 4096


def log(text):
    print(text, file=sys.stderr, flush=True)


def shadnet(path, body):
    """POST to shadNet's admin API: (status, decoded JSON)."""
    request = urllib.request.Request(SHADNET + path, data=json.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json', 'X-Admin-Api-Key': SHADNET_KEY})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read() or b'{}')
        except ValueError:
            return error.code, {}


def field_error(text, message_id=4000001):
    """A Kratos webhook reply that stops the flow with a message under the name field."""
    return {'messages': [{'instance_ptr': '#/traits/username',
                          'messages': [{'id': message_id, 'text': text, 'type': 'error'}]}]}


class Hooks(BaseHTTPRequestHandler):
    server_version = 'lbppc-hooks'
    sys_version = ''

    def log_message(self, *args):
        pass

    def answer(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b''
        self.send_response(code)
        if data:
            self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.answer(405)

    def read_json(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return None
        if not 0 < length <= MAX_BODY:
            return None
        try:
            value = json.loads(self.rfile.read(length))
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def do_POST(self):
        supplied = self.headers.get('Authorization', '')
        if not HOOKS_KEY or not hmac.compare_digest(supplied.encode(), f'Bearer {HOOKS_KEY}'.encode()):
            log('refused: wrong or missing key')
            return self.answer(401)
        body = self.read_json()
        if body is None:
            return self.answer(400)
        if self.path == '/registration':
            return self.registration(body)
        if self.path == '/password-changed':
            return self.password_changed(body)
        return self.answer(404)

    def registration(self, body):
        name = body.get('username')
        if not isinstance(name, str) or not 3 <= len(name) <= 16:
            return self.answer(400, field_error('Choose a name of 3 to 16 letters, digits, - or _.'))
        try:
            status, reply = shadnet('/admin/v1/lbppc/accounts', {'npid': name})
        except OSError as error:
            log(f'registration {name}: shadNet unreachable ({type(error).__name__})')
            return self.answer(503)
        if status == 409:
            log(f'registration {name}: taken')
            return self.answer(400, field_error('That name is taken.', 4000002))
        if status == 403:
            log(f'registration {name}: blocked name')
            return self.answer(400, field_error("That name isn't allowed.", 4000003))
        user_id = reply.get('userId') if status == 200 else None
        if not isinstance(user_id, int) or user_id <= 0:
            log(f'registration {name}: shadNet answered {status}')
            return self.answer(503)
        log(f'registration {name}: shadNet account {user_id}')
        return self.answer(200, {'identity': {
            'metadata_public': {'shadnet_user_id': user_id},
            'metadata_admin': {'shadnet_user_id': user_id, 'created_on': 'lbppc.com'}}})

    def password_changed(self, body):
        user_id = body.get('shadnet_user_id')
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
            return self.answer(204)  # no shadNet account to change
        try:
            status, _reply = shadnet(f'/admin/v1/lbppc/accounts/{user_id}/password-off', {})
        except OSError as error:
            log(f'password change for {user_id}: shadNet unreachable ({type(error).__name__})')
            return self.answer(503)
        log(f'password change for {user_id}: shadNet password ' + ('off' if status == 200 else f'NOT changed ({status})'))
        return self.answer(204 if status in (200, 404) else 503)


def main():
    if len(HOOKS_KEY) < 32 or len(SHADNET_KEY) < 8:
        sys.exit('HOOKS_KEY or SHADNET_ADMIN_KEY missing (/etc/lbppc/accounts/hooks.env)')
    server = ThreadingHTTPServer((HOST, PORT), Hooks)
    server.daemon_threads = True
    log(f'account hooks on {HOST}:{PORT}')
    server.serve_forever()


if __name__ == '__main__':
    main()
