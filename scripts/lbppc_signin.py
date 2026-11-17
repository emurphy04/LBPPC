"""Signing in to an LBPPC account through lbppc.com, for the launcher and the game (docs/accounts/DESIGN.md, Phase 2).

The launcher never sees the player's password, Discord login or second factor: it opens lbppc.com in the browser
(OAuth 2 authorization code with PKCE, as RFC 8252 describes for desktop apps), the player signs in there, and the
browser comes back to a one-time address on this PC (127.0.0.1) with a code the launcher exchanges for tokens:

  - a refresh token, kept by the launcher (locked with Windows DPAPI, as the password was) to stay signed in;
  - access tokens, valid 12 hours, which the game gives shadNet as "lbppc-token:<token>" in place of a password
    (shadNet checks them with lbppc.com: server/linux/shadnet/patches/lbppc-shadnet-tokens.patch).

Refreshing is serialised: lbppc.com (Ory Hydra) treats a refresh token used twice as stolen and ends the sign-in.
"""
import base64
import hashlib
import hmac
import html
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import tls_trust

AUTH = 'https://auth.lbppc.com'
CLIENT_ID = 'lbppc-launcher'
SCOPE = 'openid offline_access game'
PORTS = (47613, 47614, 47615)       # registered with lbppc.com for this client; the first free one is used
TOKEN_PREFIX = 'lbppc-token:'
SIGN_IN_TIMEOUT = 300               # seconds the browser has to come back
REFRESH_MARGIN = 15 * 60            # refresh an access token with less than this left


class SignInError(Exception):
    pass


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _post(path, fields, bearer=None):
    """POST a form to lbppc.com's auth server; the decoded JSON reply, or SignInError."""
    request = urllib.request.Request(AUTH + path, data=urllib.parse.urlencode(fields).encode(), method='POST',
                                     headers={'Content-Type': 'application/x-www-form-urlencoded',
                                              'Accept': 'application/json', 'User-Agent': 'LBPPC launcher'})
    if bearer:
        request.add_header('Authorization', f'Bearer {bearer}')
    try:
        with urllib.request.urlopen(request, timeout=15, context=tls_trust.context()) as response:
            return json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as error:
        try:
            reason = json.loads(error.read() or b'{}').get('error', '')
        except ValueError:
            reason = ''
        raise SignInError(reason or f'HTTP {error.code}') from None
    except (OSError, ValueError) as error:
        raise SignInError(f'lbppc.com could not be reached: {error}') from None


def _userinfo(access_token):
    request = urllib.request.Request(AUTH + '/userinfo', headers={'Authorization': f'Bearer {access_token}',
                                                                   'Accept': 'application/json',
                                                                   'User-Agent': 'LBPPC launcher'})
    try:
        with urllib.request.urlopen(request, timeout=15, context=tls_trust.context()) as response:
            return json.loads(response.read() or b'{}')
    except (OSError, ValueError) as error:
        raise SignInError(f'lbppc.com could not be reached: {error}') from None


class _Callback(BaseHTTPRequestHandler):
    """The browser's return to this PC. Only /callback with the right state counts; the page says what happened."""
    server_version = 'LBPPC'
    sys_version = ''

    def log_message(self, *_args):
        pass

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path != '/callback':
            self.send_error(404)
            return
        query = urllib.parse.parse_qs(url.query)
        state = query.get('state', [''])[0]
        if not hmac.compare_digest(state.encode(), self.server.expected_state.encode()):
            self.send_error(400)
            return
        if 'code' in query:
            self.server.result = {'code': query['code'][0]}
            title, text = 'Signed in', 'You can close this tab and go back to the LBPPC launcher.'
        else:
            reason = query.get('error_description', query.get('error', ['Sign-in was cancelled.']))[0]
            self.server.result = {'error': reason}
            title, text = 'Not signed in', reason
        page = (f'<!doctype html><meta charset="utf-8"><title>LBPPC: {html.escape(title)}</title>'
                f'<body style="font-family:Segoe UI,system-ui,sans-serif;background:#161a2a;color:#f0f0e8;'
                f'display:grid;place-items:center;height:90vh"><div><h1>{html.escape(title)}</h1>'
                f'<p>{html.escape(text)}</p></div>').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(page)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        self.wfile.write(page)


def browser_sign_in(open_browser, cancelled=lambda: False):
    """Signs in through the browser. open_browser(url) shows lbppc.com; returns the token reply (access_token,
    refresh_token, expires_in) with 'username' added. Blocks until the browser comes back (run it off the window's
    thread); cancelled() is checked about once a second."""
    server = None
    for port in PORTS:
        try:
            server = HTTPServer(('127.0.0.1', port), _Callback)
            break
        except OSError:
            continue
    if server is None:
        raise SignInError('The launcher could not open a local port for the sign-in (47613 to 47615 are in use).')
    redirect = f'http://127.0.0.1:{server.server_port}/callback'
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode('ascii')).digest())
    server.expected_state = _b64url(secrets.token_bytes(24))
    server.result = None
    server.timeout = 1
    try:
        open_browser(AUTH + '/oauth2/auth?' + urllib.parse.urlencode({
            'client_id': CLIENT_ID, 'response_type': 'code', 'scope': SCOPE, 'redirect_uri': redirect,
            'state': server.expected_state, 'code_challenge': challenge, 'code_challenge_method': 'S256'}))
        deadline = time.monotonic() + SIGN_IN_TIMEOUT
        while server.result is None:
            if cancelled():
                raise SignInError('Sign-in cancelled.')
            if time.monotonic() > deadline:
                raise SignInError('The sign-in took too long. Try again.')
            server.handle_request()
    finally:
        server.server_close()
    if 'error' in server.result:
        raise SignInError(server.result['error'])
    tokens = _post('/oauth2/token', {'grant_type': 'authorization_code', 'client_id': CLIENT_ID,
                                     'code': server.result['code'], 'redirect_uri': redirect,
                                     'code_verifier': verifier})
    if not tokens.get('access_token') or not tokens.get('refresh_token'):
        raise SignInError('lbppc.com did not send a sign-in.')
    tokens['username'] = _userinfo(tokens['access_token']).get('preferred_username', '')
    return tokens


class Account:
    """The launcher's signed-in LBPPC account. load()/save() keep the refresh token (the launcher locks it with
    DPAPI); access tokens are only held in memory."""

    def __init__(self, load, save):
        self._load, self._save = load, save
        self._lock = threading.Lock()
        self._access, self._expires = None, 0.0
        self.username = None
        stored = load()
        if stored:
            self.username = stored.get('username')

    @property
    def signed_in(self):
        return bool(self._load())

    def signed_in_with(self, tokens):
        with self._lock:
            self._remember(tokens)

    def _remember(self, tokens):
        self.username = tokens.get('username') or self.username
        self._access = tokens['access_token']
        self._expires = time.monotonic() + int(tokens.get('expires_in', 3600))
        self._save({'refresh_token': tokens['refresh_token'], 'username': self.username})

    def game_password(self):
        """What the game gives shadNet in place of a password: a fresh access token (refreshed when it has under 15
        minutes left). Raises SignInError when the sign-in has ended (sign in again)."""
        with self._lock:
            if self._access and self._expires - time.monotonic() > REFRESH_MARGIN:
                return TOKEN_PREFIX + self._access
            stored = self._load()
            if not stored:
                raise SignInError('Not signed in.')
            try:
                tokens = _post('/oauth2/token', {'grant_type': 'refresh_token', 'client_id': CLIENT_ID,
                                                 'refresh_token': stored['refresh_token']})
            except SignInError as error:
                if str(error) in ('invalid_grant', 'invalid_request', 'unauthorized_client'):
                    self._forget()
                    raise SignInError('Your sign-in has ended. Sign in again.') from None
                raise
            if not tokens.get('refresh_token'):
                tokens['refresh_token'] = stored['refresh_token']
            self._remember(tokens)
            return TOKEN_PREFIX + self._access

    def _forget(self):
        self._access, self._expires = None, 0.0
        self._save(None)

    def sign_out(self):
        """Forgets the sign-in here and asks lbppc.com to end it (best effort: forgotten here either way)."""
        with self._lock:
            stored = self._load()
            self._forget()
            self.username = None
        if stored:
            try:
                _post('/oauth2/revoke', {'client_id': CLIENT_ID, 'token': stored['refresh_token'],
                                         'token_type_hint': 'refresh_token'})
            except SignInError:
                pass
