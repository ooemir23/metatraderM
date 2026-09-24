"""Short-lived, revocable second-factor sessions for real-money actions."""
import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import struct
import time
from pathlib import Path

from app.order_journal import state_path

SESSION_SECONDS = 15 * 60
COOKIE = 'trade_unlock'


def _secret(username=None):
    second = username and username == os.getenv('DASHBOARD_USER_2', '').strip() and username != os.getenv('DASHBOARD_USER', 'admin')
    configured = os.getenv('TRADING_TOTP_SECRET_2' if second else 'TRADING_TOTP_SECRET', '').strip()
    if configured:
        return configured
    path = state_path('trading_totp_secret_2' if second else 'trading_totp_secret')
    if not path.exists():
        try:
            with path.open('x', encoding='ascii') as stream:
                os.chmod(path, 0o600)
                stream.write(base64.b32encode(secrets.token_bytes(20)).decode().rstrip('='))
        except FileExistsError:
            pass
    return path.read_text(encoding='ascii').strip()


def initialize():
    _secret()
    if os.getenv('DASHBOARD_USER_2') and os.getenv('DASHBOARD_PASSWORD_2'):
        _secret(os.getenv('DASHBOARD_USER_2'))


def verify_totp(code, now=None, username=None):
    if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
        return False
    secret = _secret(username).upper().replace(' ', '')
    try:
        key = base64.b32decode(secret + '=' * ((-len(secret)) % 8), casefold=True)
    except ValueError:
        return False
    window = int((time.time() if now is None else now) // 30)
    for counter in (window - 1, window, window + 1):
        value = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
        offset = value[-1] & 15
        expected = str((struct.unpack('>I', value[offset:offset+4])[0] & 0x7fffffff) % 1000000).zfill(6)
        if hmac.compare_digest(code, expected):
            return True
    return False


def _connect():
    path = state_path('security_sessions.sqlite3')
    db = sqlite3.connect(path, timeout=5)
    os.chmod(path, 0o600)
    db.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires REAL NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS attempts (username TEXT NOT NULL, created REAL NOT NULL)')
    return db


def allow_attempt(username):
    with _connect() as db:
        db.execute('DELETE FROM attempts WHERE created<?', (time.time()-300,))
        count = db.execute('SELECT COUNT(*) FROM attempts WHERE username=?', (username,)).fetchone()[0]
    return count < 5


def record_attempt(username, success):
    with _connect() as db:
        if success:
            db.execute('DELETE FROM attempts WHERE username=?', (username,))
        else:
            db.execute('INSERT INTO attempts VALUES (?, ?)', (username, time.time()))


def unlock(username):
    token = secrets.token_urlsafe(32)
    expires = time.time() + SESSION_SECONDS
    with _connect() as db:
        db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
        db.execute('INSERT INTO sessions VALUES (?, ?, ?)', (hashlib.sha256(token.encode()).hexdigest(), username, expires))
    return token, expires


def session_expires(token, username):
    if not token or len(token) > 150:
        return 0
    with _connect() as db:
        row = db.execute('SELECT expires FROM sessions WHERE token_hash=? AND username=?',
                         (hashlib.sha256(token.encode()).hexdigest(), username)).fetchone()
    return row[0] if row and row[0] > time.time() else 0


def active_session(username=None):
    with _connect() as db:
        if username is None:
            row = db.execute('SELECT 1 FROM sessions WHERE expires>? LIMIT 1', (time.time(),)).fetchone()
        else:
            row = db.execute('SELECT 1 FROM sessions WHERE username=? AND expires>? LIMIT 1',
                             (username, time.time())).fetchone()
    return bool(row)


def revoke(token):
    if token:
        with _connect() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
