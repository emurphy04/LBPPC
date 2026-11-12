"""Online accounts for LBPPC: a player has one on a shadNet server (sign-in, friends, multiplayer) and one with the
same name on a Refresh server (levels, profiles). The launcher's Create account and tools/shadnet_account.py,
tools/refresh_account.py use these.
"""
import hashlib
import json
import re
import socket
import struct
import urllib.error
import urllib.parse
import urllib.request

import tls_trust

try:  # the launcher's language (launcher/i18n.py); run on its own, English
    from i18n import N_, t
except ImportError:
    def N_(text):
        return text

    def t(text, **fields):
        return text.format(**fields) if fields else text

NAME_PATTERN = r'[A-Za-z0-9_-]{3,16}'
MIN_PASSWORD = 8


class AccountError(Exception):
    """Why an account was not created, in words for the player."""


def check_name_and_password(name, password):
    if not re.fullmatch(NAME_PATTERN, name or ''):
        raise AccountError(t('The online name needs 3 to 16 letters, digits, - or _.'))
    if len(password or '') < MIN_PASSWORD:
        raise AccountError(t('The password needs at least {count} characters.', count=MIN_PASSWORD))


# ---- shadNet: the game protocol's Create command ----

_HEADER = 15  # type u8, command u16, total size u32, packet id u64 (little-endian)
_REQUEST, _REPLY, _SERVER_INFO = 0, 1, 3
_CREATE = 2
_SHADNET_ERRORS = {1: N_('the request was malformed'), 2: N_('the request was invalid'),
                   3: N_('the name, password or email is not accepted'),
                   4: N_('too many attempts, try again in a minute'), 10: N_('the server could not create it'),
                   11: N_('that online name is taken'), 12: N_('that email provider is not allowed'),
                   13: N_('that email is already used')}


def _varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7f
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _proto_string(field, text):
    data = text.encode('utf-8')
    return bytes([field << 3 | 2]) + _varint(len(data)) + data


def _read_packet(sock):
    def read(count):
        data = b''
        while len(data) < count:
            chunk = sock.recv(count - len(data))
            if not chunk:
                raise AccountError(t('The shadNet server closed the connection.'))
            data += chunk
        return data
    kind, command, total, packet_id = struct.unpack('<BHIQ', read(_HEADER))
    return kind, command, packet_id, read(total - _HEADER)


def shadnet_address(server):
    """A shadNet address, "host[:port]" or "tls://host[:port]" (through the server's TLS proxy), as
    (host, port, tls)."""
    text = server.strip()
    tls = text.lower().startswith('tls://')
    host, _, port = text[6:].partition(':') if tls else text.partition(':')
    return host, int(port or 31313), tls


def _shadnet_connect(server):
    """A connection to shadNet; over TLS the certificate must be trusted by the system and name the host."""
    host, port, tls = shadnet_address(server)
    sock = socket.create_connection((host, port), timeout=10)
    if not tls:
        return sock
    try:
        return tls_trust.context().wrap_socket(sock, server_hostname=host)
    except BaseException:
        sock.close()
        raise


def create_shadnet_account(server, name, password, email):
    """server: host[:port] (31313 by default), or tls://host[:port]."""
    request = _proto_string(1, name) + _proto_string(2, password) + _proto_string(4, email)
    payload = struct.pack('<I', len(request)) + request
    try:
        with _shadnet_connect(server) as sock:
            kind, *_ = _read_packet(sock)  # the server's ServerInfo comes first
            if kind != _SERVER_INFO:
                raise AccountError(t('{server} does not look like a shadNet server.', server=server))
            sock.sendall(struct.pack('<BHIQ', _REQUEST, _CREATE, _HEADER + len(payload), 1) + payload)
            while True:
                kind, command, _id, body = _read_packet(sock)
                if kind == _REPLY and command == _CREATE:
                    break
    except OSError as error:
        raise AccountError(t('Cannot reach the shadNet server {server}: {error}', server=server, error=error)) from None
    code = body[0] if body else 1
    if code:
        raise AccountError(t('shadNet: {reason}.', reason=t(_SHADNET_ERRORS[code]) if code in _SHADNET_ERRORS
                               else t('error {code}', code=code)))


def _fields(data):
    """A protobuf message's fields as (number, value): ints for varints, bytes for length-delimited ones."""
    out, pos = [], 0
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        number, kind = key >> 3, key & 7
        if kind == 0:
            value, pos = _read_varint(data, pos)
        elif kind == 2:
            length, pos = _read_varint(data, pos)
            value, pos = data[pos:pos + length], pos + length
        elif kind == 5:
            value, pos = data[pos:pos + 4], pos + 4
        elif kind == 1:
            value, pos = data[pos:pos + 8], pos + 8
        else:
            raise AccountError(t('The shadNet server sent something this launcher does not understand.'))
        out.append((number, value))
    return out


def _read_varint(data, pos):
    value = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7f) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos


_LOGIN, _ADD_FRIEND, _REMOVE_FRIEND, _SET_CLIENT_VERSION = 0, 8, 9, 13
_FRIEND_ERRORS = {5: N_('Sign-in failed.'), 6: N_('This account is signed in already (close the game first).'),
                  7: N_('No account with that name.'), 8: N_('Wrong password.'), 23: N_('Not signed in.'),
                  26: N_('No player with that name.'), 27: N_('That player is blocked.'), 28: N_('Already friends.')}


class Friends:
    """A player's friends on shadNet: friends (name, online), requests they sent and got, players they blocked."""

    def __init__(self, friends=(), sent=(), received=(), blocked=()):
        self.friends, self.sent, self.received, self.blocked = list(friends), list(sent), list(received), list(blocked)


class ShadnetSession:
    """Signed in to shadNet as a player, for the launcher's friends list. A request to someone who asked you is an
    accept; removing someone also declines their request or cancels yours."""

    def __init__(self, server, name, password, client_version):
        self.server, self.name = server, name
        try:
            self.sock = _shadnet_connect(server)
        except OSError as error:
            raise AccountError(t('Cannot reach the shadNet server {server}: {error}', server=server, error=error)) from None
        self.next_id = 1
        try:
            kind, *_ = _read_packet(self.sock)
            if kind != _SERVER_INFO:
                raise AccountError(t('{server} does not look like a shadNet server.', server=server))
            reply = self._request(_LOGIN, _proto_string(1, name) + _proto_string(2, password))
            self._check(reply)
            self.friends = self._parse_login(reply)
            self._send(_SET_CLIENT_VERSION, _proto_string(1, client_version))
        except AccountError:
            self.sock.close()
            raise
        except (OSError, struct.error, IndexError, ValueError) as error:
            # A slow server (the first packet's timeout) or an unexpected reply: said, never an exception that
            # stops the launcher's friends list for good.
            self.sock.close()
            raise AccountError(t('Signing in to {server} failed: {error}', server=server,
                                  error=error or type(error).__name__)) from None
        except BaseException:
            self.sock.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.sock.close()

    def add(self, name):
        self._check(self._request(_ADD_FRIEND, _proto_string(1, name)))

    def remove(self, name):
        self._check(self._request(_REMOVE_FRIEND, _proto_string(1, name)))

    def _send(self, command, message):
        payload = struct.pack('<I', len(message)) + message
        packet_id = self.next_id
        self.next_id += 1
        try:
            self.sock.sendall(struct.pack('<BHIQ', _REQUEST, command, _HEADER + len(payload), packet_id) + payload)
        except OSError as error:
            raise AccountError(t('The connection to {server} was lost: {error}', server=self.server, error=error)) from None
        return packet_id

    def _request(self, command, message):
        packet_id = self._send(command, message)
        try:
            while True:  # notifications (a friend coming online) may come first
                kind, reply_command, reply_id, body = _read_packet(self.sock)
                if kind == _REPLY and reply_command == command and reply_id == packet_id:
                    return body
        except OSError as error:
            raise AccountError(t('The connection to {server} was lost: {error}', server=self.server, error=error)) from None

    @staticmethod
    def _check(body):
        code = body[0] if body else 1
        if code:
            raise AccountError(t(_FRIEND_ERRORS[code]) if code in _FRIEND_ERRORS else
                               t('shadNet error {code}.', code=code))

    @staticmethod
    def _parse_login(body):
        if len(body) < 5:
            return Friends()
        size = struct.unpack_from('<I', body, 1)[0]
        result = Friends()
        for number, value in _fields(body[5:5 + size]):
            if number == 3:
                entry = dict(_fields(value))
                result.friends.append((entry.get(1, b'').decode('utf-8', 'replace'), bool(entry.get(2, 0))))
            elif number in (4, 5, 6):
                {4: result.sent, 5: result.received, 6: result.blocked}[number].append(value.decode('utf-8', 'replace'))
        return result


# ---- Refresh: its web API, then the game logins LBPPC makes allowed ----

def _call(url, body, token=None, method='POST'):
    headers = {'Content-Type': 'application/json', **({'Authorization': token} if token else {})}
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15, context=tls_trust.context()) as reply:
            return json.load(reply)
    except urllib.error.HTTPError as error:
        try:
            return json.loads(error.read() or b'{}')
        except ValueError:
            return {'error': f'HTTP {error.code}'}
    except (OSError, ValueError) as error:
        raise AccountError(t('Cannot reach the Refresh server {server}: {error}', server=url.split('/api/')[0],
                             error=error)) from None


def create_refresh_account(server, name, password, email):
    """server: the Refresh address, e.g. https://play.lbppc.com."""
    api = server.rstrip('/') + '/api/v3'
    digest = hashlib.sha512(password.encode()).hexdigest()
    reply = _call(f'{api}/register', {'username': name, 'emailAddress': email, 'passwordSha512': digest})
    if not reply.get('success'):
        raise AccountError(t('Refresh: {reason}.', reason=reply.get('error') or t('registration failed')))
    token = reply['data']['tokenData']
    settings = _call(f'{api}/users/me', {'psnAuthenticationAllowed': True, 'rpcnAuthenticationAllowed': True},
                     token, 'PATCH')
    if not settings.get('success'):
        raise AccountError(t('Refresh: registered, but game logins are not allowed yet: {reason}.',
                             reason=settings.get('error', settings)))


# The LBPPC server. Refresh and shadNet's web API are behind its HTTPS proxy; shadNet's own protocol (TCP 31313) is
# behind its TLS proxy, and UDP 31314 (matching) is reached directly.
OFFICIAL_SERVER = 'play.lbppc.com'
OFFICIAL = {'refresh': 'https://play.lbppc.com', 'shadnet': 'tls://shadnet.lbppc.com:31313', 'webapi': 'https://api.lbppc.com'}


def server_endpoints(server=OFFICIAL_SERVER):
    """{'refresh', 'shadnet', 'webapi'} for a server: the LBPPC server's own addresses, or for any other (a test
    server on this PC, "http://127.0.0.1:10061") its host with shadNet's ports 31313 and 31315."""
    if (server or '').strip().lower().rstrip('/') in (OFFICIAL_SERVER, 'https://' + OFFICIAL_SERVER):
        return dict(OFFICIAL)
    refresh, shadnet = server_addresses(server)
    return {'refresh': refresh, 'shadnet': shadnet, 'webapi': f'http://{shadnet.rsplit(":", 1)[0]}:31315'}


def server_addresses(server):
    """The one server address a player types ("play.lbppc.com", "https://play.lbppc.com" or, for a server on this PC,
    "http://127.0.0.1:10061") as (Refresh's URL, shadNet's host:port): shadNet runs on the same host, port 31313."""
    text = (server or '').strip().rstrip('/')
    if not text:
        raise AccountError(t('Type the server address (for example play.lbppc.com).'))
    if '://' not in text:
        local = text.split(':')[0] in ('localhost', '127.0.0.1') or ':' in text
        text = ('http://' if local else 'https://') + text
    parts = urllib.parse.urlsplit(text)
    if not parts.hostname:
        raise AccountError(t('"{server}" is not a server address.', server=server))
    return text, f'{parts.hostname}:31313'


def placeholder_email(name):
    """Neither server sends mail yet; an address of its own per name keeps them apart."""
    return f'{name.lower()}@lbppc.invalid'


def create_account(shadnet_server, refresh_server, name, password, email=None):
    """Both accounts, shadNet first (its names are the ones the game signs in with)."""
    check_name_and_password(name, password)
    email = email or placeholder_email(name)
    create_shadnet_account(shadnet_server, name, password, email)
    try:
        create_refresh_account(refresh_server, name, password, email)
    except AccountError as error:
        raise AccountError(t('The shadNet account was created, but not the Refresh one. {error}', error=error)) from None
