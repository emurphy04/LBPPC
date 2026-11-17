#!/usr/bin/env python3
"""Kratos's password migration hook for LBPPC accounts (docs/accounts/DESIGN.md, "Existing accounts keep working").

The first time an imported shadNet account signs in to Kratos, Kratos asks this hook whether the password is right.
It checks it against shadNet's own hash (100,000 rounds of SHA-256 over the password and salt, shadNet's
Database::HashPassword); on a match Kratos stores the password with Argon2id and never asks again.

- Listens on 127.0.0.1:4480 only, and answers only requests carrying the key in /etc/lbppc/accounts/hook.env.
- Reads shadNet's database read-only (it runs as the lbppc user, which owns it).
- Kratos sends the account's shadNet user ID, set at import (migrate-body.jsonnet), not the name typed.
- Wrong passwords are limited per account: 10 within 15 minutes, then every answer is "no" for 15 minutes. At most
  two checks run at once, so guessing can't take the server's processors.
- Banned shadNet accounts never match. Nothing secret is logged: only the user ID and the outcome.
- Answers {"status": "password_match"} for a match and {"status": "password_mismatch"} otherwise; Kratos treats
  anything but the first as a wrong password.

Delete it (and the accounts' old hashes) at the cut-off, 3 months after accounts go live.
"""
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = os.environ.get('SHADNET_DB', '/srv/lbppc/shadnet/db/shadnet.db')
KEY = os.environ.get('HOOK_KEY', '')
HOST, PORT = '127.0.0.1', int(os.environ.get('HOOK_PORT', '4480'))
ROUNDS = 100_000
MAX_BODY = 4096
FAIL_LIMIT, FAIL_WINDOW = 10, 15 * 60

working = threading.BoundedSemaphore(2)
failures = {}          # user id -> times of recent wrong passwords
failures_lock = threading.Lock()


def log(text):
    print(text, file=sys.stderr, flush=True)


def shadnet_hash(password, salt):
    digest = password.encode('utf-8') + salt
    for _ in range(ROUNDS):
        digest = hashlib.sha256(digest).digest()
    return digest


def locked(user_id, now):
    with failures_lock:
        recent = [t for t in failures.get(user_id, []) if now - t < FAIL_WINDOW]
        if recent:
            failures[user_id] = recent
        else:
            failures.pop(user_id, None)
        return len(recent) >= FAIL_LIMIT


def failed(user_id, now):
    with failures_lock:
        failures.setdefault(user_id, []).append(now)


def check(user_id, password):
    """True when password is this shadNet account's password."""
    with sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=5) as db:
        row = db.execute('SELECT hash, salt, banned FROM account WHERE user_id = ?', (user_id,)).fetchone()
    if row is None or row[2]:
        return False
    stored, salt = bytes(row[0]), bytes(row[1])
    if not stored or not salt:
        return False
    return hmac.compare_digest(shadnet_hash(password, salt), stored)


class Hook(BaseHTTPRequestHandler):
    server_version = 'lbppc-migration'
    sys_version = ''

    def log_message(self, *args):  # no request lines in the log
        pass

    def answer(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b''
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.answer(405)

    def do_POST(self):
        if self.path != '/check':
            return self.answer(404)
        supplied = self.headers.get('Authorization', '')
        if not KEY or not hmac.compare_digest(supplied.encode(), f'Bearer {KEY}'.encode()):
            log('refused: wrong or missing key')
            return self.answer(401)
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return self.answer(400)
        if not 0 < length <= MAX_BODY:
            return self.answer(413)
        try:
            request = json.loads(self.rfile.read(length))
            user_id = request['shadnet_user_id']
            password = request['password']
        except (ValueError, KeyError, TypeError):
            return self.answer(400)
        if not isinstance(user_id, int) or isinstance(user_id, bool) or not isinstance(password, str) \
                or not 0 < len(password) <= 1024:
            return self.answer(400)

        now = time.monotonic()
        if locked(user_id, now):
            log(f'user {user_id}: locked (too many wrong passwords)')
            return self.answer(200, {'status': 'password_mismatch'})
        with working:
            try:
                ok = check(user_id, password)
            except sqlite3.Error as error:
                log(f'user {user_id}: database error {type(error).__name__}')
                return self.answer(503)
        if ok:
            log(f'user {user_id}: migrated' + (' (password under 8 characters)' if len(password) < 8 else ''))
            return self.answer(200, {'status': 'password_match'})
        failed(user_id, now)
        log(f'user {user_id}: wrong password')
        return self.answer(200, {'status': 'password_mismatch'})


def main():
    if len(KEY) < 32:
        sys.exit('HOOK_KEY is missing or too short (/etc/lbppc/accounts/hook.env)')
    server = ThreadingHTTPServer((HOST, PORT), Hook)
    server.daemon_threads = True
    log(f'password migration hook on {HOST}:{PORT}')
    server.serve_forever()


if __name__ == '__main__':
    main()
