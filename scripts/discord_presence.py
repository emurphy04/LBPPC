"""Discord Rich Presence: "Playing LittleBigPlanet 3 on LBPPC" on the player's Discord profile while the game runs.

Talks to the Discord app on this PC over its local IPC pipe (\\\\.\\pipe\\discord-ipc-N on Windows,
$XDG_RUNTIME_DIR/discord-ipc-N elsewhere): a handshake with LBPPC's Discord application ID, then SET_ACTIVITY.
Nothing goes over the internet from here; Discord shows it to the player's friends. Every failure (Discord not
running, no application ID) is silent: presence is a nicety, never a reason for the launcher to fail.
"""
import json
import os
from pathlib import Path
import struct
import time
import uuid

HANDSHAKE, FRAME, CLOSE = 0, 1, 2


def _pipes():
    if os.name == 'nt':
        return [rf'\\.\pipe\discord-ipc-{i}' for i in range(10)]
    base = os.environ.get('XDG_RUNTIME_DIR') or os.environ.get('TMPDIR') or '/tmp'
    return [str(Path(base) / f'discord-ipc-{i}') for i in range(10)]


class Presence:
    def __init__(self, client_id):
        self.client_id, self.pipe, self.sock = str(client_id or ''), None, None

    def _send(self, op, payload):
        data = json.dumps(payload).encode('utf-8')
        message = struct.pack('<II', op, len(data)) + data
        if self.pipe:
            self.pipe.write(message)
            self.pipe.flush()
        else:
            self.sock.sendall(message)

    def _receive(self):
        read = self.pipe.read if self.pipe else self.sock.recv
        header = read(8)
        if len(header) < 8:
            raise OSError('Discord closed the connection')
        op, size = struct.unpack('<II', header)
        body = read(size) if size else b''
        return op, json.loads(body or b'{}')

    def connect(self):
        """True when connected to a running Discord."""
        if not self.client_id:
            return False
        for path in _pipes():
            try:
                if os.name == 'nt':
                    self.pipe = open(path, 'r+b', buffering=0)
                else:
                    import socket
                    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.sock.settimeout(5)
                    self.sock.connect(path)
                self._send(HANDSHAKE, {'v': 1, 'client_id': self.client_id})
                op, reply = self._receive()
                if op == FRAME and reply.get('evt') == 'READY':
                    return True
            except (OSError, ValueError):
                pass
            self.close()
        return False

    def set(self, details, state, started=None, button=None):
        activity = {'details': details, 'state': state,
                    'timestamps': {'start': int(started or time.time())},
                    'assets': {'large_image': 'lbppc', 'large_text': 'LBPPC'}}
        if button:
            activity['buttons'] = [{'label': button[0], 'url': button[1]}]
        return self._command(activity)

    def clear(self):
        return self._command(None)

    def _command(self, activity):
        if not (self.pipe or self.sock):
            return False
        try:
            self._send(FRAME, {'cmd': 'SET_ACTIVITY', 'args': {'pid': os.getpid(), 'activity': activity},
                               'nonce': str(uuid.uuid4())})
            self._receive()
            return True
        except (OSError, ValueError):
            self.close()
            return False

    def close(self):
        for handle in (self.pipe, self.sock):
            try:
                if handle:
                    handle.close()
            except OSError:
                pass
        self.pipe = self.sock = None
