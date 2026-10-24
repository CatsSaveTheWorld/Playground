"""Authenticated, short-lived, idempotent PC shutdown protocol (stdlib only).

The same source is shipped to Django and the Windows telemetry workers.
"""
import hashlib
import hmac
import json
import re
import sqlite3
import time
import uuid
from contextlib import closing

NODES = ('home-ai-main', 'home-ai-sub1')
MAX_TTL = 20


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def signed(value, key):
    value = dict(value)
    value.pop('signature', None)
    value['signature'] = hmac.new(bytes.fromhex(key), encoded(value), hashlib.sha256).hexdigest()
    return value


def authenticated(value, key):
    signature = value.get('signature')
    return (isinstance(signature, str) and len(signature) == 64
            and hmac.compare_digest(signature, signed(value, key)['signature']))


def command(node, key, dry_run=False, clock=None):
    issued = int(time.time() if clock is None else clock)
    return signed({'version': 1, 'request_id': uuid.uuid4().hex, 'node_uid': node,
                   'action': 'shutdown', 'dry_run': dry_run, 'issued_at': issued,
                   'expires_at': issued + MAX_TTL}, key)


def validate(value, node, key, retained=False, clock=None):
    if retained or not isinstance(value, dict):
        raise ValueError('Retained or malformed command')
    if set(value) != {'version', 'request_id', 'node_uid', 'action', 'dry_run',
                      'issued_at', 'expires_at', 'signature'}:
        raise ValueError('Unexpected command fields')
    if (type(value['version']) is not int or value['version'] != 1
            or value['node_uid'] != node or node not in NODES
            or value['action'] != 'shutdown' or type(value['dry_run']) is not bool
            or not isinstance(value['request_id'], str)
            or re.fullmatch('[0-9a-f]{32}', value['request_id']) is None):
        raise ValueError('Unexpected command identity/action')
    issued, expiry = value['issued_at'], value['expires_at']
    current = time.time() if clock is None else clock
    if (type(issued) is not int or type(expiry) is not int
            or not 0 < expiry - issued <= MAX_TTL or issued > current + 5 or current >= expiry):
        raise ValueError('Command expired or invalid clock')
    if not authenticated(value, key):
        raise ValueError('Invalid command signature')
    return value


def valid_result(value, request, key):
    if not isinstance(value, dict) or set(value) != {
            'version', 'request_id', 'node_uid', 'action', 'dry_run', 'accepted', 'signature'}:
        return False
    return (all(type(value.get(k)) is type(request[k]) and value.get(k) == request[k]
                for k in ('version', 'request_id', 'node_uid', 'action', 'dry_run'))
            and value.get('accepted') is True and authenticated(value, key))


class Receiver:
    """A single consumer persists deduplication before dispatching the OS action.

    publish_ack must return True only after a successful QoS1 PUBACK. execute is
    injected so dry-run and replay tests cannot invoke the operating system.
    """
    def __init__(self, node, key, journal, publish_ack, execute):
        if node not in NODES or len(bytes.fromhex(key)) != 32:
            raise ValueError('Invalid PC control configuration')
        self.node, self.key, self.journal = node, key, str(journal)
        self.publish_ack, self.execute = publish_ack, execute
        with closing(sqlite3.connect(self.journal)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS commands '
                       '(id TEXT PRIMARY KEY, digest TEXT NOT NULL, dispatched INTEGER NOT NULL, expiry INTEGER NOT NULL)')

    def handle(self, payload, retained=False, clock=None):
        if len(payload) > 2048:
            raise ValueError('Oversized command')
        request = validate(json.loads(payload), self.node, self.key, retained, clock)
        request_id = request['request_id']
        digest = hashlib.sha256(encoded(request)).hexdigest()
        current = int(time.time() if clock is None else clock)
        with closing(sqlite3.connect(self.journal)) as db, db:
            # Expired commands fail validation before lookup; bounded disk usage.
            db.execute('DELETE FROM commands WHERE expiry < ?', (current - 60,))
            db.execute('INSERT OR IGNORE INTO commands VALUES (?, ?, 0, ?)',
                       (request_id, digest, request['expires_at']))
            saved = db.execute('SELECT digest FROM commands WHERE id = ?', (request_id,)).fetchone()
            if saved[0] != digest:
                raise ValueError('Request ID reused with different content')
        response = signed({k: request[k] for k in
                           ('version', 'request_id', 'node_uid', 'action', 'dry_run')}
                          | {'accepted': True}, self.key)
        if not self.publish_ack(request_id, encoded(response)):
            return 'reply_not_acknowledged'
        # Never carry a command over a disconnection/expiry window.
        after_ack = time.time() if clock is None else clock
        if after_ack >= request['expires_at']:
            return 'expired_before_dispatch'
        with closing(sqlite3.connect(self.journal)) as db, db:
            changed = db.execute('UPDATE commands SET dispatched = 1 WHERE id = ? AND dispatched = 0',
                                 (request_id,)).rowcount
        if not changed:
            return 'duplicate'
        if request['dry_run']:
            return 'dry_run'
        # At most once even if the process crashes during OS dispatch.
        self.execute()
        return 'shutdown_dispatched'
